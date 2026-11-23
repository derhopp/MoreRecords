"""Read raw Recorder states for a selected entity and UTC time interval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta

from .export_csv import ExportError, ExportTooLarge, MAX_EXPORT_ROWS
from .anchor_policy import choose_predecessor, with_initial_state
from .retention_anchors import async_read_anchor


@dataclass(frozen=True, slots=True)
class ExportSummary:
    """Number and timestamp range of available raw Recorder rows."""

    count: int
    first_ts: float | None
    last_ts: float | None
    initial_source: str | None = None


def _metadata_id(session: Any, entity_id: str) -> int | None:
    return session.scalar(
        select(StatesMeta.metadata_id).where(StatesMeta.entity_id == entity_id)
    )


def _preview(
    recorder: Any, entity_id: str, start_ts: float, end_ts: float
) -> ExportSummary:
    """Count the exact raw states (not History UI's significant states)."""
    with recorder.get_session() as session:
        meta_id = _metadata_id(session, entity_id)
        if meta_id is None:
            return ExportSummary(0, None, None)
        count, earliest, latest = session.execute(
            select(
                func.count(States.state_id),
                func.min(States.last_updated_ts),
                func.max(States.last_updated_ts),
            )
            .where(States.metadata_id == meta_id)
            .where(States.last_updated_ts >= start_ts)
            .where(States.last_updated_ts < end_ts)
        ).one()
        return ExportSummary(
            int(count), float(earliest) if earliest is not None else None,
            float(latest) if latest is not None else None
        )


def _previous(recorder: Any, entity_id: str, start_ts: float) -> tuple[float, str] | None:
    """Find the last remaining Recorder observation strictly before the window."""
    with recorder.get_session() as session:
        meta_id = _metadata_id(session, entity_id)
        if meta_id is None:
            return None
        row = session.execute(
            select(States.last_updated_ts, States.state)
            .where(States.metadata_id == meta_id)
            .where(States.last_updated_ts < start_ts)
            .order_by(States.last_updated_ts.desc(), States.state_id.desc())
            .limit(1)
        ).first()
        return (float(row[0]), str(row[1])) if row and row[1] is not None else None


async def _initial_state(
    hass: Any, recorder: Any, entity_id: str, start_ts: float
) -> tuple[float, str, str] | None:
    """Compare the live predecessor with the separately persisted purge anchor."""
    recorded = await recorder.async_add_executor_job(
        _previous, recorder, entity_id, start_ts
    )
    anchor = await async_read_anchor(hass, entity_id)
    return choose_predecessor(start_ts, recorded, anchor)


async def async_export_preview(
    hass: Any, entity_id: str, start_ts: float, end_ts: float,
    *, reconstruct_initial_state: bool = False,
) -> ExportSummary:
    """Preview actual Recorder rows or a clearly opt-in reconstructed initial state."""
    recorder = get_instance(hass)
    summary = await recorder.async_add_executor_job(
        _preview, recorder, entity_id, start_ts, end_ts
    )
    predecessor = (
        await _initial_state(hass, recorder, entity_id, start_ts)
        if reconstruct_initial_state else None
    )
    inserted = predecessor is not None and (
        summary.first_ts is None or summary.first_ts > start_ts
    )
    count = summary.count + int(inserted)
    if not count:
        raise ExportError("No stored Recorder states or known initial state in the selected time range")
    if count > MAX_EXPORT_ROWS:
        raise ExportTooLarge(
            f"{count} states exceed the {MAX_EXPORT_ROWS} row limit; "
            "select a shorter interval"
        )
    return ExportSummary(
        count,
        start_ts if inserted else summary.first_ts,
        summary.last_ts if summary.last_ts is not None else (start_ts if inserted else None),
        predecessor[2] if inserted else None,
    )


def _read(
    recorder: Any, entity_id: str, start_ts: float, end_ts: float
) -> list[tuple[float, str | None]]:
    """Fetch all raw state rows including unchanged repeats, in UTC order.

    Limit-plus-one is checked again at download time in case Recorder has
    recorded more states since the wizard preview.
    """
    with recorder.get_session() as session:
        meta_id = _metadata_id(session, entity_id)
        if meta_id is None:
            return []
        results = session.execute(
            select(States.last_updated_ts, States.state)
            .where(States.metadata_id == meta_id)
            .where(States.last_updated_ts >= start_ts)
            .where(States.last_updated_ts < end_ts)
            .order_by(States.last_updated_ts, States.state_id)
            .limit(MAX_EXPORT_ROWS + 1)
        ).all()
        if len(results) > MAX_EXPORT_ROWS:
            raise ExportTooLarge("More Recorder rows than allowed; shorten the range")
        return [(float(row[0]), row[1]) for row in results]


async def async_export_rows(
    hass: Any, entity_id: str, start_ts: float, end_ts: float,
    *, reconstruct_initial_state: bool = False,
) -> list[tuple[float, str | None]]:
    """Export real rows, with an optional *derived* start-state observation.

    The start timestamp in reconstructed mode is not an actual state event.
    """
    recorder = get_instance(hass)
    rows = await recorder.async_add_executor_job(
        _read, recorder, entity_id, start_ts, end_ts
    )
    if reconstruct_initial_state:
        predecessor = await _initial_state(hass, recorder, entity_id, start_ts)
        rows = with_initial_state(rows, start_ts, predecessor)
    if len(rows) > MAX_EXPORT_ROWS:
        raise ExportTooLarge("More than 100,000 exported rows; shorten the range")
    return rows
