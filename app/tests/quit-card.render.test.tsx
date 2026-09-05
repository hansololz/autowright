// §4.9 QUIT card: confirm-gated quit-all IPC (§3 explicit-quit exception) —
// the blocking quit overlay is up for the whole call; busy asks the force
// question, error toasts and leaves everything running, success exits the app.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Settings } from '../src/types'
import { useStore } from '../src/store'
import SettingsPage from '../src/pages/SettingsPage'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false, cliEnabled: false,
  dataPath: '/tmp', dataSize: '0 B',
}

type Result = { ok: true } | { busy: true } | { error: string }

const cliStatus = vi.fn<() => Promise<{ state: 'missing' }>>()
const quitAll = vi.fn<(force?: boolean) => Promise<Result>>()
const showToast = vi.fn<(msg: string) => void>()

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, quitAll }
  cliStatus.mockResolvedValue({ state: 'missing' })
  useStore.setState({ settings: SETTINGS, showToast })
  quitAll.mockReset()
  showToast.mockReset()
})

afterEach(cleanup)

async function openConfirm() {
  render(<SettingsPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Quit…' }))
  return screen.findByRole('alertdialog', { name: 'Quit Autowright entirely?' })
}

// ConfirmModal acts on onClose, which fires only after the overlay's exit
// animation — jsdom runs no animations, so end it by hand.
function finishModalAnim(name = 'Quit Autowright entirely?') {
  const dlg = screen.getByRole('alertdialog', { name })
  fireEvent.animationEnd(dlg.parentElement!)
}

// Confirm the first modal — the quit-all call the rest of a test hangs off.
async function confirmQuit() {
  await openConfirm()
  fireEvent.click(screen.getByRole('button', { name: 'Quit Autowright' }))
  finishModalAnim()
}

describe('QUIT card (§4.9)', () => {
  it('renders the row with its consequence copy', async () => {
    render(<SettingsPage />)
    await screen.findByText('QUIT')
    await screen.findByText('Quit Autowright entirely')
    await screen.findByText(/Schedules and message triggers pause until you next log in/)
    await screen.findByRole('button', { name: 'Quit…' })
  })

  it('Quit… opens the confirm; cancel fires nothing', async () => {
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    finishModalAnim()
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Quit Autowright entirely?' })).toBeNull())
    expect(quitAll).not.toHaveBeenCalled()
  })

  it('confirm invokes quit-all once, unforced, and shows Stopping…', async () => {
    quitAll.mockReturnValue(new Promise(() => {})) // in flight — app is exiting
    await confirmQuit()
    await waitFor(() => expect(quitAll).toHaveBeenCalledTimes(1))
    expect(quitAll).toHaveBeenCalledWith(false)
    await screen.findByText('Stopping…')
    expect(screen.getByRole('button', { name: /Stopping…/ })).toHaveProperty('disabled', true)
  })

  it('confirm raises the non-dismissable quit overlay while quit-all runs', async () => {
    quitAll.mockReturnValue(new Promise(() => {})) // in flight — app is exiting
    await confirmQuit()
    const overlay = await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })
    await screen.findByText('Quitting Autowright…')
    await screen.findByText('Stopping everything…')
    // Non-dismissable: neither Escape nor a backdrop click closes it.
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.mouseDown(overlay.parentElement!)
    expect(screen.getByRole('alertdialog', { name: 'Quitting Autowright' })).toBeTruthy()
  })

  it('ok: the overlay stays up, since the app is exiting', async () => {
    quitAll.mockResolvedValue({ ok: true })
    await confirmQuit()
    await waitFor(() => expect(quitAll).toHaveBeenCalledTimes(1))
    await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })
    // Nothing resets: the row stays busy and the overlay stays mounted.
    await waitFor(() => expect(screen.getByText('Stopping everything…')).toBeTruthy())
    expect(screen.getByRole('button', { name: /Stopping…/ })).toHaveProperty('disabled', true)
    expect(screen.queryByRole('button', { name: 'Quit…' })).toBeNull()
    expect(showToast).not.toHaveBeenCalled()
  })

  it('busy (live execution): the force confirm opens, overlay drops, no toast', async () => {
    quitAll.mockResolvedValue({ busy: true })
    await confirmQuit()
    const dlg = await screen.findByRole('alertdialog', { name: 'An automation is executing' })
    expect(dlg.textContent).toContain(
      'Shut down everything and quit? The running automation will be killed.')
    expect(screen.getByRole('button', { name: 'Shut down and quit' })).toBeTruthy()
    expect(showToast).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Quitting Autowright' })).toBeNull())
  })

  it('busy then Shut down and quit: quit-all runs again, forced', async () => {
    quitAll.mockResolvedValueOnce({ busy: true }).mockReturnValue(new Promise(() => {}))
    await confirmQuit()
    await screen.findByRole('alertdialog', { name: 'An automation is executing' })
    fireEvent.click(screen.getByRole('button', { name: 'Shut down and quit' }))
    finishModalAnim('An automation is executing')
    await waitFor(() => expect(quitAll).toHaveBeenCalledTimes(2))
    expect(quitAll.mock.calls).toEqual([[false], [true]])
    // The retry raises the overlay again and holds it — the app is exiting.
    await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })
  })

  it('busy then cancel: the force confirm closes and nothing else quits', async () => {
    quitAll.mockResolvedValue({ busy: true })
    await confirmQuit()
    await screen.findByRole('alertdialog', { name: 'An automation is executing' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    finishModalAnim('An automation is executing')
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'An automation is executing' })).toBeNull())
    expect(quitAll).toHaveBeenCalledTimes(1)
    const btn = await screen.findByRole('button', { name: 'Quit…' })
    expect(btn).toHaveProperty('disabled', false)
  })

  it('stop failure: toast with the error, overlay drops, app stays up', async () => {
    quitAll.mockResolvedValue({ error: 'stop failed: launchd still reports the job' })
    await confirmQuit()
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'stop failed: launchd still reports the job'))
    await screen.findByRole('button', { name: 'Quit…' })
    // The quit overlay closes too (its exit-timeout fallback unmounts it).
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Quitting Autowright' })).toBeNull())
    expect(screen.queryByRole('alertdialog', { name: 'An automation is executing' })).toBeNull()
  })

  it('no preload bridge (plain browser): card hidden', async () => {
    delete (window as unknown as Record<string, unknown>).autowright
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.queryByText('QUIT')).toBeNull()
  })
})
