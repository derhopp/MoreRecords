# First stable release readiness checklist

**Status: preparation only.** The current tested release remains **0.1.0b5**; this checklist does not authorize a stable GitHub tag or a `manifest.json` version change.

## Continuous integration

- [x] Unit tests run on GitHub Actions with Python 3.13.
- [x] HACS checks and Home Assistant Hassfest are configured in CI.
- [x] English and German translation catalogs contain matching keys and placeholders.
- [x] New regression tests check all known configuration/option steps, selector labels, and localized runtime preview values.
- [ ] Add meaningful Home Assistant runtime tests that exercise a config flow and options flow **in both German and English** with the frontend.
- [ ] Verify the exact supported HA Core versions for private Recorder models and task queue behavior.

## Recorder data integrity (requires live Home Assistant)

- [ ] Run a full SQLite backup/restore test, including WAL mode.
- [ ] Test overlap, exact-timestamp collisions, a stale preview, transaction rollback and predecessor link integrity with the real Recorder writer.
- [ ] Test the three gap policies and their synthetic rows against actual History queries.
- [ ] Test daily 00:00 supports, the 00:00–06:00 suppression window, DST changes and the first support after auto-purge.
- [ ] Confirm imported data do not change live entity state, trigger automations, or rewrite long-term statistics.
- [ ] Confirm purge behavior with `auto_purge` enabled and disabled, including at cutoff boundaries.

## User experience and languages

- [ ] Click through **every** configuration and options screen in a German Home Assistant frontend.
- [ ] Repeat in an English Home Assistant frontend; test both instance language and individual user language when different.
- [ ] Check for raw snake_case keys, braces, unresolved `description_placeholders`, and untranslated menu labels.
- [ ] Verify a real CCU-Historian connection and a complete query with and without an API key.
- [ ] Test a CSV import and real authenticated one-time CSV download.
- [ ] Verify UX on mobile and desktop; ensure diagnostics do not expose credentials.

## Documentation and packaging

- [x] README structured for an HA custom integration: overview, features, requirements, installation, configuration, import/export, language, limits, troubleshooting, support and license.
- [x] Dedicated installation, configuration and troubleshooting guides.
- [x] Security limitations and native Recorder API risk clearly identified.
- [ ] Confirm screenshots or walkthroughs match the target Home Assistant UI version.
- [ ] Confirm HACS install/update from a tagged release and a clean manual install.
- [ ] Decide supported HA Core versions, migrations, rollback and version support policy.
- [ ] Publish `CHANGELOG` entry and actual verified stable release notes for `1.0.0` or an explicitly selected stable version.

## Publish only after the open checks pass

1. Set `manifest.json` to the chosen final stable version **in a dedicated release PR**.
2. Update `hacs.json` minimum HA version if compatibility tests require it.
3. Obtain green CI, HACS and Hassfest results on the exact release commit.
4. Tag the tested commit, publish a **non-prerelease** GitHub Release with real change notes.
5. Verify the download/install path, then repeat a post-install import/export smoke test.

**Do not interpret passing static localization tests as proof of correct Home Assistant frontend rendering.** The stable-release gate remains open until the real HA UI and Recorder tests have passed.
