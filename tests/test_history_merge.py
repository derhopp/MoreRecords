"""Regression tests for safe Recorder merges and explicit gap-state modes."""

import pytest

from custom_components.more_records.csv_import import HistoryRow
from custom_components.more_records.history_merge import (
    StoredState, plan_missing, predecessor_updates,
)


def src(*points):
    return [HistoryRow(float(ts), value) for ts, value in points]


def stored(*points):
    return [
        StoredState(i + 100, float(ts), value, 7, None)
        for i, (ts, value) in enumerate(points)
    ]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("hold_last", None),
        ("oldest_recorder", "off"),
        ("unavailable", "unavailable"),
    ],
)
def test_prefix_gap_transition_modes_are_explicit(mode, expected):
    originals = stored((100.0, "off"), (120.0, "on"))
    plan = plan_missing(
        src((20, "off"), (90, "on"), (110, "off")),
        originals,
        mode,
    )
    assert [r.timestamp for r in plan.eligible] == [20.0, 90.0, 110.0]
    if expected is None:
        assert plan.bridge is None
    else:
        assert plan.bridge is not None
        assert plan.bridge.value == expected
        assert 90.0 < plan.bridge.timestamp < 100.0
        assert plan.bridge_next_state_id == 100


def test_no_bridge_when_oldest_recorder_state_equals_last_imported():
    plan = plan_missing(
        src((50, "on"), (80, "off")),
        stored((100, "off")),
        "oldest_recorder",
    )
    assert plan.bridge is None


def test_no_synthetic_bridge_when_only_internal_gaps_are_filled():
    plan = plan_missing(
        src((110, "on")),
        stored((100, "off"), (120, "on")),
        "unavailable",
    )
    assert plan.bridge is None
    assert [r.timestamp for r in plan.eligible] == [110]


def test_exact_timestamp_is_never_overwritten_even_if_source_disagrees():
    plan = plan_missing(
        src((90, "on"), (100, "on"), (110, "off"), (120, "off")),
        stored((100, "off"), (120, "on")),
        "hold_last",
    )
    assert [r.timestamp for r in plan.eligible] == [90, 110]
    assert plan.skipped_exact == 2
    assert plan.conflicting_exact == 2
    assert plan.skipped == 2


def test_source_beyond_last_recorded_is_not_inserted():
    plan = plan_missing(
        src((95, "off"), (100, "off"), (150, "on")),
        stored((100, "off"), (120, "on")),
        "hold_last",
    )
    assert [r.timestamp for r in plan.eligible] == [95]
    assert plan.skipped_exact == 1
    assert plan.skipped_after_latest == 1


def test_purge_boundary_filters_old_rows_even_if_history_starts_earlier():
    plan = plan_missing(
        src((20, "on"), (60, "off"), (90, "on"), (110, "off")),
        stored((100, "off"), (120, "off")),
        "unavailable", purge_before=50,
    )
    assert [r.timestamp for r in plan.eligible] == [60, 90, 110]
    assert plan.skipped_retention == 1
    assert plan.bridge.value == "unavailable"


def test_preview_signature_detects_recorded_changes():
    readings = src((90, "on"), (110, "off"))
    baseline = plan_missing(readings, stored((100, "off"), (120, "on")), "hold_last")
    changed = plan_missing(
        readings, stored((100, "off"), (120, "off")), "hold_last"
    )
    assert baseline.signature != changed.signature


def test_preview_signature_detects_different_gap_choice():
    readings = src((90, "on"))
    originals = stored((100, "off"))
    assert (
        plan_missing(readings, originals, "hold_last").signature
        != plan_missing(readings, originals, "unavailable").signature
    )


def test_preview_signature_detects_purged_eligibility():
    readings = src((30, "on"), (60, "off"))
    originals = stored((100, "off"))
    first = plan_missing(readings, originals, "hold_last", purge_before=20)
    second = plan_missing(readings, originals, "hold_last", purge_before=50)
    assert first.signature != second.signature


def test_invalid_gap_mode_is_rejected():
    with pytest.raises(ValueError, match="gap state"):
        plan_missing(src((90, "on")), stored((100, "off")), "unknown")


def test_too_short_gap_rejected_for_synthetic_marker():
    original = 1_800_000_000.0
    # At modern Unix timestamps, 1e-6 seconds remains representable,
    # but no microsecond is available before the next existing state.
    last = original - 0.0000002384
    with pytest.raises(ValueError, match="too close"):
        plan_missing(
            src((last, "on")),
            stored((original, "off")),
            "oldest_recorder",
        )


def test_same_timestamp_ordering_is_stable_for_stored_rows():
    rows = [
        StoredState(100, 100.0, "off"),
        StoredState(101, 100.0, "on"),
        StoredState(102, 120.0, "off"),
    ]
    plan = plan_missing(
        src((90, "on"), (100, "off"), (110, "on")),
        rows, "hold_last",
    )
    assert plan.skipped_exact == 1
    assert plan.conflicting_exact == 1  # latest stored row at t=100 is on
    assert [r.timestamp for r in plan.eligible] == [90, 110]


def test_predecessor_relink_only_changes_insertions_and_successors():
    """Original-to-original links must not be overwritten unnecessarily."""
    original_ids = {1, 2, 3}
    rows = [
        (9, 10.0, None),     # new prefix state
        (1, 20.0, None),     # old first row; link to new prefix
        (10, 30.0, None),    # new internal state; link to original 1
        (2, 40.0, 1),       # existing successor; now link to new 10
        (3, 50.0, None),    # legacy missing link, but not impacted: preserve
    ]
    updates, count = predecessor_updates(rows, original_ids)
    assert count == 2
    assert updates == [
        {"state_id": 1, "prev_id": 9},
        {"state_id": 10, "prev_id": 1},
        {"state_id": 2, "prev_id": 10},
    ]


def test_unmodified_recorder_chain_generates_no_updates():
    rows = [(1, 10.0, None), (2, 20.0, 1)]
    updates, count = predecessor_updates(rows, {1, 2})
    assert updates == []
    assert count == 0
