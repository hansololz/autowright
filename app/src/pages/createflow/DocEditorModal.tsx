// §11 document-editor modal: the one editing surface for the spec and notes
// documents on the create/edit page. Pressing a card's Edit
// opens it over the page (the card keeps its view state behind the backdrop);
// it is the §9.2 step-script modal's code pane on its own — a fixed toolbar
// (eyebrow, filename, live line count, Cancel / Save) over a
// full-height textarea on the --bg-code ground, and a footer stating what Save
// does. Cancel, Escape and a backdrop click close silently when the text is
// unchanged and raise the discard confirm otherwise (the Modal's guardClose);
// every path plays the §14 exit before the caller's state changes.
import React, { useEffect, useRef, useState } from 'react'
import { ConfirmModal, Eyebrow, Modal, isTopModal, useOverlayThumb } from '../../ui'

export type DocKind = 'spec' | 'notes'

export const DOC_META: Record<DocKind, {
  eyebrow: string; file: string; placeholder: string; footer: string; discardTitle: string; discardBody: string
}> = {
  spec: {
    eyebrow: 'SPEC', file: 'spec.md',
    placeholder: 'Markdown — what the automation should do, in plain words.',
    footer: 'Saving rewrites the steps to match the new spec.',
    discardTitle: 'Discard your spec edits?',
    discardBody: 'The changes you typed into the spec editor will be lost.',
  },
  notes: {
    eyebrow: 'NOTES', file: 'notes.md',
    placeholder: 'Markdown — your AI’s working knowledge for this automation. Prune anything stale or wrong.',
    footer: 'Notes guide the next sync — saving never marks the workflow out of sync.',
    discardTitle: 'Discard your notes edits?',
    discardBody: 'The changes you typed into the notes editor will be lost.',
  },
}

const TOOLBAR = 44
const FOOTER = 36
const LINE = 12 * 1.65
const PAD_Y = 14 + 20

/** "<n> lines" for the editor's current text: a trailing final newline is not
 * counted, and an empty editor holds 0 lines. */
export function docLineCount(text: string): number {
  return text === '' ? 0 : text.replace(/\n$/, '').split('\n').length
}

/** The card's fixed height for the life of the open modal: the opened text at
 * the editor's rhythm plus six spare lines, floored at 440px and capped at
 * 82vh — a short document gets a card that fits it with room to write. */
export function docModalFrame(text: string): string {
  const lines = Math.max(1, docLineCount(text))
  return `clamp(440px, ${Math.ceil(TOOLBAR + FOOTER + PAD_Y + (lines + 6) * LINE)}px, 82vh)`
}

export function DocEditorModal({ kind, text, original, onChange, onSave, onDiscard }: {
  kind: DocKind
  text: string
  /** the text the modal opened with — Save is disabled while `text` equals it */
  original: string
  onChange: (text: string) => void
  /** fired after the exit animation: apply the text and drop the edit state */
  onSave: () => void
  /** fired after the exit animation: drop the edit state without applying */
  onDiscard: () => void
}) {
  const meta = DOC_META[kind]
  const thumb = useOverlayThumb()
  const dirty = text !== original
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  const [confirm, setConfirm] = useState(false)
  // what the exit animation settles into — Save applies, everything else discards
  const pending = useRef<'save' | 'discard'>('discard')
  const closeRef = useRef<() => void>(() => {})
  const save = () => { if (dirtyRef.current) { pending.current = 'save'; closeRef.current() } }
  const cancel = () => {
    if (dirtyRef.current) setConfirm(true)
    else { pending.current = 'discard'; closeRef.current() }
  }
  // the frame is sized once, from the text the modal opened with
  const frame = useRef(docModalFrame(text)).current
  const count = docLineCount(text)
  const ta = useRef<HTMLTextAreaElement | null>(null)
  // focused on open with the caret at the end of the text
  useEffect(() => {
    const el = ta.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])
  // ⌘S / Ctrl+S saves while the modal is open (swallowed while Save is
  // disabled). It yields to a card stacked above this one — the discard
  // confirm — the way the Modal's own Escape handler yields to the stack.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isTopModal()) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // Escape / backdrop: unchanged text closes; changed text raises the confirm
  // and keeps the card open (the confirm stacks above it).
  const guardClose = () => {
    if (!dirtyRef.current) return true
    setConfirm(true)
    return false
  }
  return (
    <Modal
      onClose={() => (pending.current === 'save' ? onSave() : onDiscard())}
      width={860} ariaLabel={`Edit ${meta.file}`} guardClose={guardClose}
      cardStyle={{ padding: 0, width: 'min(860px, 92vw)', overflow: 'hidden' }}
    >
      {(close) => {
        closeRef.current = close
        return (
          <div className="ad-docmodal" data-testid="doc-editor" style={{
            height: frame, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--bg-code)',
          }}>
            <div style={{
              height: TOOLBAR, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
              padding: '0 14px 0 18px', borderBottom: '1px solid var(--hairline)',
            }}>
              <Eyebrow style={{ flex: 'none' }}>{meta.eyebrow}</Eyebrow>
              <span style={{
                font: "400 11px var(--mono)", color: 'var(--text-deco)', flex: 1, minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {meta.file}
              </span>
              <span data-testid="doc-lines" style={{ font: "500 11px var(--mono)", color: 'var(--text-faint)', flex: 'none' }}>
                {count} {count === 1 ? 'line' : 'lines'}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 'none', marginLeft: 6 }}>
                <button className="ad-btn-text dim small ad-focus-inset" onClick={cancel}>
                  Cancel
                </button>
                <button className="ad-btn-link small ad-focus-inset" disabled={!dirty} onClick={save}>
                  Save
                </button>
              </div>
            </div>
            <div className="ad-scrollwrap" style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
              <textarea
                ref={(el) => { ta.current = el; thumb.attach(el) }}
                data-testid={`${kind}-editor`}
                value={text}
                onChange={(e) => onChange(e.target.value)}
                onScroll={thumb.onScroll}
                placeholder={meta.placeholder}
                spellCheck={false}
                className="ad-scrollhide"
                style={{
                  flex: 1, minHeight: 0, width: '100%', background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--text-2)', font: "400 12px/1.65 var(--mono)", padding: '14px 28px 20px 20px',
                  resize: 'none', display: 'block', overflowY: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
                }}
              />
              {thumb.node}
            </div>
            <div style={{
              height: FOOTER, flex: 'none', display: 'flex', alignItems: 'center', padding: '0 20px',
              borderTop: '1px solid var(--hairline)', font: "400 11.5px/1.5 var(--sans)", color: 'var(--text-muted)',
            }}>
              {meta.footer}
            </div>
            {confirm && (
              <ConfirmModal
                title={meta.discardTitle}
                body={meta.discardBody}
                confirmLabel="Discard edits"
                danger
                onConfirm={() => { setConfirm(false); pending.current = 'discard'; close() }}
                onCancel={() => setConfirm(false)}
              />
            )}
          </div>
        )
      }}
    </Modal>
  )
}
