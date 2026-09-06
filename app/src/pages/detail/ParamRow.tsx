// §9.2 PARAMETERS row: local drafts + the debounce/PATCH plumbing over the
// shared presentational param controls (../../steps).
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import type { ParamDef } from '../../types'
import { MiniBadge } from '../../ui'
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

  // Resync from the server value when it changes underneath (a restore, a new
  // version's defaults, an edit from another window) — but never while an edit
  // is pending or an input is focused, so typing is never clobbered.
  const serverLines = JSON.stringify(p.lines ?? [])
  useEffect(() => {
    if (!timer.current && !foc) setLines([...(p.lines ?? [])])
  }, [serverLines])
  const serverRows = JSON.stringify(p.rows ?? [])
  useEffect(() => {
    if (!timer.current && !foc) setRows((p.rows ?? []).map((r) => ({ ...r })))
  }, [serverRows])
  useEffect(() => { setTog(null) }, [p.on])

  const commit = (value: unknown) => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    pending.current = undefined
    runAction(automationId, async () => {
      await api.patchAutomation(automationId, { paramValues: { [p.name]: value } })
    })
  }
  // Debounced commit: saves as the user types, without one PATCH per keystroke.
  const commitSoon = (value: unknown) => {
    pending.current = value
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { timer.current = null; commit(pending.current) }, 600)
  }
  const flush = () => { if (timer.current) commit(pending.current) }
  useEffect(() => () => { if (timer.current) { clearTimeout(timer.current); commit(pending.current) } }, [])

  const setLinesSaved = (next: string[], now = false) => { setLines(next); now ? commit(next) : commitSoon(next) }
  const setRowsSaved = (next: { key: string; value: string }[], now = false) => { setRows(next); now ? commit(next) : commitSoon(next) }

  // §9.2 hybrid layout: compact controls (toggle/number) sit on the label's line,
  // wide editors (text/list/kv) stack below the full-width label + help.
  const compact = p.kind === 'toggle' || p.kind === 'number'
  const labelBlock = (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{p.label}</span>
        {p.kind === 'text' && !p.value && (
          <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NOT SET</MiniBadge>
        )}
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-muted)', marginTop: 3 }}>{p.help}</div>
    </div>
  )

  return (
    <div data-testid={`param-row-${p.name}`} style={{
      padding: '15px 18px', borderBottom: last ? 'none' : '1px solid var(--hairline-dim)',
      display: 'flex', gap: compact ? 18 : 8, flexDirection: compact ? 'row' : 'column',
      alignItems: compact ? 'center' : 'stretch',
    }}>
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
            ? () => { setFoc(false); flush(); setNum(null) }
            : p.kind === 'text'
              ? () => { setFoc(false); flush(); setText(null) }
              : () => { setFoc(false); flush() }}
        />
      </div>
    </div>
  )
}
