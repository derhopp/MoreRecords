# Safety and database integrity

MoreRecords writes private Home Assistant Recorder tables. Use only after a **full Home Assistant backup** has been created and its restore procedure is understood.

## Preconditions

1. Check the effective Recorder retention configuration. With `auto_purge: true`, MoreRecords only imports points within the active `purge_keep_days` window (default: 10 days). With `auto_purge: false`, old data remain eligible; no setting changes are required to import.
2. Confirm there is enough disk space for the expanded SQLite database.
3. Test with a disposable Home Assistant instance and a copied database before enabling on a production system.
4. Import only values with the same unit and state semantics as the destination entity. On/off states for `light` and `switch` are valid; physical `PRESS_SHORT`/`PRESS_LONG` event values are **not** equivalent to `on`/`off`.
5. Preview the exact-timestamp merge report: eligible source observations can fill missing timestamps even within the original Recorder interval. Existing Recorder rows always take precedence at the same timestamp; no original HA rows are overwritten.

## Recorder CSV exports

CSV exports are read-only and delivered via a single-use ten-minute signed URL through Home Assistant. A downloaded CSV may reveal private activity history: treat it as sensitive. No `/local` file is written. The exporter cannot reconstruct old Recorder rows already deleted by purging, and it caps one file at 100,000 rows. Export is based on internal Recorder SQLAlchemy models and needs live-version verification.

## External CCU-Historian connections

The optional URL may point to a private network service; only administrators should use the integration. Do not connect it to untrusted servers. API keys are stored as Home Assistant integration options and may be sent as URL query parameters to the configured server. Use HTTPS whenever supported. The search and data download are read-only and bounded by request and row limits.

## Internal implementation risk

The writer uses `States`, `StatesMeta`, a custom `RecorderTask`, and SQLAlchemy against the live SQLite Recorder. These are **not public extension APIs**; Home Assistant updates can change their behavior. A confirmed import is committed atomically, but an HA backup is still essential. Failures before commit should roll back; recovery of an already committed import requires restoration of the backup.

MoreRecords reads the running Recorder's `auto_purge` and `keep_days` settings. When automatic purging is active, only points at or after the current UTC retention boundary are written, and the cutoff is checked again in the Recorder-thread transaction. These points will **still age out** as Home Assistant's rolling retention window advances (possibly at the next nightly purge). This filtering avoids knowingly importing already expired records, but does not guarantee any minimum remaining lifetime. Disabling `auto_purge` preserves raw history, but also keeps unrelated data indefinitely. A manual purge may still delete imported observations.

## Historical switching versus historical events

MoreRecords inserts historical **Recorder state records**; it never emits historical `state_changed` events or calls any switch/light service. Consumers that read Recorder history can see inserted `on`/`off` transitions. Consumers that only listen for live events will not. Button and event entities store timestamp-like state values, but their event-specific attributes and payloads are not rebuilt from a two-column CSV or CCU-Historian raw series. Treat historic physical button activations separately from actual light/relay states. Verify the data consumption model of any presence simulation integration (e.g., LightAway).

## Important semantic limitations

State attributes for imported rows are shared with the earliest stored existing state; historical unit changes cannot be reconstructed. Long-term statistics are *not* recomputed from inserted raw rows. Existing energy statistics or totals may therefore not match the imported raw history. Importing numeric totals without their required statistical context needs separate planning.

## Support diagnostics

Record the Home Assistant Core version, integration version, entity ID, CSV row count, and exact error messages. Never attach your entire Recorder database or unredacted CSV containing private device information to a public GitHub issue.

## CCU-Historian timestamp collisions

When the CCU-Historian API returns multiple values at the same timestamp, the direct importer preserves the final source observation and drops earlier values at that exact instant. The preview and logs report collision counts. This can lose instantaneous intermediate events, but avoids fabricated timestamps; review conflicts if exact switching sequence matters.

## CCU-Historian completeness and locally attested coverage

An extra opt-in confirmation is required before MoreRecords records source-history coverage. Confirmation must mean that the selected CCU-Historian datapoint continuously captured **all changes** throughout the requested interval, not merely that the returned rows have valid syntax. The lower bound is the first actual source reading, never the query start if that state was unknown. MoreRecords compares all source rows in the attested interval with persisted Recorder rows, refusing a certificate on any mismatch. Empty and CSV-derived histories cannot be attested. The entity-scoped `.storage/more_records_verified_history_v1` contains only entity IDs, source labels, evidence type and bounds; no credentials or state values.

No artificial HA `RecorderRuns` are inserted. The metadata does not independently prove that a Recorder has never been purged or modified later; verify the data again after repairs, purges or upgrades. Do not affirm source completeness without reliable Historian retention and source integrity. Backup and restore the HA configuration as well as the Recorder database.

## Exact timestamp merges and user-selected gap states

The current integration can add missing source timestamps inside the existing HA Recorder interval. Original stored rows, attributes, timestamps and values are authoritative at exact collisions. The user's explicit gap mode controls the interval from the final imported prefix observation until the first original Recorder state: hold last source value, adopt first Recorder state, or mark unavailable. The latter two may write an artificial state 1 microsecond after the imported sample. These are modeling choices, **not measured events**, and must not be reported as verified CCU-Historian continuity. An import preview signature is checked again during the actual Recorder write; changes require a fresh preview and confirmation. Live database integration tests are still required before production use.
