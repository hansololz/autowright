// One central model drives everything (§4 top-level, §9 navigation).
import { create } from 'zustand'
import { api, connectInfo, openWs } from './api'
import type { Agent, Automation, DraftJobRow, Execution, LogLine, PlatformCapabilities, SecretMeta, Settings, StateSnapshot, WsEvent } from './types'

type Surface = 'onboard' | 'app' | 'create' | 'menubar'
export type Page =
  | 'automations' | 'automation' | 'executions' | 'execution'
  | 'agents' | 'agentNew' | 'secrets' | 'settings' | 'about'

type CreateFrom = 'app' | 'edit' | null

// §7/§19 store window: the executions list keeps every live header plus this
// many finished rows — the /state snapshot's own shape, which the event path
// re-trims to after every merge so a long session never grows it without end.
const FINISHED_WINDOW = 50
// §7 live rows: never trimmed out of the window — the §9 Executing / Queued
// sections read them straight out of it.
const isLive = (status: string) => status === 'queued' || status === 'executing'

// §12 agent status badge — cached for the app session so the Agents page
// doesn't re-check on every visit.
export type AgentCheck = 'checking' | 'connecting' | 'ready' | 'needs'

interface NavSnap {
  surface: Surface; page: Page; automationId: string | null; executionId: string | null
  // §4.4/§11: without this, browser-back into the editor would restore
  // surface 'create' with createFrom null — the wrong editor mode.
  createFrom: CreateFrom
  // §12: which agent the agentNew page edits (null = blank add form) — in the
  // snapshot so back/forward re-enters the same edit form, never a blank one.
  agentEditId: string | null
}

// §2/§9 gating: the flags the renderer starts with before the first §19
// /health read lands. macOS-identical (all true), so a mac render never
// flickers a row it is about to keep; boot reads /health before `connected`
// flips true, so no gated surface ever mounts on these.
const MAC_CAPABILITIES: PlatformCapabilities = {
  imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
}

interface Model {
  connected: boolean | null
  version: string
  automations: Automation[]
  // §7/§19 window: live headers + the 50 newest finished, kept current by
  // the execution events — the Executions page fetches deeper pages itself.
  executions: Execution[]
  // §19 executionsTotal — the §9 Executions pill; every header the backend
  // holds, not just the window.
  executionsTotal: number
  agents: Agent[]
  secrets: SecretMeta[]
  settings: Settings | null
  // §3 update-available: a known, not-yet-installed newer version — feeds the
  // §9 "Update available" nav row and the §9.4 pre-armed Updates row. A later
  // up-to-date check clears it; otherwise it lives until the restart that
  // installs.
  updateAvailable: string | null
  // §4.4 pending create-mode slot — drives the §9.1 Resume draft button
  pendingDraft: { name: string; updatedAt: string | null } | null
  // §19 background continuation: every building or held drafting job, owner-
  // keyed — drives the §9.1 drafting notes and the §11 re-attach; kept
  // current by the draftjob.changed event.
  draftJobs: DraftJobRow[]
  // §9.5 report issue modal — opened by the §9 "Report an issue" nav row; not a page.
  reportOpen: boolean
  // §9.4 What's-new modal — opened by the About row and by the post-update
  // auto-open at boot; not a page.
  whatsNewOpen: boolean
  // §2/§9 platform gating, read from §19 GET /health at every backend
  // connection: the §5.1 os token ('' until the first read) and the platform
  // layer's flag set. Every OS-coupled surface gates on these — the renderer
  // never sniffs the platform itself.
  platformOs: string
  platformCapabilities: PlatformCapabilities

  surface: Surface
  page: Page
  automationId: string | null
  executionId: string | null
  createFrom: CreateFrom
  // §12: agent id the agentNew page edits — null renders the blank add form.
  agentEditId: string | null

  toast: string | null
  executionFull: Record<string, Execution>
  // §19 lazy logs, per execution: logKey(step, attempt) → fetched lines,
  // extended live by matching exec.log events (deduped by sequence)
  execLogs: Record<string, Record<string, LogLine[]>>
  // §11 test — the live test execution the editor's TEST card tracks.
  // Steps, status, and logs live on the ordinary exec record (executionFull[executionId],
  // kept fresh by exec.* events).
  test: { executionId: string } | null
  // §7/§9.2 Fix with AI: the failed execution id handed to the editor, which
  // seeds the thread and sends the §11 canned analyze chat message on mount,
  // then clears it.
  fixExec: string | null
  ollamaPull: { model: string; line: string; percent?: number; done: boolean; ok?: boolean } | null
  // §19 harness.install stream, latest event per provider id
  harnessInstall: Record<string, { line?: string; percent?: number; done: boolean; ok?: boolean; error?: string }>
  // §12 session cache of agent status checks, keyed by agent id
  agentChecks: Record<string, AgentCheck>

  boot(): Promise<void>
  readHealth(): Promise<void>
  runAgentCheck(id: string, pending?: AgentCheck): Promise<'ready' | 'needs'>
  disconnect(): void
  refresh(): Promise<void>
  applyEvent(msg: WsEvent): void
  go(page: Page, ids?: { automationId?: string | null; executionId?: string | null; agentEditId?: string | null }): void
  setSurface(s: Surface, from?: CreateFrom): void
  showToast(msg: string, ms?: number): void
  loadExecution(executionId: string): Promise<void>
  loadExecLogs(executionId: string, step?: number, attempt?: number): Promise<void>
  loadAuto(automationId: string): Promise<void>
  beginTest(executionId: string): void
  clearTest(): void
}

let toastTimer: ReturnType<typeof setTimeout> | undefined
let bootTimer: ReturnType<typeof setTimeout> | undefined
let closeWs: (() => void) | null = null
let passedOnboard = false
let restoring = false
// Monotonic refresh sequence: only the newest in-flight /state snapshot may
// land — an older response resolving late would clobber fresher data.
let refreshSeq = 0
// Bumped when a WS execution event mutates the list directly — an in-flight
// /state snapshot from before the bump is already stale and must refetch
// rather than clobber the fresher event-applied rows (§19 event path).
let eventSeq = 0

// §3 one-shot first-run CLI install: with cliEnabled on (default true) and the
// ad-cli-installed marker (§15) unset, a `missing` shim is installed silently
// once. Every already-settled state (installed/foreign) sets the marker
// without touching disk; a failed install leaves it unset so the next launch
// retries — but never patches cliEnabled off (a transient failure must not
// become a permanent opt-out). Once the marker is set the app never creates
// the shim on its own again — hand-deletion sticks (§4.9 missing row is the
// explicit way back).
let cliFirstRunStarted = false
async function ensureCliFirstRun(settings: Settings): Promise<void> {
  if (cliFirstRunStarted || !settings.cliEnabled) return
  if (localStorage.getItem('ad-cli-installed') === '1') return
  const bridge = window.autowright
  if (!bridge?.cliStatus) return
  cliFirstRunStarted = true
  const s = await bridge.cliStatus().catch(() => null)
  if (!s) return
  if (s.state === 'missing') {
    const r = await bridge.cliInstall().catch(() => null)
    if (r?.ok) localStorage.setItem('ad-cli-installed', '1')
  } else {
    localStorage.setItem('ad-cli-installed', '1')
  }
}

// §9.4 post-update: no auto-open. The modal opens only from the About page.
// The key still tracks the last version this renderer ran, silently, so a
// future re-enable of the auto-open fires only for versions installed after
// that point. One check per launch; the menu-bar panel window is exempt and
// does not write the key.
let whatsNewChecked = false
function checkWhatsNew(version: string, menubar: boolean) {
  if (whatsNewChecked || menubar || !version) return
  whatsNewChecked = true
  if (localStorage.getItem('ad-last-seen-version') !== version) {
    localStorage.setItem('ad-last-seen-version', version)
  }
}

// Execution cache eviction: full records and log buckets are kept only for
// the currently viewed execution plus the 5 most recently viewed (MRU head is
// the current one, so its live-streaming buckets are never evicted mid-view);
// the rest are dropped on navigation. A tracked §11 test execution is always
// kept while the test is live. Re-opening an evicted execution refetches
// through the ordinary load paths, which already handle absence.
const EXECUTION_CACHE_KEEP = 6
let executionMru: string[] = []

// §7 log cap: a log bucket only ever holds the last 2000 lines — the fetch asks
// for that tail (§19 `tail`) and live appends trim back to it, so a chatty run
// can't grow the bucket (or the pane's rows) without bound. Log lines are
// per-file gapless from 1 (§5), so a kept head whose `sequence` is past 1 is
// exactly the "this view was truncated" signal the §7 notice reads.
export const LOG_TAIL = 2000

// §19 monotonic-refetch recovery: ids whose dropped body was already replaced
// by one scheduled refetch. The drop only strands a body when nothing has
// landed in the full slot yet, so exactly one retry is owed per execution —
// this is what makes it one and not a loop.
const executionRefetched = new Set<string>()

function touchExecutionMru(id: string) {
  executionMru = [id, ...executionMru.filter((x) => x !== id)].slice(0, EXECUTION_CACHE_KEEP)
}

function evictExecutionCaches(
  m: Pick<Model, 'executionFull' | 'execLogs' | 'test'>,
): Pick<Model, 'executionFull' | 'execLogs'> | null {
  const keep = new Set(executionMru)
  if (m.test) keep.add(m.test.executionId)
  const staleFull = Object.keys(m.executionFull).filter((id) => !keep.has(id))
  const staleLogs = Object.keys(m.execLogs).filter((id) => !keep.has(id))
  if (staleFull.length === 0 && staleLogs.length === 0) return null
  const executionFull = { ...m.executionFull }
  const execLogs = { ...m.execLogs }
  for (const id of staleFull) delete executionFull[id]
  for (const id of staleLogs) delete execLogs[id]
  return { executionFull, execLogs }
}

// '/app?automation=<uuid>' — the §13 menu-bar row deep link.
function automationIdFromHash(hash: string): string | null {
  const m = hash.match(/automation=([0-9a-f-]{36})/)
  return m ? m[1] : null
}

export const useStore = create<Model>((set, get) => ({
  connected: null,
  version: '',
  automations: [],
  executions: [],
  executionsTotal: 0,
  agents: [],
  secrets: [],
  settings: null,
  updateAvailable: null,
  pendingDraft: null,
  draftJobs: [],
  reportOpen: false,
  whatsNewOpen: false,
  platformOs: '',
  platformCapabilities: MAC_CAPABILITIES,
  surface: 'app',
  page: 'automations',
  automationId: null,
  executionId: null,
  createFrom: null,
  agentEditId: null,
  toast: null,
  executionFull: {},
  execLogs: {},
  test: null,
  fixExec: null,
  ollamaPull: null,
  harnessInstall: {},
  agentChecks: {},

  // §12: the one place a status check runs — badge goes to `pending` while the
  // real §19 /agents/{id}/check call is in flight, result lands in the cache.
  async runAgentCheck(id, pending = 'checking') {
    set({ agentChecks: { ...get().agentChecks, [id]: pending } })
    let st: 'ready' | 'needs'
    try {
      const r = await api.checkAgent(id)
      st = r.status === 'ready' ? 'ready' : 'needs'
    } catch { st = 'needs' }
    set({ agentChecks: { ...get().agentChecks, [id]: st } })
    return st
  },

  // §2/§9: the os token + capability flags, from the unauthenticated §19
  // /health. Read on every backend connection — boot below (before the shell
  // mounts) and each ws.open, which follows the reconnect's backend.json
  // re-read, so a restarted (or replaced) backend refreshes them. A failed
  // read keeps the flags as they are: the connection is down, and the retry
  // that fixes it re-reads them.
  async readHealth() {
    try {
      const h = await api.health()
      set({ platformOs: h.os, platformCapabilities: { ...MAC_CAPABILITIES, ...h.capabilities } })
    } catch { /* backend not answering yet — the connect retry re-reads */ }
  },

  async boot() {
    // One retry chain only: a re-entrant boot (StrictMode re-mount) must not
    // leave a second timer chain hammering discovery in parallel.
    clearTimeout(bootTimer)
    const ok = await connectInfo()
    if (!ok) { set({ connected: false }); bootTimer = setTimeout(() => get().boot(), 1200); return }
    // §9 gating: the flags must be known before the app shell mounts, so this
    // read is awaited on the same cycle that just re-read backend.json —
    // `connected` only flips true below.
    await get().readHealth()
    try {
      // Boot's snapshot rides the same ordering guard as refresh() — a WS
      // reconnect during boot must not let the older of the two /state
      // responses land last and roll state backwards. A stale boot response
      // still does boot's non-data duties (surface, socket); only the
      // snapshot fields yield to the newer refresh.
      const n = ++refreshSeq
      const s: StateSnapshot = await api.state()
      // Existing automations do NOT bypass onboarding: step 1 always shows; with
      // prior data its Continue goes straight to the app (§10).
      const onboarded = localStorage.getItem('ad-onboarded') === '1'
      const hash = location.hash
      const deepAutomationId = onboarded ? automationIdFromHash(hash) : null
      set({
        connected: true,
        surface: hash.includes('menubar') ? 'menubar' : onboarded ? 'app' : 'onboard',
        ...(deepAutomationId ? { page: 'automation' as const, automationId: deepAutomationId } : {}),
        ...(n === refreshSeq ? {
          version: s.version, automations: s.automations, executions: s.executions,
          executionsTotal: s.executionsTotal, agents: s.agents, secrets: s.secrets, settings: s.settings,
          pendingDraft: s.pendingDraft, draftJobs: s.draftJobs ?? [],
        } : {}),
      })
      if (onboarded) passedOnboard = true
      // §3 one-shot first-run CLI install — fire-and-forget off the freshly
      // fetched snapshot (not the store: a stale boot yields its snapshot set).
      void ensureCliFirstRun(s.settings)
      // §9.4 post-update auto-open — like the CLI first-run, off the snapshot,
      // not the store (a stale boot still knows the backend's version).
      checkWhatsNew(s.version, hash.includes('menubar'))
      // Exactly one live socket: a re-entrant boot (StrictMode re-mount,
      // backend restart) must not stack subscriptions — every stacked socket
      // applies each event once more (duplicate log lines, double toasts).
      closeWs?.()
      closeWs = openWs((msg) => get().applyEvent(msg))
      // §3 update-available: subscribe to later finds and ask for one already
      // remembered — an automatic check can finish before this renderer boots.
      // Re-registering replaces the previous listener, so a re-entrant boot
      // never stacks subscriptions.
      window.autowright?.onUpdateAvailable?.((version) => set({ updateAvailable: version }))
      void window.autowright?.updateAvailable?.().then((version) => {
        if (version) set({ updateAvailable: version })
      })
      updateTrayAlert(s.automations)
    } catch {
      set({ connected: false })
      bootTimer = setTimeout(() => get().boot(), 1200)
    }
  },

  disconnect() {
    clearTimeout(bootTimer)
    closeWs?.()
    closeWs = null
  },

  async refresh() {
    const n = ++refreshSeq
    try {
      // A WS execution event landing while /state is in flight makes the
      // snapshot stale on arrival (an execution that just started would
      // vanish until the next event) — refetch instead of applying it.
      // Bounded: after a few tries the last snapshot applies best-effort.
      for (let attempt = 0; ; attempt++) {
        const mut = eventSeq
        const s = await api.state()
        // A newer refresh started while this one was in flight — its snapshot is
        // fresher (or will be); applying this one would roll state backwards.
        if (n !== refreshSeq) return
        if (mut !== eventSeq && attempt < 3) continue
        // §19 reconnect rule: version rides along — after a §3 version-sync restarts
        // the backend, this refresh is what updates the §9.4 About page's number.
        set({ version: s.version, automations: mergeAutoRows(get().automations, s.automations), executions: s.executions, executionsTotal: s.executionsTotal, agents: s.agents, secrets: s.secrets, settings: s.settings, pendingDraft: s.pendingDraft, draftJobs: s.draftJobs ?? [] })
        updateTrayAlert(s.automations)
        return
      }
    } catch { /* backend restarting; ws reconnect will re-trigger */ }
  },

  applyEvent(msg) {
    const ev = msg.event
    const m = get()
    if (ev === 'ws.open') {
      void m.refresh()
      // §2/§9: this open follows the reconnect's backend.json re-read — the
      // backend behind it can be a restarted (or upgraded) one, so refresh the
      // platform flags on the same cycle.
      void m.readHealth()
      // A reconnect means events were missed (backend restart, dropped
      // socket): a cached full record can be stuck "executing" for an
      // execution the backend already repaired to interrupted, and open log
      // buckets can hold silent gaps. Refetch what can actually be stale —
      // non-terminal records plus whatever the current page shows.
      const staleIds = new Set(
        Object.values(m.executionFull).filter((e) => e.status === 'executing').map((e) => e.id),
      )
      if (m.executionId && m.executionFull[m.executionId]) staleIds.add(m.executionId)
      for (const id of staleIds) {
        void m.loadExecution(id)
        for (const key of Object.keys(m.execLogs[id] ?? {})) {
          if (key === 'x.0') void m.loadExecLogs(id)
          else {
            const [s, a] = key.split('.')
            void m.loadExecLogs(id, Number(s), Number(a))
          }
        }
      }
      return
    }
    // §19: single-row automation update from an event payload — null removes
    // the row (delete), an unknown id falls back to a full refresh (only the
    // server knows list ordering for a brand-new row).
    const patchAutomation = (id: string, row: Automation | null) => {
      const cur = get().automations
      if (row === null) set({ automations: cur.filter((a) => a.id !== id) })
      else if (cur.some((a) => a.id === id)) set({ automations: cur.map((a) => (a.id === id ? { ...a, ...row } : a)) })
      else { void m.refresh(); return }
      updateTrayAlert(get().automations)
    }
    // §6 exec.queued (a firing admitted to the queue) carries the same header
    // shape as exec.started — the record lands in the list the same way, which
    // is what the §7 Queued section and the §9.2 "N waiting" line count.
    if (ev === 'execution.started' || ev === 'execution.finished' || ev === 'execution.queued') {
      eventSeq++ // an in-flight /state snapshot is stale from here on
      const ej = msg.execution
      const rest = m.executions.filter((e) => e.id !== ej.id)
      // §7/§19: an id the window has never seen counts as one more toward the
      // pill's total. A retry of a beyond-the-window record overcounts by one
      // until the next /state refresh trues it up — the §7 accepted drift.
      const isNew = rest.length === m.executions.length
      // §7/§19 window: re-trim after every merge — live rows all stay, finished
      // ones only while they are among the newest FINISHED_WINDOW (the
      // Executions page fetches deeper pages itself).
      let finished = 0
      const merged = [ej, ...rest].sort((a, b) => b.startedMs - a.startedMs)
        .filter((e) => isLive(e.status) || ++finished <= FINISHED_WINDOW)
      set({
        executions: merged,
        ...(isNew ? { executionsTotal: m.executionsTotal + 1 } : {}),
      })
      // §19: the event carries the owning automation's row (live/lastStatus/
      // chip) — patch it in place; no /state refetch on the execution path.
      // null means a test execution or a just-deleted automation: no row change.
      if (msg.automation && ej.automationId) patchAutomation(ej.automationId, msg.automation)
      const full = m.executionFull[ej.id]
      // ej is a header (no steps/result) — merging keeps the full record's body
      if (full) set({ executionFull: { ...m.executionFull, [ej.id]: { ...full, ...ej } } })
      // §7 retry re-publish (and §6 promotion, same id): re-fetch steps/attempts.
      if (ev === 'execution.started' && full) void m.loadExecution(ej.id)
      if (msg.event === 'execution.finished') {
        // Refresh the body only when someone has opened this execution —
        // unviewed executions would otherwise accumulate a full record each.
        if (full) void m.loadExecution(ej.id)
        // §19: full-only fields (latest/memory/snapshots/versions) never ride
        // events — refetch the full record so an open detail page's LATEST
        // RESULT card shows this run. Only when the full record was ever
        // fetched; never for tests (draft-scoped, they don't touch `latest`).
        const row = ej.automationId ? get().automations.find((a) => a.id === ej.automationId) : undefined
        if (!ej.test && row && 'latest' in row) void m.loadAuto(row.id)
        // §7: the finished execution gets a summary toast (prototype pattern:
        // "<name> finished — <chip>."). Cancelled executions are user-initiated —
        // no toast; §11 tests report in the Test card instead.
        if (!ej.test && (ej.status === 'succeeded' || ej.status === 'failed')) {
          const aj = msg.automation
          const name = aj?.name ?? m.automations.find((a) => a.id === ej.automationId)?.name ?? 'Automation'
          m.showToast(ej.status === 'failed'
            ? `${name} failed — needs attention.`
            : aj?.resultChip ? `${name} finished — ${aj.resultChip}.` : `${name} finished.`)
        }
      }
      return
    }
    if (msg.event === 'execution.step') {
      // Steps live only on the full record (§19: list headers carry none).
      const { executionId, index: idx, step } = msg
      const full = m.executionFull[executionId]
      if (full?.steps) {
        set({
          executionFull: {
            ...m.executionFull,
            [executionId]: { ...full, steps: full.steps.map((s, i) => (i === idx ? step : s)) },
          },
        })
      }
      return
    }
    if (msg.event === 'execution.log') {
      const { executionId, line } = msg
      const key = logKey(msg.stepIndex, msg.attempt)
      const buckets = m.execLogs[executionId]
      const bucket = buckets?.[key]
      if (bucket) {
        // sequence dedupe: a line already covered by a fetched snapshot is dropped
        const last = bucket.length ? bucket[bucket.length - 1].sequence : 0
        if (line.sequence > last) {
          // §7 log cap: keep only the last LOG_TAIL lines — a chatty run would
          // otherwise grow the array (and the pane's rows) without bound.
          const next = [...bucket, line]
          set({
            execLogs: {
              ...m.execLogs,
              [executionId]: { ...buckets, [key]: next.length > LOG_TAIL ? next.slice(-LOG_TAIL) : next },
            },
          })
        }
      }
      return
    }
    if (msg.event === 'harness.install') {
      set({
        harnessInstall: {
          ...get().harnessInstall,
          [msg.id]: {
            line: msg.line, percent: msg.percent,
            done: !!msg.done, ok: msg.ok, error: msg.error,
          },
        },
      })
      return
    }
    if (msg.event === 'ollama.pull') {
      set({ ollamaPull: { model: msg.model, line: msg.line, percent: msg.percent, done: !!msg.done, ok: msg.ok } })
      return
    }
    if (msg.event === 'automation.changed') {
      // §19: entity present → patch the one row in place (null = deleted);
      // bare → many may have changed, fall back to /state.
      if (msg.automationId !== undefined && msg.automation !== undefined) {
        patchAutomation(msg.automationId, msg.automation)
      } else {
        void m.refresh()
      }
      return
    }
    if (ev === 'draftjob.changed') {
      // §19 background continuation: cancelled/consumed remove the row,
      // everything else upserts it (a held outcome stays listed until consumed).
      const others = m.draftJobs.filter((j) => j.jobId !== msg.jobId)
      set({
        draftJobs: msg.status === 'cancelled' || msg.status === 'consumed'
          ? others
          : [...others, { owner: msg.owner, jobId: msg.jobId, status: msg.status, mode: msg.mode }],
      })
      return
    }
    if (ev === 'agents.changed' || ev === 'secrets.changed' || ev === 'settings.changed' || ev === 'draft.changed') {
      void m.refresh()
    }
  },

  go(page, ids = {}) {
    // Page nav always lands in the app shell — leaving the create/edit
    // surface here is what lets sidebar tabs escape the editor (§9).
    const leavingCreate = get().surface === 'create'
    set({
      page,
      automationId: ids.automationId !== undefined ? ids.automationId : get().automationId,
      executionId: ids.executionId !== undefined ? ids.executionId : get().executionId,
      // Unlike automationId/executionId this never persists across navigations — a plain
      // go('agentNew') must be the blank add form (§12).
      agentEditId: ids.agentEditId !== undefined ? ids.agentEditId : null,
      ...(leavingCreate ? { surface: 'app' as const, createFrom: null } : {}),
    })
    const m = get()
    if (m.executionId) touchExecutionMru(m.executionId)
    const evicted = evictExecutionCaches(m)
    if (evicted) set(evicted)
    syncHistory(get())
  },

  setSurface(surface, from = null) {
    if (surface !== 'onboard') passedOnboard = true
    if (surface === 'app' && get().surface === 'onboard') localStorage.setItem('ad-onboarded', '1')
    set({ surface, createFrom: from })
    syncHistory(get())
  },

  showToast(msg, ms = 2800) {
    set({ toast: msg })
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { if (get().toast === msg) set({ toast: null }) }, ms)
  },

  async loadExecution(executionId) {
    try {
      const e = await api.getExecution(executionId)
      // §19 monotonic refetch: a slow GET resolving after the finished event
      // must not regress a terminal record to queued/executing — drop the
      // stale body. Compared against the full record OR the list header:
      // before the first body lands, the finished event has only the header
      // to update, and a stale body written into the empty full slot would
      // out-rank it (readers go full-first). (§7 in-place retry re-enters
      // 'executing' via execution.started, never through here.)
      const cur = get().executionFull[executionId]
        ?? get().executions.find((x) => x.id === executionId)
      if (cur && !isLive(cur.status) && isLive(e.status)) {
        // §19: dropping is right, losing the body is not. With nothing in the
        // full slot the drop leaves the header alone — a page opened mid-run
        // whose finished event outraced its first GET would render zero steps
        // forever. Schedule exactly one fresh GET: it returns the terminal
        // body, which lands through the ordinary path below.
        if (!get().executionFull[executionId] && !executionRefetched.has(executionId)) {
          // Bounded: ids whose retry also dropped would otherwise sit here for
          // the process lifetime. Clearing merely re-arms one benign retry.
          if (executionRefetched.size > 1000) executionRefetched.clear()
          executionRefetched.add(executionId)
          setTimeout(() => { void get().loadExecution(executionId) }, 0)
        }
        return
      }
      executionRefetched.delete(executionId)
      set({ executionFull: { ...get().executionFull, [executionId]: e } })
    } catch { /* deleted */ }
  },

  async loadExecLogs(executionId, step, attempt) {
    const key = logKey(step ?? null, attempt ?? null)
    // Open the bucket before the fetch: the exec.log handler drops lines with
    // no bucket, so a line streamed while the snapshot request is in flight —
    // and written after the backend read the snapshot — would vanish for good.
    // With the bucket open it buffers here and the sequence merge below keeps it.
    {
      const all = get().execLogs
      const buckets = all[executionId] ?? {}
      if (!buckets[key]) set({ execLogs: { ...all, [executionId]: { ...buckets, [key]: [] } } })
    }
    try {
      // §7 log cap: only the last LOG_TAIL lines are ever fetched or kept.
      const { lines } = await api.getExecutionLogs(executionId, step, attempt, LOG_TAIL)
      const all = get().execLogs
      const buckets = all[executionId] ?? {}
      const bucket = buckets[key]
      // keep WS lines that streamed in past the fetched snapshot
      const sequence = lines.length ? lines[lines.length - 1].sequence : 0
      const tail = bucket ? bucket.filter((l) => l.sequence > sequence) : []
      const merged = [...lines, ...tail]
      set({
        execLogs: {
          ...all,
          [executionId]: { ...buckets, [key]: merged.length > LOG_TAIL ? merged.slice(-LOG_TAIL) : merged },
        },
      })
    } catch { /* deleted */ }
  },

  async loadAuto(automationId) {
    try {
      const a = await api.getAutomation(automationId)
      const automations = get().automations
      set({
        automations: automations.some((x) => x.id === automationId)
          ? automations.map((x) => (x.id === automationId ? a : x))
          : [...automations, a],
      })
    } catch { /* deleted */ }
  },

  beginTest(executionId) {
    touchExecutionMru(executionId) // counts as a view — survives eviction after clearTest too
    set({ test: { executionId } })
    void get().loadExecution(executionId) // steps/status render off the ordinary record
  },

  clearTest() { set({ test: null }) },
}))

// §19 log buckets: step+attempt select an attempt file, null/null the execution log.
export function logKey(step: number | null, attempt: number | null) {
  return step === null ? 'x.0' : `${step}.${attempt ?? 1}`
}

// §19: event/state rows are list-shape — no params/steps/latest/memory/… — so
// they merge over any stored record. Replacing would blank those fields on an
// open detail page: its sections unmount (flicker, focus loss, scroll jump)
// until the next full fetch puts them back.
function mergeAutoRows(cur: Automation[], rows: Automation[]): Automation[] {
  return rows.map((r) => {
    const old = cur.find((a) => a.id === r.id)
    return old ? { ...old, ...r } : r
  })
}

// §13: the dot is failed-or-overdue only — the other problems kinds are
// config nits the in-app amber chip covers.
export function trayAlertOn(automations: Automation[]) {
  return automations.some((a) => a.lastStatus === 'failed'
    || (a.problems ?? []).some((p) => p.kind === 'overdue'))
}

function updateTrayAlert(automations: Automation[]) {
  void window.autowright?.trayAlert(trayAlertOn(automations))
}

// ---------- history (§9: back works, never re-enters onboarding) ----------
let lastNav: NavSnap | null = null

function navSame(a: NavSnap | null, b: NavSnap | null) {
  return !!a && !!b && a.surface === b.surface && a.page === b.page
    && a.automationId === b.automationId && a.executionId === b.executionId && a.createFrom === b.createFrom
    && a.agentEditId === b.agentEditId
}

function syncHistory(m: Model) {
  if (restoring) return
  const s: NavSnap = {
    surface: m.surface, page: m.page, automationId: m.automationId, executionId: m.executionId,
    createFrom: m.createFrom, agentEditId: m.agentEditId,
  }
  if (navSame(s, lastNav)) return
  const first = lastNav === null
  lastNav = s
  try {
    // The session's first snapshot stamps the CURRENT entry (replaceState) —
    // a bare pushState would leave the initial history entry without adNav,
    // making the first page unreachable via back (§9: browser/OS back works).
    if (first) history.replaceState({ adNav: s }, '')
    else history.pushState({ adNav: s }, '')
  } catch { /* file:// quirks */ }
}

window.addEventListener('popstate', (e) => {
  const s = (e.state && (e.state as { adNav?: NavSnap }).adNav) || null
  if (!s) return
  if (s.surface === 'onboard' && passedOnboard) {
    try { history.pushState({ adNav: lastNav }, '') } catch { /* ignore */ }
    return
  }
  restoring = true
  useStore.setState({
    surface: s.surface, page: s.page, automationId: s.automationId, executionId: s.executionId,
    createFrom: s.createFrom ?? null, agentEditId: s.agentEditId ?? null,
  })
  const m = useStore.getState()
  if (m.executionId) touchExecutionMru(m.executionId)
  const evicted = evictExecutionCaches(m)
  if (evicted) useStore.setState(evicted)
  lastNav = s
  restoring = false
})

// §13: main pushes the menu-bar row's target here when the window already
// exists (a reload would drop the WS); fresh windows carry it in the hash.
window.autowright?.onOpenTarget?.((hash) => {
  const m = useStore.getState()
  if (m.surface === 'onboard' || m.surface === 'menubar') return
  const automationId = automationIdFromHash(hash)
  if (automationId) m.go('automation', { automationId })
})
