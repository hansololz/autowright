// §11 test-run modal: the one surface for setting up, watching, and reading a
// draft test — the TEST card only launches it and reports the outcome. It is
// the §9.2 step-script modal's two-column frame carrying the §7 execution
// view (STEPS rail + LOGS pane): a 280 px rail on the left, the --bg-code pane
// with a fixed 44 px toolbar on the right, and a 36 px status footer across
// the card. Two phases — setup (the draft's steps as inert rows, the
// test-only values and trigger message in the pane, Run test in the toolbar)
// and run (the shared execution view with Skip step / Cancel while live, Run
// again / View execution / Analyze failure once settled). Closing never
// cancels a test; the card shows the run and reopening re-attaches.
import React, { useEffect, useState } from 'react'
import { useStore } from '../../store'
import { ParamValueEditor } from '../../steps'
import { ExecutionView, MODAL_TOOLBAR, PendingStepRow, RAIL_WIDTH, RailHeader } from '../../executionView'
import type { DraftTrigger, ParamDef, Step } from '../../types'
import { EmptyLine, Eyebrow, Modal, ProgressBar, PULSE, ScrollArea, StatusLine } from '../../ui'

const FOOTER = 36
export const TEST_MODAL_FRAME = 'clamp(440px, 680px, 82vh)'

export type MsgTrigger = Extract<DraftTrigger, { kind: 'discord' | 'imessage' }>
export type TestMock = { idx: number; text: string; sender: string }

// ---------- param value editor wrapper (§4.2 kinds — §11 test values) ----------

function ParamEditor({ p, upd }: { p: ParamDef; upd: (patch: Record<string, unknown>) => void }) {
  const mn = p.min ?? 0
  // shared presentational controls (../../steps); this wrapper owns the §11
  // setup-pane layout and the immediate value+default writes into the copy
  const valueProps = {
    p, variant: 'draft' as const,
    on: !!p.on,
    lines: p.lines ?? [],
    rows: p.rows ?? [],
    value: String(p.value ?? ''),
    setOn: (v: boolean) => upd({ on: v, default: v }),
    setLines: (next: string[]) => upd({ lines: next, default: next }),
    setRows: (next: { key: string; value: string }[]) => upd({ rows: next, default: next }),
    setText: (v: string) => upd({ value: v, default: v }),
    setNumber: (digits: string) => upd({ value: digits === '' ? '' : Number(digits), default: digits === '' ? mn : Number(digits) }),
  }
  // §14 settings-row geometry for the inline controls (toggle, number)
  if (p.kind === 'toggle' || p.kind === 'number') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, padding: '13px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ font: "600 13px var(--sans)" }}>{p.label}</div>
          <div style={{ font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)', marginTop: 3 }}>{p.help}</div>
        </div>
        <ParamValueEditor
          {...valueProps}
          onBlur={p.kind === 'number' ? () => {
            const n = typeof p.value === 'number' ? p.value : NaN
            if (Number.isNaN(n) || n < mn) upd({ value: mn, default: mn })
          } : undefined}
        />
      </div>
    )
  }
  // list / kv / text — stacked label + help over the full-width control
  return (
    <div style={{ padding: '14px 18px 15px', borderBottom: '1px solid var(--hairline-dim)' }}>
      <div style={{ font: "600 13px var(--sans)" }}>{p.label}</div>
      <div style={{ font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)', margin: '3px 0 9px' }}>{p.help}</div>
      <ParamValueEditor {...valueProps} />
    </div>
  )
}

// ---------- the modal ----------

export interface TestRunModalProps {
  steps: Step[]
  /** the record the run phase shows — the tracked test, or a resumed last test whose record still exists */
  runExecutionId: string | null
  testParams: ParamDef[] | null
  setTestParams: (f: (ps: ParamDef[] | null) => ParamDef[] | null) => void
  testMock: TestMock | null
  setTestMock: (m: TestMock) => void
  msgTriggers: MsgTrigger[]
  msgLabels: (string | undefined)[]
  mockSenderSeed: (t: DraftTrigger) => string
  /** null when Run test may start; otherwise the tooltip reason */
  runDisabledReason: string | null
  onRun: () => Promise<void>
  onCancel: () => void
  onSkip: (i: number) => void
  onViewExecution: () => void
  /** null unless the shown run settled failed */
  onAnalyze: (() => void) | null
  analyzeDisabled: boolean
  lockStyle?: React.CSSProperties
  /** §9 per-OS copy: the machine noun */
  machine: string
  onClose: () => void
}

export function TestRunModal({
  steps, runExecutionId, testParams, setTestParams, testMock, setTestMock, msgTriggers, msgLabels, mockSenderSeed,
  runDisabledReason, onRun, onCancel, onSkip, onViewExecution, onAnalyze, analyzeDisabled, lockStyle, machine, onClose,
}: TestRunModalProps) {
  const executions = useStore((s) => s.executions)
  const executionFull = useStore((s) => s.executionFull)
  const loadExecution = useStore((s) => s.loadExecution)
  const full = runExecutionId ? executionFull[runExecutionId] : undefined
  const summary = runExecutionId ? executions.find((e) => e.id === runExecutionId) : undefined
  const exec = full ?? summary
  // §11 phases: Run again returns to setup with the values intact; a new run
  // (a changed record id) lands in the run phase again.
  const [again, setAgain] = useState(false)
  useEffect(() => { setAgain(false) }, [runExecutionId])
  const phase: 'setup' | 'run' = runExecutionId && !again ? 'run' : 'setup'
  // A resumed last test may not be loaded yet — fetch the body once shown.
  useEffect(() => {
    if (runExecutionId && !executionFull[runExecutionId]) void loadExecution(runExecutionId)
  }, [runExecutionId]) // eslint-disable-line react-hooks/exhaustive-deps

  const runSteps = full?.steps ?? []
  const live = exec?.status === 'executing'
  const liveIdx = runSteps.findIndex((s) => s.status === 'executing')
  const done = runSteps.filter((s) => s.status !== 'queued' && s.status !== 'executing').length
  const failedStep = exec?.error?.step

  const note = steps.length === 0 ? null
    : testParams && testParams.length > 0 && msgTriggers.length > 0
      ? 'Values and the message apply to this test only — nothing is saved.'
      : testParams && testParams.length > 0
        ? 'These values apply to this test only — nothing is saved.'
        : msgTriggers.length > 0
          ? 'The message applies to this test only — nothing is saved.'
          : null

  const closeBtn = (close: () => void) => (
    <button className="ad-btn-icon" onClick={close} title="Close" aria-label="Close">
      <i className="fa-solid fa-xmark" />
    </button>
  )
  const toolbarBtn: React.CSSProperties = { flex: 'none', whiteSpace: 'nowrap' }

  const footer = (() => {
    if (phase === 'setup') {
      return (
        <span>Real steps execute on this {machine} — emails send, files move; memory is a scratch copy.</span>
      )
    }
    if (!exec) return <span>Loading the test…</span>
    if (live) {
      return (
        <>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--accent)', flex: 'none', animation: PULSE }} />
          <span style={{ minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--text-2)' }}>
            Executing
            {runSteps.length > 0 ? ` — step ${Math.max(liveIdx, 0) + 1} of ${runSteps.length}` : ''}
            {liveIdx >= 0 ? ` · ${runSteps[liveIdx].name}` : ''}
          </span>
          <div style={{ flex: 1 }} />
          {runSteps.length > 0 && (
            <div style={{ width: 160, flex: 'none' }}>
              <ProgressBar percent={(done / runSteps.length) * 100} />
            </div>
          )}
        </>
      )
    }
    if (exec.status === 'succeeded') return <StatusLine tone="green" label="Test succeeded — the memory copy was discarded." />
    if (exec.status === 'failed') {
      const msg = exec.error?.message ?? 'see the log'
      return (
        <StatusLine
          tone="amber"
          style={{ minWidth: 0 }}
          label={
            <span style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {failedStep ? `Test failed at step “${failedStep}” — ${msg}.` : `Test failed — ${msg}.`}
            </span>
          }
        />
      )
    }
    if (exec.status === 'cancelled') return <span style={{ color: 'var(--text-faint)' }}>Test cancelled.</span>
    return <span style={{ color: 'var(--text-faint)' }}>Test {exec.status}.</span>
  })()

  return (
    <Modal
      onClose={onClose} width={1120} ariaLabel="Test draft"
      cardStyle={{ padding: 0, width: 'min(1120px, 92vw)', overflow: 'hidden' }}
    >
      {(close, closing) => (
        <div className="ad-stepmodal" data-testid="test-modal" style={{
          height: TEST_MODAL_FRAME, display: 'flex', flexDirection: 'column', minWidth: 0,
        }}>
          {phase === 'run' && runExecutionId ? (
            <ExecutionView
              executionId={runExecutionId}
              full={full}
              summary={summary}
              layout="modal"
              closing={closing}
              toolbarRight={
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 'none', marginLeft: 6 }}>
                  {live ? (
                    <>
                      {liveIdx >= 0 && (
                        <button
                          className="ad-btn-text dim small ad-focus-inset" style={toolbarBtn}
                          onClick={() => onSkip(liveIdx)}
                          title="Skip this step — kills it and continues with the next one"
                        >
                          Skip step
                        </button>
                      )}
                      <button className="ad-btn-text small ad-focus-inset" style={toolbarBtn} onClick={onCancel}>
                        Cancel
                      </button>
                    </>
                  ) : exec ? (
                    <>
                      <button className="ad-btn-link small ad-focus-inset" style={toolbarBtn} onClick={() => setAgain(true)}>
                        Run again
                      </button>
                      <button className="ad-btn-text dim small ad-focus-inset" style={toolbarBtn} onClick={() => { onViewExecution(); close() }}>
                        View execution
                      </button>
                      {onAnalyze && (
                        <button
                          className="ad-btn-text dim small ad-focus-inset" style={toolbarBtn}
                          disabled={analyzeDisabled}
                          onClick={() => { onAnalyze(); close() }}
                        >
                          Analyze failure
                        </button>
                      )}
                    </>
                  ) : null}
                  {closeBtn(close)}
                </div>
              }
            />
          ) : (
            <div style={{ display: 'flex', flex: 1, minHeight: 0, minWidth: 0 }}>
              {/* setup rail — the draft's steps as inert rows: what will run, in order */}
              <div style={{
                width: RAIL_WIDTH.modal, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
                borderRight: '1px solid var(--hairline-dim)',
              }}>
                <RailHeader count={steps.length} />
                <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
                  <div style={{ padding: '6px 0 12px' }}>
                    {steps.length === 0 ? (
                      <EmptyLine>No steps yet — sync the workflow first.</EmptyLine>
                    ) : steps.map((s, i) => <PendingStepRow key={i} name={s.name} />)}
                  </div>
                </ScrollArea>
              </div>
              {/* setup pane */}
              <div style={{ background: 'var(--bg-code)', minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <div style={{
                  height: MODAL_TOOLBAR, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
                  padding: '0 14px 0 18px', borderBottom: '1px solid var(--hairline)',
                }}>
                  <Eyebrow style={{ flex: 'none' }}>TEST DRAFT</Eyebrow>
                  <div style={{ flex: 1 }} />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 'none', marginLeft: 6 }}>
                    <button
                      className="ad-btn-link small ad-focus-inset" style={toolbarBtn}
                      disabled={runDisabledReason !== null}
                      title={runDisabledReason ?? undefined}
                      onClick={() => void onRun()}
                    >
                      <i className="fa-solid fa-play" style={{ fontSize: 10, marginRight: 5 }} />
                      Run test
                    </button>
                    {closeBtn(close)}
                  </div>
                </div>
                <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }} testId="test-setup">
                  <div style={{ paddingBottom: 12, ...lockStyle }}>
                    {note && (
                      <div style={{ padding: '14px 18px 12px', font: "400 11.5px/1.55 var(--sans)", color: 'var(--text-muted)' }}>
                        {note}
                      </div>
                    )}
                    {testParams && testParams.length > 0 && (
                      <div style={{ borderTop: '1px solid var(--hairline-dim)' }}>
                        <Eyebrow style={{ padding: '10px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                          PARAMETER VALUES · THIS TEST ONLY
                        </Eyebrow>
                        {testParams.map((p) => (
                          <ParamEditor
                            key={p.name} p={p}
                            upd={(patch) => setTestParams((ps) => ps && ps.map((x) => (x.name === p.name ? { ...x, ...patch } : x)))}
                          />
                        ))}
                      </div>
                    )}
                    {msgTriggers.length > 0 && testMock !== null && (
                      <div style={{ borderTop: '1px solid var(--hairline-dim)' }}>
                        <Eyebrow style={{ padding: '10px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                          TRIGGER MESSAGE · THIS TEST ONLY
                        </Eyebrow>
                        <div style={{ padding: '13px 18px 3px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                          {msgTriggers.length > 1 && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                              {msgTriggers.map((t, i) => (
                                <button
                                  key={i}
                                  // .ad-btn-tab owns size + resting/hover colors; aria-pressed
                                  // marks the active tab (accent text on the accent chip wash)
                                  className="ad-btn-tab"
                                  aria-pressed={i === testMock.idx}
                                  onClick={() => setTestMock({ ...testMock, idx: i, sender: mockSenderSeed(t) })}
                                >
                                  {msgLabels[i] ?? (t.kind === 'discord' ? 'Discord' : 'iMessage')}
                                </button>
                              ))}
                            </div>
                          )}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Eyebrow style={{ flex: 'none', width: 64 }}>FROM</Eyebrow>
                            <input
                              className="ad-input compact mono" value={testMock.sender}
                              onChange={(e) => setTestMock({ ...testMock, sender: e.target.value })}
                              style={{ flex: 1, minWidth: 0 }}
                            />
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Eyebrow style={{ flex: 'none', width: 64 }}>MESSAGE</Eyebrow>
                            <input
                              className="ad-input compact mono" value={testMock.text}
                              placeholder="The message that starts this test"
                              onChange={(e) => setTestMock({ ...testMock, text: e.target.value })}
                              style={{ flex: 1, minWidth: 0 }}
                            />
                          </div>
                        </div>
                        <div style={{ padding: '10px 18px', font: "400 11.5px/1.55 var(--sans)", color: 'var(--text-muted)' }}>
                          Applies to this test only — nothing is saved; leave the message empty to test without one.{' '}
                          {msgTriggers[testMock.idx]?.kind === 'discord'
                            ? 'A step’s reply() posts to the real Discord channel.'
                            : 'A step’s reply() can’t send from a mocked iMessage — it logs the failed send instead.'}
                        </div>
                      </div>
                    )}
                    {steps.length > 0 && !(testParams && testParams.length > 0) && msgTriggers.length === 0 && (
                      <EmptyLine>No parameters or message triggers — the test runs the steps as they are.</EmptyLine>
                    )}
                  </div>
                </ScrollArea>
              </div>
            </div>
          )}
          {/* §11 status footer across the card */}
          <div data-testid="test-footer" style={{
            height: FOOTER, flex: 'none', display: 'flex', alignItems: 'center', gap: 8, padding: '0 18px',
            borderTop: '1px solid var(--hairline)', font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)',
            minWidth: 0,
          }}>
            {footer}
          </div>
        </div>
      )}
    </Modal>
  )
}
