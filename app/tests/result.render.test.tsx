// Render-tier tests for src/result.tsx (§7/§9.2): file-kind routing into view
// cards, the markdown/html/image/text body decisions, link and table handling
// in the shared Markdown renderer, the §7 text-preview caps, and the compact
// LATEST RESULT promotion — branch behavior uneconomical to stage live (e2e
// covers the result.md happy-path journey only).
import React from 'react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ResultFile } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { resultFile: vi.fn(() => Promise.reject(new Error('offline'))) },
}))

import { api } from '../src/api'
import { labelBytes, Markdown, ResultSection } from '../src/result'

const f = (name: string, size = '1 KB'): ResultFile => ({ name, size })
const resp = (text: string) =>
  ({ text: async () => text, blob: async () => new Blob([text]) }) as unknown as Response

// happy-dom has no object-URL store — pin the blob URL the img branch renders.
beforeAll(() => {
  URL.createObjectURL = vi.fn(() => 'blob:test') as never
  URL.revokeObjectURL = vi.fn() as never
})
afterEach(() => { cleanup(); vi.mocked(api.resultFile).mockReset().mockRejectedValue(new Error('offline')) })

const section = (files: ResultFile[], over: Partial<React.ComponentProps<typeof ResultSection>> = {}) =>
  render(
    <ResultSection
      label="RESULT" executionId="e1"
      result={{ chip: '3 changes', chipStatus: 'changes', files, path: '/tmp/results' }}
      {...over}
    />,
  )

// The FILES footer's own collapse element (the card div wrapping its title button).
const footerCollapse = (count: number) =>
  screen.getByText(`FILES · ${count}`).closest('button')!.parentElement!
    .querySelector(':scope > .ad-collapse')!

describe('Markdown (§4.5 shared renderer)', () => {
  it('links open outside (target=_blank rel=noreferrer); tables get the scroll wrap', () => {
    render(<Markdown text={'[site](https://a.com)\n\n| h |\n| - |\n| c |'} />)
    const a = screen.getByText('site') as HTMLAnchorElement
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noreferrer')
    expect(document.querySelector('.ad-md-tablewrap table')).toBeTruthy()
  })

  it('images render only from data: URLs (§4.5)', () => {
    const pixel = 'data:image/png;base64,iVBORw0KGgo='
    render(<Markdown text={`![dot](${pixel})`} />)
    const img = document.querySelector('.ad-md img') as HTMLImageElement
    expect(img).toBeTruthy()
    expect(img.getAttribute('src')).toBe(pixel)
    expect(img.getAttribute('alt')).toBe('dot')
  })

  it('any other image source renders as its alt text plus an ordinary link, never an <img> (§4.5)', () => {
    render(<Markdown text={'![Chart](https://host.example/x.png)\n\n![](https://host.example/path/y.png)'} />)
    expect(document.querySelector('.ad-md img')).toBeNull()
    const links = Array.from(document.querySelectorAll('.ad-md a')) as HTMLAnchorElement[]
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://host.example/x.png', 'https://host.example/path/y.png'])
    expect(links.every((a) => a.getAttribute('target') === '_blank' && a.getAttribute('rel') === 'noreferrer')).toBe(true)
    const text = document.querySelector('.ad-md')!.textContent!
    expect(text).toContain('Chart https://host.example/x.png')
    // no alt text: the file name stands in
    expect(text).toContain('y.png https://host.example/path/y.png')
  })
})

describe('ResultSection file-kind routing (§7)', () => {
  it('renderable kinds get view cards, text is row-only, unknown says no preview', async () => {
    const bodies: Record<string, string> = {
      'result.md': '# Hello', 'page.html': '<p>hi</p>', 'chart.png': 'PNG', 'data.csv': 'a,b',
    }
    vi.mocked(api.resultFile).mockImplementation(async (_id, name) => resp(bodies[name]))
    section([f('result.md'), f('page.html'), f('chart.png'), f('data.csv'), f('raw.bin')])
    // one view card per renderable kind, labeled
    expect(screen.getByText('markdown')).toBeTruthy()
    expect(screen.getByText('web page')).toBeTruthy()
    expect(screen.getByText('image')).toBeTruthy()
    expect(screen.getByText('FILES · 5')).toBeTruthy()
    expect(footerCollapse(5).className).not.toContain('open')  // §7: footer starts collapsed
    expect(screen.getByText('no preview')).toBeTruthy()   // raw.bin never previews
    // md body renders through the shared Markdown component
    expect(await screen.findByText('Hello')).toBeTruthy()
    // html body: sandboxed iframe, no scripts, CSP + app base style injected
    await waitFor(() => expect(document.querySelector('iframe')).toBeTruthy())
    const frame = document.querySelector('iframe')!
    expect(frame.getAttribute('sandbox')).toBe('allow-same-origin allow-popups')
    expect(frame.getAttribute('srcdoc')).toContain('Content-Security-Policy')
    expect(frame.getAttribute('srcdoc')).toContain('<p>hi</p>')
    // img body: object URL into an inline img
    await waitFor(() => expect(document.querySelector('img[alt="chart.png"]')).toBeTruthy())
    expect(document.querySelector('img[alt="chart.png"]')!.getAttribute('src')).toBe('blob:test')
    // only the three open view cards fetched — collapsed rows cost no request
    expect(new Set(vi.mocked(api.resultFile).mock.calls.map((c) => c[1])))
      .toEqual(new Set(['result.md', 'page.html', 'chart.png']))
    expect(vi.mocked(api.resultFile)).not.toHaveBeenCalledWith('e1', 'data.csv')
  })

  it('compact promotes only result.md and collapses the FILES footer', () => {
    vi.mocked(api.resultFile).mockResolvedValue(resp('# Hi'))
    section([f('result.md'), f('chart.png')], { compact: true })
    expect(screen.queryByText('image')).toBeNull()        // png gets no view slot
    expect(screen.getByText('markdown')).toBeTruthy()     // the one promoted view
    expect(footerCollapse(2).className).not.toContain('open')
  })

  it('compact without a result.md shows no view and opens the footer instead', () => {
    section([f('chart.png')], { compact: true })
    expect(screen.queryByText('image')).toBeNull()
    expect(footerCollapse(1).className).toContain('open')
  })

  it('nothing renderable on the full page → the footer alone, expanded', () => {
    section([f('data.csv'), f('raw.bin')])
    expect(footerCollapse(2).className).toContain('open')
  })

  it('no files → the empty notice', () => {
    section([])
    expect(screen.getByText('The latest execution didn’t produce any result files.')).toBeTruthy()
  })
})

describe('FILES rows — text preview (§7 caps) and load failure', () => {
  it('a text row fetches on first open only and truncates past the line cap', async () => {
    const big = Array.from({ length: 2500 }, (_, i) => `row ${i}`).join('\n')
    vi.mocked(api.resultFile).mockResolvedValue(resp(big))
    section([f('data.csv')])
    expect(vi.mocked(api.resultFile)).not.toHaveBeenCalled()  // collapsed row: no request
    fireEvent.click(screen.getByText('data.csv'))
    expect(await screen.findByText('Truncated — use Show in Finder for the full file.')).toBeTruthy()
    expect(vi.mocked(api.resultFile)).toHaveBeenCalledWith('e1', 'data.csv')
  })

  it('short text renders whole, without the truncation notice', async () => {
    vi.mocked(api.resultFile).mockResolvedValue(resp('a,b\n1,2'))
    section([f('data.csv')])
    fireEvent.click(screen.getByText('data.csv'))
    await waitFor(() => expect(document.querySelector('pre')?.textContent).toBe('a,b\n1,2'))
    expect(screen.queryByText(/Truncated/)).toBeNull()
  })

  it('a failed fetch shows the couldn’t-load line with the error message', async () => {
    vi.mocked(api.resultFile).mockRejectedValue(new Error('boom'))
    section([f('result.md')])
    expect(await screen.findByText('Couldn’t load result.md — boom')).toBeTruthy()
  })

  it('a mid-run read failure does not outlive the stamp refetch (§7)', async () => {
    // A half-written file mid-execution fails the read; the execution settling
    // changes `stamp`, and the view has to come back with the bytes, not the
    // error it was showing.
    vi.mocked(api.resultFile).mockRejectedValue(new Error('half-written'))
    const body = (stamp: string) => (
      <ResultSection
        label="RESULT" executionId="e1" stamp={stamp}
        result={{ chip: '1 change', chipStatus: 'changes', files: [f('result.md')], path: '/tmp/results' }}
      />
    )
    const view = render(body('executing'))
    expect(await screen.findByText('Couldn’t load result.md — half-written')).toBeTruthy()

    vi.mocked(api.resultFile).mockResolvedValue(resp('# Done'))
    view.rerender(body('succeeded'))
    expect(await screen.findByText('Done')).toBeTruthy()
    expect(screen.queryByText(/Couldn’t load/)).toBeNull()
  })
})

describe('render cap (§7: markdown and HTML stop at 1 MB)', () => {
  const NOTE = 'Too large to render here - use Show in Finder for the full file.'

  it('a listed size past the cap shows the note and never fetches', async () => {
    section([f('result.md', '50.0 MB'), f('page.html', '1.2 MB')])
    expect(await screen.findAllByText(NOTE)).toHaveLength(2)
    expect(vi.mocked(api.resultFile)).not.toHaveBeenCalled()
  })

  it('the compact LATEST RESULT card refuses an oversize result.md the same way', async () => {
    section([f('result.md', '3.0 MB')], { compact: true })
    expect(await screen.findByText(NOTE)).toBeTruthy()
    expect(vi.mocked(api.resultFile)).not.toHaveBeenCalled()
  })

  it('a fetched body past the cap shows the note instead of the render', async () => {
    vi.mocked(api.resultFile).mockResolvedValue(resp('#'.repeat(1_000_001)))
    section([f('result.md', '1 KB')])
    expect(await screen.findByText(NOTE)).toBeTruthy()
  })

  it('a body under the cap still renders', async () => {
    vi.mocked(api.resultFile).mockResolvedValue(resp('# Small'))
    section([f('result.md', '976.0 KB')])
    expect(await screen.findByText('Small')).toBeTruthy()
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('labelBytes reads the backend size label back into bytes', () => {
    expect(labelBytes('12 B')).toBe(12)
    expect(labelBytes('1.0 KB')).toBe(1024)
    expect(labelBytes('2.0 MB')).toBe(2 * 1024 * 1024)
    expect(Number.isNaN(labelBytes('computing'))).toBe(true)
  })
})
