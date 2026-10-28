// §9 shell rules from the 2026-10-27 audit: the import summary's Open
// automation loads the new automation with the §9.2 Duplicate's insert (a list
// refresh a newer one superseded never strands it), and the §4.9 Command line
// toggle never patches while the cli-status read is still pending. Components
// render for real (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, ImportSummary, Settings } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    patchSettings: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let ImportSummaryModal: typeof import('../src/pages/AutomationsList').ImportSummaryModal
let SettingsPage: typeof import('../src/pages/SettingsPage').default

const cliStatus = vi.fn()
const platformInfo = vi.fn(async () => ({ platform: 'darwin', release: '25.6.0', arch: 'arm64', version: '0.13.2', trayPanel: true }))

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    cliStatus,
    platformInfo,
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  ImportSummaryModal = (await import('../src/pages/AutomationsList')).ImportSummaryModal
  SettingsPage = (await import('../src/pages/SettingsPage')).default
})

beforeEach(() => {
  mockedApi.getAutomation.mockReset()
  mockedApi.getAutomation.mockImplementation(() => Promise.reject(new Error('offline')))
  mockedApi.patchSettings.mockClear()
  cliStatus.mockReset()
})
afterEach(() => { cleanup() })

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 0, resultChip: null, resultStatus: null,
  lastExecutionLabel: '', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '', notifications: null,
  ...over,
})

const summary: ImportSummary = {
  secretsMatched: [], agentsMatched: [], unresolved: [],
  packages: [], renamedFrom: null, os: 'macos', osMismatch: false,
}

describe('import summary Open automation (§9.1)', () => {
  it('inserts the new automation even when a newer list refresh dropped it, then lands on it', async () => {
    // A superseding /state answered without the imported automation.
    storeMod.useStore.setState({ page: 'automations', automationId: null, automations: [auto()] })
    mockedApi.getAutomation.mockImplementation(async (id: string) => {
      if (id === 'a7') return auto({ id: 'a7', name: 'Imported job' })
      throw new Error('offline')
    })
    render(<ImportSummaryModal name="Imported job" automationId="a7" summary={summary} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open automation' }))
    await waitFor(() => expect(storeMod.useStore.getState().page).toBe('automation'))
    expect(storeMod.useStore.getState().automationId).toBe('a7')
    expect(storeMod.useStore.getState().automations.some((a) => a.id === 'a7')).toBe(true)
  })

  it('stays put when the user moved on while the load was in flight (§9)', async () => {
    storeMod.useStore.setState({ page: 'automations', automationId: null, automations: [auto()] })
    let land: (v: unknown) => void = () => {}
    mockedApi.getAutomation.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    render(<ImportSummaryModal name="Imported job" automationId="a7" summary={summary} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open automation' }))
    storeMod.useStore.setState({ page: 'executions' })
    land(auto({ id: 'a7', name: 'Imported job' }))
    await waitFor(() => expect(storeMod.useStore.getState().automations.some((a) => a.id === 'a7')).toBe(true))
    expect(storeMod.useStore.getState().page).toBe('executions')
  })
})

describe('Command line toggle waits for cli-status (§4.9)', () => {
  const SETTINGS: Settings = {
    login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
    notifications: 'attention', days: 30, keepForever: false, developerMode: false,
    cliEnabled: true, dataPath: '/tmp', dataSize: '0 B',
  }

  it('offers no toggle (and so never patches) until the status answers', async () => {
    let answer: (v: unknown) => void = () => {}
    cliStatus.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    storeMod.useStore.setState({ settings: { ...SETTINGS }, toast: null })
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.queryByText('COMMAND LINE')).toBeNull()
    expect(screen.queryByRole('switch', { name: 'The autowright command' })).toBeNull()
    expect(mockedApi.patchSettings).not.toHaveBeenCalled()
    answer({ state: 'installed', path: '/Users/x/.local/bin/autowright', onPath: true })
    const toggle = await screen.findByRole('switch', { name: 'The autowright command' })
    // with the status in, an off flip on an installed shim asks first (the uninstall path)
    fireEvent.click(toggle)
    expect(await screen.findByText(/Turn off the/)).toBeTruthy()
    expect(mockedApi.patchSettings).not.toHaveBeenCalled()
  })
})
