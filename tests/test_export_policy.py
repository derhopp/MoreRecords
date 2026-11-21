"""Export CSV round-trip regression checks for text state preservation."""

from custom_components.more_records.csv_import import parse_csv
from custom_components.more_records.export_csv import render_csv


def test_csv_parser_preserves_textual_commas(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text(
        "timestamp;value\n2026-11-15T12:00:00Z;Hello, world\n",
        encoding="utf-8",
    )
    assert parse_csv(path, "Europe/Berlin")[0].value == "Hello, world"


def test_recorder_state_with_semicolon_quotes_round_trips(tmp_path):
    text = 'Schalter "Küche"; aktiviert, ja'
    path = tmp_path / "export.csv"
    path.write_text(render_csv([(1763200000, text)]), encoding="utf-8")
    assert parse_csv(path, "UTC")[0].value == text


def test_numeric_decimal_comma_still_supported(tmp_path):
    path = tmp_path / "number.csv"
    path.write_text(
        "timestamp;value\n2026-11-15T12:00:00Z;21,5\n",
        encoding="utf-8",
    )
    assert parse_csv(path, "UTC")[0].value == "21.5"
