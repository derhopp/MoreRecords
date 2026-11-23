"""Coverage claims must be explicit, entity-scoped, and bound to Recorder contents."""

import pytest

from custom_components.more_records.coverage_policy import (
    HistoryVerificationError,
    attestation_range,
    compare_recorded_rows,
    normalize_intervals,
    state_fingerprint,
)
from custom_components.more_records.csv_import import HistoryRow


def claim(entity, start, end, fingerprint):
    return {
        "entity_id": entity,
        "start": start,
        "end": end,
        "source": "ccu_historian",
        "evidence": "confirmed_query_and_recorder_match",
        "fingerprint": fingerprint,
    }


def test_silent_weeks_are_covered_only_after_a_real_source_state():
    one_week = 7 * 86400.0
    rows = [HistoryRow(1000.0, "on")]
    start, end, matched = attestation_range(
        rows, 500.0, 1000.0 + 6 * one_week
    )
    assert start == 1000.0
    assert end == 1000.0 + 6 * one_week
    assert matched == rows


def test_no_state_means_no_evidence_for_a_silent_interval():
    with pytest.raises(HistoryVerificationError):
        attestation_range([], 100.0, 900.0)


def test_a_purged_or_changed_state_invalidates_verified_fingerprint():
    expected = [HistoryRow(10.0, "on"), HistoryRow(50.0, "off")]
    persisted = [(10.0, "on"), (50.0, "off")]
    compare_recorded_rows(expected, persisted)
    certificate = state_fingerprint(persisted)
    assert certificate != state_fingerprint([(10.0, "on")])
    assert certificate != state_fingerprint([(10.0, "on"), (50.0, "on")])
    assert certificate != state_fingerprint(persisted + [(70.0, "off")])


@pytest.mark.parametrize(
    "other",
    [[(10.0, "on")], [(10.0, "off"), (50.0, "off")],
     [(10.0, "on"), (60.0, "off")]],
)
def test_a_mismatch_does_not_create_evidence(other):
    expected = [HistoryRow(10.0, "on"), HistoryRow(50.0, "off")]
    with pytest.raises(HistoryVerificationError):
        compare_recorded_rows(expected, other)


def test_ranges_must_be_bound_to_one_entity_and_one_source():
    fingerprint = state_fingerprint([(10.0, "on")])
    items = [
        claim("binary_sensor.present", 10.0, 100.0, fingerprint),
        claim("light.a", 20.0, 120.0, fingerprint),
    ]
    assert len(normalize_intervals(items)) == 2
    assert normalize_intervals([dict(items[0], source="csv")]) == []
    assert normalize_intervals([dict(items[0], fingerprint="invalid")]) == []


def test_overlapping_ranges_never_merge_unrelated_fingerprints():
    h1 = state_fingerprint([(10.0, "on")])
    h2 = state_fingerprint([(50.0, "off")])
    with pytest.raises(HistoryVerificationError, match="Overlapping"):
        normalize_intervals([
            claim("light.a", 10.0, 100.0, h1),
            claim("light.a", 50.0, 120.0, h2),
        ])


def test_identical_range_can_be_reverified_after_data_changes():
    first = claim("light.a", 10.0, 100.0, "a" * 64)
    second = claim("light.a", 10.0, 100.0, "b" * 64)
    assert normalize_intervals([first, second]) == [second]


def test_timestamp_or_range_errors_are_rejected():
    with pytest.raises(HistoryVerificationError):
        attestation_range([HistoryRow(100.0, "on")], 100.0, 100.0)
    with pytest.raises(HistoryVerificationError):
        attestation_range([
            HistoryRow(20.0, "off"), HistoryRow(10.0, "on")
        ], 0.0, 30.0)



def test_redundant_home_assistant_samples_are_accepted():
    """Repeated identical states do not add unobserved transitions."""
    expected = [HistoryRow(10.0, "on"), HistoryRow(50.0, "off")]
    recorder = [
        (10.0, "on"),
        (20.0, "on"),
        (49.0, "on"),
        (50.0, "off"),
        (60.0, "off"),
    ]
    compare_recorded_rows(expected, recorder)
    # Fingerprint still covers *all* persisted observations.
    assert state_fingerprint(recorder) != state_fingerprint(recorder[:-1])


@pytest.mark.parametrize(
    "recorder",
    [
        [(10.0, "on"), (20.0, "off"), (30.0, "on"), (50.0, "off")],
        [(10.0, "on"), (20.0, "on"), (50.0, "on")],
        [(10.0, "on"), (20.0, "on")],
        [(5.0, "on"), (10.0, "on"), (50.0, "off")],
        [(10.0, "on"), (50.0, "off"), (60.0, "on")],
    ],
)
def test_missing_or_extra_changes_never_get_certified(recorder):
    expected = [HistoryRow(10.0, "on"), HistoryRow(50.0, "off")]
    with pytest.raises(HistoryVerificationError):
        compare_recorded_rows(expected, recorder)
