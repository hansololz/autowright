// Unit tests for src/store.ts — the real zustand store, with the api module
// mocked so refresh/loadExecution never hit the network. window.autowright is
// stubbed BEFORE the dynamic import so the module-level onOpenTarget hook
// registers against our capture.
//
// Note: autoIdFromHash and navSame are module-private (not exported), so they
// are exercised through their observable behavior — the onOpenTarget deep-link
// callback and history.pushState dedupe — instead of direct calls.
import { beforeAll, beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import type { Automation, Execution, LogLine, WsEvent } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    health: vi.fn(() => Promise.reject(new Error('offline'))),
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    checkAgent: vi.fn(() => Promise.reject(new Error('offline'))),
  },
}))

let store: typeof import('../src/store')
let apiMod: typeof import('../src/api')
let openTarget: ((hash: string) => void) | undefined
// Snapshot of the store's pristine state, captured once right after import —
// beforeEach restores ALL of it so no test leaks state into the next.
let initialState: Record<string, unknown>

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: (cb: (hash: string) => void) => { openTarget = cb },
    trayAlert: () => Promise.resolve(),
  }
  store = await import('../src/store')
  apiMod = await import('../src/api')
  initialState = { ...store.useStore.getState() }
})

const ex = (id: string, startedMs: number, over: Partial<Execution> = {}): Execution => ({
  id, automationId: 'a1', automationName: 'Automation', automationDeleted: false, versionLabel: 'v1',
  status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false, duration: '1s',
  started: 'now', startedMs, endedMs: 0, queuedMs: 0, durationMs: null, passStartedMs: 0, note: null, error: null, ...over,
})
// Fully-typed execution event — the backend always sends executionId/automationId
// alongside the record; automation stubs stay partial (`as never`) where the
// store only routes them.
const execEv = (
  event: 'execution.started' | 'execution.queued' | 'execution.finished',
  execution: Execution, automation: Automation | null = null,
): WsEvent => ({ event, executionId: execution.id, automationId: execution.automationId, execution, automation })
const logEv = (executionId: string, l: LogLine): WsEvent =>
  ({ event: 'execution.log', executionId, automationId: null, stepIndex: null, attempt: null, line: l })
const line = (sequence: number, text = 'line'): LogLine => ({ time: '00:00', kind: 'out', sequence, text })

beforeEach(() => {
  // Full reset: fresh deep copies of every data field, action functions shared.
  const fresh = Object.fromEntries(Object.entries(initialState).map(([k, v]) =>
    [k, typeof v === 'function' ? v : structuredClone(v)]))
  store.useStore.setState(fresh as unknown as ReturnType<typeof store.useStore.getState>, true)
})
afterEach(() => vi.useRealTimers())

describe('logKey', () => {
  it('null step → the execution log bucket', () => {
    expect(store.logKey(null, null)).toBe('x.0')
    expect(store.logKey(null, 3)).toBe('x.0')
  })
  it('step + attempt select the attempt file, attempt defaults to 1', () => {
    expect(store.logKey(2, 3)).toBe('2.3')
    expect(store.logKey(0, null)).toBe('0.1')
  })
})

describe('autoIdFromHash (via the onOpenTarget deep link)', () => {
  it('a valid 36-char uuid in the hash navigates to the automation', () => {
    expect(openTarget).toBeTypeOf('function')
    openTarget!('#/app?automation=123e4567-e89b-12d3-a456-426614174000')
    const m = store.useStore.getState()
    expect(m.page).toBe('automation')
    expect(m.automationId).toBe('123e4567-e89b-12d3-a456-426614174000')
  })
  it('missing or malformed auto id does not navigate', () => {
    openTarget!('#/app')
    expect(store.useStore.getState().page).toBe('automations')
    openTarget!('#/app?automation=SHORT')
    expect(store.useStore.getState().page).toBe('automations')
    openTarget!('#/app?automation=123E4567-E89B-12D3-A456-426614174000') // uppercase → no match
    expect(store.useStore.getState().page).toBe('automations')
  })
})

describe('navSame (via history.pushState dedupe)', () => {
  it('identical nav snapshots push exactly once; any changed field pushes again', () => {
    const spy = vi.spyOn(history, 'pushState')
    const m = store.useStore.getState()
    m.go('executions')
    const base = spy.mock.calls.length
    m.go('executions')                       // same page + same ids → deduped
    expect(spy.mock.calls.length).toBe(base)
    m.go('executions', { executionId: 'e1' })     // executionId differs → pushes
    expect(spy.mock.calls.length).toBe(base + 1)
    m.go('execution', { executionId: 'e1' })      // page differs → pushes
    expect(spy.mock.calls.length).toBe(base + 2)
    m.go('execution', { executionId: 'e1', automationId: 'a9' }) // automationId differs → pushes
    expect(spy.mock.calls.length).toBe(base + 3)
    spy.mockRestore()
  })
})

describe('applyEvent', () => {
  it('exec.started inserts and re-sorts by startedMs description, replacing an existing id', () => {
    store.useStore.setState({ executions: [ex('e1', 100), ex('e2', 50)] })
    const m = store.useStore.getState()
    m.applyEvent(execEv('execution.started', ex('e3', 200, { status: 'executing' })))
    expect(store.useStore.getState().executions.map((e) => e.id)).toEqual(['e3', 'e1', 'e2'])
    store.useStore.getState().applyEvent(execEv('execution.started', ex('e2', 300)))
    expect(store.useStore.getState().executions.map((e) => e.id)).toEqual(['e2', 'e3', 'e1'])
  })

  it('re-trims the §19 window: live rows all stay, only the 50 newest finished do', () => {
    // 60 finished rows in the window (the list arrives sorted newest first)
    // plus two live ones far down the list by start time
    const finished = Array.from({ length: 60 }, (_, i) => ex(`f${i}`, 1000 - i))
    store.useStore.setState({
      executions: [...finished, ex('q1', 5, { status: 'queued' }), ex('x1', 6, { status: 'executing' })],
      executionsTotal: 62,
    })
    store.useStore.getState().applyEvent(execEv('execution.started', ex('new', 2000, { status: 'executing' })))
    const got = store.useStore.getState().executions
    expect(got.filter((e) => e.status === 'succeeded').map((e) => e.id))
      .toEqual(finished.slice(0, 50).map((e) => e.id))
    // every live row survives the trim, wherever it sorts
    expect(got.filter((e) => e.status !== 'succeeded').map((e) => e.id)).toEqual(['new', 'x1', 'q1'])
    // the pill's total counts the new id, not the trimmed window
    expect(store.useStore.getState().executionsTotal).toBe(63)
  })

  it('exec.finished header merge preserves an already-loaded full body', () => {
    const full: Execution = {
      ...ex('e1', 100, { status: 'executing' }),
      steps: [{ name: 's1', status: 'succeeded', duration: '1s', attempts: [] }],
      result: { chip: 'done' },
    }
    store.useStore.setState({ executions: [ex('e1', 100, { status: 'executing' })], executionFull: { e1: full } })
    store.useStore.getState().applyEvent( // header: no steps/result
      execEv('execution.finished', ex('e1', 100, { status: 'failed', test: true })))
    const got = store.useStore.getState().executionFull.e1
    expect(got.status).toBe('failed')
    expect(got.steps).toEqual(full.steps)     // body kept through the merge
    expect(got.result).toEqual(full.result)
  })

  it('exec.log dedupes by sequence against the bucket tail, gaps accepted', () => {
    store.useStore.setState({ execLogs: { e9: { 'x.0': [line(5)] } } })
    const m = store.useStore.getState()
    m.applyEvent(logEv('e9', line(5)))
    expect(store.useStore.getState().execLogs.e9['x.0']).toHaveLength(1)
    store.useStore.getState().applyEvent(logEv('e9', line(4)))
    expect(store.useStore.getState().execLogs.e9['x.0']).toHaveLength(1)
    store.useStore.getState().applyEvent(logEv('e9', line(7)))
    expect(store.useStore.getState().execLogs.e9['x.0'].map((l) => l.sequence)).toEqual([5, 7])
    // no bucket open → the line is dropped, not crashed on
    store.useStore.getState().applyEvent(logEv('nope', line(1)))
    expect(store.useStore.getState().execLogs.nope).toBeUndefined()
  })

  it('exec.log trims the bucket to the last LOG_TAIL lines (§7 log cap)', () => {
    const N = store.LOG_TAIL
    // a bucket already at the cap
    store.useStore.setState({
      execLogs: { e9: { 'x.0': Array.from({ length: N }, (_, i) => line(i + 1)) } },
    })
    store.useStore.getState().applyEvent({
      event: 'execution.log', executionId: 'e9', stepIndex: null, attempt: null, line: line(N + 1),
    } as never)
    let bucket = store.useStore.getState().execLogs.e9['x.0']
    expect(bucket).toHaveLength(N)                      // capped, not N + 1
    expect(bucket[0].sequence).toBe(2)                  // the oldest line dropped
    expect(bucket[bucket.length - 1].sequence).toBe(N + 1)
    // and it stays capped as more lines stream in
    for (let i = 2; i <= 5; i++) {
      store.useStore.getState().applyEvent({
        event: 'execution.log', executionId: 'e9', stepIndex: null, attempt: null, line: line(N + i),
      } as never)
    }
    bucket = store.useStore.getState().execLogs.e9['x.0']
    expect(bucket).toHaveLength(N)
    expect(bucket[0].sequence).toBe(6)                  // > 1 → the §7 truncation signal
    expect(bucket[bucket.length - 1].sequence).toBe(N + 5)
  })

  it('loadExecLogs asks for the tail and never keeps more than LOG_TAIL lines', async () => {
    const N = store.LOG_TAIL
    const getExecutionLogs = vi.mocked(apiMod.api.getExecutionLogs)
    getExecutionLogs.mockClear()
    // a backend that ignores `tail` still can't blow the cap
    getExecutionLogs.mockResolvedValueOnce({
      lines: Array.from({ length: N + 50 }, (_, i) => line(i + 1)),
    } as never)
    await store.useStore.getState().loadExecLogs('e7')
    expect(getExecutionLogs).toHaveBeenCalledWith('e7', undefined, undefined, N)
    const bucket = store.useStore.getState().execLogs.e7['x.0']
    expect(bucket).toHaveLength(N)
    expect(bucket[0].sequence).toBe(51)
    expect(bucket[bucket.length - 1].sequence).toBe(N + 50)
  })

  it('exec.finished toasts a summary for real executions', () => {
    vi.useFakeTimers()
    store.useStore.getState().applyEvent(execEv('execution.finished', ex('e5', 1, { status: 'succeeded' }), { name: 'My Automation', resultChip: '3 changes' } as never))
    expect(store.useStore.getState().toast).toBe('My Automation finished — 3 changes.')
    vi.runAllTimers()
    expect(store.useStore.getState().toast).toBeNull()

    store.useStore.getState().applyEvent(execEv('execution.finished', ex('e6', 2, { status: 'failed' }), { name: 'My Automation', resultChip: null } as never))
    expect(store.useStore.getState().toast).toBe('My Automation failed — needs attention.')
    vi.runAllTimers()
  })

  it('exec.step replaces exactly the indexed step on a loaded full record (§19)', () => {
    const steps = [
      { name: 's1', status: 'succeeded', duration: '1s', attempts: [] },
      { name: 's2', status: 'executing', duration: '', attempts: [] },
    ] as NonNullable<Execution['steps']>
    store.useStore.setState({ executionFull: { e1: { ...ex('e1', 100), steps } } })
    const updated: NonNullable<Execution['steps']>[number] = { name: 's2', status: 'succeeded', duration: '2s', attempts: [] }
    store.useStore.getState().applyEvent({ event: 'execution.step', executionId: 'e1', automationId: null, index: 1, step: updated })
    const got = store.useStore.getState().executionFull.e1.steps!
    expect(got[0]).toEqual(steps[0])   // untouched
    expect(got[1]).toEqual(updated)    // replaced wholesale
    expect(got).toHaveLength(2)
  })

  it('exec.step is a no-op when no full record is loaded', () => {
    store.useStore.setState({ executionFull: { other: { ...ex('other', 1) } } }) // no steps either
    const before = store.useStore.getState().executionFull
    store.useStore.getState().applyEvent({
      event: 'execution.step', executionId: 'nope', automationId: null, index: 0,
      step: { name: 's', status: 'succeeded', duration: '1s', attempts: [] },
    })
    // header-only record (no steps) is also left alone
    store.useStore.getState().applyEvent({
      event: 'execution.step', executionId: 'other', automationId: null, index: 0,
      step: { name: 's', status: 'succeeded', duration: '1s', attempts: [] },
    })
    expect(store.useStore.getState().executionFull).toEqual(before)
  })

  it('beginTest tracks only the executionId and fetches the full record; clearTest drops it', () => {
    const getExecution = vi.mocked(apiMod.api.getExecution)
    getExecution.mockClear()
    store.useStore.getState().beginTest('eT')
    // §11: steps/status render off the ordinary exec record — beginTest holds
    // no analysis state of its own, just the tracked id, and loads the record.
    expect(store.useStore.getState().test).toEqual({ executionId: 'eT' })
    expect(getExecution).toHaveBeenCalledWith('eT')
    store.useStore.getState().clearTest()
    expect(store.useStore.getState().test).toBeNull()
  })

  it('fixExec is plain handed-off state, untouched by events', () => {
    // §7/§9.2 Fix with AI: the store only carries the failed executionId to the
    // editor; no event mutates it — CreateFlow consumes and clears it on mount.
    store.useStore.setState({ fixExec: 'eF' })
    store.useStore.getState().applyEvent(execEv('execution.finished', ex('eF', 1, { status: 'failed' }), { name: 'A', resultChip: null } as never))
    expect(store.useStore.getState().fixExec).toBe('eF')
  })

  it('toast suppressed for test executions and for cancelled status', () => {
    vi.useFakeTimers()
    store.useStore.getState().applyEvent(execEv('execution.finished', ex('e7', 3, { status: 'succeeded', test: true }), { name: 'My Automation', resultChip: null } as never))
    expect(store.useStore.getState().toast).toBeNull()
    store.useStore.getState().applyEvent(execEv('execution.finished', ex('e8', 4, { status: 'cancelled' }), { name: 'My Automation', resultChip: null } as never))
    expect(store.useStore.getState().toast).toBeNull()
  })

  it('exec.finished refetches the full automation record only when one is loaded (§19)', () => {
    const getAutomation = vi.mocked(apiMod.api.getAutomation)
    const row = (over: Record<string, unknown> = {}) =>
      ({ id: 'a1', name: 'Auto', lastStatus: 'none', triggers: [], live: [], ...over }) as never
    const finished = (id: string, over: Partial<Execution> = {}) => // cancelled: no toast timer to flush
      execEv('execution.finished', ex(id, 1, { status: 'cancelled', ...over }), row())
    // list-shape row only (detail page never opened) → no refetch
    getAutomation.mockClear()
    store.useStore.setState({ automations: [row()] })
    store.useStore.getState().applyEvent(finished('e1'))
    expect(getAutomation).not.toHaveBeenCalled()
    // full record loaded (`latest` key present, even null) → refetch
    store.useStore.setState({ automations: [row({ latest: null })] })
    store.useStore.getState().applyEvent(finished('e2'))
    expect(getAutomation).toHaveBeenCalledWith('a1')
    // §4.5 test executions are draft-scoped — never refetch
    getAutomation.mockClear()
    store.useStore.getState().applyEvent(finished('e3', { test: true }))
    expect(getAutomation).not.toHaveBeenCalled()
  })
})

describe('applyEvent — automation.changed row patching (§19)', () => {
  // Minimal list rows — the store only routes them, never reads deep fields.
  const auto = (id: string, over: Record<string, unknown> = {}) =>
    ({ id, name: `Auto ${id}`, lastStatus: 'none', triggers: [], live: [], ...over }) as never

  it('entity payload patches the one row in place — no /state refetch', () => {
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    store.useStore.setState({ automations: [auto('a1'), auto('a2')] })
    store.useStore.getState().applyEvent({
      event: 'automation.changed', automationId: 'a2', automation: auto('a2', { name: 'Renamed' }),
    })
    const got = store.useStore.getState().automations
    expect(got.map((a) => a.id)).toEqual(['a1', 'a2'])   // order kept
    expect(got[1].name).toBe('Renamed')
    expect(state).not.toHaveBeenCalled()
  })

  it('list-shape row merges over a full record — full-only fields survive', () => {
    // §19: the event row has no params/steps/latest — replacing would blank an
    // open detail page's sections (flicker, focus loss, scroll jump).
    const full = auto('a1', {
      params: [{ name: 'p', kind: 'text', value: 'v' }],
      steps: [{ name: 's1' }],
      latest: { executionId: 'e1' },
    })
    store.useStore.setState({ automations: [full] })
    store.useStore.getState().applyEvent({
      event: 'automation.changed', automationId: 'a1', automation: auto('a1', { lastStatus: 'succeeded' }),
    })
    const got = store.useStore.getState().automations[0]
    expect(got.lastStatus).toBe('succeeded')
    expect(got.params).toEqual([{ name: 'p', kind: 'text', value: 'v' }])
    expect(got.steps).toEqual([{ name: 's1' }])
    expect(got.latest).toEqual({ executionId: 'e1' })
  })

  it('refresh merges /state list rows over stored full records', async () => {
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    const full = auto('a1', { params: [{ name: 'p', kind: 'text', value: 'v' }] })
    store.useStore.setState({ automations: [full] })
    state.mockResolvedValueOnce({
      automations: [auto('a1', { name: 'Renamed' })], executions: [], agents: [], secrets: [],
      settings: {}, pendingDraft: null,
    } as never)
    await store.useStore.getState().refresh()
    const got = store.useStore.getState().automations[0]
    expect(got.name).toBe('Renamed')
    expect(got.params).toEqual([{ name: 'p', kind: 'text', value: 'v' }])
  })

  it('refresh applies the /state version (a version-synced backend reaches About)', async () => {
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    store.useStore.setState({ version: '0.7.0' })
    state.mockResolvedValueOnce({
      version: '0.8.0', automations: [], executions: [], agents: [], secrets: [],
      settings: {}, pendingDraft: null,
    } as never)
    await store.useStore.getState().refresh()
    expect(store.useStore.getState().version).toBe('0.8.0')
  })

  it('automation: null removes the deleted row', () => {
    store.useStore.setState({ automations: [auto('a1'), auto('a2')] })
    store.useStore.getState().applyEvent({ event: 'automation.changed', automationId: 'a1', automation: null })
    expect(store.useStore.getState().automations.map((a) => a.id)).toEqual(['a2'])
  })

  it('bare event and unknown-id entity both fall back to a full refresh', () => {
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    store.useStore.setState({ automations: [auto('a1')] })
    store.useStore.getState().applyEvent({ event: 'automation.changed' })
    expect(state).toHaveBeenCalledTimes(1)
    // a row the client has never seen: only the server knows list ordering
    store.useStore.getState().applyEvent({
      event: 'automation.changed', automationId: 'aNew', automation: auto('aNew'),
    })
    expect(state).toHaveBeenCalledTimes(2)
    expect(store.useStore.getState().automations.map((a) => a.id)).toEqual(['a1'])
  })

  it('execution events patch the owning automation row and never refetch', () => {
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    store.useStore.setState({ automations: [auto('a1')] })
    store.useStore.getState().applyEvent({
      event: 'execution.started',
      executionId: 'e1', automationId: 'a1',
      execution: ex('e1', 100, { status: 'executing' }),
      automation: auto('a1', { lastStatus: 'executing', live: ['e1'] }),
    })
    expect(store.useStore.getState().automations[0].lastStatus).toBe('executing')
    // §4.5 test executions carry automation: null — row untouched, no refetch
    store.useStore.getState().applyEvent({
      event: 'execution.finished',
      executionId: 'e2', automationId: 'a1',
      execution: ex('e2', 200, { status: 'succeeded', test: true }),
      automation: null,
    })
    expect(store.useStore.getState().automations[0].lastStatus).toBe('executing')
    expect(state).not.toHaveBeenCalled()
  })
})

describe('applyEvent — harness.install / ollama.pull live progress (§10/§12)', () => {
  it('harness.install keys progress by provider id and merges across ids', () => {
    const m = store.useStore.getState()
    m.applyEvent({ event: 'harness.install', id: 'claude', line: 'Downloading…', percent: 42, done: false })
    m.applyEvent({ event: 'harness.install', id: 'ollama', line: 'Unpacking…', done: false })
    const hi = store.useStore.getState().harnessInstall
    expect(hi.claude).toEqual({ line: 'Downloading…', percent: 42, done: false, ok: undefined, error: undefined })
    expect(hi.ollama).toEqual({ line: 'Unpacking…', percent: undefined, done: false, ok: undefined, error: undefined })
  })

  it('harness.install terminal events carry done/ok and the failure line', () => {
    const m = store.useStore.getState()
    m.applyEvent({ event: 'harness.install', id: 'codex', done: true, ok: true })
    expect(store.useStore.getState().harnessInstall.codex.done).toBe(true)
    expect(store.useStore.getState().harnessInstall.codex.ok).toBe(true)
    m.applyEvent({ event: 'harness.install', id: 'gemini', done: true, ok: false, error: 'Gemini CLI needs Node.js' })
    const g = store.useStore.getState().harnessInstall.gemini
    expect(g.ok).toBe(false)
    expect(g.error).toBe('Gemini CLI needs Node.js')
  })

  it('ollama.pull holds the latest event — the UI renders its §19 overall percent', () => {
    const m = store.useStore.getState()
    m.applyEvent({ event: 'ollama.pull', model: 'qwen3:8b', line: 'pulling 3f2a… 12% 624 MB/5.2 GB', percent: 12, done: false })
    expect(store.useStore.getState().ollamaPull).toEqual({ model: 'qwen3:8b', line: 'pulling 3f2a… 12% 624 MB/5.2 GB', percent: 12, done: false, ok: undefined })
    m.applyEvent({ event: 'ollama.pull', model: 'qwen3:8b', line: '', percent: 100, done: true, ok: true })
    expect(store.useStore.getState().ollamaPull).toEqual({ model: 'qwen3:8b', line: '', percent: 100, done: true, ok: true })
  })

  it('loadExecLogs merges the snapshot with WS lines streamed past it', async () => {
    let resolveFetch!: (v: { lines: LogLine[] }) => void
    ;(apiMod.api.getExecutionLogs as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise((r) => { resolveFetch = r }),
    )
    const p = store.useStore.getState().loadExecLogs('e1')
    // the bucket opens synchronously, before the snapshot resolves…
    expect(store.useStore.getState().execLogs.e1['x.0']).toEqual([])
    // …so a line streamed while the fetch is in flight buffers there
    store.useStore.getState().applyEvent({
      event: 'execution.log', executionId: 'e1', automationId: null, stepIndex: null, attempt: null, line: line(10),
    })
    // snapshot only covers up to sequence 8 — the WS line past it must survive
    resolveFetch({ lines: [line(7), line(8)] })
    await p
    expect(store.useStore.getState().execLogs.e1['x.0'].map((l) => l.sequence)).toEqual([7, 8, 10])
  })

  it('refresh: an out-of-order older /state snapshot never clobbers a newer one', async () => {
    const snap = (executions: Execution[]) => ({
      version: 'v', automations: [], executions, agents: [], secrets: [],
      settings: null, pendingDraft: null,
    })
    const state = apiMod.api.state as ReturnType<typeof vi.fn>
    let resolveOld!: (v: unknown) => void
    let resolveNew!: (v: unknown) => void
    state.mockReturnValueOnce(new Promise((r) => { resolveOld = r }))
    state.mockReturnValueOnce(new Promise((r) => { resolveNew = r }))
    const pOld = store.useStore.getState().refresh()
    const pNew = store.useStore.getState().refresh()
    // the newer request resolves first and lands…
    resolveNew(snap([ex('fresh', 2)]))
    await pNew
    expect(store.useStore.getState().executions.map((e) => e.id)).toEqual(['fresh'])
    // …then the stale older response arrives and must be discarded
    resolveOld(snap([ex('stale', 1)]))
    await pOld
    expect(store.useStore.getState().executions.map((e) => e.id)).toEqual(['fresh'])
  })

  it('agents.changed nudges a full refresh (§19: clients re-GET /state)', () => {
    const orig = store.useStore.getState().refresh
    const spy = vi.fn(async () => {})
    store.useStore.setState({ refresh: spy })
    try {
      store.useStore.getState().applyEvent({ event: 'agents.changed' })
      expect(spy).toHaveBeenCalledTimes(1)
    } finally {
      store.useStore.setState({ refresh: orig })
    }
  })
})

describe('applyEvent — changed nudges and ws.open recovery (§19)', () => {
  it('secrets/settings/draft.changed each nudge a full refresh', () => {
    const orig = store.useStore.getState().refresh
    const spy = vi.fn(async () => {})
    store.useStore.setState({ refresh: spy })
    try {
      for (const event of ['secrets.changed', 'settings.changed', 'draft.changed']) {
        store.useStore.getState().applyEvent({ event } as never)
      }
      expect(spy).toHaveBeenCalledTimes(3)
    } finally {
      store.useStore.setState({ refresh: orig })
    }
  })

  it('execution.started for an already-loaded record refetches its body (§7 retry re-publish)', () => {
    const getExecution = vi.mocked(apiMod.api.getExecution)
    getExecution.mockClear()
    store.useStore.setState({ executionFull: { e1: ex('e1', 100) } })
    store.useStore.getState().applyEvent(execEv('execution.started', ex('e1', 100, { status: 'executing' })))
    expect(getExecution).toHaveBeenCalledWith('e1')
  })

  it('ws.open refetches executing records, the viewed record, and their open log buckets', () => {
    const state = vi.mocked(apiMod.api.state)
    const getExecution = vi.mocked(apiMod.api.getExecution)
    const getExecutionLogs = vi.mocked(apiMod.api.getExecutionLogs)
    state.mockClear(); getExecution.mockClear(); getExecutionLogs.mockClear()
    store.useStore.setState({
      executionId: 'viewed',
      executionFull: {
        live: ex('live', 1, { status: 'executing' }),
        viewed: ex('viewed', 2),
        done: ex('done', 3),           // terminal, not on screen → left alone
      },
      execLogs: { live: { 'x.0': [line(1)], '1.2': [line(1)] } },
    })
    store.useStore.getState().applyEvent({ event: 'ws.open' } as never)
    expect(state).toHaveBeenCalledTimes(1)                 // the reconnect refresh
    expect(getExecution).toHaveBeenCalledWith('live')
    expect(getExecution).toHaveBeenCalledWith('viewed')
    expect(getExecution).not.toHaveBeenCalledWith('done')
    // 'x.0' → the execution log, '1.2' → step 1 attempt 2; both ask for the §7 tail
    expect(getExecutionLogs).toHaveBeenCalledWith('live', undefined, undefined, store.LOG_TAIL)
    expect(getExecutionLogs).toHaveBeenCalledWith('live', 1, 2, store.LOG_TAIL)
  })

  it('refresh refetches when a WS execution event lands while /state is in flight', async () => {
    const snap = (executions: Execution[]) => ({
      version: 'v', automations: [], executions, agents: [], secrets: [],
      settings: null, pendingDraft: null,
    })
    const state = vi.mocked(apiMod.api.state)
    state.mockClear()
    state.mockImplementationOnce(async () => {
      // the event makes this snapshot stale on arrival — it must not land
      store.useStore.getState().applyEvent(execEv('execution.started', ex('mid', 5, { status: 'executing' })))
      return snap([]) as never
    })
    state.mockResolvedValueOnce(snap([ex('mid', 5)]) as never)
    await store.useStore.getState().refresh()
    expect(state).toHaveBeenCalledTimes(2)
    expect(store.useStore.getState().executions.map((e) => e.id)).toEqual(['mid'])
  })
})

describe('readHealth — §19 /health platform gating (§2/§9)', () => {
  const health = () => vi.mocked(apiMod.api.health as unknown as () => Promise<unknown>)

  it('starts macOS-identical: every capability true until the first read', () => {
    const m = store.useStore.getState()
    expect(m.platformOs).toBe('')
    expect(m.platformCapabilities).toEqual({
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
    })
  })

  it('keeps the os token and the capability flags from the response', async () => {
    health().mockResolvedValueOnce({
      version: '0.4.1', app: 'Autowright', os: 'windows',
      capabilities: {
        imessage: false, notifications: false, keepAwake: false, service: true, agentInstall: false,
      },
    })
    await store.useStore.getState().readHealth()
    const m = store.useStore.getState()
    expect(m.platformOs).toBe('windows')
    expect(m.platformCapabilities).toEqual({
      imessage: false, notifications: false, keepAwake: false, service: true, agentInstall: false,
    })
  })

  it('a flag the backend omits keeps its macOS-identical default', async () => {
    health().mockResolvedValueOnce({
      version: '0.4.1', app: 'Autowright', os: 'linux',
      capabilities: { imessage: false } as never,
    })
    await store.useStore.getState().readHealth()
    expect(store.useStore.getState().platformCapabilities).toEqual({
      imessage: false, notifications: true, keepAwake: true, service: true, agentInstall: true,
    })
  })

  it('a failed read leaves the flags alone — the connect retry re-reads them', async () => {
    health().mockResolvedValueOnce({
      version: '0.4.1', app: 'Autowright', os: 'windows',
      capabilities: {
        imessage: false, notifications: false, keepAwake: false, service: true, agentInstall: false,
      },
    })
    await store.useStore.getState().readHealth()
    health().mockRejectedValueOnce(new Error('offline'))
    await store.useStore.getState().readHealth()
    expect(store.useStore.getState().platformOs).toBe('windows')
    expect(store.useStore.getState().platformCapabilities.imessage).toBe(false)
  })

  it('ws.open re-reads them — a reconnect can land on a restarted backend', async () => {
    const h = health()
    h.mockClear()
    h.mockResolvedValueOnce({
      version: '0.4.1', app: 'Autowright', os: 'windows',
      capabilities: {
        imessage: false, notifications: false, keepAwake: false, service: true, agentInstall: false,
      },
    })
    store.useStore.getState().applyEvent({ event: 'ws.open' } as never)
    expect(h).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(store.useStore.getState().platformOs).toBe('windows'))
  })
})

describe('runAgentCheck (§12 status badge cache)', () => {
  it('shows the pending badge while in flight, then caches the ready result', async () => {
    const check = vi.mocked(apiMod.api.checkAgent)
    let resolve!: (v: { status: string }) => void
    check.mockReturnValueOnce(new Promise((r) => { resolve = r }) as never)
    const p = store.useStore.getState().runAgentCheck('g1')
    expect(store.useStore.getState().agentChecks.g1).toBe('checking')
    resolve({ status: 'ready' })
    await expect(p).resolves.toBe('ready')
    expect(store.useStore.getState().agentChecks.g1).toBe('ready')
  })
  it('non-ready statuses and a failed call both land as needs; pending label is overridable', async () => {
    const check = vi.mocked(apiMod.api.checkAgent)
    check.mockResolvedValueOnce({ status: 'signed_out' } as never)
    await expect(store.useStore.getState().runAgentCheck('g2')).resolves.toBe('needs')
    expect(store.useStore.getState().agentChecks.g2).toBe('needs')
    check.mockRejectedValueOnce(new Error('offline'))
    const p = store.useStore.getState().runAgentCheck('g3', 'connecting')
    expect(store.useStore.getState().agentChecks.g3).toBe('connecting')
    await expect(p).resolves.toBe('needs')
  })
})

describe('loadExecution / setSurface', () => {
  it('loadExecution stores the fetched record under its id', async () => {
    vi.mocked(apiMod.api.getExecution).mockResolvedValueOnce(ex('eL', 1))
    await store.useStore.getState().loadExecution('eL')
    expect(store.useStore.getState().executionFull.eL).toEqual(ex('eL', 1))
  })
  // §19 monotonic refetch: a GET resolving after the finished event must not
  // regress a terminal record to queued/executing. Dropping is right — but a
  // drop with nothing in the full slot would lose the body outright (the page
  // renders zero steps forever), so that one case schedules a single refetch.
  it('drops a stale non-terminal body over a terminal full record — and refetches nothing', async () => {
    const full = ex('eM1', 100, { status: 'succeeded' })
    store.useStore.setState({ executions: [], executionFull: { eM1: full } })
    vi.mocked(apiMod.api.getExecution).mockReset()
      .mockResolvedValue(ex('eM1', 100, { status: 'executing' }))
    await store.useStore.getState().loadExecution('eM1')
    expect(store.useStore.getState().executionFull.eM1).toEqual(full)
    // A body already landed, so nothing is stranded — no retry is owed.
    await new Promise((r) => setTimeout(r, 20))
    expect(apiMod.api.getExecution).toHaveBeenCalledTimes(1)
  })

  it('drops a stale body over the list header alone, then the one refetch lands it', async () => {
    const header = ex('eM2', 100, { status: 'succeeded' })
    const body = ex('eM2', 100, { status: 'succeeded', duration: '4s' })
    store.useStore.setState({ executions: [header], executionFull: {} })
    vi.mocked(apiMod.api.getExecution).mockReset()
      .mockResolvedValueOnce(ex('eM2', 100, { status: 'executing' }))
      .mockResolvedValueOnce(body)
    await store.useStore.getState().loadExecution('eM2')
    // The stale body is dropped, so the full slot is still empty…
    expect(store.useStore.getState().executionFull.eM2).toBeUndefined()
    // …and the scheduled refetch brings the terminal body in through the
    // ordinary path.
    await vi.waitFor(() => expect(store.useStore.getState().executionFull.eM2).toEqual(body))
    expect(apiMod.api.getExecution).toHaveBeenCalledTimes(2)
  })

  it('the recovery refetch is one-shot — a body that stays stale never loops', async () => {
    store.useStore.setState({ executions: [ex('eM3', 100, { status: 'failed' })], executionFull: {} })
    vi.mocked(apiMod.api.getExecution).mockReset()
      .mockResolvedValue(ex('eM3', 100, { status: 'executing' }))
    await store.useStore.getState().loadExecution('eM3')
    await new Promise((r) => setTimeout(r, 20))
    // The first call plus its one retry — and no third.
    expect(apiMod.api.getExecution).toHaveBeenCalledTimes(2)
    expect(store.useStore.getState().executionFull.eM3).toBeUndefined()
  })

  it('setSurface app from onboard stamps ad-onboarded (§10)', () => {
    // this Node build exposes no working localStorage global — stub the
    // production mechanism (§15) with an in-memory one for the assertion
    const backing = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => backing.get(k) ?? null,
      setItem: (k: string, v: string) => backing.set(k, v),
    })
    try {
      store.useStore.setState({ surface: 'onboard' })
      store.useStore.getState().setSurface('app')
      expect(backing.get('ad-onboarded')).toBe('1')
      expect(store.useStore.getState().surface).toBe('app')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('history restore (popstate, §9)', () => {
  const nav = (over: Record<string, unknown> = {}) => ({
    adNav: {
      surface: 'app', page: 'automations', automationId: null, executionId: null,
      createFrom: null, agentEditId: null, ...over,
    },
  })

  it('a popstate with an adNav snapshot restores surface/page/ids', () => {
    store.useStore.getState().go('executions')
    window.dispatchEvent(new PopStateEvent('popstate', {
      state: nav({ page: 'execution', automationId: 'a1', executionId: 'e1' }),
    }))
    const m = store.useStore.getState()
    expect(m.page).toBe('execution')
    expect(m.automationId).toBe('a1')
    expect(m.executionId).toBe('e1')
    expect(m.surface).toBe('app')
  })

  it('a popstate without adNav state is ignored', () => {
    store.useStore.getState().go('executions')
    window.dispatchEvent(new PopStateEvent('popstate', { state: null }))
    expect(store.useStore.getState().page).toBe('executions')
  })

  it('back into onboarding is refused once passed — the entry is re-pushed', () => {
    store.useStore.getState().setSurface('app')     // marks onboarding as passed
    const spy = vi.spyOn(history, 'pushState')
    window.dispatchEvent(new PopStateEvent('popstate', { state: nav({ surface: 'onboard' }) }))
    expect(store.useStore.getState().surface).toBe('app')
    expect(spy).toHaveBeenCalledTimes(1)
    spy.mockRestore()
  })
})

describe('onOpenTarget surface guard (§13)', () => {
  it('deep links are ignored on the menubar surface', () => {
    store.useStore.setState({ surface: 'menubar' })
    openTarget!('#/app?automation=123e4567-e89b-12d3-a456-426614174000')
    expect(store.useStore.getState().page).toBe('automations')
    expect(store.useStore.getState().automationId).toBeNull()
  })
})

describe('execution cache eviction (MRU: current + 5 most recent)', () => {
  // Seeds a full record + an open log bucket for id, then navigates to it —
  // the same order the real execution page produces (go, then loads).
  const view = (id: string) => {
    const m = store.useStore.getState()
    store.useStore.setState({
      executionFull: { ...m.executionFull, [id]: ex(id, 1) },
      execLogs: { ...m.execLogs, [id]: { 'x.0': [line(1)] } },
    })
    store.useStore.getState().go('execution', { executionId: id })
  }

  it('viewing a 7th execution evicts the oldest full record and its log buckets', () => {
    const ids = ['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']
    for (const id of ids) view(id)
    const m = store.useStore.getState()
    // current (e7) + the 5 before it survive; e1 (oldest) is gone from both caches
    expect(Object.keys(m.executionFull).sort()).toEqual(['e2', 'e3', 'e4', 'e5', 'e6', 'e7'])
    expect(m.execLogs.e1).toBeUndefined()
    expect(m.executionFull.e7).toBeDefined()
    expect(m.execLogs.e7['x.0']).toHaveLength(1)
  })

  it('re-viewing an execution refreshes its MRU slot', () => {
    for (const id of ['r1', 'r2', 'r3', 'r4', 'r5', 'r6']) view(id)
    view('r1')  // r1 becomes most recent again
    view('r7')  // now r2 is the oldest → evicted
    const m = store.useStore.getState()
    expect(m.executionFull.r1).toBeDefined()
    expect(m.executionFull.r2).toBeUndefined()
    expect(m.execLogs.r2).toBeUndefined()
  })

  it('the tracked test execution survives eviction while the test is live', () => {
    store.useStore.getState().beginTest('eT') // loadExecution is mocked offline — record seeded below
    const m0 = store.useStore.getState()
    store.useStore.setState({
      executionFull: { ...m0.executionFull, eT: ex('eT', 1, { test: true }) },
      execLogs: { ...m0.execLogs, eT: { 'x.0': [line(1)] } },
    })
    for (let i = 1; i <= 7; i++) view(`t${i}`) // pushes eT out of the MRU window
    const m = store.useStore.getState()
    expect(m.executionFull.eT).toBeDefined()   // live test never evicted mid-view
    expect(m.execLogs.eT['x.0']).toHaveLength(1)
    expect(m.executionFull.t1).toBeUndefined() // ordinary oldest still evicted
  })
})

describe('draftjob.changed — the §19 background-continuation snapshot', () => {
  it('building and held statuses upsert the row; cancelled and consumed remove it', () => {
    store.useStore.setState({ draftJobs: [] })
    const apply = (status: string) => store.useStore.getState().applyEvent(
      { event: 'draftjob.changed', owner: 'a1', jobId: 'j1', status, mode: 'chat' } as never)
    apply('building')
    expect(store.useStore.getState().draftJobs).toEqual(
      [{ owner: 'a1', jobId: 'j1', status: 'building', mode: 'chat' }])
    apply('done') // a held outcome stays listed until consumed
    expect(store.useStore.getState().draftJobs).toEqual(
      [{ owner: 'a1', jobId: 'j1', status: 'done', mode: 'chat' }])
    apply('consumed')
    expect(store.useStore.getState().draftJobs).toEqual([])
    apply('building')
    apply('cancelled')
    expect(store.useStore.getState().draftJobs).toEqual([])
  })

  it('rows for other jobs survive an unrelated upsert or removal', () => {
    store.useStore.setState({ draftJobs: [{ owner: 'pending', jobId: 'jp', status: 'building', mode: 'sync' }] })
    store.useStore.getState().applyEvent(
      { event: 'draftjob.changed', owner: 'a1', jobId: 'j1', status: 'building', mode: 'chat' } as never)
    expect(store.useStore.getState().draftJobs).toHaveLength(2)
    store.useStore.getState().applyEvent(
      { event: 'draftjob.changed', owner: 'a1', jobId: 'j1', status: 'cancelled', mode: 'chat' } as never)
    expect(store.useStore.getState().draftJobs).toEqual(
      [{ owner: 'pending', jobId: 'jp', status: 'building', mode: 'sync' }])
  })
})

describe('trayAlertOn (§13 tray dot predicate)', () => {
  const auto = (over: Record<string, unknown> = {}) =>
    ({ id: 'a1', name: 'A', lastStatus: 'none', problems: [], ...over }) as never

  it('lights for a failed automation', () => {
    expect(store.trayAlertOn([auto({ lastStatus: 'failed' })])).toBe(true)
  })

  it('lights for an overdue automation', () => {
    expect(store.trayAlertOn([auto({ problems: [
      { kind: 'overdue', label: 'Scheduled executions are being missed - it has never run.' },
    ] })])).toBe(true)
  })

  it('stays dark for every other problems kind and for clean automations', () => {
    expect(store.trayAlertOn([auto()])).toBe(false)
    // §13: the dot is failed-or-overdue only — config nits never light it
    expect(store.trayAlertOn([auto({ problems: [
      { kind: 'package-missing', label: 'x' }, { kind: 'secret-unset', label: 'y' },
    ] })])).toBe(false)
  })

  it('tolerates event rows without a problems field', () => {
    expect(store.trayAlertOn([{ id: 'a2', name: 'B', lastStatus: 'none' } as never])).toBe(false)
  })
})
