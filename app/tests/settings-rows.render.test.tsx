// §4.9 Settings rows: every machine-mutating toggle patches exactly its own
// key (never a whole-settings write), the loading state before settings land,
// the §13 tray gate on the "Show in the menu bar" row, the keep-forever
// retention copy, a failed patch, and the data-path folder picker.
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
const setDataPath = vi.mocked(api.setDataPath)

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false,
  cliEnabled: false, dataPath: '/tmp', dataSize: '0 B',
}

const pickFolder = vi.fn<(p?: string) => Promise<string | null>>()
const platformInfo = vi.fn<() => Promise<{ platform: string; release: string; arch: string; version: string; trayPanel?: boolean }>>()
// The §4.9 COMMAND LINE card only renders once cli-status answers; a rejecting
// probe keeps it out of the way of the rows under test.
const cliStatus = vi.fn(() => Promise.reject(new Error('no bridge')))

// Same seeding shape as cli-card.render.test.tsx: the bridge, then the stored
// settings the page reads (they arrive over the store's own fetch, not through
// any effect of this page).
function setup(over: Partial<Settings> = {}) {
  ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, pickFolder, platformInfo }
  useStore.setState({ settings: { ...SETTINGS, ...over }, toast: null })
}

// §14 settings row: title + sub in one column, the control beside them.
const toggleFor = (title: string): Element =>
  screen.getByText(title).parentElement!.parentElement!.querySelector('[role="switch"]')!

// Spinner renders a bare span animated with adSpin. That is the only way to
// find it.
const spinnersIn = (el: Element) =>
  [...el.querySelectorAll('span')].filter((s) => ((s as HTMLElement).style.animation || '').includes('adSpin'))

beforeEach(() => {
  vi.clearAllMocks()
  patchSettings.mockResolvedValue({} as never)
  setDataPath.mockResolvedValue({} as never)
  platformInfo.mockResolvedValue({ platform: 'darwin', release: '25.6.0', arch: 'arm64', version: '0.10.2', trayPanel: true })
})
afterEach(cleanup)

describe('Settings rows (§4.9)', () => {
  it('each toggle patches exactly its own key', async () => {
    setup()
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    const rows: [string, keyof Settings][] = [
      ['Launch at login', 'login'],
      ['Show in the menu bar', 'menuBarIcon'],
      ['Keep this Mac awake', 'keepAwake'],
      ['Keep execution history forever', 'keepForever'],
      ['Developer mode', 'developerMode'],
    ]
    for (const [title, key] of rows) {
      patchSettings.mockClear()
      fireEvent.click(toggleFor(title))
      expect(patchSettings).toHaveBeenCalledTimes(1)
      expect(patchSettings).toHaveBeenCalledWith({ [key]: true })
    }
  })

  it('settings not loaded yet: the title and the busy state, no cards', () => {
    ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, pickFolder, platformInfo }
    useStore.setState({ settings: null })
    const { container } = render(<SettingsPage />)
    expect(screen.getByText('Settings')).toBeTruthy()
    expect(spinnersIn(container).length).toBe(1)
    expect(screen.queryByText('GENERAL')).toBeNull()
  })

  it('a shell with no tray panel drops the menu-bar row, keeping the rest (§13)', async () => {
    setup()
    platformInfo.mockResolvedValue({ platform: 'linux', release: '6.8', arch: 'x64', version: '0.10.2', trayPanel: false })
    render(<SettingsPage />)
    await waitFor(() => expect(screen.queryByText('Show in the menu bar')).toBeNull())
    expect(screen.getByText('Keep this Mac awake')).toBeTruthy()
  })

  it('keepForever on: the retention-days row is gone and the sub-copy says so', async () => {
    setup({ keepForever: true })
    render(<SettingsPage />)
    await screen.findByText('EXECUTION HISTORY')
    expect(screen.queryByText('Keep executions for')).toBeNull()
    expect(screen.getByText(
      'Nothing is ever removed. Execution data grows until you clear it yourself.',
    )).toBeTruthy()
  })

  it('a rejected patch toasts the error', async () => {
    setup()
    patchSettings.mockRejectedValueOnce(new Error('Settings are read-only right now.'))
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    fireEvent.click(toggleFor('Launch at login'))
    await waitFor(() => expect(useStore.getState().toast).toBe('Settings are read-only right now.'))
  })

  it('Change data path: the picked folder is set; a cancelled picker sets nothing', async () => {
    setup()
    pickFolder.mockResolvedValue('/tmp/x')
    render(<SettingsPage />)
    fireEvent.click(await screen.findByText('Change'))
    await waitFor(() => expect(setDataPath).toHaveBeenCalledWith('/tmp/x'))
    await waitFor(() => expect(useStore.getState().toast).toBe('Execution data location changed.'))

    setDataPath.mockClear()
    pickFolder.mockResolvedValue(null)
    fireEvent.click(screen.getByText('Change'))
    await waitFor(() => expect(pickFolder).toHaveBeenCalledTimes(2))
    expect(setDataPath).not.toHaveBeenCalled()
  })
})
