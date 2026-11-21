# Safety and database integrity

MoreRecords writes private Home Assistant Recorder tables. Use only after a **full Home Assistant backup** has been created and its restore procedure is understood.

## Preconditions

1. Check the effective Recorder retention configuration. With `auto_purge: true`, MoreRecords only imports points within the active `purge_keep_days` window (default: 10 days). With `auto_purge: false`, old data remain eligible; no setting changes are required to import.
2. Confirm there is enough disk space for the expanded SQLite database.
3. Test with a disposable Home Assistant instance and a copied database before enabling on a production system.
4. Import only values with the same unit and state semantics as the destination entity. On/off states for `light` and `switch` are valid; physical `PRESS_SHORT`/`PRESS_LONG` event values are **not** equivalent to `on`/`off`.
5. Preview the overlap report: the importer skips source measurements at or after the oldest destination Recorder state. No existing HA rows are overwritten.

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
