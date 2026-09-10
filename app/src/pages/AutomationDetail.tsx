// Automation detail (§4.3/§4.4/§7, prototype "Automation detail" screen).
// Thin page shell — the section cards live in ./detail/ (§17).
import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { Automation, Execution } from '../types'
import {
  BackLink, Badge, BtnGhost, BtnPrimary, Caret, Collapse, ConfirmModal, EmptyNotice, executingToast,
  Eyebrow, FailureNotice, FlaggedResultNotice, HeaderActions, MenuItemRow, MenuRow, MetaChip, MiniBadge, Modal, Notice,
  PageTitle, PopMenu, ScrollArea, Toggle, nextIn, usePopover,
} from '../ui'
import { StepList } from '../steps'
import { nextTriggerShort, useTriggerPreview } from '../triggers'
import { ResultSection, SpecMarkdown } from '../result'
import { badgeAnim, runAction } from './detail/model'
import { ConcurrencyCard } from './detail/ConcurrencyCard'
import { MemoryCard } from './detail/MemoryCard'
import { ParamRow } from './detail/ParamRow'
import { RecentExecutions } from './detail/RecentExecutions'
import { TriggersCard } from './detail/TriggersCard'

export default function AutomationDetail() {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this page on
  // every store write anywhere — every toast, every log line of every execution.
  // §9 per-OS copy rule: the machine noun the export modal names.
  const copy = usePlatformCopy()
  const automationId = useStore((s) => s.automationId)
  const automations = useStore((s) => s.automations)
  const agents = useStore((s) => s.agents)
  const secrets = useStore((s) => s.secrets)
  const executions = useStore((s) => s.executions)
  const go = useStore((s) => s.go)
  const setSurface = useStore((s) => s.setSurface)
  const showToast = useStore((s) => s.showToast)
  const loadAuto = useStore((s) => s.loadAuto)
  const auto: Automation | undefined = automations.find((a) => a.id === automationId)

  const [verOpen, setVerOpen, verRef] = usePopover()
  const [actOpen, setActOpen, actRef] = usePopover()
  const [delAsk, setDelAsk] = useState(false)
  // §9.2 capacity popup — a click on Execute now while anything is live
  // routes through the modal; `kind` is decided at click time.
  const [execAsk, setExecAsk] = useState<'parallel' | 'queue' | 'full' | null>(null)
  const [exportAsk, setExportAsk] = useState(false)
  const [exportValues, setExportValues] = useState(true)
  const [specOpen, setSpecOpen] = useState(true)
  const [, setTick] = useState(0)

  // Full record (params/steps/latest) only comes from the full fetch.
  useEffect(() => {
    if (automationId) void loadAuto(automationId)
  }, [automationId])
  // §9.2: this automation's execution headers — the §19 /state window may hold
  // none of an old automation's rows, so RECENT EXECUTIONS and the failure
  // notice read a per-automation fetch merged with the window (window wins:
  // events land there). A failed fetch degrades to the window's rows alone.
  const [fetchedExecs, setFetchedExecs] = useState<Execution[]>([])
  useEffect(() => {
    let stale = false
    setFetchedExecs([])
    if (!automationId) return
    void api.listExecutions({ automation: automationId, limit: 200 }).then(
      (r) => { if (!stale) setFetchedExecs(r.executions) },
      () => {})
    return () => { stale = true }
  }, [automationId])
  // §4.3: refresh the countdown every 30 s.
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30000)
    return () => clearInterval(t)
  }, [])
  // automationId may point at a deleted automation.
  useEffect(() => { if (!auto) go('automations') }, [auto, go])

  // §19: per-trigger next occurrences come from POST /triggers/preview — the
  // renderer holds no trigger math (must run before the early return: hooks).
  const trigPreviews = useTriggerPreview(auto?.triggers ?? [])
  // §9.2 change badge: the current version's steps plus the stored history.
  // Also above the early return: when a delete lands while the page is open,
  // `auto` vanishes for one render and a hook below the return would make React
  // throw "Rendered fewer hooks than expected" (caught only by the boundary).
  const stepHistory = useMemo(
    () => auto
      ? [{ version: auto.version, steps: auto.steps ?? [] },
         ...(auto.versions ?? []).filter((v) => v.version !== auto.version)
           .map((v) => ({ version: v.version, steps: v.steps }))]
      : [],
    [auto?.version, auto?.steps, auto?.versions], // eslint-disable-line react-hooks/exhaustive-deps
  )

  if (!auto) return null

  // §6: `live` is a list — with maxParallel > 1 several run at once, and only a
  // full set of slots blocks a manual start.
  const liveCount = auto.live.length
  const executing = liveCount > 0
  const atCapacity = liveCount >= auto.maxParallel
  const busyToast = executingToast(auto.maxParallel, auto.maxQueued)
  // §9.2 capacity popup: the waiting count is the automation's own `queued`
  // records — the same source the ConcurrencyCard and the §7 Queued section
  // count, so the popup and the settings row can never disagree. Live rows
  // always ride the §19 window, so the store alone is complete here.
  const waiting = executions.filter((e) => e.automationId === auto.id && e.status === 'queued' && !e.test).length
  // §9.2: this automation's rows — §19 window merged with the per-automation
  // fetch (window wins by id), in the §7 canonical order.
  const windowExecs = executions.filter((e) => e.automationId === auto.id)
  const windowExecIds = new Set(windowExecs.map((e) => e.id))
  const autoExecs = [...windowExecs, ...fetchedExecs.filter((e) => !windowExecIds.has(e.id))]
    .sort((a, b) => b.startedMs - a.startedMs || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const trigs = auto.triggers
  const noTrigs = trigs.length === 0
  const allOff = auto.allTriggersOff
  const countdown = auto.nextAtMs == null ? '' : nextIn(auto)
  const nextShort = nextTriggerShort(trigs, trigPreviews)
  // §4.3: enabled app_start/message triggers have no computable next — nextAtMs stays null.
  const discordOn = trigs.some((t) => t.kind === 'discord' && t.enabled)
  const imsgOn = trigs.some((t) => t.kind === 'imessage' && t.enabled)
  const msgListening = auto.nextAtMs == null && (discordOn || imsgOn)
  const listenWhat = discordOn && imsgOn ? 'messages' : discordOn ? 'Discord messages' : 'iMessages'
  const appStartOnly = auto.nextAtMs == null && !msgListening && trigs.some((t) => t.kind === 'app_start' && t.enabled)
  // nextAtMs can be null with an enabled non-app_start trigger too (e.g. an
  // elapsed one-shot not yet consumed) — never render a dangling "next in ".
  const noNext = auto.nextAtMs == null
  const trigChip = executing ? `${auto.triggerChip} · executing now`
    : noTrigs ? 'No triggers'
    : allOff ? `${auto.triggerChip} · triggers off`
    : appStartOnly ? `${auto.triggerChip} · on app start`
    : noNext ? auto.triggerChip
    : `${auto.triggerChip} · next in ${countdown}`
  // §9 per-OS copy rule: the §13 surface is the "menu bar" on macOS, the
  // "tray" on Windows, and nothing at all on Linux — so the manual-surface
  // phrasing comes from the table whole, never interpolated.
  const trigStatusText = executing ? 'Executing now… the triggers are unchanged.'
    : noTrigs ? `No triggers set — executes only ${copy.manualOnlyLong}.`
    : allOff ? `All triggers are off — won’t execute on its own. ${copy.manualStillWorks}`
    : msgListening ? `Listening for ${listenWhat} — executes when a matching message arrives. ${copy.manualStillWorks}`
    : appStartOnly ? `Executes when this app next starts — ${copy.manualStillWorks}`
    : noNext ? `No upcoming occurrence — ${copy.manualStillWorks}`
    : `Next execution in ${countdown}${nextShort ? ` (${nextShort})` : ''} · executes even when the app is closed.`
  const trigChipOn = executing || (!allOff && !noTrigs)
  const execLabel = executing ? 'Executing…' : 'Execute now'
  const execIconCls = executing ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-play'

  const runExecute = (queue = false) => {
    void (async () => {
      try {
        const r = await api.executeNow(auto.id, undefined, 'manual', queue)
        if (queue && r.queued) showToast('Queued — runs as soon as a slot frees up.')
      } catch (err) {
        // §9.2: a raced 409 (capacity changed between popup and click) falls
        // back to the §7 busy toast.
        const er = err as Error & { status?: number }
        showToast(er.status === 409 ? busyToast : er.message)
      }
    })()
  }
  // §9.2 capacity popup: anything live never fires blind — the click opens the
  // modal whose case is decided by the store's state right now.
  const doExecute = () => {
    if (executing) {
      setExecAsk(!atCapacity ? 'parallel' : waiting < auto.maxQueued ? 'queue' : 'full')
      return
    }
    runExecute()
  }

  const confirmDelete = () => {
    setDelAsk(false)
    const nm = auto.name
    runAction(auto.id, async () => {
      await api.deleteAutomation(auto.id)
      go('automations')
      return `“${nm}” deleted — its past results stay in Executions.`
    }, { reload: false })
  }

  const discardDraft = () => {
    runAction(auto.id, async () => {
      await api.deleteDraft(auto.id)
      return `Draft discarded — v${auto.version} is unchanged.`
    })
  }

  const lr = auto.latest
  // §9.2 failure notice: latest execution (§4.1: skipped AND queued records
  // never count as latest — a waiting firing must not hide a failure notice)
  // failed → its §4.5 error leads the LATEST RESULT card.
  const latestExec = autoExecs.find((e) =>
    e.status !== 'skipped' && e.status !== 'queued' && !e.test)
  const failedExec = latestExec?.status === 'failed' && latestExec.error ? latestExec : null
  // §9.2 flagged-result notice: the latest execution SUCCEEDED and its step set
  // `result.status('attention')` — never for a failed latest (the failure notice
  // and the "Needs attention" list chip already cover that).
  const flagged = latestExec?.status === 'succeeded' && lr?.chipStatus === 'attention'
  const params = auto.params ?? []
  const steps = auto.steps ?? []
  const spec = auto.spec ?? []
  const olderVersions = (auto.versions ?? []).filter((v) => v.version !== auto.version)
  // §11 test executions are draft-scoped — never listed among real executions
  // §9.2: the card shows the 5 newest rows
  const recentExecs = autoExecs.filter((e) => !e.test).slice(0, 5)

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '20px 30px 70px' }}>
      <BackLink label="Automations" onClick={() => go('automations')} />

      {/* title row — §14 PageTitle with inline metadata (version pill, badge) */}
      <PageTitle
        raw
        style={{ marginBottom: 6 }}
        right={(
        <HeaderActions>
          <button className="ad-btn-ghost" onClick={() => setSurface('create', 'edit')}>
            Edit
          </button>
          <BtnPrimary onClick={() => doExecute()}>
            <i className={execIconCls} style={{ fontSize: 9 }} /> {execLabel}
          </BtnPrimary>
          <div ref={actRef} style={{ position: 'relative' }}>
            <button
              className="ad-btn-ghost icon"
              onClick={() => setActOpen(!actOpen)}
              title="More actions"
              aria-label="Automation actions"
            >
              <i className="fa-solid fa-ellipsis" style={{ fontSize: 12 }} />
            </button>
            <PopMenu show={actOpen} style={{ top: 'calc(100% + 6px)', right: 0, minWidth: 210 }}>
              <MenuRow onClick={() => { setActOpen(false); setExportValues(true); setExportAsk(true) }}>
                <i className="fa-solid fa-file-export" style={{ fontSize: 11, width: 14, textAlign: 'center', marginRight: 9 }} />
                Export…
              </MenuRow>
              <MenuRow danger onClick={() => { setActOpen(false); setDelAsk(true) }}>
                <i className="fa-solid fa-trash-can" style={{ fontSize: 11, width: 14, textAlign: 'center', marginRight: 9 }} />
                Delete automation…
              </MenuRow>
            </PopMenu>
          </div>
        </HeaderActions>
        )}
      >
        <h1
          className="ad-h1"
          title={auto.name}
          style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {auto.name}
        </h1>
        <div ref={verRef} style={{ position: 'relative' }}>
          <button className="ad-btn-pill" onClick={() => setVerOpen(!verOpen)}>
            <span>v{auto.version}</span>
            <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 9 }} />
          </button>
          <PopMenu
            show={verOpen}
            style={{ top: 'calc(100% + 6px)', left: 0, minWidth: 360, padding: 0, overflow: 'hidden' }}
          >
              <MenuItemRow
                header mono
                title={`v${auto.version} · current`}
                sub="What triggers and Execute now always use."
              />
              <ScrollArea style={{ maxHeight: '60vh' }}>
              {olderVersions.map((v) => (
                <MenuItemRow
                  key={v.version} mono
                  title={`v${v.version}`}
                  sub={(v.note ? `${v.note} — ` : '') + v.when}
                />
              ))}
              </ScrollArea>
              <div style={{
                padding: '10px 14px', fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)',
                background: 'var(--bg-card)',
              }}>
                Triggers and{' '}
                <i className="fa-solid fa-play" style={{ fontSize: 9 }} /> Execute now always use the current version.
                To make an older version current, open Edit and restore it from the Version menu.
              </div>
          </PopMenu>
        </div>
        <Badge status={auto.lastStatus} style={{ animation: badgeAnim(auto.lastStatus) }} />
      </PageTitle>

      {/* §9.2 lede row: the automation's description (read-only — editing lives on the edit page)
          with the §4.3 trigger status chip beside it; chip stands alone when description is empty */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 20px' }}>
        {auto.description && (
          <p
            title={auto.description}
            style={{
              font: "400 13.5px/1.6 var(--sans)", color: 'var(--text-muted)', margin: 0,
              flex: '0 1 auto', minWidth: 0,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}
          >
            {auto.description}
          </p>
        )}
        <MetaChip
          c={trigChipOn ? 'var(--accent)' : 'var(--gray)'}
          bg={trigChipOn ? 'var(--accent-chip-bg)' : 'var(--gray-bg)'}
          style={{
            flex: 'none',
            transition: 'color var(--t-hover) var(--ease-enter), background var(--t-hover) var(--ease-enter)',
          }}
        >
          <i
            className={executing ? 'fa-solid fa-spinner fa-spin' : (allOff || noTrigs) ? 'fa-solid fa-pause' : 'fa-solid fa-clock'}
            style={{ fontSize: 9 }}
          />
          {trigChip}
        </MetaChip>
      </div>

      {/* §9.2/§4.1 needs-fixing banner — pure problems rendering: no probe,
          no dismiss state; it disappears by the problems being fixed. */}
      {auto.problems.length > 0 && (
        <Notice tone="amber" size="card" className="ad-anim-item" style={{ margin: '0 0 24px' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)', marginBottom: 6 }}>
            This automation needs fixing
          </div>
          {auto.problems.map((p, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '3px 0' }}>
              <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-2)' }}>
                {p.label}
              </span>
              {/* §9.2 per-kind action: unset values live on the Secrets page;
                  references and grants (and step rewrites) live in the editor;
                  package-missing needs nothing — it installs on first run —
                  and overdue is informational: it clears by the automation
                  running again, or by its triggers changing. */}
              {p.kind === 'secret-unset' ? (
                <button className="ad-btn-text dim" onClick={() => go('secrets')} style={{ flex: 'none' }}>
                  Open Secrets
                </button>
              ) : p.kind !== 'package-missing' && p.kind !== 'overdue' ? (
                <button className="ad-btn-text dim" onClick={() => setSurface('create', 'edit')} style={{ flex: 'none' }}>
                  Edit
                </button>
              ) : null}
            </div>
          ))}
        </Notice>
      )}

      {/* §4.4 draft banner */}
      {auto.draft && (
        <Notice tone="accent" size="card" dashed className="ad-anim-item" style={{
          margin: '0 0 24px', display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <MiniBadge c="var(--accent)" bg="var(--accent-chip-bg)" style={{ flex: 'none' }}>Draft</MiniBadge>
          <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-2)' }}>
            Unsaved edit based on v{auto.version} — kept from your last edit session. Resume editing to keep working on it.
          </span>
          <button
            className="ad-btn-soft"
            onClick={() => setSurface('create', 'edit')}
            style={{ flex: 'none' }}
          >
            Resume editing
          </button>
          <button className="ad-btn-text dim" onClick={discardDraft} style={{ flex: 'none' }}>
            Discard
          </button>
        </Notice>
      )}

      {/* latest result */}
      {(lr || failedExec) ? (
        <div style={{ marginBottom: 26 }}>
          {failedExec && (
            <FailureNotice
              error={failedExec.error!}
              onView={() => go('execution', { executionId: failedExec.id })}
              // §7/§9.2 Fix with AI: open the editor seeded with this failure
              onFix={() => {
                useStore.setState({ fixExec: failedExec.id })
                setSurface('create', 'edit')
              }}
              style={{ marginBottom: lr ? 10 : 0 }}
            />
          )}
          {flagged && lr && (
            <FlaggedResultNotice
              chip={lr.chip}
              onView={() => go('execution', { executionId: lr.executionId })}
              style={{ marginBottom: 10 }}
            />
          )}
          {lr && (
            <ResultSection
              label="LATEST RESULT" result={lr} executionId={lr.executionId} compact
              stamp={latestExec ? `${latestExec.status}:${latestExec.duration}` : undefined}
            />
          )}
        </div>
      ) : (
        <EmptyNotice
          title="No executions yet"
          body="Press Execute now — the first result will appear right here."
          style={{ marginBottom: 26 }}
        />
      )}

      {/* triggers */}
      <TriggersCard auto={auto} statusText={trigStatusText} />

      {/* parameters */}
      {params.length > 0 && (
        <div style={{ marginBottom: 26 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
            <Eyebrow>PARAMETERS</Eyebrow>
            <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
              Changes apply on the next execution.
            </span>
          </div>
          <div className="ad-card" style={{ overflow: 'hidden' }}>
            {params.map((p, i) => (
              // detail→detail navigation keeps this page mounted (store deep
              // link), so the key carries the automation id — a same-named
              // param on the next automation must get a fresh ParamRow rather
              // than inherit the previous one's local edit state.
              <ParamRow key={`${auto.id}:${p.name}`} automationId={auto.id} p={p} last={i === params.length - 1} />
            ))}
          </div>
        </div>
      )}

      {/* §6 concurrency — manual executions can queue too, so every automation gets it */}
      <ConcurrencyCard auto={auto} showToast={showToast} />

      {/* recent executions */}
      <RecentExecutions execs={recentExecs} />

      {/* memory (§9.2 MEMORY card, snapshots per §6.3) */}
      <MemoryCard auto={auto} executing={executing} />

      {/* steps */}
      {steps.length > 0 && (
        <div style={{ marginBottom: 26 }}>
          <Eyebrow style={{ marginBottom: 10 }}>STEPS</Eyebrow>
          <div className="ad-card" style={{ overflow: 'hidden' }}>
            {/* §9.2: one agent tag per entry id in a step's agents list,
                resolved to the live agent's name; empty →
                the automation's first enabled agent, fallback "agent". */}
            <StepList
              variant="detail"
              steps={steps}
              agents={agents}
              secrets={secrets}
              packages={auto.packages ?? []}
              unresolvedReferences={auto.unresolvedReferences}
              history={stepHistory}
              viewing={auto.version}
              params={params}
              fallbackAgent={(() => {
                const first = auto.stepAgents.map((id) => agents.find((z) => z.id === id)).find((g) => !!g)
                return first ? (first.name || first.harness) : 'agent'
              })()}
            />
          </div>
        </div>
      )}

      {/* spec */}
      {spec.length > 0 && (
        <div>
          <div className="ad-card" style={{ overflow: 'hidden' }}>
            <button
              className="ad-btn-bare ad-hover-row ad-focus-inset"
              onClick={() => setSpecOpen(!specOpen)}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', cursor: 'pointer' }}
            >
              <Eyebrow>SPEC</Eyebrow>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-muted)' }}>{auto.specMeta}</span>
              <div style={{ flex: 1 }} />
              <span style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>
                <Caret open={specOpen} openDeg={180} closedDeg={0} /> {specOpen ? 'collapse' : 'expand'}
              </span>
            </button>
            <Collapse open={specOpen}>
              <div style={{ borderTop: '1px solid var(--hairline)', padding: '8px 18px 18px' }}>
                <SpecMarkdown blocks={spec} />
                <div style={{ marginTop: 14, fontSize: 11.5, color: 'var(--text-muted)' }}>
                  The AI regenerates the steps from this document when you edit it. Every change mints a new version — older ones live in the Version menu on the edit page.
                </div>
              </div>
            </Collapse>
          </div>
        </div>
      )}

      {exportAsk && (
        <Modal onClose={() => setExportAsk(false)} width={440}>
          {(close) => {
            // §5.1/§9.2: fetch the archive, then hand it to the native save dialog.
            const doExport = async () => {
              try {
                const data = await api.exportAutomation(auto.id, exportValues)
                close()
                const safe = auto.name.replace(/[/\\:*?"<>|]+/g, ' ').trim() || 'automation'
                const path = await window.autowright?.saveFile(`${safe}.autowright`, data)
                if (path) showToast(`Exported to ${path}.`)
              } catch (e) { showToast((e as Error).message) }
            }
            return (
              <>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 6px', color: 'var(--text)' }}>
                  Export “{auto.name}”
                </h2>
                <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '0 0 18px' }}>
                  One shareable file with the spec, steps and settings — import it on any {copy.machine} running Autowright.
                </p>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>Include parameter values</div>
                    <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 3 }}>
                      Your saved parameter values travel with the file — turn this off when sharing with someone else.
                    </div>
                  </div>
                  <Toggle on={exportValues} onChange={setExportValues} />
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'flex-end', marginTop: 18 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-faint)', marginRight: 'auto' }}>
                    <i className="fa-solid fa-lock" style={{ fontSize: 10 }} />
                    Secret values and memory are never included in the file
                  </span>
                  <BtnGhost onClick={close}>Cancel</BtnGhost>
                  <BtnPrimary onClick={() => { void doExport() }}>Export</BtnPrimary>
                </div>
              </>
            )
          }}
        </Modal>
      )}
      {execAsk === 'parallel' && (
        <ConfirmModal
          title="Already executing"
          body={`${liveCount} of ${auto.maxParallel} slots are busy. This runs now, in parallel with the execution already running.`}
          confirmLabel="Run now"
          onConfirm={() => { setExecAsk(null); runExecute() }}
          onCancel={() => setExecAsk(null)}
        />
      )}
      {execAsk === 'queue' && (
        <ConfirmModal
          title="Already executing"
          body={(
            <>
              {auto.maxParallel === 1 ? 'The slot is busy.' : `All ${auto.maxParallel} slots are busy.`}
              {' '}Queue this execution? It runs as soon as a slot frees up, and waits until you cancel it.
              <p style={{ color: 'var(--text-faint)', margin: '8px 0 0' }}>Raise Max parallel in Settings to allow more at once.</p>
            </>
          )}
          confirmLabel="Queue"
          onConfirm={() => { setExecAsk(null); runExecute(true) }}
          onCancel={() => setExecAsk(null)}
        />
      )}
      {execAsk === 'full' && (
        <Modal
          onClose={() => setExecAsk(null)} width={400} zIndex={90}
         
          role="alertdialog" ariaLabel="Execution and queue capacity is full"
        >
          {(close) => (
            <>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Execution and queue capacity is full</div>
              <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-muted)', marginBottom: 18 }}>
                {auto.maxQueued > 0 ? `${liveCount} executing, ${waiting} waiting.` : `${liveCount} executing.`}
                {' '}Raise Max parallel or Max queued in Settings below to allow more.
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <BtnGhost onClick={close}>OK</BtnGhost>
              </div>
            </>
          )}
        </Modal>
      )}
      {delAsk && (
        <ConfirmModal
          title="Delete this automation?"
          body={(
            <>
              <span style={{ fontWeight: 500, color: 'var(--text)' }}>{auto.name}</span>
              {' '}will be deleted — its triggers stop, and its versions and memory go with it. Past results stay in Executions.
              {executing && (
                <p style={{ color: 'var(--amber)', margin: '8px 0 0' }}>An execution is in progress — deleting cancels it.</p>
              )}
            </>
          )}
          confirmLabel="Delete automation"
          danger
          onConfirm={confirmDelete}
          onCancel={() => setDelAsk(false)}
        />
      )}
    </div>
  )
}
