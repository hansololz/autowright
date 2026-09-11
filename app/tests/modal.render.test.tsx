// §14 Modal primitive: the guardClose escape path. Escape and a backdrop click
// ask the guard first, and a `false` keeps the card open (the caller raises its
// own confirm above it); without the prop both close as they always have. An
// open §9.3 developer-log overlay owns Escape outright — the card underneath
// yields. The Modal renders for real (happy-dom) with the api module mocked,
// so importing src/ui opens no sockets.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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

// §14: keyboard focus is trapped in the open card — it takes focus on open
// (unless a child already holds it), Tab wraps at the ends, and only the
// top-most card of a stack traps.
describe('Modal focus trap (§14)', () => {
  const card = () => screen.getByRole('dialog')

  it('a card that opens while focus sits on the page takes focus', () => {
    const outside = document.createElement('button')
    outside.textContent = 'page button'
    document.body.appendChild(outside)
    outside.focus()
    expect(document.activeElement).toBe(outside)

    render(
      <Modal onClose={vi.fn()} width={400} ariaLabel="Takes focus">
        {() => <button>inside</button>}
      </Modal>,
    )
    expect(document.activeElement).toBe(card())
    outside.remove()
  })

  it('a child that autofocuses its own input keeps the focus', () => {
    render(
      <Modal onClose={vi.fn()} width={400} ariaLabel="Autofocused">
        {() => <input autoFocus aria-label="secret value" />}
      </Modal>,
    )
    expect(document.activeElement).toBe(screen.getByLabelText('secret value'))
  })

  it('Tab on the last element wraps to the first and Shift+Tab back', () => {
    render(
      <Modal onClose={vi.fn()} width={400} ariaLabel="Wrapping">
        {() => (
          <>
            <button>first</button>
            <button>middle</button>
            <button>last</button>
          </>
        )}
      </Modal>,
    )
    const first = screen.getByText('first')
    const last = screen.getByText('last')

    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('Shift+Tab off the card itself wraps to the last element', () => {
    render(
      <Modal onClose={vi.fn()} width={400} ariaLabel="From the card">
        {() => (
          <>
            <button>first</button>
            <button>last</button>
          </>
        )}
      </Modal>,
    )
    expect(document.activeElement).toBe(card())
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(screen.getByText('last'))
  })

  it('only the top-most card of a stack traps', () => {
    render(
      <>
        <Modal onClose={vi.fn()} width={400} ariaLabel="Underneath">
          {() => (
            <>
              <button>under first</button>
              <button>under last</button>
            </>
          )}
        </Modal>
        <Modal onClose={vi.fn()} width={400} zIndex={90} ariaLabel="Stacked confirm">
          {() => (
            <>
              <button>top first</button>
              <button>top last</button>
            </>
          )}
        </Modal>
      </>,
    )
    // the confirm stacked above took focus, and Tab wraps inside it
    screen.getByText('top last').focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByText('top first'))

    // the card underneath never wraps to its own first element — the top card
    // pulls the stray focus back inside itself instead
    screen.getByText('under last').focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByText('top first'))
  })
})

// §14: the card takes focus as the trap anchor, so a whole-card outline would
// read as the modal being selected. It carries the class the token sheet hangs
// its no-ring rule on; the controls inside keep their own rings.
describe('Modal card focus ring (§14)', () => {
  it('the aria-modal card carries the ad-modal-card class', () => {
    render(
      <Modal onClose={vi.fn()} width={400} ariaLabel="Ringless">
        {() => <div>ringless body</div>}
      </Modal>,
    )
    const card = document.querySelector('[aria-modal="true"]') as HTMLElement
    expect(card.classList.contains('ad-modal-card')).toBe(true)
  })

  it('tokens.css drops the outline on that class', () => {
    // the sheet never loads in happy-dom — the rule is asserted on the source
    const tokens = readFileSync(join(__dirname, '..', 'src', 'tokens.css'), 'utf-8')
    expect(tokens).toMatch(/\.ad-modal-card:focus-visible\s*\{[^}]*outline:\s*none[^}]*\}/)
  })
})
