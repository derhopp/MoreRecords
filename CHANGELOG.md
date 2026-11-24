# Changelog

All notable changes to MoreRecords are described here. Until a stable version is released, release notes may also contain experimental API and Recorder compatibility warnings.

## Unreleased — stable-release preparation

- Localized **Import / Export CSV** operation and source dropdown values through Home Assistant selector translation keys.
- Localized dynamic import preview values including gap policy, daily support status and coverage notices for English and German HA instances.
- Added automated parity checks for English/German translation keys, placeholders, flow fields and runtime selector labels.
- Reorganized Home Assistant-style user documentation into installation, configuration, troubleshooting, and stable-release readiness guides.
- Kept the manifest version at **0.1.0b5**. **No stable release published.**

## [0.1.0b5] — prerelease (2026-11-23)

- Added explicitly opted-in local-midnight support rows for sparse historical imports, using only real source observations within the user-selected import window.
- Skip midnight supports whenever a real source/Recorder observation already exists between 00:00 and 06:00.
- Permit a genuine source observation before the purge cutoff to seed the first eligible support point; never insert expired support rows.
- Added tests for timestamps, DST, preview signatures, gap rules and conflicting source values.

## 0.1.0b4 — withdrawn (2026-11-23)

- The rolling-retention-anchor experiment was withdrawn and its release tag removed. It is **not included** in current beta 5.

## [0.1.0b3] — prerelease (2026-11-23)

- Permitted redundant Recorder samples repeating a prior value during CCU-Historian source-completeness verification.

## [0.1.0b2] — prerelease (2026-11-22)

- Added exact-timestamp merging with Recorder-authoritative collisions.
- Added explicit prefix-gap state policies and more extensive import previews.

## [0.1.0b1] — prerelease (2026-11-22)

- Initial experimental CSV / CCU-Historian Recorder import and CSV export capabilities.

[0.1.0b5]: https://github.com/derhopp/MoreRecords/releases/tag/0.1.0b5
[0.1.0b3]: https://github.com/derhopp/MoreRecords/releases/tag/0.1.0b3
[0.1.0b2]: https://github.com/derhopp/MoreRecords/releases/tag/0.1.0b2
[0.1.0b1]: https://github.com/derhopp/MoreRecords/releases/tag/0.1.0b1
