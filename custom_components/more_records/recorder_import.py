"""Experimental SQLite recorder backfill.

This intentionally uses internal recorder models and tasks, not a public API.
The write runs on the recorder thread, is transactional, and only prepends
strictly older states to an entity that already has history.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, text, update

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.components.recorder.tasks import RecorderTask

from .csv_import import HistoryRow


class RecorderImportError(ValueError):
    """Safety guard or recorder write failed."""


def _get_first(recorder: Any, entity_id: str) -> tuple[int, float, int | None, int | None] | None:
    """Return (state ID, timestamp, predecessor, attributes ID)."""
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
    """Read the earliest existing state on a database worker."""
    recorder = get_instance(hass)
    first = await recorder.async_add_executor_job(_get_first, recorder, entity_id)
    return first[1] if first else None


def _write(recorder: Any, entity_id: str, rows: list[HistoryRow]) -> int:
    """Apply the prepend as a single transaction on the recorder thread."""
    if recorder.engine is None or recorder.engine.dialect.name != "sqlite":
        raise RecorderImportError("Only SQLite recorder databases are supported")
    if recorder.auto_purge:
        raise RecorderImportError("Disable recorder.auto_purge before importing")

    with recorder.get_session() as session:
        try:
            metadata_id = session.scalar(
                select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
            )
            if metadata_id is None:
                raise RecorderImportError("Entity has no recorder metadata")
            first = session.execute(
                select(
                    States.state_id, States.last_updated_ts, States.old_state_id,
                    States.attributes_id,
                )
                .where(States.metadata_id == metadata_id)
                .where(States.last_updated_ts.is_not(None))
                .order_by(States.last_updated_ts, States.state_id)
                .limit(1)
            ).first()
            if first is None:
                raise RecorderImportError("Entity has no existing recorder states")
            first_id, cutoff, old_id, attrs_id = first
            if old_id is not None:
                raise RecorderImportError(
                    "Earliest state already references a predecessor; refusing to splice"
                )
            if rows[-1].timestamp >= cutoff:
                raise RecorderImportError("CSV overlaps existing recorder history")

            latest_change = None
            previous_value = None
            for offset in range(0, len(rows), 5000):
                chunk = []
                for row in rows[offset : offset + 5000]:
                    changed = row.value != previous_value
                    if changed:
                        latest_change = row.timestamp
                    chunk.append(
                        {
                            "metadata_id": metadata_id,
                            "state": row.value,
                            "last_updated_ts": row.timestamp,
                            "last_changed_ts": None if changed else latest_change,
                            "last_reported_ts": None,
                            "attributes_id": attrs_id,
                            "old_state_id": None,
                            "origin_idx": 0,
                        }
                    )
                    previous_value = row.value
                session.execute(insert(States), chunk)

            # SQLite window functions permit efficient predecessor linking.
            # No pre-existing state can be within the prefix by the cutoff guard.
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
            last_imported_id = session.scalar(
                select(States.state_id)
                .where(States.metadata_id == metadata_id)
                .where(States.last_updated_ts < cutoff)
                .order_by(States.last_updated_ts.desc(), States.state_id.desc())
                .limit(1)
            )
            if last_imported_id is None:
                raise RecorderImportError("No states were inserted")
            result = session.execute(
                update(States)
                .where(States.state_id == first_id)
                .where(States.old_state_id.is_(None))
                .values(old_state_id=last_imported_id)
            )
            if result.rowcount != 1:
                raise RecorderImportError("First existing state changed during import")

            session.commit()
            return len(rows)
        except Exception:
            session.rollback()
            raise


@dataclass(slots=True)
class _PrependTask(RecorderTask):
    """Run the write on Recorder's own queue/worker thread."""

    done: concurrent.futures.Future[int]
    entity_id: str
    rows: list[HistoryRow]

    def run(self, instance: Any) -> None:
        """Resolve the future after the transaction completes."""
        try:
            self.done.set_result(_write(instance, self.entity_id, self.rows))
        except Exception as err:
            self.done.set_exception(err)


async def async_prepend_states(hass: Any, entity_id: str, rows: list[HistoryRow]) -> int:
    """Queue a safe prepend; revalidate cutoff inside the transaction."""
    recorder = get_instance(hass)
    future: concurrent.futures.Future[int] = concurrent.futures.Future()
    recorder.queue_task(_PrependTask(future, entity_id, rows))
    return await asyncio.wrap_future(future)
