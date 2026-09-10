// §5.1/§9.1 file import: the native pick can come back refused (an archive
// over the 64 MB cap) or throw, and the modal must say so on its own error
// line instead of sitting there looking dead. A cancel stays silent.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    importFromUrl: vi.fn(() => Promise.reject(new Error('offline'))),
    importPreview: vi.fn(() => Promise.reject(new Error('offline'))),
    importConfirm: vi.fn(() => Promise.reject(new Error('offline'))),
  },
}))

let storeMod: typeof import('../src/store')
let AutomationsList: typeof import('../src/pages/AutomationsList').default

const openArchive = vi.fn()

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    openArchive,
  }
  storeMod = await import('../src/store')
  AutomationsList = (await import('../src/pages/AutomationsList')).default
})

const auto = (): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 0, resultChip: null, resultStatus: null,
  lastExecutionLabel: '', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
})

afterEach(() => { cleanup(); openArchive.mockReset() })

// Opens the import modal and presses its file picker.
const pickFile = () => {
  storeMod.useStore.setState({
    page: 'automations', automations: [auto()], draftJobs: [], pendingDraft: null,
  })
  render(<AutomationsList />)
  fireEvent.click(screen.getByText('Import'))
  fireEvent.click(screen.getByText(/Choose an \.autowright file/))
}

describe('§9.1 import from a file, failed picks', () => {
  it('an oversized archive reports the main process’ own line', async () => {
    openArchive.mockResolvedValueOnce({ error: 'The archive is larger than 64 MB.' })
    pickFile()
    expect(await screen.findByText('The archive is larger than 64 MB.')).toBeTruthy()
    // The picker is still there to try again with — nothing is left spinning.
    expect(screen.getByText(/Choose an \.autowright file/)).toBeTruthy()
  })

  it('a pick that throws reports the thrown message', async () => {
    openArchive.mockRejectedValueOnce(new Error('No handler for open-archive'))
    pickFile()
    expect(await screen.findByText('No handler for open-archive')).toBeTruthy()
  })

  it('a cancelled pick says nothing at all', async () => {
    openArchive.mockResolvedValueOnce(null)
    pickFile()
    await waitFor(() => expect(openArchive).toHaveBeenCalled())
    expect(screen.queryByText(/larger than 64 MB/)).toBeNull()
    expect(screen.getByText(/Choose an \.autowright file/)).toBeTruthy()
  })
})
