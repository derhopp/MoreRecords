# Installation and first-time setup

MoreRecords is a **custom Home Assistant integration**. It is distributed through a HACS custom repository or manual installation; it is **not** included in Home Assistant Core.

## Requirements

- Home Assistant Core **2026.10.0 or newer** according to `hacs.json`. Compatibility with actual Recorder internals needs verification on the precise installed version.
- Home Assistant's Recorder configured to use **SQLite**. Other Recorder databases are not supported for writing.
- An **existing entity with Recorder history**, such as `light.kitchen`, `switch.guest_room`, `binary_sensor.door`, or `sensor.temperature`.
- A complete, restorable Home Assistant backup before writing history. Take special care with the SQLite database and its WAL/SHM files.
- For direct imports, optional network access from Home Assistant to a **CCU-Historian** server.

## Install with HACS

1. Open **HACS → Custom repositories**.
2. Add `https://github.com/derhopp/MoreRecords` and select **Integration**.
3. Choose the desired release. Until a stable release is officially published, enable the **prerelease** option and install the current beta, `0.1.0b5`.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration** and find **MoreRecords**.
6. Add the integration once; no hardware entities are created.
7. Open the installed integration's **Configure** action to start an **Import** or **Export CSV** operation.

## Manual installation

1. Copy the `custom_components/more_records/` directory to `<config>/custom_components/more_records/`.
2. Restart Home Assistant and add MoreRecords under **Settings → Devices & services**.
3. To update, replace the integration directory, restart, and check the manifest version in the integration's files.

## Language

The configuration wizard, options flow, selector choices, and error messages have English and German translations. Home Assistant supplies the translated wizard text in the selected UI language. Dynamic preview values are localized using the **Home Assistant instance language** (`hass.config.language`), with English as fallback; where an individual user selects a different frontend language from the instance language, dynamic status values may reflect the instance language. This difference must be assessed in a real frontend before calling localization complete for stable release.

## Upgrade and removal

Upgrading does not rewrite old Recorder states automatically. Existing imported observations remain until Recorder purges them. Removing the integration **does not undo** already imported rows, nor automatically clear previous coverage-attestation metadata from `.storage`. See [Safety](SAFETY.md) before upgrading or uninstalling.
