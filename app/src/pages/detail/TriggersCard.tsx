// §9.2 TRIGGERS card: the rows with toggle/edit/remove, the inline editor
// swap, the add-trigger disclosure, and the remove confirmation.
import React, { useState } from 'react'
import { api } from '../../api'
import type { Automation, DraftTrigger, Trigger } from '../../types'
import { ConfirmModal, EmptyLine, Eyebrow, MiniBadge, Toggle } from '../../ui'
import { AddTrigger, kindIcon, TriggerEditor } from './TriggerEditor'
import { runAction } from './model'

// §14 list rows: a --hairline-dim divider between rows, suppressed on the last.
const rowDivider = (i: number, total: number): React.CSSProperties =>
  (i === total - 1 ? {} : { borderBottom: '1px solid var(--hairline-dim)' })

export function TriggersCard({ auto, statusText }: { auto: Automation; statusText: string }) {
  const [editTrig, setEditTrig] = useState<string | null>(null) // §9.2: id of the row swapped for the inline editor
  const [removeTrig, setRemoveTrig] = useState<Trigger | null>(null) // §9.2: trigger awaiting remove confirmation
  const trigs = auto.triggers
  const noTrigs = trigs.length === 0

  // §4.3 trigger edits are user-owned operational state: whole-list PATCH, no
  // version, no AI. A function toast reads the saved automation — the §4.3
  // display strings live on the serialized triggers, never in the renderer.
  const putTriggers = (next: Array<Trigger | DraftTrigger>, toastMsg: string | ((saved: Automation) => string)) =>
    runAction(auto.id, async () => {
      const saved = await api.patchAutomation(auto.id, { triggers: next })
      return typeof toastMsg === 'string' ? toastMsg : toastMsg(saved)
    })
  const toggleTrigger = (t: Trigger) => {
    putTriggers(
      trigs.map((x) => (x.id === t.id ? { ...x, enabled: !x.enabled } : x)),
      t.enabled ? `Trigger turned off — ${t.short}. Execute now still works.` : `Trigger turned on — ${t.short}.`,
    )
  }
  const removeTrigger = (t: Trigger) => {
    putTriggers(trigs.filter((x) => x.id !== t.id), `Trigger removed — ${t.short}.`)
  }

  return (
    // no overflow clip (§9.2): the editor's timezone/secret popovers must
    // overhang the card edge; there are no full-bleed hover rows here that
    // would need corner masking
    <div style={{ marginBottom: 26 }}>
      <Eyebrow style={{ marginBottom: 10 }}>TRIGGERS</Eyebrow>
      <div className="ad-card">
        <div>
          {trigs.map((t, i) => t.id === editTrig ? (
            <div key={t.id} className="ad-anim-item" style={{ padding: '12px 18px', ...rowDivider(i, trigs.length) }}>
              <TriggerEditor
                hasAppStart={trigs.some((x) => x.kind === 'app_start' && x.id !== t.id)}
                initial={t}
                onSave={(nt) => {
                  setEditTrig(null)
                  putTriggers(
                    trigs.map((x) => (x.id === t.id ? { ...nt, id: t.id, enabled: t.enabled } : x)),
                    (saved) => `Trigger updated — ${saved.triggers.find((x) => x.id === t.id)?.short ?? ''}.`,
                  )
                }}
                onCancel={() => setEditTrig(null)}
              />
            </div>
          ) : (
            <div key={t.id} style={{ padding: '12px 18px', ...rowDivider(i, trigs.length) }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
                <span style={{
                  width: 28, height: 28, borderRadius: 7,
                  background: t.enabled ? 'var(--accent-chip-bg)' : 'var(--hairline-dim)',
                  color: t.enabled ? 'var(--accent)' : 'var(--text-faint)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
                  transition: 'background var(--t-hover) var(--ease-enter), color var(--t-hover) var(--ease-enter)',
                }}>
                  <i className={kindIcon(t.kind)} style={{ fontSize: 12 }} />
                </span>
                <span
                  style={{
                    flex: 1, minWidth: 0, fontFamily: 'var(--mono)', fontWeight: 500, fontSize: 12.5,
                    color: t.enabled ? 'var(--text-2)' : 'var(--text-faint)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}
                >
                  {t.label}
                </span>
                {/* §9.2: the §4.3 runIfMissed opt-out, visible without opening the editor */}
                {(t.kind === 'cron' || t.kind === 'interval' || t.kind === 'time') && t.runIfMissed === false && (
                  <MiniBadge c="var(--gray)" bg="var(--gray-bg)">NO CATCH-UP</MiniBadge>
                )}
                {t.kind !== 'app_start' && (
                  <button
                    className="ad-btn-icon"
                    onClick={() => setEditTrig(t.id)}
                    title="Edit trigger"
                    aria-label="Edit trigger"
                  >
                    <i className="fa-solid fa-pen" style={{ fontSize: 11 }} />
                  </button>
                )}
                <Toggle on={t.enabled} onChange={() => toggleTrigger(t)} title={t.enabled ? 'Turn this trigger off' : 'Turn this trigger on'} />
                <button className="ad-btn-x" onClick={() => setRemoveTrig(t)} title="Remove trigger" aria-label="Remove trigger">
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
              {/* §9.2: a message trigger's listener state — errors in red, connecting muted, connected silent */}
              {(t.kind === 'discord' || t.kind === 'imessage') && t.enabled && t.connection && t.connection.state !== 'connected' && (
                <div className="ad-anim-item" style={{
                  marginLeft: 41, marginTop: 2, fontFamily: 'var(--mono)', fontSize: 11.5,
                  color: t.connection.state === 'error' ? 'var(--red-text)' : 'var(--text-faint)',
                }}>
                  {t.connection.state === 'error' ? t.connection.error ?? 'connection error' : 'connecting…'}
                </div>
              )}
            </div>
          ))}
          {noTrigs && <EmptyLine>No triggers</EmptyLine>}
          <div style={{ padding: '12px 18px' }}>
            <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)' }}>{statusText}</div>
            <AddTrigger
              hasAppStart={trigs.some((t) => t.kind === 'app_start')}
              onAdd={(t) => putTriggers(
                [...trigs, { ...t, enabled: true }],
                // a whole-list replace keeps order — the added entry is last
                (saved) => `Trigger added — ${saved.triggers[saved.triggers.length - 1]?.short ?? ''}.`,
              )}
            />
          </div>
        </div>
      </div>
      {removeTrig && (
        <ConfirmModal
          title="Remove this trigger?"
          body={(
            <>
              <span style={{ fontWeight: 500, color: 'var(--text)' }}>{removeTrig.label}</span>
              {' '}is removed from this automation. Its settings are gone — add it again to get it back.
            </>
          )}
          confirmLabel="Remove trigger"
          danger
          onConfirm={() => { const t = removeTrig; setRemoveTrig(null); removeTrigger(t) }}
          onCancel={() => setRemoveTrig(null)}
        />
      )}
    </div>
  )
}
