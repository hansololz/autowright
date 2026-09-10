// §11 chat pane — the editor's only conversational surface: the floating card
// beside the review grid holding the thread (user bubbles right; every
// agent-side entry left-aligned in the Claude-output style with option-button
// rows, plus the transient in-thread progress entry — the page's only live job
// surface), the create empty state, and the pinned composer with the
// drafting-agent picker and Clear chat.
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { usePlatformCopy } from '../../platformCopy'
import { useStore } from '../../store'
import type { Agent, ChatEntry } from '../../types'
import { BtnGhost, BtnPrimary, ConfirmModal, Eyebrow, MenuItemRow, PopMenu, ScrollArea, Spinner, agName, anyModalOpen, dispModel, durationLabel, usePopover, waitedLabel } from '../../ui'
import { devlogOverlayOpen } from '../../devlog'
import { Markdown } from '../../result'
import { type Rev, answerHeader, jobStageTitle, stageDoingBullet } from './model'

/** §11 action row — left-aligned wrapping pill row beneath an agent block
    (the turn action row and the per-entry rows share the layout). */
function ActionRow({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, justifyContent: 'flex-start', ...style }}>
      {children}
    </div>
  )
}

/** §11 block glyph box — the 13px box every block header leads with, so
    titles align across kinds and a glyph swap never shifts the text. */
function GlyphBox({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ width: 13, height: 13, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {children}
    </span>
  )
}

/** §11 operation-block bullet — `• `-prefixed description line running the
    pane's full width, flush left with the glyph (never indented under the
    title). `ellipsis` keeps activity-feed lines single-line. */
function OpBullet({ text, ellipsis, color, duration }: {
  text: string; ellipsis?: boolean; color?: string
  // §11 per-step duration stamp — right-aligned quiet mono, the §7
  // execution-step style, so the two step lists read alike
  duration?: string
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'baseline', gap: 8,
      font: '400 11.5px/1.5 var(--sans)', color: color ?? 'var(--text-faint)',
    }}>
      <span style={{
        flex: 1, minWidth: 0,
        ...(ellipsis ? { whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' } : { overflowWrap: 'break-word' as const }),
      }}>
        •&nbsp; {text}
      </span>
      {duration && (
        <span style={{ fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--text-faint)', flex: 'none' }}>{duration}</span>
      )}
    </div>
  )
}

/** §11 message-block header — glyph beside the thread's loudest title line. */
function MsgHeader({ icon, color, title }: { icon: string; color: string; title: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
      <GlyphBox><i className={`fa-solid ${icon}`} style={{ fontSize: 11, color }} /></GlyphBox>
      <span style={{ font: "600 13px var(--sans)", color: 'var(--text)' }}>{title}</span>
    </div>
  )
}

/** §11 two block families: operation blocks (the record of what the agent
    did) vs message blocks (the agent talking to the user) — drives the
    boundary spacing (uniform 12px between groups / 0 between chained ops). */
const entryFamily = (e: ChatEntry): 'user' | 'msg' | 'op' =>
  e.kind === 'user' ? 'user'
    : e.kind === 'answer' || e.kind === 'blockers' || e.kind === 'error' ? 'msg' : 'op'
const familyGap = (prev: ChatEntry | null, cur: ChatEntry): number => {
  if (!prev) return 0
  if (prev.boundary) return 12 // the marker group is its own band (§11 thread spacing)
  return entryFamily(prev) === 'op' && entryFamily(cur) === 'op' ? 0 : 12
}

/** Drafting-agent picker — lives in the chat pane composer (§11); menu opens
    upward over the thread, left-aligned so it stays inside the pane. */
function AgentPick({ agents, selected, onPick, disabled }: {
  agents: Agent[]; selected: Agent | null; onPick: (g: Agent) => void; disabled?: boolean
}) {
  const [open, setOpen, ref] = usePopover()
  return (
    <div ref={ref} style={{ position: 'relative', flex: '0 1 auto', minWidth: 0 }}>
      <button
        className="ad-btn-pill" disabled={disabled}
        onClick={() => setOpen(!open)}
        title="The agent that writes the spec and generates the steps"
        style={{ maxWidth: '100%' }}
      >
        <i className="fa-solid fa-microchip" style={{ color: 'var(--text-faint)', fontSize: 9 }} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selected ? `${agName(selected)} · ${dispModel(selected)}` : 'No agent'}</span>
        <i className="fa-solid fa-caret-down" style={{ color: 'var(--text-faint)', fontSize: 9 }} />
      </button>
      <PopMenu show={open} style={{ bottom: 'calc(100% + 6px)', left: 0, minWidth: 290 }}>
          {agents.map((g) => (
            <MenuItemRow
              key={g.id}
              title={agName(g)}
              sub={dispModel(g)}
              subMono
              selected={!!selected && g.id === selected.id}
              onPick={() => { setOpen(false); onPick(g) }}
            />
          ))}
          <div style={{ padding: '9px 14px', font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)' }}>
            Writes the spec and generates the steps for this automation. Autowright still executes everything.
          </div>
      </PopMenu>
    </div>
  )
}

export interface ChatPanelProps {
  rev: Rev
  agents: Agent[]
  selAgent: Agent | null
  isEdit: boolean
  isCreateEmpty: boolean
  anyJobBusy: boolean
  busyRewrite: boolean
  testLive: boolean
  viewingOld: boolean
  inputDisabled: boolean
  outOfSync: boolean
  syncDisabled: boolean
  lastRewriteId: string | undefined
  chatText: string
  setChatText: (v: string) => void
  sendMessage: () => void
  undoDraft: () => void
  runSync: () => void
  // §11 turn action row: starts a draft test through the TEST card —
  // the same run as its Run test button
  runDraftTest: () => void
  // §11 turn action row: sends the canned analyze message — null while the
  // draft's tracked test didn't settle failed (the pill hides)
  analyzeFailure: (() => void) | null
  patchEntry: (id: string, patch: Partial<ChatEntry>) => void
  applyBlockersEntry: (entry: ChatEntry) => void
  clearChat: () => void
  cancelChat: () => void
  cancelSync: () => void
  setAgentId: (id: string) => void
  up: (patch: Partial<Rev>) => void
  showToast: (msg: string, ms?: number) => void
}

export function ChatPanel({
  rev, agents, selAgent, isEdit, isCreateEmpty, anyJobBusy, busyRewrite,
  testLive, viewingOld, inputDisabled, outOfSync, syncDisabled,
  lastRewriteId, chatText, setChatText, sendMessage,
  undoDraft, runSync, runDraftTest, analyzeFailure, patchEntry,
  applyBlockersEntry, clearChat, cancelChat, cancelSync, setAgentId, up, showToast,
}: ChatPanelProps) {
  // §9 per-OS copy rule: the machine noun the create empty state names.
  const copy = usePlatformCopy()
  // §11 per-OS top offset, matching the §9 rail: 53 below the Windows title
  // bar (41px extent + 12px gap, mirroring the bottom gap), 46 elsewhere.
  const platformOs = useStore((s) => s.platformOs)
  const panelTop = platformOs === 'windows' ? 53 : 46
  // §11 thread auto-scroll: newest at the bottom, scrolled on new content —
  // and on the transient progress entry appearing (a job starts) — only while
  // the user is at (or near) the bottom, the same 60 px rule the feed follow
  // below uses. Sending is the one exception: the user's own bubble always
  // pins the thread to the bottom.
  const chatScrollRef = useRef<HTMLDivElement | null>(null)
  const chatLen = rev.chat.length
  const ownMessageLast = rev.chat[chatLen - 1]?.kind === 'user'
  useEffect(() => {
    const el = chatScrollRef.current
    if (!el) return
    if (ownMessageLast || el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight
  }, [chatLen, anyJobBusy, ownMessageLast])
  // §11: while the progress entry's feed grows, follow it only when already at
  // (or near) the bottom — a user who scrolled up is never yanked back down.
  useEffect(() => {
    const el = chatScrollRef.current
    if (!el || !anyJobBusy) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight
  }, [anyJobBusy, rev.genDetail, rev.genEvents])
  // §11 live durations: the progress entry's elapsed stamps tick client-side
  // once per second from the §8 stage-timing stamps — the 700 ms poll is
  // never the tick source.
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!anyJobBusy) return
    setNowSeconds(Date.now() / 1000)
    const tick = setInterval(() => setNowSeconds(Date.now() / 1000), 1000)
    return () => clearInterval(tick)
  }, [anyJobBusy])

  // §11 Clear chat: confirm step before the thread is emptied.
  const [confirmClear, setConfirmClear] = useState(false)

  // §11 composer cancel — one dispatch for the Cancel button and Esc. Marks the
  // input for refocus once the cancel re-enables it (effect below the auto-grow
  // block), so editing the returned request text picks up where it left off.
  const focusAfterCancelRef = useRef(false)
  const cancelJob = () => {
    focusAfterCancelRef.current = true
    if (rev.chatBusy) cancelChat()
    else if (rev.syncBusy) cancelSync()
  }

  // §11 Esc-to-cancel: a keyboard shortcut for the composer's Cancel while a §8
  // job is in flight — never the draft test's Cancel — yielding to surfaces
  // that own Esc while open (the modal stack, the §9.3 developer log overlay).
  // Latest-ref pattern: cancelJob closes over per-render callbacks, and deps
  // on them would tear down and re-add the document listener on every 1 Hz
  // duration tick for the job's whole run.
  const cancelJobRef = useRef(cancelJob)
  cancelJobRef.current = cancelJob
  useEffect(() => {
    if (!anyJobBusy) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || anyModalOpen() || devlogOverlayOpen()) return
      cancelJobRef.current()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [anyJobBusy])

  // Chat-input auto-grow (ask-box pattern). Runs when the text changes and once
  // when the textarea attaches (it mounts after `rev` loads and again whenever a
  // job's busy footer swaps back to the input, so the mount-time effect pass
  // misses it — an unsized box would then jump on the first keystroke). Pins the
  // thread's scrollTop across the transient height:auto collapse, which
  // otherwise clamps the thread upward while typing.
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
  const sizeChatInput = () => {
    const el = chatInputRef.current
    if (!el) return
    const sc = chatScrollRef.current
    const keep = sc?.scrollTop ?? 0
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    if (sc) sc.scrollTop = keep
  }
  const attachChatInput = useCallback((el: HTMLTextAreaElement | null) => {
    chatInputRef.current = el
    if (el) sizeChatInput()
  }, [])
  useLayoutEffect(() => { sizeChatInput() }, [chatText])

  // §11: after a composer cancel re-enables the input, focus it with the caret
  // at the end of the returned request text (the auto-grow above already sized
  // the box to it).
  useLayoutEffect(() => {
    if (anyJobBusy || !focusAfterCancelRef.current) return
    focusAfterCancelRef.current = false
    const el = chatInputRef.current
    if (!el || el.disabled) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [anyJobBusy])

  // §11 blockers-entry copy, by source and kind
  const blockersHeadline = (e: ChatEntry) => {
    const bl = e.blockers ?? []
    // §8 kind: user-action — the Mac isn't ready, the automation is fine
    if (bl.length > 0 && bl.every((b) => b.kind === 'user-action')) return 'Your AI needs you to do something first'
    if (e.diagnosed) return 'The build failed — your AI suggests these fixes'
    return bl.length > 1 ? `Your AI hit ${bl.length} blockers` : 'Your AI hit a blocker'
  }
  // §11: two sources — chat (clarification) and sync.
  const blockersExplainer = (e: ChatEntry) =>
    e.source === 'chat'
      ? 'Reply below — your answer is sent back and the spec is rewritten.'
      : 'It couldn’t sync the steps with the spec.'

  // §4.4/§11 history is inert: entries at or before the newest boundary
  // marker belong to a settled draft — they render, but offer no actions.
  let lastBoundaryIdx = -1
  for (let i = rev.chat.length - 1; i >= 0; i--) {
    if (rev.chat[i].boundary) { lastBoundaryIdx = i; break }
  }

  // §11 turn action row — the thread's one suggestion surface: pills beneath
  // the last agent-side entry while nothing runs; hides when no pill applies
  // and when the thread ends on a boundary marker (§11 history-inert rule:
  // no Undo/Sync/Test/Analyze pills dangle under a settled session).
  const rowsAllowed = !anyJobBusy && !viewingOld && !testLive
  const lastEntry = rev.chat.length ? rev.chat[rev.chat.length - 1] : null
  const undoAtEnd = rowsAllowed && !!rev.undo && !!lastEntry && rev.undo.entryId === lastEntry.id
  const showSyncPill = outOfSync && rev.dirty && !syncDisabled && !rev.pendingSync
  const showTestPill = !outOfSync && rev.steps.length > 0 && !rev.pendingSync && !rev.pendingTest
  const showTurnRow = rowsAllowed && !!lastEntry && lastEntry.kind !== 'user' && !lastEntry.boundary
    && (undoAtEnd || showSyncPill || showTestPill || !!analyzeFailure)
  const pillGlyph: React.CSSProperties = { fontSize: 9, color: 'var(--text-faint)' }
  // §11 composer: while the thread ends on a "Question for you" block, the
  // placeholder invites the answer; any entry after the question reverts it.
  const awaitingAnswer = lastEntry?.kind === 'answer' && lastEntry.title === 'Question for you'

  return (
    <div className="ad-card" style={{
      width: 'clamp(340px, 26vw, 420px)', flex: 'none', alignSelf: 'flex-start',
      position: 'sticky', top: panelTop, marginTop: 6, marginLeft: 12,
      height: `calc(100vh - ${panelTop + 12}px)`,
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* thread — no header row (§11); the composer below carries the pane's identity */}
      {/* §11 thread spacing: no flex gap — each entry carries its own top
          margin (uniform 12px group gap, 0 between chained operation blocks) */}
      <ScrollArea scrollRef={chatScrollRef} testId="chat-thread" wrapStyle={{ flex: 1, minHeight: 0 }} style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column' }}>
        {rev.chat.length === 0 && !anyJobBusy && (isCreateEmpty ? (
          <div style={{ padding: '10px 4px' }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>
              What should Autowright do for you?
            </h2>
            <p style={{ font: "400 12.5px/1.55 var(--sans)", color: 'var(--text-muted)', margin: '0 0 20px' }}>
              Describe the job in plain words. Your AI writes it as scripts — you review everything before it executes.
            </p>
            <Eyebrow>OR START FROM AN EXAMPLE</Eyebrow>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '10px 0 20px' }}>
              {[
                { label: 'Track manga chapters', icon: 'fa-solid fa-book-open', s: 'Check the manga I follow for new chapters every morning at 8.' },
                { label: 'Back up a folder every night', icon: 'fa-solid fa-box-archive', s: 'Back up my Projects folder to the Vault drive every night at 2.' },
                { label: 'Email me a weekly report', icon: 'fa-solid fa-envelope', s: 'Gather the week’s numbers and email the team a summary every Monday at 9.' },
                { label: 'Watch a product’s price', icon: 'fa-solid fa-tag', s: 'Watch the price of the keyboard I want and tell me when it drops below €120.' },
                { label: 'Tidy my screenshots folder', icon: 'fa-solid fa-broom', s: 'File my desktop screenshots into monthly folders every Sunday night.' },
                { label: 'Log ideas from Discord', icon: 'fa-brands fa-discord', s: 'When a message in my Discord #ideas channel starts with “idea:”, append it to my ideas file and react with a thumbs-up.' },
              ].map((c) => (
                <button key={c.label} className="ad-chip-btn" onClick={() => setChatText(c.s)}>
                  <i className={c.icon} />
                  {c.label}
                </button>
              ))}
            </div>
            <div style={{ font: "400 11.5px/1.6 var(--sans)", color: 'var(--text-muted)', marginTop: 14 }}>
              Your AI writes the steps — Autowright still executes everything on this {copy.machine}.
            </div>
          </div>
        ) : (
          <div style={{ font: "400 12.5px/1.6 var(--sans)", color: 'var(--text-muted)', padding: '26px 4px' }}>
            Ask anything, or describe a change — your AI answers here and rewrites the spec when you ask for changes.
          </div>
        ))}
        {rev.chat.map((e, i) => {
          // §11 thread spacing: uniform 12px group gap, 0 between chained
          // operation blocks
          const prev = i > 0 ? rev.chat[i - 1] : null
          const mt = familyGap(prev, e)
          // §11 history-inert rule: at or before the newest boundary marker
          const history = i <= lastBoundaryIdx
          // §11 draft undo: at the thread's end the pill leads the turn action
          // row (below the map); when later answer-only turns pushed the
          // snapshot's anchor off the end, it renders as its own row beneath
          // the anchor — below everything the request changed
          const undoRow = e.id === rev.undo?.entryId && !undoAtEnd && rowsAllowed ? (
            <ActionRow style={{ marginTop: 10 }}>
              <button className="ad-btn-pill action" onClick={undoDraft}>
                <i className="fa-solid fa-rotate-left" style={{ fontSize: 9, color: 'var(--text-faint)' }} />
                Undo this change
              </button>
            </ActionRow>
          ) : null
          if (e.kind === 'user') {
            return (
              <div key={e.id} style={{
                marginTop: mt,
                font: "500 12.5px/1.5 var(--sans)", color: 'var(--text-2)',
                background: 'var(--bg-inset)', border: '1px solid var(--hairline)',
                borderRadius: 10, padding: '8px 12px', alignSelf: 'flex-end', maxWidth: '92%',
                whiteSpace: 'pre-wrap', overflowWrap: 'break-word',
              }}>
                {e.text}
              </div>
            )
          }
          if (e.kind === 'answer') {
            // §11 message block: header stamped at creation (§4.4 icon/title);
            // entries persisted before the fields existed get the plain header
            const hdr = e.icon && e.title ? { icon: e.icon, title: e.title } : answerHeader(false)
            return (
              <div key={e.id} style={{ marginTop: mt }}>
                <MsgHeader icon={hdr.icon} color="var(--accent)" title={hdr.title} />
                <Markdown small text={e.text ?? ''} />
              </div>
            )
          }
          if (e.kind === 'activity') {
            // §11: a settled job — the live progress entry's exact layout with
            // an outcome glyph in the spinner's 13px box (same size, no text
            // shift): green check done, amber check blocked, red X failed (a
            // pre-outcome-field entry renders as done), the stage label kept,
            // and the full event feed beneath, flush left with the glyph
            // §4.4 eventDurationsMs is parallel to the raw text lines — pair
            // before the empty-line filter so the indexes stay aligned
            const lines = (e.text ?? '').split('\n')
              .map((t, i) => ({ text: t, durationMs: e.eventDurationsMs?.[i] }))
              .filter((l) => l.text)
            const failed = e.outcome === 'failed'
            const glyphColor = failed ? 'var(--red)' : e.outcome === 'blocked' ? 'var(--amber)' : 'var(--green)'
            return (
              <div key={e.id} style={{ marginTop: mt }}>
                {e.title && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 3 }}>
                    <GlyphBox>
                      <i className={`fa-solid ${failed ? 'fa-xmark' : 'fa-check'}`} style={{ fontSize: 11, color: glyphColor }} />
                    </GlyphBox>
                    <div style={{ flex: 1, minWidth: 0, font: "500 12.5px var(--sans)", color: 'var(--text-muted)' }}>{e.title}</div>
                  </div>
                )}
                {lines.map((l, i) => (
                  <OpBullet key={`${i}-${l.text}`} text={l.text} ellipsis
                    duration={l.durationMs != null ? durationLabel(l.durationMs) : undefined} />
                ))}
              </div>
            )
          }
          if (e.kind === 'rewrite') {
            // §11 rewrite chip — "Spec updated." in the system-chip role; the
            // stored request text feeds the agent's §8 context, never echoed
            // here (the user bubble above shows it), and the derived amber
            // out-of-sync line renders at the thread's end (below the map)
            return (
              <React.Fragment key={e.id}>
              <div className="ad-anim-item" style={{ marginTop: mt, display: 'flex', alignItems: 'center', gap: 10 }}>
                <GlyphBox><i className="fa-solid fa-file-pen" style={{ fontSize: 10, color: 'var(--text-faint)' }} /></GlyphBox>
                <div style={{ flex: 1, minWidth: 0, font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-faint)' }}>Spec updated.</div>
              </div>
              {undoRow}
              </React.Fragment>
            )
          }
          if (e.kind === 'blockers') {
            const blockers = e.blockers ?? []
            // §11: a history blockers entry always collapses to its summary,
            // whatever its stored flag says — the draft it blocked is settled
            if (e.dismissed || history) {
              return (
                <div key={e.id} style={{ marginTop: mt, display: 'flex', alignItems: 'center', gap: 10, font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)' }}>
                  <GlyphBox><i className="fa-solid fa-ban" style={{ fontSize: 10, color: 'var(--text-faint)' }} /></GlyphBox>
                  <span>{blockers.length} blocker{blockers.length === 1 ? '' : 's'} — dismissed</span>
                </div>
              )
            }
            const clarify = e.source === 'chat'
            const allUserAction = blockers.length > 0 && blockers.every((b) => b.kind === 'user-action')
            // §11: a mixed entry keeps the source's primary button — it applies
            // only the ordinary blockers' resolutions; a pure user-action entry
            // offers Dismiss only (there is nothing to amend)
            const ordinary = blockers.filter((b) => b.kind !== 'user-action')
            const mdBody = (text: string) => (
              <div style={{ margin: '3px 0 8px' }}>
                <Markdown small text={text} />
              </div>
            )
            return (
              <div key={e.id} className="ad-anim-item" style={{ marginTop: mt, textAlign: 'left' }}>
                <MsgHeader icon="fa-ban" color="var(--amber)" title={blockersHeadline(e)} />
                {!allUserAction && (
                  <div style={{ font: "400 12.5px/1.6 var(--sans)", color: 'var(--text-muted)', margin: '0 0 10px' }}>
                    {blockersExplainer(e)}
                  </div>
                )}
                {/* §11: blockers render as agent output — reason/fix/details
                    through the shared Markdown renderer, links clickable */}
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  {blockers.map((b, i) => (
                    <div key={i}>
                      {blockers.length > 1 && (
                        <Eyebrow style={{ color: 'var(--amber)', padding: '10px 0 6px' }}>BLOCKER {i + 1}</Eyebrow>
                      )}
                      <Eyebrow>REASON</Eyebrow>
                      {mdBody(b.reason)}
                      <Eyebrow>HOW TO FIX</Eyebrow>
                      {mdBody(b.fix)}
                      {(b.details ?? '').trim() && (
                        <>
                          <Eyebrow>DETAILS</Eyebrow>
                          {mdBody(b.details!)}
                        </>
                      )}
                    </div>
                  ))}
                </div>
                {(e.resolved ?? []).length > 0 && (
                  <div style={{ margin: '12px 0 0', font: "400 11.5px/1.6 var(--sans)", color: 'var(--text-faint)' }}>
                    <Eyebrow>PREVIOUSLY RESOLVED</Eyebrow>
                    {(e.resolved ?? []).map((s, i) => <div key={i}>– {s}</div>)}
                  </div>
                )}
                <ActionRow style={{ marginTop: 12 }}>
                  <BtnGhost onClick={() => patchEntry(e.id, { dismissed: true })}>Dismiss</BtnGhost>
                  {!clarify && ordinary.length > 0 && (
                    <BtnPrimary
                      disabled={anyJobBusy || viewingOld}
                      onClick={() => applyBlockersEntry(e)}
                    >
                      Apply to the spec & sync
                    </BtnPrimary>
                  )}
                </ActionRow>
              </div>
            )
          }
          if (e.kind === 'error') {
            // §11 message block — red glyph + title, the failure message as
            // body prose
            return (
              <div key={e.id} style={{ marginTop: mt }}>
                <MsgHeader icon="fa-circle-xmark" color="var(--red)" title="Something went wrong" />
                <div style={{ font: "400 12.5px/1.6 var(--sans)", color: 'var(--text-2)', overflowWrap: 'break-word' }}>{e.text}</div>
              </div>
            )
          }
          // §11 system chip — an operation block: per-op glyph (stamped at
          // creation, `fa-circle-info` fallback) beside the chip's text as its
          // title, in the secondary role so receipts read quieter than the
          // stage titles that anchor the feed; the undo row renders beneath it
          // when it anchors the snapshot.
          // A §4.4 boundary marker is the one chip that keeps the operation-
          // title role (a milestone, not a receipt — its explainer bullet must
          // stay subordinate), the one with a description bullet (the derived
          // history explainer, never persisted) and the one with chrome: 12px
          // top gap (overriding the family gap) and, only when an entry
          // follows it, a hairline divider rule beneath its group — the
          // marker closes the history it describes, the rule sits between that
          // settled conversation and the next one (no rule while the marker is
          // the thread's last entry — nothing to fence off yet).
          return (
            <React.Fragment key={e.id}>
            <div style={{ marginTop: e.boundary ? 12 : mt, display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <span style={{ width: 13, height: 13, flex: 'none', marginTop: e.boundary ? 3 : 2, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <i className={`fa-solid ${e.icon ?? 'fa-circle-info'}`} style={{ fontSize: 10, color: 'var(--text-faint)' }} />
              </span>
              <div style={{ flex: 1, minWidth: 0, font: e.boundary ? "500 12.5px/1.5 var(--sans)" : "400 11.5px/1.5 var(--sans)", color: e.boundary ? 'var(--text-muted)' : 'var(--text-faint)', overflowWrap: 'break-word' }}>
                {e.text}
              </div>
            </div>
            {e.boundary && (
              <OpBullet
                color="var(--text-faint)"
                text="The messages above are from that draft — your AI no longer reads them."
              />
            )}
            {e.boundary && i < rev.chat.length - 1 && (
              <div data-testid="chat-boundary-divider"
                style={{ height: 1, flex: 'none', background: 'var(--hairline)', marginTop: 12 }} />
            )}
            {undoRow}
            </React.Fragment>
          )
        })}
        {/* §11 derived out-of-sync line — closes the newest turn's workflow
            chip group while an agent rewrite left the workflow out of sync:
            amber chip styling, never persisted, gone the moment a sync lands */}
        {!!lastRewriteId && outOfSync && rev.dirty && !anyJobBusy && !rev.pendingSync && !viewingOld && rev.chat.length > 0 && (
          <div data-testid="chat-outofsync-note" style={{ marginTop: familyGap(rev.chat[rev.chat.length - 1], { kind: 'system' } as ChatEntry), display: 'flex', alignItems: 'center', gap: 10 }}>
            <GlyphBox><i className="fa-solid fa-triangle-exclamation" style={{ fontSize: 10, color: 'var(--amber)' }} /></GlyphBox>
            {/* the chip role (§11 secondary, 11.5/400) — amber for the color only */}
            <div style={{ flex: 1, minWidth: 0, font: "400 11.5px/1.5 var(--sans)", color: 'var(--amber)' }}>
              The workflow is out of sync — sync the steps before saving.
            </div>
          </div>
        )}
        {/* §11 turn action row — pills beneath the thread's last agent-side
            entry: Undo first (the escape hatch), then the suggested next steps */}
        {showTurnRow && (
          <div data-testid="chat-turn-actions">
            <ActionRow style={{ marginTop: 10 }}>
              {undoAtEnd && (
                <button className="ad-btn-pill action" onClick={undoDraft}>
                  <i className="fa-solid fa-rotate-left" style={pillGlyph} />
                  Undo this change
                </button>
              )}
              {showSyncPill && (
                <button className="ad-btn-pill action" data-testid="chat-sync-now" onClick={runSync}>
                  <i className="fa-solid fa-rotate" style={pillGlyph} />
                  Sync now
                </button>
              )}
              {showTestPill && (
                <button className="ad-btn-pill action" data-testid="chat-test-draft" onClick={runDraftTest}>
                  <i className="fa-solid fa-vial" style={pillGlyph} />
                  Test draft
                </button>
              )}
              {analyzeFailure && (
                <button className="ad-btn-pill action" data-testid="chat-analyze-failure" onClick={analyzeFailure}>
                  <i className="fa-solid fa-magnifying-glass" style={pillGlyph} />
                  Analyze failure
                </button>
              )}
            </ActionRow>
          </div>
        )}
        {/* §11 thread progress entry — the page's only live job surface:
            transient (derived from the job, never persisted), rendered as a
            left-aligned agent block at the bottom of the thread */}
        {anyJobBusy && (
          // §11 thread spacing: an operation block — flush beneath a
          // just-settled op entry (the same job's trail chains), the uniform
          // 12px group gap otherwise
          <div data-testid="chat-progress" style={{ marginTop: rev.chat.length === 0 ? 0 : familyGap(rev.chat[rev.chat.length - 1], { kind: 'activity' } as ChatEntry) }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 3 }}>
              <Spinner size={13} style={{ flex: 'none' }} />
              <div style={{ flex: 1, minWidth: 0, font: "500 12.5px var(--sans)", color: 'var(--text-muted)' }}>
                {jobStageTitle(rev)}
              </div>
            </div>
            {(() => {
              // §11 activity feed: the full dim event history over the live
              // detail line (the backend caps events per job), as flush-left
              // operation-block bullets; the newest event hides when detail
              // extends it (same message, growing line count) so it never
              // shows twice. The backend's `Thinking…` detail never renders —
              // the canned waiting line below subsumes it, so the waiting
              // line is never relabeled mid-tick.
              const evs = rev.genEvents
              const detail = rev.genDetail === 'Thinking…' ? null : rev.genDetail
              const last = evs.length ? evs[evs.length - 1] : null
              // detail extends the last event (same message, growing count):
              // the detail bullet replaces it and inherits its ticking stamp.
              // A detail that is a DIFFERENT activity (a tool event landed
              // after the document stream's throttled line) renders unstamped
              // instead — the last event keeps the tick, so the block shows
              // exactly one ticking stamp (§11).
              const extendsLast = !!(detail && last && detail.startsWith(last.text))
              const hist = extendsLast ? evs.slice(0, -1) : evs
              // §11 live durations: a line with a successor carries its settled
              // span; the newest line ticks its own elapsed instead (whole seconds)
              const bulletDuration = (i: number): string | undefined => {
                const t = hist[i].time
                if (t == null) return undefined
                const next = i + 1 < evs.length ? evs[i + 1].time : null
                if (next != null) return durationLabel(Math.max(0, (next - t) * 1000))
                return waitedLabel(Math.max(0, (nowSeconds - t) * 1000))
              }
              const liveSince = last?.time ?? rev.genStageStartedAt
              // §11 waiting line, one identity: the stage's canned description
              // bullet ticks from the stage's start until the first milestone,
              // then freezes in place as the feed's first bullet when the gap
              // was material (≥ 1 s) — a sub-second gap drops it, matching the
              // settled shape. A live block never renders as a bare title.
              const start = rev.genStageStartedAt
              const firstTime = evs[0]?.time
              const gapMs = start != null && firstTime != null
                ? Math.max(0, Math.round((firstTime - start) * 1000)) : null
              const waiting = evs.length === 0 && !detail
              const showLead = waiting || (gapMs != null && gapMs >= 1000)
              return (
                <>
                  {showLead && (
                    <OpBullet text={stageDoingBullet(jobStageTitle(rev))} ellipsis
                      color={waiting ? 'var(--text-muted)' : undefined}
                      duration={waiting
                        ? (start != null ? waitedLabel(Math.max(0, (nowSeconds - start) * 1000)) : undefined)
                        : durationLabel(gapMs!)} />
                  )}
                  {hist.map((e, i) => (
                    <OpBullet key={`${i}-${e.text}`} text={e.text} ellipsis duration={bulletDuration(i)} />
                  ))}
                  {detail && (
                    <OpBullet text={detail} color="var(--text-muted)"
                      duration={extendsLast && liveSince != null ? waitedLabel(Math.max(0, (nowSeconds - liveSince) * 1000)) : undefined} />
                  )}
                </>
              )
            })()}
          </div>
        )}
      </ScrollArea>
      {/* footer composer — while a §8 job runs it keeps its shape (the live
          surface is the thread progress entry above, §11); Send becomes Cancel */}
      <div style={{ flex: 'none', borderTop: '1px solid var(--hairline)', padding: '12px 14px' }}>
          <textarea
            className="ad-input oneline-ph"
            value={chatText} rows={1} disabled={inputDisabled}
            ref={attachChatInput}
            onChange={(e) => setChatText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }}
            placeholder={testLive ? 'Wait for the test to finish.'
              : viewingOld ? 'Back to the draft to edit or ask.'
                // §11: a pending "Question for you" wins over the whole
                // describe/change rule — a first-turn question (no spec
                // written yet) still reads "Answer here…"
                : awaitingAnswer ? 'Answer here…'
                  : isCreateEmpty ? 'Describe the job — one sentence is enough.'
                    : 'Change something, or ask a question…'}
            style={{ width: '100%', resize: 'none', overflow: 'hidden', display: 'block' }}
          />
          {/* composer toolbar (§11) — the drafting-agent picker is a property of the
              message being sent, so it is chosen here; Send is a quiet pill-height
              secondary affordance (Enter is the primary send path) */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 8 }}>
            {/* §11: left side = conversation meta (picker, clear); right = send/cancel alone */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 1 auto', minWidth: 0 }}>
              <AgentPick
                agents={agents} selected={selAgent} disabled={busyRewrite}
                onPick={(g) => {
                  if (busyRewrite) { showToast('Wait for the current rewrite to finish first.'); return }
                  if (selAgent && selAgent.id === g.id) return
                  setAgentId(g.id)
                  if (isEdit) up({ touched: true })
                  showToast(`${agName(g)} · ${dispModel(g)} now writes the spec and steps here.`, 3000)
                }}
              />
              {/* §11 Clear chat — icon-only dim button with a confirm step */}
              <button
                className="ad-btn-icon" data-testid="chat-clear"
                title="Clear chat" aria-label="Clear chat"
                disabled={anyJobBusy || testLive || viewingOld || rev.chat.length === 0}
                onClick={() => setConfirmClear(true)}
              >
                <i className="fa-solid fa-eraser" />
              </button>
            </div>
            {anyJobBusy ? (
              <button className="ad-btn-pill action" onClick={cancelJob} style={{ flex: 'none' }}>
                Cancel
              </button>
            ) : (
              <button className="ad-btn-pill action" disabled={inputDisabled || !chatText.trim()} onClick={sendMessage} style={{ flex: 'none' }}>
                Send
              </button>
            )}
          </div>
        </div>
      {confirmClear && (
        <ConfirmModal
          title="Clear this conversation?"
          body="The chat thread will be deleted. The draft data will be untouched."
          confirmLabel="Clear chat" danger
          onConfirm={() => { setConfirmClear(false); clearChat() }}
          onCancel={() => setConfirmClear(false)}
        />
      )}
    </div>
  )
}
