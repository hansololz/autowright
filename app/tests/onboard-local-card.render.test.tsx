// Component tests for §10 step 2's Free local AI card and the two exits from
// the connect step: the local install queue failing and resuming through Try
// again, the two not-ready reasons the readiness check reports (server down /
// model missing) with the Qwen3 8B recovery download, Skip for now committing
// every connected card, and the prior-data path that skips step 2 entirely.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { Agent } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    detectAgents: vi.fn(async () => []),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    ollamaPull: vi.fn(async () => ({})),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    installHarness: vi.fn(async () => ({})),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    listAgents: vi.fn(async () => []),
    addAgent: vi.fn(async () => ({ id: 'ag1' })),
    patchAgent: vi.fn(async () => ({})),
    patchAutomation: vi.fn(async () => ({})),
  },
}))

// This happy-dom/node combo exposes no working localStorage global; leaving
// onboarding writes the ad-onboarded marker there, so stub a minimal one.
const ls = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k: string) => ls.get(k) ?? null,
    setItem: (k: string, v: string) => { ls.set(k, String(v)) },
    removeItem: (k: string) => { ls.delete(k) },
    clear: () => { ls.clear() },
  },
})

let storeMod: typeof import('../src/store')
let Onboarding: typeof import('../src/pages/Onboarding').default
let mockedApi: typeof import('../src/api').api

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  Onboarding = (await import('../src/pages/Onboarding')).default
  mockedApi = (await import('../src/api')).api
})

const det = (over: Partial<{ id: string; name: string; installed: boolean; signedIn: boolean | null }>) => ({
  id: 'claude', name: 'Claude Code', installed: false, signedIn: null, detail: 'not found', ...over,
})

const setup = (provs: ReturnType<typeof det>[], agents: Agent[] = []) => {
  ;(mockedApi.detectAgents as ReturnType<typeof vi.fn>).mockResolvedValue(provs)
  storeMod.useStore.setState({
    connected: true, surface: 'onboard', agents, automations: [],
    harnessInstall: {}, ollamaPull: null, toast: null,
    platformCapabilities: {
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
    },
  })
}

/** Step 1 runs on timers, then step 2's detection pads the spinner to 1.9 s. */
async function toStep2() {
  render(<Onboarding />)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  fireEvent.click(screen.getByText('Connect your AI →'))
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
}

beforeEach(() => {
  vi.clearAllMocks()
  // vi.clearAllMocks clears calls, not implementations: a prior test's
  // persistent mock would otherwise leak into the next one.
  ;(mockedApi.ollamaStatus as ReturnType<typeof vi.fn>).mockImplementation(
    async () => ({ ready: false, installed: false, models: [] }))
  ;(mockedApi.checkHarness as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ status: 'ready' }))
  ;(mockedApi.listAgents as ReturnType<typeof vi.fn>).mockImplementation(async () => [])
  vi.useFakeTimers()
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('§10 Free local AI card: install queue and not-ready reasons', () => {
  it('a failed piece install shows the reason and Try again restarts the queue', async () => {
    setup([det({ id: 'opencode', name: 'OpenCode' })])
    await toStep2()
    // nothing local is present, so the card offers the whole download
    fireEvent.click(screen.getByText('Download and install · 5.2 GB'))
    expect(mockedApi.installHarness).toHaveBeenCalledWith('opencode')

    // §19 harness.install stream: the piece finished, and it failed
    act(() => {
      storeMod.useStore.setState({
        harnessInstall: { opencode: { done: true, ok: false, error: 'brew exploded' } },
      })
    })
    expect(screen.getByText('Install failed — brew exploded')).toBeTruthy()

    // §10: Try again resumes on the still-missing pieces, starting over at
    // the one that failed
    fireEvent.click(screen.getByText('Try again'))
    expect((mockedApi.installHarness as ReturnType<typeof vi.fn>).mock.calls).toEqual([['opencode'], ['opencode']])
  })

  it('a local check that fails names the local server when Ollama stopped answering', async () => {
    // detection sees every piece, so the card goes straight to the check
    let statusCalls = 0
    ;(mockedApi.ollamaStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      statusCalls += 1
      return statusCalls === 1
        ? { ready: true, installed: true, models: ['qwen3:8b'] }
        : { ready: false, installed: true, models: [] }
    })
    ;(mockedApi.checkHarness as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'error' })
    setup([det({ id: 'opencode', name: 'OpenCode', installed: true })])
    await toStep2()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('Not ready — the local server isn’t answering.')).toBeTruthy()
  })

  it('a local check that fails names the missing model, and Qwen3 8B downloads in its place', async () => {
    let statusCalls = 0
    ;(mockedApi.ollamaStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      statusCalls += 1
      return statusCalls === 1
        ? { ready: true, installed: true, models: ['llama3.2:3b'] }
        : { ready: true, installed: true, models: [] }
    })
    ;(mockedApi.checkHarness as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'error' })
    setup([det({ id: 'opencode', name: 'OpenCode', installed: true })])
    await toStep2()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('Not ready — llama3.2:3b isn’t installed yet.')).toBeTruthy()
    // §10 recovery: the found model is discarded and qwen3:8b downloads instead
    fireEvent.click(screen.getByText('Download Qwen3 8B · 5.2 GB'))
    expect(mockedApi.ollamaPull).toHaveBeenCalledWith('qwen3:8b')
  })
})

describe('§10 step-2 exits: Skip for now and prior data', () => {
  it('Skip for now commits every connected card and makes the first one the default', async () => {
    let added = 0
    ;(mockedApi.addAgent as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      added += 1
      return { id: `ag${added}` }
    })
    setup([
      det({ id: 'claude', name: 'Claude Code', installed: true, signedIn: true }),
      det({ id: 'codex', name: 'Codex', installed: true, signedIn: true }),
    ])
    await toStep2()
    // both readiness checks pad to 900 ms before the cards read connected
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getAllByText('Use as default →')).toHaveLength(2)

    fireEvent.click(screen.getByText('Skip for now'))
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    // §10 commit shape: a harness card becomes a default-mode agent with a
    // null name, so its display falls back to the harness (§4.7)
    expect((mockedApi.addAgent as ReturnType<typeof vi.fn>).mock.calls).toEqual([
      [{ name: null, harness: 'Claude Code', mode: 'default', model: null }],
      [{ name: null, harness: 'Codex', mode: 'default', model: null }],
    ])
    expect(mockedApi.patchAgent).toHaveBeenCalledWith('ag1', { default: true })
  })

  it('an agent already connected turns step 1 into the only screen', async () => {
    setup([], [{ id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null, default: true }])
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    // §10: prior data reports itself as a chip and the counter disappears
    expect(screen.getByText('Agent found')).toBeTruthy()
    expect(screen.queryByText('Step 1 of 2')).toBeNull()
    fireEvent.click(screen.getByText('Continue →'))
    expect(storeMod.useStore.getState().surface).toBe('app')
    expect(mockedApi.detectAgents).not.toHaveBeenCalled()
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
  })
})
