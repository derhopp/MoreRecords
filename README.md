# MoreRecords

MoreRecords is a **Home Assistant custom integration (development preview)** for prepending historical CSV observations to an **existing** entity's Recorder history, without changing its current state.

> **Experimental — no release has been published.** MoreRecords writes to Home Assistant's private Recorder schema. Make a full Home Assistant backup first. Compatibility is not guaranteed across Home Assistant upgrades. Use only in a disposable test instance until you have verified your version.

## Features

- Select an existing numeric `sensor` or `binary_sensor` in Home Assistant.
- Upload a two-column CSV from your computer using Home Assistant's FileSelector.
- Parse ISO-8601/Unix timestamps and local European timestamps, respecting the Home Assistant time zone.
- Validate value compatibility and reject ambiguous daylight-saving times, duplicate timestamps, overlapping history, non-finite numeric values, and unsupported entity types.
- Show a row-count/time-range confirmation screen; no import occurs without explicit confirmation.
- Prepend full-resolution states on the Recorder thread, within a SQLite transaction; leave current entity state unchanged.
- English and German configuration flows follow Home Assistant's language selection.

## Requirements

- Home Assistant Core with compatible Recorder internals (initial target: **2026.10+**, not yet verified on a live installation).
- Recorder configured to use **SQLite**.
- Existing target entity with at least one Recorder state.
- **Automatic purging disabled**, or imported full-resolution history will disappear on a later purge:

  ```yaml
  recorder:
    auto_purge: false
  ```

  Restart Home Assistant after changing this setting. With purging disabled, all recorded entities grow indefinitely: monitor disk space and database size.
- A complete, restorable backup before every import.

## Installation using HACS (custom repository)

1. Open HACS → three-dot menu → **Custom repositories**.
2. Enter `https://github.com/derhopp/MoreRecords`, choose **Integration**, and add it.
3. Install MoreRecords from its custom repository. **Development only:** no tagged release exists; installation from the default branch depends on the HACS version and available development-version options.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and search for **MoreRecords**.
6. Open the integration's **Configure** action to begin an import. There is no separate dashboard panel.

For manual installation, copy `custom_components/more_records/` into your HA `config/custom_components/` and restart.

## CSV format

Exactly two columns, in this order: timestamp and value. A recognized header is optional.

```csv
timestamp;value
2023-01-01T12:00:00+01:00;21,5
2023-01-01T12:05:00+01:00;21,7
```

Supported timestamps: ISO-8601 with offset (recommended), ISO-8601 without offset, `DD.MM.YYYY HH:MM[:SS]`, Unix seconds (10 digits), and Unix milliseconds (13 digits). Naive times are interpreted using the configured HA time zone. Ambiguous/nonexistent DST local times are rejected; add a UTC offset to disambiguate. Delimiters: semicolon, comma or tab. Comma decimal values require semicolon or tab delimiters.

Supported state values: finite numbers for numeric sensors, and `on/off`, `true/false` or `1/0` for binary sensors. Unsupported entities and textual sensors are rejected, rather than guessed. Values are assumed to use the target entity's existing unit; no unit conversion occurs.

The initial version accepts at most 100,000 observations per file and rejects conflicting duplicate timestamps. Split larger exports into chronological pieces and import **oldest first**.

## Import workflow

1. Choose an entity and upload the CSV.
2. MoreRecords validates the CSV and compares its newest timestamp with the first recorded state of the entity. **All CSV rows must be strictly older**.
3. Review the entity, row count, CSV time range, and earliest existing HA timestamp.
4. Explicitly confirm. The internal Recorder task performs an atomic SQLite prepend. The wizard reports the row count on success.

Repeating the same file is rejected as overlapping existing history. Existing Recorder rows are not overwritten. The importer does not create statistics or write into the current in-memory entity state.

## Limitations and safety

- This is a **provisional implementation**, not yet validated in a live Home Assistant environment. The direct Recorder writer uses **private, version-sensitive APIs**. Do not use with production data until integration tests on your exact Home Assistant version pass.
- CSV files and preview state are temporary and wizard-local. An uploaded file is deleted when processed.
- Historic attributes are inherited from the earliest existing Recorder row; this may misrepresent past units and device classes. Only use for entities whose semantics have stayed constant.
- Binary sensor status mapping is conventional; source-specific enumerations are not inferred.
- Full-resolution Recorder states are separate from long-term statistics. MoreRecords does not import or rebuild historical long-term statistics.
- Prepending is intentionally limited to the time **before** the first recorded state. Gaps, overlaps, conflict resolution, and rollbacks of completed imports are not supported.
- An import runs synchronously on the Recorder queue. Very large CSV files may delay other recorded events. Keep imports small and inspect logs.
- Recorder `auto_purge: false` prevents automatic cleanup of **all** entities, not just imported ones.

## Development

```bash
python -m pip install pytest
python -m pytest -q
```

GitHub Actions includes parser tests, translation/manifest checks, HACS validation, and Hassfest. The unit tests do not substitute for real Recorder integration testing.

See [Development plan](docs/DEVELOPMENT.md) and [Safety](docs/SAFETY.md). No release is planned until compatibility and data integrity are independently verified.

## License

MIT — see [LICENSE](LICENSE).
