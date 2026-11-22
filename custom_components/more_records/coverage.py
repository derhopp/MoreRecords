"""Explicit, local CCU-Historian coverage attestations for Recorder consumers.

This is NOT an HA RecorderRuns substitute. Attestations represent a completed
historian query, a user confirmation that its event series is complete, and
an exact comparison with persisted Recorder states. CSVs are never attested.
The store contains intervals and entity IDs, never measurements or credentials.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
from typing import Any

from sqlalchemy import select

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.helpers.storage import Store

from .csv_import import HistoryRow

COVERAGE_STORE_KEY = "more_records_verified_history_v1"
COVERAGE_STORE_VERSION = 1
MAX_INTERVALS = 2048


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
    """Every source observation must equal one stored Recorder observation."""
    if len(expected) != len(actual) or any(
        abs(row.timestamp - timestamp) > 0.0001 or row.value != state
        for row, (timestamp, state) in zip(expected, actual)
    ):
        raise HistoryVerificationError(
            "Recorder values do not exactly match the CCU-Historian query; "
            "history coverage was NOT certified"
        )


def _compare_recorder(
    recorder: Any, entity_id: str, expected: list[HistoryRow],
    start: float, end: float,
) -> None:
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
    compare_recorded_rows(expected, [(float(ts), str(state)) for ts, state in actual])


async def async_verify_history(
    hass: Any, entity_id: str, rows: list[HistoryRow], start: float, end: float
) -> tuple[float, float]:
    """Re-read the live database; a successful import alone is not evidence."""
    verified_start, verified_end, expected = attestation_range(rows, start, end)
    recorder = get_instance(hass)
    await recorder.async_add_executor_job(
        _compare_recorder, recorder, entity_id, expected, verified_start, verified_end
    )
    return verified_start, verified_end


def normalize_intervals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge only touching/overlapping verified ranges for the SAME entity."""
    valid = []
    for entry in items:
        try:
            start, end = float(entry["start"]), float(entry["end"])
            entity = entry["entity_id"]
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not isinstance(entity, str) or "." not in entity
            or not math.isfinite(start) or not math.isfinite(end)
            or end <= start or entry.get("source") != "ccu_historian"
            or entry.get("evidence") != "confirmed_query_and_recorder_match"
        ):
            continue
        valid.append({
            "entity_id": entity, "start": start, "end": end,
            "source": "ccu_historian",
            "evidence": "confirmed_query_and_recorder_match",
        })
    valid.sort(key=lambda item: (item["entity_id"], item["start"], item["end"]))
    result: list[dict[str, Any]] = []
    for item in valid:
        if (result and result[-1]["entity_id"] == item["entity_id"]
                and item["start"] <= result[-1]["end"]):
            result[-1]["end"] = max(result[-1]["end"], item["end"])
        else:
            result.append(item)
    if len(result) > MAX_INTERVALS:
        raise HistoryVerificationError("Too many attested historical ranges")
    return result


async def async_record_coverage(
    hass: Any, entity_id: str, start: float, end: float
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
            }
        ])
        await store.async_save({"schema": 1, "intervals": intervals})
