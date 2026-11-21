"""Atomic Recorder backfill for an existing entity (SQLite only).

Recorder internals are private and version-sensitive. Run on a test Home
Assistant installation with a restorable backup before production use.
Imported points must both precede the first existing state and fall within
the Recorder retention window if auto_purge is enabled. Source points outside
these limits are skipped without modifying existing states.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, text, update

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.components.recorder.tasks import RecorderTask

from .csv_import import HistoryRow
from .merge_policy import plan_prefix, retention_boundary


class RecorderImportError(ValueError):
    """Safety guard or Recorder write failed."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """Counts from the committed transaction, not just the earlier preview."""

    inserted: int
    skipped_overlap: int
    skipped_retention: int

    @property
    def skipped(self) -> int:
        """Return all skipped source observations."""
        return self.skipped_overlap + self.skipped_retention


def _get_first(
    recorder: Any, entity_id: str
) -> tuple[int, float, int | None, int | None] | None:
    """Return first state ID, timestamp, predecessor and attributes ID."""
    with recorder.get_session() as session:
        metadata_id = session.scalar(
            select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
        )
        if metadata_id is None:
            return None
        row = session.execute(
            select(
                States.state_id, States.last_updated_ts,
                States.old_state_id, States.attributes_id,
            )
            .where(States.metadata_id == metadata_id)
            .where(States.last_updated_ts.is_not(None))
            .order_by(States.last_updated_ts, States.state_id)
            .limit(1)
        ).first()
        return tuple(row) if row is not None else None


async def async_first_state_timestamp(hass: Any, entity_id: str) -> float | None:
    """Return earliest stored state time in a Recorder DB executor."""
    recorder = get_instance(hass)
    first = await recorder.async_add_executor_job(_get_first, recorder, entity_id)
    return first[1] if first else None


def _write(recorder: Any, entity_id: str, source_rows: list[HistoryRow]) -> ImportOutcome:
    """Recheck cutoff and atomically prepend the non-overlapping prefix."""
    if recorder.engine is None or recorder.engine.dialect.name != "sqlite":
        raise RecorderImportError("Only SQLite Recorder databases are supported")
    if not source_rows:
        raise RecorderImportError("No data to import")

    with recorder.get_session() as session:
        try:
            metadata_id = session.scalar(
                select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
            )
            if metadata_id is None:
                raise RecorderImportError("Entity has no Recorder metadata")
            first = session.execute(
                select(
                    States.state_id,
                    States.last_updated_ts,
                    States.old_state_id,
                    States.attributes_id,
                )
                .where(States.metadata_id == metadata_id)
                .where(States.last_updated_ts.is_not(None))
                .order_by(States.last_updated_ts, States.state_id)
                .limit(1)
            ).first()
            if first is None:
                raise RecorderImportError("Entity has no recorded states")
            first_id, cutoff, old_id, attrs_id = first
            if old_id is not None:
                raise RecorderImportError(
                    "Earliest state references an unexpected predecessor"
                )

            # Re-evaluate the live Recorder configuration and current UTC time
            # inside the write transaction. A long-open preview cannot bypass
            # auto_purge or import data outside the current retention window.
            purge_before = retention_boundary(
                recorder.auto_purge, recorder.keep_days, time.time()
            )
            plan = plan_prefix(
                source_rows, float(cutoff), purge_before=purge_before
            )
            if not plan.eligible:
                return ImportOutcome(
                    0, plan.skipped_overlap, plan.skipped_retention
                )

            previous_value: str | None = None
            last_change: float | None = None
            for offset in range(0, len(plan.eligible), 5000):
                chunk = []
                for row in plan.eligible[offset:offset + 5000]:
                    changed = row.value != previous_value
                    if changed:
                        last_change = row.timestamp
                    chunk.append(
                        {
                            "metadata_id": metadata_id,
                            "state": row.value,
                            "last_updated_ts": row.timestamp,
                            "last_changed_ts": None if changed else last_change,
                            "last_reported_ts": None,
                            "attributes_id": attrs_id,
                            "old_state_id": None,
                            "origin_idx": 0,
                        }
                    )
                    previous_value = row.value
                session.execute(insert(States), chunk)

            session.execute(
                text(
                    """
                    WITH predecessors AS (
                        SELECT state_id,
                               LAG(state_id) OVER (
                                   ORDER BY last_updated_ts, state_id
                               ) AS prev_id
                        FROM states
                        WHERE metadata_id = :metadata_id
                          AND last_updated_ts < :cutoff
                    )
                    UPDATE states
                    SET old_state_id = (
                        SELECT prev_id FROM predecessors
                        WHERE predecessors.state_id = states.state_id
                    )
                    WHERE state_id IN (SELECT state_id FROM predecessors)
                    """
                ),
                {"metadata_id": metadata_id, "cutoff": cutoff},
            )
            last_inserted = session.scalar(
                select(States.state_id)
                .where(States.metadata_id == metadata_id)
                .where(States.last_updated_ts < cutoff)
                .order_by(States.last_updated_ts.desc(), States.state_id.desc())
                .limit(1)
            )
            if last_inserted is None:
                raise RecorderImportError("No states were inserted")
            updated = session.execute(
                update(States)
                .where(States.state_id == first_id)
                .where(States.old_state_id.is_(None))
                .values(old_state_id=last_inserted)
            )
            if updated.rowcount != 1:
                raise RecorderImportError("First existing state changed during import")
            session.commit()
            return ImportOutcome(
                len(plan.eligible), plan.skipped_overlap, plan.skipped_retention
            )
        except Exception:
            session.rollback()
            raise


@dataclass(slots=True)
class _PrependTask(RecorderTask):
    """Write via Recorder's own task queue and SQLite writer connection."""

    done: concurrent.futures.Future[ImportOutcome]
    entity_id: str
    rows: list[HistoryRow]

    def run(self, instance: Any) -> None:
        """Propagate completion/errors to the user-facing options flow."""
        try:
            self.done.set_result(_write(instance, self.entity_id, self.rows))
        except Exception as err:
            self.done.set_exception(err)


async def async_prepend_states(
    hass: Any, entity_id: str, rows: list[HistoryRow]
) -> ImportOutcome:
    """Import eligible prefix states, skipping overlaps and auto-purge-expired rows."""
    recorder = get_instance(hass)
    future: concurrent.futures.Future[ImportOutcome] = concurrent.futures.Future()
    recorder.queue_task(_PrependTask(future, entity_id, rows))
    return await asyncio.wrap_future(future)
