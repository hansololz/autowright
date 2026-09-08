// Create / edit flow (§11): one editor screen from birth to save — a floating
// chat panel (the editor's only conversational surface: requests, answers,
// blockers, failure analyses, drafting progress) beside the Review grid.
// This file is the page shell: store wiring, draft persistence, the derived
// dirty-gating block, the title row / lede / banners, and the version menu.
// The pieces live under ./createflow/: model.ts (the pure Rev model + helpers),
// useDraftJob.ts (§8 job orchestration — chat/sync + every cancel path),
// ChatPanel.tsx (thread + composer), BuildTestCards.tsx (the BUILD and TEST
// cards) + TestRunModal.tsx (the draft test's modal), SectionCards.tsx (the left/right review cards). The step list and
// param editors are shared with the detail page via ../steps.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { Agent, ChatEntry } from '../types'
import {
  BackLink, BtnPrimary, ConfirmModal, HeaderActions, MenuItemRow, Notice, PageLoading, PageTitle,
  PopMenu, ScrollArea, usePopover,
} from '../ui'
import { nextTriggerShort, useTriggerPreview } from '../triggers'
import {
  type Rev, amendSpec, analyzeTestMessage, blockerLine, chatSinceBoundary, holdsDraftEdits, instructionCache,
  jobStageTitle, loadVersionInto,
  newEntry, persistChat, secretRefsOf, seedEmpty, seedFromAuto, seedFromPayload, serializeDraft,
} from './createflow/model'
import { useDraftJob } from './createflow/useDraftJob'
import { ChatPanel } from './createflow/ChatPanel'
import { BuildCard, TestCard } from './createflow/BuildTestCards'
import { LeftColumn, RightCards } from './createflow/SectionCards'

// The pure helpers moved to ./createflow/model (and ../steps for the shared
// step-secret scanners) — re-exported here so the unit tests and any older
// imports keep one stable import path.
export {
  specToText, textToSpec, amendSpec, newEntry, persistChat, chatSinceBoundary,
  stepSecretTags, stepSecretIds, secretRefsOf,
  seedEmpty, seedFromPayload, seedFromAuto,
  stripTrigger, mergeDraftTriggers, serializeDraft, applyTestValues,
  applyTriggerOps, coerceParamValue,
  needsMessageTriggerSetup,
} from './createflow/model'

// ---------- the page ----------

export default function CreateFlow() {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this editor on
  // every store write anywhere — every toast, every log line of every execution.
  // §9 per-OS copy rule: the machine noun the §7 Fix-with-AI seed names.
  const copy = usePlatformCopy()
  const agents = useStore((s) => s.agents)
  const secrets = useStore((s) => s.secrets)
  const automations = useStore((s) => s.automations)
  const executions = useStore((s) => s.executions)
  const executionFull = useStore((s) => s.executionFull)
  const createFrom = useStore((s) => s.createFrom)
  const automationId = useStore((s) => s.automationId)
  const go = useStore((s) => s.go)
  const setSurface = useStore((s) => s.setSurface)
  const showToast = useStore((s) => s.showToast)
  const loadAuto = useStore((s) => s.loadAuto)
  const test = useStore((s) => s.test)
  const isEdit = createFrom === 'edit'
  const auto = isEdit ? automations.find((a) => a.id === automationId) ?? null : null
  // §19: the live-execution banner's "next execution" label reads from
  // POST /triggers/preview — no local trigger math in the renderer (§4.3)
  const trigPreviews = useTriggerPreview(auto?.triggers ?? [])

  const [agentId, setAgentId] = useState<string | null>(() =>
    isEdit ? (auto?.agentId ?? null) : ((agents.find((g) => g.default) ?? agents[0])?.id ?? null))

  const [rev, setRev] = useState<Rev | null>(null)
  const [nameEdit, setNameEdit] = useState<string | null>(null)
  // §4.1/§11: the colliding name behind the title input's inline error
  const [nameErr, setNameErr] = useState<string | null>(null)
  const [descEdit, setDescEdit] = useState<string | null>(null)
  const [chatText, setChatText] = useState('')
  // §11: sending a chat message or starting a sync while a manual edit holds
  // unsaved changes first asks through the editing card's discard confirm -
  // confirming discards and proceeds, cancelling aborts the send with the
  // composer text kept. An open editor holding no changes never asks.
  const [confirmEditDiscard, setConfirmEditDiscard] =
    useState<{ doc: 'spec' | 'notes'; proceed: () => void } | null>(null)
  const draftSnap = useRef<Rev | null>(null)
  const seededRef = useRef(false)

  // §4.4: any exit path keeps the draft — system back/forward unmounts the editor
  // without going through close(), so the persist lives in unmount cleanup.
  // Discard and save settle the draft so leaving afterwards writes nothing.
  // Create mode keeps to the pending slot (<root>/draft/) once a draft landed.
  const revRef = useRef(rev)
  revRef.current = rev
  const autoRef = useRef(auto)
  autoRef.current = auto
  const agentIdRef = useRef(agentId)
  agentIdRef.current = agentId
  const draftSettled = useRef(false)
  // §4.4 "a discarded or saved draft is never resurrected": the last
  // continuous-persist PUT, awaited before any draft DELETE so an in-flight
  // write can't land after the discard on a slow backend.
  const putInFlight = useRef<Promise<unknown>>(Promise.resolve())
  // §4.4 thread lifetime: the thread persists on its own (§19 /chat/{owner}),
  // decoupled from the draft. chatLoaded gates every write — a PUT before the
  // stored thread merged in would clobber it (an empty one would unlink it);
  // chatGen invalidates armed debounce timers across a Start over, so a stale
  // pre-discard write can't land over the backend-appended boundary marker.
  const chatLoaded = useRef(false)
  const chatGen = useRef(0)
  const chatPutInFlight = useRef<Promise<unknown>>(Promise.resolve())
  const chatOwner = () => (isEdit ? autoRef.current?.id ?? automationId ?? null : 'pending')
  // The settle flows call this before their settle endpoint: the in-flight
  // debounced PUT is awaited and the current thread written now, so every
  // entry lands BEFORE the §4.4 boundary marker the endpoint appends.
  // §11 hold-and-flush: a keep/settle flush is an outcome too - held workflow
  // chips land in the thread and its write here. The discard paths (Start
  // over, Discard draft) take the chips first, so receipts for discarded
  // staging never reach a later session's thread.
  const flushChat = async (cancelling = false) => {
    await chatPutInFlight.current
    const r = revRef.current
    const owner = chatOwner()
    if (!chatLoaded.current || !r || !owner) return
    const held = jobs.takeHeldChips()
    if (held.length) setRev((x) => x && ({ ...x, chat: [...x.chat, ...held] }))
    // §11: a SETTLE flush past an in-flight chat job cancels it with no
    // composer to return the request to - the pending user entry stays, so the
    // chip right after it says the turn never ran (composer-cancel toast copy).
    // A plain leave (close, unmount) cancels nothing — the job keeps building
    // in the background (§19) and no chip lands.
    const stopped = cancelling && r.chatBusy
      ? [newEntry({ kind: 'system' as const, icon: 'fa-ban', text: 'Edit stopped — the spec is unchanged.' })]
      : []
    try { await api.putChat(owner, persistChat([...r.chat, ...held, ...stopped])) } catch { /* backend restarting */ }
  }
  useEffect(() => () => {
    const r = revRef.current
    const a = autoRef.current
    if (draftSettled.current) return
    // §4.4: the thread flushes on every exit path, like the draft below.
    // §11 hold-and-flush: leaving mid-chained-sync cancels the job — that's an
    // outcome, so any held workflow chips land in the persisted thread here.
    if (chatLoaded.current && r) {
      const owner = isEdit ? a?.id : 'pending'
      // §19 background continuation: leaving mid-job cancels nothing — the
      // job keeps building and the re-attach picks it up, so no "Edit
      // stopped" chip lands here (only the settle paths cancel and chip).
      if (owner) void api.putChat(owner, persistChat([...r.chat, ...jobs.takeHeldChips()])).catch(() => { /* backend restarting */ })
    }
    if (isEdit) {
      if (!a) return
      if (r && holdsDraftEdits(r, a)) {
        void api.putDraft(a.id, serializeDraft(r)).catch(() => { /* backend restarting */ })
      }
      return
    }
    if (r && (r.spec.length || r.steps.length)) {
      void api.putDraft('pending', serializeDraft(r), agentIdRef.current).catch(() => { /* backend restarting */ })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // §4.4 thread load: fetch the stored thread once and prepend it to whatever
  // the editor already appended (the §7 Fix-with-AI seed can land first) —
  // only then do the thread writers arm.
  const [chatMergeTick, setChatMergeTick] = useState(0)
  // §11 Fix-with-AI: true once the stored thread merged (or was cleared) - the
  // canned analyze send waits on it, so its §8 CONVERSATION context carries
  // the kept history and the seeded failure entry.
  const [chatReady, setChatReady] = useState(false)
  const pendingStoredChat = useRef<ChatEntry[] | null>(null)
  // §4.4 fresh-entry clear: in create mode the stored-thread merge waits for
  // the pending-draft answer — null until GET /draft/pending resolves, then
  // whether a draft resumed. Edit mode always merges.
  const slotResume = useRef<boolean | null>(isEdit ? true : null)
  useEffect(() => {
    const owner = isEdit ? automationId : 'pending'
    if (!owner) return
    let dead = false
    void api.getChat(owner).then(({ chat }) => {
      if (dead) return
      pendingStoredChat.current = chat
      setChatMergeTick((t) => t + 1)
    }).catch(() => { /* backend restarting; the thread renders empty, writers stay off */ })
    return () => { dead = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!rev || pendingStoredChat.current == null) return
    // §4.4 fresh-entry clear: don't merge (or arm the writers) until the
    // slot's resume answer is known — a failed GET keeps the writers off,
    // exactly like a failed thread fetch.
    if (slotResume.current == null) return
    const stored = pendingStoredChat.current
    pendingStoredChat.current = null
    chatLoaded.current = true
    setChatReady(true)
    if (!isEdit && slotResume.current === false) {
      // No draft to resume — a new automation always opens on the create
      // empty state: drop the settled session's leftover thread and unlink it.
      if (stored.length) void api.putChat('pending', []).catch(() => { /* backend restarting */ })
      return
    }
    if (stored.length) setRev((r) => (r ? { ...r, chat: [...stored, ...r.chat] } : r))
  }, [rev != null, chatMergeTick]) // eslint-disable-line react-hooks/exhaustive-deps

  // §4.4 continuous thread persistence — the thread's own debounced PUT,
  // independent of the draft persist (a pure Q&A keeps no draft but still
  // keeps its thread). Settling stops it; the settle flows flush explicitly.
  useEffect(() => {
    if (!rev || !chatLoaded.current || draftSettled.current) return
    const gen = chatGen.current
    const t = setTimeout(() => {
      if (draftSettled.current || gen !== chatGen.current) return
      const r = revRef.current
      const owner = chatOwner()
      if (!r || !owner) return
      chatPutInFlight.current = api.putChat(owner, persistChat(r.chat)).catch(() => { /* backend restarting */ })
    }, 1000)
    return () => clearTimeout(t)
  }, [rev?.chat, chatMergeTick]) // eslint-disable-line react-hooks/exhaustive-deps

  // §4.4 continuous persistence: once the draft holds anything worth keeping,
  // write it with a debounced PUT ~1 s after the last change — quitting the app
  // mid-edit loses nothing. The unmount save above stays the final flush;
  // settling (discard / save / create / start over) stops this writer.
  useEffect(() => {
    if (!rev || draftSettled.current) return
    const worthKeeping = isEdit
      ? !!auto && holdsDraftEdits(rev, auto)
      : rev.spec.length > 0 || rev.steps.length > 0
    if (!worthKeeping) return
    const t = setTimeout(() => {
      if (draftSettled.current) return // settled after the timer armed — never write
      const r = revRef.current
      const a = autoRef.current
      if (!r) return
      if (isEdit) {
        if (a && holdsDraftEdits(r, a)) {
          putInFlight.current = api.putDraft(a.id, serializeDraft(r)).catch(() => { /* backend restarting */ })
        }
        return
      }
      if (r.spec.length || r.steps.length) {
        putInFlight.current = api.putDraft('pending', serializeDraft(r), agentIdRef.current).catch(() => { /* backend restarting */ })
      }
    }, 1000)
    return () => clearTimeout(t) // each change resets the timer (debounce) and unmount cancels it
  }, [rev]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11: create mode mounts straight on the empty editor (empty thread,
  // placeholder cards). §4.4: while the pending slot holds a draft it resumes
  // over the untouched empty seed; a fast first message wins the race.
  // Opening also makes the slot's container exist (empty memory/) before any
  // drafting — §11 tests execute as execution records.
  useEffect(() => {
    if (isEdit) return
    setRev((r) => r ?? seedEmpty(agents, secrets.map((s) => s.id)))
    void api.openDraft('pending').catch(() => { /* backend restarting */ })
    let dead = false
    void api.getDraft('pending').then(({ draft, agentId: gid, job }) => {
      if (dead) return
      // §4.4 fresh-entry clear: the thread merge above waits on this answer.
      // §19 background continuation: a slot that owns a building or held job
      // is a session to resume — never cleared over, even before any draft
      // landed (a first message still in flight).
      if (slotResume.current == null) {
        slotResume.current = !!draft || !!job
        setChatMergeTick((t) => t + 1)
      }
      if (!draft || seededRef.current) return
      const cur = revRef.current
      // Only the untouched empty seed may be replaced — never in-flight work.
      if (cur && (cur.touched || cur.chatBusy || cur.syncBusy || cur.chat.length > 0 || cur.spec.length > 0)) return
      seededRef.current = true
      const seeded = seedFromPayload(draft, agents, secrets.map((s) => s.id))
      // A draft kept mid-steps-generation resumes spec-only — mark it out of
      // sync so the §11 sync panel offers the rebuild.
      setRev({ ...seeded, touched: true, ...(seeded.steps.length || !seeded.spec.length ? {} : { dirty: true }) })
      if (gid && agents.some((g) => g.id === gid)) setAgentId(gid)
      showToast('Resumed your unsaved draft — Start over discards it.', 3400)
    }).catch(() => { /* backend restarting; the editor still works */ })
    return () => { dead = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const up = (patch: Partial<Rev>) => setRev((r) => (r ? { ...r, ...patch } : r))

  // §11: which open manual editor holds unsaved changes (the edits are
  // mutually exclusive, so at most one can). Sends and syncs route through
  // guardManualEdit so typed edits are never silently destroyed.
  const editHoldsChanges = (r: Rev): 'spec' | 'notes' | null =>
    r.specEdit && r.specText !== r.specTextOrig ? 'spec'
      : r.notesEdit && r.notesDraft != null && r.notesDraft !== r.notes ? 'notes' : null
  const guardManualEdit = (proceed: () => void) => {
    const doc = rev ? editHoldsChanges(rev) : null
    if (doc) setConfirmEditDiscard({ doc, proceed })
    else proceed()
  }

  // ---- thread helpers ----
  const appendEntry = (e: Omit<ChatEntry, 'id' | 'at'>) =>
    setRev((r) => (r ? { ...r, chat: [...r.chat, newEntry(e)] } : r))
  const patchEntry = (id: string, patch: Partial<ChatEntry>) =>
    setRev((r) => (r ? { ...r, chat: r.chat.map((e) => (e.id === id ? { ...e, ...patch } : e)) } : r))

  // ---- review: derived (§11 dirty gating) ----
  // Memoized: the grant scan walks every step's code (regexes included via
  // secRefs) and would otherwise re-run on every keystroke anywhere in the editor.
  // §5.1/§11 (edit mode): the automation's unresolvedReferences map names
  // imported references that matched nothing — the red tags and warnings
  // show the archive name instead of a short id.
  const unresolvedRefs = isEdit ? auto?.unresolvedReferences : undefined
  const secRefs = useMemo(() => (rev ? secretRefsOf(rev.steps, unresolvedRefs) : []), [rev?.steps, unresolvedRefs]) // eslint-disable-line react-hooks/exhaustive-deps
  // §9.2 change badge: the stored revisions (current version + history) the
  // editor's step-script modal compares the viewed revision against.
  const stepHistory = useMemo(
    () => auto ? [{ version: auto.version, steps: auto.steps ?? [] }, ...(auto.versions ?? []).map((v) => ({ version: v.version, steps: v.steps }))] : undefined,
    [auto?.version, auto?.steps, auto?.versions], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const derived = useMemo(() => {
    const availAgents = rev ? rev.enabledAgents.map((id) => agents.find((g) => g.id === id)).filter((g): g is Agent => !!g) : []
    const agName = (g: Agent) => g.name || g.harness
    const agentStepIdx = rev ? rev.steps.map((s, i) => (s.agent ? i : -1)).filter((i) => i >= 0) : []
    // §11: per-id agent references — which steps list which agent, mirroring
    // secRefs. `name` is the live agent's name (display), resolved by id.
    const agRefs: { id: string; name: string; steps: number[] }[] = []
    if (rev) rev.steps.forEach((s, i) => {
      if (!s.agent) return
      for (const { id } of s.agents ?? []) {
        const r = agRefs.find((x) => x.id === id)
        if (r) r.steps.push(i)
        else {
          const g = agents.find((x) => x.id === id)
          const un = !g && unresolvedRefs?.[id]?.kind === 'agent' ? unresolvedRefs[id] : null
          agRefs.push({
            id, name: g ? agName(g) : un ? un.name : `${id.slice(0, 8)}…`, steps: [i],
            ...(un ? { imported: true } : {}),
          })
        }
      }
    })
    // All three states compare ids, never names — a rename changes nothing here.
    const agNotEnabled = agRefs.filter((r) => agents.some((g) => g.id === r.id) && !rev!.enabledAgents.includes(r.id))
    const agMissing = agRefs.filter((r) => !agents.some((g) => g.id === r.id))
    // steps with no listed agent fall back to the first enabled agent — they only
    // warn when nothing is enabled at all
    const agFallbackIdx = rev ? agentStepIdx.filter((i) => !(rev.steps[i].agents ?? []).length) : []
    const agNone = !!rev && agFallbackIdx.length > 0 && availAgents.length === 0
    const secNotAllowed = secRefs.filter((r) => secrets.some((z) => z.id === r.id) && !(rev?.allowedSecrets ?? []).includes(r.id))
    const secMissing = secRefs.filter((r) => !secrets.some((z) => z.id === r.id))
    const agWarn = !!rev && (agNone || agNotEnabled.length > 0 || agMissing.length > 0)
    const secWarn = !!rev && (secNotAllowed.length > 0 || secMissing.length > 0)
    // §11 dirty gating: grant sync state is derived, never stored — the workflow
    // is out of sync from grants exactly while a step needs a grant it doesn't
    // have. Re-checking the grant clears it instantly; toggles alone never dirty.
    const agentGap = !!rev && agentStepIdx.some((i) => {
      const s = rev.steps[i]
      const ids = (s.agents ?? []).map((e) => e.id)
      return ids.length
        ? ids.some((id) => !rev.enabledAgents.includes(id) || !agents.some((g) => g.id === id))
        : rev.enabledAgents.length === 0
    })
    const secretGap = secNotAllowed.length > 0
    return { availAgents, agentStepIdx, agNotEnabled, agMissing, agFallbackIdx, agNone, secNotAllowed, secMissing, agWarn, secWarn, agentGap, secretGap }
  }, [rev?.steps, rev?.enabledAgents, rev?.allowedSecrets, agents, secrets, secRefs, unresolvedRefs]) // eslint-disable-line react-hooks/exhaustive-deps
  const { availAgents, agentStepIdx, agNotEnabled, agMissing, agFallbackIdx, agNone, secNotAllowed, secMissing, agWarn, secWarn, agentGap, secretGap } = derived
  // §11: the spec card defaults open (manual edits happen in the
  // document-editor modal, so no card is held open by one); the agents and
  // secrets cards default collapsed and are forced open while their warnings
  // show (Packages pattern).
  const specOpenEff = (rev?.specSecOpen ?? null) == null ? true : !!rev?.specSecOpen
  const agSecOpenEff = !!rev?.agSecOpen || agWarn
  const secSecOpenEff = !!rev?.secSecOpen || secWarn
  // §11 Packages card: default collapsed when everything is installed; forced
  // open while any row is installing, not installed, or failed.
  const pkgProblem = !!rev && rev.packages.some((p) => p.status && p.status !== 'installed')
  const pkgSecOpenEff = (((rev?.pkgSecOpen ?? null) == null ? pkgProblem : !!rev?.pkgSecOpen) || pkgProblem || !!rev?.pkgBusy)
  // §11 BUILD INSTRUCTIONS card: defaults collapsed in create and edit alike
  const instrOpenEff = !!rev?.instrSecOpen
  // §11 NOTES card: defaults collapsed
  const notesOpenEff = ((rev?.notesSecOpen ?? null) == null) ? false : !!rev?.notesSecOpen
  const viewingOld = isEdit && !!rev && !!auto && rev.viewing !== 'draft' && rev.viewing !== auto.version
  // §5: permissions are never versioned — a grant gap never blocks restoring an
  // old version; it fails at execution time instead (the cards still warn).
  const outOfSync = !!rev && (rev.dirty || (!viewingOld && (agentGap || secretGap)))
  const saveBlocked = !!rev && (outOfSync || rev.syncBusy || rev.chatBusy
    || (!isEdit && rev.steps.length === 0))
  const busyRewrite = !!rev && (rev.syncBusy || rev.chatBusy)
  // §11: one agent job at a time — the chat input and every job starter gate on this.
  const anyJobBusy = busyRewrite
  // §11: the tracked test is an ordinary execution record — steps/status render
  // off it (executionFull carries the body; the header list covers the gap before
  // loadExecution lands).
  const testExec = test ? executionFull[test.executionId] ?? executions.find((e) => e.id === test.executionId) : undefined
  const testLive = testExec?.status === 'executing'
  // BUILD card: the sync button disables (never hides) while any §8 job runs,
  // while viewing an old version, while a draft test is executing
  // (§11 rewrites-lock: nothing rewrites the workflow under a running test),
  // and while steps AND spec are both
  // empty — a spec-only draft (a resumed spec-only
  // pending draft) must always be able to rebuild its steps here (§11).
  // §11 action chaining: an armed pending sync counts as a sync in flight —
  // the BUILD card already reads it that way, so its buttons disable too.
  const syncDisabled = !rev || busyRewrite || viewingOld || testLive || !!rev?.pendingSync
    || (rev.steps.length === 0 && rev.spec.length === 0)
  // §11 inputs-lock: while a sync or spec rewrite runs, every input disables —
  // buttons get `disabled`, non-button rows get this style. One shared look.
  const lockStyle: React.CSSProperties | undefined = busyRewrite ? { opacity: 0.45, pointerEvents: 'none' } : undefined

  // ---- §8 job orchestration (chat / sync + every cancel path) ----
  const jobs = useDraftJob({
    rev, setRev, up,
    isEdit, auto, agentId, showToast,
    chatText, setChatText,
    anyJobBusy, testLive, viewingOld,
  })

  // §11 Background continuation & re-attach: once the draft and thread have
  // loaded, pick the owner's job back up — re-attach a building job or apply
  // a held outcome (the first poll tick handles both: a settled job applies
  // exactly like a live settle, then acks). With no job left but a
  // current-session turn still ending on its user entry, the job vanished
  // without a trace (backend restart) — the "Edit stopped" chip closes the
  // turn so the thread never resumes on a request that looks unanswered.
  const reconciledRef = useRef(false)
  useEffect(() => {
    if (reconciledRef.current || !rev || !chatReady) return
    if (isEdit && !seededRef.current) return // wait for the stored-automation seed
    reconciledRef.current = true
    const owner = isEdit ? automationId : 'pending'
    if (!owner) return
    const jobRef = useStore.getState().draftJobs.find((j) => j.owner === owner)
    if (jobRef) {
      jobs.attachJob({ jobId: jobRef.jobId, mode: jobRef.mode })
      return
    }
    const turn = chatSinceBoundary(rev.chat)
    if (turn.at(-1)?.kind === 'user') {
      appendEntry({ kind: 'system', icon: 'fa-ban', text: 'Edit stopped — the spec is unchanged.' })
    }
  }, [rev, chatReady]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11 turn action row wiring: the Test-the-draft pill starts a draft test
  // through the TEST card (signal consumed there — same run as the modal's
  // Run test button, and it opens the modal); the Analyze-the-failure pill
  // sends the canned message exactly like the card's button — null while the
  // tracked test didn't settle failed, hiding the pill.
  const [testRunSignal, setTestRunSignal] = useState(0)
  const analyzeFailure = test && testExec?.status === 'failed'
    ? () => {
        if (anyJobBusy || testLive || viewingOld) return
        guardManualEdit(() => void jobs.sendChat(analyzeTestMessage(copy.machine, testExec?.error?.step), test.executionId))
      }
    : null

  // ---- guards + edit-mode seeding ----
  useEffect(() => {
    if (agents.length === 0) {
      setSurface('app')
      go('agents')
      showToast('No agent yet — add one here first. Creating and editing automations needs an AI.', 3600)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (isEdit && automationId) void loadAuto(automationId)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- instruction files (§8) — fetched once per app session ----
  const [fw, setFw] = useState<string>(instructionCache.framework ?? '')
  const [bld, setBld] = useState<string>(instructionCache.build)
  useEffect(() => {
    if (instructionCache.framework) return
    api.instructions()
      .then(({ framework, build }) => {
        instructionCache.framework = framework
        instructionCache.build = build
        setFw(framework)
        setBld(build)
      })
      .catch(() => { /* panel renders empty; next mount retries */ })
  }, [])

  // Edit mode addressing an automation that no longer exists (deleted from
  // another window, a stale history entry restored before the list loaded) —
  // bail out to the list, like the §9.2 detail page's own vanished-record
  // redirect. The save paths guard on it too: neither may fall into create.
  useEffect(() => {
    if (isEdit && !auto) {
      setSurface('app')
      go('automations')
    }
  }, [isEdit, auto, go, setSurface])

  useEffect(() => {
    if (!isEdit || seededRef.current || !auto || !auto.spec) return
    seededRef.current = true
    setRev(seedFromAuto(auto, agents, secrets.map((s) => s.id)))
    if (auto.agentId) setAgentId(auto.agentId)
  }, [auto, isEdit, agents, secrets])

  const selAgent = agents.find((g) => g.id === agentId) ?? agents.find((g) => g.default) ?? agents[0] ?? null

  // §11 Start over (create): cancel any job, discard the pending slot's draft,
  // return to the empty state with the description in the input. The thread
  // stays (§4.4 thread lifetime) behind the backend-appended "Draft
  // discarded." boundary marker — refetched so the marker shows.
  // §11: Start over / Discard draft settles the whole draft — a test still
  // executing on it is cancelled with it, so nothing keeps running against a
  // draft that no longer exists and the TEST card can never re-attach to it.
  const cancelDraftTest = async () => {
    const ids = new Set<string>()
    if (test && testLive) ids.add(test.executionId)
    // the same live-test filter the TEST card re-attaches on (§4.5: a
    // create-mode test record carries automationId null)
    const live = executions.find((e) => e.test && e.status === 'executing'
      && (isEdit ? e.automationId === auto?.id : !e.automationId))
    if (live) ids.add(live.id)
    for (const id of ids) {
      try { await api.cancelExecution(id) } catch { /* already settled */ }
    }
  }

  const resetCreate = async () => {
    jobs.cancelJob()
    // §11 hold-and-flush: Start over discards the session's staging, so its
    // held workflow chips drop with it - they must never flush into (or leak
    // through a later sync onto) the next session's thread.
    jobs.takeHeldChips()
    // §4.4: Start over discards the pending slot's draft — after any in-flight
    // continuous-persist PUT, which would otherwise resurrect it. The thread
    // flushes first so every entry lands before the boundary marker; bumping
    // chatGen kills armed debounce timers that would clobber the marker.
    chatGen.current++
    await flushChat(true) // a settle path: an in-flight job was cancelled above
    await putInFlight.current
    await cancelDraftTest()
    try { await api.deleteDraft('pending') } catch { /* none kept */ }
    let chat: ChatEntry[] = []
    try { chat = (await api.getChat('pending')).chat } catch { /* backend restarting */ }
    setNameEdit(null)
    setDescEdit(null)
    setRev({ ...seedEmpty(agents, secrets.map((s) => s.id)), chat })
    setChatText((cur) => cur || jobs.firstRequestRef.current)
  }

  // §11 title rename — hidden while any job runs and, in edit mode, while
  // viewing anything but the draft (Restore never renames). Create mode:
  // renaming becomes available once the draft holds content — a
  // pre-draft rename would be wiped when the first turn's rewrites land.
  const canRename = !!rev && !busyRewrite
    && (isEdit ? rev.viewing === 'draft' : rev.spec.length > 0 || rev.steps.length > 0)
  // Create mode: the spec `#` title stands in until the `name` action lands (§11)
  const draftName = !rev ? ''
    : !isEdit && rev.name === 'New automation' && rev.spec.find((b) => b.kind === 'h1')?.text
      ? rev.spec.find((b) => b.kind === 'h1')!.text
      : rev.name
  const titleText = !rev ? ''
    : !isEdit && rev.spec.length === 0 && anyJobBusy ? 'New automation…' : draftName
  // §4.1 uniqueness: case-insensitive, against the store's list, excluding
  // the automation being edited — the same rule the backend 422s on.
  const autoNameTaken = (nm: string) => automations.some((a) =>
    a.id !== (isEdit ? automationId : null) && a.name.trim().toLowerCase() === nm.toLowerCase())
  const commitTitleRename = () => {
    const name = (nameEdit ?? '').trim()
    if (!rev || !name || name === rev.name) { setNameEdit(null); setNameErr(null); return }
    // §11: a collision keeps the input open with the inline error
    if (autoNameTaken(name)) { setNameErr(name); return }
    setNameEdit(null)
    setNameErr(null)
    const prev = rev.name
    up({ name })
    // Edit mode: name is user-owned identity (§4.1) — it applies immediately
    // via PATCH, never rides the draft or waits for Save.
    if (isEdit && auto) void api.patchAutomation(auto.id, { name }).catch((e) => {
      up({ name: prev })
      // §4.1/§19: the backend's duplicate-name 422 surfaces like the inline
      // check (a race with another writer can slip past the client-side test).
      if (/already exists/.test((e as Error).message)) { setNameEdit(name); setNameErr(name) }
      else showToast((e as Error).message)
    })
  }
  // §11 lede: the automation's description — same identity rules as the name, but a
  // blank commit clears it (description is optional, §4.1).
  const commitDescEdit = () => {
    const description = (descEdit ?? '').trim()
    setDescEdit(null)
    if (!rev || description === rev.description) return
    up({ description })
    if (isEdit && auto) void api.patchAutomation(auto.id, { description }).catch((e) => showToast((e as Error).message))
  }
  // §11 chat input send: every message is one §8 chat job — a fresh draft's
  // first message included (the §8 new-automation rule: the agent writes the
  // spec, names the automation through actions, and chains the sync).
  const sendMessage = () => {
    // §11: an open manual edit with unsaved changes asks before the send -
    // cancelling keeps the composer text (sendChat clears it only on proceed).
    guardManualEdit(() => void jobs.sendChat())
  }

  // §11 Clear chat: empties the thread only — the debounced thread persist
  // PUTs `[]`, which unlinks chat.jsonl backend-side (§4.4 thread lifetime:
  // Clear chat is the one user delete). The undo snapshot clears with it (its
  // anchor row leaves with the thread); the draft documents and dirty state
  // are untouched — the thread is no longer draft state, so nothing is marked
  // touched.
  const clearChat = () => {
    setRev((r) => r && ({ ...r, chat: [], undo: null }))
  }

  // §11 draft undo: restore the full pre-request snapshot — the draft looks
  // exactly as it did before the last agent request, chained-sync step
  // rewrites included. Grant sync state is derived, so an intervening
  // agent/secret change keeps its own out-of-sync state regardless.
  const undoDraft = () => {
    setRev((r) => {
      if (!r || !r.undo) return r
      const snap = r.undo
      return {
        ...r,
        spec: snap.spec, steps: snap.steps, params: snap.params, packages: snap.packages,
        triggers: snap.triggers, paramValues: snap.paramValues, concurrency: snap.concurrency,
        testValues: snap.testValues,
        notes: snap.notes,
        dirty: snap.dirty, undo: null, touched: true,
        // §11: the thread records the rollback — persisted, so the agent's §8
        // CONVERSATION context never assumes the undone rewrites still stand
        chat: [...r.chat, newEntry({ kind: 'system', icon: 'fa-rotate-left', text: 'Last change undone — the rewrites above no longer apply.' })],
      }
    })
    showToast('Last change undone.', 3200)
  }

  // §11 Packages card: check statuses once per package list (§19 /packages/check,
  // fast, no pip) — a saved automation whose packages went missing shows
  // "not installed" without waiting for an execution to self-heal.
  // The keys carry the transient-field presence too: switching versions resets
  // statuses/badges without changing the pip list, and must re-trigger the
  // fetch — a pip-only key would leave every row on "checking…" forever.
  const pkgKey = rev ? rev.packages.map((p) => `${p.pip}\t${p.status ? 1 : 0}`).join('\n') : ''
  const pkgOutdatedKey = rev ? rev.packages.map((p) => `${p.pip}\t${p.latest ? 1 : 0}`).join('\n') : ''
  useEffect(() => {
    if (!rev || rev.packages.length === 0 || !rev.packages.some((p) => !p.status)) return
    let stale = false
    void api.checkPackages(rev.packages.map(({ pip, import: imp }) => ({ pip, import: imp })))
      .then(({ packages }) => {
        if (stale) return
        setRev((r) => r && ({
          ...r,
          packages: r.packages.map((p) => {
            const c = packages.find((z) => z.pip === p.pip)
            return p.status || !c ? p : { ...p, status: c.status, version: c.version }
          }),
        }))
      })
      .catch(() => { /* statuses stay unknown; the engine still ensures at execution (§7) */ })
    return () => { stale = true }
  }, [pkgKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11/§6.2 update badges: one read-only PyPI check per package list (§19
  // /packages/outdated) — advisory, a failure just leaves the badges off.
  useEffect(() => {
    if (!rev || rev.packages.length === 0) return
    let stale = false
    void api.outdatedPackages(rev.packages.map(({ pip, import: imp }) => ({ pip, import: imp })))
      .then(({ packages }) => {
        if (stale) return
        setRev((r) => r && ({
          ...r,
          packages: r.packages.map((p) => {
            const c = packages.find((z) => z.pip === p.pip)
            return c ? { ...p, latest: c.latest } : p
          }),
        }))
      })
      .catch(() => { /* badges stay off */ })
    return () => { stale = true }
  }, [pkgOutdatedKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11/§6.2 Update / Update all — pip install --upgrade in the shared
  // directory; no manifest writes, the installed version is the truth. The
  // new version applies to every automation using the package.
  const updatePkgs = async (pips: string[]) => {
    if (!rev || rev.pkgBusy) return
    const targets = rev.packages.filter((p) => p.latest && pips.includes(p.pip))
    if (targets.length === 0) return
    const before = rev.packages
    const list = targets.map(({ pip, import: imp }) => ({ pip, import: imp }))
    up({
      pkgBusy: true,
      packages: rev.packages.map((p) => (targets.includes(p) ? { ...p, status: 'installing' as const, error: undefined } : p)),
    })
    try {
      const { packages } = await api.updatePackages(list)
      setRev((r) => r && ({
        ...r, pkgBusy: false,
        packages: r.packages.map((p) => {
          const c = p.status === 'installing' && packages.find((z) => z.pip === p.pip)
          // merge onto the row — the §19 response carries no `why`
          return c ? { ...p, ...c, latest: undefined } : p
        }),
      }))
      showToast('Updated — the new version applies to every automation using the package.', 3600)
    } catch (e) {
      setRev((r) => r && ({ ...r, pkgBusy: false, packages: before }))
      showToast((e as Error).message)
    }
  }

  // §11: Install / Retry on the Packages card — the blocking §19 ensure; rows
  // show spinners while it runs. An install failure never blocks saving (§6.2).
  const installPkgs = async () => {
    if (!rev || rev.pkgBusy) return
    const list = rev.packages.map(({ pip, import: imp }) => ({ pip, import: imp }))
    up({
      pkgBusy: true,
      packages: rev.packages.map((p) => (p.status === 'installed' ? p : { ...p, status: 'installing' as const, error: undefined })),
    })
    try {
      const { packages } = await api.installPackages(list)
      setRev((r) => r && ({
        ...r, pkgBusy: false,
        packages: r.packages.map((p) => {
          const c = packages.find((z) => z.pip === p.pip)
          // merge onto the row — the §19 response carries no `why`
          return c ? { ...p, ...c } : p
        }),
      }))
    } catch (e) {
      setRev((r) => r && ({
        ...r, pkgBusy: false,
        packages: r.packages.map((p) => (p.status === 'installing' ? { ...p, status: 'missing' as const } : p)),
      }))
      showToast((e as Error).message)
    }
  }

  // §11 blockers-entry apply — same door for every non-clarification source:
  // write the blockers into the spec's "Constraints & resolutions" section,
  // collapse the entry, then sync the steps. §8 user-action blockers are
  // skipped — the Mac isn't ready, there is nothing to amend.
  const applyBlockersEntry = (entry: ChatEntry) => {
    if (!rev) return
    const blockers = (entry.blockers ?? []).filter((b) => b.kind !== 'user-action')
    if (!blockers.length) return
    // §11: starting a sync under an unsaved manual edit asks first
    guardManualEdit(() => {
      setRev((r) => r && ({
        ...r,
        resolved: [...r.resolved, ...blockers.map(blockerLine)],
        chat: r.chat.map((e) => (e.id === entry.id ? { ...e, dismissed: true } : e)),
      }))
      void jobs.runSync(amendSpec(rev.spec, blockers))
    })
  }

  // §7/§9.2 Fix with AI: the editor opened from a failed execution seeds the
  // thread with the failure and sends the §11 canned analyze chat message —
  // an ordinary §8 chat job whose RECENT EXECUTIONS context carries this execution
  // in full detail (§19 executionId). While another job is already in flight only
  // the seed lands; the user asks when it settles.
  const fixConsumed = useRef(false)
  const [fixSend, setFixSend] = useState<string | null>(null)
  useEffect(() => {
    if (!rev || fixConsumed.current) return
    const fx = useStore.getState().fixExec
    if (!fx) return
    fixConsumed.current = true
    useStore.setState({ fixExec: null })
    const ex = executionFull[fx] ?? executions.find((e) => e.id === fx)
    const failure = ex?.error
      ? `Execution failed at step ${ex.error.step ?? '?'} — ${ex.error.message}`
      : 'The execution failed.'
    appendEntry({ kind: 'system', icon: 'fa-circle-exclamation', text: failure })
    if (!ex || ex.status !== 'failed') return
    // §11: the send is deferred until the stored thread merged (chatReady) -
    // the effect below fires it, so the job's §8 CONVERSATION context carries
    // the kept history and the seed entry above instead of a pre-merge thread.
    setFixSend(fx)
  }, [rev != null]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!rev || !chatReady || !fixSend) return
    setFixSend(null)
    // While another §8 job is already in flight only the seed lands (§11);
    // the user asks when it settles.
    if (anyJobBusy || testLive) return
    // The seed entry above already names the failing step — don't repeat it here.
    void jobs.sendChat(`This execution failed — figure out why. If the automation is at fault, change it so it won’t happen again; if the fix is something I need to do on this ${copy.machine} (install or start an app, sign in), tell me what to do and how instead.`, fixSend)
  }, [rev != null, chatReady, fixSend]) // eslint-disable-line react-hooks/exhaustive-deps

  // §11: settled runs seed the thread — entering the editor after the newest
  // Draft execution finished (later than the thread's last entry) appends a
  // run-settled system entry, so the conversation picks up where the run left off.
  const draftRunSeeded = useRef(false)
  useEffect(() => {
    if (!isEdit || !rev || !chatLoaded.current || draftRunSeeded.current) return
    draftRunSeeded.current = true
    const dr = executions
      .filter((e) => e.automationId === automationId && e.versionLabel === 'Draft'
        && (e.status === 'failed' || e.status === 'succeeded'))
      .sort((a, b) => b.startedMs - a.startedMs)[0]
    if (!dr) return
    const entry = newEntry({
      kind: 'system',
      icon: 'fa-vial',
      text: dr.status === 'failed'
        ? `Draft execution failed${dr.error?.step ? ` at step ${dr.error.step}` : ''} — ${dr.error?.message ?? 'see the run'}.`
        : 'Draft execution succeeded.',
    })
    // The duplicate check runs INSIDE the updater: the stored-thread merge
    // (§4.4 thread load above) queues its prepend in the same effects pass,
    // so this effect's own `rev.chat` closure is still pre-merge — only the
    // updater sees the merged thread and can compare against its last entry.
    // Guarded on the entry's own id so a re-run updater stays idempotent.
    setRev((r) => {
      if (!r || r.chat.some((e) => e.id === entry.id)) return r
      const lastAt = r.chat.length ? Date.parse(r.chat[r.chat.length - 1].at ?? '') || 0 : 0
      if (Math.max(dr.endedMs, dr.startedMs) <= lastAt) return r
      return { ...r, chat: [...r.chat, entry] }
    })
  }, [rev != null, chatMergeTick]) // eslint-disable-line react-hooks/exhaustive-deps

  // ---- version menu (edit mode) ----
  const [verOpen, setVerOpen, verRef] = usePopover()
  const pickVersion = (key: 'draft' | number) => {
    if (!auto || !rev) return
    setVerOpen(false)
    if (rev.viewing === key) return
    if (holdsDraftEdits(rev, auto)) {
      draftSnap.current = rev
      void api.putDraft(auto.id, serializeDraft(rev)).catch(() => { /* keep local snapshot */ })
    }
    if (key === 'draft') {
      // §11: the chat thread is one live surface across views - entries that
      // landed while a version was viewed (a settling test's run chip, flushed
      // workflow receipts) stay when the draft returns; only the draft
      // documents restore from the stash. The old-version watcher's pending
      // clear was deliberate (§11), so the stash never re-arms a pending.
      if (draftSnap.current) {
        const snap = draftSnap.current
        setRev((r) => ({
          ...snap, chat: r ? r.chat : snap.chat,
          pendingSync: false, pendingTest: null, viewing: 'draft' as const,
        }))
      } else if (auto.draft) {
        setRev((r) => ({ ...seedFromAuto(auto, agents, secrets.map((s) => s.id)), chat: r ? r.chat : [] }))
      } else setRev((r) => r && loadVersionInto(r, { spec: auto.spec ?? [], steps: auto.steps ?? [], notes: auto.notes, params: auto.params, packages: auto.packages }, 'draft'))
    } else if (key === auto.version) {
      setRev((r) => r && loadVersionInto(r, { spec: auto.spec ?? [], steps: auto.steps ?? [], notes: auto.notes, params: auto.params, packages: auto.packages }, key))
    } else {
      const s = (auto.versions ?? []).find((v) => v.version === key)
      if (s) setRev((r) => r && loadVersionInto(r, s, key))
    }
  }

  // §4.4 delete an old version — the affordance exists only on older rows,
  // so the current version and the Draft can never reach here.
  const [delVer, setDelVer] = useState<number | null>(null)
  const deleteVersion = async (v: number) => {
    if (!auto) return
    try {
      await api.deleteVersion(auto.id, v)
      // Viewing the deleted version → back to the Draft view (§4.4).
      if (rev?.viewing === v) pickVersion('draft')
      await loadAuto(auto.id)
      showToast(`v${v} deleted.`)
    } catch (err) {
      showToast((err as Error).message)
    }
  }

  // ---- leave / start over / save ----
  const close = async () => {
    jobs.stopPoll()
    // §4.4 thread lifetime: the thread flushes on every exit path — the keep
    // branches below set draftSettled, which mutes the unmount flush.
    await flushChat()
    if (isEdit && auto) {
      if (rev && holdsDraftEdits(rev, auto)) {
        try { await api.putDraft(auto.id, serializeDraft(rev)) } catch { /* backend restarting */ }
        draftSettled.current = true
        showToast('Draft kept — resume it from this automation anytime.', 3400)
      }
      setSurface('app')
      go('automation')
      return
    }
    // §4.4: leaving create mode after a draft landed keeps the pending slot.
    if (!isEdit && rev && (rev.spec.length || rev.steps.length)) {
      try { await api.putDraft('pending', serializeDraft(rev), agentId) } catch { /* backend restarting */ }
      draftSettled.current = true
      showToast('Draft kept — Resume draft picks it up anytime.', 3400)
    }
    setSurface('app')
    go('automations')
  }

  const startOver = async () => {
    // an automation that vanished mid-edit has nothing to discard — and must
    // never fall through to the create branch's reset
    if (isEdit && !auto) return
    if (isEdit && auto) {
      // Discard draft → back to detail. Settle BEFORE the awaits — the 1 s
      // debounce timers check the flag at fire time, and a PUT landing after
      // the DELETE would resurrect the discarded draft (§4.4) or clobber the
      // boundary marker the DELETE appends to the kept thread.
      // §11: settling cancels any in-flight §8 job client-side too — the
      // DELETE below also kills the owner's jobs server-side (§19).
      jobs.cancelJob()
      // §11 hold-and-flush: a discard drops the session's held chips with its
      // staging - receipts for discarded staging never reach the kept thread.
      jobs.takeHeldChips()
      draftSettled.current = true
      await flushChat(true) // a settle path: an in-flight job was cancelled above
      await putInFlight.current
      await cancelDraftTest()
      try { await api.deleteDraft(auto.id) } catch { /* none saved yet */ }
      draftSnap.current = null
      setSurface('app')
      go('automation')
      showToast(`Changes discarded — back to v${auto.version} as saved.`, 3200)
      return
    }
    await resetCreate()
  }

  const doSave = async () => {
    if (!rev || saveBlocked) return
    // an automation that vanished mid-edit has nothing to save as a new
    // version — and must never fall through to the create branch
    if (isEdit && !auto) return
    try {
      draftSettled.current = true
      // §4.4 thread lifetime: every entry lands before the boundary marker
      // the save/create endpoint appends; the settle flag above mutes the
      // debounced writers so nothing clobbers the marker after.
      await flushChat()
      if (isEdit && auto) {
        if (typeof rev.viewing === 'number' && rev.viewing !== auto.version) {
          const { version } = await api.restore(auto.id, rev.viewing)
          setSurface('app')
          go('automation')
          showToast(`v${rev.viewing} restored as version ${version} — earlier versions stay in the Version menu.`, 3200)
        } else {
          const { version } = await api.saveVersion(auto.id, {
            draft: serializeDraft(rev), agentId,
            stepAgents: rev.enabledAgents, allowedSecrets: rev.allowedSecrets,
            // §4.2/§19: staged values land beside the draft — matched
            // name+kind against the landing version's definitions.
            ...(Object.keys(rev.paramValues).length ? { paramValues: rev.paramValues } : {}),
            // §8/§19: staged concurrency lands beside the draft, like the PATCH
            ...(rev.concurrency ? { concurrency: rev.concurrency } : {}),
          })
          setSurface('app')
          go('automation')
          // §4.4 operational-only save: same version back means nothing was
          // minted — the staged schedule/value changes still landed.
          showToast(version === auto.version
            ? 'Changes saved — triggers and values updated, no new version needed.'
            : auto.live.length
              ? `Version ${version} saved. ${auto.live.length === 1 ? 'The execution in progress finishes' : `The ${auto.live.length} executions in progress finish`} on v${version - 1} — v${version} applies from the next execution.`
              : `Version ${version} saved — earlier versions are in the Version menu when you edit.`, 3200)
        }
      } else {
        const created = await api.createAutomation({
          draft: serializeDraft(rev), name: rev.name, agentId,
          stepAgents: rev.enabledAgents, allowedSecrets: rev.allowedSecrets,
          ...(Object.keys(rev.paramValues).length ? { paramValues: rev.paramValues } : {}),
          ...(rev.concurrency ? { concurrency: rev.concurrency } : {}),
        })
        // The detail page guards against unknown ids — make sure the store
        // knows the new automation before navigating (WS refresh may lag).
        await useStore.getState().loadAuto(created.id)
        setSurface('app')
        go('automation', { automationId: created.id })
        showToast('Created — nothing has executed yet. Press Execute now when you’re ready.', 3600)
      }
    } catch (e) {
      draftSettled.current = false
      showToast((e as Error).message)
    }
  }

  // ---------- render ----------
  const backLabel = isEdit ? (auto?.name ?? 'Automation') : 'Automations'
  // §11 create empty state: no spec, no steps yet — the chat pane shows the
  // headline + example chips (only while the thread is empty and nothing
  // runs) and the review cards their placeholders.
  const isCreateEmpty = !isEdit && !!rev && rev.spec.length === 0 && rev.steps.length === 0
  const inputDisabled = anyJobBusy || testLive || viewingOld
  // §11 history-inert rule: the out-of-sync note anchors only to the current
  // session's last rewrite — a settled session's rewrite never carries it.
  const lastRewriteId = rev ? [...chatSinceBoundary(rev.chat)].reverse().find((e) => e.kind === 'rewrite')?.id : undefined

  return (
    <div style={{
      minHeight: '100%', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'flex-start' }}>
        {/* ===== chat panel (§11) — floating card matching the §9 rail rhythm:
            top 46 / bottom 12, 12px radius, 12px left gap; starts below the drag
            strips and the traffic lights, so no header padding or no-drag needed ===== */}
        {rev && (
          <ChatPanel
            rev={rev}
            agents={agents}
            selAgent={selAgent}
            isEdit={isEdit}
            isCreateEmpty={isCreateEmpty}
            anyJobBusy={anyJobBusy}
            busyRewrite={busyRewrite}
            testLive={testLive}
            viewingOld={viewingOld}
            inputDisabled={inputDisabled}
            outOfSync={outOfSync}
            syncDisabled={syncDisabled}
            lastRewriteId={lastRewriteId}
            chatText={chatText}
            setChatText={setChatText}
            sendMessage={sendMessage}
            undoDraft={undoDraft}
            runSync={() => guardManualEdit(() => void jobs.runSync())}
            runDraftTest={() => setTestRunSignal((s) => s + 1)}
            analyzeFailure={analyzeFailure}
            patchEntry={patchEntry}
            applyBlockersEntry={applyBlockersEntry}
            clearChat={clearChat}
            cancelChat={jobs.cancelChat}
            cancelSync={jobs.cancelSync}
            setAgentId={setAgentId}
            up={up}
            showToast={showToast}
          />
        )}

        {/* ===== review pane ===== */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* header */}
          <div className="ad-anim-page" style={{ padding: '20px 0 0' }}>
            <div style={{
              maxWidth: 1800, margin: '0 auto', padding: '0 30px 0 18px',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <BackLink label={backLabel} onClick={() => void close()} />
            </div>
          </div>
          {!rev && <PageLoading />}
          {rev && (
          <div className="ad-anim-page" style={{ maxWidth: 1800, margin: '0 auto', padding: '0 30px 70px 18px' }}>
            {/* title row */}
            <PageTitle
              raw
              style={{ marginBottom: 6 }}
              right={(
                <HeaderActions>
                  {saveBlocked && !(isCreateEmpty && !anyJobBusy) && (
                    <span style={{ font: "400 12.5px var(--sans)", color: 'var(--amber)' }}>
                      {rev.syncBusy || rev.chatBusy
                        ? jobStageTitle(rev)
                        : 'Sync and review the steps before saving'}
                    </span>
                  )}
                  <button className="ad-btn-text dim" disabled={busyRewrite} onClick={() => void startOver()}>
                    {isEdit ? 'Discard draft' : 'Start over'}
                  </button>
                  {isEdit && (rev.touched || !!auto?.draft) && (
                    <button className="ad-btn-ghost" onClick={() => void close()}>
                      Keep draft
                    </button>
                  )}
                  <BtnPrimary onClick={() => void doSave()} disabled={saveBlocked}>
                    {isEdit && auto
                      ? (viewingOld ? `Restore v${rev.viewing} as v${auto.version + 1}` : `Save as v${auto.version + 1}`)
                      : 'Create automation'}
                  </BtnPrimary>
                </HeaderActions>
              )}
            >
              {nameEdit !== null ? (
                <input
                  className={`ad-input${nameErr ? ' invalid' : ''}`}
                  value={nameEdit}
                  onChange={(e) => { setNameEdit(e.target.value); setNameErr(null) }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitTitleRename()
                    if (e.key === 'Escape') { setNameEdit(null); setNameErr(null) }
                  }}
                  onBlur={commitTitleRename}
                  autoFocus
                  style={{
                    // §14 sanctioned exception: the title rename field renders at the
                    // .ad-h1 scale so the row never jumps between reading and editing.
                    fontSize: 20, fontWeight: 600, letterSpacing: '-.01em', padding: '2px 10px',
                    minWidth: 0, flex: '0 1 auto', width: 420,
                  }}
                />
              ) : canRename ? (
                <div className="ad-title-rename always" title={draftName}>
                  <h1 className="ad-h1" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {titleText}
                  </h1>
                  <button className="pencil" title="Rename" onClick={() => setNameEdit(draftName)}>
                    <i className="fa-solid fa-pencil" />
                  </button>
                </div>
              ) : (
                <h1 className="ad-h1" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {titleText}
                </h1>
              )}
              {isEdit && auto && (
                <div ref={verRef} style={{ position: 'relative' }}>
                  <button className="ad-btn-pill" data-testid="version-menu" disabled={busyRewrite} onClick={() => setVerOpen(!verOpen)}>
                    <span>{rev.viewing === 'draft' ? 'Draft' : `v${rev.viewing}${rev.viewing === auto.version ? ' · current' : ''}`}</span>
                    <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 9 }} />
                  </button>
                  <PopMenu show={verOpen} style={{ top: 'calc(100% + 6px)', left: 0, minWidth: 360, padding: 0, overflow: 'hidden' }}>
                    {/* §4.4: the current version is never a selectable option — the Draft
                        is its working copy. Inert header only, like the detail-page menu. */}
                    <MenuItemRow
                      header
                      mono
                      title={`v${auto.version} · current`}
                      sub={`Your draft builds on this — Save lands it as v${auto.version + 1}.`}
                    />
                    {/* a long version history scrolls inside the menu instead of past the window */}
                    <ScrollArea style={{ maxHeight: '60vh' }}>
                      {([
                        {
                          key: 'draft' as const, label: 'Draft',
                          sub: 'your working copy — unsaved',
                        },
                        ...(auto.versions ?? []).map((v) => ({
                          key: v.version, label: `v${v.version}`, sub: v.when + (v.note ? ' · ' + v.note : ''),
                          // §4.4: only older rows are deletable — the Draft and
                          // current rows hide the affordance (never disabled).
                          del: true,
                        })),
                      ]).map((it) => {
                        const sel = rev.viewing === it.key
                        return (
                          <MenuItemRow
                            key={String(it.key)}
                            mono
                            title={it.label}
                            sub={it.sub}
                            selected={sel}
                            onPick={() => pickVersion(it.key)}
                            trailing={'del' in it && it.del ? (
                              <button
                                className="ad-btn-icon danger"
                                title={`Delete v${it.key}`}
                                aria-label={`Delete v${it.key}`}
                                data-testid={`delete-version-${it.key}`}
                                onClick={(e) => { e.stopPropagation(); setVerOpen(false); setDelVer(it.key as number) }}
                              >
                                <i className="fa-solid fa-trash-can" />
                              </button>
                            ) : undefined}
                          />
                        )
                      })}
                    </ScrollArea>
                  </PopMenu>
                </div>
              )}
            </PageTitle>
            {/* §4.1/§11: the title input's duplicate-name inline error — the
                §12 agent-form treatment (red dot + red text, clears on typing) */}
            {nameEdit !== null && nameErr && (
              <div className="ad-anim-item" style={{ display: 'flex', alignItems: 'center', gap: 7, margin: '0 0 8px' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--red)', flex: 'none' }} />
                <span style={{ fontWeight: 500, fontSize: 12.5, color: 'var(--red-text)' }}>
                  An automation named {nameErr} already exists — pick a different name.
                </span>
              </div>
            )}
            {/* lede: the automation's description (§4.1) — editable like the name; create mode
                shows the static drafting lede until the draft holds a spec. The row is
                height-stable: every state shares one fixed-height box. The drafting-agent
                picker lives in the chat pane composer, not here. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 13, height: 30, minWidth: 0, margin: '0 0 20px' }}>
              {!isEdit && rev.spec.length === 0 ? (
                <p style={{
                  font: "400 13.5px/1.6 var(--sans)", color: 'var(--text-muted)', margin: 0, minWidth: 0,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  Read what your AI wrote. Change anything — nothing executes until you create it.
                </p>
              ) : descEdit !== null ? (
                <input
                  className="ad-input compact"
                  value={descEdit}
                  onChange={(e) => setDescEdit(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitDescEdit()
                    if (e.key === 'Escape') setDescEdit(null)
                  }}
                  onBlur={commitDescEdit}
                  autoFocus
                  placeholder="What this automation does — one line"
                  style={{ height: 30, width: 640, maxWidth: '100%' }}
                />
              ) : (
                <div
                  className={canRename ? 'ad-title-rename always' : undefined}
                  style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}
                >
                  <p style={{
                    font: "400 13.5px/1.6 var(--sans)", margin: 0, minWidth: 0,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    color: rev.description ? 'var(--text-muted)' : 'var(--text-faint)',
                  }}>
                    {rev.description || (canRename ? 'No description yet — press the pencil to add one.' : 'No description yet.')}
                  </p>
                  {canRename && (
                    <button className="pencil" title="Edit the description" onClick={() => setDescEdit(rev.description)}>
                      <i className="fa-solid fa-pencil" />
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* old-version banner */}
            {viewingOld && auto && (
              <Notice tone="accent" className="ad-anim-item" style={{ margin: '0 0 18px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                  <span style={{ flex: 1 }}>
                    {`Loaded v${rev.viewing} from history. Saving restores it as v${auto.version + 1} — your draft stays in the Version menu.`}
                  </span>
                  <button className="ad-btn-soft" disabled={busyRewrite} onClick={() => pickVersion('draft')} style={{ flex: 'none' }}>
                    Back to draft
                  </button>
                </div>
              </Notice>
            )}

            {/* live-execution note */}
            {isEdit && !!auto?.live.length && (
              <Notice tone="cyan" className="ad-anim-item" style={{ margin: '0 0 18px' }}>
                {`An execution is happening right now on v${auto.version}. Saving won’t interrupt it — that execution finishes on v${auto.version}. v${auto.version + 1} takes over from the next execution (${nextTriggerShort(auto.triggers, trigPreviews) ?? auto.triggerChip}).`}
              </Notice>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.05fr) minmax(0,.95fr)', gap: 18, alignItems: 'start' }}>
              {/* ===== left column ===== */}
              <LeftColumn
                rev={rev}
                up={up}
                fw={fw}
                bld={bld}
                isEdit={isEdit}
                isCreateEmpty={isCreateEmpty}
                busyRewrite={busyRewrite}
                viewingOld={viewingOld}
                testLive={testLive}
                lockStyle={lockStyle}
                agents={agents}
                secrets={secrets}
                unresolvedReferences={unresolvedRefs}
                availAgents={availAgents}
                agentStepIdx={agentStepIdx}
                agWarn={agWarn}
                agNone={agNone}
                agNotEnabled={agNotEnabled}
                agMissing={agMissing}
                agFallbackIdx={agFallbackIdx}
                secWarn={secWarn}
                secNotAllowed={secNotAllowed}
                secMissing={secMissing}
                secRefs={secRefs}
                specOpenEff={specOpenEff}
                agSecOpenEff={agSecOpenEff}
                secSecOpenEff={secSecOpenEff}
                instrOpenEff={instrOpenEff}
                notesOpenEff={notesOpenEff}
                showToast={showToast}
              />

              {/* ===== right column ===== */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <BuildCard
                  rev={rev}
                  outOfSync={outOfSync}
                  syncDisabled={syncDisabled}
                  agentGap={agentGap}
                  runSync={() => guardManualEdit(() => void jobs.runSync())}
                />
                <TestCard
                  rev={rev}
                  up={up}
                  appendEntry={appendEntry}
                  isEdit={isEdit}
                  auto={auto}
                  outOfSync={outOfSync}
                  anyJobBusy={anyJobBusy}
                  busyRewrite={busyRewrite}
                  viewingOld={viewingOld}
                  lockStyle={lockStyle}
                  runSync={() => guardManualEdit(() => void jobs.runSync())}
                  flushHeldChips={jobs.flushHeldChips}
                  sendChat={async (text?: string, executionId?: string) =>
                    guardManualEdit(() => void jobs.sendChat(text, executionId))}
                  runTestSignal={testRunSignal}
                  isCreateEmpty={isCreateEmpty}
                />
                <RightCards
                  rev={rev}
                  up={up}
                  liveParams={auto?.params}
                  liveConcurrency={auto ? { maxParallel: auto.maxParallel, maxQueued: auto.maxQueued } : undefined}
                  drafting={!isEdit && anyJobBusy && rev.steps.length === 0}
                  isCreateEmpty={isCreateEmpty && !anyJobBusy}
                  outOfSync={outOfSync}
                  busyRewrite={busyRewrite}
                  availAgents={availAgents}
                  agents={agents}
                  secrets={secrets}
                  unresolvedReferences={unresolvedRefs}
                  stepHistory={stepHistory}
                  pkgSecOpenEff={pkgSecOpenEff}
                  updatePkgs={(pips) => void updatePkgs(pips)}
                  installPkgs={() => void installPkgs()}
                />
              </div>
            </div>
          </div>
          )}
        </div>
      </div>

      {delVer != null && (
        <ConfirmModal
          title={`Delete v${delVer}?`}
          body={(
            <>
              v{delVer} is deleted from the version history. This can’t be undone.
              Past executions of v{delVer} stay in Executions.
            </>
          )}
          confirmLabel={`Delete v${delVer}`}
          danger
          onConfirm={() => { const v = delVer; setDelVer(null); void deleteVersion(v) }}
          onCancel={() => setDelVer(null)}
        />
      )}

      {/* §11: a send or sync under an unsaved manual edit asks first -
          confirming discards the edits (the job start resets the editor
          state) and proceeds; cancelling aborts with the composer text kept */}
      {confirmEditDiscard && (
        <ConfirmModal
          title={confirmEditDiscard.doc === 'spec' ? 'Discard your spec edits?' : 'Discard your notes edits?'}
          body={confirmEditDiscard.doc === 'spec' ? 'The changes you typed into the spec editor will be lost.'
            : 'The changes you typed into the notes editor will be lost.'}
          confirmLabel="Discard edits"
          danger
          onConfirm={() => { const { proceed } = confirmEditDiscard; setConfirmEditDiscard(null); proceed() }}
          onCancel={() => setConfirmEditDiscard(null)}
        />
      )}

    </div>
  )
}
