// §9.3 developer log overlay: `-key toggled bottom log panel, developerMode-gated.
// Inert (no listener, never renders) while developerMode is off; polls over IPC
// every 1 s only while open. First tab (default) is the §5 request-log
// browser; the rest tail the four log files.
import React, { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { ScrollArea } from './ui'

type Tail = { name: string; text: string }

const REQUESTS = 'requests'

// Shared mono log-body style (requests pane body + file-tail body) — the
// ScrollArea scroller half; the flex sizing lives on the wrapper.
const logBodyStyle: React.CSSProperties = {
  padding: '4px 12px 10px', whiteSpace: 'pre-wrap',
  fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.5, color: 'var(--code-text)',
  wordBreak: 'break-word',
}

function editable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable
}

// §5 request-log files: descending name list (newest first) + selected file body.
function RequestsPane() {
  const [names, setNames] = useState<string[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [text, setText] = useState('')

  useEffect(() => {
    let alive = true
    const poll = async () => {
      // A rejecting bridge (main mid-restart) just skips this tick - never an
      // unhandled rejection once a second.
      let list: string[]
      try { list = (await window.autowright?.listRequestLogs()) ?? [] } catch { return }
      if (!alive) return
      setNames(list)
      setSel((s) => (s && list.includes(s) ? s : null))
    }
    void poll()
    const id = window.setInterval(() => void poll(), 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  useEffect(() => {
    if (!sel) { setText(''); return }
    let alive = true
    void window.autowright?.readRequestLog(sel).then((t) => { if (alive) setText(t ?? '') }).catch(() => {})
    return () => { alive = false }
  }, [sel])

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <ScrollArea
        wrapStyle={{ width: 280, flex: 'none', borderRight: '1px solid var(--hairline)' }}
        style={{ padding: '4px 0 10px' }}
      >
        {(names ?? []).map((n) => (
          <button
            key={n}
            className="ad-btn-bare ad-hover-row"
            onClick={() => setSel(n)}
            title={n}
            style={{
              padding: '2px 12px',
              fontFamily: 'var(--mono)', fontSize: 11.5, whiteSpace: 'nowrap',
              overflow: 'hidden', textOverflow: 'ellipsis',
              color: n === sel ? 'var(--text)' : 'var(--text-muted)',
              ...(n === sel ? { background: 'var(--bg-active)' } : null),
            }}
          >
            {n}
          </button>
        ))}
        {names && names.length === 0 && (
          <div style={{ padding: '4px 12px', fontSize: 12.5, color: 'var(--text-muted)' }}>
            No request logs yet — make a request with Developer mode on
          </div>
        )}
      </ScrollArea>
      <ScrollArea wrapStyle={{ flex: 1, minWidth: 0 }} style={logBodyStyle}>
        {sel ? text : names?.length ? 'Select a request' : ''}
      </ScrollArea>
    </div>
  )
}

// Mirror of the overlay's `open` state readable outside React (same pattern as
// ui.tsx anyModalOpen) — the §11 Esc-to-cancel handler yields to an open overlay.
let overlayOpen = false
export const devlogOverlayOpen = () => overlayOpen

export default function DevLogOverlay() {
  const developerMode = useStore((s) => s.settings?.developerMode) ?? false
  const platformOs = useStore((s) => s.platformOs)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    overlayOpen = open
    return () => { overlayOpen = false }
  }, [open])
  const [tails, setTails] = useState<Tail[] | null>(null)
  const [tab, setTab] = useState<string | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const followRef = useRef(true)

  // Turning the setting off closes an open overlay.
  useEffect(() => { if (!developerMode) setOpen(false) }, [developerMode])

  useEffect(() => {
    if (!developerMode) return
    const onKey = (e: KeyboardEvent) => {
      // e.code, not e.key: §9.3 names the physical Backquote key — on many
      // non-US layouts that key emits another glyph (or is a dead key), which
      // would make the overlay untoggleable.
      if (e.code === 'Backquote' && !editable(e.target)) {
        e.preventDefault()
        setOpen((v) => !v)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [developerMode])

  useEffect(() => {
    if (!open) { setTails(null); setTab(null); followRef.current = true; return }
    let alive = true
    const poll = async () => {
      // A rejecting bridge just skips this tick - never an unhandled rejection.
      const r = await window.autowright?.tailLogs()?.catch(() => null)
      if (alive && r) setTails(r)
    }
    void poll()
    const id = window.setInterval(() => void poll(), 1000)
    return () => { alive = false; clearInterval(id) }
  }, [open])

  // Default tab: the requests browser; file tabs show only files that exist.
  const active = tab && (tab === REQUESTS || tails?.some((t) => t.name === tab)) ? tab : REQUESTS
  const text = tails?.find((t) => t.name === active)?.text ?? ''

  // Automation-follow the tail while scrolled to the bottom; scrolling up pauses it.
  useEffect(() => {
    const el = bodyRef.current
    if (el && followRef.current) el.scrollTop = el.scrollHeight
  }, [text, active])

  if (!developerMode || !open) return null
  return (
    <div className="ad-anim-fade" style={{
      position: 'fixed', inset: 0, zIndex: 200,
      display: 'flex', flexDirection: 'column', background: 'var(--bg-code)',
    }}>
      {/* §9.3 per-OS: 38px top padding clears the macOS traffic lights (pinned
          at 14,14) and, on Windows, the 40px titleBarOverlay the close × would
          sit under; Linux's native frame has nothing to clear. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: platformOs === 'linux' ? '10px 10px 6px' : '38px 10px 6px', flex: 'none' }}>
        {[REQUESTS, ...(tails ?? []).map((t) => t.name)].map((name) => (
          <button
            key={name}
            className="ad-btn-tab"
            aria-pressed={name === active}
            onClick={() => { setTab(name); followRef.current = true }}
          >
            {name}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button
          className="ad-btn-icon"
          onClick={() => setOpen(false)}
          title="Close"
          aria-label="Close log overlay"
        >
          <i className="fa-solid fa-xmark" />
        </button>
      </div>
      {active === REQUESTS ? <RequestsPane /> : (
        <ScrollArea
          scrollRef={bodyRef}
          onScroll={() => {
            const el = bodyRef.current
            if (el) followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 4
          }}
          wrapStyle={{ flex: 1, minHeight: 0 }}
          style={logBodyStyle}
        >
          {text}
        </ScrollArea>
      )}
    </div>
  )
}
