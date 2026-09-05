// §11 BUILD card and TEST card — the top two cards of the right column.
// BUILD holds the workflow's sync state (out of sync: amber dot + reason +
// Sync now; syncing: the static "Syncing the steps with the spec…" line;
// in sync: the quiet line + faint Sync spec). TEST launches the
// test-run modal and reports the outcome (never tested / executing with
// progress / settled / a resumed last test); it owns the test-setup values
// (test-only param values, the trigger-message mock), the §8
// pendingSync/pendingTest action chaining, and the run-settled thread
// entries. Quiet when fine, loud only when blocking.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { usePlatformCopy } from '../../platformCopy'
import { useStore } from '../../store'
import { useTriggerPreview } from '../../triggers'
import type { Automation, ChatEntry, DraftTrigger, ParamDef } from '../../types'
import { Eyebrow, Spinner } from '../../ui'
import { type Rev, analyzeTestMessage, applyTestValues, serializeDraft, stepsFingerprint } from './model'
import { type MsgTrigger, type TestMock, TestRunModal } from './TestRunModal'

// §11 card buttons: compact borderless text buttons (the card-header
// treatment — never bordered or filled boxes); the class owns the padding,
// and the rows wrap so a button is never clipped.
const btnStyle: React.CSSProperties = { flex: 'none', whiteSpace: 'nowrap' }
// §11: each card body is exactly one row — status text left (a single line
// that shrinks with ellipsis, full text in its tooltip), buttons right, never
// wrapping or clipping.
const rowStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, padding: '10px 18px 12px' }
const textStyle: React.CSSProperties = {
  flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  font: "400 12.5px/1.5 var(--sans)", color: 'var(--text-muted)',
}
const btnsStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }

/** The row's toned outcome — the §14 StatusLine icon + text, ellipsized like
 * RowText so a long "Last test failed — <when>." never wraps the row. */
function ToneText({ tone, children }: { tone: 'green' | 'amber'; children: string }) {
  const color = tone === 'green' ? 'var(--green)' : 'var(--amber)'
  return (
    <>
      <i className={`fa-solid ${tone === 'green' ? 'fa-check' : 'fa-triangle-exclamation'}`} style={{ color, fontSize: 13, flex: 'none' }} />
      <RowText style={{ font: "500 13px/1.5 var(--sans)", color, marginLeft: -4 }}>{children}</RowText>
    </>
  )
}

/** A one-line status text — ellipsized, with the full text (or a longer
 * explainer) in the tooltip. */
function RowText({ children, title, style }: { children: React.ReactNode; title?: string; style?: React.CSSProperties }) {
  return <span style={{ ...textStyle, ...style }} title={title ?? (typeof children === 'string' ? children : undefined)}>{children}</span>
}

function CardHeader({ label }: { label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
      <Eyebrow>{label}</Eyebrow>
    </div>
  )
}

// ---------- BUILD card ----------

export interface BuildCardProps {
  rev: Rev
  outOfSync: boolean
  syncDisabled: boolean
  agentGap: boolean
  runSync: () => void
}

export function BuildCard({ rev, outOfSync, syncDisabled, agentGap, runSync }: BuildCardProps) {
  // §11: a sync never animates the card — while one runs (however started)
  // or a chat-armed pending sync waits to fire, the row shows the static
  // syncing line (no dot, no spinner, no detail). The sync's live surface is
  // the thread progress entry alone, so the first turn's chat → chained sync
  // → done only swaps the row's text. A failed / blocked / cancelled sync
  // leaves the workflow out of sync and the out-of-sync row renders then.
  const syncing = !!rev.syncBusy || !!rev.pendingSync
  const showOutOfSync = outOfSync && !syncing
  const outOfSyncText = rev.dirty ? 'Out of sync — steps still match the old spec.'
    : agentGap ? 'Out of sync — a step’s agent isn’t enabled.'
      : 'Out of sync — a step’s secret isn’t allowed.'
  return (
    <div className="ad-card" data-testid="build-card" style={{ overflow: 'hidden' }}>
      <CardHeader label="BUILD" />
      {showOutOfSync ? (
        <div style={rowStyle}>
          {/* §11: never a spinner here (the live surface is the thread progress
              entry) and never green — amber marks out of sync */}
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--amber)', flex: 'none' }} />
          <RowText
            style={{ font: "500 12.5px/1.5 var(--sans)", color: 'var(--text)' }}
            title={`${outOfSyncText} ${rev.dirty ? 'Sync the steps to the new spec, then review them. Saving is locked until you do — nothing ships unreviewed.'
              : agentGap ? 'Re-enable the agent, or sync the steps so they only call agents available here. Saving is locked until you do.'
                : 'Re-allow the secret, or sync the steps so they only use secrets allowed here. Saving is locked until you do.'}`}
          >
            {outOfSyncText}
          </RowText>
          {/* §11: the one accent-primary button — Sync now; disabled per Dirty
              gating (another job in flight, an old version, a live test), never
              hidden. Its own sync swaps this row for the in-sync one (above). */}
          <button
            className="ad-btn-primary small" data-testid="sync-steps"
            disabled={syncDisabled}
            onClick={runSync}
            style={btnStyle}
          >
            Sync now
          </button>
        </div>
      ) : (
        <div style={rowStyle}>
          <RowText>{syncing ? 'Syncing the steps with the spec…' : 'In sync with the spec.'}</RowText>
          {/* §11: sync access on demand — faint, disabled per Dirty gating, never hidden */}
          <button className="ad-btn-text dim" disabled={syncDisabled} onClick={runSync} style={btnStyle}>
            Sync spec
          </button>
        </div>
      )}
    </div>
  )
}

// ---------- TEST card ----------

export interface TestCardProps {
  rev: Rev
  up: (patch: Partial<Rev>) => void
  appendEntry: (e: Omit<ChatEntry, 'id' | 'at'>) => void
  isEdit: boolean
  auto: Automation | null
  outOfSync: boolean
  anyJobBusy: boolean
  busyRewrite: boolean
  viewingOld: boolean
  lockStyle?: React.CSSProperties
  runSync: () => void
  // §11 hold-and-flush: lands any held workflow chips when the old-version
  // watcher clears the pending sync silently — receipts still reach the thread
  flushHeldChips: () => void
  sendChat: (text?: string, executionId?: string) => Promise<void>
  // §11 turn action row: the chat's Test-the-draft pill bumps this counter —
  // the card starts a draft test (the current setup values, seeded defaults
  // otherwise) and opens the modal on the live run
  runTestSignal: number
  // §11: the create empty state has no draft to test — the modal closes
  isCreateEmpty: boolean
}

export function TestCard({
  rev, up, appendEntry, isEdit, auto,
  outOfSync, anyJobBusy, busyRewrite, viewingOld,
  lockStyle, runSync, flushHeldChips, sendChat, runTestSignal, isCreateEmpty,
}: TestCardProps) {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders the whole
  // card on every store write anywhere — every toast, every log line.
  const executions = useStore((s) => s.executions)
  const executionFull = useStore((s) => s.executionFull)
  const go = useStore((s) => s.go)
  const showToast = useStore((s) => s.showToast)
  const test = useStore((s) => s.test)
  const beginTest = useStore((s) => s.beginTest)
  // §9 per-OS copy rule: the machine noun the side-effects line names.
  const copy = usePlatformCopy()

  // §11 test-run modal: open/closed is card state; its setup values live
  // here too so closing the modal keeps them.
  const [modalOpen, setModalOpen] = useState(false)
  // §11 test parameter values: seeded when the modal first opens; the values
  // ride §19 `paramValues` and apply to this test only.
  const [testParams, setTestParams] = useState<ParamDef[] | null>(null)
  // §11 test trigger message: the mock rides §19 `triggerMock` only when the
  // message text is nonempty — an empty message runs the test without a payload.
  const [testMock, setTestMock] = useState<TestMock | null>(null)
  // §11 stale-outcome rule: the fingerprint of the steps the tracked test ran
  // against (null for a re-attached test — unknown, so never stale).
  const [testedFp, setTestedFp] = useState<string | null>(null)

  // §11: the tracked test is an ordinary execution record — steps/status render
  // off it (executionFull carries the body; the header list covers the gap before
  // loadExecution lands).
  const testExec = test ? executionFull[test.executionId] ?? executions.find((e) => e.id === test.executionId) : undefined
  const testLive = testExec?.status === 'executing'

  // §11 test values: seed from the automation's current values (draft default when a param
  // is new to the draft; create mode has no automation, so pure draft defaults), then the
  // drafted §8 test values (call 2's manifest `test_values`) over that base — edited
  // copies live only in this card.
  const seedTestParams = (): ParamDef[] => {
    const base = (rev?.params ?? []).map((d) => {
      const cur = (auto?.params ?? []).find((p) => p.name === d.name && p.kind === d.kind)
      if (d.kind === 'toggle') return { ...d, on: cur ? !!cur.on : !!d.default }
      if (d.kind === 'list') return { ...d, lines: cur?.lines ?? (Array.isArray(d.default) ? d.default as string[] : []) }
      if (d.kind === 'kv') return { ...d, rows: cur?.rows ?? (Array.isArray(d.default) ? d.default as { key: string; value: string }[] : []) }
      return { ...d, value: cur?.value ?? (d.default as string | number | undefined) }
    })
    return rev?.testValues ? applyTestValues(base, rev.testValues) : base
  }
  // A synced/reloaded draft may rename or retype params — drop the values.
  useEffect(() => { setTestParams(null) }, [rev.params])
  // §11 test trigger message: mock only against a message trigger in the editor's
  // list (off state irrelevant); a changed trigger list drops the mock.
  const msgTriggers = (rev.triggers ?? []).filter(
    (t): t is MsgTrigger => t.kind === 'discord' || t.kind === 'imessage')
  const mockSenderSeed = (t: DraftTrigger) => (t.kind === 'imessage' ? t.from : 'Test')
  // §19: the trigger-tab labels come from POST /triggers/preview (§4.3 — no
  // renderer trigger math); the kind name stands in until the response lands
  const msgPreviews = useTriggerPreview(msgTriggers)
  useEffect(() => { setTestMock(null) }, [rev.triggers])
  // §11: the modal never renders during the create empty state
  useEffect(() => { if (isCreateEmpty) setModalOpen(false) }, [isCreateEmpty])
  // Seeding happens only when the modal opens without prior values.
  useEffect(() => {
    if (!modalOpen) return
    if (rev.params.length > 0 && testParams === null) setTestParams(seedTestParams())
    if (msgTriggers.length > 0 && testMock === null) {
      setTestMock({ idx: 0, text: '', sender: mockSenderSeed(msgTriggers[0]) })
    }
  }, [modalOpen, testParams, testMock]) // eslint-disable-line react-hooks/exhaustive-deps
  const testTriggerMock = (m: TestMock) => {
    const t = msgTriggers[m.idx]
    if (!t || !m.text || !m.sender.trim()) return undefined
    return t.kind === 'discord'
      ? { kind: 'discord', text: m.text, sender: m.sender.trim(), channel: t.channel, secret: t.secret }
      : { kind: 'imessage', text: m.text, sender: m.sender.trim() }
  }
  const testParamValues = (ps: ParamDef[]) => Object.fromEntries(ps.map((p) => [p.name,
    p.kind === 'toggle' ? !!p.on
    : p.kind === 'list' ? (p.lines ?? [])
    : p.kind === 'kv' ? (p.rows ?? [])
    : p.kind === 'number' ? (typeof p.value === 'number' ? p.value : (p.min ?? 0))
    : String(p.value ?? ''),
  ]))
  const testSteps = (test && executionFull[test.executionId]?.steps) ?? []
  const testLiveIdx = testSteps.findIndex((s) => s.status === 'executing')

  // A live test survives leaving the editor — re-attach the card on entry.
  useEffect(() => {
    if (test) return
    // §4.5: a create-mode test record carries automationId null.
    const live = executions.find((e) => e.test && e.status === 'executing'
      && (isEdit ? e.automationId === auto?.id : !e.automationId))
    if (live) beginTest(live.id)
  }, [executions]) // eslint-disable-line react-hooks/exhaustive-deps
  const runTest = async (valuesOverride?: Record<string, unknown>) => {
    // §11: a test always runs steps that match the spec — never stale ones
    // (out of sync) and never mid-build. An old version is never synced or
    // tested (§11), so a version view can't start one either.
    if (!rev || rev.steps.length === 0 || testLive || busyRewrite || outOfSync || viewingOld) return
    try {
      // §11: with the modal never opened, drafted §8 test values still apply —
      // the run sends the seeded values (drafted map on top of the
      // stored/default base); without them the backend resolves as before
      // (stored values in edit mode, draft defaults in create).
      const values = valuesOverride ?? (testParams ? testParamValues(testParams)
        : rev.testValues && Object.keys(rev.testValues).length ? testParamValues(seedTestParams())
          : undefined)
      const mock = testMock ? testTriggerMock(testMock) : undefined
      // §11: a typed message with a blanked From must not silently run
      // without the mock — the user believes it was delivered.
      if (testMock?.text && !mock) {
        showToast('Add a From name for the test message — or clear the message to run without it.')
        return
      }
      // The tracked settled test is replaced only once the POST succeeds
      // (beginTest below) — a 409/error must not erase the last outcome.
      const fp = stepsFingerprint(rev.steps)
      const { executionId } = await api.postTest({
        draft: serializeDraft(rev),
        stepsFingerprint: fp, // §11 stale-outcome rule — stored on the summary
        ...(isEdit && auto ? { automationId: auto.id } : {}), // edit: scratch memory copies the automation's
        ...(values ? { paramValues: values } : {}), // §11 test-only values
        ...(mock ? { triggerMock: mock } : {}), // §11 test trigger message — only when text is nonempty
        enabledAgents: rev.enabledAgents, allowedSecrets: rev.allowedSecrets,
      })
      beginTest(executionId)
      setTestedFp(fp)
    } catch (e) {
      showToast((e as Error).message)
    }
  }
  const cancelTest = () => {
    if (test && testLive) void api.cancelExecution(test.executionId).catch(() => { /* already done */ })
  }
  const skipStep = (i: number) => {
    if (test && testLive) void api.skipStep(test.executionId, i).catch((err: Error) => showToast(err.message))
  }
  // §11: analysis runs only when asked, as the canned analyze chat message —
  // an ordinary §8 chat job reading the failing run's RECENT EXECUTIONS context.
  const runAnalyze = () => {
    if (!rev || !test || anyJobBusy || testLive || viewingOld) return
    void sendChat(analyzeTestMessage(copy.machine, testExec?.error?.step), test.executionId)
  }

  // §11 turn action row: the chat's Test-the-draft pill — start the test
  // right away (the same run as Run test, with the current setup values or
  // the seeded defaults) and open the modal on the live run.
  useEffect(() => {
    if (!runTestSignal) return
    void runTest()
    setModalOpen(true)
  }, [runTestSignal]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11 chat-action chaining (§8 actions.yaml): pendingSync fires as soon as
  // nothing runs; pendingTest fires once the workflow is in sync (right away,
  // or after the chained sync lands) and is dropped with a system chip when
  // the sync didn't — a chat-armed test never runs stale steps. A chat-armed
  // test never opens the modal — the agent's answer is being read.
  useEffect(() => {
    if (!rev || anyJobBusy || testLive) return
    if (!rev.pendingSync && !rev.pendingTest) return
    if (viewingOld) { up({ pendingSync: false, pendingTest: null }); flushHeldChips(); return }
    if (rev.pendingSync) {
      up({ pendingSync: false })
      runSync()
      return
    }
    if (outOfSync) {
      // chained sync failed / blocked / cancelled, or something rewrote first
      up({ pendingTest: null })
      appendEntry({ kind: 'system', icon: 'fa-rotate', text: 'Test skipped — the steps aren’t in sync with the spec.' })
      return
    }
    if (rev.steps.length === 0) { up({ pendingTest: null }); return }
    const values = rev.pendingTest?.values ?? null
    up({ pendingTest: null })
    if (values) {
      // §11: pre-fill the modal's editors and send the SAME coerced values —
      // raw yaml shapes (quoted numbers, kv mappings) would fail the backend's
      // kind check and silently fall back to defaults while the editors show
      // the coerced prefill.
      const seeded = applyTestValues(seedTestParams(), values)
      setTestParams(seeded)
      void runTest(testParamValues(seeded))
    } else {
      void runTest()
    }
  }, [rev, anyJobBusy, testLive, viewingOld, outOfSync]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11 test-settled thread anchor: when the tracked test finishes, a
  // run-settled system entry lands so follow-up chat has the run in context.
  const prevTestStatus = useRef<string | null>(null)
  useEffect(() => {
    const st = testExec?.status ?? null
    const prev = prevTestStatus.current
    prevTestStatus.current = st
    if (!rev || !st || st === prev || prev !== 'executing') return
    if (st === 'succeeded') appendEntry({ kind: 'system', icon: 'fa-vial', text: 'Test succeeded.' })
    else if (st === 'failed') {
      appendEntry({
        kind: 'system',
        icon: 'fa-vial',
        text: `Test failed${testExec?.error?.step ? ` at step ${testExec.error.step}` : ''} — ${testExec?.error?.message ?? 'see the run'}.`,
      })
    }
  }, [testExec?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11: the TEST card gates on out of sync AND on a sync running or armed —
  // the steps are about to be rewritten, so nothing about them is testable.
  const showGate = outOfSync || rev.syncBusy || !!rev.pendingSync
  // §11 stale-outcome rule: an outcome belongs to the steps it ran against.
  // Compare the fingerprint of the tested steps with today's; unknown (a
  // re-attached test, an old summary without one) is never stale.
  const fp = stepsFingerprint(rev.steps)
  const stale = test
    ? testedFp !== null && testedFp !== fp
    : !!rev.lastTest && !!rev.lastTest.stepsFingerprint && rev.lastTest.stepsFingerprint !== fp
  // §11 state 5: a resumed last test opens the modal on its run while the
  // record still exists (retention may outlive it); a stale outcome opens
  // the setup phase for the new steps instead.
  const lastTestId = rev.lastTest?.executionId && executions.some((e) => e.id === rev.lastTest!.executionId)
    ? rev.lastTest.executionId : null
  const runExecutionId = stale ? null : test ? test.executionId : lastTestId
  // §11: Test draft never starts a test — it opens the modal; disabled under
  // the inputs lock, while an old version is viewed, and with no steps.
  const launchDisabled = busyRewrite || viewingOld || rev.steps.length === 0
  const launchBtn = (
    <button className="ad-btn-text" data-testid="test-draft-toggle" disabled={launchDisabled} onClick={() => setModalOpen(true)} style={btnStyle}>
      Test draft
    </button>
  )
  const runDisabledReason = rev.steps.length === 0 ? 'Sync the workflow first — there are no steps to test.'
    : showGate ? 'Sync first — a test executes the steps as generated from the spec.'
      : busyRewrite ? 'Wait for the current request to finish.'
        : testLive ? 'A test is already executing.'
          : viewingOld ? 'An old version is never tested — restore it first.'
            : null

  return (
    <>
      <div className="ad-card" data-testid="test-card" style={{ overflow: 'hidden' }}>
        <CardHeader label="TEST" />
        {test && testLive ? (
          /* state 1 — executing: spinner + step count; the run itself (and its
             progress bar) lives in the modal */
          <div style={rowStyle}>
            <Spinner size={13} style={{ flex: 'none' }} />
            <RowText style={{ color: 'var(--text-2)' }}>
              {!testExec ? 'Loading the test…' : `Executing${testSteps.length > 0 ? ` — step ${Math.max(testLiveIdx, 0) + 1} of ${testSteps.length}` : ''}${testLiveIdx >= 0 ? ` · ${testSteps[testLiveIdx].name}` : ''}`}
            </RowText>
            <div style={btnsStyle}>
              <button className="ad-btn-text" onClick={() => setModalOpen(true)} style={btnStyle}>
                Open test
              </button>
              <button className="ad-btn-text" onClick={cancelTest} style={btnStyle}>
                Cancel
              </button>
            </div>
          </div>
        ) : showGate ? (
          /* state 2 — out of sync, or a sync running or armed: the launcher
             disabled beside the gate text (never an old outcome mid-rewrite) */
          <div style={rowStyle}>
            <RowText>Sync the steps before testing.</RowText>
            <button className="ad-btn-text" data-testid="test-draft-toggle" disabled style={btnStyle}>
              Test draft
            </button>
          </div>
        ) : stale ? (
          /* state 3 — steps changed since the last test: the old outcome no
             longer applies; Test draft opens the setup for the new steps */
          <div style={rowStyle}>
            <RowText title="The steps were rewritten after this test — its outcome no longer applies.">
              Test the new changes.
            </RowText>
            {launchBtn}
          </div>
        ) : test ? (
          /* state 4 — settled: the short outcome; the full wording is the modal footer's */
          <div style={rowStyle}>
            {!testExec ? (
              <RowText>Loading the test…</RowText>
            ) : testExec.status === 'succeeded' ? (
              <ToneText tone="green">Test succeeded.</ToneText>
            ) : testExec.status === 'failed' ? (
              <ToneText tone="amber">Test failed.</ToneText>
            ) : (
              <RowText style={{ color: 'var(--text-faint)' }}>Test {testExec.status}.</RowText>
            )}
            <div style={btnsStyle}>
              {launchBtn}
              {/* §11: sends the canned analyze chat message — the whole repair
                  loop lives in the thread. Disabled while a job runs, never hidden. */}
              {testExec?.status === 'failed' && (
                <button className="ad-btn-text dim" disabled={anyJobBusy} onClick={runAnalyze} style={btnStyle}>
                  <i className="fa-solid fa-magnifying-glass" style={{ fontSize: 10 }} /> Analyze failure
                </button>
              )}
            </div>
          </div>
        ) : rev.lastTest ? (
          /* state 5 — persisted last-test summary (test.yaml): a resumed
             draft shows the outcome instead of throwing it away */
          <div style={rowStyle}>
            {rev.lastTest.status === 'succeeded' ? (
              <ToneText tone="green">{`Last test succeeded — ${rev.lastTest.when}.`}</ToneText>
            ) : (
              <ToneText tone="amber">{`Last test failed — ${rev.lastTest.when}.`}</ToneText>
            )}
            {launchBtn}
          </div>
        ) : (
          /* state 6 — never tested: the quiet launcher (testing never shouts —
             a failed test never blocks saving); the side-effects warning is
             the modal footer's */
          <div style={rowStyle}>
            <RowText>Not tested yet.</RowText>
            {launchBtn}
          </div>
        )}
      </div>
      {modalOpen && (
        <TestRunModal
          steps={rev.steps}
          runExecutionId={runExecutionId}
          testParams={testParams}
          setTestParams={setTestParams}
          testMock={testMock}
          setTestMock={setTestMock}
          msgTriggers={msgTriggers}
          msgLabels={msgPreviews.map((p) => p?.label)}
          mockSenderSeed={mockSenderSeed}
          runDisabledReason={runDisabledReason}
          onRun={() => runTest()}
          onCancel={cancelTest}
          onSkip={skipStep}
          onViewExecution={() => { if (runExecutionId) go('execution', { executionId: runExecutionId }) }}
          onAnalyze={test && !stale && testExec?.status === 'failed' ? runAnalyze : null}
          analyzeDisabled={anyJobBusy}
          lockStyle={lockStyle}
          machine={copy.machine}
          onClose={() => setModalOpen(false)}
        />
      )}
    </>
  )
}
