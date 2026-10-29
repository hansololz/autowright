// §9 shell rules from the 2026-10-28 audit: the detail page absorbs its own
// window rows so a run trimmed out of the §7 window never lets an older
// failure resurface; the automation and agent Delete confirms navigate only
// while the page that started them is shown; Duplicate and Open automation
// stay put when the insert GET failed; the §13 menu-bar run button and the
// §12 secret save never fire twice; a rejected Retry keeps the view; a §19
// reconnect refetches the filtered executions page; and an `unknown` probe
// answer keeps the remembered iMessage automation state. Components render for
// real (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import type { Automation, Execution, ImportSummary } from '../src/types'

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
    executeNow: vi.fn(async () => ({ executionId: 'e-new', queued: false })),
    retryExecution: vi.fn(async () => ({ executionId: 'e1' })),
    deleteAutomation: vi.fn(async () => ({})),
    duplicateAutomation: vi.fn(async () => ({})),
    deleteAgent: vi.fn(async () => ({})),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
    detectAgents: vi.fn(async () => ['claude', 'gemini', 'codex', 'opencode'].map((id) => (
      { id, name: id, installed: true, signedIn: true, detail: '' }))),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    checkAgent: vi.fn(async () => ({ status: 'ready' })),
    createSecret: vi.fn(async () => ({ id: 's2', name: 'MAIL_PASSWORD', description: '', set: true, usedBy: [] })),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'unknown' })),
    imessageAutomationProbe: vi.fn(async () => ({ automation: 'unknown' })),
  },
}))

// §7 Retry: counts ExecutionView instances — the page remounts it (its
// retryKey) only once a retry was accepted. A ref identifies the instance, so
// StrictMode's effect re-run on the same instance never counts twice.
const viewMounts = vi.hoisted(() => ({ instances: new Set<object>(), get count() { return this.instances.size } }))
vi.mock('../src/executionView', () => ({
  ExecutionView: () => {
    const self = useRef({})
    useEffect(() => { viewMounts.instances.add(self.current) }, [])
    return <div data-testid="execution-view" />
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let AutomationDetail: typeof import('../src/pages/AutomationDetail').default
let ImportSummaryModal: typeof import('../src/pages/AutomationsList').ImportSummaryModal
let AgentNewPage: typeof import('../src/pages/AgentNewPage').default
let MenuBarPanel: typeof import('../src/pages/MenuBarPanel').default
let SecretModal: typeof import('../src/SecretModal').SecretModal
let ExecutionPage: typeof import('../src/pages/ExecutionPage').default
let ExecutionsList: typeof import('../src/pages/ExecutionsList').default
let TriggerEditor: typeof import('../src/pages/detail/TriggerEditor').TriggerEditor

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    resizePanel: () => Promise.resolve(),
    openApp: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  AutomationDetail = (await import('../src/pages/AutomationDetail')).default
  ImportSummaryModal = (await import('../src/pages/AutomationsList')).ImportSummaryModal
  AgentNewPage = (await import('../src/pages/AgentNewPage')).default
  MenuBarPanel = (await import('../src/pages/MenuBarPanel')).default
  SecretModal = (await import('../src/SecretModal')).SecretModal
  ExecutionPage = (await import('../src/pages/ExecutionPage')).default
  ExecutionsList = (await import('../src/pages/ExecutionsList')).default
  TriggerEditor = (await import('../src/pages/detail/TriggerEditor')).TriggerEditor
})

const NOW = 1_700_000_000_000

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

const summary: ImportSummary = {
  secretsMatched: [], agentsMatched: [], unresolved: [],
  packages: [], renamedFrom: null, os: 'macos', osMismatch: false,
}

// ConfirmModal acts on onClose, which fires only after the overlay's exit
// animation. happy-dom runs no animations, so end it by hand.
const finishModalAnim = (name: string) => {
  const dlg = screen.getByRole('alertdialog', { name })
  fireEvent.animationEnd(dlg.parentElement!)
}

beforeEach(() => {
  for (const fn of Object.values(mockedApi)) fn.mockClear()
  mockedApi.getAutomation.mockImplementation(() => Promise.reject(new Error('offline')))
  mockedApi.listExecutions.mockImplementation(async () => ({ executions: [], total: 0 }))
  mockedApi.imessagePermissions.mockImplementation(async () => ({ fullDisk: true, automation: 'unknown' }))
  storeMod.useStore.setState({ surface: 'app', toast: null, executions: [], executionsTotal: 0, reconnects: 0 })
  viewMounts.instances.clear()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('detail page absorbs its window rows (§9.2)', () => {
  it('a run trimmed out of the window stays latest — the older fetched failure never resurfaces', async () => {
    const failed = ex('f1', { status: 'failed', startedMs: NOW - 10_000, error: { step: 'Fetch', message: 'boom', reason: null } })
    mockedApi.listExecutions.mockImplementation(async () => ({ executions: [failed], total: 1 }))
    storeMod.useStore.setState({
      page: 'automation', automationId: 'a1', automations: [auto({ lastStatus: 'failed' })], executions: [],
    })
    render(<AutomationDetail />)
    expect(await screen.findByText('Failed at step “Fetch”')).toBeTruthy()
    // a newer success arrives by event, then 50 other automations' finishes
    // push it out of the §7 window
    act(() => {
      storeMod.useStore.getState().applyEvent({
        event: 'execution.finished', executionId: 's2', automationId: 'a1',
        execution: ex('s2', { startedMs: NOW }), automation: auto(),
      })
    })
    await waitFor(() => expect(screen.queryByText('Failed at step “Fetch”')).toBeNull())
    act(() => {
      for (let i = 0; i < 50; i++) {
        const id = `o${i}`
        storeMod.useStore.getState().applyEvent({
          event: 'execution.finished', executionId: id, automationId: 'a2',
          execution: ex(id, { automationId: 'a2', startedMs: NOW + 1_000 + i }), automation: null,
        })
      }
    })
    expect(storeMod.useStore.getState().executions.some((e) => e.id === 's2')).toBe(false)
    expect(screen.queryByText('Failed at step “Fetch”')).toBeNull()
  })
})

describe('Delete confirms land only on the page that started them (§9)', () => {
  it('automation delete: moving on while the DELETE is in flight never navigates', async () => {
    let land: (v: unknown) => void = () => {}
    mockedApi.deleteAutomation.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    storeMod.useStore.setState({ page: 'automation', automationId: 'a1', automations: [auto()] })
    render(<AutomationDetail />)
    fireEvent.click(screen.getByLabelText('Automation actions'))
    fireEvent.click(screen.getByText('Delete automation…'))
    fireEvent.click(screen.getByText('Delete automation'))
    finishModalAnim('Delete this automation?')
    await waitFor(() => expect(mockedApi.deleteAutomation).toHaveBeenCalledWith('a1'))
    act(() => { storeMod.useStore.setState({ page: 'executions' }) })
    await act(async () => { land({}) })
    expect(storeMod.useStore.getState().page).toBe('executions')
  })

  it('agent delete: moving on while the DELETE is in flight never navigates', async () => {
    let land: (v: unknown) => void = () => {}
    mockedApi.deleteAgent.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    storeMod.useStore.setState({
      page: 'agentNew',
      agents: [{ id: 'g1', name: 'Writer', harness: 'Claude Code', mode: 'default', model: null }] as never,
      agentEditId: 'g1', agentChecks: {},
    })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByLabelText('Agent actions'))
    fireEvent.click(screen.getByText('Remove agent…'))
    fireEvent.click(screen.getByText('Remove agent'))
    finishModalAnim('Remove this agent?')
    await waitFor(() => expect(mockedApi.deleteAgent).toHaveBeenCalledWith('g1'))
    act(() => { storeMod.useStore.setState({ page: 'executions', agentEditId: null }) })
    await act(async () => { land({}) })
    expect(storeMod.useStore.getState().page).toBe('executions')
    storeMod.useStore.setState({ agentEditId: null })
  })
})

describe('a navigation onto a record the client failed to fetch stays put (§9)', () => {
  it('Duplicate: the copy landed but its GET failed — toast, no navigation', async () => {
    mockedApi.duplicateAutomation.mockResolvedValueOnce(auto({ id: 'a2', name: 'Job 2' }))
    storeMod.useStore.setState({ page: 'automation', automationId: 'a1', automations: [auto()] })
    render(<AutomationDetail />)
    fireEvent.click(screen.getByLabelText('Automation actions'))
    fireEvent.click(screen.getByText('Duplicate…'))
    const dialog = screen.getByRole('dialog', { name: 'Duplicate Job' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }))
    await waitFor(() => expect(storeMod.useStore.getState().toast).toMatch(/Duplicated as “Job 2”\./))
    expect(storeMod.useStore.getState().page).toBe('automation')
    expect(storeMod.useStore.getState().automationId).toBe('a1')
  })

  it('Open automation: an insert GET that failed never navigates', async () => {
    storeMod.useStore.setState({ page: 'automations', automationId: null, automations: [auto()] })
    render(<ImportSummaryModal name="Imported job" automationId="a7" summary={summary} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open automation' }))
    await waitFor(() => expect(mockedApi.getAutomation).toHaveBeenCalledWith('a7'))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Open automation' }) as HTMLButtonElement).disabled).toBe(false))
    expect(storeMod.useStore.getState().page).toBe('automations')
    expect(storeMod.useStore.getState().automationId).toBeNull()
  })
})

describe('menu-bar run button busy rule (§13)', () => {
  it('a double-click starts one run, and the button disables until the request lands', async () => {
    let land: (v: unknown) => void = () => {}
    mockedApi.executeNow.mockImplementationOnce(() => new Promise((resolve) => { land = resolve }))
    storeMod.useStore.setState({ automations: [auto()] })
    render(<MenuBarPanel />)
    const run = screen.getByRole('button', { name: 'Execute now' })
    fireEvent.click(run)
    fireEvent.click(run)
    expect(mockedApi.executeNow).toHaveBeenCalledTimes(1)
    expect(mockedApi.executeNow).toHaveBeenCalledWith('a1', undefined, 'menubar')
    const busy = screen.getByRole('button', { name: 'Starting…' }) as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    await act(async () => { land({ executionId: 'e-new', queued: false }) })
    expect((screen.getByRole('button', { name: 'Execute now' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('secret save fires once (§12)', () => {
  it('Enter and a click in the same tick post one secret', async () => {
    storeMod.useStore.setState({ secrets: [] })
    render(<SecretModal modal={{ mode: 'add' }} onClose={() => {}} />)
    const nameInput = screen.getByPlaceholderText(/A short name, like MAIL_PASSWORD/) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'MAIL_PASSWORD' } })
    const save = screen.getAllByRole('button').find((b) => /^Save/.test(b.textContent ?? ''))!
    act(() => {
      fireEvent.keyDown(nameInput, { key: 'Enter' })
      fireEvent.click(save)
    })
    await waitFor(() => expect(mockedApi.createSecret).toHaveBeenCalledTimes(1))
    await act(async () => {})
    expect(mockedApi.createSecret).toHaveBeenCalledTimes(1)
  })
})

describe('execution page Retry (§7)', () => {
  const seedFailed = () => {
    const row = ex('e1', { status: 'failed' })
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [row], execLogs: {},
      automations: [auto({ params: [] } as Partial<Automation>)],
      executionFull: { e1: { ...row, steps: [], result: null } },
    })
  }

  it('a rejected retry keeps the view mounted (the retry key never bumps)', async () => {
    mockedApi.retryExecution.mockRejectedValueOnce(new Error('Retry failed.'))
    seedFailed()
    render(<ExecutionPage />)
    expect(viewMounts.count).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Retry failed.'))
    expect(viewMounts.count).toBe(1)
  })

  it('an accepted retry remounts the view for the new attempt', async () => {
    seedFailed()
    render(<ExecutionPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(viewMounts.count).toBe(2))
  })
})

describe('executions list refetches on reconnect (§19)', () => {
  it('a reconnect refetches the current filtered page with the same query', async () => {
    storeMod.useStore.setState({ page: 'executions', automations: [] })
    render(<ExecutionsList />)
    fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    const dlg = screen.getByRole('dialog', { name: 'Filter executions' })
    fireEvent.click(within(dlg).getByRole('checkbox', { name: 'Failed' }))
    fireEvent.click(within(dlg).getByRole('button', { name: 'Apply' }))
    fireEvent.animationEnd(dlg.parentElement!)
    await waitFor(() => expect(mockedApi.listExecutions).toHaveBeenCalledTimes(1))
    const first = mockedApi.listExecutions.mock.calls[0][0]
    expect(first.status).toEqual(['failed'])
    act(() => { storeMod.useStore.setState({ reconnects: 1 }) })
    await waitFor(() => expect(mockedApi.listExecutions).toHaveBeenCalledTimes(2))
    expect(mockedApi.listExecutions.mock.calls[1][0]).toEqual(first)
  })
})

describe('iMessage automation probe answering unknown (§19)', () => {
  it('stops the spinner, keeps the remembered state, and raises no toast', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    storeMod.useStore.setState({
      secrets: [],
      platformCapabilities: { imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true },
    })
    let answer: (v: unknown) => void = () => {}
    mockedApi.imessageAutomationProbe.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    render(<TriggerEditor hasAppStart={false} onSave={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('iMessage'))
    const grant = await screen.findByRole('button', { name: 'Grant' })
    fireEvent.click(grant)
    expect((screen.getByRole('button', { name: 'Grant' }) as HTMLButtonElement).disabled).toBe(true)
    // the 3 s poll lands the user's answer while the probe is still waiting
    mockedApi.imessagePermissions.mockResolvedValue({ fullDisk: true, automation: 'granted' })
    await act(async () => { vi.advanceTimersByTime(3000) })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Grant' })).toBeNull())
    // the probe's own wait ran out: unknown is not a denial and not news
    await act(async () => { answer({ automation: 'unknown' }) })
    expect(screen.queryByText(/Not asked yet/)).toBeNull()
    expect(screen.queryByText(/Denied/)).toBeNull()
    expect(storeMod.useStore.getState().toast).toBeNull()
  })

  it('an unknown answer with nothing remembered re-enables Grant', async () => {
    storeMod.useStore.setState({
      secrets: [],
      platformCapabilities: { imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true },
    })
    render(<TriggerEditor hasAppStart={false} onSave={() => {}} onCancel={() => {}} />)
    fireEvent.click(screen.getByText('iMessage'))
    fireEvent.click(await screen.findByRole('button', { name: 'Grant' }))
    await waitFor(() => expect(mockedApi.imessageAutomationProbe).toHaveBeenCalledTimes(1))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Grant' }) as HTMLButtonElement).disabled).toBe(false))
    expect(screen.getByText(/Not asked yet/)).toBeTruthy()
    expect(storeMod.useStore.getState().toast).toBeNull()
  })
})
