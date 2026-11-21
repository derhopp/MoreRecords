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


def test_text_sensor_supported():
    rows = [HistoryRow(100, "closed"), HistoryRow(200, "open")]
    assert validate_rows(entity("sensor.words", "closed"), rows, NOW) == rows


def test_nan_rejected():
    with pytest.raises(ValidationError, match="NaN"):
        validate_rows(entity("sensor.temperature", "10"), [HistoryRow(100, "nan")], NOW)


def test_binary_state_normalized():
    assert [r.value for r in validate_rows(entity("binary_sensor.door", "off"), [HistoryRow(100, "true"), HistoryRow(200, "0")], NOW)] == ["on", "off"]


def test_invalid_binary_state():
    with pytest.raises(ValidationError, match="on/off"):
        validate_rows(entity("binary_sensor.door", "on"), [HistoryRow(100, "maybe")], NOW)


def test_future_rejected():
    with pytest.raises(ValidationError, match="future"):
        validate_rows(entity("sensor.temp", "4"), [HistoryRow(NOW, "1")], NOW)


def test_switch_on_off():
    rows = [HistoryRow(100, "1"), HistoryRow(200, "off"), HistoryRow(300, "TRUE")]
    assert [r.value for r in validate_rows(entity("switch.kitchen", "off"), rows, NOW)] == [
        "on", "off", "on"
    ]


def test_light_state_changes():
    rows = [HistoryRow(100, "0"), HistoryRow(200, "on")]
    assert [r.value for r in validate_rows(entity("light.kitchen", "off"), rows, NOW)] == [
        "off", "on"
    ]


def test_light_rejects_brightness_as_state():
    with pytest.raises(ValidationError, match="on/off"):
        validate_rows(entity("light.kitchen", "off"), [HistoryRow(100, "67")], NOW)


def test_switch_rejects_press_short_as_state():
    with pytest.raises(ValidationError, match="on/off"):
        validate_rows(entity("switch.kitchen", "off"), [HistoryRow(100, "PRESS_SHORT")], NOW)


def test_input_boolean():
    rows = [HistoryRow(100, "false"), HistoryRow(200, "true")]
    assert [r.value for r in validate_rows(entity("input_boolean.away", "off"), rows, NOW)] == [
        "off", "on"
    ]


def test_select_options_are_enforced():
    ent = entity("select.mode", "auto", options=["auto", "eco"])
    assert validate_rows(ent, [HistoryRow(100, "eco")], NOW)[0].value == "eco"
    with pytest.raises(ValidationError, match="Unknown"):
        validate_rows(ent, [HistoryRow(100, "turbo")], NOW)


def test_unknown_domain_text_state():
    rows = [HistoryRow(100, "locked"), HistoryRow(200, "unlocked")]
    assert validate_rows(entity("lock.front_door", "locked"), rows, NOW) == rows


def test_button_timestamp_state():
    rows = [HistoryRow(100, "2023-01-01T12:00:00+00:00")]
    assert validate_rows(entity("button.entrance", "2023-01-01T12:00:00+00:00"), rows, NOW) == rows
    with pytest.raises(ValidationError, match="ISO timestamp"):
        validate_rows(entity("button.entrance", "2023-01-01T12:00:00+00:00"), [HistoryRow(100, "PRESS_SHORT")], NOW)


def test_unavailable_and_unknown_are_allowed():
    rows = [HistoryRow(100, "unavailable"), HistoryRow(200, "unknown")]
    assert validate_rows(entity("light.kitchen", "on"), rows, NOW) == rows


def test_long_state_rejected():
    with pytest.raises(ValidationError, match="255"):
        validate_rows(entity("sensor.long", "test"), [HistoryRow(100, "x" * 256)], NOW)


def test_non_finite_numeric_sensor_rejected_with_state_class():
    with pytest.raises(ValidationError, match="NaN"):
        validate_rows(entity("sensor.power", "unknown", state_class="measurement"), [HistoryRow(100, "inf")], NOW)
