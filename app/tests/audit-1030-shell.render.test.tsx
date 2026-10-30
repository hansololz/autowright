// §9 shell rules from the 2026-10-30 audit: the New agent page reads a 409
// from the sign-in help as "already signed in" only when it says so, and on
// Linux shows the manual sign-in line for the TUI providers (as Onboarding's
// suggestion card now does after a failed readiness check); the detail page's
// "Next execution in … (…)" label comes from the row's `nextTriggerShort`;
// Skip step is in-flight guarded; Enter during an IME composition never
// submits; the notifications `never` caption follows the §9 per-OS copy; the
// trigger editor's preview verdict is keyed by kind and text; a New secret
// lands in the store at once; the Settings tray row is decided synchronously
// from `platformOs`; and the marketplace add/settings modals ignore Escape
// while their request is in flight. Components render for real (happy-dom)
// against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Automation, Execution, MarketplaceSource, Settings, TriggerPreview } from '../src/types'

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
    skipStep: vi.fn(async () => ({})),
    cancelExecution: vi.fn(async () => ({})),
    retryExecution: vi.fn(async () => ({})),
    executeNow: vi.fn(async () => ({ executionId: 'e-new', queued: false })),
    patchAutomation: vi.fn(async () => ({})),
    duplicateAutomation: vi.fn(async () => ({})),
    deleteAutomation: vi.fn(async () => ({})),
    createSnapshot: vi.fn(async () => ({})),
    renameSnapshot: vi.fn(async () => ({})),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    installHarness: vi.fn(async () => ({})),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
    detectAgents: vi.fn(async () => []),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    checkAgent: vi.fn(async () => ({ status: 'ready' })),
    createSecret: vi.fn(async () => ({ id: 'sec-new-1', name: 'BOT_TOKEN', description: '', set: true, usedBy: [] })),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'granted' })),
    patchSettings: vi.fn(async () => ({})),
    marketplaceList: vi.fn(async () => ({ sources: [] })),
    marketplaceAdd: vi.fn(async () => ({})),
    marketplaceSettings: vi.fn(async () => ({})),
    marketplaceImage: vi.fn(() => Promise.reject(new Error('no image'))),
  },
}))

vi.mock('../src/executionView', () => ({
  ExecutionView: () => <div data-testid="execution-view" />,
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let platformCopy: typeof import('../src/platformCopyTable').platformCopy
let AgentNewPage: typeof import('../src/pages/AgentNewPage').default
let Onboarding: typeof import('../src/pages/Onboarding').default
let AutomationDetail: typeof import('../src/pages/AutomationDetail').default
let ExecutionPage: typeof import('../src/pages/ExecutionPage').default
let MemoryCard: typeof import('../src/pages/detail/MemoryCard').MemoryCard
let NotificationsCard: typeof import('../src/pages/detail/NotificationsCard').NotificationsCard
let TriggerEditor: typeof import('../src/pages/detail/TriggerEditor').TriggerEditor
let SecretModal: typeof import('../src/SecretModal').SecretModal
let SettingsPage: typeof import('../src/pages/SettingsPage').default
let MarketplacePage: typeof import('../src/pages/MarketplacePage').default

const platformInfo = vi.fn<() => Promise<{ platform: string; release: string; arch: string; version: string; trayPanel?: boolean }>>()

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath: () => Promise.resolve(),
    cliStatus: () => Promise.reject(new Error('no bridge')),
    platformInfo: () => platformInfo(),
  }
  const u = URL as unknown as Record<string, unknown>
  if (!u.createObjectURL) u.createObjectURL = () => 'blob:stub'
  if (!u.revokeObjectURL) u.revokeObjectURL = () => {}
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  platformCopy = (await import('../src/platformCopyTable')).platformCopy
  AgentNewPage = (await import('../src/pages/AgentNewPage')).default
  Onboarding = (await import('../src/pages/Onboarding')).default
  AutomationDetail = (await import('../src/pages/AutomationDetail')).default
  ExecutionPage = (await import('../src/pages/ExecutionPage')).default
  MemoryCard = (await import('../src/pages/detail/MemoryCard')).MemoryCard
  NotificationsCard = (await import('../src/pages/detail/NotificationsCard')).NotificationsCard
  TriggerEditor = (await import('../src/pages/detail/TriggerEditor')).TriggerEditor
  SecretModal = (await import('../src/SecretModal')).SecretModal
  SettingsPage = (await import('../src/pages/SettingsPage')).default
  MarketplacePage = (await import('../src/pages/MarketplacePage')).default
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

const source = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => ({
  id: 's1', kind: 'file', location: '/Users/x/shelf/marketplace-catalog.yaml',
  expanded: true, autoRefresh: false, builtin: false,
  name: 'Community', description: 'Automations I use.',
  addedAt: new Date().toISOString(), refreshedAt: new Date().toISOString(), error: null,
  cached: true, entries: [],
  ...over,
})

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
  mockedApi.triggersPreview.mockImplementation(async () => ({ triggers: [] }))
  mockedApi.skipStep.mockImplementation(async () => ({}))
  mockedApi.marketplaceList.mockImplementation(async () => ({ sources: [] }))
  mockedApi.marketplaceAdd.mockImplementation(async () => ({}))
  mockedApi.marketplaceSettings.mockImplementation(async () => ({}))
  platformInfo.mockImplementation(() => new Promise(() => {}))
  storeMod.useStore.setState({
    surface: 'app', toast: null, executions: [], executionsTotal: 0, agents: [], secrets: [],
    automations: [], harnessInstall: {}, ollamaPull: null, agentEditId: null,
    platformOs: '', platformCapabilities: { ...MAC_CAPS }, settings: { ...SETTINGS },
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('H1 New agent page sign-in 409s (§12/§9)', () => {
  const installClaude = async () => {
    detectAll({ claude: false })
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    storeMod.useStore.setState({ page: 'agentNew' })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))
    fireEvent.click(await screen.findByText('Download & set up'))
    await waitFor(() => expect(mockedApi.installHarness).toHaveBeenCalledWith('claude'))
    act(() => {
      storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'claude', done: true, ok: true })
    })
  }

  it('a 409 with another message toasts it and shows the manual sign-in line', async () => {
    mockedApi.loginHarness.mockImplementation(() => Promise.reject(conflict('Terminal didn’t respond')))
    await installClaude()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Terminal didn’t respond'))
    const line = await screen.findByTestId('manual-install-line')
    expect(line.textContent).toBe(
      'Sign in to Claude Code from a terminal, then come back — Autowright detects it automatically.')
    expect(storeMod.useStore.getState().toast).not.toMatch(/is set up/)
  })

  it('a 409 saying already signed in still counts as set up', async () => {
    mockedApi.loginHarness.mockImplementation(() => Promise.reject(conflict('Claude Code is already signed in')))
    await installClaude()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Claude Code is set up. Ready to save.'))
  })

  it('Linux: no automatic sign-in for a TUI provider — the manual sign-in line instead', async () => {
    storeMod.useStore.setState({ platformOs: 'linux' })
    await installClaude()
    const line = await screen.findByTestId('manual-install-line')
    expect(line.textContent).toMatch(/^Sign in to Claude Code from a terminal/)
    expect(mockedApi.loginHarness).not.toHaveBeenCalled()
  })
})

describe('H2 Onboarding suggestion card after a failed check on Linux (§9)', () => {
  it('an installed, signed-out TUI provider shows the manual sign-in line, not the install button', async () => {
    vi.useFakeTimers()
    mockedApi.detectAgents.mockResolvedValue([
      { id: 'claude', name: 'Claude Code', installed: false, signedIn: null, detail: 'not found' },
    ])
    mockedApi.signinStatus.mockImplementation(async () => ({ installed: true, signedIn: false }))
    mockedApi.checkHarness.mockImplementation(async () => ({ status: 'needs' }))
    storeMod.useStore.setState({ connected: true, surface: 'onboard', platformOs: 'linux' })
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    fireEvent.click(screen.getByText('Connect your AI →'))
    await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
    fireEvent.click(screen.getByText('Set up Claude Code'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(mockedApi.installHarness).toHaveBeenCalledWith('claude')
    act(() => {
      storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'claude', done: true, ok: true })
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('Not ready — it didn’t answer the readiness check.')).toBeTruthy()
    expect(screen.queryByText('Set up Claude Code')).toBeNull()
    const line = screen.getByTestId('manual-install-line')
    expect(line.textContent).toBe(
      'Sign in to Claude Code from a terminal, then come back — Autowright detects it automatically.')
    expect(mockedApi.loginHarness).not.toHaveBeenCalled()
  })
})

describe('H3 detail status line label (§4.3)', () => {
  beforeEach(() => { vi.spyOn(Date, 'now').mockReturnValue(NOW) })
  afterEach(() => { vi.restoreAllMocks() })
  const cron = { id: 't1', kind: 'cron', expression: '0 8 * * *', source: 'user', enabled: true, label: 'Daily 8:00', short: 'Daily 8:00' }

  it('reads the row’s nextTriggerShort, never a preview minimum', async () => {
    // the preview would name another trigger — it must not be consulted
    mockedApi.triggersPreview.mockImplementation(async () => ({
      triggers: [{ valid: true, label: 'x', short: 'From preview', nextAtMs: NOW + 1 }],
    }))
    storeMod.useStore.setState({
      page: 'automation', automationId: 'a1',
      automations: [auto({
        triggers: [cron] as never, triggerChip: 'Daily 8:00', nextAtMs: NOW + 3_600_000,
        nextTriggerShort: 'Every 6h',
      })],
    })
    render(<AutomationDetail />)
    expect(await screen.findByText(/Next execution in .* \(Every 6h\) · executes even when the app is closed\./)).toBeTruthy()
    await act(async () => { await new Promise((r) => setTimeout(r, 350)) })
    expect(screen.queryByText(/From preview/)).toBeNull()
  })

  it('an older backend without the field shows no label', async () => {
    storeMod.useStore.setState({
      page: 'automation', automationId: 'a1',
      automations: [auto({ triggers: [cron] as never, triggerChip: 'Daily 8:00', nextAtMs: NOW + 3_600_000 })],
    })
    render(<AutomationDetail />)
    const line = await screen.findByText(/Next execution in .* · executes even when the app is closed\./)
    expect(line.textContent).not.toMatch(/\(/)
  })
})

describe('H4 Skip step in-flight guard (§7)', () => {
  it('a double-click skips once, and the button disables until the POST settles', async () => {
    let land: (v: unknown) => void = () => {}
    mockedApi.skipStep.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    const row = ex('e1', { status: 'executing' })
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [row], execLogs: {},
      automations: [auto({ params: [] } as Partial<Automation>)],
      executionFull: { e1: { ...row, steps: [
        { name: 'Fetch', status: 'executing', duration: '', attempts: [] },
        { name: 'Send', status: 'queued', duration: '', attempts: [] },
      ], result: null } } as never,
    })
    render(<ExecutionPage />)
    const skip = screen.getByRole('button', { name: 'Skip step' }) as HTMLButtonElement
    fireEvent.click(skip)
    fireEvent.click(skip)
    expect(mockedApi.skipStep).toHaveBeenCalledTimes(1)
    expect(mockedApi.skipStep).toHaveBeenCalledWith('e1', 0)
    expect((screen.getByRole('button', { name: 'Skip step' }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { land({}) })
    expect((screen.getByRole('button', { name: 'Skip step' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('H5 Enter during an IME composition never submits', () => {
  it('Duplicate… name field', async () => {
    storeMod.useStore.setState({ page: 'automation', automationId: 'a1', automations: [auto()] })
    render(<AutomationDetail />)
    fireEvent.click(screen.getByLabelText('Automation actions'))
    fireEvent.click(screen.getByText('Duplicate…'))
    const dialog = screen.getByRole('dialog', { name: 'Duplicate Job' })
    const input = within(dialog).getByLabelText('Name')
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.duplicateAutomation).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(mockedApi.duplicateAutomation).toHaveBeenCalledTimes(1))
  })

  it('memory snapshot name field', async () => {
    render(<MemoryCard auto={auto({ memory: { path: '/m', size: '1 KB', updated: 'Today' }, snapshots: [] } as Partial<Automation>)} executing={false} />)
    fireEvent.click(screen.getByText('Snapshot'))
    const input = screen.getByPlaceholderText('Name — optional')
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.createSnapshot).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText('Name — optional')).toBeTruthy()
  })

  it('secret name field', async () => {
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    const nameInput = screen.getByPlaceholderText(/A short name, like MAIL_PASSWORD/)
    fireEvent.change(nameInput, { target: { value: 'MAIL_PASSWORD' } })
    fireEvent.keyDown(nameInput, { key: 'Enter', isComposing: true })
    await act(async () => {})
    expect(mockedApi.createSecret).not.toHaveBeenCalled()
  })
})

describe('H6 notifications never caption per OS (§9)', () => {
  it('the copy table: menu bar, tray, and Linux drops the clause', () => {
    expect(platformCopy('macos').failuresStillShow).toBe('Failures still show on the Automations list and the menu bar.')
    expect(platformCopy('windows').failuresStillShow).toBe('Failures still show on the Automations list and the tray.')
    expect(platformCopy('linux').failuresStillShow).toBe('Failures still show on the Automations list.')
  })

  it('the NOTIFICATIONS card caption follows the OS', () => {
    storeMod.useStore.setState({ platformOs: 'windows' })
    const { unmount } = render(<NotificationsCard auto={auto({ notifications: 'never' })} showToast={() => {}} />)
    expect(screen.getByText('Failures still show on the Automations list and the tray.')).toBeTruthy()
    unmount()
    act(() => { storeMod.useStore.setState({ platformOs: 'linux' }) })
    render(<NotificationsCard auto={auto({ notifications: 'never' })} showToast={() => {}} />)
    expect(screen.getByText('Failures still show on the Automations list.')).toBeTruthy()
  })

  it('the Settings caption follows the OS', () => {
    storeMod.useStore.setState({ platformOs: 'windows' })
    render(<SettingsPage />)
    expect(screen.getByText('Failures still show on the Automations list and the tray.')).toBeTruthy()
    expect(screen.queryByText(/and the menu bar\./)).toBeNull()
  })
})

describe('H7 trigger editor verdict keyed by kind and text (§9.2)', () => {
  it('a quick kind round trip never reuses the old text’s verdict', async () => {
    mockedApi.triggersPreview.mockImplementation(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map((t): TriggerPreview => (
        t.expression === '0 8 * * *'
          ? { valid: true, label: 'Daily 8:00', short: 'Daily 8:00', nextAtMs: 1, nextLabel: 'tomorrow' }
          : t.kind === 'interval'
          ? { valid: true, label: 'Every hour', short: 'Every 1h', nextAtMs: 1 }
          : { valid: false, error: 'bad cron', label: '', short: '', nextAtMs: null })),
    }))
    render(<TriggerEditor hasAppStart={false} onSave={() => {}} onCancel={() => {}} />)
    const add = () => screen.getByRole('button', { name: 'Add' }) as HTMLButtonElement
    fireEvent.change(screen.getByPlaceholderText(/^0 8 \* \* \*/), { target: { value: '0 8 * * *' } })
    await waitFor(() => expect(add().disabled).toBe(false))
    fireEvent.change(screen.getByPlaceholderText(/^0 8 \* \* \*/), { target: { value: '0 8 * * * *' } })
    expect(add().disabled).toBe(true)
    // within the 300 ms debounce: Interval, then back to Cron
    fireEvent.click(screen.getByText('Interval'))
    fireEvent.click(screen.getByText('Cron'))
    expect(add().disabled).toBe(true)
    await waitFor(() => expect(mockedApi.triggersPreview).toHaveBeenCalledWith(
      [expect.objectContaining({ kind: 'cron', expression: '0 8 * * * *' })]))
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(add().disabled).toBe(true)
  })
})

describe('H8 New secret lands in the store at once (§9.2)', () => {
  it('the picker names the new secret, never the deleted-secret short id', async () => {
    render(<TriggerEditor hasAppStart={false} onSave={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('Discord'))
    fireEvent.click(screen.getByRole('button', { name: /New secret/ }))
    fireEvent.change(screen.getByPlaceholderText(/A short name, like MAIL_PASSWORD/), { target: { value: 'BOT_TOKEN' } })
    const save = screen.getAllByRole('button').find((b) => /^Save/.test(b.textContent ?? ''))!
    fireEvent.click(save)
    await waitFor(() => expect(mockedApi.createSecret).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(storeMod.useStore.getState().secrets.map((s) => s.id)).toEqual(['sec-new-1']))
    const pill = screen.getByTitle('The secret holding your Discord bot token')
    expect(pill.textContent).toContain('BOT_TOKEN')
    expect(pill.textContent).not.toContain('sec-new-…')
  })
})

describe('H9 Settings tray row decided from platformOs (§4.9)', () => {
  it('Linux: the row never renders, even before the bridge answers', () => {
    storeMod.useStore.setState({ platformOs: 'linux' })
    render(<SettingsPage />)
    expect(screen.queryByText(/^Show in the /)).toBeNull()
  })

  it('macOS: the row renders at once', () => {
    render(<SettingsPage />)
    expect(screen.getByText('Show in the menu bar')).toBeTruthy()
  })
})

describe('H10 marketplace modals ignore Escape while busy (§9)', () => {
  it('Add marketplace: Escape during the add keeps the modal and its toast', async () => {
    let land: (s: MarketplaceSource) => void = () => {}
    mockedApi.marketplaceAdd.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-add'))
    const field = await screen.findByTestId('marketplace-add-field')
    fireEvent.change(field, { target: { value: '/Users/x/shelf/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('marketplace-add-submit'))
    await waitFor(() => expect(mockedApi.marketplaceAdd).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(document, { key: 'Escape' })
    await act(async () => { await new Promise((r) => setTimeout(r, 250)) })
    expect(screen.getByTestId('marketplace-add-field')).toBeTruthy()
    await act(async () => { land(source()) })
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Added Community.'), { timeout: 3000 })
  })

  it('Catalog settings: Escape during the save keeps the modal', async () => {
    let land: (v: unknown) => void = () => {}
    mockedApi.marketplaceList.mockImplementation(async () => ({ sources: [source()] }))
    mockedApi.marketplaceSettings.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    render(<MarketplacePage />)
    fireEvent.click((await screen.findAllByTestId('marketplace-actions'))[0])
    fireEvent.click(screen.getByRole('button', { name: 'Catalog settings…' }))
    await screen.findByTestId('catalog-settings')
    fireEvent.click(screen.getByTestId('settings-save'))
    await waitFor(() => expect(mockedApi.marketplaceSettings).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(document, { key: 'Escape' })
    await act(async () => { await new Promise((r) => setTimeout(r, 250)) })
    expect(screen.getByTestId('catalog-settings')).toBeTruthy()
    const listed = mockedApi.marketplaceList.mock.calls.length
    await act(async () => { land(source()) })
    // the save's onSaved runs once the card closes: the page reloads its list
    await waitFor(() => expect(mockedApi.marketplaceList.mock.calls.length).toBeGreaterThan(listed), { timeout: 3000 })
  })
})
