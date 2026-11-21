"""Parse timestamp/value CSV files without modifying a recorder database."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .const import MAX_IMPORT_ROWS


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """A source state and its UTC Unix timestamp."""

    timestamp: float
    value: str


class CsvImportError(ValueError):
    """Input data does not follow the supported CSV format."""


_TIME_HEADERS = frozenset({"timestamp", "time", "date", "datetime", "zeitstempel"})
_VALUE_HEADERS = frozenset({"value", "state", "data", "wert", "zustand"})


def parse_timestamp(raw: str, timezone_name: str) -> float:
    """Convert ISO, Unix seconds/ms or German local time to UTC seconds.

    Reject ambiguous or nonexistent naive local times rather than silently
    inserting a one-hour DST shift.
    """
    raw = raw.strip()
    if re.fullmatch(r"-?\d{10}(?:\.\d+)?|-?\d{13}", raw):
        value = float(raw)
        if len(raw.lstrip("-")) == 13:
            value /= 1000
        return value

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            raise CsvImportError(f"Unsupported timestamp: {raw!r}") from None

    if parsed.tzinfo is not None:
        return parsed.timestamp()

    tz = ZoneInfo(timezone_name)
    candidates = []
    for fold in (0, 1):
        local = parsed.replace(tzinfo=tz, fold=fold)
        roundtrip = local.astimezone(timezone.utc).astimezone(tz)
        if roundtrip.replace(tzinfo=None) == parsed:
            candidates.append(local.timestamp())

    if not candidates:
        raise CsvImportError(f"Nonexistent local time (DST): {raw!r}")
    if len(set(candidates)) > 1:
        raise CsvImportError(f"Ambiguous local time (DST): {raw!r}; add a UTC offset")
    return candidates[0]


def parse_csv(path: Path, timezone_name: str) -> list[HistoryRow]:
    """Read two-column CSV, optionally with header; return sorted unique rows.

    A comma decimal separator is accepted with semicolon/tab-separated CSV.
    """
    rows: list[HistoryRow] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(4096)
            stream.seek(0)
            first_line = next((line for line in sample.splitlines() if line.strip()), "")
            delimiter = ";" if first_line.count(";") else (
                "\t" if first_line.count("\t") else ","
            )
            reader = csv.reader(stream, delimiter=delimiter)
            first_data = True
            for line_number, fields in enumerate(reader, 1):
                if not fields or all(not field.strip() for field in fields):
                    continue
                if len(fields) != 2:
                    raise CsvImportError(
                        f"Line {line_number}: expected exactly two columns"
                    )
                timestamp_raw, value_raw = (field.strip() for field in fields)
                if first_data:
                    first_data = False
                    if (
                        timestamp_raw.casefold() in _TIME_HEADERS
                        and value_raw.casefold() in _VALUE_HEADERS
                    ):
                        continue
                if not value_raw:
                    raise CsvImportError(f"Line {line_number}: empty value")
                if len(rows) >= MAX_IMPORT_ROWS:
                    raise CsvImportError(
                        f"Maximum {MAX_IMPORT_ROWS} rows per import; split the file"
                    )
                timestamp = parse_timestamp(timestamp_raw, timezone_name)
                if not math.isfinite(timestamp) or timestamp <= 0:
                    raise CsvImportError(f"Line {line_number}: invalid timestamp")
                # Only normalize a *numeric* decimal comma. A textual
                # Recorder state such as "Hello, world" must survive a
                # semicolon-separated export/import round trip unchanged.
                value = (
                    value_raw.replace(",", ".")
                    if delimiter != ","
                    and re.fullmatch(r"[+-]?(?:\d+(?:,\d*)?|,\d+)", value_raw)
                    else value_raw
                )
                rows.append(HistoryRow(timestamp, value))
    except (UnicodeError, csv.Error) as err:
        raise CsvImportError(f"Could not read CSV: {err}") from err

    if not rows:
        raise CsvImportError("CSV contains no data rows")
    rows.sort(key=lambda item: item.timestamp)
    for previous, current in zip(rows, rows[1:]):
        if previous.timestamp == current.timestamp:
            raise CsvImportError(
                f"Duplicate timestamp: {datetime.fromtimestamp(current.timestamp, timezone.utc)}"
            )
    return rows
