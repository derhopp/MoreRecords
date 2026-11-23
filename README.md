# MoreRecords

MoreRecords is a **Home Assistant custom integration (development preview)** for importing historical CSV or CCU-Historian observations and **exporting Recorder states to CSV** to an **existing** entity's Recorder history, without changing its current state.

> **Experimental beta — `0.1.0b3` prerelease.** MoreRecords writes to Home Assistant's private Recorder schema. Make a full Home Assistant backup first. Compatibility is not guaranteed across Home Assistant upgrades. Use only in a disposable test instance until you have verified your version.

## Features

- Export raw state history for any existing recorded entity to an on-demand CSV file, with selectable start and end date/time.

- Select **any existing entity recorded by Home Assistant**, including `light`, `switch`, `input_boolean`, `binary_sensor`, `sensor`, `select`, `lock`, and others. Source values are checked against the target's domain and known state metadata.
- Choose **CSV file** or **CCU-Historian** as the import source.
- Connect to CCU-Historian via its configurable HTTP(S) web address, optionally with an API key. Search datapoints by **display name**, **parameter**, interface or device address, and select a local calendar date range.
- Upload a two-column CSV from your computer using Home Assistant's FileSelector.
- Parse ISO-8601/Unix timestamps and local European timestamps, respecting the Home Assistant time zone.
- Validate source state compatibility and reject ambiguous daylight-saving times, duplicate timestamps, non-finite numeric values, and invalid on/off or selectable states.
- Show a row-count/time-range confirmation screen; no import occurs without explicit confirmation.
- Merge missing full-resolution source state timestamps through the Recorder writer thread in one SQLite transaction; existing state values, attributes and current HA state are preserved.
- **Exact timestamp merge**: existing HA states are authoritative; source rows at different timestamps can fill the already recorded interval. Values at exactly matching timestamps are skipped (and conflicting values counted), and source points after the newest stored HA state are not appended.
- **Respect automatic Recorder purging**: if enabled, skip source points older than the live `purge_keep_days` cutoff; if disabled, importing older history is unrestricted by retention.
- English and German configuration flows follow Home Assistant's language selection.

## Requirements

- Home Assistant Core with compatible Recorder internals (initial target: **2026.10+**, not yet verified on a live installation).
- Recorder configured to use **SQLite**.
- Existing target entity with at least one Recorder state.
- **Recorder retention respected automatically:** when `auto_purge: true` (the default), only source observations newer than or equal to the live `now - purge_keep_days` boundary are eligible. The default `purge_keep_days` is **10**; the integration reads the effective setting from Recorder, not from hardcoded YAML values. When `auto_purge: false`, there is no retention limit imposed by MoreRecords. You do **not** need to disable automatic purging to use the importer.

  Example configuration for retaining 30 days of detailed history:

  ```yaml
  recorder:
    auto_purge: true
    purge_keep_days: 30
  ```

  With `auto_purge` enabled, imported raw data will still expire normally as the rolling retention window advances. To keep raw history indefinitely, explicitly use `auto_purge: false` and account for database growth. No per-entity retention exceptions are created by MoreRecords.
- A complete, restorable backup before every import.

## Installation using HACS (custom repository)

1. Open HACS → three-dot menu → **Custom repositories**.
2. Enter `https://github.com/derhopp/MoreRecords`, choose **Integration**, and add it.
3. Install the `0.1.0b2` **prerelease** from the custom HACS repository (enable beta/prerelease versions in HACS if necessary). This is not a stable release.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and search for **MoreRecords**.
6. Open the integration's **Configure** action and choose **Import** or **Export CSV**. There is no separate dashboard panel.

For manual installation, copy `custom_components/more_records/` into your HA `config/custom_components/` and restart.

## Export raw Home Assistant Recorder states to CSV

1. Open **Settings → Devices & services → MoreRecords → Configure**.
2. Choose **Export CSV**, select the entity (`light`, `switch`, `sensor` etc.) and a start/end date and time.
3. Preview the raw row count and timestamps; confirm to generate a single-use download link.
4. Click **Download CSV file**. The link expires after 10 minutes and can be used only once. A new export is required to retrieve the file again.

The exported UTF-8, semicolon-delimited file has exactly two columns: `timestamp;value`. Timestamps are UTC ISO-8601 with microsecond formatting. Rows are sorted by stored Recorder timestamp, then internal state ID, and repeated identical states are **not** dropped. Values are escaped according to standard CSV rules. The selected period uses **[start, end)**; timestamps on the start are included and timestamps on the end are excluded. Date/time entries without timezone offsets are interpreted in the configured HA time zone, rejecting ambiguous or nonexistent DST timestamps.

Exports use actual Recorder state rows, **not** long-term statistics, history graph smoothing, events, or inferred gaps. Purged rows are unavailable. The exporter is read-only and limits each file to **100,000 rows**; shorten the time interval for larger exports. The file is delivered through a signed, time-limited, authenticated Home Assistant endpoint; no publicly accessible `/local` file is generated. Raw states stored as NULL appear as empty cells. A CSV reimport may require cleaning empty values or duplicate exact timestamps.

The export uses internal SQLAlchemy Recorder models and must be verified against the installed HA Core version. Live frontend/download integration testing is still required before production use.

## CSV format

Exactly two columns, in this order: timestamp and value. A recognized header is optional.

```csv
timestamp;value
2023-01-01T12:00:00+01:00;21,5
2023-01-01T12:05:00+01:00;21,7
```

Supported timestamps: ISO-8601 with offset (recommended), ISO-8601 without offset, `DD.MM.YYYY HH:MM[:SS]`, Unix seconds (10 digits), and Unix milliseconds (13 digits). Naive times are interpreted using the configured HA time zone. Ambiguous/nonexistent DST local times are rejected; add a UTC offset to disambiguate. Delimiters: semicolon, comma or tab. Comma decimal values require semicolon or tab delimiters.

Supported state values depend on the target entity: `on/off`, `true/false`, `1/0`, or `1.0/0.0` for `light`, `switch`, `binary_sensor`, `input_boolean` and other binary-state domains; finite numeric values for numeric sensors, `number`, `counter`, `input_number`; known options for `select`, `input_select` and `climate`; bounded text states for other domains. `unknown` and `unavailable` are legitimate Home Assistant states. Values are assumed to use the target's existing unit; no unit conversion occurs.

The initial version accepts at most 100,000 observations per file and rejects conflicting duplicate timestamps. When automatic Recorder purging is enabled, CSV rows older than its configured retention window are counted as skipped and never written. Split larger exports into chronological pieces and import **oldest first**.

## Import directly from CCU-Historian

1. In **Settings → Devices & services → MoreRecords → Configure**, select a recorded target entity (including `light`/`switch`) and **CCU-Historian** as the source.
2. Enter the Historian web address, for example `http://192.168.1.50:8082`. The importer uses the documented `/query/jsonrpc.gy` JSON-RPC endpoint. Optionally enter the `webServer.apiKeys` key (stored in HA integration options).
3. Enter a search term matching all or part of the **data point display name or parameter**, e.g. `Schalter Küche Decke STATE`. The search also includes interface and physical address. You can search again by returning to the previous step; at most the first 100 matches are shown at once.
4. Choose the matching datapoint ID and the start/end dates, then review the preview and explicitly confirm the import. Start/end dates refer to the Home Assistant local time zone, with both calendar dates included.

MoreRecords calls CCU-Historian's `getDataPoint` (list metadata) and `getTimeSeriesRaw` (original timestamp/value observations). No PostgreSQL access is required; reads are batched in 31-day windows. Fetching is capped at **100,000 observations per operation**; select shorter date ranges for larger archives. The fetch start is automatically clamped to the live Recorder retention cutoff when `auto_purge` is active, avoiding downloads of data that would immediately expire. Source and target units must match if both are reported; the importer does **not** automatically rescale values (for example `100%` vs `%`).

### Identical CCU-Historian timestamps with differing states

CCU-Historian can store consecutive observations with the **same millisecond timestamp** but different state values, e.g. `off` followed by `on`. MoreRecords previously aborted the entire direct import with `HistorianError: Conflicting values for one Historian timestamp`. Now the direct CCU-Historian importer applies a deterministic **last-source-value-wins** policy: it retains the last observation in source order for each exact timestamp, drops earlier observations at that instant, and logs any conflicts including representative UTC timestamps. The preview shows both dropped duplicate-row count and conflicting-timestamp count. No artificial timestamps are created.

A manually exported example of `Schalter Bad Spots.STATE` (ID 172) has 30,655 measurements and two conflicting timestamps: 25 December 2023 18:01:24.117 and 7 November 2025 22:44:45.265, both with `0` then `1`. The resulting state at both timestamps is `1`, leaving 30,653 distinct timestamps. Generic CSV imports **still reject** duplicate timestamps. Recorder retention and history-overlap filtering still apply after resolving the CCU-Historian data.

### Troubleshooting CCU-Historian value validation

When CCU-Historian datapoints and raw samples load successfully but an import stops with `ValidationError`, the datapoint's values may not match the target entity's expected state format. The import wizard now displays the actual rejection reason next to the datapoint/time selection, and Home Assistant logs the datapoint ID, target entity ID and error. For Boolean on/off entities (`light`, `switch`, `binary_sensor`, etc.), CCU-Historian's `0.0` and `1.0` numeric representations are normalized to Home Assistant `off` and `on` in addition to `0`, `1`, `false`, `true`, `off`, and `on`. Other numbers such as `50.0` are intentionally **not** interpreted as Boolean states. No Recorder data is written when validation fails.

### Troubleshooting CCU-Historian connectivity

If the HA wizard reports that the datapoint list could not be loaded, read the **Last connection diagnostic** displayed on the connection step and the `custom_components.more_records` Home Assistant log message. Reachability from a desktop browser does **not** prove that Home Assistant itself can reach the server. MoreRecords normalizes `http://192.168.1.48:8081` to `http://192.168.1.48:8081/query/jsonrpc.gy`. Leave the API key blank if you have not configured `webServer.apiKeys`.

Check these URLs from a browser on your network:

- `http://192.168.1.48:8081/query/jsonrpc.gy?m=echo&p1=ping&i=1` should return JSON with `result`.
- `http://192.168.1.48:8081/query/jsonrpc.gy?m=getDataPoint&p1=2141&i=1` should return the selected datapoint.
- `http://192.168.1.48:8081/query/jsonrpc.gy?m=getDataPoint&i=1` should return the full datapoint list. This response can be large.

The importer reads the full HTTP response in chunks rather than assuming a single read returns a complete JSON document. Check local network/firewall access **from Home Assistant** for timeout or connection errors. An HTTP 404 can indicate the wrong port/path; HTTP 401/403 indicates restricted access. Errors are shown without logging the configured API key.

CCU-Historian runs on the configured Home Assistant network. For API-key-protected installations, use HTTPS where possible because the Historian API key is sent as a URL query parameter over the connection. The integration never modifies CCU-Historian data.

## Import workflow

1. Choose an entity and upload the CSV.
2. MoreRecords validates source states and compares their exact timestamps with stored Recorder states. Missing source timestamps in the existing Recorder window are eligible; existing timestamps win over any different source value. Source values beyond the latest HA state are not inserted.
3. Review the selected gap transition rule, source count, missing timestamps to insert, exact-timestamp conflicts, retention skips, newer-than-HA skips, and the resulting artificial marker, if any.
4. Explicitly confirm. The internal Recorder task performs an atomic SQLite prepend. The wizard reports the row count on success.

Repeating a source import generally produces no new rows (all matching timestamps are skipped). Existing Recorder values and attributes are not overwritten, even when the source disagrees; the disagreement is counted separately. The importer does not create statistics or write into the current in-memory entity state.

## Transition state between imported history and the first Recorder row

The import wizard requires an explicit choice for the interval after the final **newly imported source observation before the first existing Recorder state** and before that existing state. Options:

- **Hold last state of import** (`hold_last`): Home Assistant keeps the last imported source state until the original first Recorder state begins. This is a continuation assumption, not another measurement.
- **Use oldest state of Recorder** (`oldest_recorder`): create a synthetic transition one microsecond after the last imported prefix observation to the value of the first original Recorder state. The original Recorder state and timestamp remain unchanged.
- **Mark gap unavailable** (`unavailable`): create a synthetic transition one microsecond after the last imported prefix observation to `unavailable`; the first original Recorder state still ends the gap at its stored timestamp.

If the selected values already agree, no artificial marker is needed. If the import adds only missing values **within** HA's previously recorded interval, there is no leading gap to bridge and this setting does not create a marker. The preview shows the selected rule and any planned synthetic timestamp. A bridge marker is not proof of a real source event, is not replayed as a live event, and cannot certify CCU-Historian continuity. Confirming the import does **not** imply LightAway has verified all source history. The importer recomputes the full decision immediately before writing, aborting if the Recorder has changed since preview.

**Beta 2 release note:** This exact-timestamp merge and the three operator-selected transition rules are included in `0.1.0b2`; `0.1.0b1` remains available as an older prerelease.

## Switch and light states versus button press events

MoreRecords can backfill historic **entity state transitions** such as `on`/`off` for `switch.kitchen_light` and `light.kitchen`. CCU-Historian Boolean values are normalized to these standard Home Assistant states. This is suitable for visualizing recorded switching history and for consumers that query the Recorder database.

A physical Homematic button press (`PRESS_SHORT`, `PRESS_LONG`) is typically an **event**, not an `on`/`off` light or switch state. MoreRecords **does not replay historic events** or retroactively trigger Home Assistant automations. Do not map `PRESS_SHORT` to an `on` state or expect imported events to run automations. Home Assistant `event`/`button` entity *states* are timestamps and must be imported as valid ISO timestamps; the historical event payload/attributes are not reconstructed by a two-column import.

A presence simulator such as LightAway benefits from imported history **only if it queries Recorder history**; if it listens exclusively to live events, a history import alone will not reproduce physical presses. Verify its behavior separately.

## Limitations and safety

- This is a **provisional implementation**, not yet validated in a live Home Assistant environment. The direct Recorder writer uses **private, version-sensitive APIs**. Do not use with production data until integration tests on your exact Home Assistant version pass.
- CSV files, Historian search results, and preview state are temporary and wizard-local. An uploaded file is deleted when processed.
- Historic attributes are inherited from the earliest existing Recorder row; this may misrepresent past units and device classes. Only use for entities whose semantics have stayed constant.
- Boolean state mapping (`true`/`false`, `1`/`0` to `on`/`off`) is applied only to canonical on/off domains; arbitrary Homematic button-press enumerations are never converted into light/switch states.
- Generic textual states are supported but this does not guarantee complete semantics for every Home Assistant domain. For example, historical `light` brightness/color attributes and button event details cannot be reconstructed from only timestamps and state values.
- Full-resolution Recorder states are separate from long-term statistics. MoreRecords does not import or rebuild historical long-term statistics.
- Newer development commits on `main` can fill missing exact timestamps **inside** the stored HA history. Original Recorder rows always win when timestamps coincide. Source records after the latest stored HA state are intentionally skipped to avoid altering the live timeline. Undoing completed imports remains unsupported.
- An import runs synchronously on the Recorder queue. Very large CSV files may delay other recorded events. Keep imports small and inspect logs.
- Recorder `auto_purge: false` prevents automatic cleanup of **all** entities, not just imported ones. If auto-purge is enabled, the integration filters out states already past the rolling retention boundary. Home Assistant may remove imported states later as the window advances, including during the next scheduled purge.

## Development

```bash
python -m pip install pytest
python -m pytest -q
```

GitHub Actions includes parser tests, translation/manifest checks, HACS validation, and Hassfest. The unit tests do not substitute for real Recorder integration testing.

See [Development plan](docs/DEVELOPMENT.md) and [Safety](docs/SAFETY.md). No stable release is planned until compatibility and data integrity are independently verified.

## Beta prerelease

MoreRecords `0.1.0b2` is the second experimental beta. It extends the CCU-Historian and CSV importers with exact-timestamp Recorder merging and explicit user-selectable transition states, while retaining the LightAway-oriented coverage verification introduced in the first beta. See the [0.1.0b2 release notes](docs/releases/0.1.0b2.md) and [0.1.0b1 release notes](docs/releases/0.1.0b1.md). The beta remains experimental: make a restorable Home Assistant backup before testing, because the import path writes Home Assistant's private Recorder schema.

## License

MIT — see [LICENSE](LICENSE).

## Verified CCU-Historian continuity for LightAway

MoreRecords can optionally save a **local, per-entity coverage attestation** only after a CCU-Historian import or *verification-only* query: the operator explicitly confirms that CCU-Historian stored **every state change** in the queried interval and MoreRecords verifies all returned observations against the actual Recorder rows. The attestation starts at the first real source observation; no initial state is invented. It contains entity ID and UTC time bounds only, never raw readings or Historian credentials, and is stored in Home Assistant `.storage/more_records_verified_history_v1`.

An unchanged `on` state over many weeks is eligible *only when the whole queried interval is confirmed complete* and its source readings match Recorder. Merely seeing an old state in a CSV is insufficient. Importing older data via CSV **does not** establish continuous coverage. If no new rows need writing, a CCU-Historian query can still be explicitly verified; the importer will not overwrite existing Recorder history. A mismatched source/Recorder query is rejected for coverage, and an import might already have been committed before the final check. Be sure the queried interval has no missing or untrusted CCU measurements before confirming.

LightAway can use such entity-specific verified intervals separately from actual Recorder runs, without fabricating `RecorderRuns` records. **Do not manually edit the coverage store.** If you change, purge or replace the recorded history, re-import/re-verify the original Historian interval before trusting it. The coverage attestation is a claim about complete source observation, not about the Recorder process running in the past.
