# Development plan

## Scope

- A single Home Assistant config entry enables a user-invoked CSV import options flow.
- User selects an existing entity and uploads a two-column file.
- Only numeric sensors and binary sensors with existing SQLite Recorder history are supported.
- Dry-run validation and preview precede explicit confirmation.
- The destination's current Home Assistant state is never changed.
- Imports prepend strictly before the earliest recorded state; no overlaps, replacements or gap filling.
- UI translations: English (default) and German.
- HACS repository structure, tests and CI; no release or tag.

## Architecture

- `csv_import.py`: CSV format, timestamp and DST parsing, ordering and duplicate detection.
- `validation.py`: explicit entity/domain and value type checks.
- `config_flow.py`: user setup + options flow, FileSelector and confirmation UI.
- `recorder_import.py`: RecorderTask in the Recorder queue, with cutoff re-check in the transaction.
- `translations/`: en/de translations, mirrored from `strings.json`.

## Acceptance criteria before first release

- [ ] Run against a live Home Assistant SQLite Recorder, including a WAL-backed database.
- [ ] Confirm a normal CSV produces visible full-resolution History points before existing data.
- [ ] Confirm current entity state, device registry and attributes are untouched.
- [ ] Reject overlaps, duplicate timestamps, out-of-range values, DST ambiguity, invalid numeric/binary states, unexpected CSV encodings and oversized uploads.
- [ ] Test transaction rollback on injected SQL errors and stale preview cutoffs.
- [ ] Test repeated imports and concurrent Recorder events; observe queue latency under realistic load.
- [ ] Evaluate historic attribute semantics, `old_state_id` and `last_changed_ts` continuity.
- [ ] Test after HA updates, run Hassfest/HACS checks and confirm translated strings on both language settings.
- [ ] Confirm HACS custom repository installation from main before tagging a release.

## Out of scope (initial version)

External CCU-Historian API access, recurring imports, merging gaps, automatic unit conversion, textual sensors, non-SQLite databases, import undo, and automatic long-term statistics generation.
