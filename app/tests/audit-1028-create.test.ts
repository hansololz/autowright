// Audit 2026-10-28 (create/edit flow): the §9.2 find bar's length-preserving
// case fold, and the §19 /validate-steps body carrying the automation id.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { findInLines, markLine } from '../src/find'
import { api, connectInfo } from '../src/api'

afterEach(() => vi.unstubAllGlobals())

describe('findInLines case fold (§9.2)', () => {
  it('a line with "İ" (whose lowercase is two code units) keeps match offsets on the rendered text', () => {
    const line = 'İİ token = "TOKEN"'
    const hits = findInLines([[line]], 'token')
    expect(hits).toEqual([{ line: 0, start: 3, end: 8 }, { line: 0, start: 12, end: 17 }])
    for (const h of hits) expect(line.slice(h.start, h.end).toLowerCase()).toBe('token')
    // the marks wrap exactly the matched text
    const marked = markLine([line], hits.map((h) => ({ ...h, current: false })))
    const texts = (marked[0] as { props: { children: unknown[] } }).props.children
      .filter((c): c is { props: { children: string } } => typeof c === 'object' && c !== null)
      .map((c) => c.props.children)
    expect(texts).toEqual(['token', 'TOKEN'])
    // the unfoldable code point still matches itself
    expect(findInLines([[line]], 'İ')).toEqual([{ line: 0, start: 0, end: 1 }, { line: 0, start: 1, end: 2 }])
  })
})

describe('api.validateSteps body (§19)', () => {
  it('sends automationId with the draft only when given', async () => {
    ;(window as unknown as Record<string, unknown>).autowright = { backendInfo: async () => ({ port: 4242, token: 'tok' }) }
    await connectInfo()
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ errors: [] }) }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    await api.validateSteps({ steps: [] }, 'a1')
    await api.validateSteps({ steps: [] })
    const calls = fetchMock.mock.calls as unknown as [string, { body: string }][]
    expect(calls.map(([, init]) => JSON.parse(init.body))).toEqual([{ draft: { steps: [] }, automationId: 'a1' }, { draft: { steps: [] } }])
    expect(calls[0][0]).toBe('http://127.0.0.1:4242/validate-steps')
  })
})
