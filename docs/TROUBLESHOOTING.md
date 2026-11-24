# Troubleshooting

## Integration not visible after installation

Confirm the folder is `<config>/custom_components/more_records/`, that `manifest.json` exists, and that Home Assistant was restarted. In HACS, confirm it was added as an **Integration**, not another repository category. Check the Home Assistant logs for `custom_components.more_records`.

## UI shows raw keys such as `foo_bar_foo`, `gap_mode`, or English labels in German

1. Confirm you installed the intended release and restarted Home Assistant after the update.
2. Verify both `translations/en.json` and `translations/de.json` were installed along with `strings.json`.
3. Check the UI user's selected Home Assistant language, then reload the page.
4. Verify the integration has no stale prior installation directory and reinstall if necessary.

Static flow text and selector choices use Home Assistant translations. Dynamic preview values follow the **instance language**. Automated tests verify translation key coverage and runtime label mapping, but **both live frontend languages must be manually verified before the first stable release**.

## No importable source rows

Check the displayed source range, Recorder retention cutoff, exact-timestamp conflicts, and whether source timestamps are after the newest HA Recorder row. With automatic purge, expired rows are skipped. Empty imports cannot recover already purged state transitions.

## CCU-Historian cannot connect

Use the URL and port accessible from **Home Assistant's own host/container**, not merely from a separate browser. The integration queries `/query/jsonrpc.gy`. Check the CCU-Historian service status, firewall, optional API key, and TLS/HTTP settings. HTTP 401/403 typically indicate authorization problems; HTTP 404 often means the endpoint or port is wrong. Review the wizard's connection diagnostic and Home Assistant logs. Never post an unredacted API key in a public issue.

## State validation errors

For binary domains such as `light` and `switch`, the source must be recognizable as `on`/`off` (or the supported Boolean equivalents). Numeric values must be finite, and selectable states must match the current entity options. The integration does not convert units or physical button press events into switch state transitions. Timezone-less DST-ambiguous timestamps require an explicit UTC offset.

## Export link expired or download blocked

Generate a fresh export and open its link only once within 10 minutes. A second use is intentionally rejected. Sign-in may be required; the export is not published under `/local`. Choose a shorter range when the 100,000-row limit is exceeded.

## Import completed but data do not appear in history

Check the exact entity ID, time window and Recorder retention settings. A native Home Assistant History graph may aggregate or omit repeated samples; raw export can help check the exact stored timestamps. MoreRecords does **not** replay historic events, trigger automations, or rebuild statistics. Restarting an integration does not reverse Recorder purging.

## Reporting a problem

Include your HA Core version, MoreRecords version, the selected operation, a redacted preview, and relevant `custom_components.more_records` log lines. Do not share personal device activity, complete databases, credentials, access tokens, or unredacted network URLs. See the [development notes](DEVELOPMENT.md).
