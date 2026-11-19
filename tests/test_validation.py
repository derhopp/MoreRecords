"""Tests for conservative entity/type matching."""

from types import SimpleNamespace

import pytest

from custom_components.more_records.csv_import import HistoryRow
from custom_components.more_records.validation import ValidationError, validate_rows

NOW = 1_700_000_000.0


def entity(entity_id, state, **attrs):
    return SimpleNamespace(entity_id=entity_id, state=state, attributes=attrs)


def test_numeric_sensor():
    rows = [HistoryRow(100, "21.5"), HistoryRow(200, "-3.4")]
    assert validate_rows(entity("sensor.temperature", "20", unit_of_measurement="°C"), rows, NOW) == rows


def test_non_numeric_sensor_rejected():
    with pytest.raises(ValidationError, match="numeric"):
        validate_rows(entity("sensor.words", "hello"), [HistoryRow(100, "hello")], NOW)


def test_nan_rejected():
    with pytest.raises(ValidationError, match="NaN"):
        validate_rows(entity("sensor.temperature", "10"), [HistoryRow(100, "nan")], NOW)


def test_binary_state_normalized():
    assert [r.value for r in validate_rows(entity("binary_sensor.door", "off"), [HistoryRow(100, "true"), HistoryRow(200, "0")], NOW)] == ["on", "off"]


def test_invalid_binary_state():
    with pytest.raises(ValidationError, match="Invalid binary"):
        validate_rows(entity("binary_sensor.door", "on"), [HistoryRow(100, "maybe")], NOW)


def test_future_rejected():
    with pytest.raises(ValidationError, match="future"):
        validate_rows(entity("sensor.temp", "4"), [HistoryRow(NOW, "1")], NOW)


def test_unsupported_domain():
    with pytest.raises(ValidationError, match="Only sensor"):
        validate_rows(entity("switch.light", "on"), [HistoryRow(100, "on")], NOW)
