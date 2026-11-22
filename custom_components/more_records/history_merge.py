"""Pure policy for exact-timestamp Recorder merges and explicit prefix transitions.

Existing Recorder rows are authoritative. Only source points at missing
timestamps are candidates for insertion; no original state/value/attributes
is replaced. A chosen bridging state applies exclusively to the interval
between the last imported prefix state and the first stored Recorder state.
Such synthetic states are NOT CCU-Historian evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Literal

from .csv_import import HistoryRow

GapMode = Literal["hold_last", "oldest_recorder", "unavailable"]
GAP_MODES = frozenset(("hold_last", "oldest_recorder", "unavailable"))
MAX_MERGE_STATES = 100_000


@dataclass(frozen=True, slots=True)
class StoredState:
    """One existing Recorder state, ordered by UTC timestamp and state ID."""

    state_id: int
    timestamp: float
    value: str
    attributes_id: int | None = None
    old_state_id: int | None = None


@dataclass(frozen=True, slots=True)
class MergePlan:
    """A reproducible preview of missing source points and a gap transition."""

    eligible: list[HistoryRow]
    skipped_exact: int
    skipped_retention: int
    skipped_after_latest: int
    conflicting_exact: int
    oldest_existing: float
    latest_existing: float
    purge_before: float | None
    gap_mode: str
    bridge: HistoryRow | None
    bridge_next_state_id: int | None
    total: int
    signature: str

    @property
    def skipped(self) -> int:
        """Total source points that cannot be inserted."""
        return (
            self.skipped_exact + self.skipped_retention
            + self.skipped_after_latest
        )

    @property
    def skipped_overlap(self) -> int:
        """For compatibility with the old preview field."""
        return self.skipped_exact


def _signature(
    source: list[HistoryRow],
    existing: list[StoredState],
    eligible: list[HistoryRow],
    bridge: HistoryRow | None,
    gap_mode: str,
    purge_before: float | None,
) -> str:
    """Bind user confirmation to a precise source and stored-history snapshot."""
    digest = hashlib.sha256()
    for prefix, items in (
        ("source", ((r.timestamp, r.value) for r in source)),
        (
            "stored",
            (
                (r.state_id, r.timestamp, r.value, r.attributes_id, r.old_state_id)
                for r in existing
            ),
        ),
        ("eligible", ((r.timestamp, r.value) for r in eligible)),
        ("bridge", ((bridge.timestamp, bridge.value),) if bridge else ()),
    ):
        digest.update((prefix + ":").encode())
        for item in items:
            digest.update(repr(tuple(item)).encode())
            digest.update(b"\n")
    digest.update(f"{gap_mode}|{purge_before is not None}".encode())
    return digest.hexdigest()


def plan_missing(
    rows: list[HistoryRow],
    existing: list[StoredState],
    gap_mode: GapMode,
    *,
    purge_before: float | None = None,
) -> MergePlan:
    """Keep missing source timestamps while never overwriting stored states.

    A 'hold_last' mode needs no marker: Home Assistant carries forward the
    most recent state. Other modes add a synthetic transition 1 microsecond
    after the last imported prefix state, prior to the FIRST stored state.
    The marker is omitted when the desired state already equals that value.
    """
    if gap_mode not in GAP_MODES:
        raise ValueError("Choose a valid gap state mode")
    if not rows or not existing:
        raise ValueError("Source and Recorder must both contain state rows")
    if len(rows) > MAX_MERGE_STATES or len(existing) > MAX_MERGE_STATES:
        raise ValueError("Too many rows for a safe merge; use a shorter interval")
    if any(not math.isfinite(r.timestamp) for r in rows):
        raise ValueError("Invalid source timestamp")
    if any(a.timestamp >= b.timestamp for a, b in zip(rows, rows[1:])):
        raise ValueError("Source timestamps must be strictly increasing")
    if any(
        (a.timestamp, a.state_id) >= (b.timestamp, b.state_id)
        for a, b in zip(existing, existing[1:])
    ):
        raise ValueError("Stored Recorder states must be ordered")
    if purge_before is not None and not math.isfinite(purge_before):
        raise ValueError("Invalid Recorder retention boundary")

    stored_at_time = {r.timestamp: r for r in existing}
    oldest, newest = existing[0].timestamp, existing[-1].timestamp
    eligible: list[HistoryRow] = []
    skip_exact = skip_retention = skip_future = conflicts = 0
    for row in rows:
        if purge_before is not None and row.timestamp < purge_before:
            skip_retention += 1
        elif row.timestamp > newest:
            # Never insert future-source states beyond the current HA tail.
            skip_future += 1
        elif (stored := stored_at_time.get(row.timestamp)) is not None:
            skip_exact += 1
            if stored.value != row.value:
                conflicts += 1
        else:
            eligible.append(row)

    bridge: HistoryRow | None = None
    bridge_next_state_id: int | None = None
    prefix = [row for row in eligible if row.timestamp < oldest]
    if prefix and gap_mode != "hold_last":
        last_prefix = prefix[-1]
        desired = (
            existing[0].value if gap_mode == "oldest_recorder"
            else "unavailable"
        )
        if last_prefix.value != desired:
            # A state at this timestamp becomes active immediately AFTER
            # the last raw imported sample, avoiding a fabricated event time
            # at the HA first state or a changed original Recorder row.
            marker = last_prefix.timestamp + 0.000001
            if not (last_prefix.timestamp < marker < oldest):
                raise ValueError(
                    "The imported and existing states are too close to bridge"
                )
            bridge = HistoryRow(marker, desired)
            bridge_next_state_id = existing[0].state_id

    return MergePlan(
        eligible=eligible,
        skipped_exact=skip_exact,
        skipped_retention=skip_retention,
        skipped_after_latest=skip_future,
        conflicting_exact=conflicts,
        oldest_existing=oldest,
        latest_existing=newest,
        purge_before=purge_before,
        gap_mode=gap_mode,
        bridge=bridge,
        bridge_next_state_id=bridge_next_state_id,
        total=len(rows),
        signature=_signature(
            rows, existing, eligible, bridge, gap_mode, purge_before
        ),
    )


def predecessor_updates(
    chronological_rows: list[tuple[int, float, int | None]],
    original_ids: set[int],
) -> tuple[list[dict[str, int]], int]:
    """Relink newly inserted rows and only directly impacted original rows.

    The caller supplies the complete chronological Recorder snapshot AFTER
    insertion. Original rows whose predecessor is another original row are
    never modified, even if they contain legacy/incomplete link metadata.
    """
    updates: list[dict[str, int]] = []
    previous: int | None = None
    inserted = 0
    for state_id, _, old_state_id in chronological_rows:
        is_new = state_id not in original_ids
        if is_new:
            inserted += 1
        if (
            previous is not None
            and old_state_id != previous
            and (is_new or previous not in original_ids)
        ):
            updates.append({"state_id": state_id, "prev_id": previous})
        previous = state_id
    return updates, inserted
