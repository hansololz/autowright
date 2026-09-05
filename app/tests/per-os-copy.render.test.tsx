// §9 per-OS copy rule: every user-facing string that names the platform reads
// its wording from src/platformCopy.ts, keyed by the store's `platformOs`
// token (§19 GET /health). 'windows' takes the Windows forms; every other
// token — including the '' boot default — keeps the macOS copy, so a mac
// render is unchanged. No component sniffs the platform itself.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Settings } from '../src/types'
import { platformCopy } from '../src/platformCopy'
import { useStore } from '../src/store'
import SettingsPage from '../src/pages/SettingsPage'
import SecretsPage from '../src/pages/SecretsPage'
import AutomationDetail from '../src/pages/AutomationDetail'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    patchSettings: vi.fn(() => Promise.resolve({})),
    setDataPath: vi.fn(() => Promise.resolve({})),
    deleteSecret: vi.fn(() => Promise.resolve({})),
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    detectAgents: vi.fn(async () => []),
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    checkHarness: vi.fn(async () => ({ status: 'ready' })),
    installHarness: vi.fn(async () => ({})),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: false })),
  },
}))

// §4.9 PATH command block, Windows form: a PowerShell one-liner that reads and
// rewrites the USER Path — never `setx` (it truncates at 1024 chars and bakes
// the expanded system PATH into the user value).
const WIN_PATH_CMD =
  `[Environment]::SetEnvironmentVariable('Path', "$env:LOCALAPPDATA\\Autowright\\bin;"`
  + ` + [Environment]::GetEnvironmentVariable('Path','User'), 'User')`

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: true, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false,
  cliEnabled: true, dataPath: 'C:\\Users\\me\\AppData\\Local\\Autowright', dataSize: '0 B',
}

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

const cliStatus = vi.fn(() => Promise.resolve(
  { state: 'installed' as const, path: 'C:\\Users\\me\\AppData\\Local\\Autowright\\bin\\autowright.cmd', onPath: false },
))

const setup = (platformOs: string) => {
  ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, revealPath: vi.fn() }
  useStore.setState({ platformOs, settings: { ...SETTINGS }, secrets: [] })
}

beforeEach(() => { useStore.setState({ platformOs: '' }); ls.clear() })
afterEach(cleanup)

describe('§9 per-OS copy — the substitution table', () => {
  it('the pure helper answers the per-OS forms — macOS for unknown tokens', () => {
    // '' is the boot default (before the first /health read) — macOS copy.
    for (const os of ['', 'macos', 'plan9']) {
      expect(platformCopy(os)).toMatchObject({
        machine: 'Mac', secretStore: 'Keychain', reveal: 'Show in Finder', fileManager: 'Finder',
      })
    }
    expect(platformCopy('windows')).toMatchObject({
      machine: 'PC', secretStore: 'Credential Manager',
      reveal: 'Show in Explorer', fileManager: 'Explorer',
    })
    expect(platformCopy('linux')).toMatchObject({
      machine: 'PC', secretStore: 'system keyring',
      reveal: 'Show in file manager', fileManager: 'file manager',
    })
    expect(platformCopy('windows').pathCommand).toBe(WIN_PATH_CMD)
    // §9: never `setx`.
    expect(platformCopy('windows').pathCommand).not.toContain('setx')
    // §9 Linux PATH command: appends to ~/.profile (desktop sessions and
    // login shells both source it), same install dir as macOS.
    expect(platformCopy('linux').pathCommand)
      .toBe('echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.profile')
    // §4.9 COMMAND LINE card: the §3 shim location and the terminal noun.
    for (const os of ['', 'macos', 'plan9']) {
      expect(platformCopy(os)).toMatchObject({
        cliBinDir: '~/.local/bin', terminalNoun: 'the Terminal',
      })
    }
    expect(platformCopy('windows')).toMatchObject({
      cliBinDir: '%LOCALAPPDATA%\\Autowright\\bin', terminalNoun: 'a terminal',
    })
    expect(platformCopy('linux')).toMatchObject({
      cliBinDir: '~/.local/bin', terminalNoun: 'a terminal',
    })
    // §9/§13 surface noun: "menu bar" on macOS and unknown tokens; Windows
    // and Linux both say "tray".
    for (const os of ['', 'macos', 'plan9']) {
      expect(platformCopy(os).menuBar).toBe('menu bar')
    }
    expect(platformCopy('windows').menuBar).toBe('tray')
    expect(platformCopy('linux').menuBar).toBe('tray')
  })
})

describe('§9 per-OS copy — Settings', () => {
  it('windows: Show in Explorer, ON THIS PC, and the PowerShell PATH command', async () => {
    setup('windows')
    render(<SettingsPage />)
    await screen.findByText('ON THIS PC')
    expect(screen.getAllByRole('button', { name: 'Show in Explorer' }).length).toBe(2)
    expect(screen.queryByText('Show in Finder')).toBeNull()
    expect(screen.getByText('Keep this PC awake')).toBeTruthy()
    expect(screen.getByText(/Your automations and preferences\. Small, and always on this PC\./)).toBeTruthy()
    // §4.9 PATH row — the Windows hint says to open a new terminal.
    await screen.findByText('Add it to your PATH')
    expect(screen.getByText(
      /add %LOCALAPPDATA%\\Autowright\\bin to your PATH, then open a new terminal:/,
    )).toBeTruthy()
    expect(screen.getByText(WIN_PATH_CMD)).toBeTruthy()
  })

  it('windows: the §13 surface is the tray', async () => {
    setup('windows')
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.getByText('Autowright starts quietly in the tray.')).toBeTruthy()
    expect(screen.getByText('Show in the tray')).toBeTruthy()
    expect(screen.queryByText(/menu bar/)).toBeNull()
  })

  it('macOS: the §13 surface stays the menu bar', async () => {
    setup('macos')
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.getByText('Autowright starts quietly in the menu bar.')).toBeTruthy()
    expect(screen.getByText('Show in the menu bar')).toBeTruthy()
  })

  it('windows: the Copy button puts the PowerShell one-liner on the clipboard', async () => {
    setup('windows')
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<SettingsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Copy' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(WIN_PATH_CMD))
  })

  // §4.9 COMMAND LINE card: the install-location and terminal-noun rows only
  // render while the CLI is missing, so these cases drive that state.
  const setupMissingCli = (platformOs: string, cliEnabled: boolean) => {
    ;(window as unknown as Record<string, unknown>).autowright = {
      cliStatus: vi.fn(() => Promise.resolve(
        { state: 'missing' as const, path: '', onPath: false },
      )),
      revealPath: vi.fn(),
    }
    useStore.setState({ platformOs, settings: { ...SETTINGS, cliEnabled }, secrets: [] })
  }

  it('windows: the COMMAND LINE card names the shim dir and a terminal', async () => {
    setupMissingCli('windows', false)
    render(<SettingsPage />)
    await screen.findByText(
      'Not installed. Manage automations from a terminal. Turning this on installs to '
      + '%LOCALAPPDATA%\\Autowright\\bin. No password needed.',
    )
    expect(screen.queryByText(/the Terminal/)).toBeNull()
    expect(screen.queryByText(/~\/\.local\/bin/)).toBeNull()

    cleanup()
    setupMissingCli('windows', true)  // enabled + missing → the reinstall warning row
    render(<SettingsPage />)
    await screen.findByText('Not installed. Manage automations from a terminal.')
    expect(screen.getByText(
      'autowright wasn’t found in %LOCALAPPDATA%\\Autowright\\bin. It may have been '
      + 'deleted or moved. Reinstall it to keep using it from a terminal.',
    )).toBeTruthy()
    expect(screen.queryByText(/the Terminal/)).toBeNull()
  })

  it('macOS: the COMMAND LINE card keeps ~/.local/bin and the Terminal', async () => {
    setupMissingCli('', false)
    render(<SettingsPage />)
    await screen.findByText(
      'Not installed. Manage automations from the Terminal. Turning this on installs to '
      + '~/.local/bin. No password needed.',
    )

    cleanup()
    setupMissingCli('macos', true)
    render(<SettingsPage />)
    await screen.findByText('Not installed. Manage automations from the Terminal.')
    expect(screen.getByText(
      'autowright wasn’t found in ~/.local/bin. It may have been deleted or moved. '
      + 'Reinstall it to keep using it from the Terminal.',
    )).toBeTruthy()
  })

  it('the boot default keeps the macOS copy — nothing changes before /health', async () => {
    setup('')
    render(<SettingsPage />)
    await screen.findByText('ON THIS MAC')
    expect(screen.getAllByRole('button', { name: 'Show in Finder' }).length).toBe(2)
    expect(screen.getByText('Keep this Mac awake')).toBeTruthy()
    await screen.findByText('echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zprofile && source ~/.zprofile')
  })
})

describe('§9 per-OS copy — Secrets', () => {
  it('windows: the secret store is the Credential Manager', async () => {
    setup('windows')
    render(<SecretsPage />)
    await screen.findByText(
      /Stored in your PC’s Credential Manager\. Scripts read them at execution time/,
    )
  })

  it('macOS: the secret store stays the Keychain', async () => {
    setup('')
    render(<SecretsPage />)
    await screen.findByText(
      /Stored in your Mac’s Keychain\. Scripts read them at execution time/,
    )
  })
})

// §9/§13: the shared "Execute now and the menu bar still work" copy on the
// §9.2 automation detail page — the §13 surface is the tray on Windows.
const AUTO: Automation = {
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, instructions: '', notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
}

describe('§9 per-OS copy — automation detail trigger status', () => {
  const seed = (platformOs: string) => {
    ;(window as unknown as Record<string, unknown>).autowright = { revealPath: vi.fn() }
    useStore.setState({
      platformOs, page: 'automation', automationId: 'a1', automations: [AUTO],
      executions: [], toast: null,
    })
  }

  it('windows: the no-triggers line names the tray', async () => {
    seed('windows')
    render(<AutomationDetail />)
    await screen.findByText(
      'No triggers set — executes only when you press Execute now or use the tray.',
    )
    expect(screen.queryByText(/menu bar/)).toBeNull()
  })

  it('macOS: the no-triggers line stays the menu bar', async () => {
    seed('macos')
    render(<AutomationDetail />)
    await screen.findByText(
      'No triggers set — executes only when you press Execute now or use the menu bar.',
    )
  })
})

describe('§9 per-OS copy — the §1 onboarding promise', () => {
  let Onboarding: typeof import('../src/pages/Onboarding').default

  beforeAll(async () => {
    ;(window as unknown as Record<string, unknown>).autowright = {
      onOpenTarget: () => {}, trayAlert: () => Promise.resolve(),
    }
    Onboarding = (await import('../src/pages/Onboarding')).default
  })

  const promise = async (platformOs: string) => {
    useStore.setState({
      platformOs, connected: true, surface: 'onboard', agents: [], automations: [],
      harnessInstall: {}, ollamaPull: null, toast: null,
    })
    vi.useFakeTimers()
    render(<Onboarding />)
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  }

  afterEach(() => { vi.useRealTimers() })

  it('windows: the promise names the PC and the Credential Manager', async () => {
    await promise('windows')
    expect(screen.getByText('Your automations execute only on this PC')).toBeTruthy()
    expect(screen.getByText('Secrets live in your Credential Manager')).toBeTruthy()
  })

  it('macOS: the promise is unchanged', async () => {
    await promise('macos')
    expect(screen.getByText('Your automations execute only on this Mac')).toBeTruthy()
    expect(screen.getByText('Secrets live in your Keychain')).toBeTruthy()
  })
})
