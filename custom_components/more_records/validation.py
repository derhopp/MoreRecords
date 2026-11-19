"""Conservative entity and CSV value compatibility checks."""

from __future__ import annotations

import math
from typing import Any

from .csv_import import HistoryRow


class ValidationError(ValueError):
    """A CSV series does not match the selected entity."""


def validate_rows(entity: Any, rows: list[HistoryRow], now: float) -> list[HistoryRow]:
    """Validate a numeric sensor or binary sensor and return HA state strings.

    Do not guess conversions or the state type of unsupported domains.
    """
    if not rows:
        raise ValidationError("No rows provided")
    if rows[-1].timestamp >= now:
        raise ValidationError("CSV contains current or future timestamps")

    domain = entity.entity_id.partition(".")[0]
    if domain == "binary_sensor":
        normalized = []
        allowed = {
            "on": "on", "off": "off", "true": "on", "false": "off",
            "1": "on", "0": "off",
        }
        for row in rows:
            value = allowed.get(row.value.lower())
            if value is None:
                raise ValidationError(
                    f"Invalid binary sensor value at {row.timestamp}: {row.value!r}"
                )
            normalized.append(HistoryRow(row.timestamp, value))
        return normalized

    if domain != "sensor":
        raise ValidationError("Only sensor and binary_sensor domains are supported")

    attrs = entity.attributes
    has_numeric_metadata = (
        attrs.get("state_class") in ("measurement", "total", "total_increasing")
        or bool(attrs.get("unit_of_measurement"))
    )
    try:
        float(entity.state)
        current_numeric = True
    except (TypeError, ValueError):
        current_numeric = False
    if not (has_numeric_metadata or current_numeric):
        raise ValidationError("Cannot determine whether this sensor is numeric")

    result = []
    for row in rows:
        try:
            numeric = float(row.value)
        except ValueError as err:
            raise ValidationError(
                f"Non-numeric value at {row.timestamp}: {row.value!r}"
            ) from err
        if not math.isfinite(numeric):
            raise ValidationError("NaN and infinity are not valid states")
        if len(row.value) > 255:
            raise ValidationError("State text exceeds Recorder length limit")
        result.append(row)
    return result
