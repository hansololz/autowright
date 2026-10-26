// §9 shell boot: App's first render is always the boot splash (`connected`
// null), and the shell mounts only once the store flips `connected` true. The
// shell's own hooks must therefore be declared ahead of the splash/onboarding
// early-returns — a hook placed after them runs for the first time on the
// shell render, React sees more hooks than the splash render had, and the
// shell crashes into its error boundary on every boot (React #310; the
// 2026-10-25 regression every e2e scenario failed on). App renders for real
// (happy-dom) with the api module mocked so boot() never resolves on its own
// and the test drives the splash → shell transition through the store.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { Settings } from '../src/types'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false, cliEnabled: false,
  dataPath: '/tmp', dataSize: '0 B',
}

vi.mock('../src/api', () => ({
  // Never settles: boot() stays parked on discovery, so the only thing that
  // can flip `connected` is this test.
  connectInfo: vi.fn(() => new Promise<boolean>(() => {})),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(async () => ({
      version: '0.13.2', automations: [], executions: [], agents: [], secrets: [],
      settings: SETTINGS, pendingDraft: null,
    })),
  },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(null),
    onUpdateAvailable: () => {},
    updateCheck: () => Promise.resolve({ state: 'uptodate' }),
    updateBrewManaged: () => Promise.resolve(false),
    onUpdateProgress: () => {},
  }
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
  localStorage.setItem('ad-last-seen-version', '0.13.2')
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
})

beforeEach(() => {
  storeMod.useStore.setState({
    connected: null, surface: 'app', page: 'automations', automationId: null, executionId: null,
    automations: [], executions: [], agents: [], secrets: [], settings: SETTINGS,
    updateAvailable: null, whatsNewOpen: false, version: '0.13.2',
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

describe('App boot splash → shell transition (§9)', () => {
  it('mounts the shell after the splash without a hooks-order crash', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(<App />)
      // First render: the boot splash, not the shell.
      expect(await screen.findByText('Connecting…')).toBeTruthy()
      expect(screen.queryByRole('heading', { name: 'Automations' })).toBeNull()
      // The backend answers: the same component instance re-renders as the shell.
      act(() => { storeMod.useStore.setState({ connected: true }) })
      expect(await screen.findByRole('heading', { name: 'Automations' })).toBeTruthy()
      expect(screen.queryByText('Something went wrong on this page')).toBeNull()
      expect(consoleError).not.toHaveBeenCalled()
    } finally {
      consoleError.mockRestore()
    }
  })
})
