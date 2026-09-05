# Autowright SPEC — Backend API

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 19. Backend API (decided)

Localhost JSON over HTTP + one WebSocket, both authenticated with the bearer token from
`backend.json` (§3). Entity JSON uses the §4 field names verbatim (`automations`-shaped automations,
`executions`-shaped executions) so UI state mirrors the model. UI and CLI use only this API (§3 parity).

The server binds `127.0.0.1` only. Token comparisons (HTTP bearer and the WebSocket `token`
query param) use `secrets.compare_digest`. The interactive docs surfaces FastAPI would mount by
default (`/docs`, `/redoc`, `/openapi.json`) are **disabled** — `GET /health` is the only
unauthenticated route, and a browser on any website can reach localhost, so the app must not
publish its schema to one. For the same reason CORS does not allow arbitrary origins: the only
origins accepted are `null` (the packaged `file://` renderer) and `http://localhost|127.0.0.1`
with any port (the §15 renderer-URL dev server), credentials off. One rule in both modes — the
§15 knob changes where the renderer is served from, never the policy.

**Request validation:** every mutating endpoint's request body is validated by a pydantic
request model (`backend/autowright/models.py`) — a malformed body (missing or mistyped
fields, wrong shapes: `stepAgents`/`allowedSecrets` must be lists of strings, the settings
booleans must be booleans with `days` an int) answers 422 before any handler logic runs.
The store-state cross-field checks live in the handlers, answering the same 422:
`paramValues` entries are checked against the automation's param definitions (names **and**
kinds), `agentId`/`stepAgents` entries must reference configured agents, and
`allowedSecrets` entries must reference stored secrets by their §4.8 id
(`_check_param_values` / `_check_agent_refs` / `_check_secret_refs` in `api.py` — they need
store state the models
never see). Pydantic shapes **requests only** — response bodies
remain plain dicts (§2).

- `GET /health` → `{ version, app, os, capabilities }` (unauthenticated; used for
  discovery/liveness). `os` is the §5.1 platform token; `capabilities` is the §2 platform
  layer's flag set — `{ imessage, notifications, keepAwake, service, agentInstall }`, all
  true on macOS —
  and is the one surface clients gate platform features on (never by sniffing the platform
  at a call site)
- `GET /state` → boot snapshot: automations (full), executions (the §7 window, not the full
  list: every `queued`/`executing` header plus the 50 newest finished headers - exactly one
  §7 page - in the §7
  canonical order - `startedMs` desc, id asc on ties), `executionsTotal` (count of every
  execution header the backend holds, §4.5 test rows included - backs the §9 sidebar pill;
  deeper history pages in via `GET /executions` below), agents, secrets (the
  `GET /secrets` entries — id, name, description, set, usedBy; never values), settings, app
  version, `pendingDraft` (`{ name, updatedAt } | null` — the §4.4
  slot's identity summary; backs the §9.1 Resume draft button), and `draftJobs`
  (`[{ owner, jobId, status, mode }]` — every §19 drafting job currently building or held
  for consumption, `owner` an automation id or the literal `pending`; backs the §9.1
  drafting notes, kept current by the `draftjob.changed` event below)
- `GET /instructions` → `{ framework, defaultBuild }` — the two §8 instruction files verbatim
  (backs the §11 Framework-instructions and Build-instructions cards)
- `GET /automations` · `GET /automations/{id}` · `DELETE /automations/{id}` — delete cancels
  every live execution and queued firing, **and settles the automation's draft work the same
  way a save does** (the §11 draft test is cancelled and its record marked so a landing test
  deletes itself instead of rewriting the removed container; the automation's still-building
  §8 drafting jobs are cancelled - a deleted automation never leaves a test or agent harness
  process running), then **waits for the cancelled engine threads to
  finish** (bounded by the §7 SIGTERM→SIGKILL grace plus margin) before removing the
  automation directory — a step still dying during the grace window must not re-create
  `memory/` after the rmtree (a half-recreated directory with no `versions/` would be
  invisible to the UI forever). The admission window closes first: the automation stays
  registered while its executions are cancelled and awaited, so before anything else the
  delete flags the record in memory and `engine.start` refuses admissions for it — a
  scheduler tick, listener dispatch, or app-start firing landing mid-delete would otherwise
  escape the wait set and re-create the tree after the rmtree (the scheduler's own
  one-shot consumption carries the same registered-object guard for the same reason).
  If a thread somehow survives the wait, the directory is
  removed anyway (the step group is already hard-killed by then)
- `PATCH /automations/{id}` — user-owned fields only: name (a blank or missing name is
  ignored — a rename can never clear the name; a name another automation already holds,
  compared case-insensitively and excluding the automation itself, answers 422 "an
  automation named X already exists - automation names must be unique" and nothing is
  stored - §4.1 uniqueness; a case-only rename of the same automation is allowed), description (blank clears it — the description is
  optional, §4.1), triggers (the §4.3 list, replaced
  whole; entries keep their `id`, new entries get one assigned;
  cron/time/app_start/discord/imessage
  kinds — a reserved kind (pubsub), an invalid cron expression, an unknown `timezone`, a
  non-boolean `runIfMissed` (§4.3; ignored on kinds other than cron/time), a
  past `time`, a `time` whose `at` carries a UTC offset (the zone belongs in `timezone`; naive
  local ISO only), a second `app_start`, or a discord/imessage entry failing the §4.3 field
  rules
  answers 422 and nothing is stored; a cron entry's §4.3 `source` is required and must be `"spec"`
  or `"user"` (422 otherwise — absent included) and is stored as sent;
  serialized discord and imessage triggers carry the
  derived §4.3
  `connection` state), param
  values, agentId, stepAgents, allowedSecrets, snapshotSettings (the §6.3 automatic-snapshot
  toggles — partial object, sent keys merged over the stored ones), maxParallel (int ≥ 1) and
  maxQueued (int ≥ 0) — the §6 concurrency settings; a non-integer or out-of-range value
  answers 422 and nothing is stored. Lowering `maxParallel` never kills a running execution:
  the automation simply admits nothing new until it is back under the limit. Lowering
  `maxQueued` below the number already waiting keeps those entries — the cap governs admission,
  not eviction (§6).
- `POST /triggers/preview` `{ triggers: [trigger, …] }` → `{ triggers: [{ valid, error?,
  label, short, nextAtMs, nextLabel? }, …] }` — a **pure function endpoint**: no state read or
  written. Validates and labels a list of §4.3-shaped trigger dicts (kind plus its stored
  fields, `timezone` where relevant) with the same `triggers.py` code that gates the PATCH,
  answering one result per request entry in order. `valid` says whether the entry would
  store; `error` is the plain-word reason otherwise ("a cron expression needs 5 fields
  (minute hour day month weekday)", the
  §4.3 field rules) — an invalid entry is a `valid: false` result, never a 422 (the editors
  preview half-typed state); only a body that isn't a list of trigger dicts gets the
  ordinary 422. `label`/`short` are the §4.3 display strings; `nextAtMs` the next-occurrence
  epoch ms (null when the kind has no computable next — app_start and message triggers —
  or the entry is invalid or elapsed) and `nextLabel` its "Jul 20, 3:00 PM"-style moment
  label. The renderer keeps **no local trigger-math mirror**: the §9.2 Add-trigger editor's
  live preview line, the §11 draft-trigger chips, and every "next trigger" label read from
  this endpoint — trigger math exists once, in `triggers.py`.
- `POST /automations/{id}/execute` `{ version?: "vN" | "draft" (case-insensitive),
  trigger?: "manual" | "menubar" (§4.5 kind, default "manual"; anything else answers 422),
  queue?: bool (default false) }` →
  `{ executionId, queued: bool }`. `queue` absent/false: 409 when every §6 `maxParallel`
  slot is taken — a plain manual start is refused, never silently queued. `queue: true`
  (the §9.2 popup's Queue action): with a free slot it starts (`queued: false`); at
  capacity it is admitted to the §6 queue per the manual-admission rules (`queued: true`,
  the record publishes `execution.queued`); a full queue answers 409 "the queue is full
  (N waiting)" with no record, and `version: "draft"` answers 409 (a Draft is never
  queued, §6). A version label that doesn't resolve answers 404 either way.
- `POST /automations/{id}/queue/clear` → `{ cancelled }` — cancels every §6 firing-queue entry
  waiting on this automation (each finishes `skipped`, §4.6, and its sender is told). Running
  executions are untouched; use `POST /executions/{id}/cancel` for those. Answers `{cancelled: 0}`
  when the queue is empty rather than 404 — clearing an empty queue is not an error.
- `POST /app-started` `{ launchId }` → `{ fired }` — the §6 app-start firing path, called by the
  Electron main process once per app launch: starts an execution for every automation holding an
  enabled `app_start` trigger (one mid-execution gets a skipped record instead, §6); `fired`
  counts the executions started. **Idempotent per launch:** `launchId` is a uuid minted once per
  app process, and a repeat call carrying a `launchId` already served fires nothing and returns
  `fired: 0`. The caller retries while the backend is still coming up, and a response lost after
  the server already fired (socket reset, backend restarting mid-request) would otherwise execute
  every app-start automation a second time. The served-launch memory is bounded (the most recent
  256 ids, oldest dropped first) so a long-lived backend never grows it without limit; retries
  arrive seconds apart, so an id can only fall out long after any retry for it could still be in
  flight. One automation failing to start is logged and skipped
  rather than failing the batch — a 500 halfway through would leave the rest unfired and provoke
  exactly that retry.
- `GET /imessage/permissions` → `{ fullDisk: bool, automation: "granted" | "denied" |
  "unknown" }` — the §9 permission checklist's status source. `fullDisk` probes by opening
  the §6 chat.db read-only right now (false on permission error **or** missing file);
  never prompts. `automation` is the backend's remembered result of its most recent Apple
  Events send to Messages (probe, `reply()`, or busy notice) — macOS offers no
  prompt-free way to read it, so it is `"unknown"` until the backend has sent one this
  install (the remembered value persists in settings storage, §5)
- `POST /imessage/permissions/automation-probe` → `{ automation: "granted" | "denied" }` —
  fires a benign Apple Event at Messages.app (`osascript`: count chats), which makes macOS
  show the Automation consent prompt if the user has never answered it (and may launch
  Messages.app); the result updates the remembered `automation` state above. Called by the §9
  checklist's Grant button; blocks until the user answers the prompt
- `POST /automations` `{ draft, name?, agentId?, stepAgents?, allowedSecrets?, paramValues?, concurrency? }` → the new
  automation's bare §4 automation JSON — create v1 from the sent draft (the §11 Create
  button). The draft must hold steps (422 otherwise); its `triggers` list is validated and
  normalized exactly like the PATCH (422 aborts, nothing written), and the §8 step validators
  run server-side (rule below). `paramValues` (the §4.2 chat-staged map) applies after v1
  lands: entries matched by name **and kind** against v1's definitions land as stored
  values, unmatched entries are dropped silently (the §4.2 lenient matching rule — never a
  422; the definitions were just rebuilt and a stale name is expected). `concurrency`
  (the §8 chat-staged object, partial `{ maxParallel?, maxQueued? }`) applies to the new
  automation like the PATCH's fields — same validation, 422 aborts and nothing is
  created. `name` falls back to the draft's name, then "New automation", and the resolved
  name then dedupes per §4.1 (case-insensitive; smallest free "Name n" suffix) - the name
  may be agent-seeded or the fallback, so create never 422s on a collision;
  `stepAgents`/`allowedSecrets` land as the automation's grants exactly as sent (§20 grant
  model — no all-on seed), with one narrow default: when `stepAgents` is **omitted
  entirely**, the store seeds it with the drafting agent (`[agentId]`, empty without one) —
  an explicit empty list lands empty. Success consumes the §4.4
  pending create-mode slot (`<root>/draft/` is emptied on success), first migrating the
  slot's `chat.jsonl` into the new automation's container and appending the §4.4
  "Created as v1." boundary marker there (thread-lifetime rules, §4.4)
- `POST /automations/{id}/versions` `{ draft, name?, agentId?, stepAgents?, allowedSecrets?, paramValues?, concurrency? }`
  — save edit as vN+1; the optional identity/grant fields, when sent, are applied to the
  automation as a patch after the version lands — a sent `name` validates like the PATCH's
  (§4.1 case-insensitive collision with another automation), checked up front with the other
  validations: 422 aborts the save and nothing is written — the §20 push grant model rides this
  (the CLI sends the draft plus its computed `stepAgents`/`allowedSecrets` in the one call).
  The draft's `triggers`
  list (when the key is sent) replaces the automation's trigger list whole, validated and
  normalized like the PATCH (422 aborts the save; entries keep their `id`, new ones get
  one). `paramValues` applies like the create endpoint's — name+kind matched against the
  landing version's definitions, unmatched dropped. `concurrency` applies like the
  create endpoint's — the PATCH's validation, 422 aborts the save. **Operational-only save** (§4.4): when
  the sent draft's versioned content — spec, steps, instructions, notes, param
  definitions, packages — equals the current version's over the stored serialization, no
  version is minted; the triggers replacement, `paramValues`, `concurrency`, and the identity/grant patch
  still apply, and the response is the same automation JSON either way. Saving settles the
  draft (the container is deleted) and appends the §4.4 boundary marker to the
  automation's thread — "Draft saved as vN." when a version was minted, "Changes saved —
  no new version." on the operational-only save
- **Server-side step validation** — `POST /automations` and `POST /automations/{id}/versions`
  run the §8 step validators (`ast.parse`, the §6.2 import allowlist, manifest schema and
  step-file ordering, the timeout/retry rules, the §8 rule-6/7 id checks — step `agents:` /
  `secrets:` entries and the code-scanned `agents["<id>"]` / `secrets["<id>"]` literals
  resolved against all configured agents and stored secrets) on the sent draft and answer
  422 with the
  validation errors when it fails — an invalid draft can never land as a version, whatever
  client sent it. The §20 CLI keeps its own pre-save copy of the same validators only for
  friendlier errors (one per line, nothing sent); the server check is the enforcement
- `GET/PUT/DELETE /draft/{owner}` · `POST /draft/{owner}/open` — the one draft-container
  surface (§4.4): `owner` is an automation id (that automation's `draft/` container) or the
  literal **`pending`** (the create-mode slot `<root>/draft/`). One container shape and one
  shared draft serializer for both owners — only the on-disk location differs (§5,
  unchanged). An automation `owner` that doesn't resolve answers 404. `PUT`
  `{ draft, agentId? }` stores the §4.4 snapshot: the payload's
  stepAgents/allowedSecrets/triggers/paramValues/concurrency/testValues/outOfSync are stored as
  draft-only keys
  (`paramValues` the §4.2 chat-staged value map, stored as the draft-only `param_values`
  key; `concurrency` the §8 chat-staged concurrency object, stored as the draft-only
  `concurrency` key; `testValues` the §8 drafted test-value map, stored as the draft-only `test_values`
  key) and echoed back on the
  automation's `draft` object (or on `GET /draft/pending`); the §11 chat thread is **not**
  part of the draft payload — it lives on the `/chat/{owner}` surface below; for
  the `pending` owner the payload additionally carries the identity fields no automation
  record exists to hold (name and triggers ride the payload; `agentId` beside it). `GET` →
  `{ draft: payload | null, agentId }` (`draft: null` when the container is empty or
  absent). `POST /draft/{owner}/open` makes the container (`draft/` with an empty
  `memory/`) exist, never touching contents already there — the create flow calls it on
  open so the pending slot exists before any drafting or test. For an automation owner, PUT
  and DELETE answer 409 while a Draft-version execution is running (rewriting/pruning the
  draft's step scripts mid-run would break the per-step sha record). Every **draft-settle**
  endpoint (this DELETE, `POST /automations`, `POST /automations/{id}/versions`) first
  **cancels the container's still-executing test record** (§11 test lifetime), marking it
  so it deletes itself when it lands instead of surviving as an orphan or rewriting the
  settled container's `test.yaml`, **and cancels the owner's still-building §8 drafting
  jobs** (`POST /drafts` stamps every job with its owner, below; the cancel kills the
  harness process exactly like `DELETE /drafts/{jobId}`) **and drops the owner's held
  terminal job record** (background continuation, below) — a settled draft never leaves
  an agent process, test process, or unconsumed outcome behind. `DELETE` settles the
  draft but **never deletes the thread** (§4.4 thread lifetime): it appends the "Draft
  discarded." boundary marker to the owner's `chat.jsonl` and, for the pending owner,
  empties the slot's draft contents while leaving `chat.jsonl` in place. The §8 drafting-job
  endpoints (`POST /drafts`, below) are a different thing — jobs, not containers — and are
  unrelated to this surface
- `GET/PUT /chat/{owner}` — the §11 chat-thread surface (§4.4 thread lifetime): `owner` is
  an automation id or the literal `pending`, resolved exactly like `/draft/{owner}` (404
  on an unknown automation). `GET` → `{ chat: [...] }` — the owner's `chat.jsonl` entries
  (`[]` when none). `PUT` `{ chat: [...] }` rewrites the file whole (the editor's debounced
  thread persist and §11 Clear chat); an empty list unlinks the file. Independent of the
  draft container: no draft needs to exist, and no Draft-execution 409 (the thread never
  feeds a running execution). The §4.4 boundary markers are appended by the settle
  endpoints themselves (save, create, draft DELETE), never through this surface
- `POST /automations/{id}/restore` `{ version }` — restore vX as vN+1, written through the
  §5 version-folder writer (manifest last as the commit point — never a tree copy)
- `DELETE /automations/{id}/versions/{v}` — §4.4 delete an old version: removes the
  `versions/vX/` folder and the in-memory entry, answers `{ automation }` (the updated
  full JSON) and publishes the automation-changed event. Guards, checked under one lock
  span: 404 unknown automation or version, 400 when `v` is the current version (the UI
  never offers it — restore another version first), 409 when any live or queued
  execution records `(kind: version, version: v)` — an admitted version execution (§19
  execute `version`, §20 `execute --version`) must not
  lose its content before or during its run. Past execution records are untouched
  (§4.5 stores its own step list), but a failed execution on a deleted version can no
  longer retry — the §7 retry's version resolution answers 404
- `GET /automations/{id}/export?values=0|1` — the §5.1 transfer archive as `application/zip`
  (`Content-Disposition` filename `<name>.autowright`, name sanitized for the filesystem);
  `values=0` omits `param_values` from the manifest (default `1`)
- `POST /automations/import` — the §5.1 archive as the raw request body
  (`application/octet-stream`, no multipart) → `{ automation, summary }` where `summary` is
  `{ secretsMatched, agentsMatched, unresolved, packages, renamedFrom, os, osMismatch }` —
  `secretsMatched` is `[{ name, matchedTo, matchedBy }]` and `agentsMatched`
  `[{ name, matchedTo, matchedBy, ready }]`: `name` the archive record's name, `matchedTo`
  the matched local record's name, `matchedBy` the §5.1 ladder rung (`name | similarity`
  for secrets, `name | configuration | similarity` for agents), and `ready` computed at
  import time by the one §19
  check-ready rule (`/agents/{id}/check`) for the matched agent's harness/mode/model — it
  backs the §9.1 summary modal's Needs setup badge; `unresolved` is
  `[{ kind, name, description }]` (§5.1 archive order, secrets before agents) — the
  references that matched nothing and landed as §4.1 `unresolvedReferences`;
  `renamedFrom` is the archive's automation name when the §4.1 dedupe renamed the landed
  automation, else `null`; `os` is the manifest's platform token (`null` when absent) and
  `osMismatch` whether it differs from the running platform (`false` when absent), §5.1;
  `packages` the §6.2 declarations. A successful import also starts the §6.2 package
  ensure for those declarations in the background and republishes the automation over the
  WebSocket when it finishes (§5.1), so a `package-missing` problem clears without a
  reload. Validates the whole
  archive first; any failure answers
  422 with the reason and writes nothing (import writes only the new automation — never
  the agents or secrets stores). Size caps (untrusted input): the upload itself is
  capped at 64 MB (413), one member at 32 MB decompressed and the whole archive at 256 MB
  decompressed (422) — a crafted archive can't balloon into memory
- `POST /automations/import/preview` — raw archive body exactly like `/automations/import`
  (same caps) → `{ token, preview }`: validates fully, writes nothing into the store, parks the
  bytes under the one-time `token` (§5.2 — spooled to a file under `import-spool/`, not held in
  memory; 15-minute expiry; at most 4 archives are parked at once, and
  a 5th preview evicts the oldest — a confirm against an evicted token answers 404 exactly
  like an expired one; a spool file that can't be written answers 507 with the reason, and one
  that has vanished by confirm time answers the same 404 as an expired token). `preview` is `{ name, landsAs, description, steps: [{name,
  description, agent}], params: [{name, kind}], triggers, packages, agents: [{name, harness, mode,
  model, matchedTo, matchedBy}], secrets: [{name, description, matchedTo, matchedBy}], os,
  osMismatch }` — `matchedTo`/`matchedBy`
  are the §5.1 match ladders run dry (the matched local record's name and the rung; both
  `null` when the reference would land unresolved), `landsAs` the §4.1 name dedupe run dry
  (§5.2 — equal to `name` when free,
  best-effort until confirm — confirm re-runs the dedupe and the ladders, §5.2), and
  `os`/`osMismatch` the §5.1 platform token + mismatch flag
  (same rule as the import summary)
- `POST /automations/import/url` `{ url }` → same `{ token, preview }` shape plus
  `preview.sourceUrl` (as given) and `preview.resolvedUrl` (after §5.2 GitHub resolution;
  equal for direct links). Any §5.2 URL-rule failure — non-HTTPS, unresolvable page, download
  error, oversized or non-archive download — answers 422 with the reason
- `POST /automations/import/confirm` `{ token }` → `{ automation, summary }` exactly like
  `/automations/import`; the token is one-time — spent, expired, or unknown answers 404
- `GET /automations/{id}/memory/files` — read-only list of the §4.1 memory directory's
  files: `{ files: [{ name, size, updated }] }` — `name` the memory-relative posix path
  (recursive), `size` in bytes, `updated` the §4.1 display label; sorted by name; empty or
  missing memory → `[]`. No live-execution 409 and no lock: reads rely on the §6 atomic
  commit, so a whole file is always seen. Backs §20 `automation memory show`
- `GET /automations/{id}/memory/files/{name}` — one memory file's content:
  `{ name, size, text }` (`name` may contain `/` — the route takes the rest of the path).
  422 when `name` escapes the memory directory (absolute, `..`, or otherwise resolving
  outside it) or the file is not UTF-8 text (the message says the file is binary and names
  the memory path on disk); 404 when no such file. Same lock-free read rule as the list
- `POST /automations/{id}/memory/clear` — 409 while **any** execution of the automation is
  live, the same guard (and the same lock span) as manual snapshot and restore — a
  mid-execution clear could delete files a step is reading; then §6.3 pre-clear snapshot,
  then empty the §4.1 memory directory (backs §9.2 "Clear memory")
- `POST /automations/{id}/memory/snapshots` `{ name? }` — §6.3 manual snapshot (409 while
  live, 422 when memory is empty) · `PATCH /automations/{id}/memory/snapshots/{snapshot_id}`
  `{ name }` — rename; null/"" clears · `POST /automations/{id}/memory/snapshots/{snapshot_id}/restore`
  — §6.3 restore (409 while live) · `DELETE /automations/{id}/memory/snapshots/{snapshot_id}` —
  delete the snapshot; unknown `snapshot_id` answers 404
- `POST /tests` `{ automationId?, draft, enabledAgents?, allowedSecrets?, paramValues?, triggerMock?, stepsFingerprint? }`
  → `{ executionId }` — the §11 Test: starts a §4.5 **test execution record** of the sent draft's
  steps (§4.5 kind `test`, trigger kind `test` — serialized as `test: true`, `versionLabel: "Test"`,
  `trigger: "Test"`; a stale `automationId` answers 404; 409
  while a test for the same draft container is executing; starting a test deletes the
  container's previous test record). Scratch memory is copied to a temp dir — when `automationId`
  is given, from its `draft/memory/` if present else its memory dir, else from the pending
  slot's `memory/` if present else empty — and discarded at test end. Grant arrays as in
  `/drafts`; param resolution uses the automation's stored values when `automationId` is given
  (else the draft's defaults), with `paramValues` (name → value, §5 matching rules) overriding
  on top for this test only — never stored; the resolved values are snapshotted on the
  record. `triggerMock` is the §11 mocked trigger message:
  `{ kind: discord | imessage, text, sender, channel?, secret? }` — 422 unless `text` and
  `sender` are nonempty strings, and for discord `channel` is a nonempty ASCII-digit string
  and `secret` a uuid string (the §4.3 discord trigger rule — the §4.8 secret id; iMessage
  takes no extra
  fields — `sender` is the handle). The backend builds the §4.5 payload from it (fields it
  can't truthfully supply are null — discord `channelName`/`guildName`/`guildId`/`messageId`,
  iMessage `chat`/`messageId`; `at` is the test start) and stores it on the record: the
  trigger kind stays `test` (`versionLabel`/`trigger` still serialize "Test"), but `triggerSender`
  and every payload surface fill like a real message execution, and §6.1 `reply()` becomes
  callable (§6.1 mocked-payload rules). Progress, logs, and the result flow over the ordinary `execution.*` events and
  `/executions/*` endpoints; cancel and skip-step are `POST /executions/{id}/cancel` and
  `/skip-step` like any execution (retry answers 409 — the draft may have changed). A
  failed execution is **not** analyzed automatically — and there is no analysis endpoint:
  failure analysis is an ordinary `chat` drafting job whose §8 RECENT EXECUTIONS context carries
  the run's error and log tails (the §11 canned analyze messages; Fix-with-AI names the
  execution via the `/drafts` `executionId` field). A finished test writes the §11 last-test summary
  (`test.yaml`, §5) into the draft container; it rides the draft payload as `test`
  ({ status: succeeded | failed, when, executionId, stepsFingerprint }) on the automation's
  `draft` object and on `GET /draft/pending`. `stepsFingerprint` is the §11 stale-outcome
  key: an **opaque string the renderer computes** over the draft's steps (files + code) and
  sends with `POST /tests`; the backend stores it verbatim (`steps_fingerprint`, written only
  when sent) and echoes it back, never computing or comparing it — null when the summary
  carries none.
- `POST /packages/check` `{ packages: [{ pip, import }] }` → `{ packages: [{ pip, import,
  status: installed | missing, version? }] }` — the fast §6.2 installed-check, never runs
  pip; `version` is the real installed version, present when installed (backs the §11
  Packages card's page-load check) · `POST /packages/install` (same body) →
  `{ packages: [{ pip, import, status: installed | failed, version?, error? }] }` — the §6.2
  ensure, blocking; installs only what's missing, one pip run at a time process-wide (backs
  the §11 Install/Retry button) · `POST /packages/outdated` (same body) → `{ packages:
  [{ pip, import, latest? }] }` — read-only PyPI query (§6.2: newest stable non-yanked
  version with a compatible wheel); `latest` present only when newer than the **installed**
  version, absent when not installed or on any lookup failure (backs the §11 page-load update
  check) · `POST /packages/update` `{ packages: [{ pip, import }] }` → `{ packages: [{ pip,
  import, status: installed | failed, version?, error? }] }` — `pip install --upgrade` for
  each named distribution in the shared directory (§6.2: wheels only, serialized); no
  manifest writes; a malformed name → 422
- `POST /drafts` `{ mode: chat|sync, automationId?, text?, spec?, current?, chat?, executionId?,
  agentId?, enabledAgents?, allowedSecrets? }` → `{ jobId }` — `chat` requires a nonempty
  `text` (422 otherwise), takes the in-editor draft as `current` (name + description + spec +
  params + steps + instructions + notes + concurrency; in chat mode with an `automationId`, absent `name`/`description`
  fall back to the stored automation's for the §8 AUTOMATION section, and absent
  `concurrency` falls back to the stored automation's — else the 1/0 defaults — for the
  §8 CURRENT-concurrency section) plus
  `chat` (the recent §11 thread entries for the §8 CONVERSATION section — the editor sends
  only entries after the newest §4.4 boundary marker, and the backend clips at the newest
  boundary again before building the section, §8); the backend
  assembles the §8 RECENT EXECUTIONS and PACKAGES context itself (`executionId`, optional, names an
  execution to include in full detail — the §11 Fix-with-AI entry; unknown ids are
  ignored), and the terminal
  payload is `draft: { answer?, answerKind?, spec?, instructions?, notes?, actions? }` — the §8 chat call's
  response shape decides which keys are present (`answerKind: "question"` when the response
  declared the §8 `===QUESTION===` type; absent otherwise); an `automationId` that doesn't resolve
  answers 404 (like the stale-`automationId` 404 on `/tests`) — never a silent fall-back to
  the no-automation grant defaults below; the job's agent is the explicit `agentId` when sent,
  else the default agent — 404 when neither resolves to a configured agent (including the
  zero-agents case); every job is stamped with its **owner** — the sent `automationId`'s
  draft container, or the pending slot when none was sent — so the draft-settle endpoints
  (above) can cancel the owner's still-building jobs when the draft settles; the grant arrays (`enabledAgents` agent ids, `allowedSecrets` secret ids — like the
  automation's stored grants), when present, override
  the stored automation's for the §8 grants context; when `enabledAgents` / `allowedSecrets`
  is absent and no stored automation exists (no `automationId` sent — a fresh create-flow
  draft), the
  agents grant defaults to **all**
  configured agents and the secrets grant to **all** stored secrets — matching the all-on
  seeds the Review page starts from; a chat or sync body carrying no `automationId` and no
  `current.instructions` gets the §8 default build instructions substituted into the
  prompt context (belt-and-braces — the editor normally seeds and sends them); clients track progress by polling
  `GET /drafts/{jobId}` → state (`status`, `stage`, live §8 `detail` line, the §8 `events`
  activity feed — each entry stage-stamped, §8 — plus the §8 stage-timing stamps:
  `stageTimes` (one `{stage, time}` per stage entered) and `endedTime` (epoch seconds,
  `null` while building), backing the §11 per-step durations; on a chat job that flipped stages, `plan` —
  the §8 pre-marker prose, set at the flip so the §11 thread lands "The plan" mid-job) +
  validated §8 draft payload, its `draft.steps` in the §4.1 API serialization
  (`noTimeout` / `infiniteRetries`, the same `step_json` shape as `GET /draft/{owner}`;
  the §8 manifest's snake_case never reaches a client, so the §11 editor applies a settled
  sync's steps unchanged and the §9.2 clock / retry tags read them directly); a `blocked` job's state is
  `blocked` and it carries the §8 `blockers` list — each entry `{ reason, fix, details?,
  kind? }`, `kind` only ever `user-action` (§8) — plus `blockedAt: steps | chat` (`steps`
  on a sync call; a blocker response's optional `notes.md` — §8 — rides the payload as
  `draft.notes`, applied by the editor like a chat notes rewrite); a `blocked` job whose blockers came from the §8 build-diagnosis call
  (or its deterministic fallback) additionally carries `diagnosed: true`, and `failed` is
  reserved for harness errors and crashes — a validation double-failure always ends `blocked`
  (§8 failure policy); `DELETE /drafts/{jobId}` cancels
  (kills the harness process). **Background continuation** — a job's lifetime is its
  owner draft's, never its poller's (the same rule as the §11 draft test): a building job
  deliberately survives losing its audience — leaving the §11 editor, closing the window —
  and there is no unpolled reap; the §8 idle window and wall-clock hard cap are what bound
  a runaway call. A job that settles unobserved **holds its terminal state** until it is
  consumed: `POST /drafts/{jobId}/ack` is the consume — the §11 editor calls it after
  applying **any** settled job's outcome, a live settle exactly like a re-attach one
  (an unacked live settle would resurface as a held outcome on the next editor entry
  and re-apply), and the backend drops the job record (unknown id answers 404; a
  still-building job answers 409 — only terminal jobs are consumable). A held record is also dropped when the
  owner's draft settles (the draft-settle endpoints' owner cancel above) and when a new
  `POST /drafts` job starts for the same owner (one held outcome per owner — superseding
  is consuming). Job records live in memory only: a backend restart loses them, and the
  §11 re-attach reconciliation marks the orphaned turn cancelled rather than leaving it
  looking unanswered. Backend shutdown still cancels every still-building job (§3), so a
  stopping backend never leaves an agent harness running. For re-attach, the
  `GET /draft/{owner}` envelope (both owners) carries a top-level
  `job: { jobId, status, mode }` beside `draft`/`agentId` while the owner has a building
  or held job (absent otherwise — and deliberately on the envelope, not inside `draft`:
  a first message still in flight may have landed no draft at all). A `chat` job additionally echoes,
  as `sentTriggers`, the resolved trigger list its §8 CURRENT-triggers section was
  rendered from (the §4.3 entries, exactly as sourced for the prompt), so a re-attach
  apply can prove the base list that `triggers` ops index is still the one the agent saw
  (§11 — on any difference the ops are dropped, never applied to a changed list).
- `GET /executions?automation=&status=&limit=&beforeStartedMs=&beforeId=` →
  `{ executions, total }` (headers only — no steps; rows carry the §4.5 `triggerSender`),
  sorted in the §7 canonical order: `startedMs` desc, id asc on ties, the §5
  §7 canonical order (`startedMs` desc, `id` asc on ties — computed over the in-memory
  headers; the §5 `executions.db` index only seeds that set at startup). `automation`
  filters to one automation id (exact).
  `status` filters to one §4.6 execution status, or the literal `finished`, which matches
  every terminal status (everything but `queued`/`executing`); an unknown value answers 422
  naming the vocabulary, never an empty list. `limit` (optional int ≥ 1; anything lower
  answers 422) caps the returned rows; omitted means every match - §20 reference resolution
  reads the uncapped list, while the §7 page always sends 50. `total` counts every match
  regardless of `limit` and cursor - it is what sizes the §7 pager readout.
  `beforeStartedMs` + `beforeId` are the §7 keyset cursor: only rows strictly after that
  `(startedMs, id)` position in sort order, so a page stays stable while new executions land
  above it; one without the other answers 422 (never a silent default - the same rule as the
  log route's step/attempt pair) ·
  `GET /executions/{id}` (steps
  with attempts + params + error + result + `triggerPayload` + the `workspace` / `logs` dir
  paths (§4.5) — logs are lazy, never inline) ·
  `GET /executions/{id}/logs?step=&attempt=&tail=` → `{ lines: [{time, kind, sequence, text}] }` — both
  `step` and `attempt`
  select that step attempt's file, neither selects `logs/execution.ndjson`, one without the
  other answers 422 (never a silent default), a missing file
  answers empty lines. `tail` (optional int ≥ 1; anything lower answers 422) keeps only the
  last `tail` lines of the selected log, same response shape - the §7 log views send it so a
  multi-thousand-line file never has to cross the wire whole ·
  `GET /executions/{id}/result/{name}` (raw result-dir file for the §7 file views; plain
  filenames only — no path traversal) ·
  `POST /executions/{id}/cancel` (a running execution is killed per §7; a §6 `queued` one leaves
  the queue and finishes `skipped`, with its sender told — the same endpoint covers both, and
  the queue check and the live check share one lock so an entry promoted between the click and
  the call can never be cancelled twice or missed by both) ·
  `POST /executions/{id}/retry` (§7 in-place retry; 409 unless failed and not live) ·
  `POST /executions/{id}/skip-step` `{ index }` (§7 skip; 409 unless that step is executing)
- `GET/POST /agents` · `PATCH/DELETE /agents/{id}` · POST and a PATCH whose merged result
  would change the agent's effective §4.7 grant name both enforce the §4.7 uniqueness rule —
  a case-insensitive collision with another agent's grant name answers 422 ("an agent named X
  already exists - agent names must be unique"); a PATCH that leaves the grant name unchanged
  never runs the check, so unrelated-field edits of pre-existing duplicates keep working ·
  `POST /agents/{id}/check` (health/badge)
  and `POST /agents/check-harness` `{ harness, mode?, model? }` (the same check before an agent
  record exists — onboarding's found-card auto-check) — one shared readiness check
  (`harness.check_ready`) decides ready vs. needs-setup everywhere: the harness binary must
  resolve (rule below). A custom-model agent (mode `custom`, §4.7) checks exactly like a
  default-mode one — the typed model string is never validated by the check (§4.7); a wrong
  name surfaces at invoke time. A local-model agent (mode `ollama` — Claude Code, Codex, or
  OpenCode, §4.7; the check answers needs-setup for Gemini CLI) additionally
  needs Ollama's server answering **and the agent's model installed** (the model appears in
  `/api/tags`; a bare name without a tag matches its `:latest` variant) — and needs **no**
  sign-in: a local model needs no account. A Claude Code local-model check additionally
  requires **Ollama ≥ 0.14.0** (the `version` from `/ollama/status` below) — the
  Anthropic-compatible `/v1/messages` endpoint Claude Code talks to (§6) shipped in 0.14.0,
  so an older Ollama reads needs-setup rather than failing at invoke time. An OpenCode
  local-model check additionally runs the opencode.json provider sync below (an unwritable
  config is a needs-setup condition, never a 500). Every default-mode check instead requires
  the harness to be signed in, by the per-harness rule below.
- **Sign-in state, per harness** (shared by `check_ready`, detection, and the signin poll):
  Claude Code — `claude auth status` exits 0 · Codex — `codex login status` exits 0 ·
  Gemini CLI — `~/.gemini/oauth_creds.json` parses as JSON carrying a refresh token (or
  `GEMINI_API_KEY` is set in the backend's environment — an API key counts as signed in) ·
  OpenCode — `~/.local/share/opencode/auth.json` parses as a JSON dict holding at least one
  known provider entry with a non-empty credential. File existence alone never counts: an
  empty, unparseable, or credential-less file reads signed-out — a stale artifact must not
  fake a working sign-in. Ollama is not a sign-in provider (no account; `POST /agents/login`
  answers 409 for it).
- `GET /agents/detect` (§10 detection) → one entry per harness, **all four always present**:
  `{ id, name, installed, signedIn, detail }` — `signedIn` is `true`/`false` by the rule
  above; `detail` is the real version/sign-in line rendered on §10 cards
  (never a fabricated "signed in" claim). Ollama state is not part of detection — the §10
  Free local AI card reads it from `GET /ollama/status`.
- **Install** — `POST /agents/install` `{ id }` starts a background install of that provider
  (409 while one is already running for the same id) and streams `harness.install` WS events.
  Install and sign-in help (`POST /agents/login`, below) are both gated on the §2
  `agentInstall` capability: the channels below and the Terminal sign-in flow are
  macOS-shaped, so where the flag is false (every other OS today) each endpoint answers 409
  with a plain line naming the OS — "Installing agents from Autowright isn't supported on
  Windows yet — install <name> by hand." — and clients hide the actions via `/health`
  capabilities (detection, sign-in *state*, and every other agent endpoint keep working; only
  the two endpoints that would run a macOS install or open Terminal.app degrade).
  Event shape:
  `{ id, line, percent?, done, ok?, error? }` (determinate UI bar only when `percent` is present).
  One install renders as **one** continuous bar: `percent` never decreases across the install's
  phases, and download events carry the bare step label in `line` ("Downloading Codex") with the
  number riding only `percent` — post-download steps ("Unpacking…", "Starting the Ollama
  server…") keep the bar where it is while the step label explains the wait;
  `GET /agents/install/{id}` → `{ state: idle | running | done | failed, percent?, line?, error? }`
  lets a remounted UI reattach. A 15-minute wall-clock cap applies to each install phase
  (installer subprocess run and download): on expiry the job fails with a timeout message —
  it can never sit `running` forever and block retries. **Install-location principle:**
  every install lands exactly where the user's own manual install would put the tool — a
  standard user location, never a directory private to Autowright — and leaves it reachable
  from the user's terminal, never isolated inside the app. Every install whose bin lands in
  `~/.local/bin` (Claude Code's and Codex's vendor scripts, Gemini CLI's npm `--prefix`,
  Ollama's CLI symlink fallback) closes with a **terminal-access guarantee** — needed even
  for the vendor scripts, which place the binary there but at most print PATH instructions
  nobody sees in a background install: when the bin
  landed in `~/.local/bin` and that dir isn't already on the login shell's PATH (probed via
  `$SHELL -l -c`), a guarded `export PATH="$HOME/.local/bin:$PATH"` line under an
  `# Added by Autowright` marker comment is appended to the user's shell profile
  (`~/.zprofile` for zsh, `~/.bash_profile` for bash, `~/.profile` otherwise — profile
  files, not rc files, because macOS terminals start login shells and PATH is environment;
  skipped when the profile already mentions `.local/bin`) — the same PATH setup the vendor
  scripts perform for their own bin dirs. When the line is appended, the streamed install
  line says so and tells the user to open a new terminal (already-open shells can't be
  fixed). Best-effort: a profile failure never fails the install.
  Channels, per provider — each
  vendor's own suggested install method, never sudo, never Homebrew (a vendor script adding
  its bin dir to the shell profile is vendor behavior we accept):
  Claude Code — the official installer script (`curl -fsSL https://claude.ai/install.sh |
  bash`), lands in `~/.local/bin/claude` (the script only prints PATH instructions, so the
  terminal-access guarantee closes the install), indeterminate ·
  Codex — the official installer script (`curl -fsSL https://chatgpt.com/codex/install.sh |
  sh`) with `CODEX_NON_INTERACTIVE=1` (the backend has no TTY to answer its "Start Codex
  now?" prompt); versioned payloads live under `~/.codex/packages/standalone` with a `codex`
  symlink in `~/.local/bin` (closes with the terminal-access guarantee), indeterminate ·
  Gemini CLI — `npm install -g --prefix ~/.local @google/gemini-cli` (npm is Google's only
  official channel; the `--prefix` is ours, keeping the install sudo-free — bin lands in
  `~/.local/bin`, and the install closes with the terminal-access guarantee above); without
  `npm` on this Mac the install fails fast with "Gemini CLI needs
  Node.js — install it from nodejs.org first, then try again."; npm runs with the augmented
  PATH below so its `#!/usr/bin/env node` shebang resolves; indeterminate ·
  OpenCode — the official installer script (`curl -fsSL https://opencode.ai/install | bash`),
  lands in the script's own default `~/.opencode/bin` (already on the fallback bin-dir list
  below; the live script ignores its documented `OPENCODE_INSTALL_DIR`, so no env is passed),
  indeterminate ·
  Ollama — the official Mac app: `Ollama-darwin.zip` from `https://ollama.com/download`
  (the exact payload the vendor's own install.sh ships), unpacked with `ditto` and moved to
  `/Applications` (or `~/Applications` when /Applications isn't writable); vendor-script
  parity: a running Ollama app is quit and an existing `Ollama.app` replaced first. The
  CLI symlink to the app's `Contents/Resources/ollama` goes to the vendor script's own
  `/usr/local/bin` when that dir is writable without sudo (the exact manual-install
  location, already on every PATH), else to `~/.local/bin` with the terminal-access
  guarantee above — either way `ollama` works from the user's terminal. The app is then
  launched hidden (`open <app> --args hidden`) — its menu-bar agent owns the server and
  auto-updates — and the install waits up to 30 s for the server to answer; determinate
  (Content-Length). Ollama installs only as a piece of the local-model setup (§10 Free local
  AI card, §12 local-model mode) — it is never a harness.
- **Sign-in help** — `POST /agents/login` `{ id }` → `{ ok, method: browser | terminal }`,
  only for harnesses that need an account and aren't signed in (409 otherwise): Codex — the
  backend spawns `codex login` detached (the CLI opens the browser and completes on its OAuth
  callback), method `browser` · Claude Code / Gemini CLI / OpenCode — their login flows are
  interactive TUIs, so the backend opens Terminal.app via `osascript` running the harness's
  login command (`claude /login` / `gemini` / `opencode auth login`), method `terminal`.
  The Terminal command `cd`s into the provider's empty `harness/<provider-id>/workspace/`
  dir (§6) first — Terminal shells
  otherwise start in `~`, and the CLI's startup scan must not walk the home folder.
  `GET /agents/signin/{id}` → `{ installed, signedIn }` is the cheap poll (§10 waits on it
  every 2 s) — it runs only that provider's sign-in rule, never version lookups. For an
  ollama-mode agent `signedIn` is `null` — local models have no sign-in concept.
- Ollama: `GET /ollama/status` → `{ ready, installed,
  models, version }` (`version` from Ollama's `/api/version` when the server answers, else
  null — the §19 Claude Code local-model check gates on it), `POST /ollama/pull` — streams
  `ollama.pull` WS events `{ model, line, percent?, done, ok? }`. The pull rides the
  **server's `/api/pull` HTTP stream whenever the server answers** — never the CLI in that
  case. `/ollama/status` reports installed/active from the server answering, so a pull must
  succeed in exactly that state even when no `ollama` binary is resolvable (server reachable
  but the CLI missing from PATH and the app in a non-standard place — the "says active but
  pull says not installed" trap). Only when the server doesn't answer does the pull fall back
  to CLI `ollama pull` via the resolved binary; with neither, the terminal event is
  `ok: false` with a "Ollama isn't running" line (not "isn't installed" — the server URL may
  simply be down). `line` is the pull's status line (the HTTP stream's `status` field, or the
  raw CLI output line); `percent` is computed in the backend as **one overall pull
  progress**: byte-weighted across every layer seen so far in the stream (the HTTP stream's
  `completed`/`total` per digest, or the CLI's `… 2.3 GB/5.2 GB` byte counts), falling back
  to a bare `N%` in a CLI line when no byte counts parse, clamped monotonic (never decreases
  within one pull), and 100 on the ok terminal event. Raw pull streams restart their bar per
  layer (one multi-GB blob plus small metadata layers, then
  `verifying sha256 digest`/`writing manifest`) — those per-layer resets must never reach
  the UI as a bar reset, so clients render `percent` and never parse percents out of `line`.
  All CLI lookups (detection and harness invocation alike)
  resolve the binary via PATH plus the usual per-OS install locations —
  macOS: `~/.local/bin`, `~/.opencode/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, Ollama
  additionally `Ollama.app` under both `/Applications` and `~/Applications`;
  Windows: `%USERPROFILE%\.local\bin` (Claude Code's native installer uses the same
  `~/.local/bin` layout there), `%USERPROFILE%\.opencode\bin`, `%APPDATA%\npm` (npm's global
  bin — the Gemini CLI/Codex channel on Windows), Ollama additionally
  `%LOCALAPPDATA%\Programs\Ollama` (its per-user installer's location). The
  fallback-dir *executable* check is per-OS too: `os.access(X_OK)` on POSIX, existence with a
  `PATHEXT` extension (`.exe`, `.cmd`, `.bat`, …) on Windows, where the execute bit does not
  exist (`shutil.which` already honors `PATHEXT` for the PATH half). A binary that resolves
to a `.cmd`/`.bat` shim on Windows (the npm global channel — `gemini`, `opencode`) is
spawned as `[%COMSPEC%, '/d', '/c', <resolved path>, …]`, never as the bare script: `/d`
skips cmd.exe AutoRun hooks, whose output (a machine may register one under
`HKLM\…\Command Processor\AutoRun`) would otherwise land on the child's stdout and corrupt
probe and invocation parsing — the same hazard the §15 test conftest and §18 dev loop
already neutralize. The provider config and
  credential paths need no per-OS fork: OpenCode keeps `~/.config/opencode/opencode.json` +
  `~/.local/share/opencode/auth.json` and Gemini keeps `~/.gemini/oauth_creds.json` on native
  Windows as well — `expanduser` resolves them under the user profile. The fallback dirs exist
  because a GUI-launched backend gets a minimal PATH on macOS — e.g. `claude` installs to
  `~/.local/bin` by default (on Windows a GUI app inherits the full user PATH, §2, so the
  fallbacks are belt-and-braces there). Invocation uses the resolved absolute path, and every provider child the backend
  spawns (harness invocations, installs, version/status probes, login helpers, `ollama` pulls)
  runs with PATH prepended with those same install locations plus the resolved binary's own
  directory — otherwise `#!/usr/bin/env node` launchers (`npm`, `gemini`) fail with
  `env: node: No such file or directory` under the GUI minimal PATH even when Node is
  installed. If Ollama is installed but its server isn't
  answering (and `AUTOWRIGHT_OLLAMA_URL` is local), `/ollama/status` starts `ollama serve`
  once per backend process and waits briefly for it to come up — so an installed Ollama
  reads as ready instead of prompting a fresh download. Before every OpenCode local-model
  use (readiness checks and invocations alike), the backend syncs the Ollama provider entry
  into `~/.config/opencode/opencode.json` (merge, never overwrite: provider `ollama` via npm
  `@ai-sdk/openai-compatible`, `baseURL` = `AUTOWRIGHT_OLLAMA_URL` + `/v1`, the agent's model
  listed under `models`) so `opencode run --model ollama/<model>` resolves.
- `GET /secrets` (each entry: `id` — the §4.8 uuid every reference binds by — + name +
  `description` + `set` + usedBy — `{ id, name }` automation entries — never
  values) · `POST /secrets` `{ name, value, description? }` — create: validates the name
  (§4.8 rule, 422 otherwise) and its uniqueness (422 "a secret named X already exists" when
  any stored secret holds it), mints the id, and returns the serialized secret entity (same
  shape as a `GET /secrets` entry) so the client learns the id. A blank value creates a §4.8
  placeholder (`set: false`) · `PUT /secrets/{id}` `{ value, description? }` — edit: the name
  is immutable and not a field; unknown id answers 404. `description` is
  presence-sensitive (`exclude_unset`): absent leaves the stored description untouched, sent
  (even blank) sets it. A blank value keeps the stored state (§4.8) and edits only the
  description — the presence rule is what makes that read. Returns the serialized entity;
  both writes answer 503
  when the Keychain refuses the write · `DELETE /secrets/{id}` (unknown id 404) ·
  `DELETE /secrets` — delete **all** stored secrets in one call (backs the §3 reset flow
  and §20 `secret delete --all`): per entry a best-effort Keychain
  delete (§4.8 rule) then the metadata row goes; the sweep removes exactly the ids
  snapshotted at entry — `secrets.yaml` ends empty in the normal case, but a secret created
  while the Keychain deletes run keeps its row rather than becoming an orphaned value with
  no metadata — one `secrets.changed` event covers the sweep, and the answer is
  `{ deleted: <count> }` (0 with no secrets — never an error). Automations' `allowed_secrets` grants and step references
  are left as written, exactly like the per-id delete (dangling ids surface as §4.1
  `secret-missing` blockers). The unreadable-store guard below still answers 409 — the §3
  reset flow treats that as non-fatal and proceeds —
  values go straight to the Keychain (account = the secret's id, §4.8), never
  into responses or files. Routes are id-keyed (§4.8: the id is the reference identity;
  names exist for display and the create call)
- `GET /settings` · `PATCH /settings` (validates before storing: `days` must be an int —
  strict, no coercion, 422 otherwise — and is
  clamped ≥ 1, `notifications` must be `attention | all` — 422 otherwise, so a bad value can never
  persist and silently break the retention sweep; flipping `keepAwake` starts/stops the §3
  permanent power assertion immediately) · `POST /settings/data-path` `{ path }` (sets the
  execution-data location; creates the dir, reloads from it, moves nothing; answers 409 while
  an execution is in progress — it still writes into the old location — **or while a §6
  firing-queue entry is waiting**: the in-memory queue would not survive the reload, so the
  entry would neither execute nor finish `skipped`, and its sender would never be told.
  The target — the chosen folder itself when its basename is already `executions`, else its
  `executions` child — must be **empty or already an Autowright executions dir**: every
  existing entry must be `executions.db`/`-wal`/`-shm` or a directory containing
  `execution.yaml`; dot-hidden files (`.DS_Store`) are ignored; anything else answers 422
  "that folder already has unrelated files in it — choose an empty folder". The store must
  own its directory exclusively: `dataSize` sums everything under it, the startup reconcile
  scans it, and the §3 reset deletes execution content from it — adopting a folder of
  unrelated user files invites both wrong numbers and grief, and pointing back at a
  previous Autowright location keeps working)
- **Unreadable-store guard (shared)** — any route whose write would rewrite a §5 top-level
  store file that failed to load this session (`settings.yaml`, `agents.yaml`,
  `secrets.yaml` — the §5 read-only degradation) answers 409
  "`<path>` is unreadable on disk — fix or remove the file, then restart Autowright."
  This covers the agents and secrets writes, `PATCH /settings`, and
  `POST /settings/data-path`. The §5.1 import routes are no longer among them: import
  never writes the agents or secrets stores (it creates no records), so an unreadable
  store file cannot block an import.
- `WS /ws?token=` — events, each `{ event, ... }`: `execution.started` (also re-published when a
  failed execution retries in place — same execution id, updated record), `execution.queued`
  (a §6 firing was admitted to the queue — carries the new `queued` record, so it reaches the
  §7 executions list and the §9.2 "N waiting" line without a poll; promotion needs no second
  event, it publishes the ordinary `execution.started` for the same id), `execution.step`
  (status change; carries the full step incl. its attempts), `execution.log` (one NDJSON line with
  `stepIndex`/`attempt` — null for execution-level lines — and the per-file `sequence` for
  fetch-vs-stream dedupe), `execution.finished`, `automation.changed`, `agents.changed`,
  `secrets.changed`, `settings.changed`, `draft.changed` (the §4.4 pending slot was kept
  or discarded — clients re-`GET /state`; §11 test executions stream over the
  ordinary `execution.*` events), `draftjob.changed` (`{ owner, jobId, status, mode }` —
  published when an owner-stamped §19 drafting job starts, settles, is consumed, or is
  cancelled; `owner` an automation id or the literal `pending`; `status` is the job's
  (`building` / `done` / `blocked` / `failed`) plus the two removal notices `cancelled`
  and `consumed`. Clients patch their `draftJobs` snapshot from it — `cancelled` and
  `consumed` remove the entry, everything else upserts it (a held outcome stays listed
  until consumed) — backing the §9.1 drafting notes without polling),
  `harness.install` (provider install progress — the payload shape lives on the Install
  bullet above), `ollama.pull` (model-pull progress). Entity payloads ride the events under camelCase
  keys matching the REST shapes: `execution.started`/`queued`/`finished` carry `execution`
  (the record header, §19 executions-list shape) plus `automation` — the owning automation in
  list shape, or `null` when there is no row to patch (§11 test executions; an automation
  deleted before `finished`) — so clients patch the row (`lastStatus`, `live`, `nextAtMs`)
  without a poll; the merge rule below applies. A non-test `execution.finished` additionally
  makes a client holding that automation's **full** record re-`GET /automations/{id}`: the
  full-only fields (`latest`/`memory`/`snapshots`/`versions`) never ride events, and this
  refetch is what moves the §9.2 LATEST RESULT card (and the memory/snapshot lists) to the
  finished run. Execution refetches are **monotonic**: a `GET /executions/{id}` response
  resolving out of order must never regress the stored record — when the stored status is
  terminal and the fetched body says `queued`/`executing`, the response is stale and the client
  drops it. The stored status is read from the full record **or, before any body has landed,
  the executions-list header**: readers resolve full-first, so a stale body written into the
  empty full slot would out-rank a header the finished event already moved to terminal. Without
  this rule the stale body re-runs every status-transition observer, e.g. doubling the §11
  test-settled chip. A drop must not lose the body outright: when the guard drops a
  non-terminal body and **no full record for that execution has landed yet**, the client
  schedules one fresh `GET /executions/{id}` so a full record still arrives - otherwise an
  execution page opened mid-run whose `finished` event outraces its first GET holds the
  header alone and renders permanently with zero steps. A §7 in-place retry legitimately returns a finished record to
  `executing`, but it announces that through `execution.started`, which this rule leaves alone. `automation.changed` carries `automationId` plus
  `automation` — the changed automation in list shape, or `null` when it was deleted —
  whenever exactly one automation changed; clients patch that one row in place by **merging**
  it over the stored record, never replacing it. The list shape lacks the full-record fields
  (`params`/`steps`/`latest`/`memory`/`snapshots`/`spec`/`packages`/`versions`/`draft`), so a replace
  would blank those fields on an open detail page — its sections unmount and remount around
  the follow-up full fetch, which reads as a page-refresh flicker, drops input focus
  mid-edit, and collapses the page height so the scroll position jumps to the top. A bare
  `automation.changed` (no `automationId`) means "many may have changed" (data-path switch,
  startup repair): clients fall back to re-`GET /state`, applying its list rows with the
  same merge. Clients also re-`GET /state` on reconnect, and that refresh applies the snapshot's `version` alongside the data fields — after the §3 launch-time version-sync restarts the backend onto the new bundle, this reconnect refresh is what carries the new running version to the §9.4 About page (without it the page shows the pre-update number until the app is relaunched). The handler streams from a hub queue while concurrently watching the socket for
  the client's disconnect, so a dropped client ends the handler immediately — an idle open
  socket never leaves uvicorn's graceful shutdown waiting.
