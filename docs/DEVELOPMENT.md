# Development plan

## Scope

- A single Home Assistant config entry enables a user-invoked CSV import options flow.
- User selects an existing entity and chooses a two-column CSV upload or CCU-Historian JSON-RPC source.
- CCU-Historian endpoint URL, optional API key, searchable metadata by display name/parameter and user-selected date range are supported.
- Any existing entity with SQLite Recorder history can be selected; domain-aware state validation normalizes canonical on/off domains (`light`, `switch`, etc.), numeric states and selectable options. Textual states are accepted for other domains without implying event replay.
- Dry-run validation and preview precede explicit confirmation.
- The destination's current Home Assistant state is never changed.
- The importer ignores source rows whose timestamps are on or after the earliest existing HA state, **and** if auto-purge is enabled ignores states older than `utcnow - purge_keep_days`; no replacements or gap filling within HA-covered time.
- Both preview and Recorder-thread commit read live settings. The commit re-evaluates the retention cutoff with its own current UTC time.
- With auto-purge disabled, old states remain eligible regardless of `purge_keep_days`.
- UI translations: English (default) and German.
- HACS repository structure, tests and CI; no release or tag.

## Architecture

- `csv_import.py`: CSV format, timestamp and DST parsing, ordering and duplicate detection.
- `ccu_historian.py`: read-only datapoint listing, name/parameter searching, raw series fetching in bounded windows and value/time validation.
- `merge_policy.py`: shared preview/write filtering for time-range overlaps and Recorder auto-purge retention boundaries.
- `validation.py`: general domain-aware state validation for binary, numeric, enumerated and text-based entities; `light` and `switch` on/off transitions are supported.
- `config_flow.py`: user setup + options flow, FileSelector and confirmation UI.
- `recorder_import.py`: RecorderTask in the Recorder queue, with cutoff re-check in the transaction.
- `translations/`: en/de translations, mirrored from `strings.json`.

## Acceptance criteria before first release

- [ ] Run against a live Home Assistant SQLite Recorder, including a WAL-backed database.
- [ ] Confirm a normal CSV produces visible full-resolution History points before existing data.
- [ ] Live-test `light`/`switch` Boolean state backfill and confirm that historical actions are not replayed as events.
- [ ] Confirm `event` and `button` entity limitations and whether LightAway queries history or live events.
- [ ] Verify the CCU-Historian JSON-RPC API with a live instance, including keyword-protected endpoints, names with umlauts, and the `getDataPoint` list response.
- [ ] Confirm partially overlapping CSV and Historian ranges ignore only their Recorder-covered suffix; no existing rows are changed.
- [ ] Confirm auto_purge enabled/disabled and multiple purge_keep_days values against actual Recorder configuration, especially import near the nightly purge boundary.
- [ ] Confirm CSV preview counts for expired rows, and CCU-Historian fetches start at the retention cutoff when enabled.
- [ ] Confirm current entity state, device registry and attributes are untouched.
- [ ] Reject overlaps, duplicate timestamps, out-of-range values, DST ambiguity, invalid numeric/binary states, unexpected CSV encodings and oversized uploads.
- [ ] Test transaction rollback on injected SQL errors and stale preview cutoffs.
- [ ] Test repeated imports and concurrent Recorder events; observe queue latency under realistic load.
- [ ] Evaluate historic attribute semantics, `old_state_id` and `last_changed_ts` continuity.
- [ ] Test after HA updates, run Hassfest/HACS checks and confirm translated strings on both language settings.
- [ ] Confirm HACS custom repository installation from main before tagging a release.

## Out of scope (initial version)

Recurring imports, merging gaps, automatic unit conversion, textual sensors, non-SQLite databases, import undo, and automatic long-term statistics generation.

## CCU-Historian connection diagnostics

The JSON-RPC `getDataPoint` request without parameters lists all archived datapoints. The response may be large; read the full HTTP stream in bounded chunks rather than using one `StreamReader.read(n)`. Surface redacted, actionable error detail in the wizard and Home Assistant logs. Tests must cover chunked responses, oversized payloads and HTTP errors. Check that the exact API works from the Home Assistant host or container, not only a browser on a separate computer.
