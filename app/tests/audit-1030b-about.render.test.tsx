// §9.4 Updates row, 2026-10-30 second sweep: on macOS the install re-runs the
// live-execution gate once Squirrel has staged the bundle, so a `busy` answer
// can carry `staged: true`. The busy line then also says the staged update
// finishes when the app next quits; a plain busy answer keeps the old line.
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

describe('About updates row, busy answer after staging (§9.4)', () => {
  const BUSY = 'An automation is executing. The update installs when you restart after it finishes.'

  it('a busy answer with staged: true appends the staged line and keeps the button', async () => {
    updateInstall.mockResolvedValueOnce({ busy: true, staged: true })
    fireEvent.click(await downloaded())
    await waitFor(() => expect(screen.getByText(
      `${BUSY} The update is already staged and finishes when the app next quits.`,
    )).toBeTruthy())
    expect(screen.getByText('Restart to update')).toBeTruthy()
  })

  it('a plain busy answer keeps the busy line alone', async () => {
    updateInstall.mockResolvedValueOnce({ busy: true })
    fireEvent.click(await downloaded())
    await waitFor(() => expect(screen.getByText(BUSY)).toBeTruthy())
    expect(screen.queryByText(/already staged/)).toBeNull()
  })
})
