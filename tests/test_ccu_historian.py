"""Tests for direct CCU-Historian datapoint discovery and raw history."""
from datetime import datetime, timezone

import pytest

from custom_components.more_records.ccu_historian import (
    HistorianError,
    date_range_ms,
    parse_datapoints,
    parse_raw_response,
    rpc_url,
    search_points,
    sort_unique,
)


POINTS = [
    {
        "idx": 2141,
        "id": {
            "interfaceId": "BidCos-RF",
            "address": "ABC0123456:1",
            "identifier": "STATE",
        },
        "attributes": {
            "displayName": "Schalter Küche Decke",
            "type": "BOOL",
            "unit": "",
        },
    },
    {
        "idx": 39,
        "id": {
            "interfaceId": "HmIP-RF",
            "address": "001122:3",
            "identifier": "ACTUAL_TEMPERATURE",
        },
        "attributes": {
            "displayName": "Wohnzimmer Klima",
            "type": "FLOAT",
            "unit": "°C",
        },
    },
]


def test_search_name_and_parameter():
    points = parse_datapoints(POINTS)
    matched, total = search_points(points, "küche state")
    assert total == 1
    assert matched[0].idx == 2141
    assert "STATE" in matched[0].label
    matched, total = search_points(points, "actual_temperature")
    assert total == 1 and matched[0].idx == 39


def test_unique_point_ids():
    points = parse_datapoints([POINTS[0], POINTS[0]])
    assert len(points) == 1


def test_search_limit():
    points = parse_datapoints(POINTS)
    matched, total = search_points(points, "", limit=1)
    assert len(matched) == 1 and total == 2


def test_normalizes_base_url_and_key():
    assert rpc_url("http://localhost:8082/") == (
        "http://localhost:8082/query/jsonrpc.gy"
    )
    assert rpc_url("https://ccu.example/query/jsonrpc.gy", "a b").endswith(
        "/query/jsonrpc.gy?k=a+b"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:pass@host/",
        "http://host/foo",
        "http://host/#fragment",
        "http://host/?token=secret",
    ],
)
def test_rejects_invalid_urls(url):
    with pytest.raises(HistorianError):
        rpc_url(url)


def test_raw_boolean_and_numeric_series():
    rows = parse_raw_response(
        {
            "timestamps": [1672531200000, 1672531200500],
            "values": [True, 21.5],
            "states": [1, 1],
            "dataPoint": POINTS[0],
        }
    )
    assert [(r.timestamp, r.value) for r in rows] == [
        (1672531200, "on"), (1672531200.5, "21.5")
    ]


def test_reject_mismatched_arrays():
    with pytest.raises(HistorianError, match="mismatched"):
        parse_raw_response(
            {"timestamps": [1, 2], "values": [3], "states": [1]}
        )


def test_raw_duplicate_exact_vs_conflicting():
    from custom_components.more_records.csv_import import HistoryRow
    assert len(sort_unique([HistoryRow(1, "a"), HistoryRow(1, "a")])) == 1
    with pytest.raises(HistorianError, match="Conflicting"):
        sort_unique([HistoryRow(1, "a"), HistoryRow(1, "b")])


def test_local_day_includes_dst_offset():
    start, end = date_range_ms("2023-03-26", "2023-03-26", "Europe/Berlin")
    assert end - start == 23 * 3600 * 1000
    assert start == int(
        datetime(2023, 3, 25, 23, tzinfo=timezone.utc).timestamp() * 1000
    )


def test_bad_date_order():
    with pytest.raises(HistorianError):
        date_range_ms("2024-01-02", "2024-01-01", "Europe/Berlin")
