// Component tests for the §9.2 MEMORY card: the destructive confirms whose
// copy follows the §6.3 automatic-snapshot settings (clear and restore), the
// live-execution block on a restore, the snapshot rename/delete rows, and the
// automatic-snapshot toggles' single-key PATCH. The card renders for real
// (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, MemorySnapshot, SnapshotSettings } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    clearMemory: vi.fn(async () => ({})),
    createSnapshot: vi.fn(async () => ({})),
    restoreSnapshot: vi.fn(async () => ({})),
    renameSnapshot: vi.fn(async () => ({})),
    deleteSnapshot: vi.fn(async () => ({})),
    patchAutomation: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let MemoryCard: typeof import('../src/pages/detail/MemoryCard').MemoryCard
const revealPath = vi.fn(async () => {})

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    revealPath,
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  MemoryCard = (await import('../src/pages/detail/MemoryCard')).MemoryCard
})

const snapshot = (over: Partial<MemorySnapshot> = {}): MemorySnapshot => ({
  id: 's1', name: 'Nightly', reason: 'manual', when: 'Today', version: 'v1',
  size: '4 KB', files: 2, ...over,
})

const auto = (
  snapshotSettings: SnapshotSettings, snapshots: MemorySnapshot[] = [],
): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {}, snapshotSettings, specMeta: '',
  memory: { path: '/m', size: '12 KB', updated: 'Today' }, snapshots,
})

const settings = (over: Partial<SnapshotSettings> = {}): SnapshotSettings =>
  ({ preVersion: true, preClear: true, preRestore: true, ...over })

// §6.3 toggle rows: label div → its text/help wrapper → the row holding the switch
const toggleFor = (label: string) =>
  screen.getByText(label).parentElement!.parentElement!
    .querySelector('button[role="switch"]') as HTMLButtonElement

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({ automations: [], toast: null })
})
afterEach(() => cleanup())

describe('§9.2 MEMORY card clear confirm', () => {
  it('says the current memory is snapshotted first while the pre-clear snapshot is on', () => {
    render(<MemoryCard auto={auto(settings())} executing={false} />)
    fireEvent.click(screen.getByText('Clear memory'))
    expect(screen.getByText(
      'Next execution starts fresh, like the first time. Current memory is snapshotted first.',
    )).toBeTruthy()
  })

  it('warns the clear cannot be undone once the pre-clear snapshot is off', () => {
    render(<MemoryCard auto={auto(settings({ preClear: false }))} executing={false} />)
    fireEvent.click(screen.getByText('Clear memory'))
    expect(screen.getByText(
      "Next execution starts fresh, like the first time. Automatic snapshots are off — this can't be undone.",
    )).toBeTruthy()
  })
})

describe('§9.2 MEMORY card restore confirm', () => {
  it('says the current state is snapshotted first while the pre-restore snapshot is on', () => {
    render(<MemoryCard auto={auto(settings(), [snapshot()])} executing={false} />)
    fireEvent.click(screen.getByText('Restore'))
    expect(screen.getByText(
      'Replaces current memory — the current state is snapshotted first.',
    )).toBeTruthy()
  })

  it('warns the current state is lost once the pre-restore snapshot is off', () => {
    render(<MemoryCard auto={auto(settings({ preRestore: false }), [snapshot()])} executing={false} />)
    fireEvent.click(screen.getByText('Restore'))
    expect(screen.getByText(
      'Replaces current memory — automatic snapshots are off, so the current state is lost.',
    )).toBeTruthy()
  })

  it('blocks the restore while an execution is live', () => {
    const { unmount } = render(
      <MemoryCard auto={auto(settings(), [snapshot()])} executing />)
    fireEvent.click(screen.getByText('Restore'))
    const blocked = screen.getByText('Restore').closest('button') as HTMLButtonElement
    expect(blocked.disabled).toBe(true)
    expect(blocked.title).toBe('Blocked while an execution is live')

    unmount()
    render(<MemoryCard auto={auto(settings(), [snapshot()])} executing={false} />)
    fireEvent.click(screen.getByText('Restore'))
    const free = screen.getByText('Restore').closest('button') as HTMLButtonElement
    expect(free.disabled).toBe(false)
    expect(free.title).toBe('')
  })
})

describe('§6.3 snapshot rows', () => {
  it('renaming to an empty field clears the name rather than storing one', async () => {
    render(<MemoryCard auto={auto(settings(), [snapshot()])} executing={false} />)
    fireEvent.click(screen.getByText('Rename'))
    const input = screen.getByDisplayValue('Nightly')
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(mockedApi.renameSnapshot).toHaveBeenCalledTimes(1))
    expect(mockedApi.renameSnapshot).toHaveBeenCalledWith('a1', 's1', null)
  })

  it('deleting asks first, then deletes and toasts', async () => {
    render(<MemoryCard auto={auto(settings(), [snapshot()])} executing={false} />)
    fireEvent.click(screen.getByText('Delete'))
    expect(screen.getByText('Delete this snapshot?')).toBeTruthy()
    expect(mockedApi.deleteSnapshot).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Delete'))
    await waitFor(() => expect(mockedApi.deleteSnapshot).toHaveBeenCalledTimes(1))
    expect(mockedApi.deleteSnapshot).toHaveBeenCalledWith('a1', 's1')
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Snapshot deleted.'))
  })
})

describe('§6.3 automatic-snapshot toggles', () => {
  it('each toggle PATCHes its own key alone', async () => {
    render(<MemoryCard auto={auto(settings())} executing={false} />)
    fireEvent.click(toggleFor('Before clearing memory'))
    await waitFor(() => expect(mockedApi.patchAutomation).toHaveBeenCalledTimes(1))
    expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', {
      snapshotSettings: { preClear: false },
    })
  })
})
