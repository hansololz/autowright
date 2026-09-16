// §9.2 PARAMETERS row: local drafts + the debounce/PATCH plumbing over the
// shared presentational param controls (../../steps).
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import type { ParamDef } from '../../types'
import { MiniBadge, settingsRow, settingsRowDivided, settingsRowSub, settingsRowTitle } from '../../ui'
import { ParamValueEditor } from '../../steps'
import { runAction } from './model'

export function ParamRow({ automationId, p, last }: { automationId: string; p: ParamDef; last: boolean }) {
  const [lines, setLines] = useState<string[]>(() => [...(p.lines ?? [])])
  const [rows, setRows] = useState<{ key: string; value: string }[]>(() => (p.rows ?? []).map((r) => ({ ...r })))
  const [text, setText] = useState<string | null>(null)
  const [num, setNum] = useState<string | null>(null)
  const [tog, setTog] = useState<boolean | null>(null) // optimistic toggle — a double-click must not compute twice from stale props
  const [foc, setFoc] = useState(false)

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<unknown>(undefined)
  // The newest PATCH (plus the reload it triggers) — what a blur waits on
  // before it lets go of its draft.
  const inFlight = useRef<Promise<void> | null>(null)

  // Resync from the server value when it changes underneath (a restore, a new
  // version's defaults, an edit from another window) — but never while an edit
  // is pending or an input is focused, so typing is never clobbered.
  // `foc` rides in the deps: a value that changed while the row was focused
  // would otherwise never land, since nothing re-runs this on blur.
  const serverLines = JSON.stringify(p.lines ?? [])
  useEffect(() => {
    if (!timer.current && !foc) setLines([...(p.lines ?? [])])
  }, [serverLines, foc])
  const serverRows = JSON.stringify(p.rows ?? [])
  useEffect(() => {
    if (!timer.current && !foc) setRows((p.rows ?? []).map((r) => ({ ...r })))
  }, [serverRows, foc])
  useEffect(() => { setTog(null) }, [p.on])

  const commit = (value: unknown) => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    pending.current = undefined
    inFlight.current = runAction(automationId, async () => {
      await api.patchAutomation(automationId, { paramValues: { [p.name]: value } })
    })
    return inFlight.current
  }
  // Debounced commit: saves as the user types, without one PATCH per keystroke.
  const commitSoon = (value: unknown) => {
    pending.current = value
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { timer.current = null; commit(pending.current) }, 600)
  }
  // A pending keystroke commits now; with nothing pending, the debounce has
  // already fired and its round-trip is the one to wait on.
  const flush = () => (timer.current ? commit(pending.current) : inFlight.current)
  useEffect(() => () => { if (timer.current) { clearTimeout(timer.current); void commit(pending.current) } }, [])

  // Hold the committed draft until the PATCH (and the store refresh) settles:
  // clearing it now would flash the pre-PATCH value for the length of the
  // round-trip. A draft the user has since re-typed is left alone.
  const settle = (
    done: Promise<void> | null,
    committed: string | null,
    setDraft: React.Dispatch<React.SetStateAction<string | null>>,
  ) => {
    if (!done) { setDraft(null); return }
    void done.finally(() => setDraft((d) => (d === committed ? null : d)))
  }

  const setLinesSaved = (next: string[], now = false) => { setLines(next); now ? commit(next) : commitSoon(next) }
  const setRowsSaved = (next: { key: string; value: string }[], now = false) => { setRows(next); now ? commit(next) : commitSoon(next) }

  // §9.2 hybrid layout: compact controls (toggle/number) sit on the label's line,
  // wide editors (text/list/kv) stack below the full-width label + help.
  const compact = p.kind === 'toggle' || p.kind === 'number'
  // The §14 settings-row geometry either way; the stacked form keeps its
  // padding and divider but not the side-by-side part.
  const settings = last ? settingsRow : settingsRowDivided
  const rowStyle: React.CSSProperties = compact
    ? settings
    : { ...settings, gap: 8, flexDirection: 'column', alignItems: 'stretch' }
  const labelBlock = (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
        <span style={settingsRowTitle}>{p.label}</span>
        {p.kind === 'text' && !p.value && (
          <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NOT SET</MiniBadge>
        )}
      </div>
      <div style={settingsRowSub}>{p.help}</div>
    </div>
  )

  return (
    <div data-testid={`param-row-${p.name}`} style={rowStyle}>
      {labelBlock}
      <div style={{ minWidth: 0, display: 'flex', flex: 'none' }}>
        <ParamValueEditor
          variant="detail"
          p={p}
          on={tog ?? !!p.on}
          lines={lines}
          rows={rows}
          value={p.kind === 'number' ? (num ?? String(p.value ?? '')) : (text ?? String(p.value ?? ''))}
          setOn={() => {
            const v = !(tog ?? !!p.on)
            setTog(v)
            runAction(automationId, async () => {
              await api.patchAutomation(automationId, { paramValues: { [p.name]: v } })
            // roll the optimistic value back — the server still holds the old one
            }, { onError: () => setTog(null) })
          }}
          setLines={(next, now) => setLinesSaved(next, !!now)}
          setRows={(next, now) => setRowsSaved(next, !!now)}
          setText={(v) => { setText(v); commitSoon(v) }}
          setNumber={(s) => {
            setNum(s)
            const min = p.min ?? 0
            const v = s === '' ? min : Math.max(min, parseInt(s, 10))
            commitSoon(v)
          }}
          onFocus={() => setFoc(true)}
          // every kind clears the guard on blur — a list/kv row that left it
          // set would freeze this row's resync for the rest of its life
          onBlur={p.kind === 'number'
            ? () => { setFoc(false); settle(flush(), num, setNum) }
            : p.kind === 'text'
              ? () => { setFoc(false); settle(flush(), text, setText) }
              : () => { setFoc(false); flush() }}
        />
      </div>
    </div>
  )
}
