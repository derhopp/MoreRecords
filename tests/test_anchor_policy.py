"""A purge anchor preserves only the latest observed pre-cutoff state."""

import pytest

from custom_components.more_records.anchor_policy import (
    StateAnchor, choose_predecessor, update_anchors, with_initial_state,
)


def anchor(ts, value="on"):
    return StateAnchor("light.test", ts, value)


def test_rolling_cutoff_and_sparse_state_changes():
    # State observed 400 days ago is needed for a query at -350 days.
    current = update_anchors({}, [anchor(100, "on")])
    assert choose_predecessor(150, None, current["light.test"]) == (
        100, "on", "retention_anchor"
    )
    assert with_initial_state([(200, "off")], 150, choose_predecessor(
        150, None, current["light.test"]
    )) == [(150, "on"), (200, "off")]
    # Move the purge boundary past a newer observation.
    current = update_anchors(current, [anchor(200, "off")])
    assert current["light.test"].timestamp == 200
    # A stale nightly collection cannot regress the anchor.
    assert update_anchors(current, [anchor(100, "on")]) == current


def test_original_recorder_predecessor_wins_when_newer():
    assert choose_predecessor(250, (210, "off"), anchor(100)) == (
        210, "off", "recorder"
    )
    assert choose_predecessor(250, (100, "off"), anchor(100)) == (
        100, "off", "recorder"
    )


def test_never_claim_state_before_original_observation():
    assert choose_predecessor(50, None, anchor(100)) is None
    assert with_initial_state([], 50, None) == []
    assert with_initial_state([(50, "off")], 50, (30, "on", "recorder")) == [
        (50, "off")
    ]


def test_a_previous_anchor_survives_when_all_candidates_were_purged():
    previous = {"light.test": anchor(100)}
    assert update_anchors(previous, []) == previous


def test_equal_timestamp_conflict_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        update_anchors({"light.test": anchor(100, "on")}, [anchor(100, "off")])


def test_invalid_anchor_rejected():
    with pytest.raises(ValueError):
        update_anchors({}, [anchor(float("nan"))])
