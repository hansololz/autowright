// §14 Modal primitive: the guardClose escape path. Escape and a backdrop click
// ask the guard first, and a `false` keeps the card open (the caller raises its
// own confirm above it); without the prop both close as they always have. An
// open §9.3 developer-log overlay owns Escape outright — the card underneath
// yields. The Modal renders for real (happy-dom) with the api module mocked,
// so importing src/ui opens no sockets.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {},
}))

let Modal: typeof import('../src/ui').Modal
let DevLogOverlay: typeof import('../src/devlog').default
let storeMod: typeof import('../src/store')

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    // §9.3: the overlay tails the log files the moment it opens
    tailLogs: () => Promise.resolve([]),
    listRequestLogs: () => Promise.resolve([]),
    readRequestLog: () => Promise.resolve(null),
  }
  Modal = (await import('../src/ui')).Modal
  DevLogOverlay = (await import('../src/devlog')).default
  storeMod = await import('../src/store')
})

afterEach(() => cleanup())

// The Modal portals to document.body; its backdrop is the card's own parent.
const backdrop = () => screen.getByRole('dialog').parentElement!

describe('Modal guardClose (§14)', () => {
  it('a guard returning false keeps Escape and a backdrop click from closing', async () => {
    const onClose = vi.fn()
    render(
      <Modal onClose={onClose} width={400} ariaLabel="Guarded" guardClose={() => false}>
        {() => <div>guarded body</div>}
      </Modal>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.mouseDown(backdrop())
    // the exit animation falls back to a 200 ms timer — outlast it
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText('guarded body')).toBeTruthy()
  })

  it('a guard returning true closes on Escape, as an unguarded card does', async () => {
    const onClose = vi.fn()
    render(
      <Modal onClose={onClose} width={400} ariaLabel="Open" guardClose={() => true}>
        {() => <div>open body</div>}
      </Modal>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('a guard returning true closes on a backdrop click too', async () => {
    const onClose = vi.fn()
    render(
      <Modal onClose={onClose} width={400} ariaLabel="Open" guardClose={() => true}>
        {() => <div>open body</div>}
      </Modal>,
    )
    fireEvent.mouseDown(backdrop())
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('an open developer-log overlay owns Escape — the card underneath yields (§9.3)', async () => {
    storeMod.useStore.setState({
      settings: { developerMode: true } as unknown as import('../src/types').Settings,
    })
    const onClose = vi.fn()
    render(
      <>
        <Modal onClose={onClose} width={400} ariaLabel="Under the overlay">
          {() => <div>under body</div>}
        </Modal>
        <DevLogOverlay />
      </>,
    )
    // §9.3 Backquote opens the overlay over the card
    fireEvent.keyDown(window, { code: 'Backquote', key: '`' })
    fireEvent.keyDown(document, { key: 'Escape' })
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText('under body')).toBeTruthy()

    // that Escape closed the overlay (§9.3) — the next one reaches the card
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('without the prop Escape and the backdrop close exactly as before', async () => {
    const onEscape = vi.fn()
    const { unmount } = render(
      <Modal onClose={onEscape} width={400} ariaLabel="Plain">
        {() => <div>plain body</div>}
      </Modal>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(onEscape).toHaveBeenCalledTimes(1))
    unmount()

    const onBackdrop = vi.fn()
    render(
      <Modal onClose={onBackdrop} width={400} ariaLabel="Plain">
        {() => <div>plain body</div>}
      </Modal>,
    )
    fireEvent.mouseDown(backdrop())
    await waitFor(() => expect(onBackdrop).toHaveBeenCalledTimes(1))
  })
})
