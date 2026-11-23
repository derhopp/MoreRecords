"""Pure retention-anchor selection and opt-in initial-state reconstruction.

An anchor is a previously *observed* Recorder state, never a claim that all
intermediate transitions were measured. It is stored outside Recorder and is
not itself a synthetic Recorder state change.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class StateAnchor:
    entity_id: str
    timestamp: float
    value: str


def update_anchors(
    previous: dict[str, StateAnchor], candidates: list[StateAnchor]
) -> dict[str, StateAnchor]:
    """Advance, but never regress, each entity's last pre-cutoff observation."""
    result = dict(previous)
    for candidate in candidates:
        if (
            "." not in candidate.entity_id
            or not math.isfinite(candidate.timestamp)
            or not isinstance(candidate.value, str)
        ):
            raise ValueError("Invalid retention-anchor observation")
        stored = result.get(candidate.entity_id)
        if stored is None or candidate.timestamp > stored.timestamp:
            result[candidate.entity_id] = candidate
        elif candidate.timestamp == stored.timestamp and candidate.value != stored.value:
            raise ValueError("Conflicting state at a retention-anchor timestamp")
    return result


def choose_predecessor(
    start_ts: float,
    recorded: tuple[float, str] | None,
    anchor: StateAnchor | None,
) -> tuple[float, str, str] | None:
    """Choose the latest real observation strictly before the requested start.

    The Recorder row wins timestamp ties. No inference is made for times before
    an observation or across missing source observations.
    """
    if not math.isfinite(start_ts):
        raise ValueError("Invalid requested start time")
    candidates: list[tuple[float, str, str]] = []
    if recorded is not None and math.isfinite(recorded[0]) and recorded[0] < start_ts:
        candidates.append((recorded[0], recorded[1], "recorder"))
    if anchor is not None and math.isfinite(anchor.timestamp) and anchor.timestamp < start_ts:
        candidates.append((anchor.timestamp, anchor.value, "retention_anchor"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[2] == "recorder"))


def with_initial_state(
    rows: list[tuple[float, str | None]],
    start_ts: float,
    predecessor: tuple[float, str, str] | None,
) -> list[tuple[float, str | None]]:
    """For an explicitly reconstructed CSV, add the start state if justified.

    This start-boundary row is synthetic for export only. It is not a recorder
    observation and must never be used as evidence of historian completeness.
    """
    if predecessor is None or (rows and rows[0][0] <= start_ts):
        return list(rows)
    return [(start_ts, predecessor[1]), *rows]
