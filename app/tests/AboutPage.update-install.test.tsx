// §9.4 Updates row, install half: `update-install` can answer `{ busy }` (an
// automation is executing) or `{ error }` — the §3 updater refused to quit
// (nothing staged, a stale download, a failed installer spawn). The error
// renders the same "Update failed: <error>" sub-line as a download error and
// reverts the button, so the card is never stuck on "Restart to update".
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
let AboutPage: typeof import('../src/pages/AboutPage').default

const updateDownload = vi.fn()
const updateInstall = vi.fn()

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(null),
    onUpdateAvailable: () => {},
    updateCheck: vi.fn(),
    updateBrewManaged: vi.fn(async () => false),
    onUpdateProgress: () => {},
    updateDownload,
    updateInstall,
  }
  // Same happy-dom gap as the other renderer tests: boot() reads the
  // ad-onboarded flag off a localStorage this combo doesn't provide.
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
  AboutPage = (await import('../src/pages/AboutPage')).default
})

beforeEach(() => {
  updateDownload.mockReset()
  updateDownload.mockResolvedValue({ ok: true })
  updateInstall.mockReset()
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'about', automations: [],
    executions: [], agents: [], secrets: [], settings: SETTINGS, updateAvailable: '9.9.9',
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

// Walk the row to the downloaded state, where the install button lives.
async function downloaded() {
  render(<AboutPage />)
  fireEvent.click(await screen.findByText('Download update'))
  return screen.findByText('Restart to update')
}

describe('About updates row, install answers (§9.4)', () => {
  it('a refused install renders the failed sub-line and reverts the button', async () => {
    updateInstall.mockResolvedValueOnce({ error: 'spawn ENOENT' })
    fireEvent.click(await downloaded())
    expect(await screen.findByText('Update failed: spawn ENOENT')).toBeTruthy()
    // Never stuck on "Restart to update" — the flow starts over from a check.
    expect(screen.queryByText('Restart to update')).toBeNull()
    expect(screen.getByText('Check for updates')).toBeTruthy()
  })

  it('a busy answer keeps the button and explains the wait', async () => {
    updateInstall.mockResolvedValueOnce({ busy: true })
    fireEvent.click(await downloaded())
    await waitFor(() => expect(screen.getByText(
      'An automation is executing. The update installs when you restart after it finishes.',
    )).toBeTruthy())
    expect(screen.getByText('Restart to update')).toBeTruthy()
  })

  it('an install that quits leaves the card alone', async () => {
    updateInstall.mockResolvedValueOnce({ ok: true })
    fireEvent.click(await downloaded())
    await waitFor(() => expect(updateInstall).toHaveBeenCalled())
    expect(screen.getByText(
      'Update downloaded. Only the app restarts, not your automations.',
    )).toBeTruthy()
    expect(screen.getByText('Restart to update')).toBeTruthy()
  })
})
