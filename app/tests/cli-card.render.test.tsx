// §4.9 COMMAND LINE card: a Toggle bound to the stored cliEnabled setting;
// disk state from the §3 cli-status preload IPC. Turning on patches the
// setting and installs (silent, ~/.local/bin); a failed install patches it
// back. Turning off also deletes the command: with an ours shim on disk a
// danger confirm asks first (confirming patches off + fires cli-uninstall;
// a failed delete comes back as a toasted error message); with
// nothing installed the flip just patches false. No standalone Delete row.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Settings } from '../src/types'
import { useStore } from '../src/store'
import SettingsPage from '../src/pages/SettingsPage'
import { api } from '../src/api'

vi.mock('../src/api', () => ({
  api: { patchSettings: vi.fn(() => Promise.resolve({})), setDataPath: vi.fn(() => Promise.resolve({})) },
}))
const patchSettings = vi.mocked(api.patchSettings)

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false,
  cliEnabled: false, dataPath: '/tmp', dataSize: '0 B',
}

// This happy-dom/node combo exposes no working localStorage global; the §3
// first-run marker set by settled card installs lives there, so stub a minimal one.
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

type Cli = { state: 'installed' | 'missing' | 'foreign'; path: string; onPath: boolean }

const USER = '/Users/me/.local/bin/autowright'

const cliStatus = vi.fn<() => Promise<Cli>>()
const cliInstall = vi.fn<() => Promise<{ ok: boolean }>>()
const cliUninstall = vi.fn<() => Promise<{ ok: true } | { ok: false; hint: string }>>()

function setup(cliEnabled: boolean) {
  ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, cliInstall, cliUninstall }
  useStore.setState({ settings: { ...SETTINGS, cliEnabled } })
}

beforeEach(() => {
  cliStatus.mockReset()
  cliInstall.mockReset()
  cliUninstall.mockReset()
  patchSettings.mockClear()
  localStorage.clear()
})

afterEach(cleanup)

describe('COMMAND LINE card (§4.9)', () => {
  it('on + installed: path shown, toggle on, no Install/Delete buttons', async () => {
    setup(true)
    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    render(<SettingsPage />)
    await screen.findByText('COMMAND LINE')
    await screen.findByText(`Installed at ${USER}`)
    expect(screen.queryByRole('button', { name: /Install|Delete/ })).toBeNull()
  })

  it('on + installed: PATH row with the copyable command block, regardless of onPath', async () => {
    setup(true)
    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<SettingsPage />)
    // §4.9 PATH row: own title + description below the toggle row.
    await screen.findByText('Add it to your PATH')
    await screen.findByText(/If your Terminal can’t find autowright, add ~\/\.local\/bin to your PATH:/)
    await screen.findByText('echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zprofile && source ~/.zprofile')
    fireEvent.click(await screen.findByRole('button', { name: 'Copy' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zprofile && source ~/.zprofile'))
    await waitFor(() => expect(useStore.getState().toast).toContain('Copied'))
  })

  it('off + missing: turning the toggle on patches cliEnabled and installs silently', async () => {
    setup(false)
    cliStatus.mockResolvedValueOnce({ state: 'missing', path: USER, onPath: true })
    cliInstall.mockResolvedValue({ ok: true })
    cliStatus.mockResolvedValueOnce({ state: 'installed', path: USER, onPath: true })
    render(<SettingsPage />)
    await screen.findByText(/Turning this on installs to ~\/\.local\/bin\. No password needed\./)
    // No PATH row while off — it belongs to on+installed only.
    expect(screen.queryByText('Add it to your PATH')).toBeNull()
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ cliEnabled: true }))
    await waitFor(() => expect(cliInstall).toHaveBeenCalledTimes(1))
    // §3: a settled card install also settles the first-run one-shot.
    await waitFor(() => expect(localStorage.getItem('ad-cli-installed')).toBe('1'))
    // The real app flips the store via the settings.changed WS refresh —
    // simulate it, then the card shows the enabled+installed copy.
    useStore.setState({ settings: { ...SETTINGS, cliEnabled: true } })
    await screen.findByText(`Installed at ${USER}`)
  })

  it('failed install patches the setting back to false — no error banner', async () => {
    setup(false)
    cliStatus.mockResolvedValue({ state: 'missing', path: USER, onPath: true })
    cliInstall.mockResolvedValue({ ok: false })
    render(<SettingsPage />)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    await waitFor(() => expect(cliInstall).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ cliEnabled: false }))
    // §3: a failed install never settles the first-run marker.
    expect(localStorage.getItem('ad-cli-installed')).toBeNull()
  })

  it('on + installed: turning off opens the danger confirm; Cancel patches nothing, deletes nothing', async () => {
    setup(true)
    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    render(<SettingsPage />)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    // §4.9 disable confirm: own title, delete consequence, danger label.
    await screen.findByText('Turn off the autowright command?')
    await screen.findByText(/This also deletes the command file from this Mac\. Your automations, settings, and executions aren’t affected\./)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByText('Turn off the autowright command?')).toBeNull())
    expect(patchSettings).not.toHaveBeenCalled()
    expect(cliUninstall).not.toHaveBeenCalled()
  })

  it('on + installed: confirming turns off AND deletes — patch false + cli-uninstall', async () => {
    setup(true)
    cliStatus.mockResolvedValueOnce({ state: 'installed', path: USER, onPath: true })
    cliUninstall.mockResolvedValue({ ok: true })
    cliStatus.mockResolvedValueOnce({ state: 'missing', path: USER, onPath: true })
    render(<SettingsPage />)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn off and delete' }))
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ cliEnabled: false }))
    await waitFor(() => expect(cliUninstall).toHaveBeenCalledTimes(1))
    // Simulate the settings.changed refresh, then the card shows off+missing.
    useStore.setState({ settings: { ...SETTINGS, cliEnabled: false } })
    await screen.findByText(/Not installed\. Manage automations from the Terminal/)
  })

  it('on + missing: turning off needs no confirm — nothing to delete, plain patch', async () => {
    setup(true)
    cliStatus.mockResolvedValue({ state: 'missing', path: USER, onPath: true })
    render(<SettingsPage />)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    expect(screen.queryByText('Turn off the autowright command?')).toBeNull()
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ cliEnabled: false }))
    expect(cliUninstall).not.toHaveBeenCalled()
  })

  it('failed delete: confirmed disable toasts the error, setting still turns off', async () => {
    setup(true)
    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    cliUninstall.mockResolvedValue({ ok: false, hint: `Couldn’t delete ${USER} — EPERM` })
    render(<SettingsPage />)
    await screen.findByText(`Installed at ${USER}`)
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    fireEvent.click(card.querySelector('[role="switch"]') as Element)
    fireEvent.click(await screen.findByRole('button', { name: 'Turn off and delete' }))
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ cliEnabled: false }))
    await waitFor(() => expect(cliUninstall).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(useStore.getState().toast).toContain('Couldn’t delete'))
  })

  it('off + still installed (failed uninstall leftover): no Delete row anywhere', async () => {
    setup(false)
    cliStatus.mockResolvedValue({ state: 'installed', path: USER, onPath: true })
    render(<SettingsPage />)
    await screen.findByText(new RegExp(`Still installed at ${USER.replace(/[/.]/g, '\\$&')}\\. Turn on to keep it up to date`))
    expect(screen.queryByText('Delete the command')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull()
  })

  it('on + missing: amber warning row below the toggle with a Reinstall button', async () => {
    setup(true)
    cliStatus.mockResolvedValueOnce({ state: 'missing', path: USER, onPath: true })
    cliInstall.mockResolvedValue({ ok: true })
    cliStatus.mockResolvedValueOnce({ state: 'installed', path: USER, onPath: true })
    render(<SettingsPage />)
    // §4.9 missing-warning row: own title + description, Reinstall action.
    await screen.findByText(/CLI is missing/)
    await screen.findByText(/autowright wasn’t found in ~\/\.local\/bin\. It may have been deleted or moved\. Reinstall it to keep using it from the Terminal\./)
    fireEvent.click(await screen.findByRole('button', { name: 'Reinstall' }))
    await waitFor(() => expect(cliInstall).toHaveBeenCalledTimes(1))
    // §3: Reinstall is a settled card install — first-run marker set.
    await waitFor(() => expect(localStorage.getItem('ad-cli-installed')).toBe('1'))
    await screen.findByText(`Installed at ${USER}`)
    expect(screen.queryByText(/CLI is missing/)).toBeNull()
  })

  it('foreign: never touched — no toggle, no buttons', async () => {
    setup(false)
    cliStatus.mockResolvedValue({ state: 'foreign', path: USER, onPath: true })
    render(<SettingsPage />)
    await screen.findByText(new RegExp(`a different autowright is already at ${USER.replace(/[/.]/g, '\\$&')}`, 'i'))
    const card = (await screen.findByText('COMMAND LINE')).parentElement as HTMLElement
    expect(card.querySelector('[role="switch"]')).toBeNull()
    expect(screen.queryByRole('button', { name: /Install|Delete/ })).toBeNull()
  })

  it('no preload bridge (plain browser): card hidden', async () => {
    setup(false)
    delete (window as unknown as Record<string, unknown>).autowright
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.queryByText('COMMAND LINE')).toBeNull()
  })
})
