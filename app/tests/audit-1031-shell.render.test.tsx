// §7/§9.2/§10/§19 shell rules from the 2026-10-31 sweep: a reconnect on a
// deeper unfiltered Executions page refetches every row up to it; the 60 s
// preset tick refetches the filtered rows so the total and the pager age out
// with them; the MEMORY card is keyed by automation so a pending toggle never
// carries across a detail → detail navigation; and an Onboarding sign-in poll
// answer landing after unmount never starts a connection check. Components
// render for real (happy-dom) against the real store with the api module
// mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Automation, Execution, Settings } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    createSnapshot: vi.fn(async () => ({})),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    detectAgents: vi.fn(async () => []),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
  },
}))

vi.mock('../src/executionView', () => ({
  ExecutionView: () => <div data-testid="execution-view" />,
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let Onboarding: typeof import('../src/pages/Onboarding').default
let ExecutionsList: typeof import('../src/pages/ExecutionsList').default
let AutomationDetail: typeof import('../src/pages/AutomationDetail').default

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath: () => Promise.resolve(),
    cliStatus: () => Promise.reject(new Error('no bridge')),
    platformInfo: () => new Promise(() => {}),
  }
  const u = URL as unknown as Record<string, unknown>
  if (!u.createObjectURL) u.createObjectURL = () => 'blob:stub'
  if (!u.revokeObjectURL) u.revokeObjectURL = () => {}
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  Onboarding = (await import('../src/pages/Onboarding')).default
  ExecutionsList = (await import('../src/pages/ExecutionsList')).default
  AutomationDetail = (await import('../src/pages/AutomationDetail')).default
})

const NOW = 1_700_000_000_000
const MAC_CAPS = { imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true }

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '', notifications: null,
  ...over,
})

const ex = (id: string, over: Partial<Execution> = {}): Execution => ({
  id, automationId: 'a1', automationName: 'Job', automationDeleted: false, versionLabel: 'v1',
  status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false, duration: '1.0s',
  started: 'Today, 8:00 AM', startedMs: NOW, endedMs: NOW, queuedMs: 0, durationMs: null, passStartedMs: 0,
  note: null, error: null, ...over,
})

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'never', days: 30, keepForever: false, developerMode: false,
  cliEnabled: false, dataPath: '/tmp', dataSize: '0 B',
}

beforeEach(() => {
  for (const fn of Object.values(mockedApi)) fn.mockClear()
  mockedApi.detectAgents.mockImplementation(async () => [])
  mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: true }))
  mockedApi.checkHarness.mockImplementation(async () => ({ status: 'ready' }))
  mockedApi.ollamaStatus.mockImplementation(async () => ({ ready: false, installed: false, models: [] }))
  mockedApi.patchAutomation.mockImplementation(async () => ({}))
  mockedApi.listExecutions.mockImplementation(async () => ({ executions: [], total: 0 }))
  storeMod.useStore.setState({
    surface: 'app', toast: null, executions: [], executionsTotal: 0, agents: [], secrets: [],
    automations: [], harnessInstall: {}, ollamaPull: null, agentEditId: null, reconnects: 0,
    platformOs: '', platformCapabilities: { ...MAC_CAPS }, settings: { ...SETTINGS },
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const openFilter = () => {
  fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
  return screen.getByRole('dialog', { name: 'Filter executions' })
}
const applyFilter = (dlg: HTMLElement) => {
  fireEvent.click(within(dlg).getByRole('button', { name: 'Apply' }))
  fireEvent.animationEnd(dlg.parentElement!)
}

describe('H1 a reconnect on a deeper unfiltered page refetches up to it (§19)', () => {
  const rows = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      ex(`e-${String(i).padStart(4, '0')}`, { startedMs: NOW - i * 1000, endedMs: NOW - i * 1000 }))
  const pager = () => screen.getByTestId('executions-pager')
  const pagerButton = (label: string) =>
    within(pager()).getByText(label).closest('button')! as HTMLButtonElement
  type Query = { limit: number; before?: { startedMs: number; id: string } }
  const keyset = (all: Execution[]) => async (q: Query) => {
    const start = q.before ? all.findIndex((e) => e.id === q.before!.id) + 1 : 0
    return { executions: all.slice(start, start + q.limit), total: all.length }
  }

  beforeEach(() => { vi.spyOn(Date, 'now').mockReturnValue(NOW) })

  it('page 2 refetches with limit 100 and drops a row the server no longer returns', async () => {
    const all = rows(120)
    mockedApi.listExecutions.mockImplementation(keyset(all))
    storeMod.useStore.setState({ page: 'executions', executions: all.slice(0, 50), executionsTotal: 120 })
    render(<ExecutionsList />)
    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(within(pager()).getByText('51–100 of 120')).toBeTruthy())
    expect(screen.getByText('e-0060')).toBeTruthy()

    // an execution.deleted missed while the socket was down
    const afterDelete = all.filter((e) => e.id !== 'e-0060')
    mockedApi.listExecutions.mockImplementation(keyset(afterDelete))
    act(() => { storeMod.useStore.setState({ executionsTotal: 119, reconnects: 1 }) })
    await waitFor(() => expect(screen.queryByText('e-0060')).toBeNull())
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith({ status: 'finished', limit: 100 })
    expect(within(pager()).getByText('51–100 of 119')).toBeTruthy()
    expect(screen.getByText('e-0050')).toBeTruthy()
  })
})

describe('H5 the preset tick refetches the filtered rows (§7)', () => {
  it('a 60 s tick under "Last hour" refetches with the preset resolved again', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    storeMod.useStore.setState({ page: 'executions' })
    render(<ExecutionsList />)
    const dlg = openFilter()
    fireEvent.click(within(dlg).getByRole('radio', { name: 'Last hour' }))
    applyFilter(dlg)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const calls = mockedApi.listExecutions.mock.calls.length
    expect(calls).toBeGreaterThan(0)
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(mockedApi.listExecutions.mock.calls.length).toBe(calls + 1)
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith(
      expect.objectContaining({ startedFromMs: NOW + 60_000 - 3_600_000, limit: 50 }))
  })
})

describe('H3 the MEMORY card is keyed by automation (§9.2)', () => {
  it('a pending toggle on one automation never shows on the next', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    mockedApi.patchAutomation.mockImplementation(() => new Promise(() => {}))
    const memory = { memory: { path: '/m', size: '1 KB', updated: 'Today' }, snapshots: [] } as Partial<Automation>
    storeMod.useStore.setState({
      page: 'automation', automationId: 'a1', toast: null, executions: [],
      automations: [auto(memory), auto({ id: 'a2', name: 'Other', ...memory })],
    })
    render(<AutomationDetail />)
    fireEvent.click(screen.getAllByTitle('Turn this automatic snapshot off')[0])
    expect(screen.getAllByTitle('Turn this automatic snapshot on').length).toBe(1)
    await act(async () => { storeMod.useStore.setState({ automationId: 'a2' }) })
    expect(screen.getByText('Other')).toBeTruthy()
    // a2's own values: every automatic snapshot on, nothing pending
    expect(screen.queryByTitle('Turn this automatic snapshot on')).toBeNull()
    expect(screen.getAllByTitle('Turn this automatic snapshot off').length).toBe(3)
  })
})

describe('H6 an Onboarding sign-in answer after unmount starts no check (§10)', () => {
  it('the in-flight signinStatus lands signed in after unmount: no checkHarness', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: true, signedIn: false, detail: 'signed out' },
    ])
    let land: (v: { installed: boolean; signedIn: boolean }) => void = () => {}
    mockedApi.signinStatus.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    storeMod.useStore.setState({
      connected: true, surface: 'onboard', platformCapabilities: { ...MAC_CAPS, agentInstall: false },
    })
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    fireEvent.click(screen.getByText('Connect your AI →'))
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    expect(screen.getByTestId('manual-install-line')).toBeTruthy()
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.signinStatus).toHaveBeenCalledWith('claude')
    cleanup()
    await act(async () => { land({ installed: true, signedIn: true }) })
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.checkHarness).not.toHaveBeenCalled()
  })
})
