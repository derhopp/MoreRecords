"""Unit tests for conservative Recorder overlap handling."""
from custom_components.more_records.csv_import import HistoryRow
from custom_components.more_records.merge_policy import plan_prefix
import pytest


def test_no_overlap_imports_all():
    rows = [HistoryRow(100, "1"), HistoryRow(200, "2")]
    p = plan_prefix(rows, 300)
    assert p.eligible == rows
    assert p.skipped == 0


def test_csv_partly_overlaps_ha_imports_older_only():
    rows = [HistoryRow(100, "1"), HistoryRow(200, "2"),
            HistoryRow(300, "3"), HistoryRow(400, "4")]
    p = plan_prefix(rows, 300)
    assert p.eligible == rows[:2]
    assert p.skipped == 2


def test_exact_boundary_is_skipped():
    p = plan_prefix([HistoryRow(100, "1"), HistoryRow(200, "2")], 200)
    assert [x.timestamp for x in p.eligible] == [100]
    assert p.skipped == 1


def test_entirely_covered_source_is_skipped():
    p = plan_prefix([HistoryRow(500, "1")], 200)
    assert not p.eligible and p.skipped == 1


def test_out_of_order_fails_closed():
    with pytest.raises(ValueError, match="strictly increasing"):
        plan_prefix([HistoryRow(200, "2"), HistoryRow(100, "1")], 300)


def test_duplicates_fail_closed():
    with pytest.raises(ValueError, match="strictly increasing"):
        plan_prefix([HistoryRow(100, "1"), HistoryRow(100, "1")], 300)
