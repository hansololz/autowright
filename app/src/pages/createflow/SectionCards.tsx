// §11 Review grid cards. LeftColumn: spec, notes, agents, secrets, build
// instructions, framework — all through the one shared SectionCard template
// (header row, status-aware collapsed line, body top-hairline). RightCards:
// the display-only steps / triggers / parameters / packages cards under the
// BUILD and TEST cards. The page shell wires state and derived gating in.
import React, { useState } from 'react'
import { usePlatformCopy } from '../../platformCopy'
import { SecretModal } from '../../SecretModal'
import { useTriggerPreview } from '../../triggers'
import { StepList, type StepHistory } from '../../steps'
import type { Agent, SecretMeta, UnresolvedRefs } from '../../types'
import { Caret, CheckBox, Collapse, EmptyLine, Eyebrow, MetaChip, MiniBadge, Notice, ScrollArea, agName, dispModel, paramSummary } from '../../ui'
import { Markdown, SpecMarkdown } from '../../result'
import { type AgentRef, type Rev, type SecretRef, applyTestValues, shortId, specToText, stepList, textToSpec } from './model'
import { DocEditorModal } from './DocEditorModal'

// §11: one text style for a card's collapsed hint — the description never
// changes size between collapsed and open
export const cardHintFont = "400 11.5px/1.5 var(--sans)"

// §11 BUILD INSTRUCTIONS card: the collapsed explainer for a static built-in
// document, like the framework card.
const BUILD_INSTRUCTIONS_EXPLAINER = 'Default rules your AI follows when it builds this automation. Your spec can override all of them.'
const FRAMEWORK_INSTRUCTIONS_HINT = 'The source of truth for what your AI knows about the app: the framework, tools, and how everything works.'
// §11 NOTES card: always the explainer (collapsed line and open footer), never a
// first-line preview — an agent-written notes.md opens with a title heading.
const NOTES_EXPLAINER = 'Your AI records what it learns (page quirks, dead ends, fixes) as you build and test.'
const SPEC_EXPLAINER = 'What the automation should do, in plain words. The AI regenerates the steps from this document when it changes.'
const AGENTS_EXPLAINER = 'Which agents steps may call mid-execution for the parts plain code can’t do, like reading a messy page or writing prose.'

// §11: the explainer repeated under an open card body — beneath a dim hairline
// so a clipped document line never runs into it; `bare` when the block above
// already ends in a row hairline or belongs to the same block (the checklists)
function CardFooter({ bare, children }: { bare?: boolean; children: React.ReactNode }) {
  return (
    <div style={{ padding: '12px 18px', borderTop: bare ? undefined : '1px solid var(--hairline-dim)', font: cardHintFont, color: 'var(--text-muted)' }}>
      {children}
    </div>
  )
}

// §11: the one template every collapsible editor card renders through —
// whole-row header toggle with hover tint, caret + eyebrow, optional
// right-edge content (actions or counts; clicks there must stopPropagation),
// the collapsed line, and the body's top hairline. `inert` freezes the
// header while the card is held open by an edit in progress. The collapsed
// line is always the `hint` explainer (§11) — never a content preview.
function SectionCard({ eyebrow, open, onToggle, inert, right, hint, children }: {
  eyebrow: string
  open: boolean
  onToggle: (open: boolean) => void
  inert?: boolean
  right?: React.ReactNode
  hint: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="ad-card" style={{ overflow: 'hidden' }}>
      <div
        // The header nests action buttons in `right`, so it stays a div with
        // button semantics (§9: never nest buttons) — Enter/Space toggle it.
        className={inert ? 'ad-focus-inset' : 'ad-hover-row ad-focus-inset'}
        role="button"
        tabIndex={inert ? -1 : 0}
        onClick={() => { if (!inert) onToggle(!open) }}
        onKeyDown={(e) => {
          if (inert || e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(!open) }
        }}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '12px 18px', cursor: inert ? 'default' : 'pointer', userSelect: 'none' }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
          <Caret open={open} style={{ width: 14, flex: 'none', textAlign: 'center', color: 'var(--text-deco)' }} />
          <Eyebrow>{eyebrow}</Eyebrow>
        </span>
        {right}
      </div>
      <Collapse open={!open}>
        <button
          className="ad-btn-bare ad-focus-inset"
          onClick={() => onToggle(true)}
          style={{ padding: '0 18px 13px', font: cardHintFont, color: 'var(--text-muted)', cursor: 'pointer', userSelect: 'none' }}
        >
          {hint}
        </button>
      </Collapse>
      <Collapse open={open}>
        <div style={{ borderTop: '1px solid var(--hairline)' }}>{children}</div>
      </Collapse>
    </div>
  )
}

// in-card empty state, hint-styled and hint-indented (§11: same left edge as
// the collapsed line, so an empty card's text stays put when the card opens)
function CardEmpty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '10px 18px 16px', font: cardHintFont, color: 'var(--text-muted)' }}>{children}</div>
}

// §11: the one markdown body every card renders through — same padding, same
// 440px max height with inner scroll, same full-bleed table allowance
function CardMarkdown({ children }: { children: React.ReactNode }) {
  return (
    <ScrollArea style={{ padding: '14px 18px 16px', maxHeight: 440 }}>
      {children}
    </ScrollArea>
  )
}

function WarnBanner({ text }: { text: string }) {
  return (
    <Notice tone="amber" className="ad-anim-item" style={{ margin: '12px 18px' }}>{text}</Notice>
  )
}

// ---------- left column ----------

export interface LeftColumnProps {
  rev: Rev
  up: (patch: Partial<Rev>) => void
  fw: string
  bld: string
  isEdit: boolean
  isCreateEmpty: boolean
  busyRewrite: boolean
  viewingOld: boolean
  testLive: boolean
  lockStyle?: React.CSSProperties
  agents: Agent[]
  secrets: SecretMeta[]
  unresolvedReferences?: UnresolvedRefs
  availAgents: Agent[]
  agentStepIdx: number[]
  agWarn: boolean
  agNone: boolean
  agNotEnabled: AgentRef[]
  agMissing: AgentRef[]
  agFallbackIdx: number[]
  secWarn: boolean
  secNotAllowed: SecretRef[]
  secMissing: SecretRef[]
  secRefs: SecretRef[]
  specOpenEff: boolean
  agSecOpenEff: boolean
  secSecOpenEff: boolean
  instrOpenEff: boolean
  notesOpenEff: boolean
  showToast: (msg: string, ms?: number) => void
}

export function LeftColumn({
  rev, up, fw, bld, isEdit, isCreateEmpty, busyRewrite, viewingOld, testLive, lockStyle,
  agents, secrets, unresolvedReferences, availAgents, agentStepIdx,
  agWarn, agNone, agNotEnabled, agMissing, agFallbackIdx,
  secWarn, secNotAllowed, secMissing, secRefs,
  specOpenEff, agSecOpenEff, secSecOpenEff, instrOpenEff, notesOpenEff,
  showToast,
}: LeftColumnProps) {
  // §9 per-OS copy rule: the secret-store name the SECRETS card names.
  const copy = usePlatformCopy()
  // §11 Secrets card New secret modal — a secret saved here is auto-allowed.
  const [secretModal, setSecretModal] = useState(false)
  // §11 document-editor modal: the two manual edits are mutually exclusive,
  // so at most one of these is open; each Save applies exactly what the old
  // in-card Save did, and the modal fires it after its exit animation.
  const docEdit = rev.specEdit ? 'spec' : rev.notesEdit ? 'notes' : null
  // §11 SECRETS card explainer — collapsed line and open footer, per-OS store name
  const secretsExplainer = `Only selected secrets are available to this automation at execution time. Values come from your ${copy.secretStore} and never appear in scripts or logs.`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* SPEC */}
      <SectionCard
        eyebrow="SPEC"
        open={specOpenEff}
        onToggle={(o) => up({ specSecOpen: o })}
        hint={SPEC_EXPLAINER}
        right={specOpenEff && (
          <button
            // §11: an old version is browsed read-only — editing
            // here would mark the draft dirty and lock Restore
            // behind a disabled sync button.
            className="ad-btn-text small ad-focus-inset" data-testid="spec-edit"
            disabled={busyRewrite || viewingOld || testLive}
            onClick={(e) => {
              e.stopPropagation()
              if (busyRewrite || viewingOld || testLive) return
              const t = specToText(rev.spec)
              up({
                notesDraft: null, notesEdit: false,
                specText: t, specTextOrig: t, specEdit: true,
              })
            }}
            style={{ flex: 'none' }}
          >
            Edit
          </button>
        )}
      >
        {isCreateEmpty ? (
            <CardEmpty>The spec appears here as your AI writes it — describe the job in the chat to start.</CardEmpty>
          ) : (
            <CardMarkdown>
              <SpecMarkdown blocks={rev.spec} />
            </CardMarkdown>
          )}
        <CardFooter>{SPEC_EXPLAINER}</CardFooter>
      </SectionCard>

      {/* NOTES — §4.1 agent-owned working knowledge (§11): agent-written
          via §8 chat/sync notes.md rewrites, user-prunable here. Never
          marks the workflow out of sync and never gates Save. */}
      <SectionCard
        eyebrow="NOTES"
        open={notesOpenEff}
        onToggle={(o) => up({ notesSecOpen: o })}
        hint={NOTES_EXPLAINER}
        right={notesOpenEff && (
          <button
            // §11: an old version is browsed read-only — notes saved onto a
            // vX view would vanish on Restore or a version switch.
            className="ad-btn-text small ad-focus-inset" disabled={busyRewrite || viewingOld}
            onClick={(e) => {
              e.stopPropagation()
              if (busyRewrite || viewingOld) return
              up({
                specEdit: false, specText: '', specTextOrig: '',
                notesDraft: rev.notes, notesEdit: true,
              })
            }}
            style={{ flex: 'none' }}
          >
            Edit
          </button>
        )}
      >
        {rev.notes.trim() ? (
          <CardMarkdown>
            <Markdown text={rev.notes} />
          </CardMarkdown>
        ) : (
          <CardEmpty>No notes yet.</CardEmpty>
        )}
        <CardFooter>{NOTES_EXPLAINER}</CardFooter>
      </SectionCard>

      {/* AGENTS · AVAILABLE TO STEPS */}
      <SectionCard
        eyebrow="AGENTS · AVAILABLE TO STEPS"
        open={agSecOpenEff}
        onToggle={(o) => up({ agSecOpen: o })}
        hint={AGENTS_EXPLAINER}
        right={
          <span style={{ font: "500 10.5px var(--mono)", color: 'var(--text-muted)', whiteSpace: 'nowrap', flex: 'none' }}>
            {availAgents.length} of {agents.length} enabled
          </span>
        }
      >
          <div>
            {agWarn && (
              <WarnBanner text={[
                ...(agNone ? [`Step${agFallbackIdx.length > 1 ? 's' : ''} ${stepList(agFallbackIdx)} need${agFallbackIdx.length > 1 ? '' : 's'} an agent, but none is enabled — the execution would fail there. Enable one below.`] : []),
                ...agNotEnabled.map((r) => `Step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} call${r.steps.length > 1 ? '' : 's'} ${r.name}, but it isn’t enabled here — the execution would fail there. Enable it below.`),
                ...agMissing.map((r) => (r.imported
                  ? `Step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} call${r.steps.length > 1 ? '' : 's'} ${r.name} from the imported file, which has no match on this ${copy.machine} - pick an agent or ask your AI to fix it.`
                  : `${r.name} isn’t one of your agents — the execution would fail at step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)}.`)),
              ].join(' ')} />
            )}
            {agents.map((g) => {
              const on = rev.enabledAgents.includes(g.id)
              // §11: named references count even while the agent is disabled, so the
              // warned row still shows where it's called; unnamed steps fall back to
              // the first enabled agent.
              const used = agentStepIdx.filter((i) => {
                const ids = (rev.steps[i].agents ?? []).map((e) => e.id)
                return ids.length ? ids.includes(g.id) : availAgents[0]?.id === g.id
              })
              return (
                <button
                  key={g.id}
                  onClick={() => {
                    if (busyRewrite) return
                    if (on) {
                      up({ enabledAgents: rev.enabledAgents.filter((z) => z !== g.id), ...(isEdit ? { touched: true } : {}) })
                      if (used.length) showToast(`Step${used.length > 1 ? 's' : ''} ${stepList(used)} ${used.length > 1 ? 'are' : 'is'} out of sync — ${agName(g)} is no longer available here. Re-enable it or sync the steps before saving.`, 5000)
                    } else {
                      up({ enabledAgents: [...rev.enabledAgents, g.id], ...(isEdit ? { touched: true } : {}) })
                      showToast(`${agName(g)} is now available to steps — Sync spec if the steps should be rewritten to use it.`, 3600)
                    }
                  }}
                  role="checkbox"
                  aria-checked={on}
                  className="ad-btn-bare ad-focus-inset ad-hover-row"
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)', cursor: 'pointer', userSelect: 'none', ...lockStyle }}
                >
                  <CheckBox on={on} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ font: "600 13px var(--sans)" }}>{agName(g)}</div>
                    <div style={{ font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)' }}>{dispModel(g)}</div>
                  </div>
                  {used.length > 0 && (
                    <span style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none', whiteSpace: 'nowrap' }}>
                      called by step{used.length > 1 ? 's' : ''} {stepList(used)}
                    </span>
                  )}
                </button>
              )
            })}
            <CardFooter bare>{AGENTS_EXPLAINER}</CardFooter>
          </div>
      </SectionCard>

      {/* SECRETS · ALLOWED FOR STEPS */}
      <SectionCard
        eyebrow="SECRETS · ALLOWED FOR STEPS"
        open={secSecOpenEff}
        onToggle={(o) => up({ secSecOpen: o })}
        hint={secretsExplainer}
        right={
          <span style={{ font: "500 10.5px var(--mono)", color: 'var(--text-muted)', whiteSpace: 'nowrap', flex: 'none' }}>
            {rev.allowedSecrets.length} of {secrets.length} allowed
          </span>
        }
      >
          <div>
            {secWarn && (
              <WarnBanner text={[
                ...secNotAllowed.map((r) => {
                  const nm = secrets.find((z) => z.id === r.id)?.name ?? shortId(r.id)
                  return `Step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} use${r.steps.length > 1 ? '' : 's'} ${nm}, but it isn’t allowed here — the execution would fail there. Allow it below.`
                }),
                ...secMissing.map((r) => (r.importedName
                  ? `${r.importedName} came from the imported file and has no match on this ${copy.machine} - pick one of your secrets or ask your AI to fix it.`
                  : `Step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} use${r.steps.length > 1 ? '' : 's'} a secret that no longer exists (${shortId(r.id)}) — the execution would fail there. Sync the steps to rewrite them.`)),
              ].join(' ')} />
            )}
            {secrets.map((s) => {
              const ref = secRefs.find((r) => r.id === s.id)
              const on = rev.allowedSecrets.includes(s.id)
              return (
                <button
                  key={s.id}
                  onClick={() => {
                    if (busyRewrite) return
                    if (on) {
                      up({ allowedSecrets: rev.allowedSecrets.filter((z) => z !== s.id), ...(isEdit ? { touched: true } : {}) })
                      if (ref) showToast(`Step${ref.steps.length > 1 ? 's' : ''} ${stepList(ref.steps)} use${ref.steps.length > 1 ? '' : 's'} ${s.name} — re-allow it or sync the steps before saving.`, 4500)
                    } else {
                      up({ allowedSecrets: [...rev.allowedSecrets, s.id], ...(isEdit ? { touched: true } : {}) })
                    }
                  }}
                  role="checkbox"
                  aria-checked={on}
                  className="ad-btn-bare ad-focus-inset ad-hover-row"
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)', cursor: 'pointer', userSelect: 'none', ...lockStyle }}
                >
                  <CheckBox on={on} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ font: "500 12.5px var(--mono)", color: 'var(--text)' }}>{s.name}</div>
                  </div>
                  {ref && (
                    <span style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none', whiteSpace: 'nowrap' }}>
                      used by step{ref.steps.length > 1 ? 's' : ''} {stepList(ref.steps)}
                    </span>
                  )}
                </button>
              )
            })}
            {/* §11: a dangling secret id can't be fixed by re-creating the name —
                a new secret mints a NEW id (§4.8). The fix is a sync. */}
            {secMissing.map((r) => (
              <div key={r.id} style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px',
                borderBottom: '1px solid var(--hairline-dim)', ...lockStyle,
              }}>
                <i className="fa-solid fa-triangle-exclamation" style={{ fontSize: 11, color: 'var(--red-text)' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ font: "500 12.5px var(--mono)", color: 'var(--red-text)' }}>{r.importedName ?? shortId(r.id)}</div>
                  <div style={{ font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)' }}>
                    {r.importedName
                      ? `used by step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} - no match on this ${copy.machine}; pick a secret or ask your AI to fix it`
                      : `used by step${r.steps.length > 1 ? 's' : ''} ${stepList(r.steps)} — this secret no longer exists; sync the steps`}
                  </div>
                </div>
              </div>
            ))}
            {secrets.length === 0 && secRefs.length === 0 && (
              <div style={{ padding: '14px 18px', font: cardHintFont, color: 'var(--text-muted)', borderBottom: '1px solid var(--hairline-dim)' }}>
                No secrets in your {copy.secretStore} yet — press New secret.
              </div>
            )}
            {/* §11: a secret added from this card is an explicit grant — auto-allowed on save */}
            <div style={{ padding: '12px 18px 0', ...lockStyle }}>
              <button className="ad-btn-accent-ghost small" onClick={() => setSecretModal(true)}>
                <i className="fa-solid fa-plus" style={{ fontSize: 9, marginRight: 5 }} />
                New secret
              </button>
            </div>
            <CardFooter bare>{secretsExplainer}</CardFooter>
          </div>
      </SectionCard>
      {secretModal && (
        <SecretModal
          modal={{ mode: 'add' }}
          onClose={() => setSecretModal(false)}
          onSaved={(saved) => {
            // §11: a secret saved from this card is an explicit grant — the
            // §19 PUT entity response carries the minted id.
            up({ allowedSecrets: [...rev.allowedSecrets, saved.id], ...(isEdit ? { touched: true } : {}) })
          }}
        />
      )}

      {/* BUILD INSTRUCTIONS — §11: a read-only built-in document, exactly like
          the framework card below. The rules ship with the app, so nothing here
          differs between automations or between create and edit mode. */}
      <SectionCard
        eyebrow="BUILD INSTRUCTIONS"
        open={instrOpenEff}
        onToggle={(o) => up({ instrSecOpen: o })}
        hint={BUILD_INSTRUCTIONS_EXPLAINER}
      >
          <CardMarkdown>
            {bld
              ? <Markdown text={bld} />
              : <div style={{ font: cardHintFont, color: 'var(--red-text)' }}>Couldn’t load build-instructions.md — reopen this page to retry.</div>}
          </CardMarkdown>
          <CardFooter>{BUILD_INSTRUCTIONS_EXPLAINER}</CardFooter>
      </SectionCard>

      {/* §11 document-editor modal — one document at a time */}
      {docEdit === 'spec' && (
        <DocEditorModal
          kind="spec" text={rev.specText} original={rev.specTextOrig}
          // §11: typing is editor state only — Save marks the draft touched
          onChange={(t) => up({ specText: t })}
          onDiscard={() => up({ specEdit: false, specText: '', specTextOrig: '' })}
          onSave={() => {
            // §11 draft undo: a manual Save under the snapshot clears it
            up({
              undo: null,
              spec: textToSpec(rev.specText), specEdit: false, specText: '', specTextOrig: '',
              dirty: true, touched: true,
            })
            showToast('Spec saved — the workflow is out of sync. Sync the steps before saving.', 5800)
          }}
        />
      )}
      {docEdit === 'notes' && (
        <DocEditorModal
          kind="notes" text={rev.notesDraft ?? rev.notes} original={rev.notes}
          onChange={(t) => up({ notesDraft: t })}
          onDiscard={() => up({ notesDraft: null, notesEdit: false })}
          // §4.1: a notes change never marks the workflow out of sync;
          // §11 draft undo: a manual Save under the snapshot clears it
          onSave={() => up({ notes: rev.notesDraft ?? rev.notes, notesDraft: null, notesEdit: false, touched: true, undo: null })}
        />
      )}

      {/* FRAMEWORK INSTRUCTIONS */}
      <SectionCard
        eyebrow="FRAMEWORK INSTRUCTIONS"
        open={rev.fwOpen}
        onToggle={(o) => up({ fwOpen: o })}
        hint={FRAMEWORK_INSTRUCTIONS_HINT}
      >
          <CardMarkdown>
            {fw
              ? <Markdown text={fw} />
              : <div style={{ font: cardHintFont, color: 'var(--red-text)' }}>Couldn’t load framework-instructions.md — reopen this page to retry.</div>}
          </CardMarkdown>
          <CardFooter>{FRAMEWORK_INSTRUCTIONS_HINT}</CardFooter>
      </SectionCard>
    </div>
  )
}

// ---------- right column (below the BUILD and TEST cards) ----------

export interface RightCardsProps {
  stepHistory?: StepHistory[] // §9.2 change badge: the stored revisions (edit mode only)
  rev: Rev
  up: (patch: Partial<Rev>) => void
  liveParams?: import('../../types').ParamDef[] // edit mode: the automation's stored values (§16 summary source)
  // edit mode: the automation's stored §4.1 concurrency pair — create mode
  // shows the 1/0 defaults; a chat-staged value (§8) overrides its row
  liveConcurrency?: { maxParallel: number; maxQueued: number }
  drafting: boolean
  isCreateEmpty: boolean
  outOfSync: boolean
  busyRewrite: boolean
  availAgents: Agent[]
  agents: Agent[]           // every configured agent — the step tags resolve entry ids
  secrets: SecretMeta[]     // every stored secret — the step tags resolve entry ids
  unresolvedReferences?: UnresolvedRefs // §5.1 imported no-match names for the red tags (edit mode)
  pkgSecOpenEff: boolean
  updatePkgs: (pips: string[]) => void
  installPkgs: () => void
}

export function RightCards({
  rev, up, liveParams, liveConcurrency, drafting, isCreateEmpty, outOfSync, busyRewrite,
  availAgents, agents, secrets, unresolvedReferences, pkgSecOpenEff, updatePkgs, installPkgs,
  stepHistory,
}: RightCardsProps) {
  // §19: the §11 draft-trigger chips label through POST /triggers/preview —
  // the renderer keeps no local trigger-math mirror (§4.3)
  const trigPreviews = useTriggerPreview(rev.triggers)
  // §9 per-OS copy rule: the §13 surface's name in the no-triggers line.
  const copy = usePlatformCopy()
  return (
    <>
      {/* STEPS */}
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
          <Eyebrow>STEPS · GENERATED</Eyebrow>
        </div>
        {/* §11 first build on Review: static placeholder — drafting progress
            lives in the thread; also shown on the create empty state */}
        {(drafting || isCreateEmpty) && (
          <EmptyLine>Steps appear here once the build finishes.</EmptyLine>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', opacity: outOfSync || busyRewrite ? 0.45 : 1, transition: 'opacity var(--t-hover) var(--ease-enter)' }}>
          <StepList variant="editor" steps={rev.steps} availAgents={availAgents} allAgents={agents} secrets={secrets} unresolvedReferences={unresolvedReferences} packages={rev.packages} history={stepHistory} viewing={rev.viewing} params={rev.params} />
        </div>
      </div>

      {/* TRIGGERS — display-only (§11): what saving stores — drafted crons
          merged over the saved list (§4.3); one-shots/on-off edited on the
          automation page */}
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
          <Eyebrow>TRIGGERS</Eyebrow>
        </div>
        {drafting || isCreateEmpty ? (
          <EmptyLine>Triggers appear here once the build finishes.</EmptyLine>
        ) : (
          <div style={{ padding: '13px 18px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            {rev.triggers.map((t, i) => trigPreviews[i] && (
              <MetaChip
                key={i}
                c={t.enabled ? 'var(--accent)' : undefined}
                bg={t.enabled ? 'var(--accent-chip-bg)' : undefined}
              >
                {trigPreviews[i].label}
              </MetaChip>
            ))}
            <span style={{ font: "400 11.5px var(--sans)", color: 'var(--text-faint)' }}>
              {rev.triggers.length > 0
                ? 'Executes even when the app is closed. Ask the AI in chat to change these, or use the automation page — chat changes apply when you save.'
                : `No triggers — executes only via ${copy.manualOnlyShort}.`}
            </span>
          </div>
        )}
      </div>

      {/* PARAMETERS — display-only (§16): value input lives on the automation page,
          test-only values in the Test card */}
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
          <Eyebrow>PARAMETERS</Eyebrow>
        </div>
        {drafting || isCreateEmpty ? (
          <EmptyLine>Parameters appear here once the build finishes.</EmptyLine>
        ) : rev.params.length === 0 ? (
          <EmptyLine>No settings needed — your AI didn’t ask for any.</EmptyLine>
        ) : (
          <>
            {rev.params.map((p) => {
              // §16: value summary — edit mode shows the live value (§5 name+kind
              // match), create mode the drafted default; a chat-staged value
              // (§8 param_values) overrides either, marked so an unsaved value
              // is never mistaken for a stored one
              const live = liveParams?.find((q) => q.name === p.name && q.kind === p.kind)
              const staged = p.name in rev.paramValues
                ? applyTestValues([live ?? p], { [p.name]: rev.paramValues[p.name] })[0]
                : null
              return (
                <div key={p.name} style={{ padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                    <div style={{ font: "600 13px var(--sans)" }}>{p.label}</div>
                    <div style={{ font: "500 12.5px var(--mono)", color: 'var(--text-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '55%' }}>
                      {staged && (
                        <MiniBadge c="var(--accent)" bg="var(--accent-chip-bg)" style={{ marginRight: 8 }}>STAGED</MiniBadge>
                      )}
                      {paramSummary(staged ?? live ?? p)}
                    </div>
                  </div>
                  <div style={{ font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)', marginTop: 2 }}>{p.help}</div>
                </div>
              )
            })}
            <div style={{ padding: '12px 18px', font: cardHintFont, color: 'var(--text-muted)' }}>
              Values aren’t part of a version — set them on the automation page, or ask your AI here (staged values apply when you save). For a test, set test-only values in the test-run modal — or ask your AI, which can also change the parameter definitions and set test values when it runs a test.
            </div>
          </>
        )}
      </div>

      {/* CONCURRENCY — display-only (§11): the §4.1 settings; number inputs
          live on the automation page, chat stages changes (§8 `concurrency`).
          Always the two rows — no empty state, no collapse. */}
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '12px 18px', borderBottom: '1px solid var(--hairline)' }}>
          <Eyebrow>CONCURRENCY</Eyebrow>
        </div>
        {([
          { label: 'Max parallel executions', key: 'maxParallel' as const, fallback: 1,
            help: 'How many executions of this automation may run at the same time.' },
          { label: 'Max queued executions', key: 'maxQueued' as const, fallback: 0,
            help: 'How many executions wait for a free slot. Incoming messages beyond this are answered with a busy notice instead.' },
        ]).map(({ label, key, fallback, help }) => {
          const staged = rev.concurrency?.[key]
          return (
            <div key={key} style={{ padding: '12px 18px', borderBottom: key === 'maxQueued' ? 'none' : '1px solid var(--hairline-dim)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                <div style={{ font: "600 13px var(--sans)" }}>{label}</div>
                <div style={{ font: "500 12.5px var(--mono)", color: 'var(--text-2)', whiteSpace: 'nowrap' }}>
                  {staged != null && (
                    <MiniBadge c="var(--accent)" bg="var(--accent-chip-bg)" style={{ marginRight: 8 }}>STAGED</MiniBadge>
                  )}
                  {staged ?? liveConcurrency?.[key] ?? fallback}
                </div>
              </div>
              <div style={{ font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)', marginTop: 2 }}>{help}</div>
            </div>
          )
        })}
      </div>

      {/* PACKAGES · PYTHON LIBRARIES (§6.2) — display-only, right column like
          Triggers/Parameters: the drafting pipeline owns the list */}
      <div className="ad-card" style={{ overflow: 'hidden' }}>
        <button
          className={`ad-btn-bare ad-focus-inset${rev.packages.length > 0 ? ' ad-hover-row' : ''}`}
          disabled={rev.packages.length === 0}
          onClick={() => rev.packages.length > 0 && up({ pkgSecOpen: !pkgSecOpenEff })}
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '12px 18px', cursor: rev.packages.length > 0 ? 'pointer' : 'default', userSelect: 'none' }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            {rev.packages.length > 0 && (
              <Caret open={pkgSecOpenEff} style={{ width: 14, flex: 'none', textAlign: 'center', color: 'var(--text-deco)' }} />
            )}
            <Eyebrow>PACKAGES · PYTHON LIBRARIES</Eyebrow>
          </span>
          {rev.packages.length > 0 && (
            <span style={{ font: "500 10.5px var(--mono)", color: 'var(--text-muted)', whiteSpace: 'nowrap', flex: 'none' }}>
              {rev.packages.filter((p) => p.status === 'installed').length} of {rev.packages.length} installed
              {rev.packages.filter((p) => p.latest).length > 0 &&
                ` · ${rev.packages.filter((p) => p.latest).length} update${rev.packages.filter((p) => p.latest).length === 1 ? '' : 's'}`}
            </span>
          )}
        </button>
        {drafting || isCreateEmpty ? (
          <EmptyLine style={{ borderTop: '1px solid var(--hairline)' }}>Packages appear here once the build finishes.</EmptyLine>
        ) : rev.packages.length === 0 ? (
          <EmptyLine style={{ borderTop: '1px solid var(--hairline)' }}>
            No extra packages — the steps use only the built-in libraries.
          </EmptyLine>
        ) : (<>
          <Collapse open={!pkgSecOpenEff}>
            {/* §11 status-aware collapsed line — the card only collapses when the list is non-empty */}
            <button className="ad-btn-bare ad-focus-inset" onClick={() => up({ pkgSecOpen: true })} style={{ padding: '0 18px 13px', font: cardHintFont, color: 'var(--text-muted)', cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {rev.packages.map((p) => p.pip).join(' · ')}
            </button>
          </Collapse>
          <Collapse open={pkgSecOpenEff}>
          <div style={{ borderTop: '1px solid var(--hairline)' }}>
            {rev.packages.map((p) => (
              <div key={p.pip} style={{ padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0, font: "500 12.5px var(--mono)", color: 'var(--text)' }}>
                    {p.pip}
                    {p.version && (
                      <span style={{ color: 'var(--text-faint)', marginLeft: 8 }}>{p.version}</span>
                    )}
                    {p.latest && p.status !== 'installing' && (
                      <span style={{ color: 'var(--accent)', marginLeft: 8 }}>→ {p.latest}</span>
                    )}
                  </div>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: 'none', whiteSpace: 'nowrap', font: "600 11px var(--mono)",
                    color: p.status === 'installed' ? 'var(--green)'
                      : p.status === 'failed' ? 'var(--red)'
                        : p.status === 'missing' ? 'var(--amber)' : 'var(--text-faint)' }}>
                    {p.status === 'installed' && <><i className="fa-solid fa-check" style={{ fontSize: 9 }} /> installed</>}
                    {p.status === 'installing' && 'installing…'}
                    {p.status === 'missing' && 'not installed'}
                    {p.status === 'failed' && 'failed'}
                    {!p.status && 'checking…'}
                  </span>
                  {p.latest && p.status !== 'installing' && (
                    <button className="ad-btn-text small" disabled={rev.pkgBusy || busyRewrite}
                      onClick={() => updatePkgs([p.pip])} style={{ flex: 'none' }}>
                      Update
                    </button>
                  )}
                </div>
                {/* §11: the declaration's why — the card explains every install it asks the user to trust */}
                {p.why && (
                  <div style={{ margin: '3px 0 0', font: "400 11.5px/1.45 var(--sans)", color: 'var(--text-muted)' }}>{p.why}</div>
                )}
                {p.status === 'failed' && p.error && (
                  <div style={{ margin: '6px 0 0', font: "400 10.5px/1.5 var(--mono)", color: 'var(--red-text)', overflowWrap: 'break-word' }}>{p.error}</div>
                )}
              </div>
            ))}
            {rev.packages.filter((p) => p.latest).length >= 2 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                <span style={{ flex: 1, font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)' }}>
                  Newer versions are available. Updating applies to every automation that uses the package.
                </span>
                <button className="ad-btn-text small" disabled={rev.pkgBusy || busyRewrite}
                  onClick={() => updatePkgs(rev.packages.filter((p) => p.latest).map((p) => p.pip))} style={{ flex: 'none' }}>
                  Update all
                </button>
              </div>
            )}
            {rev.packages.some((p) => p.status === 'missing' || p.status === 'failed') && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px', borderBottom: '1px solid var(--hairline-dim)' }}>
                <span style={{ flex: 1, font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)' }}>
                  {rev.packages.some((p) => p.status === 'failed')
                    ? 'A package couldn’t be installed — check your connection, then retry. Saving still works; executions retry on their own too.'
                    : 'Some packages aren’t installed yet. Executions install them automatically — or install now.'}
                </span>
                <button className="ad-btn-accent-ghost small" disabled={rev.pkgBusy || busyRewrite} onClick={installPkgs} style={{ flex: 'none' }}>
                  {rev.packages.some((p) => p.status === 'failed') ? 'Retry install' : 'Install'}
                </button>
              </div>
            )}
            <div style={{ padding: '12px 18px', font: cardHintFont, color: 'var(--text-muted)' }}>
              Your AI picked these Python packages for the steps. They install automatically — nothing for you to run.
            </div>
          </div>
          </Collapse>
        </>)}
      </div>
    </>
  )
}
