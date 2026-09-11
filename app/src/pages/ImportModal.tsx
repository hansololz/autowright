// §5.2/§9.1 import modal — its own file because two surfaces open it: the §9.1
// Automations page (from the input step) and the §22.3 Marketplace page, which
// hands it a preview it already fetched (`initial`) and so opens on the preview
// step.
import React, { useState } from 'react'
import { api } from '../api'
import type { ImportPreview, ImportSummary } from '../types'
import { BtnGhost, BtnPrimary, Eyebrow, MetaChip, MiniBadge, Modal } from '../ui'
import { usePlatformCopy } from '../platformCopy'
import { useTriggerPreview } from '../triggers'

// §4.1 display-name rule for §5.1 platform tokens: known tokens display
// friendly; an unrecognized token shows verbatim (it always mismatches).
const OS_DISPLAY: Record<string, string> = { macos: 'macOS', windows: 'Windows', linux: 'Linux' }
export const osName = (os: string | null) => (os ? OS_DISPLAY[os] ?? os : '')

// §5.2/§22.3 where the previewed archive came from — the source row's glyph.
export type SourceKind = 'url' | 'file' | 'marketplace'
const SOURCE_ICON: Record<SourceKind, string> = {
  url: 'fa-link', file: 'fa-file-zipper', marketplace: 'fa-store',
}

// §5.2/§9.1 import modal — input step (URL field or file picker), then the
// preview step; Import confirms the parked token and hands off to the summary.
export default function ImportModal({ onDone, onClose, initial }: {
  onDone: (r: { name: string; automationId: string; summary: ImportSummary }) => void
  onClose: () => void
  // §22.3 install: a preview the caller already parked, which opens the modal
  // straight on its preview step — there is no input step to go back to.
  initial?: { token: string; preview: ImportPreview; source: string; srcKind: SourceKind }
}) {
  // §9 per-OS copy rule: the machine noun this modal names.
  const copy = usePlatformCopy()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<false | 'url' | 'file' | 'confirm'>(false)
  const [error, setError] = useState<{ msg: string; src: SourceKind } | null>(null)
  const [pv, setPv] = useState<{
    token: string; preview: ImportPreview; source: string; srcKind: SourceKind
  } | null>(initial ?? null)
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
    // §5.1: the pick can fail (too large, unreadable) — the error goes on the
    // modal's own line; a silent return would leave it looking dead.
    let f
    try {
      f = await window.autowright?.openArchive()
    } catch (e) {
      setError({ msg: (e as Error).message, src: 'file' })
      return
    }
    if (!f) return
    if ('error' in f) {
      setError({ msg: f.error, src: 'file' })
      return
    }
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
                className={`fa-solid ${SOURCE_ICON[pv.srcKind]}`}
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
              {/* §22.3: an `initial` preview has no input step behind it — Back closes. */}
              <BtnGhost
                onClick={() => { if (initial) close(); else { setPv(null); setError(null) } }}
                disabled={!!busy}
              >
                Back
              </BtnGhost>
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
