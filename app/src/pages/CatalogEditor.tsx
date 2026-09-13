// §22.7 catalog editor: create and edit share one two-column form on the §9.2
// step-script modal's frame. Left, the catalog navigator (a details row, one
// row per entry, a pinned Add automation… button); right, the viewed form.
// The stacked add-automation picker offers three sources (this machine, another
// catalog, an .autowright file) and ends in a title-and-description step
// before the entry joins the working catalog. The editor never exports on the
// user's behalf: an automation from this machine is exported through the
// native save dialog at pick time, and the catalog lists the file where the
// user put it.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { MarketplaceCatalogSaveEntry, MarketplaceSource } from '../types'
import {
  BtnGhost, ConfirmModal, EmptyLine, Eyebrow, MenuItemRow, Modal, PageLoading, ScrollArea, Spinner,
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

// §22.7 working-catalog entry: a reference as written, whichever way it came
// in. `pickedFile` remembers the path a This Mac export or A FILE landed on,
// so an unedited pick travels as `archiveFile` (checked as a real archive by
// the backend) while an edited one travels as `path`.
export interface EditorEntry {
  key: number
  title: string
  description: string
  image: string
  path: string
  pickedFile?: string
}

// A picker pick on its way to the details step: what the entry will carry,
// plus the line naming where it came from.
interface Pick {
  title: string
  description: string
  path: string
  image: string
  pickedFile: boolean
  label: string
  mono: boolean
}

const toSaveEntry = ({ title, description, image, path, pickedFile }: EditorEntry): MarketplaceCatalogSaveEntry => ({
  title, description,
  ...(image ? { image } : {}),
  ...(pickedFile && path === pickedFile ? { archiveFile: path } : { path }),
})

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

/** §22.7 add-automation picker: three tabs (this machine, another catalog, a
 * file), then a details step that fixes the title and description before the
 * entry joins the working catalog. */
function AddAutomationPicker({ sources, workingId, onAdd, onClose }: {
  sources: MarketplaceSource[]
  workingId: string | null
  onAdd: (entry: Omit<EditorEntry, 'key'>) => void
  onClose: () => void
}) {
  const copy = usePlatformCopy()
  const automations = useStore((s) => s.automations)
  const [tab, setTab] = useState<'mac' | 'catalog' | 'file'>('mac')
  const [query, setQuery] = useState('')
  const [pick, setPick] = useState<Pick | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [exporting, setExporting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim().toLowerCase()

  const choose = (p: Pick) => {
    setPick(p); setTitle(p.title); setDescription(p.description); setError(null)
  }
  // §22.7 THIS MAC: export the automation now - without parameter values, a
  // marketplace archive is for other people - through the same native save
  // dialog as the §9.2 Export…, so the user chooses where the file lives.
  const exportAutomation = async (a: { id: string; name: string; description: string }) => {
    setExporting(a.name); setError(null)
    try {
      const data = await api.exportAutomation(a.id, false)
      const safe = a.name.replace(/[/\\:*?"<>|]+/g, ' ').trim() || 'automation'
      const path = await window.autowright?.saveFile(`${safe}.autowright`, data)
      if (path) {
        // §22.1: the catalog can only name an .autowright file - a save dialog
        // talked into any other name says so here, not at Save.
        if (!archiveRefOk(path)) { setError(ARCHIVE_BAD); return }
        choose({ title: a.name, description: a.description, path, image: '', pickedFile: true, label: path, mono: true })
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setExporting(null)
    }
  }
  const chooseFile = async () => {
    let picked
    try {
      picked = await window.autowright?.openArchivePath()
    } catch (e) { setError((e as Error).message); return }
    if (picked) {
      if (!archiveRefOk(picked.path)) { setError(ARCHIVE_BAD); return }
      choose({
        title: lastSegment(picked.path).replace(/\.autowright$/i, ''), description: '',
        path: picked.path, image: '', pickedFile: true, label: picked.path, mono: true,
      })
    }
  }
  const shownAutomations = automations.filter((a) => a.name.toLowerCase().includes(q))
  // §22.7 A CATALOG: the other catalogs' entries, grouped; a catalog matches
  // whole by name, otherwise entry by entry on the title.
  const otherCatalogs = sources.filter((s) => s.id !== workingId && s.entries.length > 0)
  const shownCatalogs = otherCatalogs
    .map((s) => ({
      source: s,
      entries: s.name.toLowerCase().includes(q) ? s.entries
        : s.entries.filter((e) => e.title.toLowerCase().includes(q)),
    }))
    .filter((g) => g.entries.length > 0)

  const tabs: { id: typeof tab; label: string }[] = [
    { id: 'mac', label: `This ${copy.machine}` }, { id: 'catalog', label: 'A catalog' }, { id: 'file', label: 'A file' },
  ]
  const canAdd = pick !== null && title.trim() !== ''

  return (
    <Modal onClose={onClose} width={520} zIndex={80} ariaLabel="Add automation">
      {(close) => {
        const add = () => {
          if (!pick || !canAdd) return
          onAdd({
            title: title.trim(), description: description.trim(), image: pick.image, path: pick.path,
            ...(pick.pickedFile ? { pickedFile: pick.path } : {}),
          })
          close()
        }
        const onEnter = (e: React.KeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); add() } }
        return (
          <div data-testid="catalog-picker">
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>Add automation</h2>
            {pick ? (
              <>
                <p data-testid="catalog-picker-source" title={pick.label} style={{
                  margin: '6px 0 0', fontSize: 12, color: 'var(--text-muted)',
                  ...(pick.mono ? { font: '400 12px var(--mono)' } : {}),
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {pick.label}
                </p>
                <Eyebrow style={{ margin: '18px 0 6px' }}>TITLE</Eyebrow>
                <input className="ad-input" value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={onEnter}
                  autoFocus spellCheck={false} data-testid="catalog-picker-title" style={inputStyle} />
                <Eyebrow style={{ margin: '14px 0 6px' }}>DESCRIPTION</Eyebrow>
                <input className="ad-input" value={description} onChange={(e) => setDescription(e.target.value)} onKeyDown={onEnter}
                  data-testid="catalog-picker-description" style={inputStyle} />
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
                  <button className="ad-btn-ghost" data-testid="catalog-picker-back" onClick={() => setPick(null)}>Back</button>
                  <button className="ad-btn-primary" data-testid="catalog-picker-add" onClick={add} disabled={!canAdd}>Add</button>
                </div>
              </>
            ) : (
              <>
                <div style={{ display: 'flex', gap: 6, margin: '12px 0 10px' }}>
                  {tabs.map((t) => (
                    <button key={t.id} className="ad-btn-tab" aria-pressed={tab === t.id}
                      data-testid={`catalog-picker-tab-${t.id}`} onClick={() => { setTab(t.id); setError(null) }}>
                      {t.label}
                    </button>
                  ))}
                </div>
                {tab !== 'file' && (
                  <input
                    className="ad-input"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    autoFocus
                    spellCheck={false}
                    placeholder={tab === 'mac' ? 'Search automations' : 'Search catalogs'}
                    data-testid="catalog-picker-search"
                    style={{ ...inputStyle, margin: '0 0 10px' }}
                  />
                )}
                {tab === 'mac' && (
                  <div className="ad-card" style={{ padding: 4, maxHeight: 320, overflowY: 'auto' }}>
                    {exporting !== null ? (
                      <EmptyLine testId="catalog-picker-exporting" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Spinner size={13} /> Exporting {exporting}…
                      </EmptyLine>
                    ) : shownAutomations.length === 0 ? (
                      <EmptyLine>{automations.length === 0 ? 'No automations yet.' : 'No automations match.'}</EmptyLine>
                    ) : shownAutomations.map((a) => (
                      <MenuItemRow
                        key={a.id}
                        title={a.name}
                        sub={a.description || undefined}
                        testId="catalog-picker-row"
                        onPick={() => { void exportAutomation({ id: a.id, name: a.name, description: a.description || '' }) }}
                      />
                    ))}
                  </div>
                )}
                {tab === 'catalog' && (
                  <div className="ad-card" style={{ padding: 4, maxHeight: 320, overflowY: 'auto' }}>
                    {shownCatalogs.length === 0 ? (
                      <EmptyLine>
                        {otherCatalogs.length === 0
                          ? (workingId ? 'No other catalogs list automations yet.' : 'No catalogs list automations yet.')
                          : 'No automations match.'}
                      </EmptyLine>
                    ) : shownCatalogs.map(({ source, entries }) => (
                      <React.Fragment key={source.id}>
                        <MenuItemRow header title={source.name} sub={locationLabel(source)} testId="catalog-picker-catalog" />
                        {entries.map((e) => (
                          <MenuItemRow
                            key={e.index}
                            title={e.title}
                            sub={e.description || undefined}
                            testId="catalog-picker-row"
                            onPick={() => choose({
                              title: e.title, description: e.description, path: e.archive, image: e.image ?? '',
                              pickedFile: false, label: `${e.title} · ${source.name}`, mono: false,
                            })}
                          />
                        ))}
                      </React.Fragment>
                    ))}
                  </div>
                )}
                {tab === 'file' && (
                  <>
                    <button className="ad-btn-dashed" data-testid="catalog-add-file" onClick={() => { void chooseFile() }}
                      style={{ width: '100%', alignSelf: 'stretch', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, padding: '14px 15px' }}>
                      <i className="fa-solid fa-file-import" style={{ fontSize: 11, color: 'var(--text-faint)' }} />
                      Choose an .autowright file…
                    </button>
                    <p style={caption}>The file is listed where it is, never copied.</p>
                  </>
                )}
                {error && errLine(error, 'catalog-picker-error')}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                  <BtnGhost onClick={close}>Cancel</BtnGhost>
                </div>
              </>
            )}
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
 * content on Create - the new catalog is kept by Autowright. Every entry is a reference
 * as written - the editor never exports on save (a This Mac pick exports
 * through the save dialog at pick time, in the picker). */
export default function CatalogEditorModal({ source, sources, onClose, onSaved }: {
  /** the source to edit, or null to create a new catalog */
  source: MarketplaceSource | null
  /** every catalog the page knows - the picker's A CATALOG tab */
  sources: MarketplaceSource[]
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
      const problem = !r.title.trim() ? TITLE_BLANK
        : !archiveRefOk(r.path) ? ARCHIVE_BAD
          : r.image && !imageRefOk(r.image) ? IMAGE_BAD : null
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
                            spellCheck={false} placeholder="https://…/name.autowright or /path/to/name.autowright"
                            data-testid="catalog-entry-path" style={monoInput} />
                          <p style={caption}>An https link, or the archive's absolute path on this {copy.machine}.</p>
                          <Eyebrow style={eyebrowMargin(false)}>IMAGE</Eyebrow>
                          <input className="ad-input" value={entry.image} onChange={(e) => update(entry.key, { image: e.target.value })}
                            spellCheck={false} placeholder="Optional: https://… or /path/to/preview.png"
                            data-testid="catalog-entry-image" style={monoInput} />
                          <p style={caption}>A preview for the marketplace page: an https link, or an absolute path to a .png, .jpg, .jpeg, .webp, or .gif.</p>
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
              <AddAutomationPicker sources={sources} workingId={source?.id ?? null} onAdd={addEntry} onClose={() => setPicker(false)} />
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
