"""Configuration and one-off import UI for MoreRecords."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.recorder import get_instance, is_entity_recorded
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, FileSelector

from .const import DOMAIN
from .csv_import import CsvImportError, HistoryRow, parse_csv
from .recorder_import import (
    RecorderImportError,
    async_first_state_timestamp,
    async_prepend_states,
)
from .validation import ValidationError, validate_rows

_LOGGER = logging.getLogger(__name__)


class MoreRecordsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Install the import tool once, then use Configure for each CSV."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Set up the integration without creating any sensor entities."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="MoreRecords", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MoreRecordsOptionsFlow:
        """Open the CSV import wizard from the integration Configure button."""
        return MoreRecordsOptionsFlow()


class MoreRecordsOptionsFlow(OptionsFlow):
    """Read file, preview, and explicitly confirm a single import."""

    def __init__(self) -> None:
        """Initialize temporary state for this wizard only."""
        self._entity_id: str | None = None
        self._rows: list[HistoryRow] = []
        self._preview: dict[str, str] = {}

    def _load_uploaded_csv(self, file_id: str, time_zone: str) -> list[HistoryRow]:
        """Consume and remove a Home Assistant temporary upload."""
        with process_uploaded_file(self.hass, file_id) as path:
            return parse_csv(Path(path), time_zone)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Validate the destination and uploaded CSV before any writes."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input["entity_id"]
            state = self.hass.states.get(entity_id)
            recorder = get_instance(self.hass)
            if state is None or entity_id.split(".")[0] not in (
                "sensor", "binary_sensor"
            ) or not is_entity_recorded(self.hass, entity_id):
                errors["base"] = "invalid_entity"
            elif recorder.engine is None or recorder.engine.dialect.name != "sqlite":
                errors["base"] = "sqlite_only"
            elif recorder.auto_purge:
                errors["base"] = "purge_enabled"
            else:
                try:
                    rows = await self.hass.async_add_executor_job(
                        self._load_uploaded_csv,
                        user_input["file"],
                        self.hass.config.time_zone,
                    )
                    rows = validate_rows(
                        state, rows, datetime.now(tz=timezone.utc).timestamp()
                    )
                    earliest = await async_first_state_timestamp(
                        self.hass, entity_id
                    )
                    if earliest is None:
                        errors["base"] = "missing_history"
                    elif rows[-1].timestamp >= earliest:
                        errors["base"] = "overlap"
                    else:
                        self._entity_id = entity_id
                        self._rows = rows
                        self._preview = {
                            "entity_id": entity_id,
                            "count": str(len(rows)),
                            "first": datetime.fromtimestamp(
                                rows[0].timestamp, timezone.utc
                            ).isoformat(),
                            "last": datetime.fromtimestamp(
                                rows[-1].timestamp, timezone.utc
                            ).isoformat(),
                            "oldest": datetime.fromtimestamp(
                                earliest, timezone.utc
                            ).isoformat(),
                        }
                        return await self.async_step_confirm()
                except (CsvImportError, ValidationError, OSError, ValueError) as err:
                    _LOGGER.warning("CSV preview rejected: %s", err)
                    errors["base"] = "invalid_csv"
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("entity_id"): EntitySelector(
                        {"filter": {"domain": ["sensor", "binary_sensor"]}}
                    ),
                    vol.Required("file"): FileSelector(
                        {"accept":".csv,text/csv"}
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        """Commit only after an explicit confirmation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "confirmation_required"
            elif not self._entity_id or not self._rows:
                return await self.async_step_init()
            else:
                state = self.hass.states.get(self._entity_id)
                if state is None:
                    errors["base"] = "invalid_entity"
                else:
                    try:
                        validate_rows(
                            state, self._rows, datetime.now(timezone.utc).timestamp()
                        )
                        count = await async_prepend_states(
                            self.hass, self._entity_id, self._rows
                        )
                    except (ValidationError, RecorderImportError, Exception):
                        _LOGGER.exception("MoreRecords CSV import failed")
                        errors["base"] = "import_failed"
                    else:
                        return self.async_create_entry(
                            data={
                                "last_entity_id": self._entity_id,
                                "last_import_count": count,
                                "last_import_utc": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }
                        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders=self._preview,
            errors=errors,
        )
