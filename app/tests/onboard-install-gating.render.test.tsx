// §10/§2 platform gating on the step-2 cards: with `capabilities.agentInstall`
// false (§19 /health) the Install and Sign in actions are hidden and the card
// carries the one plain manual-install line instead, the provider's name
// linking to its vendor install page. Detection still runs and still reports.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    detectAgents: vi.fn(async () => []),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    installHarness: vi.fn(async () => ({})),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: false })),
  },
}))

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

const setup = (agentInstall: boolean, provs: ReturnType<typeof det>[]) => {
  ;(mockedApi.detectAgents as ReturnType<typeof vi.fn>).mockResolvedValue(provs)
  storeMod.useStore.setState({
    connected: true, surface: 'onboard', agents: [], automations: [],
    harnessInstall: {}, ollamaPull: null, toast: null,
    platformCapabilities: {
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall,
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

beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

// §10 sign-in help: the card polls the §19 sign-in status every 2 s while it
// waits — one interval per provider, never one per attempt.
describe('§10 sign-in poll', () => {
  it('a second sign-in attempt replaces the first poll instead of stacking one', async () => {
    setup(true, [det({ id: 'gemini', name: 'Gemini CLI', installed: true, signedIn: false })])
    await toStep2()

    fireEvent.click(screen.getByText('Sign in'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('Waiting for you to sign in…')).toBeTruthy()
    // back to idle, then sign in again — the first poll must not survive
    fireEvent.click(screen.getByText('Cancel'))
    fireEvent.click(screen.getByText('Sign in'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    ;(mockedApi.signinStatus as ReturnType<typeof vi.fn>).mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(mockedApi.signinStatus).toHaveBeenCalledTimes(1)
  })

  it('leaving the sign-in phase stops the poll for good', async () => {
    setup(true, [det({ id: 'gemini', name: 'Gemini CLI', installed: true, signedIn: false })])
    await toStep2()

    fireEvent.click(screen.getByText('Sign in'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    fireEvent.click(screen.getByText('Cancel'))
    ;(mockedApi.signinStatus as ReturnType<typeof vi.fn>).mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(mockedApi.signinStatus).not.toHaveBeenCalled()
  })
})

describe('§10 step-2 cards — agentInstall gating', () => {
  it('agentInstall false: the suggestion card shows the manual line, no setup action', async () => {
    setup(false, [det({ id: 'codex', name: 'Codex' })])
    await toStep2()
    // Detection still reported — the card is here, only its action is gone.
    expect(screen.getAllByText('Codex').length).toBeGreaterThan(0)
    expect(screen.queryByText('Set up Codex')).toBeNull()
    const lines = screen.getAllByTestId('manual-install-line')
    expect(lines.map((l) => l.textContent)).toContain(
      'Install Codex by hand, then come back — Autowright detects it automatically.')
    const link = lines[0].querySelector('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://developers.openai.com/codex/cli')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(mockedApi.installHarness).not.toHaveBeenCalled()
  })

  it('agentInstall false: a signed-out found card shows the line instead of Sign in', async () => {
    setup(false, [det({ id: 'gemini', name: 'Gemini CLI', installed: true, signedIn: false })])
    await toStep2()
    expect(screen.queryByText('Sign in')).toBeNull()
    const line = screen.getAllByTestId('manual-install-line')[0]
    // Installed but signed out → the sign-in wording, not the install one (§9).
    expect(line.textContent).toBe(
      'Sign in to Gemini CLI from a terminal, then come back — Autowright detects it automatically.')
    expect((line.querySelector('a') as HTMLAnchorElement).getAttribute('href'))
      .toBe('https://github.com/google-gemini/gemini-cli')
    expect(mockedApi.loginHarness).not.toHaveBeenCalled()
  })

  it('agentInstall true: the same cards keep their Install and Sign in actions', async () => {
    setup(true, [
      det({ id: 'codex', name: 'Codex' }),
      det({ id: 'gemini', name: 'Gemini CLI', installed: true, signedIn: false }),
    ])
    await toStep2()
    expect(screen.getByText('Sign in')).toBeTruthy()
    // A found card collapses the suggestions behind the §10 disclosure.
    fireEvent.click(screen.getByText('OR TRY SOMETHING NEW'))
    expect(screen.getByText('Set up Codex')).toBeTruthy()
    expect(screen.queryAllByTestId('manual-install-line')).toHaveLength(0)
  })
})
