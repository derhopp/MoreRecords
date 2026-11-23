"""Preserve a rolling last-observed state before Recorder auto-purge.

The metadata is saved in Home Assistant's .storage, not the Recorder database.
A scheduled snapshot at 04:00 local runs before the ordinary 04:12 purge.
An initial snapshot also runs after setup. Manual/early purges or long downtime
can cause unrecoverable gaps; this does not hook Recorder's deletion transaction.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from sqlalchemy import and_, func, select

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store

from .anchor_policy import StateAnchor, update_anchors
from .merge_policy import retention_boundary

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
STORE_KEY = "more_records_retention_anchors_v1"
_LOCK_KEY = "more_records_anchor_lock"


def _decode(document: Any) -> dict[str, StateAnchor]:
    if document is None:
        return {}
    if not isinstance(document, dict) or document.get("schema") != STORE_VERSION:
        raise ValueError("Unsupported MoreRecords retention-anchor schema")
    items = document.get("anchors")
    if not isinstance(items, dict):
        raise ValueError("Invalid retention-anchor storage")
    result: dict[str, StateAnchor] = {}
    for entity, value in items.items():
        if not isinstance(entity, str) or "." not in entity or not isinstance(value, dict):
            raise ValueError("Invalid stored anchor entity")
        ts, state = value.get("timestamp"), value.get("state")
        if (
            isinstance(ts, bool) or not isinstance(ts, (float, int))
            or not math.isfinite(ts) or not isinstance(state, str)
        ):
            raise ValueError("Invalid stored anchor value")
        result[entity] = StateAnchor(entity, float(ts), state)
    return result


def _encode(items: dict[str, StateAnchor]) -> dict[str, Any]:
    return {
        "schema": STORE_VERSION,
        "anchors": {
            entity: {"timestamp": item.timestamp, "state": item.value}
            for entity, item in sorted(items.items())
        },
    }


def _collect_before(recorder: Any, cutoff: float) -> list[StateAnchor]:
    """Fetch one last real pre-cutoff row per metadata ID, tie-breaking by ID."""
    if recorder.engine is None or recorder.engine.dialect.name != "sqlite":
        # MoreRecords supports SQLite Recorder only at present.
        return []
    with recorder.get_session() as session:
        latest = (
            select(
                States.metadata_id.label("meta"),
                func.max(States.last_updated_ts).label("ts"),
            )
            .where(States.last_updated_ts < cutoff)
            .group_by(States.metadata_id)
            .subquery()
        )
        rows = session.execute(
            select(
                StatesMeta.entity_id, States.last_updated_ts, States.state,
                States.state_id,
            )
            .join(latest, StatesMeta.metadata_id == latest.c.meta)
            .join(
                States,
                and_(
                    States.metadata_id == latest.c.meta,
                    States.last_updated_ts == latest.c.ts,
                ),
            )
            .order_by(StatesMeta.entity_id, States.state_id.desc())
        )
        result: list[StateAnchor] = []
        seen: set[str] = set()
        for entity, ts, value, _ in rows:
            if entity in seen or entity is None or value is None:
                continue
            seen.add(entity)
            result.append(StateAnchor(str(entity), float(ts), str(value)))
        return result


async def async_read_anchor(hass: Any, entity_id: str) -> StateAnchor | None:
    """Read a saved observation; never infer unknown state from current state."""
    lock: asyncio.Lock = hass.data.setdefault(_LOCK_KEY, asyncio.Lock())
    async with lock:
        store = Store(hass, STORE_VERSION, STORE_KEY, atomic_writes=True)
        return _decode(await store.async_load()).get(entity_id)


async def async_refresh_anchors(hass: Any) -> int:
    """Commit an advancing snapshot to .storage before Recorder purges rows."""
    recorder = get_instance(hass)
    boundary = retention_boundary(
        recorder.auto_purge, recorder.keep_days, time.time()
    )
    if boundary is None:
        return 0
    lock: asyncio.Lock = hass.data.setdefault(_LOCK_KEY, asyncio.Lock())
    async with lock:
        candidates = await recorder.async_add_executor_job(
            _collect_before, recorder, boundary
        )
        store = Store(hass, STORE_VERSION, STORE_KEY, atomic_writes=True)
        previous = _decode(await store.async_load())
        current = update_anchors(previous, candidates)
        if current != previous:
            await store.async_save(_encode(current))
        return sum(previous.get(key) != value for key, value in current.items())


async def _refresh_with_logging(hass: Any) -> None:
    try:
        count = await async_refresh_anchors(hass)
        if count:
            _LOGGER.info("Saved %d rolling Recorder retention anchors", count)
    except Exception:
        _LOGGER.exception("Failed to refresh Recorder retention anchors")


def register_retention_tracking(hass: Any, entry: Any) -> None:
    """Start once for the config entry and unregister the daily timer on unload."""
    async def _daily_refresh(_now: Any) -> None:
        await _refresh_with_logging(hass)

    unsub = async_track_time_change(
        hass, _daily_refresh, hour=4, minute=0, second=0
    )
    entry.async_on_unload(unsub)
    hass.async_create_task(_refresh_with_logging(hass))
