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
from .merge_policy import ImportPlan, plan_prefix, retention_boundary
from .recorder_import import (
    RecorderImportError,
    async_first_state_timestamp,
    async_prepend_states,
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
        self._rows: list[HistoryRow] = []
        self._plan: ImportPlan | None = None
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
        cutoff = await async_first_state_timestamp(self.hass, self._entity_id)
        if cutoff is None:
            raise RecorderImportError("No existing Recorder state")
        recorder = get_instance(self.hass)
        purge_before = retention_boundary(
            recorder.auto_purge, recorder.keep_days, time.time()
        )
        plan = plan_prefix(
            normalized, cutoff, purge_before=purge_before
        )
        if not plan.eligible:
            if plan.skipped_retention:
                raise RetentionNoDataError(
                    "No source rows fall between the live Recorder retention "
                    "boundary and the beginning of existing HA history"
                )
            raise RecorderImportError(
                "All source rows overlap existing Recorder history"
            )

        self._rows = normalized
        self._plan = plan
        self._preview = {
            "entity_id": self._entity_id,
            "source": label,
            "total": str(plan.total),
            "importable": str(len(plan.eligible)),
            "skipped": str(plan.skipped),
            "skipped_overlap": str(plan.skipped_overlap),
            "skipped_retention": str(plan.skipped_retention),
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
            "oldest": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Select a target entity and an import source."""
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
                if self._source == "csv":
                    return await self.async_step_csv()
                return await self.async_step_historian_connect()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("entity_id"): EntitySelector({}),
                    vol.Required("source", default="csv"): SelectSelector(
                        {"options": [
                            {"value": "csv", "label": "CSV"},
                            {"value": "ccu_historian", "label": "CCU-Historian"},
                        ], "mode": "dropdown"}
                    ),
                }
            ),
            errors=errors,
        )

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
                        # Avoid requesting years of CCU-Historian data that
                        # Home Assistant would automatically purge anyway.
                        recorder = get_instance(self.hass)
                        purge_before = retention_boundary(
                            recorder.auto_purge, recorder.keep_days, time.time()
                        )
                        if purge_before is not None:
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
                            if not rows:
                                errors["base"] = "historian_empty"
                            else:
                                await self._prepare(
                                    rows, f"CCU-Historian: {point.label}"
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

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Make the explicit write, rechecking all overlapping rows at commit."""
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
                    outcome = await async_prepend_states(
                        self.hass, self._entity_id, self._rows
                    )
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
                            "last_import_utc": datetime.now(timezone.utc).isoformat(),
                        }
                    )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): bool}
            ),
            description_placeholders=self._preview,
            errors=errors,
        )
