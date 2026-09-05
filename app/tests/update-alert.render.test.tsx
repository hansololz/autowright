// §3/§9 available-update state: the About nav row's update badge (exists only
// while main's check state holds a version) and the §9.4 Updates row seeding
// from / feeding back into that shared state. App renders for real (happy-dom)
// with the api module mocked to a connected, onboarded snapshot.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Settings } from '../src/types'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false, cliEnabled: false,
  dataPath: '/tmp', dataSize: '0 B',
}

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => true),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(async () => ({
      version: '0.2.0', automations: [], executions: [], agents: [], secrets: [],
      settings: SETTINGS, pendingDraft: null,
    })),
  },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default
let AboutPage: typeof import('../src/pages/AboutPage').default

// window.autowright bridge: cached seed + captured push listener, per test.
let cachedUpdate: string | null = null
let pushUpdate: ((v: string | null) => void) | null = null
const updateCheck = vi.fn()
const updateBrewManaged = vi.fn(async () => false)

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(cachedUpdate),
    onUpdateAvailable: (cb: (v: string | null) => void) => { pushUpdate = cb },
    updateCheck,
    updateBrewManaged,
    onUpdateProgress: () => {},
  }
  // This happy-dom/node combo exposes no working localStorage global; the
  // store's boot() reads the ad-onboarded flag from it, so stub a minimal one.
  const ls = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => ls.get(k) ?? null,
      setItem: (k: string, v: string) => { ls.set(k, String(v)) },
      removeItem: (k: string) => { ls.delete(k) },
    },
  })
  localStorage.setItem('ad-onboarded', '1')
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
  AboutPage = (await import('../src/pages/AboutPage')).default
})

beforeEach(() => {
  cachedUpdate = null
  pushUpdate = null
  updateCheck.mockReset()
  updateBrewManaged.mockReset()
  updateBrewManaged.mockResolvedValue(false)
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'automations', automations: [],
    executions: [], agents: [], secrets: [], settings: SETTINGS, updateAvailable: null,
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

describe('sidebar update row (§9)', () => {
  it('renders no row while no newer version is known', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('nav-rail')).toBeTruthy())
    expect(screen.queryByTestId('nav-update')).toBeNull()
  })

  it("seeds from main's cached state and navigates to About pre-armed", async () => {
    cachedUpdate = '9.9.9'
    render(<App />)
    const row = await screen.findByTestId('nav-update')
    expect(row.textContent).toContain('Update available')

    fireEvent.click(row)
    // §9.4: About mounts directly in the `available` state — no idle line.
    expect(await screen.findByText('Version 9.9.9 is available.')).toBeTruthy()
    expect(screen.getByText('Download update')).toBeTruthy()
  })

  it('appears live on a background-check push and disappears when it clears', async () => {
    render(<App />)
    await waitFor(() => expect(pushUpdate).not.toBeNull())
    pushUpdate!('9.9.9')
    expect(await screen.findByTestId('nav-update')).toBeTruthy()
    pushUpdate!(null)
    await waitFor(() => expect(screen.queryByTestId('nav-update')).toBeNull())
  })
})

describe('About updates row ↔ shared state (§9.4)', () => {
  it('a manual check finding a version sets the shared state', async () => {
    updateCheck.mockResolvedValueOnce({ state: 'available', version: '9.9.9' })
    render(<AboutPage />)
    fireEvent.click(screen.getByText('Check for updates'))
    await waitFor(() => expect(storeMod.useStore.getState().updateAvailable).toBe('9.9.9'))
    expect(screen.getByText('Download update')).toBeTruthy()
  })

  it('a manual uptodate result clears the shared state', async () => {
    updateCheck.mockResolvedValueOnce({ state: 'uptodate' })
    render(<AboutPage />)
    fireEvent.click(screen.getByText('Check for updates'))
    await waitFor(() => expect(screen.getByText("You're up to date.")).toBeTruthy())
    expect(storeMod.useStore.getState().updateAvailable).toBeNull()
  })

  it('an error result leaves the known update alone', async () => {
    storeMod.useStore.setState({ updateAvailable: '9.9.9' })
    updateCheck.mockResolvedValueOnce({ state: 'error' })
    render(<AboutPage />)
    // Seeded available — the button reads "Download update"; drive the manual
    // path from a fresh idle mount instead.
    expect(screen.getByText('Version 9.9.9 is available.')).toBeTruthy()
  })

  it('a bare error result shows the generic network line', async () => {
    updateCheck.mockResolvedValueOnce({ state: 'error' })
    render(<AboutPage />)
    fireEvent.click(screen.getByText('Check for updates'))
    await waitFor(() => expect(
      screen.getByText("Couldn't reach GitHub. Try again later."),
    ).toBeTruthy())
  })

  it('§3: a carried error detail is rendered instead of the network line', async () => {
    // A platform whose §2 module serves no update feed answers the plain
    // no-updates line — the user is never told to retry what cannot succeed.
    updateCheck.mockResolvedValueOnce({
      state: 'error', error: 'Updates are not supported on this platform yet.',
    })
    render(<AboutPage />)
    fireEvent.click(screen.getByText('Check for updates'))
    await waitFor(() => expect(
      screen.getByText('Updates are not supported on this platform yet.'),
    ).toBeTruthy())
    expect(screen.queryByText("Couldn't reach GitHub. Try again later.")).toBeNull()
  })

  it('a background check landing while the row sits idle flips it live', async () => {
    render(<AboutPage />)
    expect(screen.getByText('Check for updates')).toBeTruthy()
    storeMod.useStore.setState({ updateAvailable: '9.9.9' })
    expect(await screen.findByText('Version 9.9.9 is available.')).toBeTruthy()
    expect(screen.getByText('Download update')).toBeTruthy()
  })
})

// §9.4 Homebrew-managed fork: the `available` state swaps Download for the
// copyable brew upgrade command; everything else is untouched.
describe('About updates row, brew-managed copy (§9.4)', () => {
  it('available shows the brew command instead of Download; checking stays allowed', async () => {
    updateBrewManaged.mockResolvedValue(true)
    storeMod.useStore.setState({ updateAvailable: '9.9.9' })
    render(<AboutPage />)
    expect(await screen.findByText(
      'Version 9.9.9 is available. This copy is managed by Homebrew. Update it with:',
    )).toBeTruthy()
    expect(screen.getByText('brew upgrade --cask autowright')).toBeTruthy()
    expect(screen.queryByText('Download update')).toBeNull()
    const check = screen.getByText('Check for updates') as HTMLButtonElement
    expect(check.disabled).toBe(false)
  })

  it('Copy puts the brew command on the clipboard and toasts', async () => {
    updateBrewManaged.mockResolvedValue(true)
    storeMod.useStore.setState({ updateAvailable: '9.9.9' })
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<AboutPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith('brew upgrade --cask autowright')
    await waitFor(() => expect(storeMod.useStore.getState().toast).toContain('Copied'))
  })

  it('a manual check on a brew copy still arms the shared state, without Download', async () => {
    updateBrewManaged.mockResolvedValue(true)
    updateCheck.mockResolvedValueOnce({ state: 'available', version: '9.9.9' })
    render(<AboutPage />)
    fireEvent.click(screen.getByText('Check for updates'))
    await waitFor(() => expect(storeMod.useStore.getState().updateAvailable).toBe('9.9.9'))
    expect(await screen.findByText('brew upgrade --cask autowright')).toBeTruthy()
    expect(screen.queryByText('Download update')).toBeNull()
  })
})

describe('About LEGAL document rows (§9.4)', () => {
  // The row's View button is the one between the Terms title and the next row's title in DOM order.
  const viewButtonAfter = (title: string, nextTitle: string) =>
    screen.getAllByRole('button', { name: 'View' }).find((b) =>
      screen.getByText(title).compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING
      && !(screen.getByText(nextTitle).compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING))

  it('Privacy policy opens the doc modal on docs/PRIVACY.md', async () => {
    render(<AboutPage />)
    fireEvent.click(viewButtonAfter('Privacy policy', 'Terms of service')!)
    expect(await screen.findByRole('heading', { level: 2, name: 'Privacy policy' })).toBeTruthy()
    expect(await screen.findByText('Everything stays on your Mac')).toBeTruthy()
    expect(screen.queryByText("Couldn't load the document.")).toBeNull()
  })

  it('Terms of service row sits in LEGAL and opens the doc modal on docs/TERMS.md', async () => {
    render(<AboutPage />)
    expect(screen.getByText('No warranty, and your automations are your responsibility.')).toBeTruthy()
    fireEvent.click(viewButtonAfter('Terms of service', 'Open-source libraries')!)
    // Modal title, then the real TERMS.md body with its H1 stripped.
    expect(await screen.findByRole('heading', { level: 2, name: 'Terms of service' })).toBeTruthy()
    expect(await screen.findByText('No account, no service')).toBeTruthy()
    expect(screen.queryByText("Couldn't load the document.")).toBeNull()
  })
})
