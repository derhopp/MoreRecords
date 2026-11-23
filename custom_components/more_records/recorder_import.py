"""Atomic, exact-timestamp Recorder merge for existing entities (SQLite only).

Existing Recorder state rows are never replaced. The Recorder worker rechecks
source/target and retention before any insertion, and updates predecessor
links only when newly inserted rows change the chronological predecessor.
No historical state_changed events or service calls are emitted.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
import time
from typing import Any

from sqlalchemy import insert, select, text

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.components.recorder.tasks import RecorderTask

from .csv_import import HistoryRow
from .history_merge import (
    MAX_MERGE_STATES,
    GapMode,
    MergePlan,
    StoredState,
    plan_missing,
    predecessor_updates,
)
from .merge_policy import retention_boundary


class RecorderImportError(ValueError):
    """Unsafe Recorder state merge or database failure."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """Actual rows inserted and retained original-history conflicts."""

    inserted: int
    skipped_overlap: int
    skipped_retention: int
    skipped_after_latest: int = 0
    conflicting_exact: int = 0
    synthetic_rows: int = 0
    daily_support_rows: int = 0

    @property
    def skipped(self) -> int:
        return (
            self.skipped_overlap + self.skipped_retention
            + self.skipped_after_latest
        )


def _get_first(
    recorder: Any, entity_id: str
) -> tuple[int, float, int | None, int | None] | None:
    """Keep the existing public helper's result form for compatibility."""
    with recorder.get_session() as session:
        meta_id = session.scalar(
            select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
        )
        if meta_id is None:
            return None
        row = session.execute(
            select(
                States.state_id, States.last_updated_ts,
                States.old_state_id, States.attributes_id,
            )
            .where(States.metadata_id == meta_id)
            .where(States.last_updated_ts.is_not(None))
            .order_by(States.last_updated_ts, States.state_id)
            .limit(1)
        ).first()
        return tuple(row) if row else None


async def async_first_state_timestamp(hass: Any, entity_id: str) -> float | None:
    """Return first stored state timestamp."""
    recorder = get_instance(hass)
    row = await recorder.async_add_executor_job(_get_first, recorder, entity_id)
    return row[1] if row else None


def _existing(session: Any, entity_id: str) -> tuple[int, list[StoredState], dict[int, float | None]]:
    """Read all existing rows, fail closed if too large to verify exactly."""
    metadata_id = session.scalar(
        select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
    )
    if metadata_id is None:
        raise RecorderImportError("Entity has no Recorder metadata")
    query = (
        select(
            States.state_id, States.last_updated_ts, States.state,
            States.attributes_id, States.old_state_id, States.last_changed_ts,
        )
        .where(States.metadata_id == metadata_id)
        .where(States.last_updated_ts.is_not(None))
        .order_by(States.last_updated_ts, States.state_id)
        .limit(MAX_MERGE_STATES + 1)
    )
    data = session.execute(query).all()
    if not data:
        raise RecorderImportError("Entity has no stored Recorder states")
    if len(data) > MAX_MERGE_STATES:
        raise RecorderImportError(
            "Too many existing Recorder states for a safe merge"
        )
    rows = [
        StoredState(int(r[0]), float(r[1]), str(r[2]), r[3], r[4])
        for r in data
    ]
    changes = {int(r[0]): r[5] for r in data}
    return metadata_id, rows, changes


def _plan(
    recorder: Any, entity_id: str, source: list[HistoryRow], mode: GapMode,
    support_window: tuple[float, float] | None, support_timezone: str | None,
) -> MergePlan:
    """Read-only preview against live Recorder data and retention."""
    if recorder.engine is None or recorder.engine.dialect.name != "sqlite":
        raise RecorderImportError("Only SQLite Recorder databases are supported")
    with recorder.get_session() as session:
        _, existing, _ = _existing(session, entity_id)
        boundary = retention_boundary(
            recorder.auto_purge, recorder.keep_days, time.time()
        )
        return plan_missing(
            source, existing, mode, purge_before=boundary,
            support_window=support_window, support_timezone=support_timezone,
        )


async def async_preview_merge(
    hass: Any, entity_id: str, source: list[HistoryRow], mode: GapMode,
    *, support_window: tuple[float, float] | None = None,
    support_timezone: str | None = None,
) -> MergePlan:
    """Create the preview on a database executor, never on HA's event loop."""
    recorder = get_instance(hass)
    return await recorder.async_add_executor_job(
        _plan, recorder, entity_id, source, mode,
        support_window, support_timezone,
    )


def _write(
    recorder: Any, entity_id: str, source: list[HistoryRow],
    mode: GapMode, preview_signature: str,
    support_window: tuple[float, float] | None, support_timezone: str | None,
) -> ImportOutcome:
    """Recheck preview under a single SQLite writer transaction and merge."""
    if recorder.engine is None or recorder.engine.dialect.name != "sqlite":
        raise RecorderImportError("Only SQLite Recorder databases are supported")
    with recorder.get_session() as session:
        try:
            metadata_id, existing, changed_ts = _existing(session, entity_id)
            boundary = retention_boundary(
                recorder.auto_purge, recorder.keep_days, time.time()
            )
            plan = plan_missing(
                source, existing, mode, purge_before=boundary,
                support_window=support_window, support_timezone=support_timezone,
            )
            if plan.signature != preview_signature:
                raise RecorderImportError(
                    "Recorder states or import eligibility changed since preview; "
                    "start a new import to review the new merge plan"
                )
            if not plan.eligible and not plan.support_rows:
                return ImportOutcome(
                    0, plan.skipped_exact, plan.skipped_retention,
                    plan.skipped_after_latest, plan.conflicting_exact
                )

            additions = [*plan.eligible, *plan.support_rows]
            if plan.bridge is not None:
                additions.append(plan.bridge)
                additions.sort(key=lambda r: r.timestamp)

            existing_ids = {row.state_id for row in existing}
            # Mark synthetic rows explicitly as a separate local plan item,
            # never as verified CCU-Historian source history.
            # Compose the chronological state sequence for HA's last_changed
            # semantics and attribute selection. Original rows are unchanged.
            timeline: list[tuple[float, int, StoredState | HistoryRow]] = [
                (s.timestamp, s.state_id, s) for s in existing
            ]
            timeline.extend((row.timestamp, -1, row) for row in additions)
            timeline.sort(key=lambda row: (row[0], row[1]))

            previous_state: str | None = None
            last_changed: float | None = None
            attrs = existing[0].attributes_id
            new_values: list[dict[str, Any]] = []
            for _, _, item in timeline:
                if isinstance(item, StoredState):
                    attrs = item.attributes_id
                    previous_state = item.value
                    last_changed = (
                        float(changed_ts[item.state_id])
                        if changed_ts[item.state_id] is not None
                        else item.timestamp
                    )
                    continue
                changed = item.value != previous_state
                new_values.append({
                    "metadata_id": metadata_id,
                    "state": item.value,
                    "last_updated_ts": item.timestamp,
                    "last_changed_ts": None if changed else last_changed,
                    "last_reported_ts": None,
                    "attributes_id": attrs,
                    "old_state_id": None,
                    "origin_idx": 0,
                })
                if changed:
                    last_changed = item.timestamp
                previous_state = item.value

            for offset in range(0, len(new_values), 5000):
                session.execute(insert(States), new_values[offset:offset + 5000])

            # Re-read inserted IDs, then relink only new rows and original rows
            # whose immediately preceding chronological state is now new.
            merged = session.execute(
                select(
                    States.state_id, States.last_updated_ts, States.old_state_id
                )
                .where(States.metadata_id == metadata_id)
                .where(States.last_updated_ts.is_not(None))
                .order_by(States.last_updated_ts, States.state_id)
            ).all()
            updates, inserted_count = predecessor_updates(
                [(int(row[0]), float(row[1]), row[2]) for row in merged],
                existing_ids,
            )

            if inserted_count != len(additions):
                raise RecorderImportError(
                    "Recorder changed during merge; refusing partial insertion"
                )
            if updates:
                session.execute(
                    text(
                        "UPDATE states SET old_state_id = :prev_id "
                        "WHERE state_id = :state_id"
                    ),
                    updates,
                )
            session.commit()
            return ImportOutcome(
                len(plan.eligible),
                plan.skipped_exact,
                plan.skipped_retention,
                plan.skipped_after_latest,
                plan.conflicting_exact,
                int(plan.bridge is not None),
                len(plan.support_rows),
            )
        except Exception:
            session.rollback()
            raise


@dataclass(slots=True)
class _MergeTask(RecorderTask):
    """Perform a merge on the Recorder's serialized writer thread."""

    done: concurrent.futures.Future[ImportOutcome]
    entity_id: str
    source: list[HistoryRow]
    mode: GapMode
    preview_signature: str
    support_window: tuple[float, float] | None
    support_timezone: str | None

    def run(self, instance: Any) -> None:
        try:
            self.done.set_result(
                _write(instance, self.entity_id, self.source,
                       self.mode, self.preview_signature,
                       self.support_window, self.support_timezone)
            )
        except Exception as err:
            self.done.set_exception(err)


async def async_merge_states(
    hass: Any, entity_id: str, source: list[HistoryRow],
    mode: GapMode, preview_signature: str,
    *, support_window: tuple[float, float] | None = None,
    support_timezone: str | None = None,
) -> ImportOutcome:
    """Merge missing timestamps and optionally bridge the leading gap."""
    recorder = get_instance(hass)
    future: concurrent.futures.Future[ImportOutcome] = concurrent.futures.Future()
    recorder.queue_task(
        _MergeTask(
            future, entity_id, source, mode, preview_signature,
            support_window, support_timezone,
        )
    )
    return await asyncio.wrap_future(future)
