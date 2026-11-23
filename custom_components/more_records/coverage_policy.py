"""Pure helpers for explicit CCU-Historian coverage verification."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .csv_import import HistoryRow

MAX_INTERVALS = 2048

def state_fingerprint(rows: list[tuple[float, str]]) -> str:
    """Stable SHA-256 of all ordered persisted samples in a verified range."""
    digest = hashlib.sha256()
    for timestamp, state in rows:
        digest.update(json.dumps([float(timestamp).hex(), str(state)], separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


class HistoryVerificationError(ValueError):
    """The complete source stream does not match what Recorder stores."""


def attestation_range(
    rows: list[HistoryRow], start: float, end: float
) -> tuple[float, float, list[HistoryRow]]:
    """Do not claim a state before the first actual source observation."""
    if not all(math.isfinite(value) for value in (start, end)) or end <= start:
        raise HistoryVerificationError("Invalid historical query interval")
    included = [row for row in rows if start <= row.timestamp < end]
    if not included or any(
        current.timestamp >= following.timestamp
        for current, following in zip(included, included[1:])
    ):
        raise HistoryVerificationError("No ordered source measurements to verify")
    return included[0].timestamp, end, included


def compare_recorded_rows(
    expected: list[HistoryRow], actual: list[tuple[float, str]]
) -> None:
    """Require every source observation; permit only redundant Recorder rows.

    Home Assistant can store repeated identical states between Historian
    transitions. Those rows do not change the observed value and must not
    invalidate an otherwise complete source series. A missing source point,
    an extra *change*, or a conflict is always a verification failure.
    """
    source_index = 0
    last_value: str | None = None
    for timestamp, state in actual:
        if source_index < len(expected):
            source = expected[source_index]
            if abs(timestamp - source.timestamp) <= 0.0001:
                if state != source.value:
                    break
                last_value = source.value
                source_index += 1
                continue
            # An unmatched Recorder sample must be strictly between two
            # source points; do not trust values before the first source
            # observation, or consume a missing source transition.
            if timestamp >= source.timestamp:
                break
        if last_value is None or state != last_value:
            break
    else:
        if source_index == len(expected):
            return

    raise HistoryVerificationError(
        "Recorder contains a missing/conflicting Historian sample or "
        "an additional state transition; history coverage was NOT certified"
    )


def normalize_intervals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject intersecting claims for the same entity; keep exact fingerprints."""
    valid = []
    for entry in items:
        try:
            start, end = float(entry["start"]), float(entry["end"])
            entity = entry["entity_id"]
            fingerprint = entry["fingerprint"]
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not isinstance(entity, str) or "." not in entity
            or not math.isfinite(start) or not math.isfinite(end)
            or end <= start or entry.get("source") != "ccu_historian"
            or entry.get("evidence") != "confirmed_query_and_recorder_match"
            or not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            continue
        valid.append({
            "entity_id": entity, "start": start, "end": end,
            "source": "ccu_historian",
            "evidence": "confirmed_query_and_recorder_match",
            "fingerprint": fingerprint,
        })
    valid.sort(key=lambda item: (item["entity_id"], item["start"], item["end"]))
    result: list[dict[str, Any]] = []
    for item in valid:
        if (result and result[-1]["entity_id"] == item["entity_id"]
                and item["start"] < result[-1]["end"]):
            # A later exact re-verification replaces the previous attestation.
            # Other overlapping intervals are rejected rather than combining
            # fingerprints from potentially different source observations.
            if item["start"] == result[-1]["start"] and item["end"] == result[-1]["end"]:
                result[-1] = item
                continue
            raise HistoryVerificationError("Overlapping attested intervals")
        result.append(item)
    if len(result) > MAX_INTERVALS:
        raise HistoryVerificationError("Too many attested historical ranges")
    return result


