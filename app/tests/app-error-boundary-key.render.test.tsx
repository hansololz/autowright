// §9 render-failure containment: the shell keys its page ErrorBoundary by the
// page kind and the record it shows, so moving between two records of the
// same page kind (one execution to another) clears a latched error instead of
// leaving the second record behind the first one's failure notice. App renders
// for real (happy-dom) with the api module mocked to a connected, onboarded
// snapshot and the execution page stubbed to throw for one record only.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
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
      version: '0.8.2', automations: [], executions: [], agents: [], secrets: [],
      settings: SETTINGS, pendingDraft: null,
    })),
  },
}))

vi.mock('../src/pages/ExecutionPage', async () => {
  const { useStore } = await import('../src/store')
  return {
    default: function ExecutionPage() {
      const executionId = useStore((s) => s.executionId)
      if (executionId === 'broken') throw new Error('execution page blew up')
      return <div>execution {executionId}</div>
    },
  }
})

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
  localStorage.setItem('ad-last-seen-version', '0.8.2')
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
})

beforeEach(() => {
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'execution', automationId: null, executionId: 'broken',
    automations: [], executions: [], agents: [], secrets: [], settings: SETTINGS,
    updateAvailable: null, whatsNewOpen: false, version: '0.8.2',
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

describe('App page ErrorBoundary key (§9)', () => {
  it('moving to another record of the same page kind clears a latched error', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(<App />)
      expect(await screen.findByText('Something went wrong on this page')).toBeTruthy()
      // Same page kind, different record: a fresh boundary renders the page.
      act(() => { storeMod.useStore.setState({ executionId: 'healthy' }) })
      expect(await screen.findByText('execution healthy')).toBeTruthy()
      expect(screen.queryByText('Something went wrong on this page')).toBeNull()
    } finally {
      consoleError.mockRestore()
    }
  })
})
