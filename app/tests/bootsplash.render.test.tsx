// §3/§9 boot gate: while the backend is not answering, the splash polls the
// main process for the ensure-backend outcome every 2 s. The bridge can throw
// (no handler registered yet, a main process on its way down) and a rejection
// there would repeat forever, unhandled — the poll swallows it and keeps the
// line it has. App renders for real (happy-dom) with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

vi.mock('../src/api', () => ({
  // §3: discovery never answers, so the shell stays on the boot splash.
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { state: vi.fn(() => Promise.reject(new Error('offline'))) },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default

const backendStatus = vi.fn<() => Promise<{ state: string; detail: string }>>()
// §3 quit-entirely on the splash: main's `quit-requested` push and the
// quit-all IPC the shared flow answers it with.
let quitRequested: (() => void) | null = null
const quitAll = vi.fn<(force?: boolean) => Promise<unknown>>()

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    backendStatus,
    quitAll,
    onQuitRequested: (cb: () => void) => { quitRequested = cb; return () => {} },
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
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
})

beforeEach(() => {
  backendStatus.mockReset()
  quitAll.mockReset()
  quitRequested = null
  storeMod.useStore.setState({ connected: false, surface: 'app', quitStage: null })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

describe('§9 boot splash backend-status poll', () => {
  it('a throwing bridge keeps the splash and raises no unhandled rejection', async () => {
    backendStatus.mockRejectedValue(new Error('no handler for backend-status'))
    render(<App />)
    expect(await screen.findByText('Waiting for the Autowright backend…')).toBeTruthy()
    // Two turns of the 2 s poll: every one of them rejects.
    await act(async () => { await new Promise((r) => setTimeout(r, 4100)) })
    expect(backendStatus.mock.calls.length).toBeGreaterThanOrEqual(2)
    // §3: nothing to report, so the line the splash already has stands.
    expect(screen.getByText('Waiting for the Autowright backend…')).toBeTruthy()
  }, 10_000)

  it('a failed ensure-backend replaces the stuck hint with its own detail', async () => {
    backendStatus.mockResolvedValue({ state: 'failed', detail: 'the backend could not be installed' })
    render(<App />)
    await screen.findByText('Waiting for the Autowright backend…')
    await waitFor(() => expect(screen.getByText('the backend could not be installed')).toBeTruthy(),
      { timeout: 5000 })
  }, 10_000)
})

describe('§3 quit-entirely on the boot splash', () => {
  // The splash is a loaded main window too: a Cmd+Q while ensure-backend is
  // still installing shows the quit overlay rather than running natively.
  for (const connected of [false, null] as const) {
    it(`a quit-requested push mounts the quit overlay (connected ${String(connected)})`, async () => {
      backendStatus.mockResolvedValue({ state: 'installing', detail: '' })
      quitAll.mockReturnValue(new Promise(() => {})) // in flight — the app is exiting
      render(<App />)
      // boot() may flip connected to false on its own; pin the branch under test.
      act(() => { storeMod.useStore.setState({ connected }) })
      await waitFor(() => expect(quitRequested).toBeTypeOf('function'))
      act(() => { quitRequested!() })
      expect(await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })).toBeTruthy()
      expect(quitAll).toHaveBeenCalledWith(false)
    })
  }
})
