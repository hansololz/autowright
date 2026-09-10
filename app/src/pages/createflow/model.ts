// §11 create/edit flow — the pure editor model: the Rev working copy, its
// seeds, draft serialization, the §4.3 trigger merge, spec-text helpers, and
// the chat-thread/blocker helpers. No React here — everything is plain data
// in/data out, unit-tested via the CreateFlow page's re-exports.
import type { Agent, Automation, Blocker, ChatEntry, ConcurrencyStage, DraftPayload, DraftTest, DraftTrigger, PackageDep, ParamDef, SpecBlock, Step, Trigger, TriggerOp, UnresolvedRefs, VersionInfo } from '../../types'
import { shortId, stepSecretIds, stepSecretTags } from '../../steps'

// The step-secret scanners live in the shared step-list module (../../steps)
// — the automation detail page reads the same tags — and re-export here so
// the model stays the one import for the editor's pure helpers.
export { shortId, stepSecretIds, stepSecretTags }

// markdown-ish text ↔ SpecBlock[] ('# ', '## ', '- ', plain lines)
export function specToText(blocks: SpecBlock[]): string {
  return blocks.map((b) => (b.kind === 'h1' ? '# ' + b.text : b.kind === 'h2' ? '## ' + b.text : b.kind === 'li' ? '- ' + b.text : b.text)).join('\n')
}
export function textToSpec(text: string): SpecBlock[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean).map((s): SpecBlock =>
    s.startsWith('## ') ? { kind: 'h2', text: s.slice(3) }
      : s.startsWith('# ') ? { kind: 'h1', text: s.slice(2) }
        : s.startsWith('- ') ? { kind: 'li', text: s.slice(2) }
          : { kind: 'p', text: s })
}

// §11 Blocker panel: each blocker's reason + edited fix lands in the spec under
// a "Constraints & resolutions" section — the resolution lives in the document
// itself, so it survives later edits and syncs and versions like any spec text.
const CONSTRAINTS_TITLE = 'Constraints & resolutions'
export function amendSpec(spec: SpecBlock[], blockers: Blocker[]): SpecBlock[] {
  const items: SpecBlock[] = blockers.map((b) => ({ kind: 'li', text: `${b.reason.trim()} — ${b.fix.trim()}` }))
  const at = spec.findIndex((b) => b.kind === 'h2' && b.text.trim().toLowerCase() === CONSTRAINTS_TITLE.toLowerCase())
  if (at < 0) return [...spec, { kind: 'h2', text: CONSTRAINTS_TITLE }, ...items]
  let end = at + 1
  while (end < spec.length && spec[end].kind !== 'h2' && spec[end].kind !== 'h1') end++
  return [...spec.slice(0, end), ...items, ...spec.slice(end)]
}

export const blockerLine = (b: Blocker) => `${b.reason.trim()} — ${b.fix.trim()}`

// §11 thread entries — persisted through §19 /chat/{owner} (§4.4 thread
// lifetime: the thread outlives the draft; §5 chat.jsonl at the container
// root). The transient progress entry is rendered from job state, never stored.
export function newEntry(e: Omit<ChatEntry, 'id' | 'at'>): ChatEntry {
  return { id: crypto.randomUUID(), at: new Date().toISOString(), ...e }
}

// §11 answer header — a reply arriving with rewrites or actions is the plan;
// a reply the agent declared a question (§8 ===QUESTION=== marker, §19
// answerKind) gets its own glyph — never inferred from the text, so a closing
// courtesy question stays a plain reply. Stamped on the entry at creation;
// ChatPanel falls back to the plain header for entries persisted before the
// fields existed.
export function answerHeader(withWork: boolean, kind?: string): { icon: string; title: string } {
  return withWork ? { icon: 'fa-list-check', title: 'The plan' }
    : kind === 'question' ? { icon: 'fa-circle-question', title: 'Question for you' }
      : { icon: 'fa-message', title: 'From your AI' }
}
// §4.4: error entries persist too, so a later chat's CONVERSATION context
// still names a harness failure the user saw in the thread; activity entries
// (a settled job's event feed) persist for the user but are skipped by the
// backend's CONVERSATION assembly (§8).
const PERSIST_KINDS = new Set(['user', 'answer', 'activity', 'rewrite', 'blockers', 'system', 'error'])
export function persistChat(chat: ChatEntry[]): ChatEntry[] {
  return chat.filter((e) => PERSIST_KINDS.has(e.kind))
}

// §4.4/§8: everything at or before the newest boundary marker is a settled
// draft session's history — rendered in the thread, never sent to the agent.
// The backend clips at the boundary again (§8 belt-and-braces).
export function chatSinceBoundary(chat: ChatEntry[]): ChatEntry[] {
  for (let i = chat.length - 1; i >= 0; i--) {
    if (chat[i].boundary) return chat.slice(i + 1)
  }
  return chat
}

// §11: one stage label per job kind — shared by the live thread progress
// entry and the persisted activity entry's title, so the settled record reads
// exactly like the spinner line it replaces. No agent · model attribution:
// the composer's picker already names the agent, and naming it in one title
// only would read as if a different agent handled the other jobs.
export function jobStageTitle(r: Rev): string {
  // §8 unified stage set — the live backend stage drives the title. A chat
  // job opens at the neutral deciding phase and flips to the documents phase
  // on the first rewrite marker; the sync call is the workflow phase.
  // Package installs are bullets, never a title.
  if (r.chatBusy) {
    return r.genStage === 'Updating the documents'
      ? 'Updating the documents…' : 'Working on the request…'
  }
  return 'Syncing the workflow…'
}

// §11: display title for a §8 backend stage label — each settled per-stage
// activity entry reads exactly like the spinner line it replaces (§8 sends
// the unified labels, so this is a plain ellipsis suffix).
export function stageDisplayTitle(stage: string): string {
  return `${stage}…`
}

// §11: canned per-stage description bullets — a block, live or settled,
// never renders as a bare title. When the stream has produced no feed for a
// stage, the block says what the phase does instead of sitting empty.
const STAGE_DOING: Record<string, string> = {
  'Working on the request': 'Choosing what to do',
  'Updating the documents': 'Writing the documents',
  'Syncing the workflow': 'Building the steps from the spec',
}
export function stageDoingBullet(titleOrStage: string): string {
  return STAGE_DOING[titleOrStage.replace(/…$/, '')] ?? 'Working on it'
}

// §11 trigger-setup reminder: a workflow whose steps read the trigger message
// while the trigger list holds no message trigger needs the user to add one on
// the automation page (§8 rule 9 — the agent never invents a channel id or
// sender handle; it omits the trigger instead).
// §11 canned analyze chat message — sent as an ordinary §8 chat job by the
// panel's Analyze-the-failure button and the thread's turn-action pill alike.
// `machine` is the §9 per-OS copy rule's machine noun, passed in by the caller
// (this module stays free of React and of the store).
export const analyzeTestMessage = (machine: string, stepName?: string | null) =>
  `The test failed${stepName ? ` at step ${stepName}` : ''} — figure out why. If the automation is at fault, fix it; if it’s something I need to do on this ${machine}, tell me what to do and how instead.`

export const TRIGGER_SETUP_TEXT = 'The steps read the trigger message, but no message trigger is set up — tell your AI the channel or sender details, or add one on the automation page after saving.'
export function needsMessageTriggerSetup(steps: Step[], triggers: DraftTrigger[]): boolean {
  return !triggers.some((t) => t.kind === 'discord' || t.kind === 'imessage')
    && steps.some((s) => /\btrigger_payload\b/.test(s.code))
}

// §11: which steps reference which agent — keyed by §4.7 agent id; `name` is
// the live agent's name resolved at derivation time (display only);
// `imported` marks a missing id the §4.1 unresolvedReferences map carries
// (name is then the archive record's).
export interface AgentRef { id: string; name: string; steps: number[]; imported?: boolean }
// §11: which steps reference which secret — keyed by §4.8 secret id;
// `importedName` is the archive record's name when the id is a §4.1
// unresolved reference.
export interface SecretRef { id: string; steps: number[]; importedName?: string }
export function secretRefsOf(steps: Step[], unresolved?: UnresolvedRefs): SecretRef[] {
  const refs: SecretRef[] = []
  steps.forEach((s, i) => {
    for (const id of stepSecretIds(s)) {
      let e = refs.find((z) => z.id === id)
      if (!e) {
        const un = unresolved?.[id]?.kind === 'secret' ? unresolved[id] : null
        e = { id, steps: [], ...(un ? { importedName: un.name } : {}) }
        refs.push(e)
      }
      if (!e.steps.includes(i)) e.steps.push(i)
    }
  })
  return refs
}

// "steps 1, 3" formatter for the grant warning copy
export const stepList = (idx: number[]) => idx.map((i) => i + 1).join(', ')

// The two §8 instruction files (framework-instructions.md and build-instructions.md),
// each shown verbatim in its own read-only card. Loaded from the backend
// (GET /instructions) once per app session — the page fills this cache so both cards
// always show exactly what the agent is told.
export const instructionCache = { framework: null as string | null, build: '' }

// ---------- review working-copy state ----------

export interface Rev {
  name: string
  description: string
  note: string
  spec: SpecBlock[]
  steps: Step[]
  params: NonNullable<DraftPayload['params']>
  // §4.2 chat-staged stored values (§8 `param_values`) — land only at save/create
  paramValues: Record<string, unknown>
  // §8 chat-staged concurrency (`concurrency` action) — lands only at save/create;
  // null when nothing is staged (the card then shows the stored/default values)
  concurrency: ConcurrencyStage | null
  // §8/§11 drafted test values (call 2's manifest `test_values`) — seed the
  // test-run modal's setup editors and the never-opened test runs; draft state,
  // replaced when a later create/sync payload carries a new map
  testValues: Record<string, unknown> | null
  packages: PackageDep[]    // §6.2 declared packages — display-only, the pipeline owns the list
  triggers: DraftTrigger[]  // §11 TRIGGERS card preview — what saving stores (§4.3 cron-subset replace)
  notes: string             // §4.1 agent-owned working knowledge — never marks out of sync
  enabledAgents: string[]
  allowedSecrets: string[]
  // §11 dirty gating: true only for spec/agent-ask changes — grant
  // (agent/secret) sync state is derived from steps vs grants, never stored.
  dirty: boolean
  touched: boolean
  specEdit: boolean
  specText: string
  specTextOrig: string
  // §11 draft undo: one-level full-draft snapshot stashed when a chat
  // response changes the draft — Undo restores the draft exactly as it was
  // before that request. entryId is the thread entry the ghost Undo rides;
  // editor-state only, never serialized into the draft.
  undo: {
    spec: SpecBlock[]; steps: Step[]; params: Rev['params']; packages: PackageDep[]
    triggers: DraftTrigger[]; paramValues: Record<string, unknown>
    concurrency: ConcurrencyStage | null
    testValues: Record<string, unknown> | null
    notes: string; dirty: boolean; entryId: string
  } | null
  notesEdit: boolean
  notesDraft: string | null
  // §11 chat-action chaining (§8 actions.yaml): `pendingSync` starts a sync as
  // soon as no job is in flight; `pendingTest` starts a draft test the moment
  // the workflow is in sync (after the chained sync), carrying the test-only
  // values. Both are editor state only — never serialized.
  pendingSync: boolean
  pendingTest: { values: Record<string, unknown> | null } | null
  // §11 chat thread — the editor's one conversational surface. Persisted with
  // the draft (persistChat strips transient error entries).
  chat: ChatEntry[]
  syncBusy: boolean
  // §8 chat job in flight (the thread's progress entry carries the Cancel)
  chatBusy: boolean
  // §11 Packages card: an install/retry call in flight
  pkgBusy: boolean
  // the §8 job's live stage (unified three-phase set — drives the skeleton +
  // save-hint labels)
  genStage: string | null
  // §8 live progress: the job's finer in-flight line under the stage
  genDetail: string | null
  // §8 activity feed: the newest events (thread progress entry's dim history),
  // each with its §8 epoch stamp so the live block can show per-step durations
  genEvents: { text: string; time?: number }[]
  // §8 stage timing: when the live stage began (epoch seconds) — the title
  // row's ticking elapsed; null before the first poll carries stamps
  genStageStartedAt: number | null
  // §11 "Previously resolved": the session's applied resolutions, stamped onto
  // new blockers entries so a fix that didn't take stays visible.
  resolved: string[]
  // §11: the draft's persisted last-test summary (test.yaml) — shown in the
  // Test card when no live test is in the store; replaced by the next test.
  lastTest: DraftTest | null
  viewing: 'draft' | number
  specSecOpen: boolean | null
  agSecOpen: boolean | null
  secSecOpen: boolean | null
  pkgSecOpen: boolean | null
  instrSecOpen: boolean | null
  notesSecOpen: boolean | null
  fwOpen: boolean
}

const revDefaults = {
  dirty: false, touched: false,
  specEdit: false, specText: '', specTextOrig: '',
  undo: null as Rev['undo'],
  notesEdit: false, notesDraft: null as string | null,
  pendingSync: false, pendingTest: null as Rev['pendingTest'],
  chat: [] as ChatEntry[],
  syncBusy: false, chatBusy: false,
  pkgBusy: false, genStage: null as string | null, genDetail: null as string | null,
  genEvents: [] as { text: string; time?: number }[],
  genStageStartedAt: null as number | null,
  resolved: [] as string[],
  lastTest: null as DraftTest | null,
  viewing: 'draft' as Rev['viewing'],
  specSecOpen: null as boolean | null, agSecOpen: null as boolean | null, secSecOpen: null as boolean | null, pkgSecOpen: null as boolean | null, instrSecOpen: null as boolean | null, notesSecOpen: null as boolean | null, fwOpen: false,
}

// §11: the editor mounts on the create empty state — empty thread, placeholder
// cards; the first chat message is an ordinary §8 chat job (the
// new-automation rule).
export function seedEmpty(agents: Agent[], secretIds: string[]): Rev {
  return {
    ...revDefaults,
    name: 'New automation', description: '', note: '',
    spec: [], steps: [], params: [], paramValues: {}, concurrency: null, testValues: null, packages: [],
    triggers: [],
    notes: '',
    enabledAgents: agents.map((g) => g.id),
    allowedSecrets: secretIds,
  }
}

export function seedFromPayload(d: DraftPayload, agents: Agent[], secretIds: string[]): Rev {
  return {
    ...revDefaults,
    name: d.name || 'New automation', description: d.description || '', note: d.note || '',
    spec: d.spec ?? [], steps: d.steps ?? [], params: d.params ?? [],
    paramValues: d.paramValues ?? {},
    concurrency: d.concurrency ?? null,
    testValues: d.testValues ?? null,
    packages: d.packages ?? [],
    triggers: d.triggers ?? [],
    notes: d.notes ?? '',
    // §4.4: a resumed pending draft carries its grant selections; a fresh
    // drafting-job payload has none — default to everything enabled/allowed.
    enabledAgents: d.stepAgents
      ? d.stepAgents.filter((id) => agents.some((g) => g.id === id))
      : agents.map((g) => g.id),
    allowedSecrets: d.allowedSecrets ?? secretIds,
    lastTest: d.test ?? null,
    // §4.4/§11: restore the persisted dirty-gate state — resuming a kept
    // out-of-sync draft must not unlock Save around the gate.
    dirty: !!d.outOfSync,
  }
}

export function seedFromAuto(a: Automation, agents: Agent[], secretIds: string[]): Rev {
  // §4.4/§19: the draft container payload when one is kept, else the current version
  const src: Pick<DraftPayload, 'spec' | 'steps' | 'notes' | 'params' | 'packages'> =
    a.draft ?? {
      spec: a.spec ?? [], steps: a.steps ?? [], notes: a.notes || '',
      params: a.params,
      packages: a.packages,
    }
  return {
    ...revDefaults,
    name: a.name, description: a.description, note: '',
    spec: (src.spec ?? []).map((b) => ({ ...b })),
    steps: (src.steps ?? []).map((s) => ({ ...s })),
    params: (src.params ?? a.params ?? []).map((p) => ({ ...p })),
    paramValues: { ...(a.draft?.paramValues ?? {}) },
    concurrency: a.draft?.concurrency ?? null,
    testValues: a.draft?.testValues ?? null,
    packages: (src.packages ?? []).map((p) => ({ ...p })),
    triggers: (a.draft?.triggers ?? a.triggers).map(stripTrigger),
    notes: src.notes || '',
    // §4.4: a draft carries its own grant selections — resume restores them
    enabledAgents: (a.draft?.stepAgents ?? a.stepAgents).filter((id) => agents.some((x) => x.id === id)),
    allowedSecrets: (a.draft?.allowedSecrets ?? a.allowedSecrets).filter((id) => secretIds.includes(id)),
    lastTest: a.draft?.test ?? null,
    touched: !!a.draft,
    // §4.4/§11: restore the persisted dirty-gate state — resuming a kept
    // out-of-sync draft must not unlock Save around the gate.
    dirty: !!a.draft?.outOfSync,
  }
}

export function loadVersionInto(r: Rev, snap: { spec: SpecBlock[]; steps: Step[]; notes?: string; params?: VersionInfo['params']; packages?: VersionInfo['packages'] }, viewing: Rev['viewing']): Rev {
  return {
    ...r,
    spec: (snap.spec ?? []).map((b) => ({ ...b })),
    steps: (snap.steps ?? []).map((s) => ({ ...s })),
    params: snap.params ? snap.params.map((p) => ({ ...p })) : r.params,
    packages: (snap.packages ?? []).map((p) => ({ ...p })),
    notes: snap.notes || '',
    specEdit: false, specText: '', specTextOrig: '', undo: null,
    notesEdit: false, notesDraft: null, pendingSync: false, pendingTest: null,
    dirty: false, syncBusy: false, chatBusy: false,
    // A freshly loaded view is pristine — a stale carried-over `touched` would
    // make the §4.4 draft-keep paths persist this view's verbatim content over
    // a real draft.
    touched: false,
    resolved: [],
    viewing,
  }
}

// §4.4: which views' edits persist as the draft — the Draft view (an existing
// draft re-persists even untouched) or the current version's view when
// actually touched (§11: only old versions are read-only; untouched browsing
// must not clobber a real draft with the version's own content).
export function holdsDraftEdits(r: Rev, a: Automation): boolean {
  if (r.viewing === 'draft') return r.touched || !!a.draft
  return r.viewing === a.version && r.touched
}

// §4.3 trigger merge: a sync's drafted schedules take over the schedule subset
// (cron and interval) — an entry matching an existing cron on
// (expression, timezone), or an existing interval on `every`, keeps its id and
// enabled state. Drafted message/app-start entries add only when no existing
// trigger matches their identity fields; existing non-schedule triggers always survive.
// The §4.3 stored fields only — a stored Trigger's derived label/short/connection
// must not leak into a draft snapshot (§4.4 draft-only `triggers` key).
export function stripTrigger(t: Trigger | DraftTrigger): DraftTrigger {
  const base = { ...(t.id ? { id: t.id } : {}), enabled: t.enabled }
  switch (t.kind) {
    // §4.3 runIfMissed rides a draft only when false (absent = true)
    case 'cron': return { ...base, kind: 'cron', expression: t.expression, ...(t.timezone ? { timezone: t.timezone } : {}), ...(t.runIfMissed === false ? { runIfMissed: false } : {}), source: t.source }
    // §4.3 interval: the canonical `every`, no timezone
    case 'interval': return { ...base, kind: 'interval', every: t.every, ...(t.runIfMissed === false ? { runIfMissed: false } : {}), source: t.source }
    case 'time': return { ...base, kind: 'time', at: t.at, ...(t.timezone ? { timezone: t.timezone } : {}), ...(t.runIfMissed === false ? { runIfMissed: false } : {}) }
    case 'app_start': return { ...base, kind: 'app_start' }
    case 'discord': return {
      ...base, kind: 'discord', channel: t.channel, secret: t.secret,
      ...(t.pattern ? { pattern: t.pattern } : {}), ...(t.mention ? { mention: true } : {}),
      ...(t.author?.length ? { author: t.author } : {}),
    }
    case 'imessage': return {
      ...base, kind: 'imessage', from: t.from,
      ...(t.pattern ? { pattern: t.pattern } : {}),
    }
  }
}
// §4.3 schedule kinds — the subset a sync derives and replaces
const isSchedule = (t: DraftTrigger): t is Extract<DraftTrigger, { kind: 'cron' | 'interval' }> =>
  t.kind === 'cron' || t.kind === 'interval'
// §4.3 schedule identity: crons match on (expression, timezone), intervals on
// the canonical `every`. A cron never matches an interval — distinct identities.
function sameSchedule(a: DraftTrigger, b: DraftTrigger): boolean {
  if (a.kind === 'cron' && b.kind === 'cron') {
    return a.expression === b.expression && (a.timezone ?? '') === (b.timezone ?? '')
  }
  if (a.kind === 'interval' && b.kind === 'interval') return a.every === b.every
  return false
}
// §4.3 discord `author` normalization (mirrors the backend's normalize_authors):
// trimmed, deduped, sorted — element order must never distinguish two triggers.
const normalizedAuthors = (raw: string[] | undefined) =>
  [...new Set((raw ?? []).map((a) => a.trim()))].sort()
function sameNonCron(a: DraftTrigger, b: DraftTrigger): boolean {
  if (a.kind === 'app_start' && b.kind === 'app_start') return true
  if (a.kind === 'imessage' && b.kind === 'imessage') {
    return a.from === b.from && (a.pattern ?? '') === (b.pattern ?? '')
  }
  if (a.kind === 'discord' && b.kind === 'discord') {
    return a.channel === b.channel && a.secret === b.secret
      && (a.pattern ?? '') === (b.pattern ?? '') && !!a.mention === !!b.mention
      && normalizedAuthors(a.author).join('\n') === normalizedAuthors(b.author).join('\n')
  }
  return false
}
export function mergeDraftTriggers(cur: DraftTrigger[], drafted: DraftTrigger[]): DraftTrigger[] {
  const schedules = cur.filter(isSchedule)
  const used = new Set<number>()
  const next = drafted.filter(isSchedule).map((d) => {
    const i = schedules.findIndex((c, j) => !used.has(j) && sameSchedule(c, d))
    if (i < 0) return { ...d, enabled: true }
    used.add(i)
    return schedules[i]
  })
  // §4.3 provenance: only spec-sourced schedules are the sync's replaceable
  // subset — an unmatched `source: user` cron or interval (detail page, chat
  // op, CLI) always survives.
  const userSchedules = schedules.filter((c, j) => !used.has(j) && c.source === 'user')
  const added = drafted.filter((d) =>
    !isSchedule(d) && d.kind !== 'time' && !cur.some((c) => sameNonCron(c, d)))
  return [...next, ...userSchedules, ...cur.filter((t) => !isSchedule(t)), ...added.map((d) => ({ ...d, enabled: true }))]
}

/** §11 stale-outcome rule: an opaque fingerprint of the draft's steps (files +
 * code, in order) — FNV-1a 32-bit as 8 hex digits over a separator-joined
 * string. Sent with a test start (§19 `stepsFingerprint`) and compared with the
 * current steps to tell whether a test outcome still describes them. */
export function stepsFingerprint(steps: Step[]): string {
  let h = 0x811c9dc5
  const feed = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i)
      h = Math.imul(h, 0x01000193) >>> 0
    }
  }
  for (const s of steps) {
    feed(s.file ?? '')
    feed('\u0000')
    feed(s.code ?? '')
    feed('\u0001')
  }
  return `${steps.length}:${h.toString(16).padStart(8, '0')}`
}

export function serializeDraft(r: Rev): DraftPayload {
  return {
    name: r.name, description: r.description, note: r.note,
    params: r.params,
    packages: r.packages.map(({ pip, import: imp, why }) => ({ pip, import: imp, why })),
    steps: r.steps,
    spec: r.spec,
    notes: r.notes,
    triggers: r.triggers,
    // §4.2: the staged value map rides the snapshot only when nonempty
    ...(Object.keys(r.paramValues).length ? { paramValues: r.paramValues } : {}),
    // §8: the staged concurrency object rides the snapshot only when staged
    ...(r.concurrency ? { concurrency: r.concurrency } : {}),
    // §8/§11: the drafted test-value map rides the snapshot when present, so a
    // kept draft resumes with its test setup still seeded
    ...(r.testValues && Object.keys(r.testValues).length ? { testValues: r.testValues } : {}),
    stepAgents: r.enabledAgents,
    allowedSecrets: r.allowedSecrets,
    // §4.4/§11: the dirty-gate state rides the snapshot — a kept out-of-sync
    // draft must resume with saving still locked. The chat thread is NOT part
    // of the draft payload — it persists through §19 /chat/{owner} (§4.4).
    ...(r.dirty ? { outOfSync: true } : {}),
  }
}

// §11 chat-armed test values (§8 actions.yaml `test_values`) — merged into
// the panel's editors by name, tolerant of the yaml value shapes.
export function applyTestValues(ps: ParamDef[], vals: Record<string, unknown>): ParamDef[] {
  return ps.map((p) => {
    if (!(p.name in vals)) return p
    const v = vals[p.name]
    if (p.kind === 'toggle') return { ...p, on: !!v }
    if (p.kind === 'list') return { ...p, lines: Array.isArray(v) ? v.map(String) : [String(v)] }
    if (p.kind === 'kv') {
      if (Array.isArray(v)) return { ...p, rows: v as { key: string; value: string }[] }
      if (v && typeof v === 'object') return { ...p, rows: Object.entries(v as Record<string, unknown>).map(([key, val]) => ({ key, value: String(val) })) }
      return p
    }
    if (p.kind === 'number') return { ...p, value: typeof v === 'number' ? v : Number(v) || (p.min ?? 0) }
    return { ...p, value: String(v) }
  })
}

// §8 `param_values` staging: raw yaml value → the §4.2 stored value shape for
// the def's kind (the same tolerance applyTestValues uses), so the staged map
// survives the save endpoint's strict name+kind match.
export function coerceParamValue(p: ParamDef, v: unknown): unknown {
  if (p.kind === 'toggle') return !!v
  if (p.kind === 'list') return Array.isArray(v) ? v.map(String) : [String(v)]
  if (p.kind === 'kv') {
    if (Array.isArray(v)) return (v as { key: string; value: string }[]).map((r) => ({ key: String(r?.key ?? ''), value: String(r?.value ?? '') }))
    if (v && typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([key, val]) => ({ key, value: String(val) }))
    return []
  }
  if (p.kind === 'number') return typeof v === 'number' ? v : Number(v) || (p.min ?? 0)
  return String(v)
}

// §11 re-attach trigger guard (§19 sentTriggers): are two trigger lists the
// same list, entry for entry, on the fields the agent's 1-based indexes refer
// to? Compared through a stable per-entry projection rather than raw JSON so
// key order and backend normalization can never fake a difference — and an
// entry the projection misses errs toward "changed", which only drops ops
// (never misapplies them).
export function sameTriggerList(a: unknown, b: unknown): boolean {
  const xs = Array.isArray(a) ? a : []
  const ys = Array.isArray(b) ? b : []
  const key = (t: Record<string, unknown>) => JSON.stringify([
    t.id ?? null, t.kind ?? null, t.expression ?? null, t.every ?? null, t.at ?? null,
    t.timezone ?? null, t.from ?? null, t.channel ?? null, t.secret ?? null,
    t.pattern ?? null, t.mention ?? null, t.author ?? null, t.enabled !== false,
    t.runIfMissed !== false,
  ])
  return xs.length === ys.length
    && xs.every((t, i) => key(t as Record<string, unknown>) === key(ys[i] as Record<string, unknown>))
}

// §11 chat `triggers` ops — applied to the editor's trigger list in op order,
// each yielding the system-chip text the thread shows. An `add` matching an
// existing trigger on the §4.3 identity fields is a no-op backstop chip
// ("already exists"); ops touch only the entries they name. Indexes are
// 1-based over the CURRENT triggers list the agent saw — a handle table keeps
// them meaningful even after an earlier op removes or edits an entry, so a
// multi-op response can never hit a shifted neighbor.
// §11 chip wording only — fixed display words per kind, never §4.3 label math
// (labels still come from §19 `/triggers/preview`).
const TRIGGER_KIND_WORD: Record<string, string> = {
  cron: 'Cron', interval: 'Interval', time: 'One-time', app_start: 'App-start',
  discord: 'Discord', imessage: 'iMessage',
}
const triggerNoun = (kind: string): string =>
  TRIGGER_KIND_WORD[kind] ? `${TRIGGER_KIND_WORD[kind]} trigger` : 'Trigger'

export function applyTriggerOps(triggers: DraftTrigger[], ops: TriggerOp[]): { triggers: DraftTrigger[]; chips: string[] } {
  const sameTrigger = (a: DraftTrigger, b: DraftTrigger): boolean => {
    if (a.kind === 'cron' && b.kind === 'cron') {
      return a.expression === b.expression && (a.timezone ?? '') === (b.timezone ?? '')
    }
    if (a.kind === 'interval' && b.kind === 'interval') return a.every === b.every
    if (a.kind === 'time' && b.kind === 'time') {
      return a.at === b.at && (a.timezone ?? '') === (b.timezone ?? '')
    }
    return sameNonCron(a, b)
  }
  // handles[i] = the current object for original index i+1 (null once removed)
  const handles: (DraftTrigger | null)[] = [...triggers]
  let list = [...triggers]
  const chips: string[] = []
  for (const op of ops) {
    if (op.op === 'add') {
      const dup = list.find((t) => sameTrigger(t, op.trigger))
      if (dup) {
        chips.push('That trigger already exists.')
      } else {
        list = [...list, { ...op.trigger, enabled: true }]
        chips.push(`${triggerNoun(op.trigger.kind)} added.`)
      }
      continue
    }
    const target = handles[op.index - 1]
    if (!target) continue // removed by an earlier op — nothing to touch
    if (op.op === 'edit') {
      // §8: an edit keeps id, enabled, and the §4.3 runIfMissed choice; the
      // dialect cannot set it, so the user's opt-out survives a schedule change
      const keptOptOut = (target.kind === 'cron' || target.kind === 'interval' || target.kind === 'time')
        && target.runIfMissed === false
        && (op.trigger.kind === 'cron' || op.trigger.kind === 'interval' || op.trigger.kind === 'time')
      const edited = {
        ...op.trigger, ...(target.id ? { id: target.id } : {}), enabled: target.enabled,
        ...(keptOptOut ? { runIfMissed: false } : {}),
      } as DraftTrigger
      list = list.map((t) => (t === target ? edited : t))
      handles[op.index - 1] = edited
      chips.push(`${triggerNoun(edited.kind)} ${op.index} updated.`)
    } else if (op.op === 'enable') {
      const flipped = { ...target, enabled: op.enabled }
      list = list.map((t) => (t === target ? flipped : t))
      handles[op.index - 1] = flipped
      chips.push(`${triggerNoun(flipped.kind)} ${op.index} turned ${op.enabled ? 'on' : 'off'}.`)
    } else {
      list = list.filter((t) => t !== target)
      handles[op.index - 1] = null
      chips.push(`${triggerNoun(target.kind)} ${op.index} removed.`)
    }
  }
  return { triggers: list, chips }
}
