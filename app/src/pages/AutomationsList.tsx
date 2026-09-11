// Automations list (§4, prototype "Automations list" screen).
import React, { useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import type { Automation, ImportSummary } from '../types'
import { Badge, BtnGhost, BtnPrimary, ConfirmModal, EmptyState, Eyebrow, HeaderActions, MetaChip, MiniBadge, Modal, P, PageTitle, PULSE, resultChipColors, executingToast } from '../ui'
import { usePlatformCopy } from '../platformCopy'
import ImportModal, { osName } from './ImportModal'

// §5.1/§9.1 import summary modal — only the sections that apply render. The
// §22.3 Marketplace page hands off to this same modal after an install.
export function ImportSummaryModal({ name, automationId, summary, onClose }: {
  name: string
  automationId: string
  summary: ImportSummary
  onClose: () => void
}) {
  const go = useStore((s) => s.go)
  // §9 per-OS copy rule: the machine noun this modal names.
  const copy = usePlatformCopy()
  const section = (title: string, body: React.ReactNode) => (
    <div style={{ marginTop: 16 }}>
      <Eyebrow style={{ margin: '0 0 8px' }}>{title}</Eyebrow>
      {body}
    </div>
  )
  const nameRow = (n: string, extra?: React.ReactNode) => (
    <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
      <span style={{ font: `500 12px var(--mono)`, color: 'var(--text)' }}>{n}</span>
      {extra}
    </div>
  )
  return (
    <Modal onClose={onClose} width={460}>
      {(close) => (
        <>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>
            Imported “{name}”
          </h2>
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '6px 0 0' }}>
            Its triggers are off until you enable them.
          </p>
          {summary.renamedFrom && (
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '2px 0 0' }}>
              Renamed from “{summary.renamedFrom}”, which already exists on this {copy.machine}.
            </p>
          )}
          {summary.osMismatch && (
            // §5.1: the same warning persists on the automation as the §4.1
            // os-mismatch problem.
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--amber)', margin: '2px 0 0' }}>
              Built on {osName(summary.os)} — its steps may need rewriting before they run on this {copy.machine}.
            </p>
          )}
          {(summary.secretsMatched.length > 0 || summary.agentsMatched.length > 0) && section(`MATCHED ON THIS ${copy.machine.toUpperCase()}`, (
            <>
              {/* §5.1: the archive name, with "uses <local>" when the match
                  renamed; a not-ready matched agent gets the §12 badge. */}
              {summary.secretsMatched.map((m) => nameRow(m.name, m.matchedTo !== m.name ? (
                <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>uses {m.matchedTo}</span>
              ) : undefined))}
              {summary.agentsMatched.map((m) => nameRow(m.name, (
                <>
                  {m.matchedTo !== m.name && (
                    <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>uses {m.matchedTo}</span>
                  )}
                  {!m.ready && <MiniBadge c={P.amber} bg={P.amberBg}>Needs setup</MiniBadge>}
                </>
              )))}
            </>
          ))}
          {summary.unresolved.length > 0 && section('NEEDS ATTENTION', (
            <>
              {summary.unresolved.map((u) => (
                <div key={`${u.kind}:${u.name}`} style={{ padding: '3px 0' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <i
                      className={`fa-solid ${u.kind === 'secret' ? 'fa-key' : 'fa-microchip'}`}
                      style={{ fontSize: 10, color: 'var(--text-faint)' }}
                    />
                    <span style={{ font: `500 12px var(--mono)`, color: 'var(--text)' }}>{u.name}</span>
                  </div>
                  {u.description && (
                    <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: '1px 0 0 18px' }}>
                      {u.description}
                    </div>
                  )}
                </div>
              ))}
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--amber)', margin: '6px 0 0' }}>
                No match was found on this {copy.machine} - pick a replacement on the edit page.
              </p>
            </>
          ))}
          {summary.packages.length > 0 && (
            <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)', margin: '16px 0 0' }}>
              {summary.packages.length} package{summary.packages.length === 1 ? ' is' : 's are'} installing in the background.
            </p>
          )}
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
            <BtnGhost onClick={close}>Close</BtnGhost>
            <BtnPrimary onClick={() => { close(); go('automation', { automationId }) }}>
              Open automation
            </BtnPrimary>
          </div>
        </>
      )}
    </Modal>
  )
}


// §9.1/§19 drafting note — background work is never invisible: faint text
// only, never a spinner (§11 owns live progress).
const draftJobNote = (status: string) => (status === 'building'
  ? 'Your AI is drafting…'
  : 'Your AI finished — reopen the draft to review.')

function AutoCard({ a }: { a: Automation }) {
  const go = useStore((s) => s.go)
  const showToast = useStore((s) => s.showToast)
  // §9.1/§19: a building or held drafting job on this automation's draft
  // container surfaces as the faint drafting note below. Selected as the
  // status alone — a row selector returning the job object would hand back a
  // fresh reference on every /state refresh and re-render every card.
  const draftJobStatus = useStore((s) => s.draftJobs.find((j) => j.owner === a.id)?.status)
  const executing = a.live.length > 0

  const execute = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (executing) return
    void (async () => {
      try {
        await api.executeNow(a.id)
      } catch (err) {
        const er = err as Error & { status?: number }
        showToast(er.status === 409 ? executingToast(a.maxParallel, a.maxQueued) : er.message)
      }
    })()
  }

  return (
    <div
      className="ad-card-click"
      role="button"
      tabIndex={0}
      onClick={() => go('automation', { automationId: a.id })}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return
        if ((e.target as HTMLElement).closest('button')) return
        e.preventDefault()
        go('automation', { automationId: a.id })
      }}
      style={{
        padding: 18,
        display: 'flex', flexDirection: 'column', gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div
          title={a.name}
          style={{
            flex: 1, fontSize: 15, fontWeight: 600, minWidth: 0,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}
        >
          {a.name}
        </div>
        <button
          className="ad-btn-exec"
          onClick={execute}
          disabled={executing}
          title={executing ? 'Executing…' : 'Execute now'}
          aria-label={executing ? 'Executing…' : 'Execute now'}
        >
          {/* play glyph sits 1px right of center optically */}
          <i
            className={executing ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-play'}
            style={{ fontSize: 9, marginLeft: executing ? 0 : 1 }}
          />
        </button>
      </div>
      <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)', minHeight: 39 }}>{a.description}</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
        <MetaChip c={(a.allTriggersOff || a.triggers.length === 0) ? 'var(--text-faint)' : undefined}>
          {a.triggerChip}
        </MetaChip>
        {a.allTriggersOff && (
          <MiniBadge c="var(--gray)" bg="var(--gray-bg)">OFF</MiniBadge>
        )}
        <Badge
          status={a.lastStatus}
          style={{
            animation: a.lastStatus === 'executing' ? PULSE : 'none',
          }}
        />
        {a.problems.length > 0 && (
          // §9.1/§4.1: amber Needs fixing chip — tooltip lists every problem
          // label; the full detail lives in the §9.2 banner.
          <span title={a.problems.map((p) => p.label).join('\n')} style={{ display: 'inline-flex' }}>
            <MetaChip c={resultChipColors('attention').c} bg={resultChipColors('attention').bg}>
              Needs fixing
            </MetaChip>
          </span>
        )}
        {a.resultChip && (
          <MetaChip c={resultChipColors(a.resultStatus).c} bg={resultChipColors(a.resultStatus).bg}>
            {a.resultChip}
          </MetaChip>
        )}
      </div>
      {draftJobStatus && (
        <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)' }}>
          {draftJobNote(draftJobStatus)}
        </div>
      )}
    </div>
  )
}

export default function AutomationsList() {
  const automations = useStore((s) => s.automations)
  const setSurface = useStore((s) => s.setSurface)
  const pendingDraft = useStore((s) => s.pendingDraft)
  // §9.1/§19: the pending slot's building or held drafting job — the Resume
  // draft button shows for it too (a first message still in flight has landed
  // no draft yet, but the session is resumable all the same).
  const hasSlotJob = useStore((s) => s.draftJobs.some((j) => j.owner === 'pending'))
  const refresh = useStore((s) => s.refresh)
  const [confirmFresh, setConfirmFresh] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [imported, setImported] = useState<{ name: string; automationId: string; summary: ImportSummary } | null>(null)

  // §4.4/§9.1: with a kept pending draft, New automation starts fresh —
  // confirm, delete the slot, clear its chat thread, then open the create
  // flow empty (the one discard that deletes the thread).
  const startFresh = async () => {
    setConfirmFresh(false)
    try {
      await api.deleteDraft('pending')
      await api.putChat('pending', [])
    } catch { /* backend restarting */ }
    setSurface('create', 'app')
  }

  // §5.2/§9.1 import: the modal previews (URL or file), confirm lands it.
  const importDone = async (r: { name: string; automationId: string; summary: ImportSummary }) => {
    setImportOpen(false)
    await refresh()
    setImported(r)
  }

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
      <PageTitle
        right={(
          <HeaderActions>
            <BtnGhost onClick={() => setImportOpen(true)}>
              Import
            </BtnGhost>
            {(pendingDraft || hasSlotJob) && (
              <BtnGhost onClick={() => setSurface('create', 'app')}>
                Resume draft
              </BtnGhost>
            )}
            <BtnPrimary
              onClick={() => (pendingDraft ? setConfirmFresh(true) : setSurface('create', 'app'))}
            >
              New automation
            </BtnPrimary>
          </HeaderActions>
        )}
      >
        Automations
      </PageTitle>
      {importOpen && (
        <ImportModal
          onDone={(r) => { void importDone(r) }}
          onClose={() => setImportOpen(false)}
        />
      )}
      {imported && (
        <ImportSummaryModal
          name={imported.name}
          automationId={imported.automationId}
          summary={imported.summary}
          onClose={() => setImported(null)}
        />
      )}
      {confirmFresh && (
        <ConfirmModal
          title="Start a new automation?"
          body={`Your unsaved draft${pendingDraft?.name ? ` “${pendingDraft.name}”` : ''} will be discarded. This can't be undone.`}
          confirmLabel="Discard and start new"
          danger
          onConfirm={() => void startFresh()}
          onCancel={() => setConfirmFresh(false)}
        />
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(310px,1fr))', gap: 14 }}>
        {automations.map((a) => <AutoCard key={a.id} a={a} />)}
      </div>
      {automations.length === 0 && (
        <EmptyState
          text="No automations yet. Describe a job in plain words. Your AI writes it as scripts you can read, and Autowright executes them on your schedule."
          cta={(
            <BtnPrimary
              onClick={() => (pendingDraft ? setConfirmFresh(true) : setSurface('create', 'app'))}
            >
              Create your first automation
            </BtnPrimary>
          )}
        />
      )}
    </div>
  )
}
