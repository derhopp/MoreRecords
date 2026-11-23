"""Installation and one-shot CSV / CCU-Historian import wizard."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import math
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.recorder import get_instance, is_entity_recorded
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DateSelector,
    DateTimeSelector,
    EntitySelector,
    FileSelector,
    SelectSelector,
)

from .ccu_historian import (
    HistorianClient,
    HistorianError,
    HistorianPoint,
    date_range_ms,
    rpc_url,
    search_points,
)
from .const import DOMAIN
from .csv_import import CsvImportError, HistoryRow, parse_csv
from .coverage import (
    HistoryVerificationError,
    async_record_coverage,
    async_verify_history,
)
from .export_csv import ExportError, ExportTooLarge, parse_export_window
from .export_view import signed_export_url
from .recorder_export import async_export_preview
from .history_merge import GAP_MODES, MergePlan
from .merge_policy import retention_boundary
from .recorder_import import (
    ImportOutcome,
    RecorderImportError,
    async_preview_merge,
    async_merge_states,
)
from .validation import ValidationError, validate_rows

_LOGGER = logging.getLogger(__name__)


class RetentionNoDataError(RecorderImportError):
    """No source states survive the active Recorder purge window."""


class MoreRecordsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Install the import utility once; invoke Configure to import."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Create the one-time config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="MoreRecords", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MoreRecordsOptionsFlow:
        """Return the per-import wizard."""
        return MoreRecordsOptionsFlow()


class MoreRecordsOptionsFlow(OptionsFlow):
    """Choose a source, validate it, preview overlap and confirm atomically."""

    def __init__(self) -> None:
        """The source data are held only for this options-flow session."""
        self._entity_id: str | None = None
        self._source = "csv"
        self._operation = "import"
        self._export_entity: str | None = None
        self._export_start: float | None = None
        self._export_end: float | None = None
        self._export_preview: dict[str, str] = {}
        self._rows: list[HistoryRow] = []
        self._plan: MergePlan | None = None
        self._gap_mode = "hold_last"
        self._daily_support = False
        self._support_window: tuple[float, float] | None = None
        self._historian_range: tuple[float, float] | None = None
        self._preview: dict[str, str] = {}
        self._ccu_url = ""
        self._ccu_api_key = ""
        self._points: list[HistorianPoint] = []
        self._point_choices: list[HistorianPoint] = []

    def _load_uploaded_csv(self, file_id: str, time_zone: str) -> list[HistoryRow]:
        """Consume Home Assistant's temporary upload safely in an executor."""
        with process_uploaded_file(self.hass, file_id) as path:
            return parse_csv(Path(path), time_zone)

    def _target_valid(self, entity_id: str) -> bool:
        """Allow any existing entity that the active Recorder records."""
        state = self.hass.states.get(entity_id)
        return state is not None and is_entity_recorded(self.hass, entity_id)

    async def _prepare(self, rows: list[HistoryRow], label: str) -> None:
        """Validate and preview; writes happen only in the confirm step."""
        if self._entity_id is None or not self._target_valid(self._entity_id):
            raise ValidationError("Destination entity is unavailable")
        state = self.hass.states.get(self._entity_id)
        normalized = validate_rows(
            state, rows, datetime.now(timezone.utc).timestamp()
        )
        recorder = get_instance(self.hass)
        if self._source == "csv":
            # CSV has no separate start/end controls: its actual observation
            # range is the only user-supplied import window.
            self._support_window = (
                normalized[0].timestamp,
                normalized[-1].timestamp + 0.000001,
            )
        support_window = self._support_window if self._daily_support else None
        plan = await async_preview_merge(
            self.hass, self._entity_id, normalized, self._gap_mode,
            support_window=support_window,
            support_timezone=(self.hass.config.time_zone if support_window else None),
        )
        if not plan.eligible and not plan.support_rows and self._source != "ccu_historian":
            if plan.skipped_retention:
                raise RetentionNoDataError(
                    "No importable rows inside the live Recorder retention window"
                )
            raise RecorderImportError(
                "All source timestamps already exist in Recorder or are beyond "
                "its latest existing state"
            )
        purge_before = plan.purge_before

        self._rows = normalized
        self._plan = plan
        self._preview = {
            "entity_id": self._entity_id,
            "source": label,
            "total": str(plan.total),
            "importable": str(len(plan.eligible)),
            "daily_support_enabled": "yes" if self._daily_support else "no",
            "daily_support_count": str(len(plan.support_rows)),
            "skipped": str(plan.skipped),
            "skipped_overlap": str(plan.skipped_exact),
            "skipped_retention": str(plan.skipped_retention),
            "skipped_after_latest": str(plan.skipped_after_latest),
            "conflicting_existing": str(plan.conflicting_exact),
            "gap_mode": plan.gap_mode,
            "gap_marker": (
                datetime.fromtimestamp(
                    plan.bridge.timestamp, timezone.utc
                ).isoformat() + " → " + plan.bridge.value
                if plan.bridge else "none"
            ),
            "duplicate_rows": "0",
            "conflicting_timestamps": "0",
            "coverage": (
                "CCU-Historian: requires a complete-query confirmation and "
                "exact Recorder verification; no coverage for CSV imports"
                if self._source == "ccu_historian"
                else "CSV: no historical continuity is certified"
            ),
            "retention": (
                f"auto_purge=true, purge_keep_days={recorder.keep_days}"
                if recorder.auto_purge else "auto_purge=false"
            ),
            "purge_before": (
                datetime.fromtimestamp(purge_before, timezone.utc).isoformat()
                if purge_before is not None else "—"
            ),
            "first": datetime.fromtimestamp(
                normalized[0].timestamp, timezone.utc
            ).isoformat(),
            "last": datetime.fromtimestamp(
                normalized[-1].timestamp, timezone.utc
            ).isoformat(),
            "oldest": datetime.fromtimestamp(
                plan.oldest_existing, timezone.utc
            ).isoformat(),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Choose import or export without limiting entity domains."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._operation = user_input["operation"]
            if self._operation == "export":
                return await self.async_step_export()
            return await self.async_step_import()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("operation", default="import"): SelectSelector(
                        {"options": [
                            {"value": "import", "label": "Import"},
                            {"value": "export", "label": "Export CSV"},
                        ], "mode": "dropdown"}
                    )
                }
            ),
            errors=errors,
        )

    def _import_schema(self) -> vol.Schema:
        """Require an explicit operator decision for the leading state gap."""
        return vol.Schema(
            {
                vol.Required("entity_id"): EntitySelector({}),
                vol.Required("source", default="csv"): SelectSelector(
                    {
                        "options": [
                            {"value": "csv", "label": "CSV"},
                            {"value": "ccu_historian", "label": "CCU-Historian"},
                        ],
                        "mode": "dropdown",
                    }
                ),
                vol.Optional("daily_midnight_support", default=False): bool,
                vol.Required("gap_mode"): SelectSelector(
                    {
                        "options": [
                            "hold_last", "oldest_recorder", "unavailable"
                        ],
                        "translation_key": "gap_mode",
                        "mode": "dropdown",
                    }
                ),
            }
        )

    async def async_step_import(self, user_input: dict[str, Any] | None = None):
        """Choose destination and CSV or CCU-Historian import source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input["entity_id"]
            recorder = get_instance(self.hass)
            if not self._target_valid(entity_id):
                errors["base"] = "invalid_entity"
            elif recorder.engine is None or recorder.engine.dialect.name != "sqlite":
                errors["base"] = "sqlite_only"
            else:
                self._entity_id = entity_id
                self._source = user_input["source"]
                self._gap_mode = user_input["gap_mode"]
                self._daily_support = bool(user_input.get("daily_midnight_support", False))
                if self._gap_mode not in GAP_MODES:
                    errors["base"] = "invalid_gap_mode"
                    return self.async_show_form(
                        step_id="import",
                        errors=errors,
                        data_schema=self._import_schema(),
                    )
                if self._source == "csv":
                    return await self.async_step_csv()
                return await self.async_step_historian_connect()

        return self.async_show_form(
            step_id="import",
            data_schema=self._import_schema(),
            errors=errors,
        )

    async def async_step_export(self, user_input: dict[str, Any] | None = None):
        """Read and preview raw states in a selected timestamp interval."""
        errors: dict[str, str] = {}
        diagnostic = "—"
        if user_input is not None:
            entity_id = user_input["entity_id"]
            if not self._target_valid(entity_id):
                errors["base"] = "invalid_entity"
            else:
                try:
                    start_ts, end_ts = parse_export_window(
                        user_input["start_time"], user_input["end_time"],
                        self.hass.config.time_zone,
                    )
                    summary = await async_export_preview(
                        self.hass, entity_id, start_ts, end_ts
                    )
                    self._export_entity = entity_id
                    self._export_start = start_ts
                    self._export_end = end_ts
                    self._export_preview = {
                        "entity_id": entity_id,
                        "count": str(summary.count),
                        "start": datetime.fromtimestamp(
                            start_ts, timezone.utc
                        ).isoformat(),
                        "end": datetime.fromtimestamp(
                            end_ts, timezone.utc
                        ).isoformat(),
                        "first": datetime.fromtimestamp(
                            summary.first_ts, timezone.utc
                        ).isoformat(),
                        "last": datetime.fromtimestamp(
                            summary.last_ts, timezone.utc
                        ).isoformat(),
                    }
                    return await self.async_step_export_confirm()
                except ExportTooLarge as err:
                    diagnostic = str(err)[:300]
                    errors["base"] = "export_too_large"
                except ExportError as err:
                    diagnostic = str(err)[:300]
                    errors["base"] = (
                        "export_empty" if "No stored" in str(err)
                        else "export_invalid"
                    )
                except Exception:
                    _LOGGER.exception("Recorder export preview failed")
                    errors["base"] = "export_failed"
        return self.async_show_form(
            step_id="export",
            data_schema=vol.Schema(
                {
                    vol.Required("entity_id"): EntitySelector({}),
                    vol.Required("start_time"): DateTimeSelector({}),
                    vol.Required("end_time"): DateTimeSelector({}),
                }
            ),
            errors=errors,
            description_placeholders={"diagnostic": diagnostic},
        )

    async def async_step_export_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Issue a short-lived download link after confirmation."""
        if not self._export_entity or self._export_start is None or self._export_end is None:
            return await self.async_step_export()
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="export_confirm",
                    data_schema=vol.Schema(
                        {vol.Required("confirm", default=False): bool}
                    ),
                    errors={"base": "confirmation_required"},
                    description_placeholders=self._export_preview,
                )
            # The download endpoint re-reads the Recorder range, so no
            # potentially sensitive CSV is stored in the config entry.
            url = signed_export_url(
                self.hass, self.config_entry.entry_id, self._export_entity,
                self._export_start, self._export_end,
            )
            return self.async_show_form(
                step_id="export_download",
                data_schema=vol.Schema({}),
                description_placeholders={
                    **self._export_preview,
                    "download_url": url,
                },
            )
        return self.async_show_form(
            step_id="export_confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): bool}
            ),
            description_placeholders=self._export_preview,
        )

    async def async_step_export_download(self, user_input: dict[str, Any] | None = None):
        """Finish the wizard after the user has used the download link."""
        if user_input is not None:
            return self.async_create_entry(data=dict(self.config_entry.options))
        return await self.async_step_export()

    async def async_step_csv(self, user_input: dict[str, Any] | None = None):
        """Upload CSV, normalize rows, and display a preview."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                rows = await self.hass.async_add_executor_job(
                    self._load_uploaded_csv, user_input["file"],
                    self.hass.config.time_zone,
                )
                await self._prepare(rows, "CSV")
                return await self.async_step_confirm()
            except RetentionNoDataError as err:
                _LOGGER.info("CSV contains no retained historical data: %s", err)
                errors["base"] = "retention_empty"
            except RecorderImportError as err:
                _LOGGER.info("CSV import does not add older history: %s", err)
                errors["base"] = "no_new_data"
            except (CsvImportError, ValidationError, OSError, ValueError) as err:
                _LOGGER.warning("MoreRecords CSV rejected: %s", err)
                errors["base"] = "invalid_csv"
        return self.async_show_form(
            step_id="csv",
            data_schema=vol.Schema(
                {vol.Required("file"): FileSelector({"accept": ".csv,text/csv"})}
            ),
            errors=errors,
        )

    async def async_step_historian_connect(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure Historian address and read available datapoints."""
        errors: dict[str, str] = {}
        diagnostic = "—"
        if user_input is not None:
            self._ccu_url = user_input["url"].strip()
            self._ccu_api_key = (user_input.get("api_key") or "").strip()
            try:
                rpc_url(self._ccu_url, self._ccu_api_key)
                client = HistorianClient(
                    async_get_clientsession(self.hass),
                    self._ccu_url,
                    self._ccu_api_key,
                )
                self._points = await client.async_datapoints()
                if not self._points:
                    raise HistorianError("No datapoints available")
                return await self.async_step_historian_search()
            except HistorianError as err:
                # Keep diagnostics actionable. RPC errors deliberately avoid
                # including the configured API key or full request URL.
                diagnostic = str(err)[:300]
                _LOGGER.warning(
                    "CCU-Historian datapoint discovery failed: %s",
                    diagnostic,
                )
                errors["base"] = "historian_connection"

        saved = self.config_entry.options
        return self.async_show_form(
            step_id="historian_connect",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "url", default=self._ccu_url or saved.get("ccu_url", "")
                    ): str,
                    vol.Optional(
                        "api_key",
                        default=self._ccu_api_key or saved.get("ccu_api_key", ""),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={"diagnostic": diagnostic},
        )

    async def async_step_historian_search(
        self, user_input: dict[str, Any] | None = None
    ):
        """Search by display name, parameter, interface or address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._point_choices, total = search_points(
                self._points, user_input.get("query", "")
            )
            if self._point_choices:
                return await self.async_step_historian_point()
            errors["base"] = "no_datapoints"
        return self.async_show_form(
            step_id="historian_search",
            data_schema=vol.Schema(
                {vol.Optional("query", default=""): str}
            ),
            errors=errors,
        )

    async def async_step_historian_point(
        self, user_input: dict[str, Any] | None = None
    ):
        """Select a searched datapoint and the requested calendar date range."""
        if not self._point_choices:
            return await self.async_step_historian_search()

        errors: dict[str, str] = {}
        diagnostic = "—"
        if user_input is not None:
            point = next(
                (p for p in self._point_choices
                 if str(p.idx) == user_input["point_id"]),
                None,
            )
            if point is None:
                errors["base"] = "invalid_point"
            else:
                try:
                    state = self.hass.states.get(self._entity_id)
                    dest_unit = str(
                        state.attributes.get("unit_of_measurement") or ""
                    ) if state else ""
                    # Never silently import e.g. fraction-of-1 levels into
                    # a percentage sensor or °F into a °C sensor.
                    if point.unit and dest_unit and point.unit != dest_unit:
                        errors["base"] = "unit_mismatch"
                    else:
                        start_ms, end_ms = date_range_ms(
                            user_input["start_date"], user_input["end_date"],
                            self.hass.config.time_zone,
                        )
                        # The optional daily support feature needs original
                        # observations from the selected user interval, possibly
                        # BEFORE the cutoff for its first eligible midnight.
                        recorder = get_instance(self.hass)
                        purge_before = retention_boundary(
                            recorder.auto_purge, recorder.keep_days, time.time()
                        )
                        if purge_before is not None and not self._daily_support:
                            start_ms = max(
                                start_ms, math.ceil(purge_before * 1000)
                            )
                        if start_ms >= end_ms:
                            errors["base"] = "retention_empty"
                        else:
                            client = HistorianClient(
                                async_get_clientsession(self.hass),
                                self._ccu_url,
                                self._ccu_api_key,
                            )
                            rows = await client.async_history(
                                point.idx, start_ms, end_ms
                            )
                            self._historian_range = (start_ms / 1000, end_ms / 1000)
                            self._support_window = self._historian_range
                            if not rows:
                                errors["base"] = "historian_empty"
                            else:
                                await self._prepare(
                                    rows, f"CCU-Historian: {point.label}"
                                )
                                self._preview["duplicate_rows"] = str(
                                    client.duplicate_rows
                                )
                                self._preview["conflicting_timestamps"] = str(
                                    client.conflicting_timestamps
                                )
                                return await self.async_step_confirm()
                except RetentionNoDataError as err:
                    _LOGGER.info("No retained CCU-Historian rows: %s", err)
                    errors["base"] = "retention_empty"
                except RecorderImportError as err:
                    _LOGGER.info("CCU-Historian history already present: %s", err)
                    errors["base"] = "no_new_data"
                except (HistorianError, ValidationError, ValueError) as err:
                    # The exception type alone hides the actual problem
                    # (e.g. on/off mismatch, future timestamp or bad unit).
                    # Show a bounded diagnostic to the admin, with no API key
                    # or complete source series in the log.
                    diagnostic = f"{type(err).__name__}: {err}"[:400]
                    _LOGGER.warning(
                        "CCU-Historian datapoint %s rejected for %s: %s",
                        point.idx,
                        self._entity_id,
                        diagnostic,
                    )
                    errors["base"] = "historian_data"

        today = datetime.now(timezone.utc).date().isoformat()
        return self.async_show_form(
            step_id="historian_point",
            data_schema=vol.Schema(
                {
                    vol.Required("point_id"): SelectSelector(
                        {
                            "options": [
                                {"value": str(p.idx), "label": p.label}
                                for p in self._point_choices
                            ],
                            "mode": "dropdown",
                        }
                    ),
                    vol.Required("start_date", default="2020-01-01"):
                        DateSelector({}),
                    vol.Required("end_date", default=today): DateSelector({}),
                }
            ),
            errors=errors,
            description_placeholders={"diagnostic": diagnostic},
        )

    def _confirm_schema(self) -> vol.Schema:
        fields = {vol.Required("confirm", default=False): bool}
        if self._source == "ccu_historian":
            fields[vol.Optional("confirm_complete_history", default=False)] = bool
        return vol.Schema(fields)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Write eligible states and verify explicit historian continuity."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "confirmation_required"
            elif not self._entity_id or not self._rows:
                return await self.async_step_init()
            elif not self._target_valid(self._entity_id):
                errors["base"] = "invalid_entity"
            else:
                try:
                    recorder = get_instance(self.hass)
                    validate_rows(
                        self.hass.states.get(self._entity_id),
                        self._rows,
                        datetime.now(timezone.utc).timestamp(),
                    )
                    if self._plan is None:
                        raise RecorderImportError("Import preview has expired")
                    if self._plan.eligible or self._plan.support_rows:
                        support_window = (
                            self._support_window if self._daily_support else None
                        )
                        outcome = await async_merge_states(
                            self.hass, self._entity_id, self._rows,
                            self._gap_mode, self._plan.signature,
                            support_window=support_window,
                            support_timezone=(
                                self.hass.config.time_zone if support_window else None
                            ),
                        )
                    else:
                        outcome = ImportOutcome(
                            0, self._plan.skipped_exact,
                            self._plan.skipped_retention,
                            self._plan.skipped_after_latest,
                            self._plan.conflicting_exact,
                        )
                    coverage_status = "not_requested"
                    if (
                        self._source == "ccu_historian"
                        and user_input.get("confirm_complete_history", False)
                    ):
                        # Coverage is entirely optional; it is never inferred
                        # from the import itself. A synthetic transition can
                        # never qualify as source-verified evidence.
                        coverage_status = "not_verified"
                        if self._plan.bridge is None and not self._plan.support_rows:
                            try:
                                if self._historian_range is None:
                                    raise HistoryVerificationError(
                                        "Missing Historian query range"
                                    )
                                verified_start, verified_end, fingerprint = (
                                    await async_verify_history(
                                        self.hass, self._entity_id, self._rows,
                                        *self._historian_range,
                                    )
                                )
                                await async_record_coverage(
                                    self.hass, self._entity_id,
                                    verified_start, verified_end, fingerprint,
                                )
                                coverage_status = "verified"
                            except HistoryVerificationError as err:
                                _LOGGER.warning(
                                    "Import completed, but historian coverage "
                                    "could not be certified for %s: %s",
                                    self._entity_id, err,
                                )
                except HistoryVerificationError as err:
                    _LOGGER.warning("Historian verification rejected: %s", err)
                    errors["base"] = "coverage_verification_failed"
                except Exception:
                    _LOGGER.exception("MoreRecords Recorder import failed")
                    errors["base"] = "import_failed"
                else:
                    # Preserve connection details for subsequent one-shot imports.
                    return self.async_create_entry(
                        data={
                            **self.config_entry.options,
                            "ccu_url": self._ccu_url
                            or self.config_entry.options.get("ccu_url", ""),
                            "ccu_api_key": self._ccu_api_key
                            or self.config_entry.options.get("ccu_api_key", ""),
                            "last_entity_id": self._entity_id,
                            "last_imported": outcome.inserted,
                            "last_skipped": outcome.skipped,
                            "last_skipped_overlap": outcome.skipped_overlap,
                            "last_skipped_retention": outcome.skipped_retention,
                            "last_skipped_after_latest": outcome.skipped_after_latest,
                            "last_conflicting_existing": outcome.conflicting_exact,
                            "last_synthetic_rows": outcome.synthetic_rows,
                            "last_daily_support_rows": outcome.daily_support_rows,
                            "last_daily_support_enabled": self._daily_support,
                            "last_gap_mode": self._gap_mode,
                            "last_coverage_status": coverage_status,
                            "last_import_utc": datetime.now(timezone.utc).isoformat(),
                        }
                    )
        return self.async_show_form(
            step_id="confirm",
            data_schema=self._confirm_schema(),
            description_placeholders=self._preview,
            errors=errors,
        )
