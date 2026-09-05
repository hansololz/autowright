// Execution page (§7): full-width Result card on top, the run's inputs
// (TRIGGER MESSAGE, PARAMETERS), then a single execution card joining the
// selectable STEPS rail and the LOGS pane — the shared `ExecutionView`
// (../executionView) — and the WORKSPACE card at the bottom; plus
// skip-live-step, Cancel / Retry / Execute again. A §6 `queued` record renders
// the waiting state instead of that body.
import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import { BackLink, Badge, EmptyNotice, Eyebrow, FailureNotice, HeaderActions, LoadingRow, MetaChip, PageLoading, PageTitle, paramSummary, PULSE, waitedLabel } from '../ui'
import { ResultSection } from '../result'
import { ExecutionView } from '../executionView'
import type { Execution, ParamDef, TriggerPayload } from '../types'

function ordinal(n: number): string {
  return ['1st', '2nd', '3rd'][n - 1] ?? `${n}th`
}

const bodyCard: React.CSSProperties = { padding: '16px 18px' }

/** §7 TRIGGER MESSAGE block — the input that fired a message-triggered
 * execution. Shared between the queued waiting state and the ordinary page,
 * so the message stays visible after promotion. */
function TriggerMessage({ payload }: { payload: TriggerPayload }) {
  const discord = payload.kind === 'discord' ? payload : null
  // §7: origin is Discord-only — names are best-effort (§6 cache), raw
  // channel id when null; an iMessage payload shows the sender alone.
  const origin = discord && (discord.channelName
    ? `#${discord.channelName}${discord.guildName ? ` · ${discord.guildName}` : ''}`
    : discord.channel)
  return (
    <div className="ad-card" style={bodyCard}>
      <Eyebrow style={{ marginBottom: 10 }}>TRIGGER MESSAGE</Eyebrow>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 2 }}>
        <div style={{
          fontSize: 12.5, color: 'var(--text-2)', minWidth: 0,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {payload.sender}
          {origin && <span style={{ color: 'var(--text-faint)' }}>{` in ${origin}`}</span>}
        </div>
        {discord && discord.messageId && (
          <a
            className="ad-btn-ghost"
            href={`https://discord.com/channels/${discord.guildId ?? '@me'}/${discord.channel}/${discord.messageId}`}
            target="_blank" rel="noopener noreferrer"
            style={{ marginLeft: 'auto', whiteSpace: 'nowrap', flex: 'none' }}
          >
            Open in Discord ↗
          </a>
        )}
      </div>
      <div style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)', marginBottom: 10 }}>
        {new Date(payload.at).toLocaleString()}
      </div>
      <div style={{
        fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.7, color: 'var(--text-muted)',
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {payload.text}
      </div>
    </div>
  )
}

/** §7 PARAMETERS card — the run's other input: the execution's snapshot of
 * its param definitions with the values as used. Read-only settings rows
 * (§14), never controls; omitted by the caller when there are no params. */
function ParametersCard({ params }: { params: ParamDef[] }) {
  return (
    <div className="ad-card">
      <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
        <Eyebrow>PARAMETERS</Eyebrow>
      </div>
      {params.map((p) => (
        <div key={p.name} style={{ display: 'flex', alignItems: 'flex-start', gap: 20, padding: '15px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>{p.label}</div>
            {p.help && <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-muted)', marginTop: 3 }}>{p.help}</div>}
          </div>
          <div style={{ flex: '0 1 50%', minWidth: 0, textAlign: 'right', fontSize: 12.5, fontWeight: 500, lineHeight: 1.5, color: 'var(--text-2)', overflowWrap: 'break-word' }}>
            {paramSummary(p)}
          </div>
        </div>
      ))}
      <div style={{ padding: '10px 18px 12px', fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)' }}>
        Values as used by this execution.
      </div>
    </div>
  )
}

/** §7 WORKSPACE card — the scratch dir the steps ran in, for inspecting what
 * a run left behind. Sits at the page's bottom so its reveal never competes
 * with the RESULT card's Show in Finder (the user-facing output). */
function WorkspaceCard({ path }: { path: string }) {
  // §9 per-OS copy rule: the reveal button's label.
  const copy = usePlatformCopy()
  return (
    <div className="ad-card" data-testid="workspace-card">
      <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
        <Eyebrow>WORKSPACE</Eyebrow>
      </div>
      <div style={{ padding: '12px 18px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            flex: 1, minWidth: 0, fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', direction: 'rtl', textAlign: 'left',
          }}>
            {path}
          </span>
          <button
            className="ad-btn-ghost"
            onClick={() => { void window.autowright?.revealPath(path) }}
            title="Opens the scratch directory the steps ran in"
            style={{ flex: 'none' }}
          >
            <i className="fa-solid fa-folder-open" style={{ fontSize: 10 }} /> {copy.reveal}
          </button>
        </div>
        <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 6 }}>
          The scratch directory the steps ran in. Shared across steps and retries, and deleted with the execution.
        </div>
      </div>
    </div>
  )
}

/** §7 queued execution body — the waiting state, in place of the RESULT card
 * and the steps/logs card. A queued record has no steps, no logs and no
 * duration, so an ordinary body would be a page of empty machinery. */
function WaitingBody({ pos, total, payload }: {
  pos: number; total: number; payload: TriggerPayload | null
}) {
  return (
    <>
      <div className="ad-card" style={{ ...bodyCard, textAlign: 'center' }}>
        <div style={{ fontSize: 13.5, fontWeight: 500, marginBottom: 4 }}>Waiting for a free slot</div>
        {pos > 0 && (
          <div style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 4 }}>
            {ordinal(pos)} of {total} waiting
          </div>
        )}
        <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
          Every slot is busy. This runs as soon as one frees up.
        </div>
      </div>
      {payload && <TriggerMessage payload={payload} />}
    </>
  )
}

export default function ExecutionPage() {
  // Per-field selectors (UI-GUIDE): a bare useStore() would re-render this page
  // on every store write anywhere — including each execution.log event of every
  // other execution.
  const executionId = useStore((s) => s.executionId)
  const executions = useStore((s) => s.executions)
  const executionFull = useStore((s) => s.executionFull)
  const automations = useStore((s) => s.automations)
  const go = useStore((s) => s.go)
  const showToast = useStore((s) => s.showToast)
  const loadExecution = useStore((s) => s.loadExecution)
  const full = executionId ? executionFull[executionId] : undefined
  const e = full ?? (executionId ? executions.find((x) => x.id === executionId) : undefined)
  const auto = e ? automations.find((a) => a.id === e.automationId) : undefined

  const [missing, setMissing] = useState(false) // fetched and truly gone (retention-purged deep link)
  // §7 in-place retry keeps the execution id — bumping this remounts the view
  // so its selection and live auto-follow start over with the new attempt.
  const [retryKey, setRetryKey] = useState(0)

  const steps = full?.steps ?? []
  const executing = e?.status === 'executing'
  const queued = e?.status === 'queued'
  const liveIdx = steps.findIndex((s) => s.status === 'executing')

  // §7: the waiting elapsed counts up while the page is open. Promotion clears
  // `queued` and the timer stops with it.
  const [, tick] = useState(0)
  useEffect(() => {
    if (!queued) return
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [queued])

  // Mount / executionId change: guard, reset, (re)fetch the full record.
  useEffect(() => {
    if (!executionId) { go('executions'); return }
    let stale = false
    setMissing(false)
    void loadExecution(executionId).then(() => {
      // loadExecution swallows the 404 — if nothing landed anywhere, the record is
      // gone (deleted by retention): show that instead of a forever-spinner. A
      // late resolution must not mark the execution the page moved on to missing.
      const st = useStore.getState()
      if (!stale && !st.executionFull[executionId] && !st.executions.some((x) => x.id === executionId)) setMissing(true)
    })
    return () => { stale = true }
  }, [executionId])

  if (!executionId) return null

  const shell = (body: React.ReactNode) => (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '20px 30px 70px' }}>
      <BackLink label="Executions" onClick={() => go('executions')} />
      {body}
    </div>
  )

  if (!e) {
    return shell(
      missing ? (
        <EmptyNotice
          title="This execution no longer exists"
          body="It was removed — most likely by retention cleanup."
          style={{ marginTop: 20 }}
        />
      ) : (
        <PageLoading />
      ),
    )
  }

  const cancelExecution = () => {
    void api.cancelExecution(e.id).catch((err: Error) => showToast(err.message))
  }
  const skipStep = (i: number) => {
    void api.skipStep(e.id, i).catch((err: Error) => showToast(err.message))
  }
  const retry = () => {
    // §7 in-place retry: same execution record — stay on this page, the
    // re-published exec.started flips the badge back to Executing.
    setRetryKey((k) => k + 1)
    void api.retryExecution(e.id).catch((err: Error) => showToast(err.message))
  }
  const executeAgain = () => {
    if (!e.automationId) return // §4.5: create-mode tests have no automation to re-execute
    const automationId = e.automationId
    void (async () => {
      try {
        const r = await api.executeNow(automationId)
        go('execution', { executionId: r.executionId })
      } catch (err) {
        showToast((err as Error).message)
      }
    })()
  }
  const canOpenAuto = !e.automationDeleted && !!auto
  // §11: tests aren't re-executable from here — iteration lives in the editor's Test card.
  const retryPrimary = e.status === 'failed' && !e.automationDeleted && !e.test
  const againQuiet = ['succeeded', 'failed', 'cancelled', 'interrupted', 'skipped'].includes(e.status) && !e.automationDeleted && !e.test
  // §7: values as used by this execution — snapshotted on the record; older records fall back
  // to the automation's current params.
  const params = (full?.params?.length ? full.params : auto?.params) ?? []
  const result = full?.result ?? null

  // §6 queue position — the queue *is* the automation's `queued` records, drained
  // oldest first, so the list gives the position without a second endpoint.
  const queue: Execution[] = queued
    ? executions
      .filter((x) => x.automationId === e.automationId && x.status === 'queued')
      .sort((a, b) => (a.queuedMs || a.startedMs) - (b.queuedMs || b.startedMs))
    : []
  const queuePos = queue.findIndex((x) => x.id === e.id) + 1

  const noResultWhy = e.status === 'executing'
    ? 'The execution is still going — the result appears when it finishes.'
    : e.status === 'failed'
      ? 'The execution failed before a result was built. The logs show what happened.'
      : e.status === 'cancelled'
        ? (steps.length === 0 && e.note
          ? `The execution was cancelled before it started — ${e.note}.`
          : 'The execution was cancelled before a result was built.')
        : 'This execution didn’t produce a result.'

  return shell(
    <>
      {/* §7: the row never wraps — the name ellipsizes so the actions stay on
        * the title line at the same height as every other page header. */}
      <PageTitle
        raw
        style={{ marginBottom: 6 }}
        right={
          <HeaderActions>
            {/* §9 rising prominence: ghosts, then danger-ghost, primary last. */}
            {executing && liveIdx >= 0 && (
              <button
                className="ad-btn-ghost"
                onClick={() => skipStep(liveIdx)}
                title="Skip this step — kills it and continues with the next one"
              >
                Skip step
              </button>
            )}
            {againQuiet && (
              <button
                className="ad-btn-ghost"
                onClick={executeAgain}
                title="Executes the automation again from the start"
              >
                Execute again
              </button>
            )}
            {/* §6: one endpoint covers both — a queued entry leaves the queue and
              * finishes skipped, a running one is killed. */}
            {(executing || queued) && (
              <button className="ad-btn-danger-ghost" onClick={cancelExecution}>
                Cancel
              </button>
            )}
            {retryPrimary && (
              <button
                className="ad-btn-primary"
                onClick={retry}
                title="Retries this execution from the failed step. Steps that already succeeded keep their results."
              >
                Retry
              </button>
            )}
          </HeaderActions>
        }
      >
        <h1
          className={`ad-h1${canOpenAuto ? ' ad-link-title' : ''}`}
          onClick={() => { if (canOpenAuto) go('automation', { automationId: e.automationId }) }}
          title={canOpenAuto ? `Open automation — ${e.automationName}` : e.automationName}
          style={{
            cursor: canOpenAuto ? 'pointer' : 'default',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}
        >
          {e.automationName}
        </h1>
        {e.test && (
          /* §11 draft test — a create-mode test has no automation by design */
          <MetaChip>Draft test</MetaChip>
        )}
        {e.automationDeleted && !e.test && (
          <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>(deleted)</span>
        )}
        <Badge
          status={e.status}
          style={executing ? { animation: PULSE } : undefined}
        />
      </PageTitle>
      <div style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-faint)', marginBottom: 20 }}>
        <span>{e.id}</span>
        {` · ${e.trigger}`}{e.versionLabel ? ` · ${e.versionLabel}` : ''}
        {/* A queued record has not started and has no duration (§7) — it reports
          * when it was queued and how long it has been waiting instead. */}
        {queued
          ? <> · queued {e.started} · waiting {waitedLabel(Date.now() - (e.queuedMs || e.startedMs))}</>
          : <> · started {e.started} · {e.duration}</>}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {queued ? (
          <WaitingBody
            pos={queuePos}
            total={queue.length}
            payload={full?.triggerPayload ?? null}
          />
        ) : (
        <>
          {e.status === 'failed' && e.error && (
            <div className="ad-anim-item">
              <FailureNotice
                error={e.error}
                // §7 Fix with AI — failed non-test executions whose automation
                // still exists; tests iterate from the editor already
                onFix={!e.test && auto ? () => {
                  useStore.setState({ fixExec: e.id })
                  go('automation', { automationId: auto.id })
                  useStore.getState().setSurface('create', 'edit')
                } : undefined}
              />
            </div>
          )}

          {/* Full-width RESULT card (§7) — the execution's outcome, above the machinery */}
          {!full ? (
            <div className="ad-card" style={{ padding: '16px 18px' }}>
              <LoadingRow label="Loading…" />
            </div>
          ) : result ? (
            <ResultSection label="RESULT" result={result} executionId={e.id} stamp={`${e.status}:${e.duration}`} />
          ) : (
            <EmptyNotice title="No result" body={noResultWhy} />
          )}

          {/* §7 TRIGGER MESSAGE — the run's input (steps read it via the §6.1
              SDK): below the outcome, above the machinery. */}
          {full?.triggerPayload && <TriggerMessage payload={full.triggerPayload} />}

          {/* §7 PARAMETERS — the run's other input, with the trigger message
              above the machinery; omitted when the execution has none. */}
          {params.length > 0 && <ParametersCard params={params} />}

          {/* Execution card (§7): the shared STEPS rail + LOGS pane */}
          <ExecutionView
            key={`${e.id}:${retryKey}`}
            executionId={e.id}
            full={full}
            summary={e}
            layout="page"
          />

          {/* §7 WORKSPACE — last, so its reveal never competes with the RESULT
              card's Show in Finder (the user-facing output) */}
          {full?.workspace && <WorkspaceCard path={full.workspace} />}
        </>
        )}
      </div>
    </>,
  )
}
