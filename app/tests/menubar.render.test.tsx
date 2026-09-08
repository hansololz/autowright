// Component test for the §13 menu-bar panel's attention count: the aggregate
// line matches the tray dot exactly: failed automations plus the §4.1
// `overdue` problem, and nothing else.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
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
