"""Entity-independent Recorder state validation and safe domain-aware normalization.

MoreRecords works with any *existing, recorded* Home Assistant entity. The
target domain and live state metadata determine which CSV/Historian values
are acceptable. No devices are controlled and no live events are emitted.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from .csv_import import HistoryRow


class ValidationError(ValueError):
    """A CSV/CCU-Historian series is incompatible with the selected entity."""


# These domains have HA's canonical 'on'/'off' state semantics. Converting
# 0/1 and true/false is safe for *state* data, not proof of a button event.
_BOOLEAN_DOMAINS = frozenset({
    "automation", "binary_sensor", "fan", "humidifier", "input_boolean",
    "light", "remote", "script", "siren", "switch",
})
_BOOLEAN_VALUES = {
    "on": "on", "off": "off",
    "true": "on", "false": "off",
    "1": "on", "0": "off",
}
# Numeric-only domains; sensors may be numeric *or* textual.
_NUMERIC_DOMAINS = frozenset({"counter", "input_number", "number"})
_UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})
_MAX_STATE_LENGTH = 255


def _is_numeric_entity(entity: Any, domain: str) -> bool:
    """Infer numeric states from domain, state class, unit, or current value."""
    if domain in _NUMERIC_DOMAINS:
        return True
    if domain != "sensor":
        return False

    attrs = entity.attributes or {}
    if attrs.get("state_class") in ("measurement", "total", "total_increasing"):
        return True
    if attrs.get("unit_of_measurement") is not None:
        return True
    try:
        return math.isfinite(float(entity.state))
    except (TypeError, ValueError):
        return False


def _allowed_options(entity: Any, domain: str) -> frozenset[str] | None:
    """Use an entity's own live allowed states for selectable entities."""
    attributes = entity.attributes or {}
    if domain in {"select", "input_select"}:
        options = attributes.get("options")
    elif domain == "climate":
        options = attributes.get("hvac_modes")
    else:
        return None

    if not isinstance(options, (list, tuple, set)) or not options:
        raise ValidationError(
            f"Cannot determine the allowed states for {entity.entity_id}"
        )
    if not all(isinstance(option, str) for option in options):
        raise ValidationError("Entity options must be strings")
    return frozenset(options)


def _validate_text(value: str, timestamp: float) -> str:
    """Validate Recorder's general state-string storage constraints."""
    if not isinstance(value, str) or not value or len(value) > _MAX_STATE_LENGTH:
        raise ValidationError(
            f"Empty or oversized state at {timestamp}: maximum 255 characters"
        )
    if "\x00" in value or any(
        ord(character) < 32 and character not in ("\t",)
        for character in value
    ):
        raise ValidationError(f"Invalid control character at {timestamp}")
    return value


def validate_rows(entity: Any, rows: list[HistoryRow], now: float) -> list[HistoryRow]:
    """Validate recorded states for an existing entity, without domain limits.

    - Light, switch and other binary domains accept canonical on/off states,
      including conventional 1/0 and true/false representations.
    - Numeric domains and numeric sensors require finite numeric values.
    - Select/input_select/climate require values listed in live entity options.
    - Other entity domains accept bounded nonempty textual state values; this
      is not a claim that imported states reconstruct service calls/events.
    - 'unknown'/'unavailable' are legitimate HA states for all domains.

    Timestamp, duplicate and retention checks are handled by the source
    parser and recorder merge policy.
    """
    if entity is None or not isinstance(getattr(entity, "entity_id", None), str):
        raise ValidationError("A real existing Home Assistant entity is required")
    if not rows:
        raise ValidationError("No rows provided")
    if rows[-1].timestamp >= now:
        raise ValidationError("CSV contains current or future timestamps")

    domain = entity.entity_id.partition(".")[0]
    numeric = _is_numeric_entity(entity, domain)
    options = _allowed_options(entity, domain)
    normalized: list[HistoryRow] = []

    for row in rows:
        value = _validate_text(row.value, row.timestamp)
        if value in _UNAVAILABLE_STATES:
            normalized.append(HistoryRow(row.timestamp, value))
            continue

        if domain in _BOOLEAN_DOMAINS:
            state = _BOOLEAN_VALUES.get(value.casefold())
            if state is None:
                raise ValidationError(
                    f"Invalid on/off state for {domain} at {row.timestamp}: "
                    f"{value!r}"
                )
            normalized.append(HistoryRow(row.timestamp, state))
            continue

        if numeric:
            try:
                number = float(value)
            except ValueError as err:
                raise ValidationError(
                    f"Non-numeric value for {domain} at {row.timestamp}: "
                    f"{value!r}"
                ) from err
            if not math.isfinite(number):
                raise ValidationError("NaN and infinity are not valid states")
            normalized.append(row)
            continue

        if options is not None and value not in options:
            raise ValidationError(
                f"Unknown {domain} option at {row.timestamp}: {value!r}"
            )

        if domain in ("button", "event"):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as err:
                raise ValidationError(
                    f"Expected an ISO timestamp state for {domain}: {value!r}"
                ) from err

        # For remaining entity domains HA states are strings. Do not reject
        # them solely because they are not sensors/binary sensors.
        normalized.append(row)

    return normalized
