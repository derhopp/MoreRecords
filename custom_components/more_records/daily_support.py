"""Generate optional local-midnight support rows from *real source observations*.

This is a forward carry of an actually imported-window observation, not
interpolation, a measured midnight event, or evidence of complete coverage.
No recorder state or previously generated support point is used as the source.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from .csv_import import HistoryRow

MAX_SUPPORT_DAYS = 100_000


def midnight_supports(
    source: list[HistoryRow],
    recorded_timestamps: list[float],
    *,
    window_start: float,
    window_end: float,
    timezone_name: str,
    purge_before: float | None,
    newest_recorded: float,
) -> list[HistoryRow]:
    """Plan only missing local 00:00 samples, suppressing days with 00-06 events.

    A source observation must be within the user-selected window and at or
    before midnight. It may be before the retention cutoff, but the support
    ROW itself must be inside retention. The same original observation may
    establish the carried value on subsequent days; support rows never feed
    back into this calculation.
    """
    if not all(math.isfinite(x) for x in (window_start, window_end, newest_recorded)):
        raise ValueError("Invalid support-point time range")
    if purge_before is not None and not math.isfinite(purge_before):
        raise ValueError("Invalid retention boundary")
    if window_end <= window_start:
        raise ValueError("Invalid selected import window")
    if any(a.timestamp >= b.timestamp for a, b in zip(source, source[1:])):
        raise ValueError("Source timestamps must be strictly increasing")
    if any(not window_start <= row.timestamp < window_end for row in source):
        raise ValueError("Source observation lies outside selected import window")

    zone = ZoneInfo(timezone_name)
    # Date arithmetic in LOCAL calendar days: DST days need not be 86400 sec.
    first_date = datetime.fromtimestamp(window_start, zone).date()
    last_date = datetime.fromtimestamp(window_end, zone).date()
    days = (last_date - first_date).days + 1
    if days > MAX_SUPPORT_DAYS:
        raise ValueError("Selected interval is too long for daily supports")

    source_times = [row.timestamp for row in source]
    source_check_times = sorted(set(source_times + recorded_timestamps))
    result: list[HistoryRow] = []
    for offset in range(days):
        local_midnight = datetime.combine(
            first_date + timedelta(days=offset), datetime.min.time(), zone
        )
        midnight = local_midnight.astimezone(timezone.utc).timestamp()
        if (
            midnight < window_start or midnight >= window_end
            or midnight > newest_recorded
            or (purge_before is not None and midnight < purge_before)
        ):
            continue
        six_am = local_midnight.replace(hour=6).astimezone(timezone.utc).timestamp()
        # No artificial 00:00 row whenever any real source OR Recorder
        # observation exists in [local 00:00, local 06:00).
        first_in_window = bisect_left(source_check_times, midnight)
        if (
            first_in_window < len(source_check_times)
            and source_check_times[first_in_window] < six_am
        ):
            continue
        index = bisect_right(source_times, midnight) - 1
        if index < 0:
            continue
        value = source[index].value
        result.append(HistoryRow(midnight, value))
    return result
