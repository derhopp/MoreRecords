"""Read-only CCU-Historian JSON-RPC client and datapoint search.

Documented API:
- getDataPoint with no parameters lists all datapoints.
- getTimeSeriesRaw(id, start_ms, end_ms) returns raw timestamps, values,
  and quality states within [start_ms, end_ms).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import logging
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiohttp import ClientError

from .const import MAX_IMPORT_ROWS
from .csv_import import HistoryRow

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)
HTTP_READ_CHUNK_BYTES = 64 * 1024
MAX_POINT_OPTIONS = 100
WINDOW_DAYS = 31
MAX_POINTS = 20000


class HistorianError(ValueError):
    """An invalid Historian endpoint, API response, or oversized series."""


@dataclass(frozen=True, slots=True)
class HistorianPoint:
    """Metadata for selecting an archived CCU-Historian datapoint."""

    idx: int
    name: str
    parameter: str
    interface: str
    address: str
    unit: str
    value_type: str

    @property
    def label(self) -> str:
        """Searchable and unambiguous selector label."""
        return (
            f"{self.name} · {self.parameter} · "
            f"{self.interface}/{self.address} · #{self.idx}"
        )

    @property
    def haystack(self) -> str:
        """Match names, parameters and physical identities."""
        return self.label.casefold()


def rpc_url(base_url: str, api_key: str = "") -> str:
    """Normalize URL to the CCU JSON-RPC endpoint.

    Hosts may be private LAN hosts. Disallow credentials and arbitrary paths,
    redirects (in transport), fragments and pre-existing query parameters so
    the integration cannot accidentally send the API key elsewhere.
    """
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HistorianError("Use an http:// or https:// CCU-Historian address")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise HistorianError("URL must not contain credentials, query or fragment")
    if parsed.path.rstrip("/") not in ("", "/query/jsonrpc.gy"):
        raise HistorianError("Use the Historian base URL, not another path")
    # Accessing port can raise ValueError for invalid ports.
    try:
        _ = parsed.port
    except ValueError as err:
        raise HistorianError("Invalid URL port") from err
    query = urlencode({"k": api_key}) if api_key else ""
    return urlunsplit(
        (parsed.scheme, parsed.netloc, "/query/jsonrpc.gy", query, "")
    )


def parse_datapoints(payload: object) -> list[HistorianPoint]:
    """Validate and normalize the getDataPoint list response."""
    if not isinstance(payload, list):
        raise HistorianError("Historian did not return a datapoint list")
    if len(payload) > MAX_POINTS:
        raise HistorianError("Historian returned too many datapoints")
    points: list[HistorianPoint] = []
    seen: set[int] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        attributes = item.get("attributes") or {}
        identifier = item.get("id") or {}
        if not isinstance(attributes, dict) or not isinstance(identifier, dict):
            continue
        points.append(
            HistorianPoint(
                idx=idx,
                name=str(attributes.get("displayName") or "Unnamed"),
                parameter=str(identifier.get("identifier") or "VALUE"),
                interface=str(identifier.get("interfaceId") or ""),
                address=str(identifier.get("address") or ""),
                unit=str(attributes.get("unit") or ""),
                value_type=str(attributes.get("type") or ""),
            )
        )
    return sorted(points, key=lambda p: (p.name.casefold(), p.parameter.casefold(), p.idx))


def search_points(
    points: list[HistorianPoint], query: str, limit: int = MAX_POINT_OPTIONS
) -> tuple[list[HistorianPoint], int]:
    """Filter searchable name/parameter/address list; cap frontend options."""
    words = query.casefold().strip().split()
    matches = [point for point in points
               if all(word in point.haystack for word in words)]
    return matches[:limit], len(matches)


def date_range_ms(start: str, end: str, timezone_name: str) -> tuple[int, int]:
    """Inclusive local calendar dates to exclusive UTC Unix-ms boundaries."""
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError as err:
        raise HistorianError("Invalid date range") from err
    if last < first:
        raise HistorianError("End date must not precede start date")
    if last > datetime.now(timezone.utc).date() + timedelta(days=1):
        raise HistorianError("End date cannot be in the future")
    if (last - first).days > 365 * 50:
        raise HistorianError("Date range is too large")
    zone = ZoneInfo(timezone_name)
    return (
        round(datetime.combine(first, datetime.min.time(), tzinfo=zone).timestamp() * 1000),
        round(datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone).timestamp() * 1000),
    )


def parse_raw_response(payload: object) -> list[HistoryRow]:
    """Parse the parallel JSON-RPC arrays, preserving original milliseconds."""
    if not isinstance(payload, dict):
        raise HistorianError("Unexpected getTimeSeriesRaw result")
    ts = payload.get("timestamps")
    values = payload.get("values")
    states = payload.get("states")
    if (not isinstance(ts, list) or not isinstance(values, list)
            or not isinstance(states, list)):
        raise HistorianError("Missing raw Historian timestamps, values or states")
    if len(ts) != len(values) or len(ts) != len(states):
        raise HistorianError("Historian returned mismatched array lengths")
    if len(ts) > MAX_IMPORT_ROWS:
        raise HistorianError("Too many points in a single response")
    rows: list[HistoryRow] = []
    for timestamp, value, state in zip(ts, values, states):
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise HistorianError("Invalid Historian timestamp")
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise HistorianError("Invalid Historian timestamp")
        if isinstance(value, bool):
            text = "on" if value else "off"
        elif value is None:
            raise HistorianError("Null Historian value")
        else:
            text = str(value)
        if not text:
            raise HistorianError("Empty Historian state")
        # Historian quality bits are preserved in source but are not HA states.
        # Do not treat 1 (UNCERTAIN) as a binary sensor value.
        if not isinstance(state, int):
            raise HistorianError("Invalid Historian quality state")
        rows.append(HistoryRow(float(timestamp) / 1000.0, text))
    return rows


def resolve_duplicate_timestamps(
    rows: list[HistoryRow],
) -> tuple[list[HistoryRow], int, set[float]]:
    """Keep the last source state for each identical millisecond timestamp.

    A stable timestamp sort preserves CCU-Historian's order for tied samples.
    For a single point in time HA can represent only one unambiguous state in
    the import series. Retaining the last observation preserves the state
    *after* an instantaneous off/on transition, without inventing timing.
    """
    rows.sort(key=lambda row: row.timestamp)  # Python sorting is stable.
    unique: list[HistoryRow] = []
    duplicate_rows = 0
    conflicting_timestamps: set[float] = set()
    for row in rows:
        if unique and row.timestamp == unique[-1].timestamp:
            duplicate_rows += 1
            if unique[-1].value != row.value:
                conflicting_timestamps.add(row.timestamp)
            unique[-1] = row
        else:
            unique.append(row)
    return unique, duplicate_rows, conflicting_timestamps


def sort_unique(rows: list[HistoryRow]) -> list[HistoryRow]:
    """Compatibility wrapper returning last-in-source-order deduplicated rows."""
    return resolve_duplicate_timestamps(rows)[0]


async def read_response_complete(response: object, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read *all* of a JSON-RPC response, enforcing a size limit.

    aiohttp StreamReader.read(n) may return fewer than n bytes even if more
    data will arrive later. Read until EOF; otherwise a valid large response
    can look like truncated JSON and break datapoint discovery.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(HTTP_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise HistorianError(
                f"CCU-Historian JSON response exceeds {limit // 1048576} MiB"
            )
        chunks.append(chunk)
    return b"".join(chunks)


class HistorianClient:
    """Call the read-only JSON-RPC API using Home Assistant's aiohttp session."""

    def __init__(self, session: object, base_url: str, api_key: str = ""):
        self._session = session
        self._url = rpc_url(base_url, api_key)
        self.duplicate_rows = 0
        self.conflicting_timestamps = 0

    async def _rpc(self, method: str, parameters: list[object]) -> object:
        """Issue a bounded, non-redirecting JSON-RPC POST."""
        try:
            async with asyncio.timeout(35):
                async with self._session.post(
                    self._url,
                    json={"id": 1, "method": method, "params": parameters},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        if response.status in (401, 403):
                            raise HistorianError(
                                f"CCU-Historian HTTP {response.status}: access denied; "
                                "check the configured API key"
                            )
                        if response.status in (301, 302, 307, 308):
                            raise HistorianError(
                                f"CCU-Historian HTTP {response.status}: redirected; "
                                "check the server address, port, and API path"
                            )
                        raise HistorianError(
                            f"CCU-Historian HTTP {response.status}: verify the "
                            "JSON-RPC endpoint /query/jsonrpc.gy"
                        )
                    payload = await read_response_complete(response)
            data = json.loads(payload)
        except (ClientError, OSError, asyncio.TimeoutError) as err:
            raise HistorianError(
                f"CCU-Historian request failed ({type(err).__name__}); "
                "check reachability from Home Assistant, not just from a browser"
            ) from err
        except (UnicodeError, json.JSONDecodeError) as err:
            raise HistorianError(
                "CCU-Historian returned invalid JSON; check whether the "
                "configured URL points to the JSON-RPC server"
            ) from err
        if not isinstance(data, dict):
            raise HistorianError("Unexpected Historian JSON-RPC response")
        if data.get("error") is not None:
            error = data["error"]
            message = error.get("message", "Unknown JSON-RPC error") if isinstance(error, dict) else "JSON-RPC error"
            raise HistorianError(f"CCU-Historian: {message}")
        if "result" not in data:
            raise HistorianError("CCU-Historian response has no result")
        return data["result"]

    async def async_datapoints(self) -> list[HistorianPoint]:
        """Retrieve all searchable datapoints once per import wizard."""
        return parse_datapoints(await self._rpc("getDataPoint", []))

    async def async_history(
        self, point_id: int, start_ms: int, end_ms: int
    ) -> list[HistoryRow]:
        """Fetch raw history in short bounded windows with a hard row cap."""
        if not isinstance(point_id, int) or point_id < 0 or end_ms <= start_ms:
            raise HistorianError("Invalid point or time range")
        chunk_ms = WINDOW_DAYS * 86_400_000
        rows: list[HistoryRow] = []
        for window_start in range(start_ms, end_ms, chunk_ms):
            window_end = min(end_ms, window_start + chunk_ms)
            result = await self._rpc(
                "getTimeSeriesRaw", [point_id, window_start, window_end]
            )
            new_rows = parse_raw_response(result)
            if len(rows) + len(new_rows) > MAX_IMPORT_ROWS:
                raise HistorianError(
                    f"More than {MAX_IMPORT_ROWS} rows; narrow the date range"
                )
            rows.extend(new_rows)
        unique, duplicates, conflicts = resolve_duplicate_timestamps(rows)
        self.duplicate_rows = duplicates
        self.conflicting_timestamps = len(conflicts)
        if conflicts:
            examples = ", ".join(
                datetime.fromtimestamp(ts, timezone.utc).isoformat()
                for ts in sorted(conflicts)[:3]
            )
            _LOGGER.warning(
                "CCU-Historian datapoint %s: %s duplicate rows at identical "
                "timestamps (%s with conflicting values); using the final "
                "state in source order for each timestamp; examples UTC: %s",
                point_id, duplicates, len(conflicts), examples,
            )
        elif duplicates:
            _LOGGER.info(
                "CCU-Historian datapoint %s: removed %s identical "
                "duplicate timestamp rows",
                point_id, duplicates,
            )
        return unique
