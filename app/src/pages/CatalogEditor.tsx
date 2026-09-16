// §22.7 catalog editor: create and edit share one two-column form on the §9.2
// step-script modal's frame. Left, the catalog navigator (a details row, one
// row per entry, a pinned Add automation… button); right, the viewed form.
// The stacked add form takes the entry's four fields (title, description,
// reference, image) as typed - the only way in. The editor never exports or
// reads an archive on the user's behalf: an automation from this machine is
// exported first (§9.2 Export…) and then listed by the path it landed on.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { MarketplaceCatalogSaveEntry, MarketplaceSource } from '../types'
import {
  BtnGhost, ConfirmModal, EmptyLine, Eyebrow, Modal, PageLoading, ScrollArea, Spinner,
} from '../ui'

export const KEPT_LABEL = 'Kept by Autowright'
export const CATALOG_FILE = 'marketplace-catalog.yaml'

export const lastSegment = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p

/** §22.3 location chip text: a link shows its host, a path the catalog's file
 * name, a null location "Kept by Autowright"; the full location rides in the
 * `title` attribute. */
export function locationLabel(source: MarketplaceSource): string {
  if (source.location === null) return KEPT_LABEL
  if (source.kind === 'url') {
    try { return new URL(source.location).hostname } catch { return source.location }
  }
  return lastSegment(source.location)
}

export const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', color: 'var(--text)',
  font: '400 12.5px var(--sans)', padding: '9px 11px',
}
export const monoInput: React.CSSProperties = { ...inputStyle, font: '400 12.5px var(--mono)' }
export const caption: React.CSSProperties = { fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: '7px 0 0' }

export const errLine = (msg: string, testId?: string) => (
  <p data-testid={testId} style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--red-text)', margin: '8px 0 0' }}>
    {msg}
  </p>
)

// §22.1 reference forms, checked by the editor before a save travels: an
// https link or an absolute path (macOS/Linux, a drive letter, a UNC share),
// ending in the right extension once any query string is dropped.
const isHttps = (s: string) => /^https:\/\//i.test(s)
const isAbsolute = (s: string) => /^(\/|[A-Za-z]:[\\/]|\\\\)/.test(s)
const extensionOf = (s: string) => s.replace(/[?#].*$/, '').toLowerCase()
export const archiveRefOk = (s: string) =>
  (isHttps(s) || isAbsolute(s)) && extensionOf(s).endsWith('.autowright')
export const imageRefOk = (s: string) =>
  (isHttps(s) || isAbsolute(s)) && /\.(png|jpe?g|webp|gif)$/.test(extensionOf(s))

const TITLE_BLANK = 'Give this automation a title.'
const ARCHIVE_BAD = 'Give an https link or an absolute path to an .autowright file.'
const IMAGE_BAD = 'Give an https link or an absolute path to a .png, .jpg, .jpeg, .webp, or .gif image.'

// §22.7 working-catalog entry: a reference as written. Every entry travels as
// `path`; the editor never sends automationId, exportFolder, or archiveFile
// (those are the §22.5 CLI's).
export interface EditorEntry {
  key: number
  title: string
  description: string
  image: string
  path: string
}

const toSaveEntry = ({ title, description, image, path }: EditorEntry): MarketplaceCatalogSaveEntry => ({
  title, description,
  ...(image ? { image } : {}),
  path,
})

/** §22.7: the entry form's checks, in Save order - the first problem, or null. */
const entryProblem = (e: { title: string; path: string; image: string }) =>
  !e.title.trim() ? TITLE_BLANK
    : !archiveRefOk(e.path) ? ARCHIVE_BAD
      : e.image && !imageRefOk(e.image) ? IMAGE_BAD : null

/** The navigator's one-line reference label for an entry. */
function referenceLabel(e: EditorEntry): string {
  const p = e.path
  if (!p.trim()) return 'No archive yet'
  if (isHttps(p)) {
    try { return new URL(p).hostname } catch { return p }
  }
  return lastSegment(p)
}

const eyebrowMargin = (first: boolean): React.CSSProperties => ({ margin: first ? '0 0 6px' : '16px 0 6px' })

const ARCHIVE_PLACEHOLDER = 'https://…/name.autowright or /path/to/name.autowright'
const IMAGE_PLACEHOLDER = 'Optional: https://… or /path/to/preview.png'
const IMAGE_CAPTION = 'A preview for the marketplace page: an https link, or an absolute path to a .png, .jpg, .jpeg, .webp, or .gif.'

/** §22.7 add-automation form: the entry's four fields typed in, checked on
 * Add the way Save checks an entry, then appended to the working catalog. */
function AddAutomationForm({ onAdd, onClose }: {
  onAdd: (entry: Omit<EditorEntry, 'key'>) => void
  onClose: () => void
}) {
  const copy = usePlatformCopy()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [path, setPath] = useState('')
  const [image, setImage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const canAdd = title.trim() !== ''

  return (
    <Modal onClose={onClose} width={520} zIndex={80} ariaLabel="Add automation">
      {(close) => {
        const add = () => {
          if (!canAdd) return
          const entry = { title: title.trim(), description: description.trim(), path: path.trim(), image: image.trim() }
          // §22.7: the same checks Save runs, here, so a bad reference never
          // joins the catalog unnoticed.
          const problem = entryProblem(entry)
          if (problem) { setError(problem); return }
          onAdd(entry)
          close()
        }
        const onEnter = (e: React.KeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); add() } }
        return (
          <div data-testid="catalog-picker">
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>Add automation</h2>
            <Eyebrow style={{ margin: '18px 0 6px' }}>TITLE</Eyebrow>
            <input className="ad-input" value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={onEnter}
              autoFocus spellCheck={false} data-testid="catalog-picker-title" style={inputStyle} />
            <Eyebrow style={{ margin: '14px 0 6px' }}>DESCRIPTION</Eyebrow>
            <input className="ad-input" value={description} onChange={(e) => setDescription(e.target.value)} onKeyDown={onEnter}
              data-testid="catalog-picker-description" style={inputStyle} />
            <Eyebrow style={{ margin: '14px 0 6px' }}>AUTOMATION</Eyebrow>
            <input className="ad-input" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={onEnter}
              spellCheck={false} placeholder={ARCHIVE_PLACEHOLDER} data-testid="catalog-picker-path" style={monoInput} />
            <p style={caption}>An https link, or the archive's absolute path on this {copy.machine}.</p>
            <Eyebrow style={{ margin: '14px 0 6px' }}>IMAGE</Eyebrow>
            <input className="ad-input" value={image} onChange={(e) => setImage(e.target.value)} onKeyDown={onEnter}
              spellCheck={false} placeholder={IMAGE_PLACEHOLDER} data-testid="catalog-picker-image" style={monoInput} />
            <p style={caption}>{IMAGE_CAPTION}</p>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 18 }}>
              <div style={{ flex: 1, minWidth: 0, fontSize: 12.5, lineHeight: 1.5 }}>
                {error && <span data-testid="catalog-picker-error" style={{ color: 'var(--red-text)' }}>{error}</span>}
              </div>
              <BtnGhost onClick={close}>Cancel</BtnGhost>
              <button className="ad-btn-primary" data-testid="catalog-picker-add" onClick={add} disabled={!canAdd}>Add</button>
            </div>
          </div>
        )
      }}
    </Modal>
  )
}

const FRAME_HEIGHT = 'min(680px, 82vh)'
const NAV_ROW: React.CSSProperties = { display: 'block', width: '100%', padding: '9px 18px', textAlign: 'left' }
const VIEWED_ROW: React.CSSProperties = {
  // §14 selected row: the --bg-active wash plus an inset accent bar
  background: 'var(--bg-active)', boxShadow: 'inset 2px 0 0 var(--accent)', userSelect: 'text', cursor: 'default',
}
const rowTitle = (viewed: boolean, faint: boolean): React.CSSProperties => ({
  font: `${viewed ? 600 : 500} 13px/18px var(--sans)`,
  color: faint ? 'var(--text-faint)' : viewed ? 'var(--text)' : 'var(--text-muted)',
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
})
const rowSub: React.CSSProperties = {
  font: '400 11.5px/1.45 var(--sans)', color: 'var(--text-muted)', marginTop: 2,
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
}

/** One navigator row: the viewed row is a plain block (§9.2 rule), any other a
 * button that views it. */
function NavRow({ viewed, onView, testId, children }: {
  viewed: boolean; onView: () => void; testId: string; children: React.ReactNode
}) {
  if (viewed) {
    return <div data-testid={testId} aria-current="true" style={{ ...NAV_ROW, ...VIEWED_ROW }}>{children}</div>
  }
  return (
    <button className="ad-btn-bare ad-hover-row ad-focus-inset" data-testid={testId} onClick={onView} style={{ ...NAV_ROW, cursor: 'pointer' }}>
      {children}
    </button>
  )
}

/** §22.7 catalog editor. Edit mode opens on the catalog as the editor sees it
 * (GET) and PUTs it whole on Save; create mode opens empty and POSTs the
 * content on Create - the new catalog is kept by Autowright. Every entry is a
 * reference as written - the editor never exports. */
export default function CatalogEditorModal({ source, onClose, onSaved }: {
  /** the source to edit, or null to create a new catalog */
  source: MarketplaceSource | null
  onClose: () => void
  onSaved: (source: MarketplaceSource) => void
}) {
  const copy = usePlatformCopy()
  const showToast = useStore((s) => s.showToast)
  const creating = source === null
  const [loaded, setLoaded] = useState<string | null>(creating ? '' : null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [rows, setRows] = useState<EditorEntry[]>([])
  const [viewed, setViewed] = useState<'details' | number>('details')
  const [picker, setPicker] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const nextKey = useRef(1)

  const snapshot = (s: { name: string; description: string; rows: EditorEntry[] }) =>
    JSON.stringify({ ...s, rows: s.rows.map(({ key: _key, ...rest }) => rest) })
  // §22.7: a catalog created here is kept by Autowright - there is no
  // location to choose; Export on the page hands the file out.
  const where = source?.location ?? KEPT_LABEL

  useEffect(() => {
    if (creating) { setLoaded(snapshot({ name: '', description: '', rows: [] })); return }
    let gone = false
    void (async () => {
      try {
        const c = await api.marketplaceCatalogRead(source.id)
        if (gone) return
        const initial: EditorEntry[] = c.entries.map((e) => ({
          key: nextKey.current++, title: e.title, description: e.description, image: e.image || '', path: e.path,
        }))
        setName(c.name); setDescription(c.description); setRows(initial)
        setLoaded(snapshot({ name: c.name, description: c.description, rows: initial }))
      } catch (e) {
        // §22.4: a 409 (not on this machine) or 422 (unreadable file) - the
        // modal can't open on nothing, so it toasts and closes.
        if (gone) return
        showToast((e as Error).message)
        onClose()
      }
    })()
    return () => { gone = true }
  }, [source?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = loaded !== null && snapshot({ name, description, rows }) !== loaded
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  const pending = useRef<'save' | 'discard'>('discard')
  const saved = useRef<MarketplaceSource | null>(null)
  const closeRef = useRef<() => void>(() => {})

  const update = (key: number, patch: Partial<EditorEntry>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const addEntry = (entry: Omit<EditorEntry, 'key'>) => {
    const key = nextKey.current++
    setRows((prev) => [...prev, { ...entry, key }])
    setViewed(key)
    setError(null)
  }
  const removeEntry = (key: number) => {
    const i = rows.findIndex((r) => r.key === key)
    const rest = rows.filter((r) => r.key !== key)
    setRows(rest)
    // §22.7: view the entry after it, the one before when it was last, the
    // details form when none is left.
    setViewed(rest.length === 0 ? 'details' : rest[Math.min(i, rest.length - 1)].key)
    setError(null)
  }
  const viewedIndex = viewed === 'details' ? -1 : rows.findIndex((r) => r.key === viewed)
  const entry = viewedIndex >= 0 ? rows[viewedIndex] : null

  const canSave = !busy && loaded !== null
  const save = async () => {
    if (!canSave) return
    // §22.7: the editor checks every entry before anything travels, and views
    // the first one with a problem.
    for (const r of rows) {
      const problem = entryProblem(r)
      if (problem) { setViewed(r.key); setError(problem); return }
    }
    setBusy(true); setError(null)
    try {
      const body = { name, description, entries: rows.map(toSaveEntry) }
      saved.current = source
        ? await api.marketplaceCatalogSave(source.id, body)
        : await api.marketplaceCatalogCreate(body)
      pending.current = 'save'
      closeRef.current()
    } catch (e) {
      // §22.7: a 422 or 409 shows in the footer; one naming an entry views it.
      const msg = (e as Error).message
      const m = /^entry (\d+):/.exec(msg)
      if (m && rows[Number(m[1])]) setViewed(rows[Number(m[1])].key)
      setError(msg); setBusy(false)
    }
  }
  const guardClose = () => {
    if (!dirtyRef.current) return true
    setConfirm(true)
    return false
  }

  return (
    <Modal
      onClose={() => (pending.current === 'save' && saved.current ? onSaved(saved.current) : onClose())}
      width={960} ariaLabel={creating ? 'Create catalog' : 'Edit catalog'} guardClose={guardClose}
      cardStyle={{ padding: 0, width: 'min(960px, 92vw)', overflow: 'hidden' }}
    >
      {(close) => {
        closeRef.current = close
        const tryClose = () => { if (dirtyRef.current) setConfirm(true); else close() }
        return (
          <div data-testid="catalog-editor" style={{ height: FRAME_HEIGHT, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
              {/* catalog navigator */}
              <div style={{
                width: 280, flex: 'none', minHeight: 0, display: 'flex', flexDirection: 'column',
                borderRight: '1px solid var(--hairline-dim)',
              }}>
                <div style={{
                  height: 44, flex: 'none', display: 'flex', alignItems: 'center',
                  padding: '0 18px', borderBottom: '1px solid var(--hairline-dim)',
                }}>
                  <Eyebrow>{creating ? 'CREATE CATALOG' : 'EDIT CATALOG'}</Eyebrow>
                </div>
                <ScrollArea wrapStyle={{ flex: 1, minHeight: 0 }}>
                  <div style={{ padding: '6px 0 12px' }}>
                    <NavRow viewed={viewed === 'details'} onView={() => setViewed('details')} testId="catalog-nav-details">
                      <div style={rowTitle(viewed === 'details', !name.trim())}>{name.trim() || 'Untitled catalog'}</div>
                      <div data-testid="catalog-where" style={{ ...rowSub, font: '400 11px/1.45 var(--mono)' }} title={where}>{where}</div>
                    </NavRow>
                    {loaded !== null && (
                      <>
                        <Eyebrow style={{ padding: '14px 18px 4px' }}>AUTOMATIONS · {rows.length}</Eyebrow>
                        {rows.length === 0 ? (
                          <EmptyLine style={{ padding: '6px 18px 10px' }}>No automations yet.</EmptyLine>
                        ) : rows.map((r) => (
                          <NavRow key={r.key} viewed={viewed === r.key} onView={() => setViewed(r.key)} testId="catalog-nav-row">
                            <div style={rowTitle(viewed === r.key, !r.title.trim())}>{r.title.trim() || 'Untitled'}</div>
                            <div style={rowSub}>{referenceLabel(r)}</div>
                          </NavRow>
                        ))}
                      </>
                    )}
                  </div>
                </ScrollArea>
                <div style={{ flex: 'none', padding: '12px 14px', borderTop: '1px solid var(--hairline-dim)' }}>
                  <button className="ad-btn-dashed" data-testid="catalog-add-automation" onClick={() => setPicker(true)} disabled={loaded === null}
                    style={{ width: '100%', alignSelf: 'stretch', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9 }}>
                    <i className="fa-solid fa-plus" style={{ fontSize: 11, color: 'var(--text-faint)' }} />
                    Add automation…
                  </button>
                </div>
              </div>
              {/* form pane */}
              <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
                <div style={{
                  height: 44, flex: 'none', display: 'flex', alignItems: 'center', gap: 12,
                  padding: '0 10px 0 22px', borderBottom: '1px solid var(--hairline-dim)',
                }}>
                  <Eyebrow style={{ flex: 1 }}>
                    {entry ? `AUTOMATION ${viewedIndex + 1} OF ${rows.length}` : 'DETAILS'}
                  </Eyebrow>
                  <button className="ad-btn-icon" aria-label="Close" data-testid="catalog-close" onClick={tryClose}>
                    <i className="fa-solid fa-xmark" />
                  </button>
                </div>
                {loaded === null ? <PageLoading /> : (
                  <ScrollArea key={String(viewed)} className="ad-anim-fade" wrapStyle={{ flex: 1, minHeight: 0 }}>
                    <div style={{ padding: '18px 22px 24px' }}>
                      {entry ? (
                        <>
                          <Eyebrow style={eyebrowMargin(true)}>TITLE</Eyebrow>
                          <input className="ad-input" value={entry.title} onChange={(e) => update(entry.key, { title: e.target.value })}
                            spellCheck={false} data-testid="catalog-entry-title" style={inputStyle} />
                          <Eyebrow style={eyebrowMargin(false)}>DESCRIPTION</Eyebrow>
                          <input className="ad-input" value={entry.description} onChange={(e) => update(entry.key, { description: e.target.value })}
                            data-testid="catalog-entry-description" style={inputStyle} />
                          <Eyebrow style={eyebrowMargin(false)}>AUTOMATION</Eyebrow>
                          <input className="ad-input" value={entry.path} onChange={(e) => update(entry.key, { path: e.target.value })}
                            spellCheck={false} placeholder={ARCHIVE_PLACEHOLDER}
                            data-testid="catalog-entry-path" style={monoInput} />
                          <p style={caption}>An https link, or the archive's absolute path on this {copy.machine}.</p>
                          <Eyebrow style={eyebrowMargin(false)}>IMAGE</Eyebrow>
                          <input className="ad-input" value={entry.image} onChange={(e) => update(entry.key, { image: e.target.value })}
                            spellCheck={false} placeholder={IMAGE_PLACEHOLDER}
                            data-testid="catalog-entry-image" style={monoInput} />
                          <p style={caption}>{IMAGE_CAPTION}</p>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 18 }}>
                            <button className="ad-btn-text danger" data-testid="catalog-entry-remove" aria-label="Remove entry"
                              onClick={() => removeEntry(entry.key)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
                              <i className="fa-solid fa-trash" style={{ fontSize: 11 }} />
                              Remove from catalog
                            </button>
                            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>Its archive file stays where it is.</span>
                          </div>
                        </>
                      ) : (
                        <>
                          <Eyebrow style={eyebrowMargin(true)}>LOCATION</Eyebrow>
                          <div data-testid="catalog-location" style={{ font: '400 12px/1.5 var(--mono)', color: 'var(--text-muted)', overflowWrap: 'anywhere' }}>
                            {where}
                          </div>
                          <p style={caption}>
                            {source?.location ? 'The file this editor writes.' : 'Autowright keeps the catalog. Export its file from the Marketplace page to share it.'}
                          </p>
                          <Eyebrow style={eyebrowMargin(false)}>NAME</Eyebrow>
                          <input className="ad-input" value={name} onChange={(e) => setName(e.target.value)}
                            placeholder="My catalog" spellCheck={false} data-testid="catalog-name" style={inputStyle} />
                          <Eyebrow style={eyebrowMargin(false)}>DESCRIPTION</Eyebrow>
                          <input className="ad-input" value={description} onChange={(e) => setDescription(e.target.value)}
                            data-testid="catalog-description" style={inputStyle} />
                        </>
                      )}
                    </div>
                  </ScrollArea>
                )}
              </div>
            </div>
            {/* footer */}
            <div style={{
              flex: 'none', display: 'flex', alignItems: 'center', gap: 10, padding: '14px 22px',
              borderTop: '1px solid var(--hairline-dim)',
            }}>
              <div style={{ flex: 1, minWidth: 0, fontSize: 12.5, lineHeight: 1.5 }}>
                {error && <span data-testid="catalog-error" style={{ color: 'var(--red-text)' }}>{error}</span>}
              </div>
              <button className="ad-btn-ghost" data-testid="catalog-cancel" onClick={tryClose} disabled={busy}>Cancel</button>
              <button
                className="ad-btn-primary"
                data-testid="catalog-save"
                onClick={() => { void save() }}
                disabled={!canSave}
              >
                {busy ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <Spinner size={13} /> {creating ? 'Creating…' : 'Saving…'}
                  </span>
                ) : creating ? 'Create' : 'Save'}
              </button>
            </div>
            {picker && (
              <AddAutomationForm onAdd={addEntry} onClose={() => setPicker(false)} />
            )}
            {confirm && (
              <ConfirmModal
                title="Discard your catalog edits?"
                body="The changes you made to this catalog will be lost."
                confirmLabel="Discard"
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
