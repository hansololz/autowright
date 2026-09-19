// Component test for the §13 menu-bar panel's attention count: the aggregate
// line matches the tray dot exactly: failed automations plus the §4.1
// `overdue` problem, and nothing else.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    executeNow: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let MenuBarPanel: typeof import('../src/pages/MenuBarPanel').default

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    resizePanel: () => {},
    openApp: () => {},
  }
  storeMod = await import('../src/store')
  MenuBarPanel = (await import('../src/pages/MenuBarPanel')).default
})

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 0, resultChip: null, resultStatus: null,
  lastExecutionLabel: '', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
  ...over,
})

let mockedApi: Record<string, ReturnType<typeof vi.fn>>

beforeEach(async () => {
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  mockedApi.executeNow.mockReset()
  mockedApi.executeNow.mockResolvedValue({})
  storeMod.useStore.setState({ toast: null })
})
afterEach(() => cleanup())

describe('§13 menu-bar attention count', () => {
  it('a failed automation and an overdue one both count toward the plural line', () => {
    storeMod.useStore.setState({
      automations: [
        auto({ id: 'a1', name: 'Nightly digest', lastStatus: 'failed' }),
        auto({ id: 'a2', name: 'Price watch', problems: [{ kind: 'overdue', label: 'Overdue' }] }),
      ],
    })
    render(<MenuBarPanel />)
    expect(screen.getByText('2 need attention')).toBeTruthy()
  })

  it('one automation needing attention takes the singular verb', () => {
    storeMod.useStore.setState({
      automations: [
        auto({ id: 'a1', name: 'Nightly digest', lastStatus: 'failed' }),
        auto({ id: 'a2', name: 'Price watch' }),
      ],
    })
    render(<MenuBarPanel />)
    expect(screen.getByText('1 needs attention')).toBeTruthy()
    expect(screen.queryByText('1 need attention')).toBeNull()
  })

  it('nothing wrong reads as the all-good line instead of a count', () => {
    storeMod.useStore.setState({
      automations: [auto({ id: 'a1', name: 'Nightly digest' })],
    })
    render(<MenuBarPanel />)
    expect(screen.getByText('All good · 1 automation')).toBeTruthy()
  })
})

// §7: the no-free-slot 409 reads the same here as on every other execute
// surface — what happens next depends on the automation's §6 slots.
describe('§13 menu-bar execute now', () => {
  const execute = () => fireEvent.click(screen.getByRole('button', { name: 'Execute now' }))
  // §19: the busy toast keys on `reason: "capacity"` — the only 409 body that
  // carries one.
  const reject = (status: number, message = 'already executing', reason?: string) => {
    mockedApi.executeNow.mockRejectedValue(Object.assign(new Error(message), { status, reason }))
  }

  it('a 409 with the default slots toasts the one-at-a-time line', async () => {
    storeMod.useStore.setState({ automations: [auto({ maxParallel: 1, maxQueued: 0 })] })
    reject(409, 'already executing', 'capacity')
    render(<MenuBarPanel />)
    execute()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe(
      'Already executing — one execution at a time. A trigger firing now would be skipped.'))
  })

  it('a 409 with a queue says the firing would be queued', async () => {
    storeMod.useStore.setState({ automations: [auto({ maxParallel: 2, maxQueued: 5 })] })
    reject(409, 'already executing', 'capacity')
    render(<MenuBarPanel />)
    execute()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe(
      'All 2 slots are busy. A trigger firing now would be queued.'))
  })

  // §19: every other 409 (shutting down, being deleted, a full queue) carries
  // `detail` alone, and the surface shows that verbatim — the busy copy would
  // promise the wrong thing.
  it('a 409 with no capacity reason shows the backend detail verbatim', async () => {
    storeMod.useStore.setState({ automations: [auto({ maxParallel: 1, maxQueued: 3 })] })
    reject(409, 'the queue is full (3 waiting)')
    render(<MenuBarPanel />)
    execute()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('the queue is full (3 waiting)'))
  })

  it('any other failure still toasts its own message', async () => {
    storeMod.useStore.setState({ automations: [auto()] })
    reject(500, 'backend is restarting')
    render(<MenuBarPanel />)
    execute()
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('backend is restarting'))
  })
})
