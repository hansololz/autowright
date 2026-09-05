// Shared secret add/edit modal (§4.8, §12): Keychain-backed name/value pairs —
// values are never fetched back. Opened by the Secrets page and by the §9.2
// Discord trigger editor's New secret button.
import React, { useState } from 'react'
import { api } from './api'
import { usePlatformCopy } from './platformCopy'
import { useStore } from './store'
import type { SecretMeta } from './types'
import { BtnGhost, BtnPrimary, Eyebrow, MiniBadge, Modal } from './ui'

const NAME_RE = /^[A-Z][A-Z0-9_]*$/

// .ad-input owns padding and type (§14); call sites set only layout.
const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box' }

export type SecretModalState =
  | { mode: 'add' }
  | { mode: 'edit'; id: string; name: string; description: string; set: boolean; usedBy: { id: string; name: string }[] }

// §12: the list's mask, reused for the edit modal's kept-value row.
const MASK = '••••••••••••'

export function SecretModal({ modal, onClose, onSaved }: {
  modal: SecretModalState
  onClose: () => void
  // §19: the POST/PUT response entity — carries the (possibly just-minted) §4.8 id
  onSaved?: (saved: SecretMeta) => void
}) {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this modal on
  // every store write anywhere — every toast, every log line of every execution.
  const showToast = useStore((s) => s.showToast)
  const secrets = useStore((s) => s.secrets)
  // §9 per-OS copy rule: the secret-store name and machine noun.
  const copy = usePlatformCopy()
  const isAdd = modal.mode === 'add'
  const [name, setName] = useState(isAdd ? '' : modal.name)
  const [description, setDesc] = useState(isAdd ? '' : modal.description)
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  // §12: a set secret's stored value can't be shown (§4.8: the API never
  // returns it), so the edit modal opens on a masked "kept" row rather than an
  // empty textarea, and the textarea only appears once Replace value is pressed.
  const hasStoredValue = modal.mode === 'edit' && modal.set
  const [replacing, setReplacing] = useState(false)
  const showTextarea = !hasStoredValue || replacing

  return (
    <Modal onClose={onClose} width={460}>
      {(close) => {
        const save = async () => {
          if (isAdd) {
            if (!name) { showToast('Give the secret a name.'); return }
            if (!NAME_RE.test(name)) { showToast('Secret names must start with a letter and use only A–Z, 0–9 and _.'); return }
            // §4.8 uniqueness — the §19 POST 422s on a duplicate; the guard
            // just gives the friendlier message.
            if (secrets.some((s) => s.name === name)) {
              showToast(`${name} already exists. Edit it from the list instead.`)
              return
            }
          }
          try {
            // §4.8: a blank value on edit keeps the stored one (description-only
            // update); a blank value on add creates a placeholder (set: false).
            const saved = modal.mode === 'edit'
              ? await api.putSecret(modal.id, value, description)
              : await api.createSecret(name, value, description)
            close()
            showToast(isAdd
              ? (value ? `Saved to your ${copy.secretStore}.` : 'Saved. Add the value before an automation needs it.')
              : 'Secret updated.')
            onSaved?.(saved)
          } catch (e) { showToast((e as Error).message) }
        }

        const onKeyDown = (e: React.KeyboardEvent) => {
          if (e.key === 'Enter') void save()
        }

        // Value is a textarea (multi-line values are allowed): Enter inserts a
        // newline, Cmd/Ctrl+Enter saves. Escape is handled by the Modal shell.
        const onValueKeyDown = (e: React.KeyboardEvent) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void save()
        }

        return (
          <>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 6px', color: 'var(--text)' }}>
              {isAdd ? 'New secret' : 'Edit secret'}
            </h2>
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '0 0 18px' }}>
              {isAdd
                ? 'A password or API key your automations use. The value itself never appears in a script or a log.'
                : hasStoredValue
                  ? 'The stored value stays as it is unless you replace it. A new value is used from the next execution onward.'
                  : 'This secret has no value yet. Automations that need it fail until you add one.'}
            </p>
            <Eyebrow style={{ margin: '0 0 8px' }}>NAME</Eyebrow>
            {isAdd ? (
              <input
                className="ad-input"
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_'))}
                onKeyDown={onKeyDown}
                autoFocus
                spellCheck={false}
                placeholder="A short name, like MAIL_PASSWORD or CRM_API_KEY"
                style={inputStyle}
              />
            ) : (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8, background: 'var(--bg-inset)',
                border: '1px solid var(--hairline)', borderRadius: 8, padding: '9px 11px',
              }}>
                <i className="fa-solid fa-key" style={{ fontSize: 10, color: 'var(--text-faint)' }} />
                <span style={{ font: `500 12.5px var(--mono)`, color: 'var(--text)' }}>{name}</span>
                <span style={{
                  fontSize: 11.5, color: 'var(--text-faint)', marginLeft: 'auto',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {modal.mode === 'edit'
                    ? (modal.usedBy.map((u) => u.name).join(', ') || 'Not used yet')
                    : ''}
                </span>
              </div>
            )}
            <Eyebrow style={{ margin: '16px 0 8px' }}>DESCRIPTION · OPTIONAL</Eyebrow>
            <input
              className="ad-input oneline-ph"
              value={description}
              onChange={(e) => setDesc(e.target.value)}
              onKeyDown={onKeyDown}
              spellCheck={false}
              placeholder="Where this secret is used, so the drafting agent knows when to use it"
              style={inputStyle}
            />
            <Eyebrow style={{ margin: '16px 0 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
              VALUE
              {!isAdd && !hasStoredValue && (
                <MiniBadge c="var(--amber)" bg="var(--amber-bg)">NOT SET</MiniBadge>
              )}
            </Eyebrow>
            {showTextarea ? (
              <>
                <div style={{ position: 'relative' }}>
                  <textarea
                    className="ad-input mono"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={onValueKeyDown}
                    autoFocus={!isAdd}
                    spellCheck={false}
                    rows={3}
                    placeholder={isAdd
                      ? 'Paste the password or API key, or leave blank to add the value later'
                      : hasStoredValue
                        ? 'Paste the new value, or leave blank to keep the current one'
                        : 'Paste the password or API key'}
                    style={{
                      // paddingRight clears the overlaid Show button — the class owns the rest.
                      ...inputStyle, paddingRight: 62, resize: 'vertical', minHeight: 60,
                      WebkitTextSecurity: show ? 'none' : 'disc',
                    } as React.CSSProperties}
                  />
                  <button
                    className="ad-btn-text small"
                    onClick={() => setShow(!show)}
                    style={{ position: 'absolute', right: 9, top: 11 }}
                  >
                    {show ? 'Hide' : 'Show'}
                  </button>
                </div>
                {hasStoredValue && (
                  <button
                    className="ad-btn-text small dim"
                    onClick={() => { setReplacing(false); setValue(''); setShow(false) }}
                    style={{ marginTop: 8 }}
                  >
                    Keep current value
                  </button>
                )}
              </>
            ) : (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10, background: 'var(--bg-inset)',
                border: '1px solid var(--hairline)', borderRadius: 8, padding: '9px 11px',
              }}>
                <span style={{ font: `400 12.5px var(--mono)`, color: 'var(--text-muted)' }}>{MASK}</span>
                <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>Current value is kept secret</span>
                <button
                  className="ad-btn-text small"
                  onClick={() => setReplacing(true)}
                  style={{ marginLeft: 'auto' }}
                >
                  Replace value
                </button>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'flex-end', marginTop: 18 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-faint)', marginRight: 'auto' }}>
                <i className="fa-solid fa-lock" style={{ fontSize: 10 }} />
                Stored in your {copy.machine}’s {copy.secretStore}
              </span>
              <BtnGhost onClick={close}>Cancel</BtnGhost>
              <BtnPrimary onClick={() => { void save() }}>
                {isAdd ? `Save to ${copy.secretStore}` : 'Save changes'}
              </BtnPrimary>
            </div>
          </>
        )
      }}
    </Modal>
  )
}
