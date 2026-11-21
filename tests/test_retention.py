"""Recorder retention policy is applied to both preview and import plans."""

from custom_components.more_records.csv_import import HistoryRow
from custom_components.more_records.merge_policy import (
    plan_prefix,
    retention_boundary,
)

import pytest

DAY = 86400.0
NOW = 2_000_000_000.0


def test_auto_purge_uses_live_keep_days_not_a_hardcoded_ten_days():
    assert retention_boundary(True, 3, NOW) == NOW - 3 * DAY
    assert retention_boundary(True, 30, NOW) == NOW - 30 * DAY


def test_disabled_auto_purge_has_no_time_limit():
    assert retention_boundary(False, 10, NOW) is None
    assert retention_boundary(False, 1, NOW) is None


def test_default_ha_ten_day_window():
    cutoff = retention_boundary(True, 10, NOW)
    rows = [
        HistoryRow(NOW - 11 * DAY, "old"),
        HistoryRow(NOW - 10 * DAY, "boundary"),
        HistoryRow(NOW - 9 * DAY, "eligible"),
        HistoryRow(NOW - 8 * DAY, "overlap"),
    ]
    result = plan_prefix(rows, NOW - 8.5 * DAY, purge_before=cutoff)
    assert [r.value for r in result.eligible] == ["boundary", "eligible"]
    assert result.skipped_retention == 1
    assert result.skipped_overlap == 1
    assert result.skipped == 2
    assert result.total == 4


def test_all_source_rows_expired():
    rows = [HistoryRow(100, "v1"), HistoryRow(200, "v2")]
    result = plan_prefix(rows, 300, purge_before=250)
    assert result.eligible == []
    assert result.skipped_retention == 2
    assert result.skipped_overlap == 0


def test_all_rows_in_existing_recorder_are_skipped_before_retention():
    rows = [HistoryRow(400, "v1"), HistoryRow(500, "v2")]
    result = plan_prefix(rows, 300, purge_before=450)
    assert not result.eligible
    assert result.skipped_overlap == 2
    assert result.skipped_retention == 0


def test_disabled_purge_imports_old_rows():
    rows = [HistoryRow(1, "old"), HistoryRow(2, "middle")]
    result = plan_prefix(
        rows, 3, purge_before=retention_boundary(False, 10, NOW)
    )
    assert result.eligible == rows


def test_cutoff_equal_to_ha_oldest_yields_no_import():
    rows = [HistoryRow(99, "old"), HistoryRow(100, "existing")]
    result = plan_prefix(rows, 100, purge_before=100)
    assert result.eligible == []
    assert result.skipped_retention == 1
    assert result.skipped_overlap == 1


def test_invalid_keep_days_fail_closed():
    for days in (0, -1, True, 3.0):
        with pytest.raises(ValueError, match="purge_keep_days"):
            retention_boundary(True, days, NOW)


def test_reject_non_finite_now_when_purge_enabled():
    with pytest.raises(ValueError):
        retention_boundary(True, 10, float("nan"))
