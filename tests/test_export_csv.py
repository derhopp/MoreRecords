"""Raw Recorder CSV formatting, datetime boundaries, and download tickets."""

from datetime import datetime, timezone

import pytest

from custom_components.more_records.csv_import import parse_csv
from custom_components.more_records.export_csv import (
    EXPORT_TICKET_SECONDS,
    ExportError,
    ExportTicketStore,
    ExportTooLarge,
    MAX_EXPORT_ROWS,
    parse_export_window,
    render_csv,
)


def test_utc_datetime_range_with_explicit_offsets():
    start, end = parse_export_window(
        "2026-11-21T10:00:00+01:00",
        "2026-11-21T11:00:00+01:00",
        "Europe/Berlin",
    )
    assert end - start == 3600
    assert start == datetime(
        2026, 11, 21, 9, tzinfo=timezone.utc
    ).timestamp()


def test_naive_local_datetime_uses_ha_timezone():
    start, end = parse_export_window(
        "2026-01-02T10:00:00", "2026-01-02T11:00:00",
        "Europe/Berlin",
    )
    assert datetime.fromtimestamp(start, timezone.utc).hour == 9
    assert end - start == 3600


def test_ambiguous_dst_rejected_instead_of_guessing():
    with pytest.raises(ExportError, match="Ambiguous"):
        parse_export_window(
            "2026-10-25T02:30:00",
            "2026-10-25T04:00:00",
            "Europe/Berlin",
        )


def test_invalid_or_equal_interval_rejected():
    with pytest.raises(ExportError, match="End time"):
        parse_export_window(
            "2026-01-01T12:00:00Z",
            "2026-01-01T12:00:00Z",
            "UTC",
        )


def test_csv_has_exact_utc_microseconds_and_repeat_states():
    raw = render_csv([
        (1763200000.125, "on"),
        (1763200000.625, "on"),
        (1763200001.0, "off"),
    ])
    assert raw.startswith("timestamp;value\n")
    assert raw.count("\non\n") == 0
    assert raw.count(";on\n") == 2
    assert raw.count(";off\n") == 1
    assert "+00:00" in raw
    assert ".125000" in raw
    assert ".625000" in raw


def test_semicolon_quote_newline_and_unicode_are_escaped(tmp_path):
    raw = render_csv([(1763200000, 'Tür "auf"; eingeschaltet, ja')])
    path = tmp_path / "download.csv"
    path.write_text(raw, encoding="utf-8")
    rows = parse_csv(path, "Europe/Berlin")
    assert rows[0].value == 'Tür "auf"; eingeschaltet, ja'


def test_empty_recorder_value_still_serializes_without_crashing():
    assert render_csv([(1763200000, None)]).endswith(";\n")


def test_export_row_limit_guard():
    with pytest.raises(ExportTooLarge):
        render_csv([(float(i + 1), "on") for i in range(MAX_EXPORT_ROWS + 1)])


def test_ticket_is_one_time_and_expires():
    store = ExportTicketStore()
    token = store.create("entry1", "light.kitchen", 100, 200, now=1000)
    ticket = store.consume(token, now=1001)
    assert ticket is not None
    assert ticket.entity_id == "light.kitchen"
    assert ticket.start_ts == 100
    assert store.consume(token, now=1002) is None

    token = store.create("entry1", "switch.kitchen", 100, 200, now=1000)
    assert store.consume(token, now=1000 + EXPORT_TICKET_SECONDS) is None


def test_ticket_revoked_when_entry_unloads():
    store = ExportTicketStore()
    token1 = store.create("entry1", "light.a", 100, 200, now=20)
    token2 = store.create("entry2", "light.b", 100, 200, now=20)
    store.clear_entry("entry1")
    assert store.consume(token1, now=21) is None
    assert store.consume(token2, now=21) is not None


def test_ticket_store_bounds_memory_use():
    store = ExportTicketStore()
    for i in range(35):
        store.create("entry", "light.a", i, i + 1, now=123)
    assert len(store._tickets) <= 30


def test_export_ticket_remembers_opt_in_reconstruction():
    store = ExportTicketStore()
    token = store.create(
        "entry1", "light.kitchen", 100, 200, now=5,
        reconstruct_initial_state=True,
    )
    assert store.consume(token, now=6).reconstruct_initial_state is True
    other = store.create("entry1", "light.kitchen", 100, 200, now=5)
    assert store.consume(other, now=6).reconstruct_initial_state is False
