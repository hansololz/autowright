// §7/§9/§10/§12 shell rules from the 2026-10-30 second sweep: Onboarding's
// manual sign-in line polls the sign-in rule like the Sign in help does; the
// Executions time presets resolve against the clock at every use and a 60 s
// tick ages rows out; a reconnect alone keeps the Executions page; Enter
// during an IME composition never submits the import URL, the marketplace
// path or the model pull; the MEMORY automatic-snapshot toggles hold a
// pending value while their PATCH is in flight; and a sign-in 409 saying the
// harness isn't installed returns the New agent card to idle. Components
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
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    createSnapshot: vi.fn(async () => ({})),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    ollamaPull: vi.fn(async () => ({})),
    installHarness: vi.fn(async () => ({})),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
    detectAgents: vi.fn(async () => []),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    checkAgent: vi.fn(async () => ({ status: 'ready' })),
    importFromUrl: vi.fn(() => new Promise(() => {})),
    marketplaceList: vi.fn(async () => ({ sources: [] })),
    marketplaceAdd: vi.fn(() => new Promise(() => {})),
    marketplaceImage: vi.fn(() => Promise.reject(new Error('no image'))),
  },
}))

vi.mock('../src/executionView', () => ({
  ExecutionView: () => <div data-testid="execution-view" />,
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let AgentNewPage: typeof import('../src/pages/AgentNewPage').default
let Onboarding: typeof import('../src/pages/Onboarding').default
let ExecutionsList: typeof import('../src/pages/ExecutionsList').default
let ImportModal: typeof import('../src/pages/ImportModal').default
let MarketplacePage: typeof import('../src/pages/MarketplacePage').default
let MemoryCard: typeof import('../src/pages/detail/MemoryCard').MemoryCard

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
  AgentNewPage = (await import('../src/pages/AgentNewPage')).default
  Onboarding = (await import('../src/pages/Onboarding')).default
  ExecutionsList = (await import('../src/pages/ExecutionsList')).default
  ImportModal = (await import('../src/pages/ImportModal')).default
  MarketplacePage = (await import('../src/pages/MarketplacePage')).default
  MemoryCard = (await import('../src/pages/detail/MemoryCard')).MemoryCard
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

const detectAll = (installed: Record<string, boolean>) =>
  mockedApi.detectAgents.mockResolvedValue(['claude', 'gemini', 'codex', 'opencode'].map((id) => (
    { id, name: id, installed: installed[id] ?? true, signedIn: installed[id] ?? true, detail: '' })))

const conflict = (message: string) => Object.assign(new Error(message), { status: 409 })

beforeEach(() => {
  for (const fn of Object.values(mockedApi)) fn.mockClear()
  mockedApi.detectAgents.mockImplementation(async () => [])
  mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: true }))
  mockedApi.loginHarness.mockImplementation(async () => ({ ok: true, method: 'terminal' }))
  mockedApi.checkHarness.mockImplementation(async () => ({ status: 'ready' }))
  mockedApi.ollamaStatus.mockImplementation(async () => ({ ready: false, installed: false, models: [] }))
  mockedApi.patchAutomation.mockImplementation(async () => ({}))
  mockedApi.listExecutions.mockImplementation(async () => ({ executions: [], total: 0 }))
  mockedApi.marketplaceList.mockImplementation(async () => ({ sources: [] }))
  storeMod.useStore.setState({
    surface: 'app', toast: null, executions: [], executionsTotal: 0, agents: [], secrets: [],
    automations: [], harnessInstall: {}, ollamaPull: null, agentEditId: null, reconnects: 0,
    platformOs: '', platformCapabilities: { ...MAC_CAPS }, settings: { ...SETTINGS },
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

describe('H1 Onboarding manual sign-in line polls the sign-in rule (§10)', () => {
  const toCards = async () => {
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    fireEvent.click(screen.getByText('Connect your AI →'))
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  }

  it('a found signed-out card (agentInstall false) runs the check once a terminal sign-in lands', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: true, signedIn: false, detail: 'signed out' },
    ])
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    storeMod.useStore.setState({
      connected: true, surface: 'onboard', platformCapabilities: { ...MAC_CAPS, agentInstall: false },
    })
    await toCards()
    expect(screen.getByTestId('manual-install-line').textContent).toMatch(/^Sign in to Claude Code from a terminal/)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.signinStatus).toHaveBeenCalledWith('claude')
    expect(mockedApi.checkHarness).not.toHaveBeenCalled()

    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: true }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.checkHarness).toHaveBeenCalledWith('Claude Code', null)
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.queryByTestId('manual-install-line')).toBeNull()
    // the line is gone, so the poll stops
    const calls = mockedApi.signinStatus.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(mockedApi.signinStatus.mock.calls.length).toBe(calls)
    expect(mockedApi.checkHarness).toHaveBeenCalledTimes(1)
  })

  it('the poll stops on unmount', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: true, signedIn: false, detail: 'signed out' },
    ])
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    storeMod.useStore.setState({
      connected: true, surface: 'onboard', platformCapabilities: { ...MAC_CAPS, agentInstall: false },
    })
    await toCards()
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.signinStatus).toHaveBeenCalled()
    cleanup()
    const calls = mockedApi.signinStatus.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(mockedApi.signinStatus.mock.calls.length).toBe(calls)
  })

  it('Linux: the post-install signed-out card runs the check once a terminal sign-in lands', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: false, signedIn: null, detail: 'not found' },
    ])
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    mockedApi.checkHarness.mockImplementation(async () => ({ status: 'needs' }))
    storeMod.useStore.setState({ connected: true, surface: 'onboard', platformOs: 'linux' })
    await toCards()
    fireEvent.click(screen.getByText('Set up Claude Code'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    act(() => {
      storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'claude', done: true, ok: true })
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByTestId('manual-install-line')).toBeTruthy()
    expect(mockedApi.checkHarness).toHaveBeenCalledTimes(1)

    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: true }))
    mockedApi.checkHarness.mockImplementation(async () => ({ status: 'ready' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.checkHarness).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.queryByTestId('manual-install-line')).toBeNull()
    expect(screen.queryByText('Not ready — it didn’t answer the readiness check.')).toBeNull()
  })
})

const openFilter = () => {
  fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
  return screen.getByRole('dialog', { name: 'Filter executions' })
}
const applyFilter = (dlg: HTMLElement) => {
  fireEvent.click(within(dlg).getByRole('button', { name: 'Apply' }))
  fireEvent.animationEnd(dlg.parentElement!)
}

describe('H2 Executions time presets are relative (§7)', () => {
  it('a "Last hour" row ages out of a page left open', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const row = ex('e-recent', { startedMs: NOW - 10 * 60_000, endedMs: NOW - 10 * 60_000 })
    mockedApi.listExecutions.mockImplementation(async () => ({ executions: [row], total: 1 }))
    storeMod.useStore.setState({ page: 'executions', executions: [row], executionsTotal: 1 })
    render(<ExecutionsList />)
    const dlg = openFilter()
    fireEvent.click(within(dlg).getByRole('radio', { name: 'Last hour' }))
    applyFilter(dlg)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('e-recent'.slice(0, 8))).toBeTruthy()
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith(
      expect.objectContaining({ startedFromMs: NOW - 3_600_000 }))

    await act(async () => { await vi.advanceTimersByTimeAsync(2 * 3_600_000) })
    expect(screen.queryByText('e-recent'.slice(0, 8))).toBeNull()
  })

  it('a reconnect refetch resolves the preset against the clock again', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    storeMod.useStore.setState({ page: 'executions' })
    render(<ExecutionsList />)
    const dlg = openFilter()
    fireEvent.click(within(dlg).getByRole('radio', { name: 'Last hour' }))
    applyFilter(dlg)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * 3_600_000) })
    act(() => { storeMod.useStore.setState({ reconnects: 1 }) })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith(
      expect.objectContaining({ startedFromMs: NOW + 2 * 3_600_000 }))
  })
})

describe('H3 a reconnect alone keeps the Executions page (§7)', () => {
  const rows = (n: number, status: Execution['status'] = 'succeeded') =>
    Array.from({ length: n }, (_, i) =>
      ex(`e-${String(i).padStart(4, '0')}`, { status, startedMs: NOW - i * 1000, endedMs: NOW - i * 1000 }))
  const pager = () => screen.getByTestId('executions-pager')
  const pagerButton = (label: string) =>
    within(pager()).getByText(label).closest('button')! as HTMLButtonElement
  type Query = { limit: number; before?: { startedMs: number; id: string } }
  const keyset = (all: Execution[]) => async (q: Query) => {
    const start = q.before ? all.findIndex((e) => e.id === q.before!.id) + 1 : 0
    return { executions: all.slice(start, start + q.limit), total: all.length }
  }

  beforeEach(() => { vi.spyOn(Date, 'now').mockReturnValue(NOW) })

  it('unfiltered: page 2 survives a reconnect with no fetch', async () => {
    const all = rows(120)
    mockedApi.listExecutions.mockImplementation(keyset(all))
    storeMod.useStore.setState({ page: 'executions', executions: all.slice(0, 50), executionsTotal: 120 })
    render(<ExecutionsList />)
    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(within(pager()).getByText('51–100 of 120')).toBeTruthy())
    const calls = mockedApi.listExecutions.mock.calls.length
    act(() => { storeMod.useStore.setState({ reconnects: 1 }) })
    await act(async () => {})
    expect(within(pager()).getByText('51–100 of 120')).toBeTruthy()
    expect(screen.getByText('e-0050')).toBeTruthy()
    expect(mockedApi.listExecutions.mock.calls.length).toBe(calls)
  })

  it('filtered: a reconnect refetches up to the current page and stays on it', async () => {
    const all = rows(120, 'failed')
    mockedApi.listExecutions.mockImplementation(keyset(all))
    storeMod.useStore.setState({ page: 'executions', executions: [], executionsTotal: 120 })
    render(<ExecutionsList />)
    const dlg = openFilter()
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Failed' }))
    applyFilter(dlg)
    await waitFor(() => expect(within(pager()).getByText('1–50 of 120')).toBeTruthy())
    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(within(pager()).getByText('51–100 of 120')).toBeTruthy())

    // a deleted row missed while the socket was down is gone from the refetch
    const afterDelete = all.filter((e) => e.id !== 'e-0060')
    mockedApi.listExecutions.mockImplementation(keyset(afterDelete))
    act(() => { storeMod.useStore.setState({ reconnects: 1 }) })
    await waitFor(() => expect(within(pager()).getByText('51–100 of 119')).toBeTruthy())
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith({ status: ['failed'], limit: 100 })
    expect(screen.queryByText('e-0060')).toBeNull()
    expect(screen.getByText('e-0050')).toBeTruthy()

    // a filter-dimension change still starts from page 1
    const dlg2 = openFilter()
    fireEvent.click(within(dlg2).getByRole('checkbox', { name: 'Cancelled' }))
    applyFilter(dlg2)
    await waitFor(() => expect(within(pager()).getByText('1–50 of 119')).toBeTruthy())
    expect(mockedApi.listExecutions).toHaveBeenLastCalledWith({ status: ['failed', 'cancelled'], limit: 50 })
  })
})

describe('H4 Enter during an IME composition never submits', () => {
  it('import URL field', async () => {
    render(<ImportModal onDone={() => {}} onClose={() => {}} />)
    const input = screen.getByPlaceholderText('https://github.com/… or a direct .autowright link')
    fireEvent.change(input, { target: { value: 'https://example.com/a.autowright' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.importFromUrl).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(mockedApi.importFromUrl).toHaveBeenCalledTimes(1))
  })

  it('Add marketplace path field', async () => {
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-add'))
    const field = await screen.findByTestId('marketplace-add-field')
    fireEvent.change(field, { target: { value: '/Users/x/shelf/marketplace-catalog.yaml' } })
    fireEvent.keyDown(field, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.marketplaceAdd).not.toHaveBeenCalled()
    fireEvent.keyDown(field, { key: 'Enter' })
    await waitFor(() => expect(mockedApi.marketplaceAdd).toHaveBeenCalledTimes(1))
  })

  it('model pull field', async () => {
    detectAll({})
    mockedApi.ollamaStatus.mockImplementation(async () => ({ ready: true, installed: true, models: [] }))
    storeMod.useStore.setState({ page: 'agentNew' })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    const input = await screen.findByPlaceholderText('e.g. qwen3-coder:30b')
    fireEvent.change(input, { target: { value: 'qwen3-coder:30b' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.ollamaPull).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(mockedApi.ollamaPull).toHaveBeenCalledWith('qwen3-coder:30b'))
  })
})

describe('H5 MEMORY automatic-snapshot toggles hold a pending value (§9.2)', () => {
  it('a double-click before the refresh lands sends off then on, never off twice', async () => {
    let land: () => void = () => {}
    mockedApi.patchAutomation.mockImplementationOnce(() => new Promise<void>((resolve) => { land = resolve }))
    const a = auto({ memory: { path: '/m', size: '1 KB', updated: 'Today' }, snapshots: [] } as Partial<Automation>)
    render(<MemoryCard auto={a} executing={false} />)
    const toggle = screen.getAllByTitle('Turn this automatic snapshot off')[0]
    fireEvent.click(toggle)
    expect(screen.getAllByTitle('Turn this automatic snapshot on').length).toBe(1)
    fireEvent.click(screen.getByTitle('Turn this automatic snapshot on'))
    await act(async () => {})
    expect(mockedApi.patchAutomation.mock.calls.map((c) => c[1])).toEqual([
      { snapshotSettings: { preVersion: false } },
      { snapshotSettings: { preVersion: true } },
    ])
    await act(async () => { land() })
  })
})

describe('H6 Onboarding sign-in 409 for a harness no longer installed (§10)', () => {
  it('returns the card to idle, re-runs detection and toasts', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: true, signedIn: false, detail: 'signed out' },
    ])
    mockedApi.loginHarness.mockImplementation(() => Promise.reject(conflict("Claude Code isn't installed on this Mac")))
    storeMod.useStore.setState({ connected: true, surface: 'onboard' })
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    fireEvent.click(screen.getByText('Connect your AI →'))
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    const detections = mockedApi.detectAgents.mock.calls.length
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: false, signedIn: null, detail: 'not found' },
    ])
    fireEvent.click(screen.getByText('Sign in'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(storeMod.useStore.getState().toast).toBe("Claude Code isn't installed on this Mac")
    expect(mockedApi.detectAgents.mock.calls.length).toBeGreaterThan(detections)
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    expect(screen.queryByTestId('manual-install-line')).toBeNull()
    expect(screen.queryByText('Sign in')).toBeNull()
    expect(screen.getByText('Set up Claude Code')).toBeTruthy()
  })
})

describe('H6 New agent sign-in 409 for a harness no longer installed (§12)', () => {
  it('returns to idle, re-runs detection and toasts, never the manual sign-in line', async () => {
    detectAll({ claude: false })
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    mockedApi.loginHarness.mockImplementation(() => Promise.reject(conflict("Claude Code isn't installed on this Mac")))
    storeMod.useStore.setState({ page: 'agentNew' })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))
    fireEvent.click(await screen.findByText('Download & set up'))
    await waitFor(() => expect(mockedApi.installHarness).toHaveBeenCalledWith('claude'))
    const detections = mockedApi.detectAgents.mock.calls.length
    act(() => {
      storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'claude', done: true, ok: true })
    })
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe("Claude Code isn't installed on this Mac"))
    await waitFor(() => expect(mockedApi.detectAgents.mock.calls.length).toBeGreaterThan(detections))
    expect(screen.queryByTestId('manual-install-line')).toBeNull()
    expect(await screen.findByText('Download & set up')).toBeTruthy()
  })
})
