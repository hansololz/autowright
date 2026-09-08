// Automations list (§4, prototype "Automations list" screen).
import React, { useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import type { Automation, ImportPreview, ImportSummary } from '../types'
import { Badge, BtnGhost, BtnPrimary, ConfirmModal, EmptyState, Eyebrow, HeaderActions, MetaChip, MiniBadge, Modal, P, PageTitle, PULSE, resultChipColors, executingToast } from '../ui'
import { usePlatformCopy } from '../platformCopy'
import { useTriggerPreview } from '../triggers'

// §4.1 display-name rule for §5.1 platform tokens: known tokens display
// friendly; an unrecognized token shows verbatim (it always mismatches).
const OS_DISPLAY: Record<string, string> = { macos: 'macOS', windows: 'Windows', linux: 'Linux' }
const osName = (os: string | null) => (os ? OS_DISPLAY[os] ?? os : '')

// §5.1/§9.1 import summary modal — only the sections that apply render.
function ImportSummaryModal({ name, automationId, summary, onClose }: {
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


// §5.2/§9.1 import modal — input step (URL field or file picker), then the
// preview step; Import confirms the parked token and hands off to the summary.
function ImportModal({ onDone, onClose }: {
  onDone: (r: { name: string; automationId: string; summary: ImportSummary }) => void
  onClose: () => void
}) {
  // §9 per-OS copy rule: the machine noun this modal names.
  const copy = usePlatformCopy()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<false | 'url' | 'file' | 'confirm'>(false)
  const [error, setError] = useState<{ msg: string; src: 'url' | 'file' } | null>(null)
  const [pv, setPv] = useState<{
    token: string; preview: ImportPreview; source: string; srcKind: 'url' | 'file'
  } | null>(null)
  // §19: the preview's trigger chips label through POST /triggers/preview —
  // the renderer keeps no local trigger-math mirror (§4.3)
  const trigPreviews = useTriggerPreview(pv?.preview.triggers ?? [])

  const fetchUrl = async () => {
    if (!url.trim() || busy) return
    setBusy('url'); setError(null)
    try {
      const r = await api.importFromUrl(url.trim())
      setPv({ token: r.token, preview: r.preview,
              source: r.preview.resolvedUrl || url.trim(), srcKind: 'url' })
    } catch (e) { setError({ msg: (e as Error).message, src: 'url' }) }
    setBusy(false)
  }
  const chooseFile = async () => {
    if (busy) return
    const f = await window.autowright?.openArchive()
    if (!f) return
    setBusy('file'); setError(null)
    try {
      const r = await api.importPreview(f.data)
      setPv({ token: r.token, preview: r.preview, source: f.name, srcKind: 'file' })
    } catch (e) { setError({ msg: (e as Error).message, src: 'file' }) }
    setBusy(false)
  }

  const errLine = (msg: string) => (
    <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--red-text)', margin: '8px 0 0' }}>
      {msg}
    </p>
  )
  const nameRow = (n: string, extra?: React.ReactNode) => (
    <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
      <span style={{ font: `500 12px var(--mono)`, color: 'var(--text)' }}>{n}</span>
      {extra}
    </div>
  )
  const section = (title: string, body: React.ReactNode) => (
    <div style={{ marginTop: 16 }}>
      <Eyebrow style={{ margin: '0 0 8px' }}>{title}</Eyebrow>
      {body}
    </div>
  )

  return (
    <Modal onClose={onClose} width={460}>
      {(close) => {
        const confirm = async () => {
          if (!pv || busy) return
          setBusy('confirm'); setError(null)
          try {
            const r = await api.importConfirm(pv.token)
            close()
            onDone({ name: r.automation.name, automationId: r.automation.id, summary: r.summary })
          } catch (e) { setError({ msg: (e as Error).message, src: pv.srcKind }); setBusy(false) }
        }
        return pv === null ? (
          <>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>
              Import automation
            </h2>
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '6px 0 0' }}>
              Add an automation someone shared — from a link, or a file on this {copy.machine}.
            </p>
            <Eyebrow style={{ margin: '18px 0 6px' }}>FROM A LINK</Eyebrow>
            <input
              className="ad-input"
              value={url}
              onChange={(e) => { setUrl(e.target.value); setError(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') void fetchUrl() }}
              autoFocus
              spellCheck={false}
              placeholder="https://github.com/… or a direct .autowright link"
              style={{
                width: '100%', boxSizing: 'border-box', color: 'var(--text)',
                font: `400 12.5px var(--mono)`, padding: '9px 11px',
              }}
            />
            {error?.src === 'url' ? errLine(error.msg) : (
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: '7px 0 0' }}>
                A GitHub repository page, a release, or any https link to an .autowright file.
              </p>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '18px 0' }}>
              <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
              <Eyebrow>OR</Eyebrow>
              <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
            </div>
            <button
              className="ad-btn-dashed"
              onClick={() => { void chooseFile() }}
              disabled={!!busy}
              style={{
                alignSelf: 'stretch', width: '100%',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9,
              }}
            >
              <i className="fa-solid fa-file-import" style={{ fontSize: 12, color: 'var(--text-faint)' }} />
              {busy === 'file' ? 'Reading…' : `Choose an .autowright file on this ${copy.machine}…`}
            </button>
            {error?.src === 'file' && errLine(error.msg)}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <BtnGhost onClick={close} disabled={!!busy}>Cancel</BtnGhost>
              <BtnPrimary onClick={() => { void fetchUrl() }} disabled={!url.trim() || !!busy}>
                {busy === 'url' ? 'Fetching…' : 'Import'}
              </BtnPrimary>
            </div>
          </>
        ) : (
          <>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>
              {pv.preview.landsAs}
            </h2>
            {pv.preview.description && (
              <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '6px 0 0' }}>
                {pv.preview.description}
              </p>
            )}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, background: 'var(--bg-inset)',
              border: '1px solid var(--hairline)', borderRadius: 8, padding: '8px 11px',
              marginTop: 12,
            }}>
              <i
                className={`fa-solid ${pv.srcKind === 'url' ? 'fa-link' : 'fa-file-zipper'}`}
                style={{ fontSize: 10, color: 'var(--text-faint)' }}
              />
              <span style={{
                font: `500 11.5px var(--mono)`, color: 'var(--text-muted)', minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {pv.source}
              </span>
            </div>
            {pv.preview.landsAs !== pv.preview.name && (
              // §5.1 name dedupe run dry — the title above shows what lands.
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: '8px 0 0' }}>
                An automation named “{pv.preview.name}” already exists, so this one arrives as “{pv.preview.landsAs}”.
              </p>
            )}
            {pv.preview.triggers.length > 0 && section('TRIGGERS', (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {pv.preview.triggers.map((_, i) => trigPreviews[i] && (
                  <MetaChip key={i}>{trigPreviews[i].label}</MetaChip>
                ))}
              </div>
            ))}
            {pv.preview.steps.length > 0 && section('STEPS', pv.preview.steps.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '3px 0' }}>
                <span style={{
                  font: `500 11px var(--mono)`, color: 'var(--text-faint)',
                  width: 14, textAlign: 'right', flexShrink: 0,
                }}>
                  {i + 1}
                </span>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{s.name}</span>
                {s.agent && <MiniBadge c="var(--accent)" bg="var(--accent-bg)">AGENT</MiniBadge>}
              </div>
            )))}
            {/* §5.1/§9.1: the match ladders run dry — ON THIS MAC (exact),
                USES <local> (renaming match), amber NO MATCH (would land
                needing attention). */}
            {pv.preview.secrets.length > 0 && section('SECRETS', (
              pv.preview.secrets.map((s) => nameRow(s.name, s.matchedTo === null
                ? <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NO MATCH</MiniBadge>
                : s.matchedTo === s.name
                  ? <MiniBadge c="var(--gray)" bg="var(--gray-bg)">ON THIS {copy.machine.toUpperCase()}</MiniBadge>
                  : <MiniBadge c="var(--gray)" bg="var(--gray-bg)">USES {s.matchedTo}</MiniBadge>))
            ))}
            {pv.preview.agents.length > 0 && section('AGENTS', (
              pv.preview.agents.map((g) => nameRow(g.name, g.matchedTo === null
                ? <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NO MATCH</MiniBadge>
                : g.matchedTo === g.name
                  ? <MiniBadge c="var(--gray)" bg="var(--gray-bg)">ON THIS {copy.machine.toUpperCase()}</MiniBadge>
                  : <MiniBadge c="var(--gray)" bg="var(--gray-bg)">USES {g.matchedTo}</MiniBadge>))
            ))}
            <div style={{ borderTop: '1px solid var(--hairline)', marginTop: 18, paddingTop: 12 }}>
              {pv.preview.osMismatch && (
                // §5.1/§9.1: the archive was exported on another platform.
                <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--amber)', margin: '0 0 4px' }}>
                  Built on {osName(pv.preview.os)} — its steps may need rewriting before they run on this {copy.machine}.
                </p>
              )}
              {[...pv.preview.secrets, ...pv.preview.agents].some((x) => x.matchedTo === null) && (
                <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--amber)', margin: '0 0 4px' }}>
                  Some agents or secrets have no match on this {copy.machine} - the automation arrives needing attention.
                </p>
              )}
              {pv.preview.packages.length > 0 && (
                <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)', margin: '0 0 4px' }}>
                  {pv.preview.packages.length} package{pv.preview.packages.length === 1 ? ' installs' : 's install'} with the import.
                </p>
              )}
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: 0 }}>
                Its triggers arrive off — review the scripts in the editor before enabling them.
              </p>
            </div>
            {error && errLine(error.msg)}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <BtnGhost onClick={() => { setPv(null); setError(null) }} disabled={!!busy}>Back</BtnGhost>
              <BtnPrimary onClick={() => { void confirm() }} disabled={!!busy}>
                {busy ? 'Importing…' : 'Import'}
              </BtnPrimary>
            </div>
          </>
        )
      }}
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
