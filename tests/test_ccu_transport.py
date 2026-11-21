"""CCU-Historian HTTP transport regression tests (no network required)."""

import asyncio
import json

import pytest

from custom_components.more_records.ccu_historian import (
    HistorianClient,
    HistorianError,
    read_response_complete,
)


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks, status=200):
        self.content = FakeContent(chunks)
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.sent = None

    def post(self, url, *, json, allow_redirects):
        self.sent = (url, json, allow_redirects)
        return self.response


def run(coro):
    return asyncio.run(coro)


def test_reads_all_chunks_and_does_not_stop_at_first_partial_result():
    raw = b"abc" * 100
    chunks = [raw[:4], raw[4:79], raw[79:122], raw[122:]]
    result = run(read_response_complete(FakeResponse(chunks), limit=512))
    assert result == raw


def test_rejects_oversize_stream_even_in_many_small_chunks():
    stream = FakeResponse([b"x" * 6, b"y" * 6])
    with pytest.raises(HistorianError, match="exceeds"):
        run(read_response_complete(stream, limit=10))


def test_discovery_accepts_chunked_json_response():
    point = {
        "idx": 2141,
        "id": {
            "interfaceId": "BidCos-RF",
            "address": "ABC123:1",
            "identifier": "STATE",
        },
        "attributes": {
            "displayName": "Schalter Küche Decke",
            "type": "BOOL",
            "unit": "",
        },
    }
    payload = json.dumps({"id": 1, "result": [point]}).encode()
    response = FakeResponse([payload[:17], payload[17:39], payload[39:]])
    session = FakeSession(response)
    client = HistorianClient(session, "http://192.168.1.48:8081")
    points = run(client.async_datapoints())
    assert len(points) == 1
    assert points[0].idx == 2141
    assert session.sent == (
        "http://192.168.1.48:8081/query/jsonrpc.gy",
        {"id": 1, "method": "getDataPoint", "params": []},
        False,
    )


def test_invalid_json_reports_diagnostic_without_leaking_body():
    session = FakeSession(FakeResponse([b'{"result":']))
    client = HistorianClient(session, "http://test.local:8081")
    with pytest.raises(HistorianError, match="invalid JSON"):
        run(client.async_datapoints())


def test_http_error_explains_bad_port_or_path():
    session = FakeSession(FakeResponse([], status=404))
    client = HistorianClient(session, "http://test.local:8081")
    with pytest.raises(HistorianError, match="HTTP 404"):
        run(client.async_datapoints())


def test_no_api_key_is_required_or_transmitted():
    session = FakeSession(FakeResponse([b'{"result": []}']))
    client = HistorianClient(session, "http://test.local:8081")
    assert run(client.async_datapoints()) == []
    assert "?" not in session.sent[0]


def test_rpc_protocol_error_message_is_reported():
    error = {"id": 1, "error": {"code": -32601, "message": "Method not found"}}
    session = FakeSession(FakeResponse([json.dumps(error).encode()]))
    client = HistorianClient(session, "http://test.local:8081")
    with pytest.raises(HistorianError, match="Method not found"):
        run(client.async_datapoints())
