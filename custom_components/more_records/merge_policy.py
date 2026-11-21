"""Conservative overlap and Recorder retention policy for historical imports.

Only insert source rows *before* the first existing HA state. When automatic
Recorder purging is enabled, additionally keep only rows within the current
purge_keep_days interval. The same policy runs in the preview and inside
the final Recorder transaction to account for elapsed time or configuration
changes.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .csv_import import HistoryRow

SECONDS_PER_DAY = 86400


def retention_boundary(auto_purge: bool, keep_days: int, now_ts: float) -> float | None:
    """Return Home Assistant's live automatic-purge threshold in UTC seconds.

    HA's nightly Recorder task calls utcnow() - timedelta(days=keep_days).
    A state exactly on the threshold is kept, while older states are purged.
    With auto_purge disabled, this returns None regardless of keep_days.
    """
    if not auto_purge:
        return None
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 1:
        raise ValueError("Recorder purge_keep_days must be a positive integer")
    if not math.isfinite(now_ts):
        raise ValueError("Current UTC timestamp must be finite")
    return now_ts - keep_days * SECONDS_PER_DAY


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """Preview of source states eligible for import and skipped by reason."""

    eligible: list[HistoryRow]
    skipped_overlap: int
    skipped_retention: int
    total: int
    oldest_existing: float
    purge_before: float | None

    @property
    def skipped(self) -> int:
        """Total rows skipped, without double counting overlapping reasons."""
        return self.skipped_overlap + self.skipped_retention


def plan_prefix(
    rows: list[HistoryRow],
    oldest_existing: float,
    *,
    purge_before: float | None = None,
) -> ImportPlan:
    """Filter a chronological source by HA history *and* retention boundaries.

    Classification is exclusive: an existing HA interval takes precedence over
    the purge rule. This returns no candidates if both windows do not intersect.
    """
    if any(
        rows[index].timestamp >= rows[index + 1].timestamp
        for index in range(len(rows) - 1)
    ):
        raise ValueError("Source timestamps must be strictly increasing")
    if not math.isfinite(oldest_existing):
        raise ValueError("Invalid earliest Recorder timestamp")
    if purge_before is not None and not math.isfinite(purge_before):
        raise ValueError("Invalid Recorder purge boundary")

    eligible: list[HistoryRow] = []
    skipped_overlap = skipped_retention = 0
    for row in rows:
        if row.timestamp >= oldest_existing:
            skipped_overlap += 1
        elif purge_before is not None and row.timestamp < purge_before:
            skipped_retention += 1
        else:
            eligible.append(row)

    return ImportPlan(
        eligible=eligible,
        skipped_overlap=skipped_overlap,
        skipped_retention=skipped_retention,
        total=len(rows),
        oldest_existing=oldest_existing,
        purge_before=purge_before,
    )
