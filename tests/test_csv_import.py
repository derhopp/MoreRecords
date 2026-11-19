"""Pure Python tests: no Home Assistant runtime or Recorder writes required."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from custom_components.more_records.csv_import import CsvImportError, parse_csv, parse_timestamp


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "example.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_semicolon_decimal_and_header(tmp_path):
    rows = parse_csv(write_csv(tmp_path, "timestamp;value\n2023-01-01T12:00:00+01:00;21,5\n"), "Europe/Berlin")
    assert len(rows) == 1
    assert rows[0].value == "21.5"
    assert rows[0].timestamp == datetime(2023, 1, 1, 11, tzinfo=timezone.utc).timestamp()


def test_headerless_comma_and_sorted(tmp_path):
    rows = parse_csv(write_csv(tmp_path, "2023-01-02T00:00:00Z,2.3\n2023-01-01T00:00:00Z,1.2\n"), "UTC")
    assert [row.value for row in rows] == ["1.2", "2.3"]


def test_unix_milliseconds():
    assert parse_timestamp("1672531200000", "UTC") == parse_timestamp("1672531200", "UTC")


def test_reject_ambiguous_dst():
    with pytest.raises(CsvImportError, match="Ambiguous"):
        parse_timestamp("2023-10-29 02:30:00", "Europe/Berlin")


def test_reject_nonexistent_dst():
    with pytest.raises(CsvImportError, match="Nonexistent"):
        parse_timestamp("2023-03-26 02:30:00", "Europe/Berlin")


def test_explicit_offset_survives_dst():
    assert parse_timestamp("2023-10-29T02:30:00+02:00", "Europe/Berlin") != parse_timestamp("2023-10-29T02:30:00+01:00", "Europe/Berlin")


def test_duplicate_timestamp_is_rejected(tmp_path):
    with pytest.raises(CsvImportError, match="Duplicate"):
        parse_csv(write_csv(tmp_path, "2023-01-01T00:00:00Z;1\n2023-01-01T00:00:00Z;2\n"), "UTC")


def test_reject_extra_columns(tmp_path):
    with pytest.raises(CsvImportError, match="two columns"):
        parse_csv(write_csv(tmp_path, "2023-01-01;2;3\n"), "UTC")


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(CsvImportError, match="no data"):
        parse_csv(write_csv(tmp_path, ""), "UTC")
