// §4.9 shared quit flow / §3 quit means quit for good: main pushes
// `quit-requested` on an OS quit (Cmd+Q, the application menu's Quit, the
// dock's Quit) and QuitFlow runs the very flow the QUIT card runs — the
// unforced quit-all, the overlay, the busy question — with no confirm of its
// own. A request while one is in flight is a no-op.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useStore } from '../src/store'
import QuitFlow from '../src/pages/QuitFlow'

type Result = { ok: true } | { busy: true } | { error: string }

const quitAll = vi.fn<(force?: boolean) => Promise<Result>>()
const showToast = vi.fn<(msg: string) => void>()
let requested: (() => void) | null = null
const unsubscribe = vi.fn()

beforeEach(() => {
  requested = null
  ;(window as unknown as Record<string, unknown>).autowright = {
    quitAll,
    onQuitRequested: (cb: () => void) => { requested = cb; return unsubscribe },
  }
  useStore.setState({ showToast, quitStage: null })
  quitAll.mockReset()
  showToast.mockReset()
  unsubscribe.mockReset()
})

afterEach(cleanup)

function finishModalAnim(name: string) {
  fireEvent.animationEnd(screen.getByRole('alertdialog', { name }).parentElement!)
}

describe('QuitFlow (§4.9 shared quit flow)', () => {
  it('renders nothing until asked, and subscribes to the OS quit push', () => {
    const { unmount } = render(<QuitFlow />)
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(requested).toBeTypeOf('function')
    // StrictMode re-runs the effect once on mount (tests/setup.ts), so count
    // the unmount's own unsubscribe rather than the total.
    const before = unsubscribe.mock.calls.length
    unmount()
    expect(unsubscribe).toHaveBeenCalledTimes(before + 1)
  })

  it('quit-requested runs the unforced quit-all under the overlay — no confirm of its own', async () => {
    quitAll.mockReturnValue(new Promise(() => {})) // in flight — app is exiting
    render(<QuitFlow />)
    requested!()
    await waitFor(() => expect(quitAll).toHaveBeenCalledTimes(1))
    expect(quitAll).toHaveBeenCalledWith(false)
    await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })
    await screen.findByText('Stopping everything…')
    expect(screen.queryByRole('alertdialog', { name: 'Quit Autowright entirely?' })).toBeNull()
  })

  it('a repeated request while stopping is a no-op', async () => {
    quitAll.mockReturnValue(new Promise(() => {}))
    render(<QuitFlow />)
    requested!()
    requested!()
    await useStore.getState().quitAll()
    await waitFor(() => expect(quitAll).toHaveBeenCalledTimes(1))
  })

  it('busy: the force question opens; Shut down and quit retries forced', async () => {
    quitAll.mockResolvedValueOnce({ busy: true }).mockReturnValue(new Promise(() => {}))
    render(<QuitFlow />)
    requested!()
    const dlg = await screen.findByRole('alertdialog', { name: 'An automation is executing' })
    expect(dlg.textContent).toContain('Shut down everything and quit? The running automation will be killed.')
    // The overlay drops (its exit-timeout fallback unmounts it).
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog', { name: 'Quitting Autowright' })).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Shut down and quit' }))
    finishModalAnim('An automation is executing')
    await waitFor(() => expect(quitAll.mock.calls).toEqual([[false], [true]]))
    await screen.findByRole('alertdialog', { name: 'Quitting Autowright' })
  })

  it('busy then Cancel: everything stays up and the flow resets', async () => {
    quitAll.mockResolvedValue({ busy: true })
    render(<QuitFlow />)
    requested!()
    await screen.findByRole('alertdialog', { name: 'An automation is executing' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    finishModalAnim('An automation is executing')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(useStore.getState().quitStage).toBeNull()
    expect(quitAll).toHaveBeenCalledTimes(1)
  })

  it('stop failure: toast, overlay drops, flow resets', async () => {
    quitAll.mockResolvedValue({ error: 'stop failed: launchd still reports the job' })
    render(<QuitFlow />)
    requested!()
    await waitFor(() => expect(showToast).toHaveBeenCalledWith('stop failed: launchd still reports the job'))
    await waitFor(() => expect(screen.queryByRole('alertdialog', { name: 'Quitting Autowright' })).toBeNull())
    expect(useStore.getState().quitStage).toBeNull()
  })
})
