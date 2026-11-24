# Configuration and usage

MoreRecords has no YAML configuration. Add it from **Settings → Devices & services**, then open **MoreRecords → Configure** whenever you want to import or export.

## Import from CSV

1. Choose **Import**, a target entity that is actively recorded, **CSV file** and a required gap-transition policy.
2. Optionally enable **Create daily 00:00 support rows**. The option is **off by default**.
3. Upload a UTF-8 CSV with timestamp and value columns; review the preview before accepting any Recorder modification.
4. Confirm with a restorable Home Assistant backup in place.

Example:

```csv
timestamp;value
2026-11-19T18:30:00+01:00;on
2026-11-20T11:15:00+01:00;off
```

Timestamp formats include ISO-8601 with offset (recommended), recognized local date/time formats, and Unix timestamps. Local timestamps without an offset are parsed using Home Assistant's configured time zone. Ambiguous and nonexistent DST instants are rejected. CSV delimiters may be semicolon, comma, or tab. The importer preserves recorded precision, rejects duplicate source timestamps, and validates states against the target entity's domain. For numeric data, make sure units match.

## Import directly from CCU-Historian

1. Select **Import → CCU-Historian**, choose the existing recorded target entity, a gap policy and optional daily supports.
2. Provide the CCU-Historian HTTP(S) base URL, such as `https://historian.example.net:8082`; supply an API key only if your Historian requires one.
3. Search by datapoint display name, parameter, interface or address.
4. Select a datapoint and local start/end dates, then review the preview and confirm.

The integration reads CCU-Historian using JSON-RPC `getDataPoint` and `getTimeSeriesRaw`, in bounded date ranges. It does **not** modify the Historian database. When the source repeats the same timestamp, the last source value wins and the preview reports conflicts. API keys are sensitive: use a trusted server and HTTPS where available.

## Gap transition policy

The required gap policy concerns the period between the last **newly imported prefix observation** and the first existing Recorder state:

| Setting | Behavior |
| --- | --- |
| **Hold last state of import** | Carries forward the final source state; no transition marker is needed |
| **Use oldest state of Recorder** | May insert an artificial transition to the first stored Recorder value |
| **Mark gap unavailable** | May insert an artificial `unavailable` transition |

A synthetic gap state is a modeling choice, not a measured event or proof of source completeness.

## Optional local-midnight support points

With the daily-support setting enabled, each local 00:00 may receive the **last actual source state observed within the user-selected import interval**. This *reuses a known state value*; the 00:00 timestamp is not an observed event. No support is generated for a day with any actual source or Recorder sample between 00:00 (inclusive) and 06:00 (exclusive). The support timestamp itself must be eligible under Recorder retention. An actual source observation from the selected interval that predates the retention cutoff can seed the first eligible midnight; the expired observation itself is not reinserted. No support is generated before the first known source observation or beyond the latest existing HA Recorder state. The selected gap-transition policy takes precedence.

These supports cannot verify that all intermediate state changes were recorded. When enabled for CCU-Historian, the entire user-selected source window is fetched to find a possible state before the cutoff. For CSV, the source timestamp range is the available import window.

## Recorder retention and exact-time collisions

With `recorder.auto_purge: true`, observations older than `now - purge_keep_days` will not be inserted. Imported rows will still be purged later. If `auto_purge` is false, this import-specific time restriction is disabled. Existing Recorder rows remain authoritative when the timestamps exactly match, and rows after the latest original Recorder record are not appended. The writer checks eligibility and source/Recorder snapshot again before committing.

## Export CSV

1. Open **MoreRecords → Configure → Export CSV**.
2. Select the target entity and the start/end timestamps.
3. Review the row count and confirm.
4. Follow the signed download link (expires in **10 minutes** and works once).

Exports contain **raw Recorder rows**, not interpolation, Recorder-run reconstruction, long-term statistics, or previously purged states. Files use UTF-8, semicolon-delimited `timestamp;value` and UTC ISO timestamps with microseconds. The interval is **start-inclusive, end-exclusive**. Each export is limited to **100,000 rows**. There is no persistent public `/local` export file.

## Optional CCU-Historian continuity attestation

The wizard can offer an independent, **explicit confirmation** that a complete CCU-Historian source interval has been captured. The integration rechecks actual Recorder rows before storing a local per-entity verification record. **CSV imports and artificial gap/midnight support rows are not evidence of complete history.** See [Safety](SAFETY.md).
