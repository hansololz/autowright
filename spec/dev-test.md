# Autowright SPEC — Dev/test knobs, seed data, commands

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 15. Dev/test knobs

**Dev/release parity rule:** dev and release share the SAME code paths — there are no mock modes,
no alternate backends, no dev-only branches in app code. The only knobs that exist are pure
configuration (they relocate or re-tune the same behavior, never select different behavior).
Every knob defaults to the release value and is developer opt-in; the single knob dev.sh sets
itself is `AUTOWRIGHT_RENDERER_URL` (below — same renderer source, served with HMR instead of
pre-bundled). Dev sessions use the real app-support dir, real Keychain, real agent CLIs, random
port, request logging via the §4.9 developerMode setting (§5), and the real launchd service (§18
dev.sh).

Frontend state (localStorage/URL — production mechanisms, not dev branches): `ad-onboarded`
(persisted onboarding completion; clearing it replays onboarding), `ad-cli-installed` (§3
first-run CLI install settled; clearing it re-arms the one-shot at next boot), `#menubar` URL
hash (selects
the menu-bar surface — how the tray panel window loads). The renderer discovers the backend only
via `backend.json` through the Electron preload bridge; there is no browser-dev URL-param
fallback.

Backend env knobs (configuration only):

- `AUTOWRIGHT_HOME` — overrides the app-support root (isolated dev/test homes); logs move to
  `<home>/logs/` (§5), and Electron's Chromium profile follows to `<home>/electron/` — an
  isolated home isolates the renderer's localStorage/cookies too, never the real profile.
- `AUTOWRIGHT_PORT` — fixed port instead of a random free one.
- `AUTOWRIGHT_SHIM` — overrides the §3 CLI shim location
  (default `~/.local/bin/autowright`) and skips the
  login-PATH probe (a forced location counts as on-PATH); honored by both `service.py` and
  the Electron shell's `cli-status`/`cli-install`, so tests never touch the real locations.
- `AUTOWRIGHT_OLLAMA_URL` — Ollama HTTP endpoint override (default `http://localhost:11434`).
- `AUTOWRIGHT_STEP_TIMEOUT` — the **default** per-step timeout in seconds (default 900); a
  step's own `timeout`/`no_timeout` (§4.1, §6) always wins over it.
- `AUTOWRIGHT_AGENT_TIMEOUT_S` — per-invocation agent-call **idle window** in seconds (default
  300): the call is killed after this long with no observed progress; every stdout line,
  parsed handler event, and scratch-document change resets the window (§8)
- `AUTOWRIGHT_AGENT_HARD_CAP_S` — per-invocation agent-call **total wall-clock cap** in seconds
  (default 1800); ends a call that streams forever (§8)
  for every §8 harness call (drafting, chat, build diagnosis).
  Configuration only, never a different code path; read per call, so a running backend picks
  up changes. Local Ollama models on big builds are the typical reason to raise it.
- `AUTOWRIGHT_REPAIR_ROUNDS` — §8 maximum automatic repair rounds per drafting call
  (default 1, clamped 0–5; 0 = no repair, an invalid response goes straight to the §8
  build diagnosis). Configuration only, never a different code path; read per call, so a
  running backend picks up changes.
- `AUTOWRIGHT_TICK_S` — scheduler tick period in seconds (default 15). Same loop re-tuned,
  never a different code path; the integration harness sets `1` so live-scheduler tests wait
  seconds instead of sitting out real 15 s ticks.
- `AUTOWRIGHT_LISTEN_TICK_S` — §6 listener-manager reconcile period in seconds (default 3).
  Same rule as `AUTOWRIGHT_TICK_S`: configuration only, never a different code path.
- `AUTOWRIGHT_QUEUE_TTL_S` — §6 firing-queue staleness cutoff in seconds (default 120): an entry
  that reaches the head having waited longer finishes `skipped` instead of executing.
- `AUTOWRIGHT_CHAT_DB` — path of the Messages database the §6 iMessage watcher reads (default
  `~/Library/Messages/chat.db`). Configuration only — tests point it at a fixture db; the
  watcher code path is identical.
- `AUTOWRIGHT_IMSG_MAX_AGE_S` — §6 iMessage backlog fence in seconds (default 120): a
  cursor-passed row whose message timestamp is older than this when observed never fires.
- `AUTOWRIGHT_STEP_RETRY_PAUSE_S` — §7 spacing between consecutive attempts of an
  `infiniteRetries` step in seconds (default 1; finite retries never pause). Configuration
  only — tests set `0` so retry loops run instantly.

Electron env knob (configuration only):

- `AUTOWRIGHT_RENDERER_URL` — when set, Electron `loadURL`s the renderer from this origin (with
  the same `#/app` / `#/menubar` hashes) instead of `loadFile`-ing `app/dist/index.html`. It
  points at a Vite dev server serving the identical `app/src` source — HMR delivery of the same
  code, not a different code path (the preload bridge, `backend.json` discovery, and backend
  are untouched; the backend's open CORS covers the http origin). Set by `dev.sh` (§18);
  release never sets it.

Test doubles live in `tests/` only: a fake `claude` CLI at `tests/bin/claude` (conftest prepends
`tests/bin` to `PATH`, so the real detect/invoke/subprocess path is exercised against it;
like the real CLI, it takes the prompt from its last argv element and falls back to reading
stdin when no positional prompt is given — the §8 per-OS delivery rule exercises the argv
form on POSIX and the stdin form on Windows;
`AUTOWRIGHT_TEST_CLAUDE_SIGNED_OUT=1` makes its `auth`-status invocation exit non-zero, so the
signed-out detection path is testable; `AUTOWRIGHT_TEST_STREAM_DELAY_MS` — milliseconds pacing
its stream-json output, for manual UI checks of live §8 progress; unset → instant, so the
pytest suite stays fast), a fake
`osascript` at `tests/bin/osascript` (records its argv to a file named by
`AUTOWRIGHT_TEST_OSASCRIPT_LOG` and exits 0 — the §6 iMessage sender resolves `osascript`
through PATH, so tests exercise the real send path; exit/stderr overridable via
`AUTOWRIGHT_TEST_OSASCRIPT_FAIL` to simulate the −1743 denial), and conftest
fixtures that monkeypatch `keychain` (in-memory dict) and `notify.post` (no-op).
On Windows the shebang scripts aren't executable, so each fake has a twin beside it that
PATHEXT resolves: `claude.cmd` / `osascript.cmd`, thin batch shims running a Python port of
the same contract (`claude.py` / `osascript.py` — same env knobs, same output, byte-for-byte
observable behavior) on the interpreter conftest publishes as `AUTOWRIGHT_TEST_PYTHON`
(`sys.executable`). The sh fake stays the POSIX implementation, untouched; the pairs must
not drift — each carries a header comment naming its twin as the contract. Removed knobs —
do not reintroduce: `AUTOWRIGHT_MOCK_AGENT`, `AUTOWRIGHT_KEYRING`, `AUTOWRIGHT_NO_NOTIFY`,
`ad-sudo-denied`, `?port=&token=` (the renderer dev server returned as `AUTOWRIGHT_RENDERER_URL`,
above — `VITE_DEV`/`npm run dev:app` themselves stay gone).

**Test suite layout.** Backend unit tests are pytest under `tests/` (run
`python -m pytest tests/` from the repo root; `pytest.ini` runs them parallel via
pytest-xdist `-n auto`). Renderer unit tests are Vitest under
`app/tests/` (run `npm test` in `app/`) — pure logic (label
formatting, store reducers, spec/text round-trips) plus a small happy-dom component tier
(`*.render.test.tsx`, @testing-library/react). It covers flows the e2e tier cannot reach
under its safety rules — the original motivation: installer/set-up card flows (e2e must
never click them), queued/waiting execution rows (timing-hard to stage live), and
grant-checkbox → draft-request payloads — and editor branch behavior uneconomical to stage
live: blocker-entry states and action gating, chat-response application branches, thread
progress-entry stage labels, collapsed-card defaults, and analyze/agent-picker request
payloads. Full
journeys stay e2e; all other component rendering is exercised by the playwright-driven
Electron path, never by DOM unit tests.
Both suites carry guards for the §2 CLI-leaf invariant: a pytest scan asserts no backend
module besides `cli.py` imports `autowright.cli`, and a Vitest guard reads
`app/electron/main.cjs` **plus every `app/electron/platform/*.cjs` module (the union is one
trust surface — a §2 extraction must not move a call out of the guard's sight)** asserting
backend registration runs `-m autowright.service`, that
no child-process call executes the CLI (the string `autowright.cli` may appear only inside
the shim file text — the POSIX `exec "<python>" -m autowright.cli "$@"` line or the §3
Windows `"<python>" -m autowright.cli %*` line, never an `execFile`/`spawn` call), and that
shim writes only ever target the §3 user-local location —
no osascript admin prompt exists (`with administrator privileges` must not appear), and
nothing reads, writes, or deletes at `/usr/local/bin` (creation only through
`cli-install`, deletion only through
`cli-uninstall` and only of marker-carrying files).
The §5 per-OS root table is drift-guarded on both sides: `tests/test_platform.py` pins
`paths.py`'s roots per platform token and `app/tests/platform-roots.test.ts` pins
`electron/platform/roots.cjs` — both against the same §5 table, so the two implementations
can never disagree silently.

**Typecheck coverage.** `tsc --noEmit` runs twice, over two configs, because a single
`include` cannot hold both: `app/tsconfig.json` covers `src` under the strict app settings,
and `app/tsconfig.test.json` extends it to cover everything else that is TypeScript but
never shipped: `tests/`, `e2e/`, the Vite/Vitest config files, and `ds-entry.ts`, adding
only the `node` types those need. Neither the renderer's settings nor its file set change;
the second config exists so untypechecked TypeScript cannot accumulate outside `src`.

**Drift guards.** `tests/test_drift_guards.py` holds the cheap textual guards over facts
that live in more than one hand-maintained file: the app version agrees across `VERSION`,
`backend/pyproject.toml`, `backend/autowright/__init__.py`, and `app/package.json`; the
§6.2 curated-package list agrees across its four homes: `imports_check.ALLOWED_IMPORTS`
(import names), the `backend/pyproject.toml` dependencies (distribution names),
`instructions/framework-instructions.md`, and §6.2 itself, with the import-name ↔
distribution-name mapping written out in the guard; and every `*.ps1` in the §17 script
directories still starts with a UTF-8 BOM (Windows PowerShell 5.1 misreads a BOM-less file
as ANSI and fails to parse the scripts' non-ASCII result lines); and the §17
`docs/CHANGELOG.md` carries a `## v<version> - <date>` entry for the current `VERSION` with its
version headings in strictly descending semver order (newest first, no duplicates). The
changelog guard deliberately checks "an entry exists", not "the top entry matches":
notes for the *next* version are written and committed ahead of running `release.sh`
(the §18 `release-start.sh` step produces exactly that), and a pre-written newer entry
above the current version's is legitimate.

The same file guards the §3 update feeds under `release/` and the §17
`docs/downloads.json` index, which nothing else reads at test time - their only real
consumer is a shipped app fetching them over the network, so a wrong artifact or a
version that ran ahead of the published release stays invisible until an installed copy
tries to update. Five checks, over whichever feeds exist on disk (a feed is absent until
its OS's release leg first runs, and absence is never a failure): every download URL a
feed hands the updater is a `github.com/hansololz/autowright/releases/download/…` URL,
embeds that feed's own version, and carries the extension that OS's update flow can
actually open (`.zip` for the mac electron-updater/Squirrel path, `.exe` for the NSIS
updater, `.AppImage` for the AppImage updater); no feed is *newer* than `VERSION` (it
would name a release that does not exist - the reverse is legitimate and deliberately
unflagged, since each leg rewrites only its own feed); at least one `darwin-<arch>` feed
equals the newest *published* release (`release.sh` cuts the release `VERSION` names and
rewrites the mac feed in one run, so a lagging mac feed means a lost feed write or push -
recover with `release.sh --feed`). Published is decided by the release tag, not by `VERSION` alone:
the last committed `VERSION` (`git show HEAD:VERSION`, falling back to the file outside a
git checkout) counts as published once its `v<version>` tag exists in the local checkout,
and the feed must then equal it; while that tag is absent the release has not been cut
yet - `release-start.sh` bumps `VERSION` and the developer commits it ahead of the
release, and `release.sh` then runs this very suite before the GitHub release exists and
writes the feed only once it is live - so the feed may equal either that `VERSION` or
the previous release, the
newest `v*` tag reachable from `HEAD` (`git describe --tags --abbrev=0 --match 'v*'`).
The working-tree `VERSION` is never compared: between `release-start.sh` and the commit
it legitimately runs ahead of every feed. Until the v0.6.1 release first writes `latest-mac.yml`, the legacy feed
satisfies this check;
each `docs/downloads.json` entry names a release-download URL embedding its own version
and, where that key's feed exists, matches the feed's version - for `win32`/`linux` it
must offer the very URL the feed names, while the `darwin` entries offer the DMG beside
the feed's zip (§3: install and update artifacts differ on mac), same release, same
basename, only the extension apart; and the legacy `darwin-<arch>/feed.json` bridge
feeds (§3) stay internally consistent, name a live `.dmg` release URL, and are never
rewritten past `0.6.1` - the frozen bridge is load-bearing for stranded 0.6.0 installs,
so a rewrite is a bug, not a refresh.

The §9 per-OS **copy table** is drift-guarded on both sides the way the §5 root table is:
`tests/test_platform.py` pins `backend/autowright/paths.py`'s per-OS strings and
`app/tests` pins `app/src/platformCopyTable.ts`, both against the same §9 table - so the
backend-served copy and the renderer's copy can never disagree silently about what a
machine or a secret store is called on a given OS.

**Shift-left order.** Tiers run cheapest-first so failures surface early: Vitest unit
(<1 s) → `tsc --noEmit` (both configs) → pytest unit (seconds) → pytest `-m integration`
(~10 s) → e2e (minutes). `scripts/tests/fast.sh` (§18) runs the three cheap tiers in that
order, failing fast; its typecheck step is the pair of invocations, `tsc --noEmit`
followed by `tsc --noEmit -p tsconfig.test.json`.
`scripts/tests/all.sh` (§18) runs all five tiers in the same order — the
fast gate via `fast.sh`, then integration, then e2e — failing fast at every tier.

**Integration tests** live under `tests/integration/`, marked `integration` and excluded from
the default run (`pytest.ini` at the repo root; run them with `python -m pytest -m
integration`). They boot the real backend (`python -m autowright.main`) as a subprocess and
exercise it over real HTTP/WebSocket connections and via the real CLI as a second subprocess —
the §3 bind-and-listen-before-publish handshake, the execution lifecycle, crash recovery
(SIGKILL → restart → stale-executing repair), live scheduler firing, the §6 iMessage
message-trigger loop over a fixture chat.db (`AUTOWRIGHT_CHAT_DB` + the fake `osascript`
capturing the reply; macOS-only — skipped on platforms whose §2 capabilities compose
`imessage: false`, where the watcher can never connect), and the CLI authoring/execution
surfaces (pull → edit → push round-trip; execution cancel). Isolation is
per-test: a fresh `AUTOWRIGHT_HOME` tmp dir (the app's entire write surface), a random
localhost port per backend (the harness also sets `AUTOWRIGHT_TICK_S=1`, §15), and
localhost-only test doubles in the spirit of the fake `claude`
CLI — a local HTTP server standing in for the web (`fetch_page` pages + robots.txt) and a
local wheel directory standing in for PyPI (pip's `PIP_NO_INDEX`/`PIP_FIND_LINKS`, so
`packages.ensure` runs a real pip install into the home's `site-packages/`). Nothing outside
the tmp home is written and no packet leaves localhost. Deliberately excluded (machine-mutating,
covered at their unit seams): Keychain values, launchd, harness/Ollama installers, real agent
CLIs. The backend subprocess runs real `notify.post`, so a failed-execution test may show one
real macOS notification — accepted, per the no-dev-only-branches rule.

**End-to-end tests** live under `app/e2e/` (run `npm run test:e2e` in `app/` — it builds
first, then runs a second Vitest config, `vitest.e2e.config.ts`, sequentially with long
timeouts and one automatic retry per test: launching a real Electron per test occasionally
dies to a transient helper-process crash outside our code — "Target page, context or browser
has been closed" — and a genuine failure still fails both attempts). The suite runs on
whatever OS hosts it, so assertions on §9 per-OS copy read the renderer's own table
(`platformCopy` from the store-free `platformCopyTable` module, exported through the harness
as `COPY` keyed by the host platform) rather
than hardcoding one OS's strings. Each test launches the real pieces exactly as release does: the backend subprocess
over a tmp `AUTOWRIGHT_HOME` (fake `claude` from `tests/bin` on PATH), then the real Electron
binary via playwright-core `_electron.launch` loading `app/dist` — real preload bridge, real
`backend.json` discovery, real windows on screen. Teardown stops the backend **gracefully**
(SIGTERM, a bounded wait, then SIGKILL only if it has not exited) so the backend's lifespan
cleanup runs and reaps the live step and drafting process groups before the tmp home is
deleted; a hard SIGKILL with no warning is reserved for the crash-simulation restart below,
whose whole point is that the backend dies without cleaning up. A backend that fails to come
up is killed on the spot, so a startup timeout never leaks a python process either.
Scenarios stay high-value journeys —
everything finer-grained belongs to the unit/integration tiers:

- onboarding on an empty home, real agent detection (fake CLI)
- onboarding commit against a backend that already holds a grant-name-clashing agent (a
  partially-landed earlier commit): Use-as-default reuses it per the §10 idempotent-commit
  rule and lands in the app shell — never the already-exists toast that stranded step 2
- list → detail → execute → result on a seeded-via-API home
- the create-flow journey: request → AI draft via the fake CLI through the real §8 chat +
  chained-sync pipeline → Test draft run → Create → execute → execution page
- a new automation always opens on the create empty state: the suggestion headline shows
  and stays on fresh entry, and re-entering after a settled session (send → build →
  Start over → leave) shows the suggestions again — the §4.4 fresh-entry clear, never
  the old session's thread
- adding a config-only agent; adding a placeholder secret, then editing and deleting it;
  adding a cron trigger, seeing its humanized chip, and toggling it off
- the §4.3 catch-up opt-out journey: a cron added with "Catch up if missed" unchecked
  flips the sleep note to "that time is skipped", shows the NO CATCH-UP row badge, and
  stores `runIfMissed: false`; re-checking it through the edit swap clears the badge and
  stores explicit true (§19 serialization)
- an execution whose step writes a result file, rendered in the execution page's result view;
  the read-only "Show workspace in Finder" button is present (never clicked — it would open a
  real Finder window)
- memory snapshots from the detail page — create, list, restore
- an edit-mode draft Test on an existing automation: run Test from the editor, the §11 test
  record succeeds while live memory stays untouched, its View execution opens the execution page;
  the settled test also lands as a quiet system chip in the chat thread; editing the spec
  locks Test until Sync; Discard drops the draft
- failed execution → §7 diagnostics on the execution page → Retry in place → attempt 2
  succeeds → attempts visible, list chip recovers
- Cancel on a live execution; skip-step on a live execution continuing to the next step
- the full edit loop: spec edit → Sync spec (real second AI call) → Save as v2 →
  version history → restore v1 as v3
- the editor chat pane, question then edit: a question-shaped message lands a prose answer
  entry (workflow untouched); a change request rewrites the spec from chat, the thread
  entry's inline Sync now rebuilds the steps, and the draft saves as v2; the same journey
  checks the pane's two §11 visual behaviors happy-dom can't reach — the composer
  auto-grows and shrinks with its content, and the thread scroll stays put while typing
  and re-pins to the newest entry when one lands
- the chat `actions.yaml` fix-and-test chain: one response carrying an answer, a spec
  rewrite, a notes rewrite, and `sync: true` + `test: true` → auto-sync → auto-test →
  the settled-test system chip in the thread
- Fix with AI from a failed execution: the failure seeds the thread as a system entry, the
  canned analyze message sends automatically, and the response's spec rewrite lands in the
  thread
- backend restart under a live UI: the renderer re-reads backend.json, reconnects (§3), and
  keeps working
- parameter editing across the §4.2 kinds on the detail page, values reaching a real execution
- the missing-secret warning flow (placeholder secret only, no Keychain write)
- agent management: switch the default agent, delete an agent → default reassigns
- the menu-bar surface (`#menubar`): rows with status dots, execute from the tray panel
- executions list behavior: §4.5 test records hidden, statuses and click-through correct
- Settings: retention-days edit with clamp, notification setting persisted The §10 install/sign-in machines are
real — e2e must never click "Set up" suggestion cards (they install CLIs onto the machine);
the found-card "Check connection" is read-only and safe. Secret values go to the real macOS
Keychain in every mode, so e2e only ever creates §4.8 placeholder secrets (blank value —
name + description, no Keychain write); value-setting is covered by the unit tier's in-memory
keychain. Trigger math has **one** implementation (backend `triggers.py`; the editors
preview through §19 `POST /triggers/preview`), so there is no cross-language parity fixture
to maintain — the backend pytest suite covers the cron/one-shot cases (DST gap and
fall-back included) directly. Testability knobs (configuration only, release
behavior unchanged): `Scheduler` accepts an injectable `clock` callable (defaults to
`datetime.now`) so tick-loop policies (coalescing, catch-up, one-shot consumption) are
deterministic under test, and the §17 createflow module (`app/src/pages/createflow/model.ts`)
exports its pure helpers
(`specToText`, `textToSpec`, `amendSpec`, `stepSecretNames`, `secretRefsOf`, `instrToMd`,
`mergeDraftTriggers`, `persistChat`, `applyTestValues`, and the seed/serialization helpers
`seedEmpty`, `seedFromPayload`, `seedFromAuto`, `serializeDraft`) and `result.tsx`
exports `SpecMarkdown`/`Markdown` and `ext`/`fileKind` for the Vitest suite.

**Selector policy.** An element an e2e test targets carries a stable `data-testid` (or is
reached by role/label/user-visible text); tests never select by internal CSS class, DOM
order (`.nth()`, xpath ancestor walks, unscoped `.first()` used to pick among same-named
controls), or structural traversal — those re-aim silently when layout, styling, or copy
around them changes. `.first()` stays legal only to collapse duplicates of the *same*
target (a text that legitimately renders twice), never to choose between different
controls. Test ids in the app (all in `app/src`): `nav-rail` (the §9 nav rail — the
harness's `clickNav` measures its width), `agent-card` (§12 agent cards),
`onboard-agent-card` (§10 step-2 found-agent cards; every connected card carries the same
"Use as default →" button, so the id is what scopes an assertion to one agent),
`execution-row` (§9 executions-list rows), `workspace-card` (§7 execution page's WORKSPACE
card — its reveal button shares the RESULT card's per-OS "Show in Finder" label, so the id is
what scopes an assertion to it), `param-row-<name>` (§9.2 parameter rows, one
per param), `spec-edit` / `spec-editor` (§11 SPEC card's Edit button and its edit
textarea), `sync-steps` (§11 Build panel's Sync now / Sync spec button), `test-draft-toggle` (§11 test
panel's Test draft setup-toggle button — its "Test draft" label also appears on the chat
turn-row pill `chat-test-draft`, so bare text can't reach it),
`chat-sync-now` (§11 chat thread rewrite entry's inline Sync now), `chat-turn-actions` /
`chat-test-draft` / `chat-analyze-failure` (§11 chat turn action row and its Test draft /
Analyze failure pills), `chat-thread` (§11
chat thread's scrolling body — the element whose scrollTop the pinning tests measure),
`chat-progress` (§11 thread's transient live-job progress entry — its stage label text
also appears verbatim as settled activity-entry titles, so bare text can't reach it),
`version-menu` (§11 editor version pill). New e2e targets that role/label/text cannot
reach unambiguously get a test id added here. Selectors that assert copy covered by the
§9 per-OS table (machine noun, secret-store name) must resolve the value for the OS the
suite runs on, never a hard-coded single-OS literal: every such assertion reads the
harness's `COPY` export (`platformCopy` for the host platform - the same table the
renderer uses), so the specs that once pinned mac forms (`FOUND ON THIS MAC` in
`app.e2e.ts`, `Save to Keychain` in `agents-secrets.e2e.ts`, and the result-file copy in
`result-files.e2e.ts`) now pass unchanged on Windows and Linux.

## 16. Seed / demo data (tests only)

The shipped app has NO seed path: a fresh install always starts empty (onboarding), and there is
no CLI or API to populate demo data. The seed fixture lives in `tests/seed_data.py` and is
applied only by tests calling `seed(store)` (it refuses to seed when any automations exist).

The fixture ships four demo automations: "Track manga
chapters" (cron `0 8 * * *`, list/toggle/number/text/kv params, result.md markdown table with a READ
column),
"Nightly folder backup" (cron `0 2 * * *`), "Weekly report email" (cron `0 9 * * 1`, failed, uses
`SMTP_PASSWORD`, retry-from-step), "Clean screenshots folder" (cron `0 21 * * 0`). Demo secrets:
`SMTP_PASSWORD`, `VAULT_DRIVE_KEY`. Twelve seed executions cover every terminal status including
skipped (§4.6 queue-entry records that never ran, note "previous execution still in
progress"), cancelled (a user-cancelled execution with a cancelled step and queued
remainder), and interrupted (note "Mac went to sleep"); `executing` is inherently live and
is not seeded. The fixture includes one execution
with a skipped step (execution still `succeeded`) and one failed-then-retried execution whose
failing step carries two attempts.


## 18. Commands

Everything under `scripts/`, `windows-scripts/`, and `linux-scripts/` is developer-only: run
by hand in a terminal, never by an agent.
`.claude/settings.json` enforces this with PreToolUse hooks (commands in
`.claude/hooks/guard_bash.py` + `guard_paths.py`): the Bash hook blocks any command referencing
any of those three repo directories (bare `scripts/`, `./scripts/`, `cd scripts`, or the
`$CLAUDE_PROJECT_DIR` absolute path — same forms for the other two) or the repo-root
`knowledge.md`; the path hook
(`Read|Edit|Write|Grep|Glob`) blocks tool calls targeting the repo-root `knowledge.md`.
Both are scoped to exactly those repo-root paths — same-named files or `scripts/` directories
anywhere else (other repos, `node_modules`, subdirectories) are unaffected. Deterministic
harness-level block, independent of model compliance; agents may still read/edit the `scripts/`
files via the non-Bash tools. Agents verify
changes by launching the app pieces directly (backend module, `npm run build`, Electron via
playwright — see `.claude/skills/verify`).

Dev workflow:

- **`./scripts/build.sh`** — build only, no launch: creates the venv and `node_modules` if
  missing, re-installs deps when `backend/pyproject.toml` (stamp file `.venv/.backend-stamp`)
  or `app/package.json` changed, then typechecks + builds the renderer (`npm run build` →
  `app/dist`, the bundle Electron loads in release). Runs `release.sh --sync` first, so the
  three version sites always track `VERSION`. Touches no processes and no data dir;
  safe to invoke anytime. **`--deps`** stops after the dependency step (no renderer bundle) —
  what dev.sh uses. Two hygiene rules on the dependency step: the app install is
  `npm ci` (the lockfile is the source of truth, and an install must never silently float
  a dependency), and the editable backend install's setuptools scratch output
  (`backend/build/`, `backend/*.egg-info/`) is deleted immediately afterwards, because a
  stale full copy of the package tree there is invisible to the running app yet poisons
  every repo-wide search.
- **`./scripts/release-start.sh <version>`** - prepares the repo for a release, the first
  of the three steps a release is (`release-start.sh` prepares, the developer curates and
  commits, `release.sh` cuts; the Windows and Linux legs then append their artifacts).
  Validates the argument (semver `MAJOR.MINOR.PATCH`, optional pre-release suffix; a
  leading `v` is accepted and stripped) and requires it to order semver-higher than the
  current `VERSION` (numeric base first; a release beats its own pre-releases;
  pre-releases compare lexically - the same rule `release.sh` re-applies before cutting).
  The current `VERSION` must itself be released already: its `v<VERSION>` tag must exist
  after a `git fetch --tags` (`gh release create` tags on GitHub only, so the local
  checkout learns of release tags through that fetch), otherwise the script refuses with
  the hint to cut it with `release.sh` first - a version whose release never went out is
  re-cut, never skipped over. It also refuses when the tag `v<version>` already exists.
  Then, in order: drafts the `## v<version> - <today>` section of `docs/CHANGELOG.md`
  (§17) - it collects the `v<VERSION>..HEAD` commit subjects and bodies plus the range
  diffstat, and a porcelain summary and diffstat of uncommitted work (release notes are
  drafted while the release's changes often still sit uncommitted, and a dirty tree is
  allowed here for exactly that reason), each block size-capped, and asks Claude (Opus 5,
  `claude --model claude-opus-5 -p`, prompt on stdin) for the user-facing bullet list:
  the two newest changelog sections ride along as voice examples, and the prompt demands
  `- ` bullet lines only, written for users (never a commit dump, internal-only changes
  skipped, plain hyphens, no em dash). The reply is cleaned (code-fence and blank lines
  dropped, any em dash replaced with a plain hyphen) and rejected, with the raw output
  printed, unless every remaining line is a `- ` bullet; on success the section is
  inserted directly above the previous newest section and printed. A section that
  already exists for `<version>` is kept untouched (so a re-run never overwrites curated
  notes). Only then writes `<version>` to the repo-root `VERSION` file (the single
  version source, §17) and syncs it into the three version sites via `release.sh --sync`
  - the draft is the failure-prone step, so a failed run leaves the version untouched.
  Nothing is committed: the script ends by printing the next steps (curate the section,
  commit with `commit.sh`, run `release.sh`). Fails if the `claude` CLI is missing,
  there are no changes since `v<VERSION>`, or the model returns nothing usable.
  Developer-only: agents never run this script (`.claude/CLAUDE.md` forbids it).
- **`./scripts/release.sh`** - cuts the release the committed `VERSION` names, end to end:
  builds the distributable and publishes it as a GitHub release. Takes no version - the
  version was chosen by `release-start.sh`; an argument that looks like one is refused
  with the hint to run `release-start.sh <version>` instead. Preflight, all before
  anything is modified: refuses if the working tree is dirty (the release preparation
  must be committed first - the release is cut from a commit, and the feed commit lands
  on top of it), if the checkout is not on `main` (the §3 feed URLs are pinned to
  `raw.githubusercontent.com/…/main/release/…`, so a feed committed on any other branch
  is never the file installed apps read - the same on-main rule the tap preflight applies
  to the `homebrew-tap` checkout, applied to this repo), if the tag `v<VERSION>` already
  exists (checked locally and on `origin`), if `VERSION` does not order semver-higher
  than the newest `v*` tag reachable from `HEAD` (after a tag fetch - the previous
  release), if the three version sites do not match `VERSION` (the `--check` gate; a
  mismatch means the `release-start.sh` sync was never committed), or if
  `docs/CHANGELOG.md` (§17) has no `## v<VERSION>` section or the section's body is
  empty - the §9.4 What's-new notes are written and committed before the release is cut,
  never after, and `release.sh` never drafts them itself: the hint names
  `release-start.sh`. The `homebrew-tap` preflight (below) runs last. Then: runs the
  full test suite (`build.sh --deps` for the venv/node_modules, then
  `scripts/tests/fast.sh`, then `pytest -m integration`, then `npm run test:e2e` - §15
  shift-left order; any failure aborts the release before anything is pushed or built);
  pushes `HEAD` to `origin` (the release tags the pushed commit; nothing is committed
  here - the tree was proven clean); invokes `prod.sh` to produce the versioned `.app` +
  DMG + update zip (which re-checks the artifacts itself: the in-bundle import smoke test
  and the Gatekeeper assessment); then runs `gh release create v<VERSION> <DMG> <zip>
  --title "v<VERSION>" --notes-file <notes>` to tag the pushed commit and upload the DMG
  (the §3 install artifact) and the zip (the §3 update artifact). The release body is the
  curated `docs/CHANGELOG.md` section for the version, never GitHub's commit-derived
  auto-notes: `<notes>` is a temp file holding the lines below the `## v<VERSION>`
  heading up to the next `## ` heading (or end of file), leading and trailing blank
  lines trimmed, so the GitHub release page, the §9.4 What's-new modal, and the file on
  GitHub all show the same words;
  then rewrites the built arch's update feed (`release/darwin-<arch>/latest-mac.yml`, §3 -
  the zip's release download URL plus its base64 sha512 and byte size, computed from the
  built zip) and its
  `darwin-<arch>` entry in the §17 `docs/downloads.json` download index (the DMG URL) -
  plus, exactly when the version being released is `0.6.1`, the one-time §3 legacy-bridge
  rewrite of `release/darwin-<arch>/feed.json` to point stranded 0.6.0 installs at the
  0.6.1 DMG (any other version leaves `feed.json` untouched, frozen) - and commits +
  pushes them with a plain git commit; finally updates the §3 Homebrew cask in the separate
  `homebrew-tap` repository and pushes it to that repo's `main`. Requires the `gh` CLI,
  authenticated (`gh auth login`); fails with a hint otherwise. Files are rewritten only
  when their version actually differs, so an unchanged `pyproject.toml` mtime never
  churns the `.backend-stamp` dependency re-install. The version-site rewrites probe the
  sed dialect once (GNU `-i` vs BSD `-i ''`), so `--sync`/`--check` - and therefore
  `build.sh` - run on Linux as well as macOS; the release flow itself stays macOS-only.
  Modes (version-only - no build, no
  git/GitHub actions): **`--sync`** rewrites the three sites from `VERSION` (what
  `build.sh` and `release-start.sh` run); **`--check`** verifies all three match
  `VERSION` and exits non-zero listing every mismatch (what `prod.sh` runs).
  - **`--feed` (recovery).** Sibling of `--cask`, for the other half of the post-release
    tail: it re-runs only the feed step - rewrite `release/darwin-<arch>/latest-mac.yml`
    (and, for `0.6.1` only, the §3 legacy-bridge `feed.json`) and the
    `darwin-<arch>` entry in `docs/downloads.json` for the current `VERSION`, then commit
    and push just those files - against a release that is already published. The
    recovery path when the feed write, its commit, or its push failed after the GitHub
    release went out, which a re-run of `<version>` cannot fix (the tag already exists).
    Requires `gh`, the checkout on `main`, and the release `v<VERSION>` to exist; it reads
    that release's asset list to prove the artifacts the feed is about to name are live,
    and reuses the zip in `build/` for the yml's sha512/size when it survived - otherwise
    it downloads the released zip to a temp dir to hash it (the yml cannot be written
    without the digest). Builds nothing and touches no version site. Idempotent, split the way the
    cask publish is split: an already-current pair commits nothing, but the push still runs
    whenever `main` is ahead of `origin/main`. The full release flow calls the same three
    functions, so the recovery path and the release path can never write different feeds.
  - **Homebrew cask publish (§3).** Fully scripted, never hand-edited. The tap checkout is
    always `../homebrew-tap`, the sibling of the repo root — the two repositories live in the
    same parent directory, and there is no override; the cask file is `Casks/autowright.rb`
    inside it. Preflight (with the other release prerequisites, before
    anything is modified): clone `https://github.com/hansololz/homebrew-tap.git` if the
    checkout is missing, then require it to be on `main`, clean, and holding the cask file,
    and fast-forward it to `origin/main` — a dirty or diverged tap aborts the release before
    the version bump is written, never after the GitHub release exists. The publish step runs
    **last**, after the release and the update feed, and only for an `arm64` build (the cask
    is `depends_on arch: :arm64`; an `x86_64` release logs that it skipped the cask rather
    than overwriting the arm64 URL). It rewrites the cask's `version` line to the released
    version, its `sha256` line to `shasum -a 256` of the uploaded DMG, and its `livecheck`
    URL to the §3 raw arm64 `latest-mac.yml` URL (re-pinned every release, not merely left alone - the
    four-space indent scopes that rewrite to the `livecheck do` block, since the cask's own
    `url` stanza sits at two spaces and ends in a comma), runs `brew style`
    on the result when `brew` is present, then commits `autowright <version>` and pushes the
    tap's `main` to GitHub. Commit and push are decided separately: an already-current cask
    commits nothing, but the push still runs whenever `main` is ahead of `origin/main`, so
    earlier local tap commits can never leave the published cask lagging the checkout. Both
    halves are idempotent — a tap already in sync pushes nothing and says so. A failed push
    names the `--cask` recovery mode.
- **`./scripts/prod.sh`** — the production distribution (§3), under `build/` (gitignored).
  Runs `release.sh --check` first and refuses to build on any version mismatch (the
  distributable's DMG name, bundle, and backend must agree on one version).
  Invokes `build.sh` (full), then: downloads the pinned relocatable CPython
  (python-build-standalone `20260807` / CPython `3.14.7`, arch from `uname -m`, tarball
  cached in `build/cache/`, URL overridable via `AUTOWRIGHT_PBS_URL`), upgrades the bundled
  pip to the latest release, pip-installs the backend
  + curated packages into it **pinned by `backend/constraints.txt`** (`pip install -c`, §17:
  without it, two DMGs cut from one commit could ship different library versions, and the
  §6.2 curated packages are part of the user-facing step environment)
  (inside the bundle the backend/CLI execute as
  `python3 -m autowright.main` / `-m autowright.cli` — pip's `bin/` entry scripts carry absolute
  staging-path shebangs), uses the checked-in app icon `app/electron/icon/icon.icns`
  (§14), packages `Autowright.app` with `@electron/packager` (bundle id
  `ai.autowright.app`; ships only `electron/`, `dist/`, and `package.json` — the renderer is
  fully bundled and main/preload use Electron builtins only, so no `node_modules`), copies
  the interpreter to `Contents/Resources/python/`, smoke-checks that the bundled interpreter
  imports `autowright` + every curated package from inside the bundle, codesigns and
  notarizes per §3 (Developer ID + hardened runtime on every Mach-O, inside-out, stapled —
  no ad-hoc fallback), and produces `build/Autowright-<version>-darwin-<arch>.dmg` (hdiutil UDZO),
  the §3 install + update artifact `release.sh` uploads (no separate update zip: the app
  unpacks the DMG itself at update time, §3).
- **`./linux-scripts/prod.sh`** — the Linux production distribution (bash; §17
  `linux-scripts/`, §3 Linux packaging block), under `build/linux/` (gitignored). Order
  mirrors `prod.sh`: the same three-site version gate (reimplemented — `release.sh` is
  macOS-only), `npm ci` + typechecked vite
  build, the pinned relocatable CPython in its `x86_64-unknown-linux-gnu-install_only`
  flavor staged to `build/python` (same tag/version/cache/`AUTOWRIGHT_PBS_URL` knob as
  `prod.sh`), pip install pinned by `constraints.txt`, the
  `PYTHONDONTWRITEBYTECODE=1` smoke check (imports include `secretstorage`, the §17 Linux
  keyring backend), then electron-builder `--linux appimage --x64` with the output
  overridden to `build/linux/` — producing
  `build/linux/Autowright-<version>-linux-x86_64.AppImage`, unsigned by design (§3),
  plus `latest-linux.yml` (the §3 Linux update feed; the script verifies both exist —
  the block map is embedded in the AppImage itself, §3, never a separate artifact).
- **`./linux-scripts/release.sh`** - the Linux release half (bash; §17 `linux-scripts/`, §3
  Linux packaging block), the append-only model shared with `release.ps1`: it never
  creates a release, tag, or version bump - the version is prepared by
  `release-start.sh` and the release cut by `release.sh`, both on macOS; this leg only
  adds the Linux artifact to the release those produced. Steps,
  in order: requires an authenticated `gh`, an existing GitHub release `v<VERSION>` for
  the repo-root `VERSION` (fails with the cut-it-from-macOS hint otherwise), a clean
  working tree, and the checkout on `main` (the feed it pushes is fetched from the
  `/main/` raw URL, §3 - a feed committed elsewhere is never the file installed apps
  read); runs the full test
  suite in the §15 shift-left order (`build.sh --deps`, `scripts/tests/fast.sh`,
  `pytest -m integration`, `npm run test:e2e` - the same suite `release.sh` runs; any
  failure aborts before anything is built or uploaded); builds the AppImage via
  `linux-scripts/prod.sh` (which re-checks the three-site version gate); uploads the
  AppImage to the
  existing release with `gh release upload --clobber` (idempotent - a re-run replaces the
  asset; the block map rides embedded in the AppImage, §3); then rewrites `release/linux-x86_64/latest-linux.yml` from the build
  output - the bare artifact name replaced with the released binary's download URL, the
  `release.ps1` rewrite in sed - plus the `linux-x86_64` entry in the §17
  `docs/downloads.json` download index, and commits + pushes just those files (§3:
  written only after the upload, so the feed never names a URL that is not live).
- **`./scripts/dev.sh`** — fastest dev loop, with hot reloading: invokes `build.sh --deps` only
  (no renderer bundle); shuts down lingering processes from previous sessions — backend by
  command-line pattern (`[Pp]ython -m autowright` — ps shows the venv python's resolved binary,
  never the `.venv/bin/python` path; SIGTERM, 5 s grace, then SIGKILL — defensive against any
  process stuck in shutdown; the §19 ws handler exits on client disconnect, so uvicorn's
  graceful shutdown no longer waits on WebSockets), stale Electron, and stale Vite;
  then (re)installs the real launchd LaunchAgent (`autowright service uninstall` +
  `service install`, `ai.autowright.backend`, §3) so the backend behaves exactly as in release:
  launchd-managed, RunAtLoad/KeepAlive, cwd `/`, minimal launchd PATH, random free port,
  macOS Keychain, developerMode-gated request logging (§5) to `backend.out.log`/`backend.err.log`
  and per-request files under `<logs>/requests/` (§5) under the logs
  dir (§5), data in `~/Library/Application Support/Autowright` (starts empty on a fresh
  machine); starts a Vite dev server on a random free port (`npx vite --strictPort`, log
  `vite.log` under the logs dir, killed on script exit); waits for a fresh `backend.json`
  (rewritten with new pid/token
  each start) plus `/health` and for Vite to answer, then launches Electron in the foreground
  with `AUTOWRIGHT_RENDERER_URL=http://127.0.0.1:<vite port>` (§15) — renderer edits under
  `app/src` hot-reload live; backend edits need a dev.sh restart. Quitting Electron normally
  (Cmd+Q) leaves the backend running (release semantics — automations keep firing; stop it with
  `.venv/bin/autowright service stop`, or `service uninstall` for full teardown). Ctrl+C in the dev.sh terminal instead shuts the
  whole app down: Electron dies with the terminal's SIGINT, the exit trap kills Vite, and an
  INT/TERM trap stops the backend — `autowright service uninstall` first (launchd KeepAlive
  would otherwise respawn it), then the same SIGTERM → 5 s grace → SIGKILL escalation as the
  startup stale-process sweep (defensive — the §19 ws handler exits on disconnect, so a plain
  SIGTERM normally suffices); the script exits 130. The SIGKILL path leaves a stale `backend.json` behind, which the next
  startup already tolerates (fresh-file compare).
  Nothing self-relaunches: a §4.9 reset quits the app outright (§3 reset step 6), so once
  the foreground Electron exits the script falls straight through to teardown.
  Isolated mode: setting any `AUTOWRIGHT_*` knob (§15) switches dev.sh to spawning the backend
  directly with that env instead of via launchd (the plist carries no env) — detached, cwd `/`,
  launchd PATH (`/usr/bin:/bin:/usr/sbin:/sbin`), same log filenames under the chosen home.
  `--fresh` wipes the data dir first and is refused unless `AUTOWRIGHT_HOME` is set (never wipes
  the real app data).
- **`.\windows-scripts\dev.ps1`** — dev.sh on Windows (PowerShell; §17 `windows-scripts/`).
  Same contract — deps only, stale-process sweep, real service, Vite + Electron with HMR,
  release semantics on normal quit, full teardown on Ctrl+C, the same isolated mode and
  wipe rule (`-Fresh`, the PowerShell flag form) — mapped per-OS:
  - Deps are inlined (build.sh is bash): venv via `py -3.14` when `.venv\Scripts\python.exe`
    is missing, then the same two change-gated steps — `.venv/.backend-stamp`-gated
    `pip install -e backend[dev]` (plus the setuptools-debris cleanup) and lockfile-gated
    `npm ci`. Two deliberate omissions: no version sync (`release.sh --sync` is macOS-only
    bash; `prod.ps1`'s version gate protects distributables) and no acknowledgements regen
    (`gen_licenses.py`'s list-form `subprocess` call cannot resolve `npm.cmd` on Windows —
    the checked-in file is refreshed by mac builds, the stance `prod.ps1` also takes).
  - Stale sweep: the same four command-line patterns (backend `python* -m autowright`, the
    repo venv's entry points, this repo's Electron, this repo's Vite), matched via CIM
    `Win32_Process` and killed with `taskkill /T /F` — no TERM-then-KILL grace: Windows has
    no deliverable SIGTERM, and the §2 process contract treats every kill as the hard tree
    kill, which also clears backends stuck in graceful shutdown.
  - Backend: `service uninstall` + `service install` (re)registers the §3 Task Scheduler
    task `ai.autowright.backend` on the venv interpreter (§3 picks the `pythonw.exe` beside
    `Scripts\python.exe`). Data `%LOCALAPPDATA%\Autowright`, logs
    `%LOCALAPPDATA%\Autowright\Logs` (§5 root table); log filenames are unchanged because
    on Windows the backend routes its own stdout/stderr to `backend.out.log` /
    `backend.err.log` (§3 route_logs).
  - Isolated mode (any `AUTOWRIGHT_*` knob set — `AUTOWRIGHT_RENDERER_URL` excluded, the
    script sets that one itself for Electron) spawns the backend directly: detached hidden
    window, cwd `%SystemRoot%\System32` (Task Scheduler's default), the **full** user
    environment — no launchd-PATH mimicry, per §2 Windows tasks are not env-stripped —
    with stdout/stderr redirected to the same two log files (catches failures earlier than
    route_logs, e.g. import errors).
  - Vite runs on a random free port picked via a loopback `TcpListener`, launched as
    `node node_modules\vite\bin\vite.js` under `cmd /d /c` (the redirect merges both
    streams into `vite.log`; `/d` skips cmd AutoRun hooks, and invoking node directly —
    not `npx` — keeps a machine's AutoRun output out of the picture entirely). Electron
    likewise runs as `node node_modules\electron\cli.js .` in the foreground with
    `AUTOWRIGHT_RENDERER_URL` set (removed again on exit — the variable must not leak into
    the caller's session, where it would flip the next run to isolated mode; `.ps1` scripts
    share the calling PowerShell process).
  - Teardown: a `finally` block replaces the bash traps — every exit sweeps Vite; Ctrl+C
    (which reaches the console-attached Electron on its own) additionally uninstalls the
    service and sweeps the backend, while a normal Electron quit and the startup-failure
    exits leave the backend running, exactly as dev.sh does. No `exit 130` (PowerShell
    cannot set an exit code from a Ctrl+C-interrupted `finally`).
- **`./linux-scripts/dev.sh`** — dev.sh on Linux (bash; §17 `linux-scripts/`). Same
  contract — deps only, stale-process sweep, real service, Vite + Electron with HMR,
  release semantics on normal quit, full teardown on Ctrl+C (exit 130), the same
  isolated mode and `--fresh` wipe rule — mapped per-OS:
  - Deps stay `scripts/build.sh --deps` verbatim: build.sh is plain bash and runs
    natively on Linux, version sync and acknowledgements regen included (the release.sh
    version rewrites are sed-dialect-portable, above).
  - Backend: `service uninstall` + `service install` (re)registers the §3 systemd user
    unit `ai.autowright.backend` on the venv interpreter. Data
    `${XDG_DATA_HOME:-~/.local/share}/autowright`, logs
    `${XDG_STATE_HOME:-~/.local/state}/autowright/log` (§5 root table); log filenames
    are unchanged (the unit's `append:` routing writes the same
    `backend.out.log`/`backend.err.log`).
  - Stale sweep: the same four command-line patterns and the same SIGTERM → 5 s grace →
    SIGKILL escalation, with the backend pattern widened to
    `[Pp]ython[0-9.]* -m autowright` — Linux `/proc` cmdlines show the argv the process
    was exec'd with (the venv's `python`, `python3`, or `python3.14`), never a resolved
    framework binary name the way macOS `ps` does.
  - Isolated mode (any `AUTOWRIGHT_*` knob set) spawns the backend directly, mimicking
    the systemd user-manager environment the unit would get (§3 — the unit sets no
    `WorkingDirectory` or `Environment`): cwd `$HOME`, the default user-manager PATH
    (`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`), detached, with
    stdout/stderr redirected to the same two log files under the chosen home.
  - Electron sandbox heal: Ubuntu 24.04+ restricts unprivileged user namespaces via
    AppArmor (`kernel.apparmor_restrict_unprivileged_userns=1`), which forces Chromium
    onto its SUID sandbox helper — npm unpacks `electron/dist/chrome-sandbox` user-owned
    without the setuid bit, and Electron aborts rather than run unsandboxed. After the
    deps step, when that sysctl reads `1` and the helper is not already root-owned mode
    4755, dev.sh heals it (`sudo chown root:root` + `sudo chmod 4755` — the script is
    run by hand in a terminal, so the sudo prompt is fine); a fresh `npm ci` unpack
    re-triggers the heal. The sandbox itself stays on — never `--no-sandbox` (dev/release
    parity). The packaged AppImage faces the same restriction — open item, `spec/ports.md`.
- **`./scripts/build-clean.sh`** — resets the repo to a pre-build state so the next
  `build.sh`/`dev.sh` rebuilds from scratch. First stops anything running **from this repo**
  (deleting `.venv` under the live launchd KeepAlive service would otherwise break):
  `autowright service uninstall` only when the LaunchAgent plist's program points inside the
  repo — a service registered by the installed `/Applications` app is the user's live backend
  and survives a repo clean untouched — then the same kill_stale patterns as dev.sh for the
  repo's backend, Electron, and Vite processes. Then deletes the build
  artifacts: `.venv` (incl. the `.backend-stamp`), `app/node_modules`, `app/dist`, and the
  contents of `build/` except `build/cache/` (the pinned CPython tarball, expensive to
  re-download); **`--cache`** drops the cache too, removing `build/` entirely. Never touches the
  data dir (`~/Library/Application Support/Autowright` or `AUTOWRIGHT_HOME`) or the logs dir.
- Backend: `python3.14 -m venv .venv && .venv/bin/pip install -e "backend[dev]"`; test with
  `.venv/bin/python -m pytest tests/`; dev.sh launches the backend via `python -m autowright.main`
  (equivalent to the `autowright-backend` entry point); start an isolated backend (real agent CLIs,
  real Keychain, empty home) with `AUTOWRIGHT_HOME=<dir> AUTOWRIGHT_PORT=8799 .venv/bin/autowright-backend`.
- App: `cd app && npm install`; typecheck+bundle with `npm run build`; `npm run app` launches
  Electron against the built bundle (release delivery; dev.sh instead serves the same source
  via Vite + `AUTOWRIGHT_RENDERER_URL`, §15).
- **`./scripts/uninstall/<tool>.sh`** (`claude-code.sh`, `codex.sh`, `gemini.sh`,
  `opencode.sh`, `ollama.sh`) — developer-only reversal of the §19 installers, run manually by a
  developer in a terminal. Default removes what the §19 installer put there (Claude: the
  `~/.local/bin/claude` symlink + `~/.local/share/claude` versions; Codex: the
  `~/.local/bin/codex` + `codex-code-mode-host` symlinks and the
  `~/.codex/packages/standalone` payload tree; Gemini via
  `npm uninstall -g --prefix ~/.local @google/gemini-cli` plus its `~/.local/lib/node_modules`
  tree; Ollama quits the app and running server first, then removes `Ollama.app` from
  `/Applications`/`~/Applications` and the `~/.local/bin/ollama` symlink (plus a
  `/usr/local/bin/ollama` symlink when it points into that app bundle — the §19
  vendor-location symlink); OpenCode prefers
  the CLI's own `opencode uninstall --force` — with `--keep-config --keep-data` unless
  purging — then removes leftovers in `~/.opencode/bin` and legacy `~/.local/bin`).
  **`--purge`** also deletes the tool's
  config/auth/data dirs (`~/.claude` + `~/.claude.json*` and `~/.local/share/claude`;
  `~/.codex`; `~/.gemini`; `~/.opencode` plus the
  `~/.config`/`~/.local/share`/`~/.local/state`/`~/.cache` `opencode` dirs;
  `~/.ollama` incl. models). Never invoked by the app, the backend, or any
  agent — each script guards itself (shared `_lib.sh`): exits if agent env markers are present
  (`CLAUDECODE`), exits without an interactive TTY on stdin+stdout, and requires the developer
  to type the tool name to confirm.
- **`./scripts/knowledge.sh`** — regenerates `knowledge.md` at the repo root: a gitignored,
  developer-only orientation doc (concise, diagram-heavy — mermaid architecture + per-action
  sequence diagrams, annotated file tree, data-model and key-file tables, CLI command table,
  agent-skill overview, Python APIs — step SDK + backend HTTP/WS endpoint map — message-trigger
  flows (Discord + iMessage today, pubsub reserved), and a dev-workflow scripts table). Invokes
  `claude --model claude-opus-5 -p` with read-only tools (`Read`, `Glob`, `Grep`, and
  read-only Bash: `ls`/`tree`/`git ls-files`/`git log`/`wc`/`head`/`cat`) to explore the repo
  (SPEC.md as primary source, verified against code), prepends a generated-at header, and
  writes atomically (temp file + `mv`). Purely for developer reading — never read by agents,
  never used to build the app; no other file references it. Developer-only: agents never run
  this script (`.claude/CLAUDE.md` forbids it). The §18 PreToolUse hooks reject
  any Bash command or `Read|Edit|Write|Grep|Glob` call targeting the repo-root `knowledge.md`
  (only that exact path — same-named files elsewhere are unaffected).
  **`audit` mode** — `./scripts/knowledge.sh audit` writes `knowledge-audit.md` (repo root,
  gitignored, developer-only, same generated-at header) instead of the orientation doc: a
  soundness audit, run with the same read-only Claude invocation, that cross-checks three
  layers against each other and reports every mismatch rather than describing the app.
  Coverage: (1) **data model** — every §4 entity/field in `spec/data-model.md` vs the backend
  (`storage.py`, `execdb.py`) vs the renderer mirror (`app/src/types.ts`, `store.ts`): missing
  fields, type/enum drift, fields present in code but absent from the spec or vice versa;
  (2) **on-disk layout** — the §5 storage tree in `spec/storage.md` vs `paths.py` and the
  read/write sites: paths or files the spec promises but code never writes, and files code
  writes that the spec omits; (3) **repository structure** — §17 vs `git ls-files`: entries
  documented but missing, top-level files/dirs present but undocumented. Output is a findings
  table per layer (finding, where spec says, where code says, severity: mismatch /
  spec-missing / code-missing) with an explicit "sound — no findings" verdict for any clean
  layer; no orientation prose. Fails (non-empty check, same as the doc mode) if generation
  returns nothing.
- **`./scripts/pip-release.sh`** — builds and uploads the `pypi/` placeholder package (§17;
  reserves the `autowright` name on PyPI, unrelated to the app build and to `release.sh`).
  Creates `pypi/.venv` if missing, installs/upgrades `build` + `twine` into it, rebuilds
  `pypi/dist/` from scratch, validates both artifacts with `twine check`, then uploads.
  Credentials come from `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` (API token: username
  `__token__`) — never stored in the repo. Modes: **`--build`** stops after build + check (no
  upload); **`--test`** uploads to TestPyPI (`--repository testpypi`) instead of PyPI.
- **`./scripts/commit.sh`** — stages all uncommitted changes (`git add -A`), asks Claude
  (Opus 5, `claude --model claude-opus-5 -p`) for a commit message based on the staged diff
  (≤72-char imperative summary, whole message capped at 2-3 sentences), strips any markdown
  code-fence lines (```/```lang) the model wraps the message in, prints it, and commits. Exits 0 with no commit if
  the tree is clean; fails if the message is empty after stripping. Does not push. Developer-only:
  agents never run this script (`.claude/CLAUDE.md` forbids it).
- **`.\windows-scripts\commit.ps1`** — commit.sh on Windows (PowerShell; §17
  `windows-scripts/`). Same contract — clean-tree exit 0 with no commit, `git add -A`,
  Claude-generated message (Opus 5) from the staged diff with the same prompt, the same
  fence-stripping, fails on an empty message, does not push, developer-only — mapped per-OS:
  - The prompt goes to `claude -p` on **stdin** instead of as an argument — Windows caps a
    process command line at ~32K characters, which a real diff overflows.
  - Windows PowerShell 5.1 defaults would mangle non-ASCII diff content in both directions
    (ASCII `$OutputEncoding` on the stdin pipe, the OEM codepage when reading native
    output), so the script sets both to UTF-8 for the duration and restores the console
    encoding on exit (`.ps1` scripts share the calling PowerShell process).
  - `Push-Location`/`Pop-Location` to the repo top-level replaces `cd` (same
    shared-process reason).
- **`./linux-scripts/commit.sh`** — commit.sh on Linux (bash; §17 `linux-scripts/`). Same
  contract — clean-tree exit 0 with no commit, `git add -A`, Claude-generated message
  (Opus 5) from the staged diff with the same prompt, the same fence-stripping, fails on an
  empty message, does not push, developer-only — mapped per-OS: the prompt goes to
  `claude -p` on **stdin** instead of as an argument — Linux caps a single argv string at
  `MAX_ARG_STRLEN` (128 KiB), which a real diff overflows.
