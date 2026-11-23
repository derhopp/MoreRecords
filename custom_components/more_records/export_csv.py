"""Pure helpers for lossless Recorder state CSV export and download tickets."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
import math
import secrets
import time

from .csv_import import CsvImportError, parse_timestamp

MAX_EXPORT_ROWS = 100_000
EXPORT_TICKET_SECONDS = 600
MAX_ACTIVE_EXPORTS = 30


class ExportError(ValueError):
    """A requested Recorder state export is unavailable or invalid."""


class ExportTooLarge(ExportError):
    """An export would exceed the configured CSV row safety limit."""


@dataclass(frozen=True, slots=True)
class ExportTicket:
    """One-time, short-lived description of a Recorder read."""

    entry_id: str
    entity_id: str
    start_ts: float
    end_ts: float
    expires_at: float


class ExportTicketStore:
    """Keep temporary download grants in memory; never expose files in /local."""

    def __init__(self) -> None:
        self._tickets: dict[str, ExportTicket] = {}

    def create(
        self, entry_id: str, entity_id: str, start_ts: float,
        end_ts: float, *, now: float | None = None,
    ) -> str:
        """Issue a random single-use ticket valid for ten minutes."""
        current = time.monotonic() if now is None else now
        self._tickets = {
            key: ticket for key, ticket in self._tickets.items()
            if ticket.expires_at > current
        }
        while len(self._tickets) >= MAX_ACTIVE_EXPORTS:
            self._tickets.pop(next(iter(self._tickets)))
        ticket_id = secrets.token_urlsafe(32)
        self._tickets[ticket_id] = ExportTicket(
            entry_id, entity_id, start_ts, end_ts,
            current + EXPORT_TICKET_SECONDS,
        )
        return ticket_id

    def consume(self, ticket_id: str, *, now: float | None = None) -> ExportTicket | None:
        """Allow exactly one use, returning None for missing or expired tickets."""
        ticket = self._tickets.pop(ticket_id, None)
        current = time.monotonic() if now is None else now
        return ticket if ticket is not None and ticket.expires_at > current else None

    def clear_entry(self, entry_id: str) -> None:
        """Revoke exports when the corresponding integration entry unloads."""
        self._tickets = {
            key: ticket for key, ticket in self._tickets.items()
            if ticket.entry_id != entry_id
        }


def parse_export_window(
    start: str, end: str, timezone_name: str
) -> tuple[float, float]:
    """Convert local or offset-aware date/time inputs to a UTC [start, end) range.

    The importer applies the same time-zone and DST ambiguity checks.
    """
    try:
        start_ts = parse_timestamp(start, timezone_name)
        end_ts = parse_timestamp(end, timezone_name)
    except (CsvImportError, OverflowError) as err:
        raise ExportError(str(err)) from err
    if not all(math.isfinite(value) for value in (start_ts, end_ts)):
        raise ExportError("Export timestamps must be finite")
    if start_ts <= 0 or end_ts <= start_ts:
        raise ExportError("End time must be after start time")
    return start_ts, end_ts


def render_csv(rows: list[tuple[float, str | None]]) -> str:
    """Write a UTF-8-friendly semicolon CSV that MoreRecords can read again.

    Recorder stores UTC seconds as floats; ISO UTC timestamps preserve their
    microsecond-level precision. Export every stored row in query order, even
    when successive values are identical.
    """
    if len(rows) > MAX_EXPORT_ROWS:
        raise ExportTooLarge(
            f"More than {MAX_EXPORT_ROWS} Recorder rows; shorten the interval"
        )
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(("timestamp", "value"))
    for timestamp, value in rows:
        if not math.isfinite(timestamp):
            raise ExportError("Recorder contains a non-finite timestamp")
        writer.writerow((
            datetime.fromtimestamp(timestamp, timezone.utc).isoformat(
                timespec="microseconds"
            ),
            value if value is not None else "",
        ))
    return output.getvalue()
