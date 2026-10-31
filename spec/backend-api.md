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
request model (`backend/autowright/models.py`) — a malformed body (missing fields, fields of
the wrong type — unknown field *names* are ignored, never a 422, so an older client's extra key
cannot break a newer backend — wrong shapes: `stepAgents`/`allowedSecrets` must be lists of strings, the settings
booleans must be booleans with `days` an int; a §4.4 draft's `spec` must be a list of
block objects, `notes` a string, `params` and `packages` lists) answers 422 before any
handler logic runs — and before any file is written, so a mistyped draft can never leave a
half-written version folder behind. A model-level 422 carries the same shape as a
handler 422: `detail` is one sentence (pydantic's per-field entries are flattened to
`<field>: <reason>` joined by `; `, with the location prefix — `body`, `query`, `path`,
`header` — stripped alike, so a query 422 reads `limit: …` exactly like a body one), so
clients render every 422 the same way.
The store-state cross-field checks live in the handlers, answering the same 422:
`paramValues` entries are checked against the automation's param definitions (names **and**
kinds), `agentId`/`stepAgents` entries must reference configured agents, and
`allowedSecrets` entries must reference stored secrets by their §4.8 id
(`_check_param_values` / `_check_agent_refs` / `_check_secret_refs` in `api.py` — they need
store state the models
never see). Pydantic shapes **requests only** — response bodies
remain plain dicts (§2).

- `GET /health` → `{ version, app, os, capabilities, pid }` (unauthenticated; used for
  discovery/liveness). It is served on the event loop (an `async` handler that touches
  no I/O), never on the request worker pool: a pool saturated by long blocking routes
  (package installs, imports, probes) must not make the liveness probe time out, or the §3
  ensure-backend step would read a busy backend as dead and bootout it mid-execution.
  `GET /instructions` is served the same way. `os` is the §5.1 platform token; `capabilities` is the §2 platform
  layer's flag set — `{ imessage, notifications, keepAwake, service, agentInstall }`, all
  true on macOS —
  and is the one surface clients gate platform features on (never by sniffing the platform
  at a call site)
- `GET /state` → boot snapshot: automations (full — the §4.1 memory card's size/updated
  stats are memoized per automation for 5 s, cleared by clear/restore and by every execution
  finish of the automation, so a reconnect burst
  never re-walks a gigabyte `memory/` under the store lock — and the walk itself **never
  runs under the store lock or on the request**: an expired or cleared memo answers the
  last known stats at once (a first-ever read answers `size` "computing…" with an empty
  `updated`) and starts one background walk per automation — and an invalidation (clear,
  restore, execution finish) itself starts that fresh walk whenever the record's stats
  were ever read, so a client whose finish-triggered refetch races the invalidation still
  gets the landing rather than waiting for its next refetch; when the walk lands with
  stats that differ from what was last served, the backend publishes the single-row
  `automation.changed` for that automation so an open detail page refetches and the
  MEMORY card catches up without a manual refresh (a walk that lands unchanged publishes
  nothing); the latest-result memo keys off
  one linear pass over the headers, never a sort), executions (the §7 window, not the full
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
- `GET /instructions` → `{ framework, build }` — the two §8 instruction files verbatim
  (the §8 per-OS placeholder resolved; backs the §11 Framework-instructions and
  Build-instructions cards)
- `GET /automations` (the **list shape** — the row every `automation.changed` event carries,
  without the full-record fields; the §20 reference resolution and the §13 tray poll read
  it, so it never serializes version bodies) · `GET /automations/{id}` (the full record) ·
  `DELETE /automations/{id}` — a second DELETE for an id whose delete is still in progress
  answers 409 "already being deleted" (never a second rmtree of the aside tree, which would
  run under the store lock); delete cancels
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
  delete flags the record in memory (and clears the flag again if the delete fails partway,
  so a transient write error never leaves an automation silently refusing every firing)
  and `engine.start` refuses admissions for it — a
  scheduler tick, listener dispatch, or app-start firing landing mid-delete would otherwise
  escape the wait set and re-create the tree after the rmtree. The mid-delete check and a
  drafting job's registration are one critical section: `POST /drafts` re-checks the flag
  when it registers the job and cancels it right there if a DELETE landed in the gap (its
  context build — log reads, RECENT EXECUTIONS — can take seconds), and `POST /tests`
  answers 409 for an automation being deleted rather than running step code during the
  delete window — re-checked under the same lock hold that registers the test record
  (inside the test start), so a DELETE landing between the route's check and the record's
  creation can't run step code or leave a record the delete's wait set never saw. Every
  path that settles a draft — `DELETE /draft/{owner}`, the version save
  `POST /automations/{id}/versions`, and `POST /automations` with `settlePending` — bumps
  the owner's **draft generation** through one shared settle helper (bump, then cancel);
  a `POST /drafts` that read the older generation before building its context is refused
  at registration (409, "the draft was discarded"), so a settle can never miss a job that
  was still being registered and later re-attach its outcome to the next draft. That
  refusal is decided **inside the registry's own lock, before the new job supersedes
  anything**: a stale request never cancels the owner's legitimate post-settle job nor
  consumes its held outcome on its way to the 409 (the mid-delete 404 is judged there
  too) — a refused registration is side-effect free for the fresh draft; a job that
  already settled by the time that check runs (a missing harness binary fails on its own
  thread at once) is dropped from the held outcomes right there, never left for the fresh
  draft to inherit (the
  scheduler's own
  one-shot consumption carries the same registered-object guard for the same reason, and
  so does **every store write into an automation directory** — patch, new version, restore,
  draft and chat saves, draft open, memory clear, the §6.3 snapshot operations: a write
  whose record is no longer the registered one answers 404 instead of re-creating the
  directory, so a mutation racing the delete can never resurrect the automation at the
  next boot). Removing a record from disk (this route, the memory clear, and internally
  the §7 retention sweep and the draft-test settle) moves the directory aside first;
  "nothing there" (`FileNotFoundError`) is the header-only case and completes normally,
  while any other OS error (a read-only or vanished volume, an open handle on Windows) is
  logged and the record stays registered — the routes answer 409 with `couldn't remove it
  from disk: <reason>`, the sweeps log and move on to the next record — never silently
  dropped from the index while its directory survives to be re-adopted at the next
  startup.
  If a thread somehow survives the wait, the directory is
  removed anyway (the step group is already hard-killed by then)
- `PATCH /automations/{id}` — user-owned fields only: name (a blank or missing name is
  ignored — a rename can never clear the name; a name another automation already holds,
  compared case-insensitively and excluding the automation itself, answers 422 "an
  automation named X already exists - automation names must be unique" and nothing is
  stored - §4.1 uniqueness, checked up front and **again under the store lock at the write**,
  so two concurrent renames to one name, or a rename racing a create's dedupe, can never both
  land; a case-only rename of the same automation is allowed), description (blank clears it — the description is
  optional, §4.1), triggers (the §4.3 list, replaced
  whole; entries keep their `id`, new entries get one assigned;
  cron/interval/time/app_start/discord/imessage
  kinds — a reserved kind (pubsub), an invalid cron expression, an invalid or out-of-range
  interval `every` (§4.3 interval dialect; `timezone` on an interval is ignored, never
  stored), an unknown `timezone`, a
  non-boolean `runIfMissed` (§4.3; ignored on kinds other than cron/interval/time), a
  non-boolean `enabled` (a string `"false"` must never store a trigger switched on), a
  past `time`, a `time` whose `at` carries a UTC offset (the zone belongs in `timezone`; naive
  local ISO only), a second `app_start`, or a discord/imessage entry failing the §4.3 field
  rules
  answers 422 and nothing is stored; a cron or interval entry's §4.3 `source` is required and must be `"spec"`
  or `"user"` (422 otherwise — absent included) and is stored as sent; an interval's `every`
  is stored in the §4.3 canonical form, whatever spelling arrived;
  serialized discord and imessage triggers carry the
  derived §4.3
  `connection` state), param
  values, agentId, stepAgents, allowedSecrets, snapshotSettings (the §6.3 automatic-snapshot
  toggles — partial object, sent keys merged over the stored ones), maxParallel (int ≥ 1) and
  maxQueued (int ≥ 0) — the §6 concurrency settings; a non-integer or out-of-range value
  answers 422 and nothing is stored — and `notifications` (the §4.1 override:
  `"attention" | "all" | "never"`, or `null` to clear it back to inherit; any other value
  answers 422). Lowering `maxParallel` never kills a running execution:
  the automation simply admits nothing new until it is back under the limit. Lowering
  `maxQueued` below the number already waiting keeps those entries — the cap governs admission,
  not eviction (§6).
- `POST /triggers/preview` `{ triggers: [trigger, …] }` → `{ triggers: [{ valid, error?,
  label, short, nextAtMs, nextLabel? }, …] }` — a **pure function endpoint**: no state read or
  written. Validates and labels a list of §4.3-shaped trigger dicts (kind plus its stored
  fields, `timezone` where relevant) with the same `triggers.py` code that gates the PATCH,
  answering one result per request entry in order. `valid` says whether the entry would
  store; `error` is the plain-word reason otherwise ("a cron expression needs 5 fields
  (minute hour day month weekday)", "an interval must be at least 15 seconds", the
  §4.3 field rules) — an invalid entry is a `valid: false` result, never a 422 (the editors
  preview half-typed state); only a body that isn't a list of trigger dicts gets the
  ordinary 422. `label`/`short` are the §4.3 display strings; `nextAtMs` the next-occurrence
  epoch ms (null when the kind has no computable next — app_start and message triggers —
  or the entry is invalid or elapsed; for an interval, `now + every` — the endpoint has no
  execution state, so it anchors at the request moment, §4.3) and `nextLabel` its "Jul 20, 3:00 PM"-style moment
  label. The renderer keeps **no local trigger-math mirror**: the §9.2 Add-trigger editor's
  live preview line, the §11 draft-trigger chips, and every "next trigger" label read from
  this endpoint — trigger math exists once, in `triggers.py`.
- `POST /automations/{id}/execute` `{ version?: "vN" | "draft" (case-insensitive),
  trigger?: "manual" | "menubar" (§4.5 kind, default "manual"; anything else answers 422),
  queue?: bool (default false) }` →
  `{ executionId, queued: bool }`. `queue` absent/false: 409 when every §6 `maxParallel`
  slot is taken — a plain manual start is refused, never silently queued. That
  no-free-slot 409 is the only one whose body carries `reason: "capacity"` beside
  `detail` — the §7 busy toast keys on it; every other 409 this route answers (the backend
  shutting down, the automation being deleted, a full queue, a Draft queue attempt) carries
  `detail` alone and clients show that detail verbatim. `queue: true`
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
- `POST /imessage/permissions/automation-probe` → `{ automation: "granted" | "denied" | "unknown" }` —
  fires a benign Apple Event at Messages.app (`osascript`: count chats), which makes macOS
  show the Automation consent prompt if the user has never answered it (and may launch
  Messages.app); the result updates the remembered `automation` state above. Called by the §9
  checklist's Grant button; blocks until the user answers the prompt, up to 300 s — a prompt
  still unanswered then answers `unknown` and leaves the remembered state untouched (a
  timeout is not a denial)
- `POST /automations` `{ draft, name?, agentId?, stepAgents?, allowedSecrets?, paramValues?, concurrency?, settlePending? }` → the new
  automation's bare §4 automation JSON — create v1 from the sent draft (the §11 Create
  button). `settlePending` (default `true`) is the §11 Create button's behavior described
  at the end of this entry — consuming the pending create-mode slot; `false` (the §20 CLI
  `create`, which is unrelated to whatever the user is drafting in the app) leaves the
  pending slot, its chat, its building job, and its draft test untouched and migrates
  nothing. The draft must hold steps (422 otherwise); its `triggers` list is validated and
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
  entirely**, the store seeds it with the authoring agent (`[agentId]`, empty without one) —
  an explicit empty list lands empty. Success consumes the §4.4
  pending create-mode slot (`<root>/draft/` is emptied on success), first migrating the
  slot's `chat.jsonl` into the new automation's container and appending the §4.4
  "Created as v1." boundary marker there (thread-lifetime rules, §4.4) — all of it only
  with `settlePending` true
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
  version is minted (a current step modified on disk, §4.1 `step-modified`, never compares
  equal: the save always mints, validates the edited script, and seals it); the triggers replacement, `paramValues`, `concurrency`, and the identity/grant patch
  still apply, and the response is `{ version, automation }` either way — `version` the
  minted number, or the unchanged current one on an operational-only save (the editor
  compares it to tell the two apart); restore answers the same shape. Saving settles the
  draft (the container is deleted) and appends the §4.4 boundary marker to the
  automation's thread — "Draft saved as vN." when a version was minted, "Changes saved —
  no new version." on the operational-only save
- **Step field typing** — the request models type every step field (`name`, `code`,
  `description`, `file` strings; `timeout`, `retries` integers; `params`, `steps` lists;
  each step a mapping), so a wrongly typed value is a 422, never a `KeyError`/`TypeError`
  500 from the manifest writer or a half-written version folder; the on-disk readers
  (`step_json`, the manifest entry builder, the version diff, restore) read those fields
  leniently, so a hand-edited stored version with `timeout: abc` degrades to the default
  instead of a 500.
- `POST /validate-steps` `{ draft, automationId? }` — the §8 step validators (the same pass the save
  endpoints run, below) over a draft-shaped body, answering **200** `{ errors: string[] }`
  — an empty list means it would save; validation failures are the response body, never
  a 422, because the caller is asking, not saving — and that holds for a mistyped step
  field too (`timeout: "60"`, `retries: 2.5`, `locked: "yes"`): the request-model error
  is rendered into the list as `step N: <field> <message>` rather than refusing the ask,
  since the §20 `automation validate` verb exists to check a hand-edited manifest
  (2026-10-30; the one route exempt from the §19 wrong-type 422). It writes nothing. Backs the §11 step
  editor's Save (which shows the lines inline) and the §20 `automation validate` verb; the
  step keys `authoredBy` / `locked` ride the body like every other step field (§4.1).
  With an `automationId` the pass reads that automation's context exactly as its save
  would (the §5.1 `unresolved_references` wording for an imported reference), so the
  editor's strip and `automation validate` match a refused save word for word; a stale id
  answers 404.
- **An unreadable current version** (its folder failed to load, §5) answers 422 "the
  current version can't be read — restore an earlier version" from export, and 409 with
  the same wording from `POST /drafts` (sync without a `current`) and duplicate — never
  a 500 (2026-10-30). Execute and retry answer 404 only for a version label that doesn't
  resolve (the §7 label grammar, the same `vN` parser the diff route uses — bare `3`
  or `vv3` is not a label); a retry whose version vanishes between the route's pre-check
  and the engine's own lookup answers the pre-check's 409 (the engine raises
  `VersionNotFound`), and one whose record body can't be read (`execution.yaml` gone or
  unreadable) answers 404 (2026-10-30 second sweep: both were a 500); any other lookup
  failure inside a start is an internal error.
- **Server-side step validation** — `POST /automations` and `POST /automations/{id}/versions`
  run the §8 step validators (`ast.parse`, the §6.2 import allowlist, manifest schema and
  step-file ordering, §4.1 step-name uniqueness, the timeout/retry rules, the §8 rule-6/7 id checks — step `agents:` /
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
  §5 version-folder writer (manifest last as the commit point — never a tree copy). The
  restored content is the stored content, unchanged and unvalidated (2026-10-27, replacing
  the one-day rule that ran the save validators: a version written under older rules, or
  one referencing an agent or secret deleted since, must stay restorable — the §4.1
  `problems` audit reports what no longer resolves, as it always did), with one gate: a
  version holding a step modified on disk (§4.1 `step-modified`) answers 422 ("v3's step
  `X` was edited on disk — pull and push it instead"), so a hand edit never becomes a
  validated-looking new version by way of a restore
- `GET /automations/{id}/diff?from=vX&to=vY`: the §9.2 version diff, what changed between
  two stored versions, computed here (stdlib `difflib`, `versions_diff.py`) so the UI and
  the §20 CLI render one result and neither diffs. `from` / `to` are "vN" labels
  (case-insensitive, as execute's `version`), each must name a stored version (404
  otherwise; the current version included), and they must differ (400). Answers
  `{ from: X, to: Y, files: [...] }`, one entry per **file of the §5 version folder**, in
  the order the §9.2 navigator lists them: the manifest, the spec, the notes, then the
  steps in the `to` version's order with the `from`-only steps appended (steps matched by
  name, k-th of a name to k-th). A file entry is `{ kind: "manifest" | "spec" | "notes" |
  "step", name (the navigator title: "Manifest" / "Spec" / "Notes" / the step name), file
  (the folder filename: `automation.yaml`, `spec.md`, `notes.md`, the step's `file`),
  status: "unchanged" | "changed" | "new" | "removed", added, removed (line counts), rows:
  [{ kind: "same" | "add" | "del" | "mod", left: { number, text } | null, right: { number,
  text } | null }] }`; a **step** entry also carries `moved: { from, to } | null` — the
  step's 1-based positions in the `from` and `to` versions when it is matched on both
  sides and its **relative order among the steps both versions hold** changed (the moved
  steps are those outside the longest in-order run of the common steps, so adding or
  removing a step marks nothing moved while a step dragged from 1 to 5 marks that step
  alone — a §11 reorder), `null` otherwise (and absent on the three
  documents). `status` is unaffected: a moved step with an identical script stays
  `unchanged`. The text compared: for the **manifest**, the versioned manifest
  fields the §5 writer emits (`params` definitions, `packages`, the step entries with
  their `file`, `name`, `description`, agent / secrets / packages / time-limit / retry
  fields) dumped as YAML through the same `save_yaml` dump, minus the `when` / `note`
  metadata that differs by construction, so the manifest side reads as the on-disk
  `automation.yaml` would; the **spec** as `spec.md`'s markdown (§4.1 blocks rendered
  back through the writer's `blocks_to_md`); the **notes** as `notes.md`; a **step** as its
  script. A single trailing newline is stripped before splitting, and empty text is zero
  lines. Rows come from `SequenceMatcher` opcodes with `replace` blocks paired
  line-for-line into `mod` rows (the leftover of the longer side as `del` / `add`), so a
  side-by-side renders straight from the list; `added` / `removed` count the `add` + `mod`
  and `del` + `mod` rows. A file identical on both sides is still listed (`unchanged`,
  every row `same`), a file on one side only is `new` / `removed` with every row `add` /
  `del`; a file absent on both sides (no notes in either version) is `unchanged` with no rows.
  The two version records are taken under `store.lock` (a concurrent save never yields a
  half-read pair) and the diff itself is computed **outside** it — versions are immutable,
  and difflib over every file must never stall firings or `/state`. A label is exactly `v`
  followed by digits; anything else is the 404.
- `POST /automations/{id}/duplicate` `{ name?, copyMemory? }` → 201 with the new automation's
  bare §4 automation JSON (the create shape) — the §5 store-level duplicate: current version
  as v1, top-level operational fields copied, triggers off with fresh ids, memory empty unless
  `copyMemory` (default false) is true. `name` (trimmed; blank or omitted → the source name)
  dedupes per §4.1 like create, so the route never 422s on a collision — the landed name is
  in the response. 404 on an unknown id. Publishes one single-row `automation.changed` for the
  new automation (clients insert the row, §19 merge rule for an unknown id). A `copyMemory`
  request is refused up front with 409 "an execution is in progress" while the source is live;
  any duplicate is refused with 409 "Step `X` was edited on disk — pull and push it, or save
  it from the editor, before duplicating" while the source's current version holds a §4.1
  `step-modified` step — decided from the files on disk against their seals at copy time,
  not only the loaded flag (a copy would seal an unvalidated script, §5)
  (the §6.3 manual-snapshot rule: a mid-execution copy could catch a half-written file) — nothing
  is created; the copy's commit re-checks the source, and a source that started executing
  during the copy abandons the memory copy. A memory copy that failed or was abandoned after
  the automation landed still answers **201** — the automation exists and a retry would make a
  second copy — with a `warning` string on the automation JSON ("Its memory could not be
  copied: <reason>" / "Its memory was not copied — an execution started meanwhile"); the
  client shows the warning instead of an error.
- `GET /automations/{id}/export?values=0|1` — the §5.1 transfer archive as `application/zip`
  (`Content-Disposition` filename `<name>.autowright`, name sanitized for the filesystem);
  `values=0` omits `param_values` from the manifest (default `1`); 422 naming the step for
  a current version that can't travel (§5.1: a `step-modified` step checked on disk, two
  steps of one trimmed name, a manifest shape the importer refuses)
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
  capped at 64 MB (413), one member at 32 MB decompressed, the whole archive at 256 MB
  decompressed, and the entry count at 1,000 members (422; a real archive holds a handful of
  documents plus one file per step, and the central directory is materialized before any
  size check can run) — a crafted archive can't balloon into memory
- `POST /automations/import/preview` — raw archive body exactly like `/automations/import`
  (same caps) → `{ token, preview }`: validates fully, writes nothing into the store, parks the
  bytes under the one-time `token` (§5.2 — spooled to a file under `import-spool/`, not held in
  memory; 15-minute expiry, with expired spool files swept on every preview *and* every
  confirm — never left on disk until the next preview; at most 4 archives are parked at once, and
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
- `GET /marketplace` · `POST /marketplace/sources` · `POST /marketplace/sources/{id}/refresh` ·
  `POST /marketplace/refresh` · `DELETE /marketplace/sources/{id}` ·
  `GET /marketplace/sources/{id}/entries/{index}/image` ·
  `POST /marketplace/sources/{id}/entries/{index}/preview` — the §22 marketplace surface,
  specified in §22.4; the entry preview parks a §5.2 token the ordinary
  `/automations/import/confirm` lands
- `GET /automations/{id}/memory/files` — read-only list of the §4.1 memory directory's
  files: `{ files: [{ name, size, updated }] }` — `name` the memory-relative posix path
  (recursive), `size` in bytes, `updated` the §4.1 display label; sorted by name; empty or
  missing memory → `[]`. No live-execution 409 and no lock: reads rely on the §6 atomic
  commit, so a whole file is always seen. Backs §20 `automation memory show`
- `GET /automations/{id}/memory/files/{name}` — one memory file's content:
  `{ name, size, text }` (`name` may contain `/` — the route takes the rest of the path).
  422 when `name` escapes the memory directory (absolute, `..`, or otherwise resolving
  outside it) or the file is not UTF-8 text (the message says the file is binary and names
  the memory path on disk); 404 when no such file; 413 when the file is larger than 8 MB
  (the message names the memory path on disk — the file is opened there, never served
  whole into one JSON body). Same lock-free read rule as the list
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
  container's previous test record). The route answers as soon as the record and its
  live-engine entry exist — in one lock hold, so a cancel, draft discard or automation
  delete landing during setup finds the entry, marks it, and the test thread finishes
  `cancelled` at registration without running a step (2026-10-30: a setup gap of seconds
  to minutes used to let a cancelled test run every step after the draft was gone);
  the step-file writes and the memory copy happen on the test thread after that, so a
  large memory folder never stalls the request past the 30 s client timeout (a setup
  failure there — a memory copy that cannot complete — deletes the record and publishes
  `execution.deleted`, so the row vanishes and the app log carries the error; the route
  has already answered, so there is no error body to give). Scratch memory is copied to a temp dir — when `automationId`
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
  `/skip-step` like any execution (retry answers 409 for **every** test record, edit-mode
  included — the draft may have changed). A
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
  the §11 Install/Retry button; a request that finds the pip lock held by another install
  answers 409 "a package install is already running" instead of holding a request worker
  until it frees — unless the body carries `wait: true`, which waits its turn: the §20
  import's foreground ensure sends it, the §11 buttons never do. The engine's pre-execution
  ensure, the §8 post-steps install and the §5.1 background import ensure always wait, and
  a §7 cancel arriving during that wait is still honored at once; the wait is bounded by
  the per-package install timeout, after which the request answers the same 409 — a
  `wait: true` call never pins a worker for longer than one install could take) · `POST /packages/outdated` (same body) → `{ packages:
  [{ pip, import, latest? }] }` — read-only PyPI query (§6.2: newest stable non-yanked
  version with a compatible wheel); `latest` present only when newer than the **installed**
  version, absent when not installed or on any lookup failure (backs the §11 page-load update
  check) · `POST /packages/update` `{ packages: [{ pip, import }] }` → `{ packages: [{ pip,
  import, status: installed | failed, version?, error? }] }` — `pip install --upgrade` for
  each named distribution that is installed in the shared directory (§6.2: wheels only,
  serialized; the same 409 as install while another pip run holds the lock); an entry that
  isn't installed is never installed by update — it answers `status: failed`, `error: "not
  installed"`; no manifest writes; a malformed name → 422. Every packages body is capped at
  64 entries (422 above)
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
  seeds the Review page starts from; the build instructions never travel in the body — the
  backend reads §8 `build-instructions.md` itself for every call, and a `current.instructions`
  key from an older client is ignored; clients track progress by polling
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
  kind?, step?, rule?, workaround? }`, `kind` `user-action` or `constraint` (§8; the last
  three only with `constraint`) — plus `blockedAt: steps | chat` (`steps`
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
  is consuming). Finished records nothing will ever consume (`cancelled`, and outcomes already
  consumed) are bounded at the newest 20 and the oldest dropped; a **held** outcome is never
  dropped by that bound — only its ack, its owner's settle, or a same-owner start consumes it. One **building** job per owner is a backend invariant, not a client
  courtesy: a `POST /drafts` for an owner whose previous job is still building cancels that
  job first (its harness is killed, its record settles `cancelled` and is consumed at once),
  so two agent runs can never write the same draft. Job records live in memory only: a backend restart loses them, and the
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
- `GET /executions?automation=&status=&startedFromMs=&startedToMs=&idPrefix=&limit=&beforeStartedMs=&beforeId=` →
  `{ executions, total }` (headers only — no steps; rows carry the §4.5 `triggerSender`),
  sorted in the §7 canonical order: `startedMs` desc, id asc on ties, the §5
  §7 canonical order (`startedMs` desc, `id` asc on ties — computed over the in-memory
  headers; the §5 `executions.db` index only seeds that set at startup). `automation`
  filters to automation ids (exact) and **may repeat** - `automation=a&automation=b` matches
  (`idPrefix` keeps rows whose execution id starts with the given string — the §20
  execution reference resolution sends it with a `limit`, so resolving a short id never
  serializes the whole history; an empty value is ignored)
  rows of either (the §7 filter modal's multi-select); a row with no automation id (a
  create-mode test) never matches. `startedFromMs` / `startedToMs` (optional ints ≥ 0,
  either alone or both) bound the row's `startedMs` **inclusively** - the §7 time range;
  a from above the to answers 422 naming both parameters. Every filter composes with every
  other by AND, and with the cursor.
  `status` filters to §4.6 execution statuses and **may repeat** (`status=failed&status=executing`
  matches rows in either); the literal `finished` matches every terminal status (everything
  but `queued`/`executing`) and may sit among the repeats; an unknown value answers 422
  naming the vocabulary, never an empty list. `limit` (optional int ≥ 1; anything lower
  answers 422) caps the returned rows; omitted means every match - §20 reference resolution
  reads the uncapped list, while the §7 page always sends 50. `total` counts every match
  regardless of `limit` and cursor - it is what sizes the §7 pager readout. The matched
  headers are copied under the store lock and serialized outside it, and each header's
  `startedMs` is derived once (cached beside its stored timestamp, recomputed only when
  that changes), so an uncapped read of a long history never stalls the engine.
  `beforeStartedMs` + `beforeId` are the §7 keyset cursor: only rows strictly after that
  `(startedMs, id)` position in sort order, so a page stays stable while new executions land
  above it; one without the other answers 422 (never a silent default - the same rule as the
  log route's step/attempt pair) ·
  `GET /executions/{id}` (steps
  with attempts + params + error + result + `triggerPayload` + the `workspace` / `logs` dir
  paths (§4.5) — logs are lazy, never inline; the header is looked up under the store lock
  and the body read + serialized outside it — a terminal record is immutable, and the §7
  page polls this route, so it must never hold the engine's lock across disk) ·
  `GET /executions/{id}/logs?step=&attempt=&tail=` → `{ lines: [{time, kind, sequence, text}] }` — both
  `step` and `attempt`
  select that step attempt's file, neither selects `logs/execution.ndjson`, one without the
  other answers 422 (never a silent default), a missing file
  answers empty lines. `tail` (optional int ≥ 1; anything lower answers 422) keeps only the
  last `tail` lines of the selected log, same response shape - the §7 log views send it so a
  multi-thousand-line file never has to cross the wire whole. `sinceSequence` (optional int
  ≥ 0; anything lower answers 422) keeps only lines whose `sequence` is greater — the §20
  follow loop sends the last sequence it printed, so a polled live attempt never re-downloads
  what it already showed; `tail` applies after it. Sequences are gapless per-file line
  numbers, so the server slices the file at that line before parsing (after checking that
  the line there carries that sequence — otherwise it parses the whole file), and a 1 Hz
  follow never re-parses what it already served. An undecodable byte run in the file (a
  crash mid-append) is replaced, never a 500 ·
  `GET /executions/{id}/result/{name}` (raw result-dir file for the §7 file views; plain
  filenames only — no path traversal) ·
  `POST /executions/{id}/cancel` (404 for an unknown id, like its siblings; a running
  execution is killed per §7; a §6 `queued` one leaves
  the queue and finishes `skipped`, with its sender told — the same endpoint covers both, and
  the queue check and the live check share one lock so an entry promoted between the click and
  the call can never be cancelled twice or missed by both) ·
  `POST /executions/{id}/retry` (§7 in-place retry; 409 unless failed and not live, and
  409 "version vN no longer exists" when its version is gone — never a 404, the record
  itself resolved) ·
  `POST /executions/{id}/skip-step` `{ index }` (§7 skip; 409 unless that step is executing)
- `GET/POST /agents` · `PATCH/DELETE /agents/{id}` (DELETE: §5 per-automation re-point —
  a failed automation write is logged and skipped, both events still publish) · POST and a PATCH whose merged result
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
  Free local AI card reads it from `GET /ollama/status`. The four probes run in parallel,
  and concurrent detect calls share one in-flight probe (single-flight) — a wedged CLI costs
  one bounded probe, never one per caller.
- **Install** — `POST /agents/install` `{ id }` starts a background install of that provider
  (409 while one is already running for the same id — including while an *abandoned*
  phase's worker thread is still finishing its filesystem work after the 15-minute give-up,
  detail "the previous install is still finishing — try again in a minute"; a retry must
  never race the abandoned run's staged move of the same bundle) and streams `harness.install` WS events.
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
  it can never sit `running` forever and block retries — the cap's kill also releases the
  install's own pipe read (§2 pipe-release contract), so a daemon the script leaves behind
  holding the pipe (a `curl | bash` grandchild in its own session) cannot hold the phase past
  its cap; the abandoned phase's late progress
  is discarded (each job carries a generation token its emitter checks), so it can never
  write into a later install's snapshot. **Install-location principle:**
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
  Every `curl … | sh` channel below runs the script **from a file**: the installer is
  downloaded first (curl's own exit code and error line decide — offline, 404, TLS, a
  stream cut off mid-way are all reported as the install's failure carrying curl's line),
  then executed; a pipe would run an empty or partial script, exit 0, and report success.
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
  every 2 s) — it runs only that provider's sign-in rule, never version lookups. The
  provider's probe result is cached for 2 s behind a per-provider lock, so overlapping
  polls (a CLI cold start can outlast the poll interval) share one process instead of
  stacking. For an
  ollama-mode agent `signedIn` is `null` — local models have no sign-in concept.
- Ollama: `GET /ollama/status` → `{ ready, installed,
  models, version }` (`version` from Ollama's `/api/version` when the server answers, else
  null — the §19 Claude Code local-model check gates on it), `POST /ollama/pull` — streams
  `ollama.pull` WS events `{ model, line, percent?, done, ok? }`. One pull per model at a
  time: a second request for a model already pulling answers 409 ("already pulling"); a
  pull runs under a 30-minute wall-clock cap (past it the child is killed and the terminal
  event is `ok: false` with a "pull timed out" line), and a CLI-mode pull child is
  registered with the §3 shutdown sweep so quit-all and reset never leave a multi-GB
  download running. The pull rides the
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
  `{ deleted: <count> }` (0 with no secrets — never an error). The §4.8 rows the load
  skipped as malformed are swept too: each skipped row that carries a string id gets the
  same best-effort Keychain delete, and the skipped set is cleared so the write leaves
  `secrets.yaml` empty rather than writing the raw rows back (2026-10-30 second sweep). Automations' `allowed_secrets` grants and step references
  are left as written, exactly like the per-id delete (dangling ids surface as §4.1
  `secret-missing` blockers). The unreadable-store guard below still answers 409 — the §3
  reset flow treats that as non-fatal and proceeds —
  values go straight to the Keychain (account = the secret's id, §4.8), never
  into responses or files. Routes are id-keyed (§4.8: the id is the reference identity;
  names exist for display and the create call)
- `GET /settings` · `PATCH /settings` (validates before storing: `days` must be an int —
  strict, no coercion, 422 otherwise — and is
  clamped ≥ 1, `notifications` must be `attention | all | never` — 422 otherwise, so a bad value can never
  persist and silently break the retention sweep; flipping `keepAwake` starts/stops the §3
  permanent power assertion immediately) · `POST /settings/data-path` `{ path }` (sets the
  execution-data location — an absolute path, 422 otherwise; disk-first per §5: the settings
  file is written before the index is closed and the store reloaded, and a failed reload
  restores the old location; creates the dir, reloads from it, moves nothing; answers 409 while
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
- `WS /ws?token=&role=` — events, each `{ event, ... }`. `role` is optional and names the
  subscriber kind: `shell` for the Electron main process (§13 — it connects with this role
  only when the OS notification API is available to it), anything else or absent for a
  renderer. The hub keeps each subscriber's role so the §6 notification carrier rule can ask
  "is a shell connected?" at post time; roles grant nothing else (every subscriber receives
  every event). `notification.posted` `{ id, kind: "execution" | "overdue", title, body,
  target: { page: "execution", executionId } | { page: "automation", automationId } }` — a
  notification the backend decided to post (§6) and handed to the shell to show, published
  **only** while a `shell` subscriber is connected (otherwise the backend posted natively and
  no event exists); `title` and `body` are already redacted (§6.1 `notify`). Renderers ignore
  it. Then: `execution.started` (also re-published when a
  failed execution retries in place — same execution id, updated record), `execution.queued`
  (a §6 firing was admitted to the queue — carries the new `queued` record, so it reaches the
  §7 executions list and the §9.2 "N waiting" line without a poll; promotion needs no second
  event, it publishes the ordinary `execution.started` for the same id), `execution.step`
  (status change; carries the full step incl. its attempts), `execution.log` (one NDJSON line with
  `stepIndex`/`attempt` — null for execution-level lines — and the per-file `sequence` for
  fetch-vs-stream dedupe), `execution.finished`, `execution.deleted` (`{ executionId }` — a
  §4.5 test record superseded by the next test or removed by its draft settling; clients
  drop the row and decrement `executionsTotal` — every such row: a draft settling publishes
  one event per test record in a burst, so a client keeps the set of deleted ids it has not
  yet pruned, never only the last one; the §6 retention sweep publishes nothing,
  its rows fall off at the next fetch), `automation.changed`, `agents.changed`,
  `secrets.changed`, `settings.changed`, `marketplace.changed` (§22.4 — after every
  marketplace add, refresh, and remove; no payload), `draft.changed` (the §4.4 pending slot was kept
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
  `executing`, but it announces that through `execution.started`, which this rule leaves alone —
  and symmetrically a fetched **terminal** body never overwrites a header the socket has since
  moved to `executing` (the finish-triggered GET of a failed record landing after the user's
  Retry started it again): the stale body is dropped and the live header stands. `automation.changed` carries `automationId` plus
  `automation` — the changed automation in list shape, or `null` when it was deleted —
  whenever exactly one automation changed; clients patch that one row in place by **merging**
  it over the stored record, never replacing it — and when the client holds the **full**
  record for that id (a §9.2 detail page or the editor is open on it), the merge is followed
  by one `GET /automations/{id}` so the full-record fields (steps, spec, params, versions,
  memory, latest) catch up with a change made elsewhere (the CLI, a second window); the
  delete form also stamps
  `automationDeleted` on every execution row the client holds for that id — exactly what a
  fresh `/state` would serialize, so Retry / Execute again never stay offered on orphaned
  rows. The list shape lacks the full-record fields
  (`params`/`steps`/`latest`/`memory`/`snapshots`/`spec`/`packages`/`versions`/`draft`), so a replace
  would blank those fields on an open detail page — its sections unmount and remount around
  the follow-up full fetch, which reads as a page-refresh flicker, drops input focus
  mid-edit, and collapses the page height so the scroll position jumps to the top. A bare
  `automation.changed` (no `automationId`) means "many may have changed" (data-path switch,
  startup repair): clients fall back to re-`GET /state`, applying its list rows with the
  same merge. The single-row event is published from **inside the same lock hold that
  serialized the row** (`hub.publish` only schedules the send on the event loop, it never
  blocks), so two writers — a PATCH and a scheduler firing — can never deliver their rows
  out of order and leave the client patched with the older one; the bulk events and the
  execution events keep publishing outside the lock. Clients apply automation refetches
  **ordered per id**: the newest `GET /automations/{id}` issued for an id wins, and an
  older response resolving later (a finish-triggered refetch slowed by the memory walk,
  overtaken by a PATCH's refetch) is dropped rather than regressing `paramValues`,
  triggers, or versions. Every renderer request carries a **client timeout** (30 s;
  660 s for the install/import/delete routes, for `POST /imessage/permissions/automation-probe`
  (it blocks on the consent prompt up to 300 s — a 30 s client abort would stop the spinner
  with no verdict while the backend thread stays blocked), for `POST /automations/{id}/duplicate` (its
  `copyMemory` copies the whole memory directory, like the memory routes), and for the §22 marketplace routes that read
  the network or a slow disk — add, per-source refresh, refresh-all, the entry archive,
  catalog create and save — like the §20 CLI; 90 s for marketplace images, whose backend
  slot wait plus fetch can reach that) — a wedged-but-listening
  backend fails requests instead of hanging every surface and pinning the coalesced
  refresh. A client timeout or a connection failure surfaces as plain words — "The backend
  didn't answer in time." / "The backend isn't reachable." — never the browser's "signal
  timed out" or "Failed to fetch"; the raw-bytes routes (export, catalog file, preview
  image, result file) go through the same timed wrapper and surface the API's `detail` the
  same way — never a bare status text when the API sent a `detail` (a non-JSON body,
  such as a proxy's 500 page, still falls back to the status line). A newer `GET /automations/{id}` that fails hands its
  slot back (the per-id sequence rolls back to the last one that landed), so an older
  response still in flight can land instead of leaving the record stale. The renderer
  forgets an id's full-record status when the last page holding it unmounts — and a GET
  issued after that (a PATCH's reload answering late) lands the record without re-tracking
  it; tracking resumes only with a page's own mount load — so the
  finish-triggered and change-triggered refetches stay bounded to the pages open now. A
  mount load's tracking is **armed when issued, not granted when it lands**: if a page
  action's reload supersedes the mount GET on the wire (the per-id sequence drops the
  older answer), or the mount GET fails, the armed tracking carries to the next GET for
  that id that lands — unless the page released the id in between — so a page that is
  still open never ends up untracked because its first GET lost a race. Clients reconnect with backoff (1.5 s, doubling to a 15 s ceiling, reset on a
  successful open), re-reading `backend.json` before each attempt, and re-`GET /state` on
  reconnect (a client page holding a *full* automation record — one the client fetched
  through `GET /automations/{id}` itself, tracked as a set of ids and never inferred from
  the row's fields, since `/state` rows carry the full fields too — the §9.2 detail page, the
  §11 editor — also re-`GET`s that record on reconnect, and the §22 marketplace page
  refetches its table on reconnect too, since a `marketplace.changed` missed while the socket
  was down is never replayed, and the §9 executions list refetches from its first page on
  reconnect (the pager resets — a deeper page's cursor may name rows that are gone), since
  an `execution.deleted` missed meanwhile — a retention sweep, a deleted automation — would
  otherwise leave ghost rows on a filtered or deeper page (2026-10-28): the snapshot's list rows carry no
  steps/spec/versions, and an `automation.changed` missed while the socket was down would
  otherwise leave the page showing a new version number over old bodies until navigation), and that refresh applies the snapshot's `version` alongside the data fields — after the §3 launch-time version-sync restarts the backend onto the new bundle, this reconnect refresh is what carries the new running version to the §9.4 About page (without it the page shows the pre-update number until the app is relaunched). The handler streams from a hub queue while concurrently watching the socket for
  the client's disconnect, so a dropped client ends the handler immediately — an idle open
  socket never leaves uvicorn's graceful shutdown waiting. Events are serialized with a
  `str()` fallback for any value the JSON encoder doesn't know (`json.dumps(default=str)`),
  and a send that still fails is logged and **closes the socket (1011)** so the client's
  reconnect-and-refetch runs — never a sender that dies quietly while the socket stays open
  and every later event is lost (2026-10-30 second sweep: a hand-edited `description:
  2026-10-30` loads as a YAML date and did exactly that to every client).
