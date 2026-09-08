// Component tests for the §9.2 CONCURRENCY card: the number rows' commit-on-blur
// clamp, the §6 memory-conflict caution that names the offending steps, and the
// live queue row with its Clear queue confirm. The card renders for real
// (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Automation, Execution, Step } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    clearQueue: vi.fn(async () => ({ cancelled: 2 })),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let ConcurrencyCard: typeof import('../src/pages/detail/ConcurrencyCard').ConcurrencyCard

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  ConcurrencyCard = (await import('../src/pages/detail/ConcurrencyCard')).ConcurrencyCard
})

const NOW = 1_700_000_000_000

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 2, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
  ...over,
})

const step = (name: string, code: string): Step => ({ name, description: '', code })

const queuedRow = (id: string, over: Partial<Execution> = {}): Execution => ({
  id, automationId: 'a1', automationName: 'Job', automationDeleted: false, versionLabel: 'v1',
  status: 'queued', trigger: 'Discord', triggerSender: null, test: false, duration: '',
  started: 'Today, 8:00 AM', startedMs: NOW, endedMs: 0, queuedMs: NOW - 5_000, durationMs: null,
  passStartedMs: 0, note: null, error: null, ...over,
})

const showToast = vi.fn()

// The §6 caution is the card's one Notice; read the whole line off it, since
// the step names are <code> children inside the sentence.
const cautionText = () =>
  Array.from(document.querySelectorAll('.ad-anim-item'))
    .map((el) => el.textContent ?? '')
    .find((t) => t.includes('to memory.'))

// ConfirmModal acts on onClose, which fires only after the overlay's exit
// animation. happy-dom runs no animations, so end it by hand.
const finishModalAnim = () =>
  fireEvent.animationEnd(screen.getByRole('alertdialog').parentElement!)

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({ automations: [], executions: [], toast: null })
})
afterEach(() => cleanup())

describe('§9.2 CONCURRENCY number rows', () => {
  it('a blurred zero commits the minimum instead', async () => {
    render(<ConcurrencyCard auto={auto({ maxParallel: 2 })} showToast={showToast} />)
    const input = screen.getByDisplayValue('2')
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.blur(input)
    await waitFor(() => expect(mockedApi.patchAutomation).toHaveBeenCalledTimes(1))
    expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', { maxParallel: 1 })
  })

  it('blurring an emptied field PATCHes nothing', async () => {
    render(<ConcurrencyCard auto={auto({ maxParallel: 2 })} showToast={showToast} />)
    const input = screen.getByDisplayValue('2')
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    await waitFor(() => expect(screen.getByDisplayValue('2')).toBeTruthy())
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
  })
})

describe('§6 memory-conflict caution', () => {
  const steps = [step('Save it', 'memory.save(x)'), step('Fetch', 'fetch_page()')]

  it('names only the steps that touch memory, in the singular', () => {
    render(<ConcurrencyCard auto={auto({ maxParallel: 2, steps })} showToast={showToast} />)
    expect(cautionText()).toBe(
      'Save it writes to memory. Parallel executions share one memory directory, '
      + 'so two runs updating the same value can lose one of the updates.',
    )
    expect(screen.queryByText('Fetch')).toBeNull()
  })

  it('switches to the plural once two steps match', () => {
    render(<ConcurrencyCard
      auto={auto({ maxParallel: 2, steps: [...steps, step('Note it', 'memory.append(y)')] })}
      showToast={showToast}
    />)
    expect(cautionText()).toBe(
      'Save it, Note it write to memory. Parallel executions share one memory directory, '
      + 'so two runs updating the same value can lose one of the updates.',
    )
  })

  it('is absent while only one execution may run at a time', () => {
    render(<ConcurrencyCard auto={auto({ maxParallel: 1, steps })} showToast={showToast} />)
    expect(cautionText()).toBeUndefined()
  })
})

describe('§9.2 queue row', () => {
  it('counts the queued executions and clears them on confirm', async () => {
    storeMod.useStore.setState({ executions: [queuedRow('e1'), queuedRow('e2')] })
    render(<ConcurrencyCard auto={auto()} showToast={showToast} />)
    expect(screen.getByText('2 waiting')).toBeTruthy()

    fireEvent.click(screen.getByText('Clear queue'))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText(
      'Cancel 2 waiting messages? Each sender is told. Executions already running are not affected.',
    )).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Clear queue' }))
    finishModalAnim()
    await waitFor(() => expect(mockedApi.clearQueue).toHaveBeenCalledTimes(1))
    expect(mockedApi.clearQueue).toHaveBeenCalledWith('a1')
    await waitFor(() =>
      expect(storeMod.useStore.getState().toast).toBe('2 waiting messages cancelled.'))
  })

  it('a lone queued execution reads in the singular', () => {
    storeMod.useStore.setState({ executions: [queuedRow('e1')] })
    render(<ConcurrencyCard auto={auto()} showToast={showToast} />)
    expect(screen.getByText('1 waiting')).toBeTruthy()
    fireEvent.click(screen.getByText('Clear queue'))
    expect(within(screen.getByRole('alertdialog')).getByText(
      'Cancel 1 waiting message? Each sender is told. Executions already running are not affected.',
    )).toBeTruthy()
  })

  it('a queued §11 test run never counts toward the waiting row', () => {
    storeMod.useStore.setState({ executions: [queuedRow('e1', { test: true })] })
    render(<ConcurrencyCard auto={auto()} showToast={showToast} />)
    expect(screen.queryByText('1 waiting')).toBeNull()
    expect(screen.queryByText('Clear queue')).toBeNull()
  })
})
