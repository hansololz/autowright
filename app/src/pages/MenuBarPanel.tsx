// Menu-bar surface (§13): 334px translucent panel — one row per automation.
import { useEffect, useRef } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { badgeOf, Eyebrow, PULSE, ScrollArea } from '../ui'

const dotColor = (s: string) => badgeOf(s).c

export default function MenuBarPanel() {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this panel on
  // every store write anywhere — every toast, every log line of every execution.
  const automations = useStore((s) => s.automations)
  const version = useStore((s) => s.version)
  const showToast = useStore((s) => s.showToast)
  const ref = useRef<HTMLDivElement>(null)

  // §13: the count matches the tray dot — failed or §4.1 overdue only.
  const failed = automations.filter((a) => a.lastStatus === 'failed'
    || (a.problems ?? []).some((p) => p.kind === 'overdue')).length
  const aggregate = failed > 0
    ? `${failed} need${failed === 1 ? 's' : ''} attention`
    : `All good · ${automations.length} automation${automations.length === 1 ? '' : 's'}`

  useEffect(() => {
    const el = ref.current
    if (!el) return
    // Border-box measure (scrollHeight excludes the 1px border), re-sent whenever
    // the panel grows — late font loads and row changes both land after mount.
    const send = () => void window.autowright?.resizePanel(Math.ceil(el.getBoundingClientRect().height))
    send()
    const ro = new ResizeObserver(send)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const openAutomation = (id: string) => { void window.autowright?.openApp(`/app?automation=${id}`) }

  return (
    <div
      ref={ref}
      style={{
        width: 334, background: 'var(--bg-panel)', borderRadius: 12,
        border: '1px solid var(--border-panel)', boxShadow: 'var(--shadow-panel)',
        overflow: 'hidden', fontFamily: 'var(--sans)',
        display: 'flex', flexDirection: 'column', maxHeight: 640,
      }}
    >
      <div style={{ padding: '11px 14px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: 'none' }}>
        <Eyebrow>Autowright</Eyebrow>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 11.5, fontWeight: 500, color: failed ? 'var(--red-text)' : 'var(--text-faint)' }}>{aggregate}</div>
      </div>
      <ScrollArea wrapStyle={{ minHeight: 0 }}>
        {automations.map((a) => {
          const live = a.live.length > 0
          const subColor = a.live.length
            ? 'var(--cyan)'
            : a.lastStatus === 'failed'
              ? 'var(--red-text)'
              : a.resultChip
                ? 'var(--accent)'
                : 'var(--text-faint)'
          return (
            <div
              key={a.id}
              className="ad-hover-row"
              role="button"
              tabIndex={0}
              onClick={() => openAutomation(a.id)}
              onKeyDown={(e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return
                if ((e.target as HTMLElement).closest('button')) return
                e.preventDefault()
                openAutomation(a.id)
              }}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                cursor: 'pointer',
              }}
            >
              <span style={{
                width: 7, height: 7, borderRadius: '50%', flex: 'none',
                background: live ? 'var(--cyan)' : dotColor(a.lastStatus),
                animation: live ? PULSE : undefined,
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {a.name}
                </div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: subColor, marginTop: 1 }}>
                  {a.live.length ? 'Executing now…' : a.resultChip ?? a.triggerChip}
                </div>
              </div>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--text-faint)', width: 56, textAlign: 'right', flex: 'none' }}>
                {a.live.length ? '' : a.lastExecutionLabel}
              </span>
              <button
                className="ad-btn-exec small"
                onClick={(e) => {
                  e.stopPropagation()
                  if (!live) void api.executeNow(a.id, undefined, 'menubar').catch((err: Error) => showToast(err.message))
                }}
                disabled={live}
                title={live ? 'Executing…' : 'Execute now'}
                aria-label={live ? 'Executing…' : 'Execute now'}
              >
                <i
                  className={live ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-play'}
                  style={{ fontSize: 8, marginLeft: live ? 0 : 1 }}
                />
              </button>
            </div>
          )
        })}
        {automations.length === 0 && (
          <div style={{ padding: '14px 14px', fontSize: 12.5, color: 'var(--text-muted)' }}>
            No automations yet — open Autowright to create one.
          </div>
        )}
      </ScrollArea>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '9px 14px', borderTop: '1px solid var(--hairline)', flex: 'none',
      }}>
        <button
          className="ad-btn-link"
          onClick={() => void window.autowright?.openApp('/app')}
        >
          Open Autowright
        </button>
        <span style={{ font: '500 11px var(--mono)', color: 'var(--text-faint)' }}>v{version || '0.1.0'}</span>
      </div>
    </div>
  )
}
