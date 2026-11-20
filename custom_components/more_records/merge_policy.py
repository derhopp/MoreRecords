"""Explicit overlap policy for one-time historical Recorder imports.

The importer appends a prefix to an existing entity's history. A Recorder
time range is treated as authoritative: never overwrite, replace or insert
points on/after the earliest existing HA state. This is intentional even
when individual event timestamps do not match.
"""

from __future__ import annotations

from dataclasses import dataclass

from .csv_import import HistoryRow


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """Preview of prefix candidates and Recorder-covered rows."""

    eligible: list[HistoryRow]
    skipped: int
    total: int
    oldest_existing: float


def plan_prefix(rows: list[HistoryRow], oldest_existing: float) -> ImportPlan:
    """Keep only rows before the Recorder's existing history boundary.

    Input must be chronological (CSV parser and CCU importer guarantee this).
    Re-run inside the Recorder transaction to avoid preview/write races.
    """
    if any(rows[index].timestamp >= rows[index + 1].timestamp
           for index in range(len(rows) - 1)):
        raise ValueError("Source timestamps must be strictly increasing")

    eligible = [row for row in rows if row.timestamp < oldest_existing]
    return ImportPlan(
        eligible=eligible,
        skipped=len(rows) - len(eligible),
        total=len(rows),
        oldest_existing=oldest_existing,
    )
