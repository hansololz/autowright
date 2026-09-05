// §8/§11 job orchestration for the create/edit editor: one hook owning the
// POST /drafts + poll lifecycle for both job modes (chat, sync)
// and every cancel path. All jobs run through one {start, cancel} core sharing
// jobIdRef / cancelGenRef / stopPoll, so the gen-guard (a cancel landing while
// the POST is in flight), the staleness guard on slow poll ticks, and the
// leave-the-page cleanup behave identically no matter how a job was started.
import { useEffect, useRef } from 'react'
import { api } from '../../api'
import { useStore } from '../../store'
import type { Automation, Blocker, ChatEntry, DraftPayload, SpecBlock } from '../../types'
import {
  type Rev, TRIGGER_SETUP_TEXT, answerHeader, applyTriggerOps, chatSinceBoundary, coerceParamValue, jobStageTitle,
  mergeDraftTriggers,
  needsMessageTriggerSetup, newEntry, persistChat,
  sameTriggerList, serializeDraft, stageDisplayTitle, stageDoingBullet,
} from './model'

interface PollHandlers {
  onDone: (d: DraftPayload) => void
  onFail: (msg: string, detail?: string[]) => void
  onCancelled?: () => void
  // §8: `notes` is the blocker response's optional notes.md — applied by every
  // handler like a chat notes rewrite, so a blocked build keeps what it learned.
  onBlocked?: (blockers: Blocker[], at: 'steps' | 'chat', spec: SpecBlock[] | null, diagnosed: boolean, notes: string | null) => void
  onPlan?: (plan: string) => void // §11: chat job's pre-marker prose, at the flip
}

export interface DraftJobDeps {
  rev: Rev | null
  setRev: React.Dispatch<React.SetStateAction<Rev | null>>
  up: (patch: Partial<Rev>) => void
  isEdit: boolean
  auto: Automation | null
  agentId: string | null
  showToast: (msg: string, ms?: number) => void
  chatText: string
  setChatText: React.Dispatch<React.SetStateAction<string>>
  // §11 gating, derived by the page: one agent job at a time, rewrites lock
  // while a test executes, old versions are read-only.
  anyJobBusy: boolean
  testLive: boolean
  viewingOld: boolean
}

export function useDraftJob(d: DraftJobDeps) {
  const {
    rev, setRev, up, isEdit, auto, agentId, showToast,
    chatText, setChatText, anyJobBusy, testLive, viewingOld,
  } = d

  const jobIdRef = useRef<string | null>(null)
  // Cancel generation: bumped by every cancel path (and unmount). A POST-then-
  // poll flow captures it before the await; if it moved while the POST was in
  // flight, the flow cancels the freshly created job instead of arming the
  // poll — otherwise a cancel that lands mid-POST is silently ignored.
  const cancelGenRef = useRef(0)
  // §11: the request that started the fresh draft's first chat turn — Start
  // over returns it to the input.
  const firstRequestRef = useRef('')
  const chatReqRef = useRef<{ text: string; entryId: string } | null>(null)
  const dirtyBeforeSync = useRef(false)
  // §11 workflow chip group (hold-and-flush): a chat response arming a sync
  // holds its staged-change chips here; the sync's settle — any outcome — or
  // the old-version watcher's silent pending-clear flushes them beneath the
  // sync trail, so the workflow group reads contiguously.
  const heldChipsRef = useRef<ChatEntry[]>([])
  const takeHeldChips = (): ChatEntry[] => {
    const held = heldChipsRef.current
    heldChipsRef.current = []
    return held
  }
  // §11: the old-version watcher clears pending sync/test silently — the held
  // receipts still land (the staging already happened at apply time).
  const flushHeldChips = () => {
    const held = takeHeldChips()
    if (held.length) setRev((r) => r && ({ ...r, chat: [...r.chat, ...held] }))
  }

  // ---- polling ----
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // §11 background continuation: true while the tracked job was re-attached
  // rather than started here — the settle's `triggers` ops then run the §19
  // sentTriggers guard (the base list may have changed while the editor was
  // away; a live-started job's inputs lock keeps it still).
  const attachedRef = useRef(false)
  const sentTriggersRef = useRef<unknown[] | null>(null)
  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  const startPoll = (jobId: string, { onDone, onFail, onCancelled, onBlocked, onPlan }: PollHandlers,
    opts?: { preSettled?: string[] }) => {
    stopPoll()
    jobIdRef.current = jobId
    let planDelivered = false
    let lastStage: string | null = null
    let lastDetail: string | null = null
    let lastEvKey = ''
    // §11 per-stage activity entries: the stages observed so far (chronological
    // — event stamps first, then the live stage) and the ones already settled
    // into the thread, so each stage lands exactly one entry.
    const seenStages: string[] = []
    // §11 re-attach reconciliation: stages already settled into the persisted
    // thread (their activity entries landed live, before the editor was left)
    // seed as settled — deduped by stage title, they never land twice.
    const settledStages = new Set<string>(opts?.preSettled ?? [])
    const noteStage = (s: string | null | undefined) => {
      if (s && !seenStages.includes(s)) seenStages.push(s)
    }
    // §8 stage timing: the newest payload's stamps, read by settleStages —
    // a stage's end is the next stage's stamp, or the settle stamp for the last.
    let lastStageTimes: { stage: string; time: number }[] = []
    let lastEndedTime: number | null = null
    // Builds (and marks settled) one activity entry per given stage, each
    // carrying its own stage-stamped slice of the feed; only the last stage of
    // a terminal batch takes the job's outcome — earlier stages finished fine.
    // §11: every stage that was on screen settles — a shown block is never
    // removed, even when its feed is empty (a bare title + check is the
    // record that the phase ran).
    const settleStages = (
      evs: { text: string; stage?: string; time?: number }[],
      stages: string[], outcome: 'done' | 'blocked' | 'failed' | null,
    ) => stages.map((s, i) => {
      settledStages.add(s)
      // §8 stamps every event; a stampless one (older payloads) belongs to
      // the batch's first stage rather than vanishing
      const sevs = evs.filter((e) => e.stage === s || (!e.stage && i === 0))
      // §11 per-step durations, frozen from the §8 stage-timing stamps: an
      // event's span runs to the next milestone (next event in the stage,
      // else the stage's end); a payload without stamps yields no duration
      // key at all (§4.4 pre-field shape).
      const si = lastStageTimes.findIndex((t) => t.stage === s)
      const start = si >= 0 ? lastStageTimes[si].time : null
      const end = si >= 0 && si + 1 < lastStageTimes.length
        ? lastStageTimes[si + 1].time : lastEndedTime
      let lines = sevs.map((e) => e.text)
      let durations = sevs.map((e, k) => {
        const next = k + 1 < sevs.length ? sevs[k + 1].time : end
        return e.time != null && next != null
          ? Math.max(0, Math.round((next - e.time) * 1000)) : null
      })
      // §11: a material pre-first-milestone gap (≥ 1 s — a flip-entered stage
      // starts on its first milestone and stays clean) settles the stage's
      // canned description bullet — the exact waiting line the live block
      // ticked, never relabeled — as the first timed line, so the stage's
      // time is fully accounted by its bullets (no title stamp exists).
      const firstTime = sevs[0]?.time
      if (start != null && firstTime != null) {
        const gap = Math.round((firstTime - start) * 1000)
        if (gap >= 1000) {
          lines = [stageDoingBullet(s), ...lines]
          durations = [gap, ...durations]
        }
      }
      // §11: a stage whose stream left no milestones still says what the
      // phase does — a settled block is never a bare title — and its canned
      // bullet, the stage's only action, carries the stage's whole span.
      if (!lines.length) {
        lines = [stageDoingBullet(s)]
        durations = [start != null && end != null
          ? Math.max(0, Math.round((end - start) * 1000)) : null]
      }
      return newEntry({
        kind: 'activity', title: stageDisplayTitle(s),
        text: lines.join('\n'),
        outcome: outcome && i === stages.length - 1 ? outcome : 'done',
        ...(durations.some((d) => d != null) ? { eventDurationsMs: durations } : {}),
      })
    })
    // Staleness guard: a slow in-flight tick may resolve after this job was
    // cancelled/replaced (jobIdRef changed) or after another tick already
    // handled the terminal status (jobIdRef cleared below). Checking the ref
    // covers both — callbacks fire once, and never against a different job.
    // §11: transient poll errors keep the job tracked - the job may still be
    // running fine server-side, so only three consecutive failures give up.
    let pollFails = 0
    pollRef.current = setInterval(() => {
      void (async () => {
        try {
          const j = await api.getDraftJob(jobId)
          if (jobIdRef.current !== jobId) return
          pollFails = 0
          if (j.status !== 'building') jobIdRef.current = null
          // §8/§11: the job's live stage drives the skeleton + save-hint labels
          // (the unified three-phase set); `detail` is the
          // finer live-progress line under it, `events` the feed's history.
          const evs = j.events ?? []
          // §11: chat jobs display "Working on the request…" from the
          // moment of send (the pre-poll default title) — seed it first so the
          // shown block always settles, even when the backend flipped stages
          // before the first poll ever observed the neutral one.
          if (j.mode !== 'sync') noteStage('Working on the request')
          evs.forEach((e) => noteStage(e.stage))
          noteStage(j.stage)
          lastStageTimes = j.stageTimes ?? []
          lastEndedTime = j.endedTime ?? null
          const evKey = evs.length ? `${evs.length}:${evs[evs.length - 1].text}` : ''
          if (j.status === 'building' && (j.stage !== lastStage || (j.detail ?? null) !== lastDetail || evKey !== lastEvKey)) {
            lastStage = j.stage
            lastDetail = j.detail ?? null
            lastEvKey = evKey
            // §11: a stage the job moved past settles into the thread right
            // away — its title and feed survive the transition — and the live
            // progress entry restarts with only the current stage's events.
            const finished = settleStages(evs,
              seenStages.filter((s) => s !== j.stage && !settledStages.has(s)), null)
            const live = evs.filter((e) => !e.stage || e.stage === j.stage)
              .map((e) => ({ text: e.text, time: e.time }))
            // §11 live durations: the title row's elapsed ticks from the
            // current stage's §8 stamp
            const startedAt = [...lastStageTimes].reverse()
              .find((t) => t.stage === j.stage)?.time ?? null
            setRev((r) => (r ? {
              ...r, genStage: j.stage, genDetail: lastDetail, genEvents: live,
              genStageStartedAt: startedAt,
              ...(finished.length ? { chat: [...r.chat, ...finished] } : {}),
            } : r))
          }
          // §11: a chat job's plan lands the moment the flip is observed —
          // beneath the just-settled deciding block, above the restarted live
          // entry. A job that settles before any building poll saw the plan
          // skips this and lands the answer at settle as always (onDone dedups
          // against a landed plan, so both paths yield exactly one entry).
          if (onPlan && !planDelivered && j.status === 'building' && j.plan) {
            planDelivered = true
            onPlan(j.plan)
          }
          // §11 activity entries: a settled job persists every stage not yet
          // settled (rendered with a glyph where the spinner was), each with
          // its own feed slice, before the outcome entries the handlers
          // append; only the last takes the job's outcome. A cancelled job
          // leaves none for its in-flight stage — its request returns to the
          // input — but already-settled stages stay.
          if (j.status === 'done' || j.status === 'blocked' || j.status === 'failed') {
            const status = j.status
            const pending = seenStages.filter((s) => !settledStages.has(s))
            const rest = settleStages(evs, pending, status)
            setRev((r) => {
              if (!r) return r
              // A payload with no stage at all (belt-and-braces — the backend
              // always sets one) settles the old way: one entry, busy-flag
              // title, the whole feed — never bare (§11 canned bullet).
              const title = jobStageTitle(r)
              const entries = (rest.length || pending.length) ? rest : [newEntry({
                kind: 'activity', title,
                text: evs.map((e) => e.text).join('\n') || stageDoingBullet(title),
                outcome: status,
              })]
              return entries.length ? { ...r, chat: [...r.chat, ...entries] } : r
            })
          }
          // §19 sentTriggers guard: captured before the handlers run, so a
          // re-attached settle's `triggers` ops can prove the base list.
          if (j.status !== 'building') sentTriggersRef.current = j.sentTriggers ?? null
          if (j.status === 'done') {
            stopPoll()
            if (j.draft) onDone(j.draft)
            else onFail('The agent returned an empty draft.')
          } else if (j.status === 'blocked') {
            stopPoll()
            if (onBlocked) onBlocked(j.blockers ?? [], j.blockedAt ?? 'steps', j.draft?.spec ?? null, j.diagnosed ?? false, j.draft?.notes ?? null)
            else onFail(j.error || 'Your AI hit a blocker.')
          } else if (j.status === 'failed') {
            stopPoll()
            onFail(j.error || '', j.errorDetail)
          } else if (j.status === 'cancelled') {
            stopPoll()
            onCancelled?.()
          }
          // §19 ack: the editor consumes every settled outcome it applies —
          // live settles exactly like re-attached ones — so the backend drops
          // the held record and a later entry never re-applies it.
          if (j.status === 'done' || j.status === 'blocked' || j.status === 'failed') {
            void api.ackDraftJob(jobId).catch(() => { /* already dropped */ })
          }
        } catch (e) {
          if (jobIdRef.current !== jobId) return
          if (++pollFails < 3) return // transient - the next tick retries
          jobIdRef.current = null
          stopPoll()
          onFail((e as Error).message)
        }
      })()
    }, 700)
  }

  // §8 blocker notes: a blocked job's draft.notes applies exactly like a chat
  // notes rewrite — state patch plus a "Notes updated." chip after the
  // blockers entry; notes never mark the workflow out of sync (§4.1).
  const blockerNotes = (r: Rev, notes: string | null) => (notes != null && notes !== r.notes
    // §4.4: an applied notes rewrite is a draft change — it marks touched
    // (the thread itself no longer rides the draft)
    ? { patch: { notes, touched: true }, chip: [newEntry({ kind: 'system' as const, icon: 'fa-note-sticky', text: 'Notes updated.' })] }
    : { patch: {}, chip: [] })

  // The {start} half of the core: POST the job, then arm the poll — unless a
  // cancel landed while the POST was in flight (gen moved), in which case the
  // freshly created job is cancelled instead.
  const startJob = async (body: Parameters<typeof api.postDraftJob>[0], handlers: PollHandlers) => {
    const gen = cancelGenRef.current
    const { jobId } = await api.postDraftJob(body)
    if (cancelGenRef.current !== gen) { void api.cancelDraftJob(jobId).catch(() => { /* already gone */ }); return }
    startPoll(jobId, handlers)
  }

  // The {cancel} half of the core: stop polling, invalidate any in-flight
  // POST via the gen counter, and kill the running job. Every cancel path
  // (chat, create, sync, steps-generation, Start over, unmount) goes through
  // here and layers its own state patch on top.
  const cancelJob = () => {
    stopPoll()
    cancelGenRef.current++
    if (jobIdRef.current) void api.cancelDraftJob(jobIdRef.current).catch(() => { /* already gone */ })
    jobIdRef.current = null
  }

  useEffect(() => () => {
    // §19 background continuation: leaving the editor any way (sidebar nav,
    // system back) detaches the UI and nothing more — the job keeps building
    // in the background and re-entering re-attaches (§11). Only the settle
    // paths (Discard draft, Start over) cancel; here just the poll stops.
    stopPoll()
    jobIdRef.current = null
    // §11: a live test keeps executing — it's a real record, visible and
    // cancellable from its execution page; re-entering the editor re-attaches.
    useStore.getState().clearTest()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // §11: the chat job's poll handlers, shared by sendChat and the §11
  // re-attach (attachJob below) — the response decides the outcome, applied
  // exactly the same whether the settle was observed live or on return. ctx:
  // the turn's request text (the rewrite entry + cancel restore), whether an
  // undo snapshot existed at send (the restore toast — always false on
  // re-attach: the snapshot never survives leaving), and the id of a plan
  // entry that already landed (the settle updates it in place).
  const makeChatHandlers = (ctx: { request: string; hadSnap: boolean; planEntryId: string | null }): PollHandlers => ({
    onDone: (dft) => {
      const actions = dft.actions ?? {}
      const rewrote = !!dft.spec || dft.instructions != null || dft.notes != null
      const empty = !dft.answer && !rewrote && !dft.actions
      // §4.1/§11 uniqueness backstop: a chat rename into another automation's
      // name is skipped with a system entry — checked against the store's
      // list (edit mode's §19 PATCH answers 422 on the same rule).
      const nameSkipped = !!actions.name && useStore.getState().automations.some((a) =>
        a.id !== (isEdit && auto ? auto.id : null)
        && a.name.trim().toLowerCase() === actions.name!.trim().toLowerCase())
      setRev((r) => {
        if (!r) return r
        let next: Rev = { ...r, chatBusy: false }
        const chat = [...r.chat]
        // §11 answer header: stamped at creation — a reply arriving with
        // rewrites or actions is the plan; a question gets its own glyph.
        // A plan that already landed at the flip dedups here: no second
        // entry, an in-place text update when a repair round changed the
        // prose (a shown block is never removed).
        const planIdx = ctx.planEntryId ? chat.findIndex((e) => e.id === ctx.planEntryId) : -1
        if (dft.answer) {
          if (planIdx >= 0) {
            if (chat[planIdx].text !== dft.answer) chat[planIdx] = { ...chat[planIdx], text: dft.answer }
          } else {
            chat.push(newEntry({
              kind: 'answer', text: dft.answer,
              ...answerHeader(rewrote || Object.keys(actions).length > 0, dft.answerKind),
            }))
          }
        }
        // §8 undo action: arrives alone (validation) — run the §11
        // restore exactly like the undo row's button, or say there is
        // nothing left to undo
        if (actions.undo) {
          const snap = r.undo
          if (snap) {
            next = {
              ...next,
              spec: snap.spec, steps: snap.steps, params: snap.params, packages: snap.packages,
              triggers: snap.triggers, paramValues: snap.paramValues, concurrency: snap.concurrency,
              testValues: snap.testValues,
              instructions: snap.instructions, notes: snap.notes,
              dirty: snap.dirty, undo: null,
            }
            chat.push(newEntry({ kind: 'system', icon: 'fa-rotate-left', text: 'Last change undone — the rewrites above no longer apply.' }))
          } else {
            chat.push(newEntry({ kind: 'system', icon: 'fa-rotate-left', text: 'Nothing to undo.' }))
          }
        }
        // §11 draft undo: a draft-changing response stashes the full
        // pre-request draft as one snapshot, anchored to the LAST
        // document entry it appends — the standalone undo row renders
        // directly beneath it
        let anchorId: string | null = null
        if (dft.spec) {
          const entry = newEntry({ kind: 'rewrite', text: ctx.request })
          anchorId = entry.id
          next = { ...next, spec: dft.spec, dirty: true }
          chat.push(entry)
          // §11 provisional name (create): the spec `#` title stands in
          // until the response's `name` action replaces it
          if (!isEdit && (!r.name || r.name === 'New automation') && !actions.name) {
            const h1 = dft.spec.find((b) => b.kind === 'h1')?.text
            if (h1) next = { ...next, name: h1 }
          }
        }
        if (dft.instructions != null && dft.instructions !== r.instructions) {
          const entry = newEntry({ kind: 'system', icon: 'fa-list-check', text: 'Build instructions updated.' })
          anchorId = entry.id
          // like a manual Build-instructions save — same dirty gating (§11)
          next = { ...next, instructions: dft.instructions, dirty: true }
          chat.push(entry)
        }
        if (dft.notes != null && dft.notes !== r.notes) {
          const entry = newEntry({ kind: 'system', icon: 'fa-note-sticky', text: 'Notes updated.' })
          anchorId = entry.id
          // notes never mark the workflow out of sync (§4.1)
          next = { ...next, notes: dft.notes }
          chat.push(entry)
        }
        if (actions.name && actions.name !== r.name) {
          const entry = nameSkipped
            ? newEntry({ kind: 'system', icon: 'fa-pen', text: `Rename to “${actions.name}” skipped — an automation with that name already exists.` })
            : newEntry({ kind: 'system', icon: 'fa-pen', text: `Renamed to “${actions.name}”.` })
          if (anchorId) anchorId = entry.id // the row sits below every chip the request produced
          if (!nameSkipped) next = { ...next, name: actions.name }
          chat.push(entry)
        }
        if (actions.description && actions.description !== r.description) {
          const entry = newEntry({ kind: 'system', icon: 'fa-pen', text: 'Description updated.' })
          if (anchorId) anchorId = entry.id
          next = { ...next, description: actions.description }
          chat.push(entry)
        }
        // §11 workflow chip group: the staged-change chips collect here —
        // held for the chained sync's settle when the response arms one,
        // landed right after the document chips otherwise (hold-and-flush).
        const wfChips: ChatEntry[] = []
        // §8 `param_values`: stage stored values (§4.2) — coerced against
        // today's defs; a name today's defs don't hold stays raw in the
        // map and re-checks after the chained sync lands (§11).
        if (actions.paramValues) {
          const staged = { ...next.paramValues }
          for (const [n, v] of Object.entries(actions.paramValues)) {
            const def = r.params.find((p) => p.name === n)
            staged[n] = def ? coerceParamValue(def, v) : v
            const entry = newEntry({ kind: 'system', icon: 'fa-sliders', text: `Parameter “${n}” staged — applies when you save.` })
            anchorId = entry.id
            wfChips.push(entry)
          }
          next = { ...next, paramValues: staged }
        }
        // §8 `triggers` ops: edit the editor's trigger list (staged like a
        // sync's §4.3 preview — lands only at save), one chip per op.
        if (actions.triggers?.length) {
          // §11 re-attach guard: ops apply only when the base list is
          // provably the one the agent's indexes refer to (§19
          // sentTriggers) — a §9.2 trigger edit while the job ran
          // unattended drops every op with one chip. A live-started job
          // skips the check: the inputs lock kept the list still.
          if (attachedRef.current && !sameTriggerList(sentTriggersRef.current, next.triggers)) {
            wfChips.push(newEntry({ kind: 'system', icon: 'fa-clock', text: 'Trigger changes dropped — the triggers changed while your AI worked. Ask again.' }))
          } else {
            const applied = applyTriggerOps(next.triggers, actions.triggers)
            next = { ...next, triggers: applied.triggers }
            for (const text of applied.chips) {
              const entry = newEntry({ kind: 'system', icon: 'fa-clock', text })
              anchorId = entry.id
              wfChips.push(entry)
            }
          }
        }
        // §8 `concurrency`: stage the §4.1 settings — sent keys merge over
        // the staged object, land only at save; the CONCURRENCY card shows
        // the staged values.
        if (actions.concurrency) {
          next = { ...next, concurrency: { ...next.concurrency, ...actions.concurrency } }
          const entry = newEntry({ kind: 'system', icon: 'fa-layer-group', text: 'Concurrency staged — applies when you save.' })
          anchorId = entry.id
          wfChips.push(entry)
        }
        // §11 hold-and-flush: an armed sync takes the workflow chips with
        // it (assignment, not append — idempotent under a re-run updater);
        // no sync means they land now, after the document chips. The undo
        // anchor may point at a held chip — the row stays hidden until the
        // flush lands it in the thread.
        const willSync = !!(actions.sync || (actions.test && next.dirty))
        if (willSync) heldChipsRef.current = wfChips
        else chat.push(...wfChips)
        // §4.4: a response that changed the draft marks it touched — an
        // answer-only reply leaves the draft (and the keep paths) alone.
        // anchorId covers every doc rewrite and staged chip; the undo
        // restore counts only when a snapshot actually restored.
        if (anchorId || (actions.undo && r.undo)) next = { ...next, touched: true }
        // an answer-only response leaves the existing snapshot untouched
        if (anchorId) {
          next = {
            ...next,
            undo: {
              spec: r.spec, steps: r.steps, params: r.params, packages: r.packages,
              triggers: r.triggers, paramValues: r.paramValues, concurrency: r.concurrency,
              testValues: r.testValues,
              instructions: r.instructions, notes: r.notes,
              dirty: r.dirty, entryId: anchorId,
            },
          }
        }
        if (empty) chat.push(newEntry({ kind: 'error', text: 'The agent returned an empty response.' }))
        // §11 action chaining: arm the sync/test pendings; the watcher
        // effect (TEST card) fires them against fresh state.
        if (willSync) next = { ...next, pendingSync: true }
        if (actions.test) next = { ...next, pendingTest: { values: actions.testValues ?? null } }
        return { ...next, chat }
      })
      // §4.1: name/description are user-owned identity — edit mode applies them
      // immediately via PATCH, exactly like the pencil edits. A skipped
      // rename never rides the PATCH (its 422 would drop the description too).
      if (isEdit && auto && ((actions.name && !nameSkipped) || actions.description)) {
        void api.patchAutomation(auto.id, {
          ...(actions.name && !nameSkipped ? { name: actions.name } : {}),
          ...(actions.description ? { description: actions.description } : {}),
        }).catch((e) => showToast((e as Error).message))
      }
      if (dft.spec && !actions.sync && !actions.test) {
        showToast('Spec updated — the workflow is out of sync. Sync the steps before saving.', 5800)
      }
      if (actions.undo && ctx.hadSnap) showToast('Last change undone.', 3200)
    },
    onFail: (msg) => setRev((r) => r && ({
      ...r, chatBusy: false,
      chat: [...r.chat, newEntry({ kind: 'error', text: msg || 'The request failed — try again or rephrase.' })],
    })),
    onCancelled: () => setRev((r) => r && ({ ...r, chatBusy: false })),
    // §11: the flip's plan lands mid-job as "The plan" (rewrites follow by
    // definition), beneath the just-settled deciding block — unless one
    // already landed (a re-attached job whose flip was observed live).
    onPlan: (plan) => {
      if (ctx.planEntryId) return
      const entry = newEntry({ kind: 'answer', text: plan, ...answerHeader(true) })
      ctx.planEntryId = entry.id
      setRev((r) => r && ({ ...r, chat: [...r.chat, entry] }))
    },
    // §11: a blocked chat call lands as a blockers entry — draft untouched
    // apart from the §8 blocker notes when carried.
    onBlocked: (blockers, _at, _spec, diagnosed, notes) => setRev((r) => {
      if (!r) return r
      const bn = blockerNotes(r, notes)
      return {
        ...r, chatBusy: false, ...bn.patch,
        chat: [...r.chat, newEntry({ kind: 'blockers', source: 'chat', blockers, diagnosed, resolved: r.resolved }), ...bn.chip],
      }
    }),
  })

  // §11: a chat message starts one §8 `chat` job — the authoring agent gets the
  // in-editor draft (spec + steps + build instructions + notes), the grants
  // context, and the recent thread; the backend adds the RECENT EXECUTIONS and
  // PACKAGES context itself. One response may combine an answer with rewrites
  // (spec / build instructions / notes) and follow-up actions (sync, test,
  // rename) — applied in that order (§11), with the sync/test chain armed as
  // pending flags a watcher effect fires.
  const sendChat = async (textArg?: string, executionId?: string) => {
    if (!rev || anyJobBusy || testLive || viewingOld) return
    const request = (textArg ?? chatText).trim()
    if (!request) return
    if (textArg === undefined) setChatText('')
    // §11: a fresh draft's first message is the automation's description —
    // Start over returns it to the input (the §8 new-automation rule; the
    // job itself is an ordinary chat job).
    const freshDraft = !isEdit && rev.spec.length === 0 && rev.steps.length === 0
    if (freshDraft) firstRequestRef.current = request
    const entry = newEntry({ kind: 'user', text: request })
    chatReqRef.current = { text: request, entryId: entry.id }
    // §8 undo action: inputs lock while the job runs, so the snapshot at send
    // time is the snapshot at apply time — decides the restore toast below
    const hadSnap = !!rev.undo
    // §8/§4.4: the thread BEFORE this message, clipped at the newest boundary
    // marker — a settled draft session's conversation never reaches the agent
    const history = chatSinceBoundary(persistChat(rev.chat))
    const current = serializeDraft(rev)
    attachedRef.current = false // a live-started job never runs the re-attach trigger guard
    setRev((r) => r && ({
      ...r,
      specEdit: false, specText: '', specTextOrig: '', instrDraft: null, instrEdit: false, // one edit at a time
      notesDraft: null, notesEdit: false,
      // §11 auto-dismiss on reply: a sent message answers any open
      // clarification blockers (chat source); sync entries stay
      // open — their Apply button remains useful until a sync lands
      chat: [...r.chat.map((e) => (e.kind === 'blockers' && !e.dismissed
        && e.source === 'chat' ? { ...e, dismissed: true } : e)), entry],
      // §4.4 thread lifetime: the thread no longer rides the draft, so sending
      // alone doesn't touch it — a pure Q&A keeps no draft. The response
      // handler marks touched when it actually changes the draft.
      chatBusy: true, genStage: null, genDetail: null, genEvents: [], genStageStartedAt: null,
    }))
    try {
      await startJob({
        mode: 'chat', text: request, ...(isEdit && auto ? { automationId: auto.id } : {}),
        ...(executionId ? { executionId } : {}),
        agentId, current, chat: history,
        enabledAgents: rev.enabledAgents, allowedSecrets: rev.allowedSecrets,
      }, makeChatHandlers({ request, hadSnap, planEntryId: null }))
    } catch (e) {
      setRev((r) => r && ({
        ...r, chatBusy: false,
        chat: [...r.chat, newEntry({ kind: 'error', text: (e as Error).message })],
      }))
    }
  }

  // §11: the sync job's poll handlers, shared by runSync and the §11
  // re-attach (attachJob below) — same settle whether observed live or on return.
  const makeSyncHandlers = (): PollHandlers => ({
    onDone: (dft) => {
      // §11 hold-and-flush: taken outside the updater (a ref mutation is
      // not idempotent under a re-run updater) — the held workflow chips
      // land beneath the sync trail, before the drop chips, so a value
      // staged this turn that the rebuild then dropped reads staged → dropped.
      const held = takeHeldChips()
      setRev((r) => {
        if (!r) return r
        const steps = dft.steps ?? r.steps
        const triggers = dft.triggers ? mergeDraftTriggers(r.triggers, dft.triggers) : r.triggers
        const params = dft.params ?? r.params
        // §11: the staged value map re-checks against the rebuilt params —
        // names no drafted param matches are dropped with a chip, the rest
        // re-coerce to the (possibly re-kinded) definitions.
        const stale = Object.keys(r.paramValues).filter((n) => !params.some((p) => p.name === n))
        const paramValues = Object.fromEntries(Object.entries(r.paramValues)
          .filter(([n]) => !stale.includes(n))
          .map(([n, v]) => [n, coerceParamValue(params.find((p) => p.name === n)!, v)]))
        const syncedEntry = newEntry({ kind: 'system', icon: 'fa-rotate', text: 'Steps synced with the spec.' })
        const notesEntry = dft.notes != null && dft.notes !== r.notes
          ? newEntry({ kind: 'system' as const, icon: 'fa-note-sticky', text: 'Notes updated.' }) : null
        const dropEntries = stale.map((n) => newEntry({
          kind: 'system' as const, icon: 'fa-sliders', text: `Value for “${n}” dropped — no such parameter after the rebuild.` }))
        // §11 trigger-setup reminder — only when this sync introduced the
        // gap, so repeated syncs over an unchanged gap never repeat it
        const remind = needsMessageTriggerSetup(steps, triggers)
          && !needsMessageTriggerSetup(r.steps, r.triggers)
        // §8: the manifest's name/description are create-only — a sync never
        // touches them (both are user-owned identity, §4.1).
        return {
          // §11 draft undo: a completed sync keeps the snapshot — Undo
          // reverts the whole request, chained-sync steps included — and
          // re-anchors the undo row below the sync's own chips
          ...r, syncBusy: false, genStage: null, dirty: false,
          undo: r.undo ? { ...r.undo, entryId: (dropEntries.at(-1) ?? held.at(-1) ?? notesEntry ?? syncedEntry).id } : r.undo,
          steps, params, paramValues, packages: dft.packages ?? [],
          // §8/§11: a sync's drafted test values replace the map; absent
          // one, the old map stays (unmatched names simply seed nothing)
          ...(dft.testValues ? { testValues: dft.testValues } : {}),
          triggers,
          // §8: call 2 may return an updated notes.md beside the manifest
          ...(dft.notes != null && dft.notes !== r.notes ? { notes: dft.notes } : {}),
          // §11: a completed sync collapses any pending blockers entry —
          // its blockers describe steps that no longer exist — and lands
          // a quiet system chip in the thread.
          chat: [
            ...r.chat.map((e) => (e.kind === 'blockers' && !e.dismissed ? { ...e, dismissed: true } : e)),
            syncedEntry,
            ...(notesEntry ? [notesEntry] : []),
            ...held,
            ...dropEntries,
            ...(remind ? [newEntry({ kind: 'system', icon: 'fa-clock', text: TRIGGER_SETUP_TEXT })] : []),
          ],
        }
      })
      showToast('Steps synced with the spec — review them, then save.', 3600)
    },
    onFail: (msg) => {
      // §11 hold-and-flush: any outcome flushes the held workflow chips —
      // the staging happened, a failed sync never swallows the receipts.
      const held = takeHeldChips()
      setRev((r) => r && ({ ...r, syncBusy: false, ...(held.length ? { chat: [...r.chat, ...held] } : {}) }))
      showToast(`The draft didn’t validate — try again or rephrase.${msg ? ' ' + msg : ''}`, 4500)
    },
    onCancelled: () => {
      const held = takeHeldChips()
      setRev((r) => r && ({ ...r, syncBusy: false, ...(held.length ? { chat: [...r.chat, ...held] } : {}) }))
    },
    onBlocked: (blockers, _at, _spec, diagnosed, notes) => {
      // §11 hold-and-flush: the held chips land after the settled trail,
      // before the blockers message block.
      const held = takeHeldChips()
      setRev((r) => {
        if (!r) return r
        const bn = blockerNotes(r, notes)
        return {
          ...r, syncBusy: false, ...bn.patch,
          chat: [...r.chat, ...held, newEntry({ kind: 'blockers', source: 'sync', blockers, diagnosed, resolved: r.resolved }), ...bn.chip],
        }
      })
    },
  })

  // §11: a blocked sync lands as a thread blockers entry (source: sync);
  // applying amends the in-editor spec (specOverride) and repeats the sync with it.
  const runSync = async (specOverride?: SpecBlock[]) => {
    // §11 rewrites-lock: nothing rewrites the workflow under a running test
    if (!rev || anyJobBusy || testLive) return
    // A cancel must return the panel to the state it was in (§11) — a sync
    // started from a clean draft must not leave it marked out-of-sync. A
    // blocker amend (specOverride) is a spec rewrite: it dirties the draft,
    // and a cancelled/failed sync keeps it dirty — the amended spec stays.
    dirtyBeforeSync.current = specOverride ? true : rev.dirty
    attachedRef.current = false // a live-started job never runs the re-attach trigger guard
    up({
      specEdit: false, specText: '', specTextOrig: '', instrDraft: null, instrEdit: false, // discard unsaved edits
      notesDraft: null, notesEdit: false,
      syncBusy: true, genStage: null, genDetail: null, genEvents: [], genStageStartedAt: null, touched: true,
      // §11 draft undo: a repair amend replaces the spec outside the undo flow
      ...(specOverride ? { spec: specOverride, dirty: true, undo: null } : {}),
    })
    try {
      await startJob({
        mode: 'sync', ...(isEdit && auto ? { automationId: auto.id } : {}),
        agentId, current: { ...serializeDraft(rev), spec: specOverride ?? rev.spec },
        enabledAgents: rev.enabledAgents, allowedSecrets: rev.allowedSecrets,
      }, makeSyncHandlers())
    } catch (e) {
      // §11 hold-and-flush: a POST that never became a job is an outcome too —
      // the held chips land like on any other failure.
      const held = takeHeldChips()
      setRev((r) => r && ({ ...r, syncBusy: false, ...(held.length ? { chat: [...r.chat, ...held] } : {}) }))
      showToast((e as Error).message)
    }
  }

  // §11 Background continuation & re-attach: re-enter a job the editor left
  // running (or one that settled unobserved — the first poll tick then applies
  // the held outcome exactly like a live settle and acks it). Reconciliation:
  // stages whose activity entries already landed live seed as settled (never
  // landing twice), a plan entry that landed at a live-observed flip seeds
  // for the in-place settle update, and the last user entry re-arms the
  // cancel path's text restore. The caller (CreateFlow's re-attach effect)
  // guards that the thread is merged before calling.
  const attachJob = (ref: { jobId: string; mode: 'chat' | 'sync' }) => {
    if (!rev) return
    attachedRef.current = true
    const turn = chatSinceBoundary(rev.chat)
    const lastUserIdx = turn.map((e) => e.kind).lastIndexOf('user')
    const afterUser = lastUserIdx >= 0 ? turn.slice(lastUserIdx + 1) : turn
    const preSettled = afterUser
      .filter((e) => e.kind === 'activity' && e.title)
      .map((e) => (e.title as string).replace(/…$/, ''))
    if (ref.mode === 'chat') {
      const userEntry = lastUserIdx >= 0 ? turn[lastUserIdx] : null
      const request = userEntry?.text ?? ''
      if (userEntry) chatReqRef.current = { text: request, entryId: userEntry.id }
      // §11: a fresh draft's first message — Start over returns it to the input
      if (!isEdit && rev.spec.length === 0 && rev.steps.length === 0) firstRequestRef.current = request
      const planEntry = [...afterUser].reverse()
        .find((e) => e.kind === 'answer' && e.title === 'The plan')
      setRev((r) => r && ({ ...r, chatBusy: true, genStage: null, genDetail: null, genEvents: [], genStageStartedAt: null }))
      startPoll(ref.jobId, makeChatHandlers({ request, hadSnap: false, planEntryId: planEntry?.id ?? null }),
        { preSettled })
    } else {
      dirtyBeforeSync.current = rev.dirty
      setRev((r) => r && ({ ...r, syncBusy: true, genStage: null, genDetail: null, genEvents: [], genStageStartedAt: null }))
      startPoll(ref.jobId, makeSyncHandlers(), { preSettled })
    }
  }

  // §11: Cancel on the footer action block — kill the running job. A chat
  // cancel drops the pending user entry and returns the text to the input.
  const cancelChat = () => {
    if (!rev?.chatBusy) return
    cancelJob()
    const req = chatReqRef.current
    chatReqRef.current = null
    setRev((r) => r && ({
      ...r, chatBusy: false,
      chat: req ? r.chat.filter((e) => e.id !== req.entryId) : r.chat,
    }))
    if (req) setChatText((cur) => cur || req.text)
    showToast('Edit stopped — the spec is unchanged.', 4200)
  }

  // §11: sync Cancel (footer action block) — kill the job, keep the steps
  // and spec untouched, return the panel to the state it was in before.
  // §11 hold-and-flush: cancelJob() stops the poll, so onCancelled never
  // fires for a user cancel — the held workflow chips flush here instead
  // (the staging already happened; a cancelled sync never swallows a receipt).
  const cancelSync = () => {
    if (!rev?.syncBusy) return
    cancelJob()
    const wasDirty = dirtyBeforeSync.current
    const held = takeHeldChips()
    setRev((r) => r && ({
      ...r, syncBusy: false, dirty: wasDirty,
      ...(held.length ? { chat: [...r.chat, ...held] } : {}),
    }))
    showToast(wasDirty
      ? 'Sync stopped — the workflow is still out of sync.'
      : 'Sync stopped — nothing changed.', 4200)
  }

  return {
    sendChat, runSync, attachJob, flushHeldChips, takeHeldChips,
    cancelChat, cancelSync, cancelJob,
    stopPoll, jobIdRef, firstRequestRef,
  }
}
