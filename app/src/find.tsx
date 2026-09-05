// §9.2 find bar — one primitive for the step-script modal ("Find in script")
// and the §7 execution view's LOGS pane ("Find in log"): the case-insensitive
// substring search over rendered lines, the <mark> highlighting, the hook that
// owns the bar's state and keeps the current match mid-pane, and the 36 px bar.
import React, { useEffect, useMemo, useRef, useState } from 'react'

const lineTextOf = (nodes: React.ReactNode[]) => nodes.map((n) =>
  typeof n === 'string' ? n : React.isValidElement<{ children?: string }>(n) ? String(n.props.children ?? '') : '').join('')

export type CodeMatch = { line: number; start: number; end: number }

// Case-insensitive substring matches over the rendered lines, in document order.
export function findInLines(lines: React.ReactNode[][], query: string): CodeMatch[] {
  const q = query.toLowerCase()
  if (!q) return []
  const out: CodeMatch[] = []
  lines.forEach((ln, line) => {
    const t = lineTextOf(ln).toLowerCase()
    for (let at = t.indexOf(q); at !== -1; at = t.indexOf(q, at + q.length)) out.push({ line, start: at, end: at + q.length })
  })
  return out
}

const MARK: React.CSSProperties = { background: 'var(--find-bg)', color: 'inherit', borderRadius: 2 }
const MARK_CURRENT: React.CSSProperties = { ...MARK, background: 'var(--find-active-bg)' }

// Wraps one line's matched ranges in <mark>s, keeping the token colors: a
// styled token is split around the match and re-wrapped in its own span.
export function markLine(nodes: React.ReactNode[], ranges: { start: number; end: number; current: boolean }[]): React.ReactNode[] {
  if (!ranges.length) return nodes
  const out: React.ReactNode[] = []
  let offset = 0
  let key = 0
  for (const node of nodes) {
    const el = React.isValidElement<{ style?: React.CSSProperties; children?: string }>(node) ? node : null
    const text = typeof node === 'string' ? node : el ? String(el.props.children ?? '') : ''
    const parts: React.ReactNode[] = []
    let pos = 0
    for (const r of ranges) {
      const s = Math.max(r.start - offset, 0)
      const e = Math.min(r.end - offset, text.length)
      if (e <= 0 || s >= text.length || s >= e || s < pos) continue
      if (s > pos) parts.push(text.slice(pos, s))
      parts.push(
        <mark key={key++} data-match={r.current ? 'current' : 'hit'} style={r.current ? MARK_CURRENT : MARK}>{text.slice(s, e)}</mark>,
      )
      pos = e
    }
    if (pos === 0) out.push(node)
    else {
      if (pos < text.length) parts.push(text.slice(pos))
      out.push(el ? <span key={key++} style={el.props.style}>{parts}</span> : <React.Fragment key={key++}>{parts}</React.Fragment>)
    }
    offset += text.length
  }
  return out
}

export type Find = {
  open: boolean
  query: string
  setQuery: (q: string) => void
  matches: CodeMatch[]
  input: React.RefObject<HTMLInputElement | null>
  show: () => void
  close: () => void
  step: (d: 1 | -1) => void
  marked: React.ReactNode[][]
  counter: string
}

/** The bar's state over `lines`, scrolling `scroller` so the current match sits
 * mid-pane (never the page). The bar and its query survive a change of
 * `resetKey` (a step flip, a log flip); the current match resets to the first
 * on a new query or a new key. */
export function useFind(lines: React.ReactNode[][], scroller: React.RefObject<HTMLElement | null>, resetKey: unknown): Find {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cur, setCur] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const matches = useMemo(() => findInLines(lines, query), [lines, query])
  useEffect(() => { setCur(0) }, [query, resetKey])
  const show = () => {
    setOpen(true)
    // an open bar refocuses and selects; a fresh one focuses once mounted
    requestAnimationFrame(() => { input.current?.focus(); input.current?.select() })
  }
  const close = () => { setOpen(false); setQuery('') }
  const step = (d: 1 | -1) => { if (matches.length) setCur((c) => (c + d + matches.length) % matches.length) }
  useEffect(() => {
    const sc = scroller.current
    const mark = sc?.querySelector<HTMLElement>('mark[data-match="current"]')
    if (!sc || !mark) return
    const r = mark.getBoundingClientRect()
    const box = sc.getBoundingClientRect()
    sc.scrollTop += r.top - box.top - sc.clientHeight / 2 + r.height / 2
  }, [cur, matches, resetKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const marked = useMemo(() => {
    if (!matches.length) return lines
    return lines.map((ln, n) => markLine(ln, matches.map((m, k) => ({ ...m, current: k === cur })).filter((m) => m.line === n)))
  }, [lines, matches, cur])
  const counter = matches.length ? `${cur + 1} of ${matches.length}` : query ? 'No matches' : ''
  return { open, query, setQuery, matches, input, show, close, step, marked, counter }
}

/** The 36 px find bar under a code-pane toolbar or the LOGS pane header. */
export function FindBar({ find, label }: { find: Find; label: string }) {
  return (
    <div data-testid="find-bar" style={{
      height: 36, flex: 'none', display: 'flex', alignItems: 'center', gap: 8,
      padding: '0 10px 0 18px', borderBottom: '1px solid var(--hairline-dim)',
    }}>
      <input
        ref={find.input}
        className="ad-input compact"
        placeholder={label}
        aria-label={label}
        value={find.query}
        autoFocus
        onChange={(e) => find.setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); find.step(e.shiftKey ? -1 : 1) }
          else if (e.key === 'Escape') { e.stopPropagation(); find.close() }
          else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.stopPropagation()
        }}
        style={{ width: 280, flex: '0 1 auto', minWidth: 0 }}
      />
      <span data-testid="find-counter" style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none', width: 72, whiteSpace: 'nowrap' }}>
        {find.counter}
      </span>
      <button className="ad-btn-icon" aria-label="Previous match" disabled={!find.matches.length} onClick={() => find.step(-1)}>
        <i className="fa-solid fa-chevron-up" />
      </button>
      <button className="ad-btn-icon" aria-label="Next match" disabled={!find.matches.length} onClick={() => find.step(1)}>
        <i className="fa-solid fa-chevron-down" />
      </button>
      <button className="ad-btn-icon" aria-label="Close find" onClick={find.close} style={{ marginLeft: 'auto' }}>
        <i className="fa-solid fa-xmark" />
      </button>
    </div>
  )
}
