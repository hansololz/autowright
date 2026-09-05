// §7 execution view — the STEPS rail + LOGS pane that the execution page and
// the §11 test-run modal both render: the "Setup log" pseudo-row over the
// selectable step rows, the selection and auto-follow rules, the lazy
// per-selection log fetch, live streaming with auto-scroll, and the 2000-line
// cap. One run UI, two homes: the rail holds only the step rows in both (it is
// a selector, never a home for reference data); the modal adds its toolbar
// controls to the pane (`toolbarRight`) and a rail header of its own.
import React, { useEffect, useRef, useState } from 'react'
import { LOG_TAIL, logKey, useStore } from './store'
import { anyModalOpen, badgeOf, BLINK, EmptyLine, Eyebrow, LoadingRow, logColor, MetaChip, PULSE, ScrollArea } from './ui'
import type { Execution, ExecutionStep, LogLine } from './types'

// null = the execution-scoped log (§5 execution.ndjson)
export type LogSel = { step: number | null; attempt: number | null }

export const RAIL_WIDTH = { page: 250, modal: 280 } as const
export const MODAL_TOOLBAR = 44
/** §7 page layout: the rail's STEPS header and the LOGS pane header share this
 * minimum height (8 px of padding under a 38 px floor) so their eyebrows and
 * hairlines align whatever the pane header holds — name text, attempt pills,
 * chips — and a wrapped pane header still keeps its padding. */
export const PAGE_HEADER = 38

const rowBase: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 18px', cursor: 'pointer',
}

// §7 (the §9.2 step-navigator rule): the selected row is a plain, unfocusable,
// text-selectable block marked by the accent bar and faint fill alone; the
// other rows are buttons. A clicked row unmounts as it becomes the block, so
// no focus ring can linger on it — never a box around a row.
function RailRow({ selected, current, onSelect, children }: {
  selected: boolean; current: 'step' | 'true'; onSelect: () => void; children: React.ReactNode
}) {
  return selected ? (
    <div aria-current={current} style={{ ...rowBase, cursor: 'default', userSelect: 'text', background: 'var(--bg-active)', boxShadow: 'inset 2px 0 0 var(--accent)' }}>
      {children}
    </div>
  ) : (
    <button className="ad-btn-bare ad-hover-row ad-focus-inset" onClick={onSelect} style={rowBase}>
      {children}
    </button>
  )
}

/** §7: the "Setup log" pseudo-row above step 1 — selects execution.ndjson. */
export function ExecLogRow({ selected, onSelect }: { selected: boolean; onSelect: () => void }) {
  return (
    <RailRow selected={selected} current="true" onSelect={onSelect}>
      <i className="fa-solid fa-terminal" style={{ fontSize: 9, width: 8, color: 'var(--text-faint)', flex: 'none' }} />
      <span style={{ flex: 1, fontSize: 11.5, color: 'var(--text-faint)', fontStyle: 'italic' }}>Setup log</span>
    </RailRow>
  )
}

/** Selectable step row (§7): status dot + name + attempt chip + duration —
 * no row actions; skipping lives in the header's Skip-step button. */
export function StepRow({ step, selected, onSelect }: {
  step: ExecutionStep; selected: boolean; onSelect: () => void
}) {
  const executing = step.status === 'executing'
  const dot = step.status === 'queued' ? 'var(--text-deco)' : badgeOf(step.status).c
  return (
    <RailRow selected={selected} current="step" onSelect={onSelect}>
      <span style={{
        width: 7, height: 7, borderRadius: '50%', background: dot, flex: 'none',
        animation: executing ? PULSE : 'none',
      }} />
      <span style={{
        flex: 1, fontSize: 13, fontWeight: 500, lineHeight: 1.4, minWidth: 0,
        color: step.status === 'queued' ? 'var(--text-faint)' : 'var(--text-2)',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {step.name}
      </span>
      {latestN(step) > 1 && (
        <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)', flex: 'none' }}>
          ×{latestN(step)}
        </span>
      )}
      <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)', flex: 'none' }}>{step.duration}</span>
    </RailRow>
  )
}

/** §11 test-run modal setup phase: an inert step row — what will run, in
 * order — with the same geometry as the selectable rows above. */
export function PendingStepRow({ name }: { name: string }) {
  return (
    <div style={{ ...rowBase, cursor: 'default' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--text-deco)', flex: 'none' }} />
      <span style={{
        flex: 1, fontSize: 13, fontWeight: 500, lineHeight: 1.4, minWidth: 0, color: 'var(--text-faint)',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {name}
      </span>
    </div>
  )
}

// §4.5: attempt `n` is monotonic and old attempts prune — the latest entry's
// `n` is the true attempt count and the newest log's number; never the length.
export function latestN(step: ExecutionStep | undefined): number {
  const atts = step?.attempts
  return atts?.length ? atts[atts.length - 1].number : 1
}

/** §7 attempt pill — `.ad-attempt-pill` owns the resting/hover neutrals; the
 * active pill pins its status badge colors inline (beats the class hover). */
function AttemptPill({ a, active, onSelect }: {
  a: ExecutionStep['attempts'][number]; active: boolean; onSelect: () => void
}) {
  const b = badgeOf(a.status)
  return (
    <button
      className="ad-btn-bare ad-attempt-pill"
      onClick={onSelect}
      style={active ? { color: b.c, background: b.bg } : undefined}
    >
      Attempt {a.number} · {b.label}{a.duration ? ` · ${a.duration}` : ''}
    </button>
  )
}

/** The §11 modal's rail header — shared by the setup and run phases so the
 * rail never shifts when a test starts: STEPS eyebrow + mono count. */
export function RailHeader({ count }: { count: number }) {
  return (
    <div style={{
      height: MODAL_TOOLBAR, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 18px', borderBottom: '1px solid var(--hairline)',
    }}>
      <Eyebrow style={{ flex: 'none' }}>STEPS</Eyebrow>
      <span style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)' }}>{count}</span>
    </div>
  )
}

export function ExecutionView({ executionId, full, summary, layout, toolbarRight, closing }: {
  executionId: string
  /** the full record (`executionFull`), undefined while loading */
  full: Execution | undefined
  /** the header-list record — covers status/note/redaction before `full` lands */
  summary: Execution | undefined
  layout: 'page' | 'modal'
  /** modal: the run controls at the toolbar's right end */
  toolbarRight?: React.ReactNode
  /** modal: true through the card's exit animation — the flip keys go inert */
  closing?: boolean
}) {
  // Per-field selectors (UI-GUIDE): a bare useStore() would re-render on every
  // store write anywhere — including each execution.log event of every other
  // execution.
  const execLogs = useStore((s) => s.execLogs)
  const loadExecLogs = useStore((s) => s.loadExecLogs)
  const e = full ?? summary
  const steps = full?.steps ?? []
  const executing = e?.status === 'executing'
  const liveIdx = steps.findIndex((s) => s.status === 'executing')

  const [sel, setSel] = useState<LogSel | null>(null)
  const manualSel = useRef(false) // a user click stops the live auto-follow (§7)
  const logRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  // A new record resets the selection and the follow.
  useEffect(() => {
    manualSel.current = false
    stickRef.current = true
    setSel(null)
  }, [executionId])

  // Selection (§7): auto-follow the live step until the user picks a row; a
  // failed execution auto-selects the failed step's latest attempt.
  useEffect(() => {
    if (!full?.steps?.length) return
    const latest = (i: number) => latestN(full.steps![i])
    if (executing && liveIdx >= 0 && !manualSel.current) {
      if (sel?.step !== liveIdx || sel.attempt !== latest(liveIdx)) {
        setSel({ step: liveIdx, attempt: latest(liveIdx) })
      }
      return
    }
    if (sel !== null) return
    const failedIdx = full.steps.findIndex((s) => s.status === 'failed')
    const pick = failedIdx >= 0 ? failedIdx
      : [...full.steps].reduce((acc, s, i) => (s.attempts.length ? i : acc), -1)
    setSel(pick >= 0 ? { step: pick, attempt: latest(pick) } : { step: null, attempt: null })
  }, [full, executing, liveIdx]) // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch the selected log lazily (§19); live lines append via exec.log events.
  useEffect(() => {
    if (sel === null) return
    void loadExecLogs(executionId, sel.step ?? undefined, sel.attempt ?? undefined)
  }, [executionId, sel]) // eslint-disable-line react-hooks/exhaustive-deps

  const logs: LogLine[] = (sel !== null
    ? execLogs[executionId]?.[logKey(sel.step, sel.attempt)]
    : undefined) ?? []
  const liveSelected = executing && sel?.step === liveIdx && liveIdx >= 0
    && sel.attempt === latestN(steps[liveIdx])
  // §7 log cap: the store keeps only the last LOG_TAIL lines (fetched tail plus
  // trimmed live appends). Sequences are gapless from 1 (§5), so a kept head
  // past 1 means earlier lines were dropped — say so, like the §7 text preview.
  const logsTruncated = logs.length > 0 && logs[0].sequence > 1

  // Live auto-scroll — only while executing and only if the user hasn't scrolled up.
  // Keyed on the newest line, not the length: past the §7 cap the length stops
  // changing (each append trims one off the head) and the follow would freeze.
  const lastSeq = logs.length ? logs[logs.length - 1].sequence : 0
  useEffect(() => {
    const el = logRef.current
    if (el && liveSelected && stickRef.current) el.scrollTop = el.scrollHeight
  }, [logs.length, lastSeq, liveSelected])

  const selectRow = (step: number | null) => {
    manualSel.current = true
    const attempt = step === null ? null : latestN(steps[step])
    setSel({ step, attempt })
  }

  // §7 flip keys — the §9.2 step-modal shape: ← / → move the selection one
  // row through the rail's order (Setup log, then the steps), no wrap, no-op
  // at the ends and while nothing is selected. Editable targets are ignored;
  // the page's rail yields to any open modal (the keys must not flip logs
  // under a Report-issue card), the modal's rail to its own closing card. A
  // flip is the user's own selection (ends the auto-follow) and drops focus
  // from whatever holds it, so keyboard mode never draws a ring.
  // Read through refs so the listener is installed once per view, not once
  // per store write (every live log event re-renders with a fresh `steps`).
  const selRef = useRef(sel)
  selRef.current = sel
  const stepsRef = useRef(steps)
  stepsRef.current = steps
  useEffect(() => {
    if (closing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      if (e.target instanceof HTMLElement && e.target.closest('input, textarea, [contenteditable="true"]')) return
      if (layout === 'page' && anyModalOpen()) return
      const cur = selRef.current
      const count = stepsRef.current.length
      if (cur === null || count === 0) return
      // rail order as an index: 0 = Setup log, k = step k-1
      const at = cur.step === null ? 0 : cur.step + 1
      const to = e.key === 'ArrowLeft' ? at - 1 : at + 1
      if (to < 0 || to > count) return
      e.preventDefault()
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
      const step = to === 0 ? null : to - 1
      manualSel.current = true
      setSel({ step, attempt: step === null ? null : latestN(stepsRef.current[step]) })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [closing, layout])

  const selStep = sel?.step != null ? steps[sel.step] : undefined
  const attempts = selStep?.attempts ?? []
  const modal = layout === 'modal'

  const redactNote = e?.redactedSecrets && (
    <MetaChip>
      <i className="fa-solid fa-key" style={{ fontSize: 8.5 }} />
      secrets redacted: {e.redactedSecrets.join(', ')}
    </MetaChip>
  )

  const rail = (
    <div style={{
      paddingBottom: modal ? 0 : 14, minWidth: 0,
      borderRight: `1px solid var(--${modal ? 'hairline-dim' : 'hairline'})`,
      ...(modal ? { width: RAIL_WIDTH.modal, flex: 'none', display: 'flex', flexDirection: 'column', minHeight: 0 } : {}),
    }}>
      {modal ? (
        <RailHeader count={steps.length} />
      ) : (
        <div style={{ minHeight: PAGE_HEADER, display: 'flex', alignItems: 'center', padding: '8px 18px', borderBottom: '1px solid var(--hairline)' }}>
          <Eyebrow>STEPS</Eyebrow>
        </div>
      )}
      {(() => {
        const rows = !full ? (
          <LoadingRow label="Loading steps…" style={{ padding: '14px 18px' }} />
        ) : steps.length === 0 ? (
          <EmptyLine>
            {e?.note ? `Nothing executed — ${e.note}.` : 'Nothing executed.'}
          </EmptyLine>
        ) : (
          <>
            <ExecLogRow
              selected={sel !== null && sel.step === null}
              onSelect={() => selectRow(null)}
            />
            {steps.map((s, i) => (
              <StepRow
                key={i}
                step={s}
                selected={sel?.step === i}
                onSelect={() => selectRow(i)}
              />
            ))}
          </>
        )
        return modal ? (
          <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
            <div style={{ padding: '6px 0 12px' }}>{rows}</div>
          </ScrollArea>
        ) : rows
      })()}
    </div>
  )

  const pane = (
    <div style={{ background: 'var(--bg-code)', minWidth: 0, display: 'flex', flexDirection: 'column', flex: modal ? 1 : undefined, minHeight: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: modal ? 'nowrap' : 'wrap',
        borderBottom: '1px solid var(--hairline)',
        ...(modal
          ? { height: MODAL_TOOLBAR, flex: 'none', padding: '0 14px 0 18px' }
          : { minHeight: PAGE_HEADER, padding: '8px 18px' }),
      }}>
        {/* §7 header: "LOG k OF n" counter (the §9.2 modal's STEP N OF M idiom) + the
            step's name in the modal's dim mono; the Setup log is not one of the n
            logs, so the pseudo-row keeps its plain eyebrow */}
        {sel?.step != null ? (
          <>
            <Eyebrow style={{ flex: 'none', whiteSpace: 'nowrap' }}>
              LOG {sel.step + 1} OF {steps.length}{liveSelected ? ' · LIVE' : ''}
            </Eyebrow>
            <span style={{
              font: '400 11px var(--mono)', color: 'var(--text-deco)', flex: '0 1 auto', minWidth: 0,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {selStep?.name}
            </span>
          </>
        ) : (
          <Eyebrow style={{ flex: 'none', whiteSpace: 'nowrap' }}>Setup log</Eyebrow>
        )}
        {/* §7 attempt control — pills only when the step retried */}
        {attempts.length > 1 && (
          <span style={{ display: 'inline-flex', gap: 4, flex: 'none' }}>
            {attempts.map((a) => (
              <AttemptPill
                key={a.number}
                a={a}
                active={sel?.attempt === a.number}
                onSelect={() => { manualSel.current = true; setSel({ step: sel!.step, attempt: a.number }) }}
              />
            ))}
          </span>
        )}
        {redactNote}
        <div style={{ flex: 1 }} />
        {toolbarRight}
      </div>
      <ScrollArea
        scrollRef={logRef}
        onScroll={() => {
          const el = logRef.current
          if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
        wrapStyle={modal ? { flex: 1, minHeight: 0 } : { flex: 1, maxHeight: 420 }}
        style={{
          ...(modal ? {} : { maxHeight: 420 }),
          padding: modal ? '13px 28px 16px 18px' : '13px 18px',
          fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.75,
        }}
        testId="execution-log"
      >
        {!full ? (
          <LoadingRow label="Loading log…" />
        ) : (
          <>
            {/* §7 truncation notice — the dropped lines are the oldest,
                so it sits above the kept tail (and clear of the live
                auto-scroll at the bottom). */}
            {logsTruncated && (
              <div style={{
                marginBottom: 8, fontFamily: 'var(--sans)', fontSize: 11.5,
                lineHeight: 1.6, color: 'var(--text-faint)',
              }}>
                Truncated — showing the last {LOG_TAIL} lines. The full log is on disk.
              </div>
            )}
            {logs.map((l) => (
              <div key={l.sequence} style={{ display: 'flex', gap: 12 }}>
                <span style={{ color: 'var(--text-deco)', flex: 'none' }}>{l.time}</span>
                <span style={{
                  color: logColor(l.kind), whiteSpace: 'pre-wrap', minWidth: 0,
                  fontStyle: l.kind === 'sys' ? 'italic' : 'normal',
                }}>
                  {l.text}
                </span>
              </div>
            ))}
            {logs.length === 0 && (
              <EmptyLine style={{ padding: 0 }}>
                {steps.length === 0
                  ? 'No logs — this execution never started.'
                  : sel?.step == null
                    ? 'No setup events — installs, retries, and failures would appear here.'
                    : 'No log lines here.'}
              </EmptyLine>
            )}
            {liveSelected && (
              <span style={{
                display: 'inline-block', width: 7, height: 13, background: 'var(--cyan)',
                animation: BLINK, verticalAlign: 'middle', marginLeft: 2,
              }} />
            )}
          </>
        )}
      </ScrollArea>
    </div>
  )

  if (modal) {
    return (
      <div style={{ display: 'flex', flex: 1, minHeight: 0, minWidth: 0 }}>
        {rail}
        {pane}
      </div>
    )
  }
  // Execution card (§7): STEPS rail + LOGS pane in one card — the rail's
  // selection drives the pane, so they share a border.
  return (
    <div className="ad-card" style={{
      display: 'grid', gridTemplateColumns: `${RAIL_WIDTH.page}px 1fr`, alignItems: 'stretch', overflow: 'hidden',
    }}>
      {rail}
      {pane}
    </div>
  )
}
