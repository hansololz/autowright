// API shapes (§4 field names, served by §19).

export type Status =
  | 'queued' | 'executing' | 'succeeded' | 'failed' | 'cancelled'
  | 'skipped' | 'interrupted' | 'none'

export interface ParamDef {
  name: string
  kind: 'toggle' | 'list' | 'kv' | 'number' | 'text'
  label: string
  help: string
  validate?: boolean
  min?: number
  placeholder?: string
  default?: unknown
  // merged current values:
  on?: boolean
  lines?: string[]
  rows?: { key: string; value: string }[]
  value?: string | number
}

export interface SpecBlock { kind: 'h1' | 'h2' | 'p' | 'li'; text: string }

export interface Step {
  name: string
  description: string
  code: string
  file?: string
  agent?: boolean
  agents?: { id: string; why?: string }[] // §8 grant entries the step may call, by §4.7 agent
                      // uuid — the first is the bare `agent` handle's binding; why = that
                      // agent's role note (§9.2 tag tooltip), required by §8 validation when
                      // a step lists two or more. Display resolves id → live name.
  secrets?: { id: string; why?: string }[] // §8 secret grants the step uses beyond its
                      // secrets["<id>"] code references, by §4.8 secret uuid; why = the
                      // per-use note (§9.2 key-tag tooltip), required by §8 validation on
                      // every declared entry. Display resolves id → live name.
  packages?: { import: string; why?: string }[] // §6.2 declared packages the step uses beyond its
                      // import statements; why = the per-step note (§11 box-tag tooltip: what THIS
                      // step uses the package for), required by §8 validation on every entry
  why?: string
  timeout?: number    // §4.1 per-step time limit in seconds; absent → the 900 s engine default
  noTimeout?: boolean // §4.1 explicit no-limit marker — never combined with timeout
  retries?: number    // §4.1 automatic retry budget per pass (≤ 10); absent → 0
  infiniteRetries?: boolean // §4.1 never-stop marker — never combined with retries
}

// §6.2 declared package — a bare pip distribution name plus the module it
// provides; the installed distribution is the source of truth for the version.
// `status` and `version` are transient (§19 check/install responses and §8
// draft payloads); 'installing' exists client-side only, while an install
// call is in flight. `latest` is transient too (§19 outdated response) —
// present only when newer than the installed version; drives the §11 badge.
export interface PackageDep {
  pip: string
  import: string
  why?: string // §8 rule 5 — the authoring agent's one-line purpose, shown under the §11 row
               // (absent only on transient check/outdated response entries)
  status?: 'installed' | 'missing' | 'failed' | 'installing'
  version?: string
  error?: string
  latest?: string
}

export interface ResultFile { name: string; size: string }

export interface ExecutionResult {
  chip?: string
  chipStatus?: 'changes' | 'ok' | 'attention'
  files?: ResultFile[]
  path?: string
  when?: string
}

// §11 last-test summary — persisted in the draft container (`test.yaml`, §5),
// rides the draft payload so a resumed draft's Test card shows the last outcome
// and can link to the test's execution page while the record still exists.
export interface DraftTest {
  status: 'succeeded' | 'failed'
  when: string
  executionId?: string | null
  // §11 stale-outcome rule: the renderer's fingerprint of the tested steps
  // (§19, stored verbatim in test.yaml); null on summaries written without one
  stepsFingerprint?: string | null
}

export interface VersionInfo {
  version: number
  when: string
  note: string | null
  spec: SpecBlock[]
  instructions: string
  notes?: string     // §4.1 agent-owned working-knowledge doc — versions like spec/instructions
  steps: Step[]
  params: ParamDef[]
  packages: PackageDep[]
}

// §4.4/§11 chat-thread entry — persisted at the container root (chat.jsonl,
// §19 /chat/{owner}); the thread outlives the draft (§4.4 thread lifetime).
// Transient progress entries are editor state only and never use this shape.
export interface ChatEntry {
  id: string
  kind: 'user' | 'answer' | 'activity' | 'rewrite' | 'blockers' | 'system' | 'error'
  text?: string
  title?: string         // activity: the settled job's final stage label; answer: the §11 message-block header
  // §11 block glyph, a Font Awesome class stamped at creation (system chips and
  // answer headers); an entry without one falls back per §11
  icon?: string
  // activity: the job's settled status, driving the §11 outcome glyph — a
  // pre-field entry has none and renders as done
  outcome?: 'done' | 'blocked' | 'failed'
  // §4.4 boundary marker (backend-appended system entry on settle): entries at
  // or before the newest one are a settled draft session's history — rendered,
  // never sent to the agent (§8 CONVERSATION clips there)
  boundary?: boolean
  blockers?: Blocker[]
  // blockers entries: which call produced them — decides headline + primary action
  source?: 'chat' | 'sync'
  diagnosed?: boolean   // §8 build-diagnosis blockers — build-failure wording
  dismissed?: boolean   // §11: collapsed to a one-line summary
  resolved?: string[]   // §11 "Previously resolved" list stamped at creation
  // activity: the §11 per-step durations, derived from the §8 stage-timing
  // stamps at settle — one duration per `text` line, parallel by index (null
  // where no stamp bounds the line); pre-field entries carry none and render
  // without stamps (§21.4)
  eventDurationsMs?: (number | null)[]
  at?: string
}

// §4.3 trigger kinds — discriminated unions: each kind carries exactly its own
// fields, so the compiler enforces the pairing the backend validates with 422.
// pubsub is a reserved kind the API refuses to store ("coming soon").
export type TriggerKindFields =
  // §4.3 runIfMissed: the §6 wake catch-up opt-out; the backend serializes it
  // explicitly on every cron/time trigger; a draft carries it only when false
  | { kind: 'cron'; expression: string; timezone?: string; runIfMissed?: boolean; source: 'spec' | 'user' } // §4.3 provenance: required
  | { kind: 'time'; at: string; timezone?: string; runIfMissed?: boolean }         // one-shot wall-clock ISO timestamp
  | { kind: 'app_start' }
  // §4.3: `secret` is the token secret's §4.8 id — displays resolve it to the live name
  | { kind: 'discord'; channel: string; secret: string; pattern?: string; mention?: boolean; author?: string[] }
  | { kind: 'imessage'; from: string; pattern?: string }

// The stored/serialized trigger (§4.3): id + enabled + backend-derived display
// strings; message triggers add the derived listener state `connection`.
export type Trigger = TriggerKindFields & {
  id: string
  enabled: boolean
  connection?: { state: 'connected' | 'connecting' | 'error'; error?: string }
  label: string
  short: string
}

// The shape drafts carry (§8) and PATCH sends — no labels; `id` only on
// entries that already exist on the automation (kept through an edit save).
export type DraftTrigger = TriggerKindFields & { id?: string; enabled: boolean }

// §19 POST /triggers/preview — one result per sent entry, in order. The
// renderer keeps no local trigger-math mirror: every display string and
// next-occurrence value comes from these results.
export interface TriggerPreview {
  valid: boolean
  error?: string       // plain-word reason when invalid
  label: string        // §4.3 long display string (best-effort when invalid)
  short: string        // §4.3 short display string
  nextAtMs: number | null // next-occurrence epoch ms; null when none is computable
  nextLabel?: string   // "Jul 20, 3:00 PM"-style moment label for nextAtMs
}

export interface Automation {
  id: string
  name: string
  description: string
  version: number
  triggers: Trigger[]        // §4.3 — user-owned, never versioned
  triggerChip: string        // one → its short label · several → "N triggers" · none → "No triggers"
  allTriggersOff: boolean    // nonempty list, every trigger off (drives the OFF tag)
  nextAtMs: number | null      // epoch ms of the next enabled occurrence
  instructions: string
  notes: string              // §4.1 — the current version's notes doc ("" when empty)
  lastStatus: Exclude<Status, 'queued' | 'skipped'>  // §4.1: those two can never be an automation's latest
  live: string[]             // §4.1 execution ids in progress, oldest first — maxParallel may allow several
  maxParallel: number        // §6 how many executions may run at once (≥ 1)
  maxQueued: number          // §6 how many message firings may wait for a slot (≥ 0)
  resultChip: string | null
  resultStatus: 'changes' | 'ok' | 'attention' | null
  lastExecutionLabel: string
  agentId: string | null
  stepAgents: string[]      // §4.1 agent-enablement grants — §4.7 agent uuids
  allowedSecrets: string[]  // §4.1 secret-allowance grants — §4.8 secret uuids
  // §4.1 problems — the derived would-this-fire-successfully (and
  // is-it-firing-at-all) audit; non-empty drives the §9.1 Needs fixing chip
  // and the §9.2 banner. `label` is the exact UI copy; `kind` picks the
  // banner row's action link — and `overdue` also feeds the §13 tray dot.
  problems: { kind: 'overdue'
                  | 'secret-unresolved' | 'secret-missing' | 'secret-ungranted' | 'secret-unset'
                  | 'agent-unresolved' | 'agent-missing' | 'agent-ungranted' | 'package-missing'
                  | 'os-mismatch'; label: string }[]
  // §4.1/§5.1: the import's no-match map — the id is the minted placeholder
  // the steps carry; name/description are the archive record's. Always
  // present ({} when none), filtered to still-referenced ids.
  unresolvedReferences: UnresolvedRefs
  snapshotSettings: SnapshotSettings // §6.3 automatic-snapshot toggles
  specMeta: string
  latest?: (ExecutionResult & { executionId: string; when: string }) | null
  params?: ParamDef[]
  memory?: { size: string; updated: string; path?: string }
  snapshots?: MemorySnapshot[] // §6.3 — newest-first
  steps?: Step[]
  spec?: SpecBlock[]
  packages?: PackageDep[]    // §6.2 — the current version's declared packages
  versions?: VersionInfo[]
  // §4.4/§19: the draft container's payload — the same shape GET /draft/{owner}
  // answers (the shared serializer); name/description ride only the pending slot's
  draft?: DraftPayload | null
}

// §6.3 automatic-snapshot toggles — one per automatic reason, all default true
export interface SnapshotSettings {
  preVersion: boolean
  preClear: boolean
  preRestore: boolean
}

// §6.3 memory snapshot (API shape, §4.1)
export interface MemorySnapshot {
  id: string
  name: string | null
  reason: 'manual' | 'pre-clear' | 'pre-version' | 'pre-restore'
  when: string
  version: string
  size: string
  files: number
}

// §4.5: one entry per execution of a step — a step's status is its latest
// attempt's status; logs are fetched lazily per (step, attempt) (§19).
// `number` is monotonic: only the newest 20 attempts are retained, so the
// latest entry's `number` is the true attempt count — never use the list
// length for that.
export interface Attempt { number: number; status: Status; duration: string; startedMs: number }
export interface ExecutionStep { name: string; status: Status; duration: string; attempts: Attempt[] }
export interface LogLine { time: string; kind: 'sys' | 'out' | 'wrn' | 'err'; sequence: number; text: string }

// §4.5: what a message trigger was firing on — discriminated on kind.
export type TriggerPayload =
  | { kind: 'discord'; text: string; sender: string; messageId: string | null; at: string
      channel: string; channelName: string | null; guildName: string | null
      guildId: string | null; secret: string }
  | { kind: 'imessage'; text: string; sender: string; messageId: string | null; at: string
      chat: string | null }  // Messages chat guid (reply routing)
// messageId/chat are null only on §4.5 mocked-test payloads — real firings always carry them.

export interface Execution {
  id: string
  automationId: string | null  // §4.5: null on a create-mode test — no automation record exists
  automationName: string
  automationDeleted: boolean
  versionLabel: string
  status: Status
  trigger: 'Manual' | 'Menu bar' | 'Tray' | 'Cron' | 'Once' | 'App start' | 'Discord' | 'iMessage' | 'Test'  // §4.5 labels ('Tray' = menubar on Windows/Linux, §9)
  triggerSender: string | null  // §4.5 — payload sender on every row ("Discord · Dave · v3")
  test: boolean  // §4.5 test executions — §11 draft tests
  duration: string
  started: string
  startedMs: number
  endedMs: number  // 0 while live or when finished_at was never set (§3 interrupted)
  queuedMs: number  // §6 admission time; 0 on every execution that never waited
  note: string | null
  // §4.5 failure diagnostics — failed executions only
  error: { step: string | null; message: string; reason: string | null } | null
  // full record only (§19 GET /executions/{id}) — absent on list headers
  steps?: ExecutionStep[]
  result?: ExecutionResult | null
  redactedSecrets?: string[] | null  // §4.5: a list — display joins it
  params?: ParamDef[]
  triggerPayload?: TriggerPayload | null
  workspace?: string  // §4.5: the execution's workspace dir — §7 Finder link
  logs?: string  // §4.5: the execution's logs dir — §7 LOGS pane reveal button
}

export interface Agent {
  id: string
  name: string | null
  description?: string
  harness: 'Claude Code' | 'Gemini CLI' | 'Codex' | 'OpenCode'
  // 'ollama' = the harness driving a local Ollama model (§4.7) — valid with
  // Claude Code, Codex, and OpenCode, never Gemini CLI;
  // 'custom' = user-typed model string, valid with every harness
  mode: 'default' | 'ollama' | 'custom'
  // null only when mode is 'default' — the harness uses its own configured model
  model: string | null
  default?: boolean
  // §4.7 usedBy: automations using the agent, as { id, name } — id is what
  // the §12 chips navigate by, name the display.
  usedBy?: { id: string; name: string }[]
}

// §4.8: id = the uuid every reference binds by (names are unique + immutable
// display); set=false → placeholder (name reserved, no Keychain value yet).
// usedBy entries are { id, name } of automations using the secret — the UI
// joins the names.
export interface SecretMeta {
  id: string
  name: string
  description: string
  set: boolean
  usedBy: { id: string; name: string }[]
}

// §4.1/§5.1 unresolvedReferences — the archive references import could not
// match, keyed by the minted placeholder id the steps carry.
export type UnresolvedRefs = Record<string, { kind: 'secret' | 'agent'; name: string; description: string }>

// §5.1 how a reference matched: the ladder rung (§19 matchedBy).
export type MatchedBy = 'name' | 'configuration' | 'similarity'

// §5.1 import summary — what matched (auto-granted) and what landed
// unresolved (§19). Matched agents carry `ready` (backend §19 check-ready
// rule at import time) so the summary modal can badge a not-ready harness
// Needs setup (§12 badge).
export interface ImportSummary {
  secretsMatched: { name: string; matchedTo: string; matchedBy: MatchedBy }[]
  agentsMatched: { name: string; matchedTo: string; matchedBy: MatchedBy; ready: boolean }[]
  unresolved: { kind: 'secret' | 'agent'; name: string; description: string }[]
  packages: PackageDep[]
  // §5.1: the archive's name when the §4.1 dedupe renamed the landed automation.
  renamedFrom: string | null
  // §5.1: the archive manifest's platform token (null when absent) and
  // whether it differs from the running platform.
  os: string | null
  osMismatch: boolean
}

// §5.2 import preview — the archive's contents plus the §5.1 match ladders
// run dry (`matchedTo`/`matchedBy`, null when the reference would land
// unresolved); sourceUrl/resolvedUrl only on URL fetches (§19)
export interface ImportPreview {
  name: string
  // §5.2: the §4.1 name dedupe run dry — what the import will land as
  // (equal to `name` when the name is free; best-effort until confirm).
  landsAs: string
  description: string
  steps: { name: string; description: string; agent: boolean }[]
  params: { name: string; kind: string }[]
  triggers: { kind: 'cron' | 'app_start' | 'discord' | 'imessage'; expression?: string; timezone?: string; channel?: string; from?: string; pattern?: string }[]
  packages: PackageDep[]
  agents: { name: string; harness: string; mode: string; model: string | null; matchedTo: string | null; matchedBy: MatchedBy | null }[]
  secrets: { name: string; description: string; matchedTo: string | null; matchedBy: MatchedBy | null }[]
  // §5.1 platform token + mismatch flag — same rule as the import summary.
  os: string | null
  osMismatch: boolean
  sourceUrl?: string
  resolvedUrl?: string
}

export interface Settings {
  login: boolean
  menuBarIcon: boolean
  keepAwake: boolean
  automaticUpdateCheck: boolean
  notifications: 'attention' | 'all'
  days: number
  keepForever: boolean
  developerMode: boolean
  cliEnabled: boolean
  dataPath: string
  dataSize: string
  appPath?: string
}

// §8 chat-call follow-up actions (actions.yaml, validated backend-side) —
// the editor performs them after applying the response's rewrites (§11).
// §8 `triggers` action ops — entries normalized to the §4.3 stored shape by
// the backend's validation (chat-op crons arrive `source: 'user'`).
export type TriggerOp =
  | { op: 'add'; trigger: DraftTrigger }
  | { op: 'edit'; index: number; trigger: DraftTrigger }
  | { op: 'enable'; index: number; enabled: boolean }
  | { op: 'remove'; index: number }

// §8 chat-staged concurrency — partial over the §4.1 pair, lands at save
export interface ConcurrencyStage {
  maxParallel?: number
  maxQueued?: number
}

export interface ChatActions {
  sync?: boolean
  test?: boolean
  testValues?: Record<string, unknown>
  paramValues?: Record<string, unknown> // §4.2 staged stored values — land at save
  triggers?: TriggerOp[]                // §4.3 staged trigger edits — land at save
  concurrency?: ConcurrencyStage        // §8 staged concurrency — lands at save
  name?: string
  description?: string
  undo?: boolean // §8: always alone — runs the §11 draft-undo restore
}

// §8: `chat` jobs return any subset of { answer, spec, instructions, notes, actions };
// create/sync jobs return the full payload.
export interface DraftPayload {
  name?: string | null
  description?: string
  note?: string
  params?: ParamDef[]
  packages?: PackageDep[]    // §6.2 — statuses attached after the install stage
  steps?: Step[]
  spec: SpecBlock[] | null
  instructions?: string | null
  notes?: string             // §4.1 notes doc — rides drafts and §8 chat/sync payloads
  triggers?: DraftTrigger[]  // §8: cron-only in drafts
  secretReferences?: string[] // §8: code-referenced secret ids
  // §4.4: grant selections carried by the draft snapshot (agent / secret uuids)
  stepAgents?: string[]
  allowedSecrets?: string[]
  // §4.2: the chat-staged stored-value map — rides the draft snapshot, lands
  // only at save/create
  paramValues?: Record<string, unknown>
  // §8: the chat-staged concurrency object — rides the draft snapshot, lands
  // only at save/create
  concurrency?: ConcurrencyStage
  // §8/§11: call 2's drafted best-effort test values (manifest `test_values`) —
  // seed the test-run modal's setup and the never-opened test runs; draft-only
  testValues?: Record<string, unknown>
  // §4.4/§11: the dirty-gate state rides the draft — a kept out-of-sync draft
  // must resume with saving still locked
  outOfSync?: boolean
  test?: DraftTest  // §11: last-test summary, GET responses only — never sent back
  answer?: string        // §8 chat call: prose answer / accompanying message
  answerKind?: string    // §8 chat call: "question" when the agent declared the ===QUESTION=== type
  actions?: ChatActions  // §8 chat call: validated follow-up actions
}

// §8 blocker envelope entry — a `blocked` job's payload. `kind: user-action`
// marks a fix the USER does on their Mac (install/start something) — the
// automation is fine; §11 renders it Dismiss-only with the instructions as
// markdown.
export interface Blocker {
  reason: string
  fix: string
  details?: string
  kind?: 'user-action'
}

// §8 activity feed entry — one discrete drafting milestone (file started, web
// tool used, retry notice, package install), `time` epoch seconds
export interface DraftEvent {
  time: number
  text: string
  // §8: the job's stage label when the event was appended — groups the feed
  // into per-stage §11 activity entries; absent on pre-field payloads
  stage?: string
}

// §19 background continuation: one building-or-held job, owner-keyed
// ('pending' = the create-mode slot) — the GET /state `draftJobs` rows and
// the store snapshot the §9.1 drafting notes and §11 re-attach read.
export interface DraftJobRow {
  owner: string
  jobId: string
  status: string
  mode: 'chat' | 'sync'
}

// §19: the GET /draft/{owner} envelope's `job` ref (present while the owner
// has a building or held job) — same shape minus the owner.
export type DraftJobRef = Omit<DraftJobRow, 'owner'>

export interface DraftJob {
  id: string
  status: 'building' | 'done' | 'failed' | 'cancelled' | 'blocked'
  stage: string | null
  // §8 live progress: finer in-flight line under the stage ("Writing step 2 of
  // 5 — 02-classify.py · 38 lines"); null when the harness yields no stream
  detail: string | null
  // §8 activity feed, append-only, capped to the newest 200 — backs the §11
  // footer feed's dim history lines
  events: DraftEvent[]
  // §8 stage timing: one stamp per stage entered (epoch seconds) plus the
  // settle stamp — the §11 per-step durations derive from these client-side
  stageTimes?: { stage: string; time: number }[]
  endedTime?: number | null
  error: string | null
  errorDetail?: string[]
  // §8 chat flip: the prose streamed before the first rewrite marker — set the
  // moment the job flips to "Updating the documents", so the §11 thread lands
  // "The plan" mid-job; the settled payload's `answer` stays authoritative
  plan?: string
  draft: DraftPayload | null
  mode: 'chat' | 'sync'
  // blocked jobs only: which call blocked (`steps` on a sync call)
  blockedAt?: 'steps' | 'chat'
  blockers?: Blocker[]
  // §8 build diagnosis: true when the blockers came from a validation
  // double-failure (agent diagnosis or deterministic fallback), not a refusal
  diagnosed?: boolean
  // §19 background continuation (chat jobs): the resolved trigger list the §8
  // CURRENT-triggers section was rendered from — the §11 re-attach apply
  // proves the base list `triggers` ops index is still this one
  sentTriggers?: DraftTrigger[]
}

// §2 platform layer / §19 GET /health: what this OS can honor. The renderer
// gates every OS-coupled surface on these flags and never sniffs the platform
// itself (§9). All true on macOS.
export interface PlatformCapabilities {
  imessage: boolean
  notifications: boolean
  keepAwake: boolean
  service: boolean
  agentInstall: boolean
}

// §19 GET /health (unauthenticated). `os` is the §5.1 platform token
// (macos | windows | linux) — kept as a plain string so an unknown token from
// a newer backend can never break the read.
export interface Health {
  version: string
  app: string
  os: string
  capabilities: PlatformCapabilities
}

export interface StateSnapshot {
  version: string
  automations: Automation[]
  // §7/§19 window, not the full list: every queued/executing header plus the
  // 50 newest finished ones — deeper history pages in via GET /executions.
  executions: Execution[]
  // §19: count of every execution header the backend holds (test rows
  // included) — the §9 sidebar pill's number.
  executionsTotal: number
  agents: Agent[]
  secrets: SecretMeta[]
  settings: Settings
  // §4.4 pending create-mode slot summary — backs the §9.1 Resume draft button
  pendingDraft: { name: string; updatedAt: string | null } | null
  // §19 background continuation: every building or held drafting job
  draftJobs: DraftJobRow[]
}

// §19 WS events — one envelope per publish, discriminated on `event`. Entity
// payloads use the REST shapes above; a field the union doesn't list doesn't
// exist on the wire.
export type WsEvent =
  | { event: 'ws.open' }  // client-synthesized on (re)connect — not a backend event
  | { event: 'execution.started' | 'execution.queued' | 'execution.finished'
      executionId: string; automationId: string | null
      execution: Execution
      // the owning automation's list row (null on §4.5 test executions and
      // when the automation is already gone) — clients patch it in place
      automation: Automation | null }
  | { event: 'execution.step'
      executionId: string; automationId: string | null; index: number
      step: NonNullable<Execution['steps']>[number] }
  | { event: 'execution.log'
      executionId: string; automationId: string | null
      stepIndex: number | null; attempt: number | null; line: LogLine }
  | { event: 'harness.install'
      id: string; line?: string; percent?: number; done?: boolean; ok?: boolean; error?: string }
  // §19: `percent` is the backend's single overall pull percent (byte-weighted
  // across layers, monotonic) — the UI renders it and never parses `line`.
  | { event: 'ollama.pull'; model: string; line: string; percent?: number; done?: boolean; ok?: boolean }
  // §19: automationId + automation (null = deleted) → patch one row in place;
  // a bare event → many may have changed, re-GET /state.
  | { event: 'automation.changed'; automationId?: string; automation?: Automation | null }
  | { event: 'agents.changed' }
  | { event: 'secrets.changed' }
  | { event: 'settings.changed' }
  | { event: 'draft.changed' }
  // §19 background continuation: cancelled/consumed remove the snapshot row,
  // everything else upserts it (a held outcome stays listed until consumed)
  | { event: 'draftjob.changed'; owner: string; jobId: string; status: string; mode: 'chat' | 'sync' }
