// Component tests for the §11 PACKAGES card on the create/edit page (§6.2):
// the check → install lifecycle, an install failure reverting the rows and
// toasting, and the update-all row the /packages/outdated badges unlock.
// CreateFlow renders for real (happy-dom) in edit mode with the store seeded
// and the api module mocked; payload assertions read the exact POST bodies.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Agent, Automation } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    instructions: vi.fn(async () => ({ framework: '# Framework', build: '- rules' })),
    postDraftJob: vi.fn(async () => ({ jobId: 'j1' })),
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
    // §6.2/§19: the four package endpoints the card drives
    checkPackages: vi.fn(async () => ({ packages: [] })),
    outdatedPackages: vi.fn(async () => ({ packages: [] })),
    installPackages: vi.fn(async () => ({ packages: [] })),
    updatePackages: vi.fn(async () => ({ packages: [] })),
    postTest: vi.fn(async () => ({ executionId: 'e1' })),
    cancelExecution: vi.fn(async () => ({})),
    skipStep: vi.fn(async () => ({})),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(async () => ({ lines: [] })),
    analyzeExec: vi.fn(async () => ({})),
    getAutomation: vi.fn(async () => ({})),
    deleteVersion: vi.fn(async () => ({ automation: {} })),
    saveVersion: vi.fn(async () => ({ version: 2 })),
    createAutomation: vi.fn(async () => ({ id: 'a2' })),
    state: vi.fn(async () => ({})),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
  },
}))

let storeMod: typeof import('../src/store')
let CreateFlow: typeof import('../src/pages/CreateFlow').default
let mockedApi: typeof import('../src/api').api

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  CreateFlow = (await import('../src/pages/CreateFlow')).default
  mockedApi = (await import('../src/api')).api
})

const AGENTS: Agent[] = [
  { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null, default: true },
]
const AUTO = {
  id: 'a1', name: 'My auto', description: '', version: 1,
  triggers: [], triggerChip: 'No triggers', allTriggersOff: false, nextAtMs: null,
  lastStatus: 'none', live: [], resultChip: null, resultStatus: null, lastExecutionLabel: '',
  agentId: 'g1', stepAgents: ['g1'], allowedSecrets: [], problems: [],
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true },
  specMeta: '', params: [],
  steps: [{ file: '01-a.py', name: 'Fetch pages', description: '', code: 'log("a")' }],
  spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Does things.' }],
  packages: [{ pip: 'httpx', import: 'httpx' }], versions: [], draft: null,
} as unknown as Automation

const seed = (packages: { pip: string; import: string }[]) =>
  storeMod.useStore.setState({
    surface: 'create', createFrom: 'edit', page: 'automations', automationId: 'a1',
    automations: [{ ...AUTO, packages } as unknown as Automation],
    agents: AGENTS, secrets: [], draftJobs: [],
    executions: [], executionFull: {}, execLogs: {}, toast: null, test: null,
  })

beforeEach(() => {
  vi.clearAllMocks()
  // vi.clearAllMocks clears calls, not implementations: a prior test's
  // persistent mock would otherwise leak into the next one.
  ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ chat: [] }))
  ;(mockedApi.getDraft as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ draft: null, agentId: null }))
  ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => { /* poll never answers */ }))
  ;(mockedApi.getAutomation as ReturnType<typeof vi.fn>).mockImplementation(async () => storeMod.useStore.getState().automations[0] ?? {})
  ;(mockedApi.outdatedPackages as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ packages: [] }))
  seed([{ pip: 'httpx', import: 'httpx' }])
})
afterEach(() => cleanup())

// The ui.tsx Collapse always keeps its children mounted (happy-dom renders
// them), so "open" is asserted through the .ad-collapse open class.
const collapseOf = (el: Element) => el.closest('.ad-collapse')!

describe('CreateFlow PACKAGES card (§11/§6.2)', () => {
  it('a missing package opens the card, reads not installed, and installs on demand', async () => {
    ;(mockedApi.checkPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [{ pip: 'httpx', import: 'httpx', status: 'missing' }],
    })
    render(<CreateFlow />)
    await waitFor(() => expect(mockedApi.checkPackages).toHaveBeenCalledWith([{ pip: 'httpx', import: 'httpx' }]))
    // §11: a row that is not installed forces the card open
    const row = await screen.findByText('not installed')
    expect(collapseOf(row).classList.contains('open')).toBe(true)
    expect(screen.getByText(
      'Some packages aren’t installed yet. Executions install them automatically — or install now.')).toBeTruthy()

    ;(mockedApi.installPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [{ pip: 'httpx', import: 'httpx', status: 'installed', version: '0.27.0' }],
    })
    fireEvent.click(screen.getByText('Install'))
    await waitFor(() => expect(mockedApi.installPackages).toHaveBeenCalledWith([{ pip: 'httpx', import: 'httpx' }]))
    await waitFor(() => expect(screen.getByText('installed')).toBeTruthy())
    expect(screen.getByText('0.27.0')).toBeTruthy()
  })

  it('a rejected install reverts the rows and toasts; a failed status brings the retry copy', async () => {
    ;(mockedApi.checkPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [{ pip: 'httpx', import: 'httpx', status: 'missing' }],
    })
    ;(mockedApi.installPackages as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('no network'))
    render(<CreateFlow />)
    await screen.findByText('not installed')
    fireEvent.click(screen.getByText('Install'))
    // §6.2: a failed install never blocks saving. The row simply goes back
    // to not installed and the error rides the toast
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('no network'))
    expect(screen.getByText('not installed')).toBeTruthy()
    expect(screen.getByText('Install')).toBeTruthy()

    // a response that reports the failure per package flips the card's copy
    ;(mockedApi.installPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [{ pip: 'httpx', import: 'httpx', status: 'failed', error: 'wheel build failed' }],
    })
    fireEvent.click(screen.getByText('Install'))
    await waitFor(() => expect(screen.getByText('Retry install')).toBeTruthy())
    expect(screen.getByText(
      'A package couldn’t be installed — check your connection, then retry.'
      + ' Saving still works; executions retry on their own too.')).toBeTruthy()
    expect(screen.getByText('wheel build failed')).toBeTruthy()
  })

  it('two outdated packages offer Update all, which upgrades both and toasts', async () => {
    seed([{ pip: 'httpx', import: 'httpx' }, { pip: 'lxml', import: 'lxml' }])
    ;(mockedApi.checkPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [
        { pip: 'httpx', import: 'httpx', status: 'installed', version: '1.0' },
        { pip: 'lxml', import: 'lxml', status: 'installed', version: '1.1' },
      ],
    })
    ;(mockedApi.outdatedPackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [
        { pip: 'httpx', import: 'httpx', latest: '1.2' },
        { pip: 'lxml', import: 'lxml', latest: '1.2' },
      ],
    })
    ;(mockedApi.updatePackages as ReturnType<typeof vi.fn>).mockResolvedValue({
      packages: [
        { pip: 'httpx', import: 'httpx', status: 'installed', version: '1.2' },
        { pip: 'lxml', import: 'lxml', status: 'installed', version: '1.2' },
      ],
    })
    render(<CreateFlow />)
    // everything installed → the card defaults collapsed; the header opens it
    await waitFor(() => expect(screen.getByText('2 of 2 installed · 2 updates')).toBeTruthy())
    fireEvent.click(screen.getByText('PACKAGES · PYTHON LIBRARIES'))
    expect(screen.getByText(
      'Newer versions are available. Updating applies to every automation that uses the package.')).toBeTruthy()

    fireEvent.click(screen.getByText('Update all'))
    await waitFor(() => expect(mockedApi.updatePackages).toHaveBeenCalledWith([
      { pip: 'httpx', import: 'httpx' }, { pip: 'lxml', import: 'lxml' },
    ]))
    await waitFor(() => expect(storeMod.useStore.getState().toast)
      .toBe('Updated — the new version applies to every automation using the package.'))
    // both rows carry the new version the §19 response reported
    await waitFor(() => expect(screen.getAllByText('1.2').length).toBe(2))
  })
})
