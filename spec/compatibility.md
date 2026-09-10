# Compatibility

## 21. Backward compatibility

Adopted 2026-08-23, with v0.6.0 as the current shipped release. This reverses the earlier
no-backward-compat rule: from v0.6.0 on, an upgrade must never strand user data. This section
is the single home for the promise (§21.1), the strategy (§21.2), what is deliberately out of
scope (§21.3), and the decision log (§21.4). Every change to a stored shape lands an entry in
the log, so the history of compatibility decisions can be revisited in one place.

### 21.1 The promise

On-disk user data written by any released version >= v0.6.0 loads and works in every newer
version. Covered: everything the app persists under the §5 roots that the user would lose by
deletion - automations (manifest, spec, notes, versions, step scripts),
triggers, executions, agents, secrets metadata (their Keychain entries included), and
settings. Derived stores are exempt where the spec already defines a rebuild: `executions.db`
keeps its `SCHEMA_VERSION` drop-and-rebuild (§17), and caches, markers, and step
environments (§6.2 self-heal) may be regenerated.

The promise covers released shapes only: what a tagged release actually wrote. Hand-edited
files, corrupt data, and shapes no release ever shipped stay under the §5 lenient-load
backstop (skip with a warning, never fatal, never healed), not under this promise.

### 21.2 Strategy: migrate on load

When a stored shape changes, the reader keeps accepting the old shape and upgrades it to the
current in-memory model at load; the next save writes only the current shape. Rules:

- One migration per shape change, written together with the change, spec-first like
  everything else: the shape change and its migration are specified here before code starts.
- Migrations live at the read seam - the loader for that file kind - never scattered through
  call sites. The rest of the code sees only the current shape.
- No permanent field aliases and no dual-write: writers emit exactly the current shape.
- No stored schema-version machinery for the YAML stores. An old shape is recognized
  structurally (absent field, old field name, old value form). If a change ever cannot be
  recognized structurally, that change introduces whatever marker it needs and records the
  reasoning in the §21.4 log.
- Every migration ships a fixture of the real old on-disk shape plus a test proving the
  fixture loads and re-saves as the current shape.
- Migrations are kept until the log retires them; a retirement (for example "shapes older
  than two years dropped") is itself a §21.4 entry.

Interaction with existing rules:

- §5 lenient load stays the backstop beneath this policy: data no released version wrote
  still skips with a warning, never crashes, never heals. Migrate-on-load takes precedence
  for shapes a released version >= v0.6.0 actually wrote - those must load, not skip.
- The §2 naming policy still forbids aliases on served surfaces (API fields, routes, CLI
  flags change both ends in one commit). What changes: a rename or reshape of a *stored*
  field now requires a load migration here, instead of relying on lenient load to drop the
  old field.

### 21.3 Out of scope (recorded so they can be revisited)

- **Transfer archives (§5.1):** no promise yet that an export written by an older version
  imports into a newer app. Exercised 2026-08-24: archive `format_version` 2 (numeric
  refs, match-or-flag import) replaced format 1 as a clean break - a format-1 archive is
  rejected with re-export guidance, no migration path.
- **API/CLI version skew:** no promise for an older CLI or app shell against a newer
  backend. §3 packaging ships shell and backend at one locked version, and the CLI command
  surface keeps its no-alias rule (§20).
- **Forward compatibility:** data written by a newer version opened in an older app stays
  under §5 lenient load only.

### 21.4 Decision log

Newest first. One entry per compatibility decision: what changed, the migration, the first
version that writes the new shape, and the oldest shape still read.

- **2026-09-09 - `interval` trigger kind added to automation.yaml.** The §4.3 trigger
  list gains a new kind, `interval` (`every`: an ISO-8601 duration in the §4.3 canonical
  form, beside the cron-style `source`, `runIfMissed`, and `enabledAt`). Additive: no
  existing key changes shape, and data written before this date holds no such entry, so
  nothing migrates and every old shape loads exactly as before; recognition is structural
  (the `kind` value). Loading stays §5 lenient - an interval whose `every` is unparsable
  or out of range, or that lacks `source`, drops with the malformed-trigger warning like
  any other bad entry. Downgrade note (outside the forward-only §21 promise, recorded for
  diagnosis): a release before this one reads an interval as an unknown kind and drops it
  at load with the §5 warning; the file is untouched until that old build next saves the
  automation's trigger list, which rewrites it without the entry. The §5.1 archive and the
  §20 manifest carry the additive `every` entry (archives are outside the promise). First
  version writing the new shape: the next release after 2026-09-09; oldest shape still
  read: v0.6.0. Fixture test:
  `tests/test_storage.py::test_interval_trigger_round_trips_and_malformed_drops`.

- **2026-09-07 - per-automation `instructions.md` retired.** Build instructions became an
  app-shipped document (§8 `build-instructions.md`, overridable by the spec). The §4.1
  `instructions` field, the version folder's `instructions.md`, the §5.1 archive member
  `automation/instructions.md`, and the §20 workdir file are no longer written or read.
  Migration at the read seam: a `versions/vN/instructions.md` written by v0.6.0 through
  v0.11.1 is ignored on load and **left in place** - the version writer never creates,
  rewrites, or unlinks it (before this change the writer unlinked the file when a version
  carried none, so ignoring the file is not enough on its own: the writer must not touch
  it). No data rewrite exists; recognition is structural (the file is simply not read).
  Custom rules a user wrote there are not carried into the spec automatically - the app
  cannot tell a custom rule from an old seeded default - so the user re-states them as
  spec build rules (§8); the file stays on disk for that. Import ignores an older
  archive's `automation/instructions.md`; the CLI ignores a workdir's. A `current.instructions`
  key sent by an older client is ignored by `POST /drafts`, `PUT /draft`, `POST /automations`,
  and `POST /automations/{id}/versions`. First version writing the new shape: the next
  release after 2026-09-07; oldest shape still read: v0.6.0 (file present). Fixture test:
  `tests/test_storage.py::test_version_folder_with_legacy_instructions_loads_and_keeps_the_file`.
- **2026-09-05 - version `params` narrowed to definitions only (read-side migration).** The
  2026-09-01 save-side fix (`strip_param_values`) stopped new versions from storing resolved
  value keys (`value`/`on`/`lines`/`rows`) inside `versions/vN/automation.yaml` `params`,
  but versions written by v0.6.0–v0.8.3 keep the polluted shape on disk and served it over
  §19 for old versions. Migration: `_load_version_folder` strips the value keys at the read
  seam; nothing is rewritten on disk until that version's folder is next written (never, for
  a frozen old version — the strip is repeatable). First version writing the narrow shape:
  v0.9.0; oldest shape still read: v0.6.0. Fixture test:
  `tests/test_storage.py::test_version_params_with_value_keys_load_stripped`.

- **2026-09-05 - `agent_pgids` added to `execution.yaml` (recorded after the fact).** The
  §4.5 in-flight agent-call groups, written sparse (absent when empty) since v0.9.0 for the
  §3 orphan recovery. Additive: an absent key reads as `[]`. First version writing it:
  v0.9.0; oldest shape still read: v0.6.0 (key absent). Fixture test:
  `tests/test_storage.py::test_exec_yaml_without_agent_pgids_loads`.

- **2026-09-03 - `steps_fingerprint` added to the draft `test.yaml` summary.** The §11
  last-test summary gains one optional key holding the opaque steps fingerprint the renderer
  sent with `POST /tests` (§19 `stepsFingerprint`), written only when the client sent one.
  Additive: an absent key is the old shape and reads as `stepsFingerprint: null` on the draft
  payload, which the §11 TEST card treats as "unknown — not stale" (today's behavior). No
  data rewrite; recognition is structural (absent key). First version writing the new
  shape: the next release after 2026-09-03; oldest shape still read: v0.6.0 (key absent).
  Fixture test: `tests/test_storage.py::test_draft_test_summary_without_fingerprint_loads`.

- **2026-08-30 - `runIfMissed` added to cron/time triggers in automation.yaml.** A §4.3
  cron or one-shot trigger gains one optional key, `runIfMissed`, written only when false
  (the user opted the trigger out of the §6 wake catch-up). Additive: an absent key is the
  old shape and reads as true - today's behavior, unchanged - so no data rewrite exists;
  recognition is structural (absent key), the same pattern as `timezone`. Loading stays §5
  lenient (a non-boolean value on disk drops the trigger like any malformed entry). The
  §5.1 archive and the §20 manifest carry the same additive `run_if_missed: false` key
  (archives are outside the §21 promise; noted for completeness). First version writing
  the new shape: the next release after 2026-08-30; oldest shape still read: v0.6.0 (key
  absent). Fixture test: `tests/test_storage.py::test_trigger_without_run_if_missed_loads_true`.

- **2026-08-26 - `eventDurationsMs` added to the §4.4 activity chat entry.** The settled
  activity entry in `chat.jsonl` gains one optional key: a per-`text`-line duration array
  (parallel by index, `null` where no stamp bounds the line), derived by the editor from
  the §8 stage-timing stamps at settle (§11 per-step durations). Additive: an absent key
  is the old shape and renders without duration stamps - no data rewrite exists;
  recognition is structural (absent key), the same pattern as the entry's
  `outcome`/`icon` fields. Loading stays §5 lenient for malformed entries. (Same-day
  amendment, pre-release: an initial cut also stored a `durationMs` stage total; the
  title stamp was dropped from the design before any release wrote it, so the key was
  removed rather than migrated.) First version writing the new shape: the next release
  after 2026-08-26; oldest shape still read: v0.6.0 (key absent). Fixture test:
  `tests/test_storage.py::test_activity_chat_entry_without_durations_round_trips`.
- **2026-09-09 - `executions.db` secondary indexes dropped (schema 8 → 10).** The three
  `CREATE INDEX` statements (page / automation / status) were never queried — §5 loads the
  whole table once and filters in memory — and each one taxed every header upsert on the
  execution hot path. The DB is an index over the yaml truth, so the migration is the
  existing schema-version drop-and-rebuild: a DB at `user_version` 8 (or the unreleased 9)
  is dropped at open and the startup reconcile re-seeds it from `execution.yaml`. First
  version writing the new shape: the next release after 2026-09-09; oldest shape still
  read: v0.6.0 (any earlier `user_version` rebuilds the same way). Fixture test:
  `tests/test_storage.py::test_execdb_schema_8_with_indexes_rebuilds`.
- **2026-08-24 - `unresolved_references` added to automation.yaml.** The §5.1
  match-or-flag import stores the archive references it could not match as a top-level
  `unresolved_references` map (`{id: {kind, name, description}}`, §4.1) on the imported
  automation - written only by import, pruned by save-new-version and trigger replaces,
  kept by restores. Additive: an absent key is the old shape and loads as an empty map, so
  no data rewrite exists; recognition is structural (absent key). Loading stays §5
  lenient for malformed entries. First version writing the new shape: the next release
  after 2026-08-24; oldest shape still read: v0.6.0 (key absent). Fixture test:
  `tests/test_storage.py::test_automation_yaml_without_unresolved_references_loads`.
- **2026-08-23 - mac updater migrated to electron-updater; 0.6.0 update bridge.** Not a
  stored-data shape, but a compatibility promise to a released version, so it is logged
  here. v0.6.1 replaces the mac in-app updater (Squirrel JSON `feed.json` + on-device
  DMG-to-zip repack) with electron-updater's `MacUpdater` reading
  `release/darwin-<arch>/latest-mac.yml` (§3). Bridge: the v0.6.1 release leg rewrites the
  legacy `feed.json` one last time to point at the v0.6.1 DMG, then the file is frozen
  forever and the v0.6.1 DMG release asset is never deleted - an installed 0.6.0 updates
  0.6.0 → 0.6.1 through the old feed, then rides electron-updater from there. Guarded by
  the §15 drift guards (frozen feed never rewritten past 0.6.1, still a live `.dmg` release
  URL). First version writing the new shape: v0.6.1 (`latest-mac.yml`); oldest client still
  served: v0.6.0 (via the frozen bridge). Pre-0.6.0 installs stay orphaned (the §3 clean
  break, unchanged).
- **2026-08-23 - policy adopted.** Baseline v0.6.0; scope on-disk user data (§21.1);
  strategy migrate-on-load (§21.2); transfer archives, API/CLI skew, and forward
  compatibility out of scope (§21.3). No migrations exist yet - the formats v0.6.0 writes
  are the baseline.
- **(pre-policy, stands) 0.3.4/0.3.5 materialized `cliEnabled: false`** for users who never
  touched the toggle: accepted with no migration when the default flipped to true (§4.9).
  Revisit only if support burden appears.
