"""Read raw Recorder states for a selected entity and UTC time interval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta

from .export_csv import ExportError, ExportTooLarge, MAX_EXPORT_ROWS


@dataclass(frozen=True, slots=True)
class ExportSummary:
    """Number and timestamp range of available raw Recorder rows."""

    count: int
    first_ts: float | None
    last_ts: float | None


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


async def async_export_preview(
    hass: Any, entity_id: str, start_ts: float, end_ts: float
) -> ExportSummary:
    """Read a bounded export preview through Recorder's DB executor."""
    recorder = get_instance(hass)
    summary = await recorder.async_add_executor_job(
        _preview, recorder, entity_id, start_ts, end_ts
    )
    if not summary.count:
        raise ExportError("No stored Recorder states in the selected time range")
    if summary.count > MAX_EXPORT_ROWS:
        raise ExportTooLarge(
            f"{summary.count} states exceed the {MAX_EXPORT_ROWS} row limit; "
            "select a shorter interval"
        )
    return summary


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
    hass: Any, entity_id: str, start_ts: float, end_ts: float
) -> list[tuple[float, str | None]]:
    """Load raw state history without writing or modifying Recorder data."""
    recorder = get_instance(hass)
    return await recorder.async_add_executor_job(
        _read, recorder, entity_id, start_ts, end_ts
    )
