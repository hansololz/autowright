// §9.3 developer log overlay: the Backquote toggle, its developerMode gate, the
// tabs (requests browser first, then one per existing log file), and the
// `devlogOverlayOpen()` mirror the §11 Esc-to-cancel handler reads. The overlay
// renders for real (happy-dom) with the store seeded and the preload IPC stubbed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { Settings } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { state: vi.fn(() => Promise.reject(new Error('offline'))) },
}))

import { useStore } from '../src/store'
import DevLogOverlay, { devlogOverlayOpen } from '../src/devlog'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: true,
  cliEnabled: false, dataPath: '/tmp', dataSize: '0 B',
}

const tailLogs = vi.fn<() => Promise<{ name: string; text: string }[]>>()
const listRequestLogs = vi.fn<() => Promise<string[]>>()
const readRequestLog = vi.fn<(name: string) => Promise<string | null>>()

const setDeveloperMode = (developerMode: boolean) =>
  act(() => { useStore.setState({ settings: { ...SETTINGS, developerMode } }) })

// The overlay only listens on window, so the toggle is a real keystroke.
const backquote = (target: Window | Element = window) =>
  fireEvent.keyDown(target, { code: 'Backquote', key: '`' })

beforeEach(() => {
  tailLogs.mockReset().mockResolvedValue([{ name: 'app.log', text: 'app log body' }])
  listRequestLogs.mockReset().mockResolvedValue([])
  readRequestLog.mockReset().mockResolvedValue(null)
  ;(window as unknown as Record<string, unknown>).autowright = {
    tailLogs, listRequestLogs, readRequestLog,
  }
  useStore.setState({ settings: { ...SETTINGS } })
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('developer log overlay (§9.3)', () => {
  it('is inert while developerMode is off: no overlay, no key listener', () => {
    setDeveloperMode(false)
    const { container } = render(<DevLogOverlay />)

    backquote()
    expect(container.firstChild).toBeNull()
    expect(devlogOverlayOpen()).toBe(false)
    expect(tailLogs).not.toHaveBeenCalled()
  })

  it('toggles open on Backquote and closes on Escape, mirroring the state outside React', async () => {
    render(<DevLogOverlay />)
    expect(devlogOverlayOpen()).toBe(false)

    backquote()
    expect(await screen.findByRole('button', { name: 'requests' })).toBeTruthy()
    expect(devlogOverlayOpen()).toBe(true)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: 'requests' })).toBeNull()
    expect(devlogOverlayOpen()).toBe(false)

    // the same key toggles it back closed, too
    backquote()
    expect(devlogOverlayOpen()).toBe(true)
    backquote()
    expect(devlogOverlayOpen()).toBe(false)
  })

  it('ignores the key while focus sits in an editable element', () => {
    const { container } = render(
      <div><input data-testid="field" /><DevLogOverlay /></div>,
    )

    backquote(screen.getByTestId('field'))
    expect(devlogOverlayOpen()).toBe(false)
    expect(container.querySelector('.ad-anim-fade')).toBeNull()
  })

  it('turning developerMode off closes an open overlay', async () => {
    render(<DevLogOverlay />)
    backquote()
    await screen.findByRole('button', { name: 'requests' })

    setDeveloperMode(false)
    expect(screen.queryByRole('button', { name: 'requests' })).toBeNull()
    expect(devlogOverlayOpen()).toBe(false)
  })

  it('clears the mirror when the overlay unmounts', async () => {
    const view = render(<DevLogOverlay />)
    backquote()
    await screen.findByRole('button', { name: 'requests' })
    expect(devlogOverlayOpen()).toBe(true)

    view.unmount()
    expect(devlogOverlayOpen()).toBe(false)
  })

  it('opens on the requests tab and shows a tab per existing log file', async () => {
    tailLogs.mockResolvedValue([
      { name: 'app.log', text: 'app log body' },
      { name: 'vite.log', text: 'vite log body' },
    ])
    render(<DevLogOverlay />)
    backquote()

    // requests is the default tab: its empty note shows, no file body yet
    expect(await screen.findByText('No request logs yet — make a request with Developer mode on')).toBeTruthy()
    const appTab = await screen.findByRole('button', { name: 'app.log' })
    expect(screen.getByRole('button', { name: 'vite.log' })).toBeTruthy()
    expect(screen.queryByText('app log body')).toBeNull()

    // a file tab tails that file's text
    fireEvent.click(appTab)
    expect(screen.getByText('app log body')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'vite.log' }))
    expect(screen.getByText('vite log body')).toBeTruthy()
  })

  it('browses the §5 request logs: names list, then the selected file body', async () => {
    listRequestLogs.mockResolvedValue(['20260820-120001-post-chat.json', '20260820-120000-get-state.json'])
    readRequestLog.mockResolvedValue('{"request": "body"}')
    render(<DevLogOverlay />)
    backquote()

    const first = await screen.findByRole('button', { name: '20260820-120001-post-chat.json' })
    // nothing selected yet, so the right pane prompts
    expect(screen.getByText('Select a request')).toBeTruthy()

    fireEvent.click(first)
    expect(await screen.findByText('{"request": "body"}')).toBeTruthy()
    expect(readRequestLog).toHaveBeenCalledWith('20260820-120001-post-chat.json')
  })

  it('stops polling once closed', async () => {
    render(<DevLogOverlay />)
    backquote()
    await screen.findByRole('button', { name: 'app.log' })
    const polls = tailLogs.mock.calls.length

    fireEvent.keyDown(window, { key: 'Escape' })
    await act(async () => { await new Promise((r) => setTimeout(r, 1100)) })
    expect(tailLogs.mock.calls.length).toBe(polls)
  })
})

describe('developer log overlay polls a rejecting bridge (§9.3)', () => {
  it('a rejecting tail/list bridge produces no unhandled rejection', async () => {
    tailLogs.mockReset().mockRejectedValue(new Error('main restarting'))
    listRequestLogs.mockReset().mockRejectedValue(new Error('main restarting'))
    const unhandled = vi.fn()
    process.on('unhandledRejection', unhandled)
    try {
      render(<DevLogOverlay />)
      act(() => { backquote() })
      await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
      expect(tailLogs).toHaveBeenCalled()
      expect(listRequestLogs).toHaveBeenCalled()
      expect(unhandled).not.toHaveBeenCalled()
    } finally {
      process.off('unhandledRejection', unhandled)
    }
  })
})
