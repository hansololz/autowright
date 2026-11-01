// Regression tests for the 2026-10-31 third sweep, §11 editor: the §4.4
// continuous draft persist retries a failed write every 5 s and at once on a
// reconnect, says so in the hint slot after two consecutive failures, clears
// it on the next success, and stops once the editor settles. CreateFlow
// renders for real (happy-dom, StrictMode via the setup file) against the
// real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Agent, Automation, SecretMeta } from '../src/types'

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

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath: () => Promise.resolve({ ok: true }),
    cliStatus: () => Promise.reject(new Error('no bridge')),
    platformInfo: () => new Promise(() => {}),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  CreateFlow = (await import('../src/pages/CreateFlow')).default
})

const AGENTS: Agent[] = [
  { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null, default: true },
]
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
const RETRYING = 'Draft not saved yet — retrying…'

beforeEach(() => {
  for (const fn of Object.values(mockedApi)) fn.mockClear()
  mockedApi.getChat.mockImplementation(async () => ({ chat: [] }))
  mockedApi.getDraft.mockImplementation(async () => ({ draft: null, agentId: null }))
  mockedApi.putDraft.mockImplementation(() => Promise.reject(new Error('backend restarting')))
  mockedApi.deleteDraft.mockImplementation(async () => ({}))
  mockedApi.getAutomation.mockImplementation(() => Promise.reject(new Error('offline')))
  storeMod.useStore.setState({
    surface: 'create', createFrom: 'edit', page: 'automation', automationId: 'a1',
    automations: [AUTO], agents: AGENTS, secrets: [] as SecretMeta[],
    executions: [], executionsTotal: 0, executionFull: {}, execLogs: {}, toast: null, test: null,
    reconnects: 0,
  })
  // the debounce and retry timers are faked, real time still flows for waitFor
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const puts = () => mockedApi.putDraft.mock.calls.length

describe('M a failed continuous persist retries until it lands (§4.4, §11)', () => {
  it('retries after 5 s, shows the hint after two failures, and clears it on a success', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(puts()).toBe(1), { timeout: 3000 })
    await advance(0)
    // one failure: silent
    expect(screen.queryByText(RETRYING)).toBeNull()
    await advance(5000)
    expect(puts()).toBe(2)
    await waitFor(() => expect(screen.getByText(RETRYING)).toBeTruthy())
    // the next write lands: the hint clears and no further retry is armed
    mockedApi.putDraft.mockImplementation(async () => ({}))
    await advance(5000)
    expect(puts()).toBe(3)
    await waitFor(() => expect(screen.queryByText(RETRYING)).toBeNull())
    await advance(15000)
    expect(puts()).toBe(3)
  })

  it('a reconnect retries a failing write at once', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(puts()).toBe(1), { timeout: 3000 })
    await advance(0)
    act(() => { storeMod.useStore.setState({ reconnects: 1 }) })
    await waitFor(() => expect(puts()).toBe(2))
  })

  it('a settled editor stops retrying', async () => {
    render(<CreateFlow />)
    await waitFor(() => expect(puts()).toBe(1), { timeout: 3000 })
    await advance(0)
    // Discard draft settles the editor; its DELETE stays in flight so the
    // editor is still mounted while the retry would have fired
    mockedApi.deleteDraft.mockImplementation(() => new Promise(() => { /* in flight */ }))
    fireEvent.click(screen.getByText('Discard draft'))
    await waitFor(() => expect(mockedApi.deleteDraft).toHaveBeenCalledWith('a1'))
    await advance(20000)
    expect(puts()).toBe(1)
    expect(storeMod.useStore.getState().surface).toBe('create')
  })
})
