"""Regression tests for deliberately opt-in daily local-midnight support rows."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.more_records.csv_import import HistoryRow
from custom_components.more_records.daily_support import midnight_supports
from custom_components.more_records.history_merge import StoredState, plan_missing


BERLIN = ZoneInfo("Europe/Berlin")


def ts(day, hour=0, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=BERLIN).timestamp()


def src(*data):
    return [HistoryRow(t, value) for t, value in data]


def existing(*data):
    return [
        StoredState(i + 1, t, value, None, None)
        for i, (t, value) in enumerate(data)
    ]


def plan(data, recorded, *, enabled=True, cutoff=None, end=None, mode="hold_last"):
    return plan_missing(
        src(*data), existing(*recorded), mode,
        purge_before=cutoff,
        support_window=(ts(19), end or ts(25)) if enabled else None,
        support_timezone="Europe/Berlin" if enabled else None,
    )


def test_disabled_default_never_adds_supports():
    p = plan(
        [(ts(19, 10), "on"), (ts(23, 14), "off")],
        [(ts(25), "off")], enabled=False,
    )
    assert p.support_rows == []


def test_copy_last_real_source_observation_each_local_midnight():
    p = plan(
        [(ts(19, 10), "on"), (ts(23, 14), "off")],
        [(ts(25), "off")],
    )
    assert [(x.timestamp, x.value) for x in p.support_rows] == [
        (ts(20), "on"), (ts(21), "on"), (ts(22), "on"),
        (ts(23), "on"), (ts(24), "off"),
    ]


def test_no_support_if_any_real_source_or_recorder_state_between_00_06():
    p = plan(
        [(ts(19, 10), "on"), (ts(20, 2), "off"),
         (ts(22, 5, 59), "on")],
        [(ts(21, 1), "on"), (ts(25), "off")],
    )
    assert [r.timestamp for r in p.support_rows] == [
        ts(23), ts(24)
    ]


def test_six_oclock_is_outside_morning_exclusion_window():
    p = plan(
        [(ts(19, 10), "on"), (ts(20, 6), "off")],
        [(ts(25), "off")], end=ts(21),
    )
    assert [(r.timestamp, r.value) for r in p.support_rows] == [
        (ts(20), "on"),
    ]


def test_pre_cutoff_source_may_seed_first_midnight_but_never_be_inserted():
    cutoff = ts(19, 18)
    p = plan(
        [(ts(19, 10), "on"), (ts(20, 14), "off")],
        [(ts(25), "off")], cutoff=cutoff, end=ts(21),
    )
    assert p.support_rows == [HistoryRow(ts(20), "on")]
    assert p.skipped_retention == 1
    assert all(r.timestamp >= cutoff for r in p.support_rows)


def test_outside_import_window_predecessor_never_used():
    rows = src((ts(20, 10), "on"))
    supports = midnight_supports(
        rows, [ts(25)], window_start=ts(19),
        window_end=ts(21), timezone_name="Europe/Berlin",
        purge_before=None, newest_recorded=ts(25),
    )
    assert supports == []
    with pytest.raises(ValueError, match="outside"):
        midnight_supports(
            src((ts(18, 10), "off"), (ts(20, 10), "on")),
            [ts(25)], window_start=ts(19), window_end=ts(21),
            timezone_name="Europe/Berlin", purge_before=None,
            newest_recorded=ts(25),
        )


def test_never_append_supports_beyond_last_existing_recorder_state():
    p = plan(
        [(ts(19, 10), "on")],
        [(ts(21, 10), "on")], end=ts(25),
    )
    assert [r.timestamp for r in p.support_rows] == [ts(20), ts(21)]


def test_preview_signature_changes_when_toggle_enabled():
    data = [(ts(19, 10), "on"), (ts(23, 14), "off")]
    saved = [(ts(25), "off")]
    assert plan(data, saved, enabled=True).signature != plan(
        data, saved, enabled=False
    ).signature


def test_repeated_marker_is_not_assumed_to_be_an_observed_transition():
    p = plan(
        [(ts(19, 10), "on")], [(ts(25), "off")],
        end=ts(22),
    )
    assert [r.value for r in p.support_rows] == ["on", "on"]
    assert len(p.eligible) == 1


def test_selected_bridge_policy_remains_authoritative_after_last_source():
    p = plan(
        [(ts(19, 10), "on")], [(ts(25), "off")],
        end=ts(25), mode="unavailable",
    )
    assert p.bridge is not None
    assert p.bridge.value == "unavailable"
    assert p.support_rows == []


def test_morning_exact_midnight_exclusion_and_dst_midnight():
    p = plan(
        [(ts(19, 10), "on"), (ts(21), "on")],
        [(ts(25), "off")],
    )
    assert ts(21) not in [r.timestamp for r in p.support_rows]
    # DST transitions still follow local calendar midnight, not UTC 24h steps.
    old = datetime(2026, 3, 28, 10, tzinfo=BERLIN).timestamp()
    first = datetime(2026, 3, 29, 0, tzinfo=BERLIN).timestamp()
    second = datetime(2026, 3, 30, 0, tzinfo=BERLIN).timestamp()
    points = midnight_supports(
        [HistoryRow(old, "on")], [], window_start=old,
        window_end=second + 1, timezone_name="Europe/Berlin",
        purge_before=None, newest_recorded=second + 100,
    )
    assert [r.timestamp for r in points] == [first, second]
    assert second - first == 23 * 3600


def test_rejected_timestamp_conflict_is_not_used_as_a_midnight_source():
    p = plan(
        [(ts(19, 10), "on"), (ts(20, 10), "off")],
        [(ts(20, 10), "on"), (ts(25), "on")], end=ts(23),
    )
    assert p.conflicting_exact == 1
    assert [(r.timestamp, r.value) for r in p.support_rows] == [
        (ts(20), "on")
    ]
