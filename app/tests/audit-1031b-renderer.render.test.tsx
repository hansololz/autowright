// Regression tests for the 2026-10-31 renderer sweep (second pass): the §11
// editor's back link writes no draft while a Save or Discard is in flight
// and Save refuses while the leave path's own write is out; Save navigates
// only while the editor is still the shown page (§9 landing rule); the §4.4
// one-editor-per-version rule (`baseVersion`, the stale-seed hint, the 409);
// a chat job cancelled outside the editor still ends the turn visibly; the
// §7 preset tick never drops a Next page; the §9.2 Delete confirm disables
// the actions for the record being deleted; and the §9.2 PARAMETERS toggle's
// request counter. Components render for real (happy-dom, StrictMode via the
// setup file) against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Agent, Automation, Execution, ParamDef, SecretMeta } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    instructions: vi.fn(async () => ({ framework: '# Framework', build: '- build rules' })),
    postDraftJob: vi.fn(async () => ({ jobId: 'j1' })),
    validateSteps: vi.fn(async () => ({ errors: [] })),
    patchAutomation: vi.fn(async () => ({})),
    getDraftJob: vi.fn(() => new Promise(() => { /* poll never answers in tests */ })),
    cancelDraftJob: vi.fn(async () => ({})),
    ackDraftJob: vi.fn(async () => ({})),
    putDraft: vi.fn(async () => ({})),
    deleteDraft: vi.fn(async () => ({})),
    getDraft: vi.fn(async () => ({ draft: null, agentId: null })),
    openDraft: vi.fn(async () => ({})),
    getChat: vi.fn(async () => ({ chat: [] })),
    putChat: vi.fn(async () => ({})),
    checkPackages: vi.fn(async () => ({ packages: [] })),
    outdatedPackages: vi.fn(async () => ({ packages: [] })),
    postTest: vi.fn(async () => ({ executionId: 'e1' })),
    cancelExecution: vi.fn(async () => ({})),
    skipStep: vi.fn(async () => ({})),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
    analyzeExec: vi.fn(async () => ({})),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    deleteAutomation: vi.fn(async () => ({})),
    versionDiff: vi.fn(async () => ({ from: 1, to: 2, files: [] })),
    saveVersion: vi.fn(async () => ({ version: 5 })),
    restore: vi.fn(async () => ({ version: 5 })),
    createAutomation: vi.fn(async () => ({ id: 'a2' })),
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
  },
}))

vi.mock('../src/executionView', () => ({
  ExecutionView: () => <div data-testid="execution-view" />,
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let CreateFlow: typeof import('../src/pages/CreateFlow').default
let ExecutionsList: typeof import('../src/pages/ExecutionsList').default
let AutomationDetail: typeof import('../src/pages/AutomationDetail').default
let ParamRow: typeof import('../src/pages/detail/ParamRow').ParamRow

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath: () => Promise.resolve(),
    cliStatus: () => Promise.reject(new Error('no bridge')),
    platformInfo: () => new Promise(() => {}),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  CreateFlow = (await import('../src/pages/CreateFlow')).default
  ExecutionsList = (await import('../src/pages/ExecutionsList')).default
  AutomationDetail = (await import('../src/pages/AutomationDetail')).default
  ParamRow = (await import('../src/pages/detail/ParamRow')).ParamRow
})

const AGENTS: Agent[] = [
  { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null, default: true },
]
const SECRETS: SecretMeta[] = []
const STEPS = [{ file: '01-a.py', name: 'Fetch pages', description: '', code: 'log("a")' }]
const SPEC = [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Does things.' }]
const AUTO = {
  id: 'a1', name: 'My auto', description: '', version: 4,
  triggers: [], triggerChip: 'No triggers', allTriggersOff: false, nextAtMs: null,
  lastStatus: 'none', live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: '', agentId: 'g1', stepAgents: ['g1'], allowedSecrets: [], problems: [],
  unresolvedReferences: {}, notes: '', notifications: null,
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true },
  specMeta: '', params: [], steps: STEPS, spec: SPEC, packages: [], versions: [],
  // a kept draft: the editor holds draft edits from the moment it seeds
  draft: { spec: SPEC, steps: STEPS, notes: '', stepAgents: ['g1'], allowedSecrets: [] },
} as unknown as Automation

const STALE_HINT = 'Version v5 was saved outside this editor — close and reopen Edit to continue from it.'
const STOPPED = 'Edit stopped — the spec is unchanged.'
const NOW = 1_700_000_000_000

beforeEach(() => {
  for (const fn of Object.values(mockedApi)) fn.mockClear()
  mockedApi.getChat.mockImplementation(async () => ({ chat: [] }))
  mockedApi.getDraft.mockImplementation(async () => ({ draft: null, agentId: null }))
  mockedApi.postDraftJob.mockImplementation(async () => ({ jobId: 'j1' }))
  mockedApi.getDraftJob.mockImplementation(() => new Promise(() => { /* never answers */ }))
  mockedApi.putDraft.mockImplementation(async () => ({}))
  mockedApi.deleteDraft.mockImplementation(async () => ({}))
  mockedApi.saveVersion.mockImplementation(async () => ({ version: 5 }))
  mockedApi.deleteAutomation.mockImplementation(async () => ({}))
  mockedApi.patchAutomation.mockImplementation(async () => ({}))
  mockedApi.getAutomation.mockImplementation(() => Promise.reject(new Error('offline')))
  mockedApi.listExecutions.mockImplementation(async () => ({ executions: [], total: 0 }))
  storeMod.useStore.setState({
    surface: 'create', createFrom: 'edit', page: 'automation', automationId: 'a1',
    automations: [AUTO], agents: AGENTS, secrets: SECRETS,
    executions: [], executionsTotal: 0, executionFull: {}, execLogs: {}, toast: null, test: null,
    reconnects: 0,
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const settle = (ms = 300) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)) })
const backLink = () => screen.getByText('My auto', { selector: 'button' }) as HTMLButtonElement
const saveButton = (label = 'Save as v5') => screen.getByText(label).closest('button') as HTMLButtonElement
const toast = () => storeMod.useStore.getState().toast
const composerInput = () =>
  screen.getByPlaceholderText('Change something, or ask a question…') as HTMLTextAreaElement

describe('U1 the back link writes no draft over a save or discard in flight (§11, §4.4)', () => {
  it('Save then the back link: the link disables and no PUT /draft follows', async () => {
    mockedApi.saveVersion.mockImplementation(() => new Promise(() => { /* in flight */ }))
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    fireEvent.click(saveButton())
    await waitFor(() => expect(mockedApi.saveVersion).toHaveBeenCalledTimes(1))
    const before = mockedApi.putDraft.mock.calls.length
    expect(backLink().disabled).toBe(true)
    fireEvent.click(backLink())
    await settle(1300)
    expect(mockedApi.putDraft.mock.calls.length).toBe(before)
    expect(storeMod.useStore.getState().surface).toBe('create')
  })

  it('Discard draft then the back link: no PUT /draft resurrects the discarded draft', async () => {
    mockedApi.deleteDraft.mockImplementation(() => new Promise(() => { /* in flight */ }))
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    const before = mockedApi.putDraft.mock.calls.length
    fireEvent.click(screen.getByText('Discard draft'))
    await waitFor(() => expect(mockedApi.deleteDraft).toHaveBeenCalledWith('a1'))
    expect(backLink().disabled).toBe(true)
    fireEvent.click(backLink())
    await settle(1300)
    expect(mockedApi.putDraft.mock.calls.length).toBe(before)
  })

  it('while the leave path writes the draft, Save is disabled and sends nothing', async () => {
    mockedApi.putDraft.mockImplementation(() => new Promise(() => { /* in flight */ }))
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    fireEvent.click(backLink())
    await waitFor(() => expect(saveButton().disabled).toBe(true))
    fireEvent.click(saveButton())
    await settle()
    expect(mockedApi.saveVersion).not.toHaveBeenCalled()
  })
})

describe('U2 Save navigates only while the editor is still shown (§9 landing rule)', () => {
  it('a save landing after the user moved to Secrets does not pull them back', async () => {
    let land: (v: { version: number }) => void = () => {}
    mockedApi.saveVersion.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    fireEvent.click(saveButton())
    await waitFor(() => expect(mockedApi.saveVersion).toHaveBeenCalledTimes(1))
    act(() => { storeMod.useStore.getState().go('secrets') })
    expect(storeMod.useStore.getState().page).toBe('secrets')
    await act(async () => { land({ version: 5 }) })
    await settle()
    expect(storeMod.useStore.getState().page).toBe('secrets')
    expect(storeMod.useStore.getState().surface).toBe('app')
  })

  it('a save landing while the editor is still shown navigates to the detail page', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    fireEvent.click(saveButton())
    await waitFor(() => expect(storeMod.useStore.getState().surface).toBe('app'))
    expect(storeMod.useStore.getState().page).toBe('automation')
  })
})

describe('N one editor per version: baseVersion and the stale seed (§4.4, §19)', () => {
  it('draft PUTs and the save carry the seed version as baseVersion', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    await waitFor(() => expect(mockedApi.putDraft).toHaveBeenCalled(), { timeout: 3000 })
    expect(mockedApi.putDraft.mock.calls[0][0]).toBe('a1')
    expect(mockedApi.putDraft.mock.calls[0][3]).toBe(4)
    fireEvent.click(saveButton())
    await waitFor(() => expect(mockedApi.saveVersion).toHaveBeenCalledTimes(1))
    expect(mockedApi.saveVersion.mock.calls[0][1]).toEqual(expect.objectContaining({ baseVersion: 4 }))
  })

  it('a version minted outside the editor disables Save, shows the hint, and stops the draft writes', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    act(() => { storeMod.useStore.setState({ automations: [{ ...AUTO, version: 5 } as Automation] }) })
    await waitFor(() => expect(screen.getByText(STALE_HINT)).toBeTruthy())
    expect(saveButton('Save as v6').disabled).toBe(true)
    mockedApi.putDraft.mockClear()
    fireEvent.click(saveButton('Save as v6'))
    await settle(1300)
    expect(mockedApi.saveVersion).not.toHaveBeenCalled()
    // the leave path writes nothing either, and neither does the unmount flush
    fireEvent.click(backLink())
    await settle()
    cleanup()
    await settle()
    expect(mockedApi.putDraft).not.toHaveBeenCalled()
  })

  it('a 409 from the save surfaces its detail through the save error', async () => {
    const detail = 'version v5 was saved outside this editor — close and reopen Edit to continue from it'
    mockedApi.saveVersion.mockImplementation(() => Promise.reject(new Error(detail)))
    render(<CreateFlow />)
    await waitFor(() => expect(saveButton().disabled).toBe(false))
    fireEvent.click(saveButton())
    await waitFor(() => expect(toast()).toBe(detail))
    expect(storeMod.useStore.getState().surface).toBe('create')
  })
})

describe('O a chat job cancelled outside the editor ends the turn visibly (§11)', () => {
  it('the poll observes cancelled: the stopped chip lands and the request returns to the composer', async () => {
    let cancelled = false
    mockedApi.getDraftJob.mockImplementation(async () => (cancelled
      ? { id: 'j1', status: 'cancelled', stage: null, detail: null, error: null, mode: 'chat', events: [] }
      : { id: 'j1', status: 'building', stage: 'Working on the request', detail: null, error: null, mode: 'chat', events: [] }))
    storeMod.useStore.setState({ automations: [{ ...AUTO, draft: null } as unknown as Automation] })
    render(<CreateFlow />)
    await waitFor(() => expect(composerInput()).toBeTruthy())
    fireEvent.change(composerInput(), { target: { value: 'Do a thing' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(mockedApi.getDraftJob).toHaveBeenCalled(), { timeout: 3000 })
    cancelled = true
    const thread = () => screen.getByTestId('chat-thread')
    await waitFor(() => expect(within(thread()).getByText(STOPPED)).toBeTruthy(), { timeout: 3000 })
    expect(within(thread()).getByText('Do a thing')).toBeTruthy()
    expect(composerInput().value).toBe('Do a thing')
    expect(mockedApi.cancelDraftJob).not.toHaveBeenCalled()
  })
})

describe('X the preset tick never drops a Next page (§7)', () => {
  const ex = (i: number): Execution => ({
    id: `e-${String(i).padStart(4, '0')}`, automationId: 'a1', automationName: 'Job', automationDeleted: false,
    versionLabel: 'v1', status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false,
    duration: '1.0s', started: 'Today, 8:00 AM', startedMs: NOW - i * 1000, endedMs: NOW - i * 1000,
    queuedMs: 0, durationMs: null, passStartedMs: 0, note: null, error: null,
  })

  it('a tick during an in-flight Next fetch lets the landing page advance', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const all = Array.from({ length: 120 }, (_, i) => ex(i))
    let landNext: () => void = () => {}
    mockedApi.listExecutions.mockImplementation((q: { limit: number; before?: { id: string } }) => {
      const start = q.before ? all.findIndex((e) => e.id === q.before!.id) + 1 : 0
      const answer = { executions: all.slice(start, start + q.limit), total: all.length }
      if (!q.before) return Promise.resolve(answer)
      return new Promise((resolve) => { landNext = () => resolve(answer) })
    })
    storeMod.useStore.setState({ surface: 'app', page: 'executions', automationId: null })
    render(<ExecutionsList />)
    fireEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    const dlg = screen.getByRole('dialog', { name: 'Filter executions' })
    fireEvent.click(within(dlg).getByRole('radio', { name: 'Last hour' }))
    fireEvent.click(within(dlg).getByRole('button', { name: 'Apply' }))
    fireEvent.animationEnd(dlg.parentElement!)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const pager = () => screen.getByTestId('executions-pager')
    expect(within(pager()).getByText('1–50 of 120')).toBeTruthy()
    fireEvent.click(within(pager()).getByText('Next').closest('button')!)
    const calls = mockedApi.listExecutions.mock.calls.length
    // the 60 s tick fires while Next's fetch is out: it skips its refetch
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(mockedApi.listExecutions.mock.calls.length).toBe(calls)
    await act(async () => { landNext() })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(within(pager()).getByText('51–100 of 120')).toBeTruthy()
  })
})

describe('U5 the Delete confirm disables the record\'s actions until the DELETE lands (§9)', () => {
  it('Delete twice sends one DELETE; the menu, Execute now and Edit disable meanwhile', async () => {
    let land: () => void = () => {}
    mockedApi.deleteAutomation.mockImplementation(() => new Promise<void>((resolve) => { land = resolve }))
    storeMod.useStore.setState({ surface: 'app', automations: [{ ...AUTO, draft: null } as unknown as Automation] })
    render(<AutomationDetail />)
    const menuTrigger = () => screen.getByLabelText('Automation actions') as HTMLButtonElement
    fireEvent.click(menuTrigger())
    fireEvent.click(screen.getByText('Delete automation…'))
    fireEvent.click(screen.getByText('Delete automation'))
    const dialog = screen.getByRole('alertdialog', { name: 'Delete this automation?' })
    fireEvent.animationEnd(dialog.parentElement!)
    await waitFor(() => expect(mockedApi.deleteAutomation).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(menuTrigger().disabled).toBe(true))
    expect((screen.getByText('Edit', { selector: 'button' }) as HTMLButtonElement).disabled).toBe(true)
    expect((document.querySelector('.ad-btn-primary') as HTMLButtonElement).disabled).toBe(true)
    // a second Delete has no way in while the first is out
    fireEvent.click(menuTrigger())
    const again = screen.queryByText('Delete automation…')
    if (again) fireEvent.click(again)
    if (again) {
      fireEvent.click(screen.getByText('Delete automation'))
      const second = screen.getByRole('alertdialog', { name: 'Delete this automation?' })
      fireEvent.animationEnd(second.parentElement!)
    }
    await settle()
    expect(mockedApi.deleteAutomation).toHaveBeenCalledTimes(1)
    await act(async () => { land() })
  })
})

describe('U6 the PARAMETERS toggle keeps the newest optimistic value (§9.2)', () => {
  it('two quick toggles: the first reload landing never wipes the second click', async () => {
    const reloads: Array<(a: Automation) => void> = []
    mockedApi.getAutomation.mockImplementation(() => new Promise((resolve) => { reloads.push(resolve) }))
    storeMod.useStore.setState({ surface: 'app', automations: [{ ...AUTO, draft: null } as unknown as Automation] })
    const param = (on: boolean) => ({ name: 'flag', label: 'Flag', help: '', kind: 'toggle', on } as unknown as ParamDef)
    const { rerender } = render(<ParamRow automationId="a1" p={param(false)} last />)
    const toggle = () => screen.getByRole('switch')
    fireEvent.click(toggle()) // on
    fireEvent.click(toggle()) // off again
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    await waitFor(() => expect(reloads.length).toBe(2))
    // the first click's reload lands: the server now reads on
    await act(async () => { reloads[0]({ ...AUTO, draft: null } as unknown as Automation) })
    rerender(<ParamRow automationId="a1" p={param(true)} last />)
    await settle()
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    // the second click's reload lands: the server reads off, the row follows it
    await act(async () => { reloads[1]({ ...AUTO, draft: null } as unknown as Automation) })
    rerender(<ParamRow automationId="a1" p={param(false)} last />)
    await settle()
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })
})
