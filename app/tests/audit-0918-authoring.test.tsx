// Audit fixes on the authoring surfaces: the §4.9 COMMAND LINE toggle held
// disabled while an install is in flight (a second click must never read the
// busy short-circuit as a failure and patch the setting off), and the §9.2
// trigger editor's Discord mention box defaulting checked when an existing
// trigger of another kind is switched to Discord. Both render for real
// (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Settings, Trigger } from '../src/types'
import { useStore } from '../src/store'
import SettingsPage from '../src/pages/SettingsPage'
import { TriggerEditor } from '../src/pages/detail/TriggerEditor'
import { api } from '../src/api'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    patchSettings: vi.fn(async () => ({})),
    setDataPath: vi.fn(async () => ({})),
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'granted' })),
    triggersPreview: vi.fn(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map(() => ({ valid: true, label: 'Every day', short: 'Every day', nextAtMs: null })),
    })),
  },
}))
const patchSettings = vi.mocked(api.patchSettings)

// This happy-dom/node combo exposes no working localStorage global; the §3
// first-run marker a settled card install sets lives there, so stub one.
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

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false,
  cliEnabled: false, dataPath: '/tmp', dataSize: '0 B',
}

const USER = '/Users/me/.local/bin/autowright'
const cliStatus = vi.fn<() => Promise<{ state: string; path: string; onPath: boolean }>>()
const cliInstall = vi.fn<() => Promise<{ ok: boolean }>>()

beforeEach(() => {
  vi.clearAllMocks()
  patchSettings.mockResolvedValue({} as never)
  localStorage.clear()
  ;(window as unknown as Record<string, unknown>).autowright = {
    cliStatus, cliInstall, cliUninstall: vi.fn(async () => ({ ok: true })),
    onOpenTarget: () => {}, trayAlert: () => Promise.resolve(),
  }
})
afterEach(() => cleanup())

describe('§4.9 COMMAND LINE toggle under an in-flight install', () => {
  it('two more clicks while the install runs patch cliEnabled once, never off', async () => {
    cliStatus.mockResolvedValue({ state: 'missing', path: USER, onPath: true })
    let landInstall: (v: { ok: boolean }) => void = () => {}
    cliInstall.mockImplementation(() => new Promise((resolve) => { landInstall = resolve }))
    useStore.setState({ settings: { ...SETTINGS, cliEnabled: false }, toast: null })
    render(<SettingsPage />)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    const toggle = () => card.querySelector('[role="switch"]') as HTMLButtonElement

    fireEvent.click(toggle())
    await waitFor(() => expect(cliInstall).toHaveBeenCalledTimes(1))
    // §4.9: the toggle is disabled while the install is in flight
    await waitFor(() => expect(toggle().disabled).toBe(true))
    fireEvent.click(toggle())
    fireEvent.click(toggle())

    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    await act(async () => { landInstall({ ok: true }) })
    await waitFor(() => expect(toggle().disabled).toBe(false))
    // one install, one PATCH — the clicks that started nothing patched nothing
    expect(cliInstall).toHaveBeenCalledTimes(1)
    expect(patchSettings.mock.calls).toEqual([[{ cliEnabled: true }]])
  })
})

describe('§9.2 Discord mention default on a kind switch', () => {
  const capabilities = () => useStore.setState({
    secrets: [],
    platformCapabilities: {
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
    },
  })
  const editor = (initial: Trigger) => render(
    <TriggerEditor hasAppStart={false} initial={initial} onSave={() => {}} onCancel={() => {}} />,
  )
  const mentionBox = () => screen.getByLabelText('Only when the bot is mentioned') as HTMLInputElement

  it('a cron trigger switched to Discord starts with the box checked', () => {
    capabilities()
    editor({
      id: 't1', kind: 'cron', expression: '0 8 * * *', source: 'user',
      enabled: true, label: 'Every day', short: 'Every day',
    } as unknown as Trigger)
    fireEvent.click(screen.getByText('Discord'))
    expect(mentionBox().checked).toBe(true)
  })

  it('a stored Discord trigger still seeds the box from its own value', () => {
    capabilities()
    editor({
      id: 't1', kind: 'discord', channel: '123', secret: 'sec-1',
      enabled: true, label: 'On Discord message', short: 'Discord',
    } as unknown as Trigger)
    // the stored trigger carries no `mention` — unticked, not the new-trigger default
    expect(mentionBox().checked).toBe(false)
  })
})
