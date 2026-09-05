// §4.9 RESET card — the destructive card below QUIT, last on the page. It
// renders only with the preload bridge. Busy and error leave everything in
// place and toast; success is the shell's business (the app quits).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
const resetAll = vi.fn<() => Promise<Result>>()
const onResetProgress = vi.fn<(cb: (stage: string) => void) => void>()
const showToast = vi.fn<(msg: string) => void>()

const bridge = () => {
  ;(window as unknown as Record<string, unknown>).autowright = { cliStatus, resetAll, onResetProgress }
}

beforeEach(() => {
  bridge()
  cliStatus.mockResolvedValue({ state: 'missing' })
  useStore.setState({ platformOs: '', settings: SETTINGS, showToast })
  resetAll.mockReset()
  onResetProgress.mockReset()
  showToast.mockReset()
})

afterEach(cleanup)

// ConfirmModal acts on onClose, which fires only after the overlay's exit
// animation — jsdom runs no animations, so end it by hand.
function finishModalAnim(name: string) {
  const dlg = screen.getByRole('alertdialog', { name })
  fireEvent.animationEnd(dlg.parentElement!)
}

describe('RESET card (§4.9)', () => {
  const openConfirm = async () => {
    render(<SettingsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Reset…' }))
    return screen.findByRole('alertdialog', { name: 'Delete all data and quit app?' })
  }

  it('renders the row with its consequence copy', async () => {
    render(<SettingsPage />)
    await screen.findByText('RESET')
    await screen.findByText('Delete all data and quit app')
    await screen.findByText(
      'Erases every automation, execution, agent, secret, and setting from this Mac, '
      + 'then Autowright quits. The next launch starts as new.',
    )
    await screen.findByRole('button', { name: 'Reset…' })
  })

  it('the confirm carries the spec body and the Delete everything label', async () => {
    await openConfirm()
    expect(screen.getByText(
      'Every automation, execution, agent, and setting on this Mac is deleted, and every '
      + 'secret is removed from your Keychain. Autowright then quits, and the next launch '
      + 'starts as if newly installed. This can’t be undone.',
    )).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete everything' })).toBeTruthy()
  })

  it('cancel fires nothing', async () => {
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    finishModalAnim('Delete all data and quit app?')
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Delete all data and quit app?' })).toBeNull())
    expect(resetAll).not.toHaveBeenCalled()
  })

  it('confirm invokes reset-all once and shows Resetting…', async () => {
    resetAll.mockReturnValue(new Promise(() => {})) // in flight — the app quits
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    finishModalAnim('Delete all data and quit app?')
    await waitFor(() => expect(resetAll).toHaveBeenCalledTimes(1))
    await screen.findByText('Resetting…')
    expect(screen.getByRole('button', { name: /Resetting…/ })).toHaveProperty('disabled', true)
  })

  it('confirm raises the non-dismissable progress overlay', async () => {
    resetAll.mockReturnValue(new Promise(() => {})) // in flight — the app quits
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    finishModalAnim('Delete all data and quit app?')
    const overlay = await screen.findByRole('alertdialog', { name: 'Deleting all data' })
    await screen.findByText('Deleting all data…')
    await screen.findByText('Preparing…')
    // Non-dismissable: neither Escape nor a backdrop click closes it.
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.mouseDown(overlay.parentElement!)
    expect(screen.getByRole('alertdialog', { name: 'Deleting all data' })).toBeTruthy()
  })

  it('reset-progress pushes drive the overlay stage line', async () => {
    resetAll.mockReturnValue(new Promise(() => {}))
    await openConfirm()
    // The page subscribed on mount; drive the captured callback by hand.
    const push = onResetProgress.mock.calls[0][0]
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    finishModalAnim('Delete all data and quit app?')
    await screen.findByText('Preparing…')
    act(() => push('service'))
    await screen.findByText('Stopping the background service…')
    act(() => push('data'))
    await screen.findByText('Deleting data…')
  })

  it('a stray reset-progress push never raises the overlay', async () => {
    render(<SettingsPage />)
    await screen.findByRole('button', { name: 'Reset…' })
    const push = onResetProgress.mock.calls[0][0]
    act(() => push('data'))
    expect(screen.queryByRole('alertdialog', { name: 'Deleting all data' })).toBeNull()
  })

  it('busy (live execution): toast, row resets, nothing deleted', async () => {
    resetAll.mockResolvedValue({ busy: true })
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    finishModalAnim('Delete all data and quit app?')
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'An automation is executing. Reset when it finishes.'))
    const btn = await screen.findByRole('button', { name: 'Reset…' })
    expect(btn).toHaveProperty('disabled', false)
    // The progress overlay closes too (its exit-timeout fallback unmounts it).
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Deleting all data' })).toBeNull())
  })

  it('error: toast with the error text, row resets', async () => {
    resetAll.mockResolvedValue({ error: 'stop failed: launchd still reports the job' })
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: 'Delete everything' }))
    finishModalAnim('Delete all data and quit app?')
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'stop failed: launchd still reports the job'))
    await screen.findByRole('button', { name: 'Reset…' })
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Deleting all data' })).toBeNull())
  })

  it('no preload bridge (plain browser): card hidden', async () => {
    delete (window as unknown as Record<string, unknown>).autowright
    render(<SettingsPage />)
    await screen.findByText('GENERAL')
    expect(screen.queryByText('RESET')).toBeNull()
  })

  it('windows: the copy names the PC and the Credential Manager', async () => {
    useStore.setState({ platformOs: 'windows' })
    await openConfirm()
    expect(screen.getByText(
      'Erases every automation, execution, agent, secret, and setting from this PC, '
      + 'then Autowright quits. The next launch starts as new.',
    )).toBeTruthy()
    expect(screen.getByText(
      'Every automation, execution, agent, and setting on this PC is deleted, and every '
      + 'secret is removed from your Credential Manager. Autowright then quits, and the '
      + 'next launch starts as if newly installed. This can’t be undone.',
    )).toBeTruthy()
    expect(screen.queryByText(/Keychain/)).toBeNull()
  })
})
