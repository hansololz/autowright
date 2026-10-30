// Unit tests for src/triggers.ts (no trigger math lives in the renderer —
// §4.3). The useTriggerPreview debounce/sequence guard is timing-hard to stage in e2e, so
// it is exercised here through renderHook with the api mocked.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { TriggerPreview } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { triggersPreview: vi.fn(() => Promise.reject(new Error('offline'))) },
}))

import { api } from '../src/api'
import { useTriggerPreview } from '../src/triggers'

const pv = (over: Partial<TriggerPreview> = {}): TriggerPreview =>
  ({ valid: true, label: 'L', short: 'S', nextAtMs: 1000, ...over })

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useTriggerPreview (§19 debounced preview)', () => {
  const cron = { kind: 'cron', expression: '0 8 * * *' }

  it('debounces 300 ms then fetches; an empty list clears immediately with no call', async () => {
    vi.useFakeTimers()
    const tp = vi.mocked(api.triggersPreview)
    tp.mockClear()
    tp.mockResolvedValue({ triggers: [pv({ short: 'daily 8:00' })] } as never)
    const { result, rerender } = renderHook(
      ({ t }: { t: object[] }) => useTriggerPreview(t), { initialProps: { t: [cron] as object[] } },
    )
    expect(result.current).toEqual([])          // empty until the first response
    await act(async () => { await vi.advanceTimersByTimeAsync(299) })
    expect(tp).not.toHaveBeenCalled()           // still inside the debounce window
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(tp).toHaveBeenCalledWith([cron])
    expect(result.current).toEqual([pv({ short: 'daily 8:00' })])
    rerender({ t: [] })
    expect(result.current).toEqual([])          // cleared synchronously
    expect(tp).toHaveBeenCalledTimes(1)         // no request for the empty list
  })

  it('a stale in-flight response never lands after the triggers change', async () => {
    vi.useFakeTimers()
    const tp = vi.mocked(api.triggersPreview)
    tp.mockClear()
    let resolveOld!: (v: unknown) => void
    tp.mockReturnValueOnce(new Promise((r) => { resolveOld = r }) as never)
    tp.mockResolvedValueOnce({ triggers: [pv({ short: 'new' })] } as never)
    const { result, rerender } = renderHook(
      ({ t }: { t: object[] }) => useTriggerPreview(t), { initialProps: { t: [cron] as object[] } },
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })  // old request in flight
    rerender({ t: [{ kind: 'cron', expression: '30 9 * * 1' }] })     // bumps the sequence
    expect(result.current).toEqual([])          // previous results keep showing (none yet)
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    expect(result.current).toEqual([pv({ short: 'new' })])
    await act(async () => { resolveOld({ triggers: [pv({ short: 'old' })] }) })
    expect(result.current).toEqual([pv({ short: 'new' })])  // stale response discarded
  })
})
