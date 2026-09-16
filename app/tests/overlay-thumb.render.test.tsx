// §14 overlay scrollbar: the pane watches its own box and subtree so content
// that grows without a re-render (async loads, streamed logs) still moves the
// thumb. The hook's observers have to survive the StrictMode remount every
// render-tier test runs under (tests/setup.ts) — the cleanup drops them, so
// re-attaching the same element must observe it again.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {},
}))

// One recording observer for both halves of the pair: `live` is what the pane
// still watches after React has mounted, unmounted and remounted it.
class RecordingObserver {
  static instances: RecordingObserver[] = []
  targets: Element[] = []
  live = false
  constructor() { (this.constructor as typeof RecordingObserver).instances.push(this) }
  observe(el: Element) { this.targets.push(el); this.live = true }
  disconnect() { this.live = false }
}
class FakeResizeObserver extends RecordingObserver { static instances: RecordingObserver[] = [] }
class FakeMutationObserver extends RecordingObserver { static instances: RecordingObserver[] = [] }

const watching = (instances: RecordingObserver[], el: Element) =>
  instances.filter((o) => o.live && o.targets.includes(el))

let ScrollArea: typeof import('../src/ui').ScrollArea

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  ;(globalThis as unknown as Record<string, unknown>).ResizeObserver = FakeResizeObserver
  ;(globalThis as unknown as Record<string, unknown>).MutationObserver = FakeMutationObserver
  ScrollArea = (await import('../src/ui')).ScrollArea
})

afterEach(() => {
  cleanup()
  FakeResizeObserver.instances = []
  FakeMutationObserver.instances = []
})

describe('useOverlayThumb (§14)', () => {
  it('the mounted pane is still observed after the StrictMode remount', () => {
    render(<ScrollArea testId="pane">rows</ScrollArea>)
    const pane = screen.getByTestId('pane')
    // The remount really happened — the first pair was disconnected…
    expect(FakeResizeObserver.instances.some((o) => !o.live)).toBe(true)
    // …and the element the pane came back with is watched by a live pair, or
    // content growing after mount would never move the thumb.
    expect(watching(FakeResizeObserver.instances, pane)).toHaveLength(1)
    expect(watching(FakeMutationObserver.instances, pane)).toHaveLength(1)
  })

  it('unmounting leaves nothing observing', () => {
    const view = render(<ScrollArea testId="pane">rows</ScrollArea>)
    const pane = screen.getByTestId('pane')
    view.unmount()
    expect(watching(FakeResizeObserver.instances, pane)).toHaveLength(0)
    expect(watching(FakeMutationObserver.instances, pane)).toHaveLength(0)
  })
})
