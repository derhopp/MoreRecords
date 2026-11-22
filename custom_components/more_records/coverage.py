"""Explicit, local CCU-Historian coverage attestations for Recorder consumers.

This is NOT an HA RecorderRuns substitute. Attestations represent a completed
historian query, a user confirmation that its event series is complete, and
an exact comparison with persisted Recorder states. CSVs are never attested.
The store contains intervals and entity IDs, never measurements or credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.helpers.storage import Store

from .csv_import import HistoryRow
from .coverage_policy import (
    HistoryVerificationError,
    attestation_range,
    compare_recorded_rows,
    normalize_intervals,
    state_fingerprint,
)

COVERAGE_STORE_KEY = "more_records_verified_history_v1"
COVERAGE_STORE_VERSION = 1
def _compare_recorder(
    recorder: Any, entity_id: str, expected: list[HistoryRow],
    start: float, end: float,
) -> str:
    with recorder.get_session() as session:
        metadata_id = session.scalar(
            select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
        )
        if metadata_id is None:
            raise HistoryVerificationError("Entity has no Recorder metadata")
        actual = session.execute(
            select(States.last_updated_ts, States.state)
            .where(
                States.metadata_id == metadata_id,
                States.last_updated_ts >= start,
                States.last_updated_ts < end,
            )
            .order_by(States.last_updated_ts, States.state_id)
        ).all()
    persisted = [(float(ts), str(state)) for ts, state in actual]
    compare_recorded_rows(expected, persisted)
    return state_fingerprint(persisted)


async def async_verify_history(
    hass: Any, entity_id: str, rows: list[HistoryRow], start: float, end: float
) -> tuple[float, float, str]:
    """Re-read the live database; a successful import alone is not evidence."""
    verified_start, verified_end, expected = attestation_range(rows, start, end)
    recorder = get_instance(hass)
    fingerprint = await recorder.async_add_executor_job(
        _compare_recorder, recorder, entity_id, expected, verified_start, verified_end
    )
    return verified_start, verified_end, fingerprint


async def async_record_coverage(
    hass: Any, entity_id: str, start: float, end: float, fingerprint: str
) -> None:
    """Commit metadata only AFTER source/database verification succeeded."""
    lock: asyncio.Lock = hass.data.setdefault(
        "more_records_coverage_write_lock", asyncio.Lock()
    )
    async with lock:
        store = Store(hass, COVERAGE_STORE_VERSION, COVERAGE_STORE_KEY, atomic_writes=True)
        previous = await store.async_load() or {}
        items = previous.get("intervals", []) if isinstance(previous, dict) else []
        if not isinstance(items, list):
            raise HistoryVerificationError("Coverage storage has invalid format")
        intervals = normalize_intervals([
            *items, {
                "entity_id": entity_id, "start": start, "end": end,
                "source": "ccu_historian",
                "evidence": "confirmed_query_and_recorder_match",
                "fingerprint": fingerprint,
            }
        ])
        await store.async_save({"schema": 1, "intervals": intervals})
