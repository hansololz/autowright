// Regression tests for the 2026-10-31 third sweep, renderer shell: the §12
// agent edit page redirects when its agent disappears from the live list; the
// §9 coalesced /state refresh never applies a snapshot live events overtook
// (one more attempt re-arms a second later instead); the §9.1 start-fresh
// chain disables its buttons and honours the §9 landing rule; a refused
// reveal toasts its reason instead of reporting a reveal (§9); and a failed
// iMessage Grant probe toasts (§9.2). Components render for real (happy-dom,
// StrictMode via the setup file) against the real store with the api module
// mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Execution, WsEvent } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    triggersPreview: vi.fn(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map(() => ({ valid: true, label: 'Every day', short: 'Every day', nextAtMs: null })),
    })),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'unknown' })),
    imessageAutomationProbe: vi.fn(async () => ({ automation: 'granted' })),
    deleteDraft: vi.fn(async () => ({})),
    cancelDraftJob: vi.fn(async () => ({})),
    putChat: vi.fn(async () => ({})),
    clearMemory: vi.fn(async () => ({})),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
    detectAgents: vi.fn(async () => ['claude', 'gemini', 'codex', 'opencode'].map((id) => (
      { id, name: id, installed: true, signedIn: true, detail: '' }))),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    patchAgent: vi.fn(async () => ({})),
    deleteAgent: vi.fn(async () => ({})),
    checkAgent: vi.fn(async () => ({ status: 'ready' })),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let AgentNewPage: typeof import('../src/pages/AgentNewPage').default
let AutomationsList: typeof import('../src/pages/AutomationsList').default
let MemoryCard: typeof import('../src/pages/detail/MemoryCard').MemoryCard
let TriggerEditor: typeof import('../src/pages/detail/TriggerEditor').TriggerEditor
const revealPath = vi.fn(async (_path: string): Promise<{ ok: true } | { ok: false; error: string }> => ({ ok: true }))

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath,
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  AgentNewPage = (await import('../src/pages/AgentNewPage')).default
  AutomationsList = (await import('../src/pages/AutomationsList')).default
  MemoryCard = (await import('../src/pages/detail/MemoryCard')).MemoryCard
  TriggerEditor = (await import('../src/pages/detail/TriggerEditor')).TriggerEditor
})

const CAPABILITIES = { imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true }

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({
    surface: 'app', page: 'automations', automations: [], agents: [], secrets: [],
    draftJobs: [], pendingDraft: null, toast: null, platformOs: 'macos',
    platformCapabilities: CAPABILITIES,
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const toast = () => storeMod.useStore.getState().toast

describe('X the agent edit page reads the live agents list (§12)', () => {
  it('an agent removed while its edit page is shown redirects to Agents', async () => {
    storeMod.useStore.setState({
      page: 'agentNew',
      agents: [{ id: 'g1', name: 'Writer', harness: 'Claude Code', mode: 'default', model: null }] as never,
      agentEditId: 'g1',
    })
    render(<AgentNewPage />)
    expect(screen.getByText('Save changes')).toBeTruthy()
    expect(storeMod.useStore.getState().page).toBe('agentNew')
    act(() => { storeMod.useStore.setState({ agents: [] }) })
    await waitFor(() => expect(storeMod.useStore.getState().page).toBe('agents'))
  })
})

describe('Y the coalesced /state refresh never applies an overtaken snapshot (§9)', () => {
  const ex = (id: string, over: Partial<Execution> = {}): Execution => ({
    id, automationId: 'a1', automationName: 'Automation', automationDeleted: false, versionLabel: 'v1',
    status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false, duration: '1s',
    started: 'now', startedMs: 5, endedMs: 0, queuedMs: 0, durationMs: null, passStartedMs: 0,
    note: null, error: null, ...over,
  })
  const snap = (executions: Execution[]) => ({
    version: 'v', automations: [], executions, executionsTotal: executions.length,
    agents: [], secrets: [], settings: null, pendingDraft: null,
  })
  const finished = (): WsEvent => {
    const execution = ex('mid')
    return { event: 'execution.finished', executionId: 'mid', automationId: 'a1', execution, automation: null }
  }

  it('events during each of 4 fetches: the 4th stale body is dropped, a 5th attempt applies a second later', async () => {
    vi.useFakeTimers()
    storeMod.useStore.setState({ executions: [ex('mid', { status: 'executing' })], executionsTotal: 1 })
    let calls = 0
    mockedApi.state.mockImplementation(async () => {
      calls += 1
      if (calls <= 4) {
        // an event lands while this fetch is in flight: the body is stale
        storeMod.useStore.getState().applyEvent(finished())
        return snap([ex('mid', { status: 'executing' })]) as never
      }
      return snap([ex('mid')]) as never
    })
    await storeMod.useStore.getState().refresh()
    expect(calls).toBe(4)
    // the stale "executing" row never came back over the finished event
    expect(storeMod.useStore.getState().executions[0].status).toBe('succeeded')
    // a second refresh past its budget does not double the pending re-arm
    calls = 0
    await storeMod.useStore.getState().refresh()
    expect(calls).toBe(4)
    await act(async () => { await vi.advanceTimersByTimeAsync(999) })
    expect(calls).toBe(4)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(calls).toBe(5)
    expect(storeMod.useStore.getState().executions.map((e) => [e.id, e.status])).toEqual([['mid', 'succeeded']])
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(calls).toBe(5)
  })
})

describe('Z start fresh: one chain at a time, and the §9 landing rule (§9.1)', () => {
  const finishModalAnim = (name: string) => {
    const dialog = screen.getByRole('alertdialog', { name })
    fireEvent.animationEnd(dialog.parentElement!)
  }
  const newButton = () => screen.getByText('New automation').closest('button') as HTMLButtonElement
  const resumeButton = () => screen.getByText('Resume draft').closest('button') as HTMLButtonElement

  const startChain = () => {
    let land: () => void = () => {}
    mockedApi.deleteDraft.mockImplementation(() => new Promise<void>((resolve) => { land = () => resolve() }))
    storeMod.useStore.setState({ pendingDraft: { name: 'Weekly report', updatedAt: null } })
    render(<AutomationsList />)
    fireEvent.click(newButton())
    fireEvent.click(screen.getByText('Discard and start new'))
    finishModalAnim('Start a new automation?')
    return { land: () => land() }
  }

  it('New automation and Resume draft disable while the chain runs; a second click starts nothing', async () => {
    const chain = startChain()
    await waitFor(() => expect(mockedApi.deleteDraft).toHaveBeenCalledTimes(1))
    expect(newButton().disabled).toBe(true)
    expect(resumeButton().disabled).toBe(true)
    fireEvent.click(newButton())
    fireEvent.click(resumeButton())
    expect(screen.queryByText('Discard and start new')).toBeNull()
    expect(storeMod.useStore.getState().surface).toBe('app')
    await act(async () => { chain.land() })
    await waitFor(() => expect(storeMod.useStore.getState().surface).toBe('create'))
    expect(mockedApi.deleteDraft).toHaveBeenCalledTimes(1)
    expect(mockedApi.putChat).toHaveBeenCalledTimes(1)
  })

  it('navigating away during the chain never opens the editor', async () => {
    const chain = startChain()
    await waitFor(() => expect(mockedApi.deleteDraft).toHaveBeenCalledTimes(1))
    act(() => { storeMod.useStore.getState().go('secrets') })
    await act(async () => { chain.land() })
    await waitFor(() => expect(mockedApi.putChat).toHaveBeenCalledWith('pending', []))
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
    expect(storeMod.useStore.getState().surface).toBe('app')
    expect(storeMod.useStore.getState().page).toBe('secrets')
  })
})

describe('AF a refused reveal toasts its reason (§9)', () => {
  const auto = {
    id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
    allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
    live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
    lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
    unresolvedReferences: {}, snapshotSettings: { preVersion: true, preClear: true, preRestore: true },
    specMeta: '', notifications: null,
    memory: { path: '/m', size: '12 KB', updated: 'Today' }, snapshots: [],
  } as unknown as Automation

  it('MemoryCard: ok false toasts the failure and never the success line', async () => {
    revealPath.mockResolvedValueOnce({ ok: false, error: 'the path no longer exists' })
    storeMod.useStore.setState({ automations: [auto] })
    render(<MemoryCard auto={auto} executing={false} />)
    fireEvent.click(screen.getByText('Show in Finder'))
    await waitFor(() => expect(toast()).toBe('Couldn’t open Finder — the path no longer exists'))
    expect(revealPath).toHaveBeenCalledWith('/m')
  })

  it('MemoryCard: ok true shows the success line', async () => {
    revealPath.mockResolvedValueOnce({ ok: true })
    storeMod.useStore.setState({ automations: [auto] })
    render(<MemoryCard auto={auto} executing={false} />)
    fireEvent.click(screen.getByText('Show in Finder'))
    await waitFor(() => expect(toast()).toBe('Shown in Finder — Autowright › Memory › Job'))
  })
})

describe('AG a failed iMessage Grant probe toasts (§9.2)', () => {
  it('the probe\'s error message reaches the toast and Grant re-enables', async () => {
    mockedApi.imessageAutomationProbe.mockImplementationOnce(() => Promise.reject(new Error('The backend isn\'t reachable.')))
    render(<TriggerEditor hasAppStart={false} onSave={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('iMessage'))
    fireEvent.click(await screen.findByRole('button', { name: 'Grant' }))
    await waitFor(() => expect(toast()).toBe('The backend isn\'t reachable.'))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Grant' }) as HTMLButtonElement).disabled).toBe(false))
  })
})
