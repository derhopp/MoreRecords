# MoreRecords

**Import and export historical Home Assistant Recorder states** from CSV files or CCU-Historian, without changing the live entity state or replaying events.

MoreRecords is an independently maintained **Home Assistant custom integration**, available through HACS or manual installation. It supports English and German.

> **Current release: `0.1.0b5` (experimental prerelease).** MoreRecords inserts historical rows through **private, version-sensitive Home Assistant Recorder internals**. Make a restorable Home Assistant backup and validate behavior on your exact Home Assistant Core version before writing to production history. A **first stable release is being prepared, not yet published**.

## Features

- Import timestamp/value CSV data to an **existing, Recorder-tracked entity** (including supported `light`, `switch`, `binary_sensor`, `sensor`, `select` and other domains).
- Import real timestamp/value observations directly from **CCU-Historian**, with datapoint search and optional API key.
- Preview candidate rows, exact-time conflicts, skipped rows and Recorder retention before an explicitly confirmed import.
- Keep original Recorder values and timestamps authoritative; fill only eligible missing timestamps. Never modify live entity states or replay past events.
- Choose how the historical leading gap is represented: **hold imported state**, **use oldest Recorder state**, or **mark unavailable**.
- Optionally add **daily local-midnight support rows** to sparse imports, with strict source-value and 00:00–06:00 exclusion rules.
- Export raw Recorder rows to a **signed, authenticated, single-use CSV link**.
- German and English installation/configuration flows and translated dropdown choices.

## Requirements

| Requirement | Details |
| --- | --- |
| Home Assistant | Core **2026.10.0+** according to the HACS minimum; **live Recorder compatibility is not yet independently verified** |
| Recorder backend | **SQLite** for historical writes |
| Entity | Must already exist and have recorded history |
| Backup | **Full restorable HA backup** before imports |
| CCU-Historian | Optional; accessible from the HA host/container for direct source imports |

Recorder retention remains in force: with `auto_purge: true`, raw states older than `now - purge_keep_days` are skipped or eventually removed by Home Assistant. MoreRecords does not exempt individual entities from the purge.

## Installation

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/derhopp/MoreRecords` as an **Integration**.
3. Install the latest published prerelease (`0.1.0b5`); enable prerelease display in HACS if necessary.
4. **Restart Home Assistant**.
5. Go to **Settings → Devices & services → Add integration → MoreRecords** and complete the one-time setup.
6. Open **MoreRecords → Configure** to begin an import or CSV export.

Manual installation and update instructions: [Installation guide](docs/INSTALLATION.md).

## Configuration and usage

MoreRecords has **no YAML setup**. Each operation is started through **Settings → Devices & services → MoreRecords → Configure**.

### Import a CSV

Select a target entity, **CSV file**, and a gap-transition policy. Optionally select daily supports, upload your CSV, inspect the preview and explicitly confirm.

Example UTF-8 CSV (`timestamp;value`):

```csv
timestamp;value
2026-11-19T18:30:00+01:00;on
2026-11-20T11:15:00+01:00;off
```

Use timestamps with UTC offsets when possible. Local timestamps use the configured HA time zone, and ambiguous or nonexistent DST instants are rejected. Valid values depend on the destination's entity domain and existing units.

### Import from CCU-Historian

Select the target entity and **CCU-Historian**; enter the server URL and optional API key, search for a datapoint, select start/end dates, review the preview and confirm. Data are read through the Historian JSON-RPC interface without modifying the source. Duplicate Historian timestamps retain the **last source observation** and are reported in the preview. With the optional daily supports enabled, the complete selected source window can be queried to retain a genuine pre-cutoff value for the first eligible midnight.

### Optional daily 00:00 supports

This feature is **off by default** and must be enabled explicitly for each import. It copies the last **actually observed source state within the selected import window** to a synthetic 00:00 timestamp in the HA local time zone. No point is created if any actual imported-source or existing Recorder observation falls between **00:00 (inclusive) and 06:00 (exclusive)** that day. Support timestamps must satisfy Recorder retention; a genuine source observation **before** the purge cutoff can establish the first eligible midnight, but that expired source row is not imported itself.

A support row reuses a known value; its midnight **timestamp is artificial**. It does not prove the state was continuously correct in the presence of unrecorded changes. Daily supports do not certify complete CCU-Historian coverage.

### Export CSV

Select **Export CSV**, entity and start/end times, review the raw row count, confirm and follow the signed link. The link is single-use and expires after **10 minutes**. Exported files have `timestamp;value` columns with UTC timestamps and preserve repeated raw Recorder observations. A single export is limited to **100,000 rows**; expired/purged history cannot be recovered.

For the full description of gap policies, data format, limits and Historian configuration, see [Configuration and usage](docs/CONFIGURATION.md).

## Language support

The installation and options wizards have complete **German (`de`) and English (`en`) translation catalogs**. Home Assistant selects the flow text and dropdown translation from the frontend language. Runtime preview placeholders (such as the selected gap policy or Yes/No) follow the **configured HA instance language**, defaulting to English when the instance language is not German. Individual users with a different frontend language from the instance language may therefore see a mixed-language **dynamic preview**. Both frontend language modes and this edge case are part of the [stable-release verification checklist](docs/RELEASE_CHECKLIST.md).

## Safety and limitations

- Writes depend on Home Assistant **private Recorder APIs** and currently support **SQLite only**; actual compatibility must be tested on your Core version.
- Original existing Recorder rows take precedence at exact matching timestamps; source timestamps newer than the latest stored Recorder state are not appended.
- Artificial gap markers and daily supports are **not measured events**, and do not provide proof of completeness.
- Historical changes are inserted into Recorder, not replayed through automations or Home Assistant's event bus.
- Historical state attributes, physical button payloads and long-term statistics are **not** reconstructed from two-column source observations.
- Source validation and preview rechecks reduce risk but **cannot replace a restorable backup**. Import undo is not provided.
- Only an **explicit** complete-source confirmation followed by exact verification against Recorder rows can record optional CCU-Historian history coverage. Ordinary CSVs and generated supports cannot certify continuity.

Read [Safety and database integrity](docs/SAFETY.md) before importing.

## Troubleshooting and support

For installation problems, CCU-Historian connectivity, time-zone validation, raw translation keys in the UI, retention skips or export link expiry, see [Troubleshooting](docs/TROUBLESHOOTING.md).

For bug reports, use [GitHub Issues](https://github.com/derhopp/MoreRecords/issues) and include the Home Assistant Core version, MoreRecords version, redacted preview and relevant log lines. **Do not publish** unredacted entity activity history, API keys or Recorder databases.

## Development and release status

Run unit tests with:

```bash
python -m pip install pytest aiohttp
python -m pytest -q
```

CI checks include unit tests, translation/manifest validation, HACS and Hassfest. These checks are **not a substitute for real HA frontend and Recorder integration tests**.

- [Development notes](docs/DEVELOPMENT.md)
- [First stable release readiness checklist](docs/RELEASE_CHECKLIST.md)
- [Changelog](CHANGELOG.md)
- [Current prereleases](https://github.com/derhopp/MoreRecords/releases)

**No stable version has been tagged or published.** The withdrawn `0.1.0b4` retention-anchor experiment is not included in `0.1.0b5`.

## License

[MIT](LICENSE).
