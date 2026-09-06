// §7 Filter executions modal: the Executions page's three filter dimensions
// (status, automations, started-time range) edited together as a draft and
// committed by Apply — the page's only filter control. Any applied dimension
// puts a filter line under the title and a count on the Filter button.
import React, { useState } from 'react'
import { useStore } from '../store'
import { BtnGhost, BtnPrimary, CheckBox, EmptyLine, Eyebrow, Modal, RadioRing, ScrollArea } from '../ui'

// §7: the §4.6 vocabulary in the sections' own order — the two live statuses
// (the section names, capitalized), then the five terminal statuses.
export const STATUS_FILTERS = ['executing', 'queued', 'succeeded', 'failed', 'cancelled', 'skipped', 'interrupted'] as const
export type StatusFilter = (typeof STATUS_FILTERS)[number]
export const LIVE_STATUSES: readonly StatusFilter[] = ['executing', 'queued']
export const statusLabel = (s: StatusFilter) => s[0].toUpperCase() + s.slice(1)

// §7 STARTED presets are relative — resolved against the clock at every use.
export const TIME_PRESETS = [
  { id: 'any', label: 'Any time', ms: 0 },
  { id: 'hour', label: 'Last hour', ms: 60 * 60 * 1000 },
  { id: 'day', label: 'Last 24 hours', ms: 24 * 60 * 60 * 1000 },
  { id: 'week', label: 'Last 7 days', ms: 7 * 24 * 60 * 60 * 1000 },
  { id: 'month', label: 'Last 30 days', ms: 30 * 24 * 60 * 60 * 1000 },
  { id: 'custom', label: 'Custom range', ms: 0 },
] as const
export type TimePreset = (typeof TIME_PRESETS)[number]['id']

export interface ExecutionFilters {
  statuses: StatusFilter[]                 // empty = every status
  automations: string[]                    // ids; empty = any automation
  time: { preset: TimePreset; from: string; to: string }  // from/to: datetime-local values (custom only)
}

export const DEFAULT_FILTERS: ExecutionFilters = {
  statuses: [], automations: [], time: { preset: 'any', from: '', to: '' },
}

const localMs = (value: string) => (value ? new Date(value).getTime() : NaN)

/** §7: the inclusive `startedMs` bounds a filter resolves to right now —
 *  presets against `now`, a custom range from its local datetime fields (the
 *  To bound covers the whole picked minute). Empty fields are open ends. */
export function resolveRange(time: ExecutionFilters['time'], now: number): { from?: number; to?: number } {
  if (time.preset === 'any') return {}
  if (time.preset !== 'custom') {
    const preset = TIME_PRESETS.find((p) => p.id === time.preset)!
    return { from: now - preset.ms }
  }
  const from = localMs(time.from)
  const to = localMs(time.to)
  return {
    ...(Number.isFinite(from) ? { from } : {}),
    ...(Number.isFinite(to) ? { to: to + 59_999 } : {}),
  }
}

/** A custom range whose From lands after its To can never match — the modal
 *  disables Apply on it. */
export const rangeInverted = (time: ExecutionFilters['time']) => {
  if (time.preset !== 'custom') return false
  const from = localMs(time.from)
  const to = localMs(time.to)
  return Number.isFinite(from) && Number.isFinite(to) && from > to
}

const shortStamp = (value: string) =>
  new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/** §7 filter-line label for the time filter, null when it is off. */
export function timeLabel(time: ExecutionFilters['time']): string | null {
  if (time.preset === 'any') return null
  if (time.preset !== 'custom') return TIME_PRESETS.find((p) => p.id === time.preset)!.label
  const from = Number.isFinite(localMs(time.from)) ? shortStamp(time.from) : null
  const to = Number.isFinite(localMs(time.to)) ? shortStamp(time.to) : null
  if (from && to) return `${from} – ${to}`
  if (from) return `From ${from}`
  if (to) return `Until ${to}`
  return null
}

/** How many of the three dimensions (status, automations, time) are on. */
export const activeCount = (f: ExecutionFilters) =>
  (f.statuses.length > 0 ? 1 : 0) + (f.automations.length > 0 ? 1 : 0) + (timeLabel(f.time) ? 1 : 0)

export const filtersActive = (f: ExecutionFilters) => activeCount(f) > 0

// One whole-row (or whole-cell) choice inside a filter card: a RadioRing
// (single-choice sections) or a CheckBox (automations). §14 list-row scale;
// `last` drops the divider below, `divideLeft` adds the grid's column divider.
function ChoiceRow({ on, kind, label, onClick, last, divideLeft, compact }: {
  on: boolean; kind: 'radio' | 'checkbox'; label: string; onClick: () => void
  last: boolean; divideLeft?: boolean; compact?: boolean
}) {
  return (
    <button
      role={kind}
      aria-checked={on}
      className="ad-btn-bare ad-focus-inset ad-hover-row"
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: compact ? 8 : 10, padding: compact ? '10px 12px' : '10px 14px',
        cursor: 'pointer', userSelect: 'none', borderBottom: last ? 'none' : '1px solid var(--hairline-dim)',
        borderLeft: divideLeft ? '1px solid var(--hairline-dim)' : 'none',
        font: '500 13px var(--sans)', color: 'var(--text)', textAlign: 'left', minWidth: 0,
      }}
    >
      {kind === 'radio' ? <RadioRing selected={on} size={15} /> : <CheckBox on={on} />}
      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
    </button>
  )
}

// §7: choice cells in a wide grid (wide rather than tall so the modal fits
// the card cap on the minimum window) — the grid's last row carries no
// divider below, its first column none to the left. `kind` radio = one
// value, checkbox = a set.
function ChoiceGrid({ name, options, kind, isOn, onPick, columns }: {
  name: string; options: readonly { id: string; label: string }[]; kind: 'radio' | 'checkbox'
  isOn: (id: string) => boolean; onPick: (id: string) => void; columns: number
}) {
  const lastRowFrom = options.length - (options.length % columns || columns)
  return (
    <div
      className="ad-card"
      role={kind === 'radio' ? 'radiogroup' : 'group'}
      aria-label={name}
      style={{ display: 'grid', gridTemplateColumns: `repeat(${columns}, 1fr)`, overflow: 'hidden' }}
    >
      {options.map((o, i) => (
        <ChoiceRow
          key={o.id}
          kind={kind}
          compact
          on={isOn(o.id)}
          label={o.label}
          onClick={() => onPick(o.id)}
          last={i >= lastRowFrom}
          divideLeft={i % columns !== 0}
        />
      ))}
    </div>
  )
}

const SEARCH_FROM = 8

export default function FilterModal({ filters, onApply, onClose }: {
  filters: ExecutionFilters; onApply: (f: ExecutionFilters) => void; onClose: () => void
}) {
  const automations = useStore((s) => s.automations)
  const [draft, setDraft] = useState<ExecutionFilters>(filters)
  const [q, setQ] = useState('')
  const sorted = [...automations].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  const needle = q.trim().toLowerCase()
  const shown = needle ? sorted.filter((a) => a.name.toLowerCase().includes(needle)) : sorted
  const selected = new Set(draft.automations)
  const inverted = rangeInverted(draft.time)

  const toggleAutomation = (id: string) =>
    setDraft((d) => ({
      ...d,
      automations: d.automations.includes(id) ? d.automations.filter((x) => x !== id) : [...d.automations, id],
    }))
  // Kept in the vocabulary's order so chips and queries read the same way
  // whatever order the cells were clicked in.
  const toggleStatus = (id: string) =>
    setDraft((d) => ({
      ...d,
      statuses: STATUS_FILTERS.filter((s) => d.statuses.includes(s) !== (s === id)),
    }))
  const setTime = (patch: Partial<ExecutionFilters['time']>) =>
    setDraft((d) => ({ ...d, time: { ...d.time, ...patch } }))

  return (
    <Modal onClose={onClose} width={560} ariaLabel="Filter executions">
      {(close) => (
        <>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 6px', color: 'var(--text)' }}>Filter executions</h2>
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '0 0 18px' }}>
            Narrow the list. Filters stay until you clear them or leave the page.
          </p>

          <Eyebrow style={{ margin: '0 0 8px' }}>STATUS</Eyebrow>
          <ChoiceGrid
            name="Status"
            kind="checkbox"
            options={STATUS_FILTERS.map((s) => ({ id: s, label: statusLabel(s) }))}
            isOn={(id) => draft.statuses.includes(id as StatusFilter)}
            onPick={toggleStatus}
            columns={4}
          />

          <Eyebrow style={{ margin: '16px 0 8px' }}>AUTOMATIONS</Eyebrow>
          <div className="ad-card" style={{ overflow: 'hidden' }}>
            {sorted.length > SEARCH_FROM && (
              <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--hairline-dim)' }}>
                <input
                  className="ad-input compact"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Find an automation"
                  aria-label="Find an automation"
                  spellCheck={false}
                  style={{ width: '100%', boxSizing: 'border-box' }}
                />
              </div>
            )}
            {sorted.length === 0 ? (
              <EmptyLine>No automations yet.</EmptyLine>
            ) : shown.length === 0 ? (
              <EmptyLine>No automation matches.</EmptyLine>
            ) : (
              <ScrollArea style={{ maxHeight: 220 }}>
                <div role="group" aria-label="Automations" style={{ display: 'flex', flexDirection: 'column' }}>
                  {shown.map((a, i) => (
                    <ChoiceRow
                      key={a.id}
                      kind="checkbox"
                      on={selected.has(a.id)}
                      label={a.name}
                      onClick={() => toggleAutomation(a.id)}
                      last={i === shown.length - 1}
                    />
                  ))}
                </div>
              </ScrollArea>
            )}
          </div>

          <Eyebrow style={{ margin: '16px 0 8px' }}>STARTED</Eyebrow>
          <ChoiceGrid
            name="Started"
            kind="radio"
            options={TIME_PRESETS}
            isOn={(id) => draft.time.preset === id}
            onPick={(id) => setTime({ preset: id as TimePreset })}
            columns={3}
          />
          {draft.time.preset === 'custom' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 12 }}>
              <div>
                <Eyebrow style={{ margin: '0 0 8px' }}>FROM</Eyebrow>
                <input
                  className="ad-input compact mono"
                  type="datetime-local"
                  aria-label="From"
                  value={draft.time.from}
                  onChange={(e) => setTime({ from: e.target.value })}
                  style={{ width: '100%', boxSizing: 'border-box', colorScheme: 'dark' }}
                />
              </div>
              <div>
                <Eyebrow style={{ margin: '0 0 8px' }}>TO</Eyebrow>
                <input
                  className="ad-input compact mono"
                  type="datetime-local"
                  aria-label="To"
                  value={draft.time.to}
                  onChange={(e) => setTime({ to: e.target.value })}
                  style={{ width: '100%', boxSizing: 'border-box', colorScheme: 'dark' }}
                />
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'flex-end', marginTop: 18 }}>
            <button
              className="ad-btn-text dim"
              style={{ marginRight: 'auto' }}
              onClick={() => { setQ(''); setDraft(DEFAULT_FILTERS) }}
            >
              Reset
            </button>
            <BtnGhost onClick={close}>Cancel</BtnGhost>
            <BtnPrimary
              disabled={inverted}
              title={inverted ? 'From must be before To' : undefined}
              onClick={() => { onApply(draft); close() }}
            >
              Apply
            </BtnPrimary>
          </div>
        </>
      )}
    </Modal>
  )
}
