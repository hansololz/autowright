// Component tests for the §9.2 NOTIFICATIONS card: the §4.1 per-automation
// override behind the same capability gate as the §4.9 Settings row, its four
// choices (inherit naming the app value), the PATCH each sends (null clears
// back to inherit), the revert on failure, and the `never` caption. The card
// renders for real (happy-dom) against the real store with the api mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Settings } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let NotificationsCard: typeof import('../src/pages/detail/NotificationsCard').NotificationsCard

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  NotificationsCard = (await import('../src/pages/detail/NotificationsCard')).NotificationsCard
})

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 0, notifications: null, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
  ...over,
})

const showToast = vi.fn()
const NEVER_CAPTION = 'Failures still show on the Automations list and the menu bar.'

const setCapability = (notifications: boolean) => {
  const s = storeMod.useStore.getState()
  storeMod.useStore.setState({ platformCapabilities: { ...s.platformCapabilities, notifications } })
}

const menuRows = () => Array.from(document.querySelectorAll('.ad-menu-row')).map((el) => el.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  setCapability(true)
  storeMod.useStore.setState({
    automations: [], toast: null,
    settings: { notifications: 'attention' } as unknown as Settings,
  })
})
afterEach(() => cleanup())

describe('§9.2 NOTIFICATIONS card', () => {
  it('renders nothing while the OS notification API is unavailable', () => {
    setCapability(false)
    const { container } = render(<NotificationsCard auto={auto()} showToast={showToast} />)
    expect(container.textContent).toBe('')
    expect(screen.queryByText('NOTIFICATIONS')).toBeNull()
  })

  it('shows the eyebrow, caption and the inherit label by default', () => {
    render(<NotificationsCard auto={auto()} showToast={showToast} />)
    expect(screen.getByText('NOTIFICATIONS')).toBeTruthy()
    expect(screen.getByText('Changes apply immediately.')).toBeTruthy()
    expect(screen.getByText('Notify me')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Notify me' }).textContent).toBe('Use the app setting')
    expect(screen.queryByText(NEVER_CAPTION)).toBeNull()
  })

  it('opens four rows; the inherit row names the app value and carries the check', () => {
    storeMod.useStore.setState({ settings: { notifications: 'all' } as unknown as Settings })
    render(<NotificationsCard auto={auto()} showToast={showToast} />)
    fireEvent.click(screen.getByRole('button', { name: 'Notify me' }))
    expect(menuRows()).toEqual([
      'Use the app setting · After every execution',
      'Only when something needs attention',
      'After every execution',
      'Never for this automation',
    ])
    const rows = Array.from(document.querySelectorAll('.ad-menu-row'))
    expect(rows[0].classList.contains('active')).toBe(true)
    expect(rows[0].querySelector('.fa-check')).not.toBeNull()
    expect(rows.slice(1).every((r) => r.querySelector('.fa-check') === null)).toBe(true)
  })

  it('a global never: the inherit row names it', () => {
    storeMod.useStore.setState({ settings: { notifications: 'never' } as unknown as Settings })
    render(<NotificationsCard auto={auto()} showToast={showToast} />)
    fireEvent.click(screen.getByRole('button', { name: 'Notify me' }))
    expect(menuRows()[0]).toBe('Use the app setting · Never')
  })

  it('picking a value PATCHes it, holding the committed label', async () => {
    render(<NotificationsCard auto={auto()} showToast={showToast} />)
    fireEvent.click(screen.getByRole('button', { name: 'Notify me' }))
    fireEvent.click(screen.getByText('Never for this automation'))
    // The committed label holds through the round-trip.
    expect(screen.getByRole('button', { name: 'Notify me' }).textContent).toBe('Never for this automation')
    await waitFor(() => expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', { notifications: 'never' }))
  })

  it('picking inherit sends null to clear the override', async () => {
    render(<NotificationsCard auto={auto({ notifications: 'all' })} showToast={showToast} />)
    expect(screen.getByRole('button', { name: 'Notify me' }).textContent).toBe('After every execution')
    fireEvent.click(screen.getByRole('button', { name: 'Notify me' }))
    fireEvent.click(screen.getByText('Use the app setting'))
    await waitFor(() => expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', { notifications: null }))
  })

  it('a failed PATCH reverts the label and toasts the error', async () => {
    mockedApi.patchAutomation.mockRejectedValueOnce(new Error('backend said no'))
    render(<NotificationsCard auto={auto({ notifications: 'attention' })} showToast={showToast} />)
    fireEvent.click(screen.getByRole('button', { name: 'Notify me' }))
    fireEvent.click(screen.getByText('After every execution', { selector: '.ad-menu-row span span' }))
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('backend said no'))
    expect(screen.getByRole('button', { name: 'Notify me' }).textContent).toBe('Only when something needs attention')
  })

  it('the never value shows the quiet caption', () => {
    render(<NotificationsCard auto={auto({ notifications: 'never' })} showToast={showToast} />)
    expect(screen.getByText(NEVER_CAPTION)).toBeTruthy()
  })
})
