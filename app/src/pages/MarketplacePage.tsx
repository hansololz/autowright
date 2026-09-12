// Marketplace page (§22.3): every catalog in the §22.2 catalog table, each
// listing the entries of its copy; Install runs the ordinary §5.2 two-phase
// import. The page and its nav row render only while the §4.9 developerMode
// setting is on (§22 preview gate) - nothing else here is gated.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { ImportPreview, ImportSummary, MarketplaceEntry, MarketplaceSource } from '../types'
import {
  BtnGhost, ConfirmModal, EmptyLine, EmptyState, Eyebrow, HeaderActions, MetaChip,
  Modal, Notice, PageLoading, PageTitle, Spinner, Toggle,
} from '../ui'
import CatalogEditorModal, {
  KEPT_LABEL, caption, errLine, inputStyle, lastSegment, locationLabel, monoInput,
} from './CatalogEditor'
import ImportModal from './ImportModal'
import { ImportSummaryModal } from './AutomationsList'

// §22.1 example catalog - the MAKE YOUR OWN section's code box, verbatim from
// the spec so an author can copy it and fill it in.
const EXAMPLE_CATALOG = `format_version: 1
name: "Community automations"        # optional (max 200 chars): the source's title
description: "Automations I use."    # optional (max 1000 chars)
entries:                             # required list, may be empty, max 200 entries
  - title: "Manga chapter watcher"   # required, non-empty, max 120 chars
    description: "Checks the series you follow every morning at 8."  # optional, max 1000
    path: /Users/you/Automations/manga.autowright   # required: https URL or absolute local path
    image: /Users/you/Automations/manga.png         # optional: https URL or absolute local path`

/** §4.1 shared time labels - Today | Yesterday | weekday (2-6 days back) | the
 * locale date, with the clock time appended. A §22.4 source carries the raw §5
 * timestamp rather than a serialized label, so the label is built here. */
function relativeTime(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((midnight(new Date()) - midnight(at)) / 86_400_000)
  const date = days === 0 ? 'Today'
    : days === 1 ? 'Yesterday'
      : days > 1 && days < 7 ? at.toLocaleDateString(undefined, { weekday: 'long' })
        : at.toLocaleDateString()
  return `${date}, ${at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}

const locationIcon = (source: MarketplaceSource) =>
  source.kind === 'url' ? 'fa-link' : source.kind === 'file' ? 'fa-file-lines' : 'fa-box-archive'

// §22.3 preview images load by reference, on demand, through the
// authenticated §19 image route, and are shown as blob URLs cached in memory
// per (source, entry, refreshedAt) for the session - nothing on disk. The page
// revokes and drops every URL it made when it unmounts, which also re-arms the
// cache for the next mount (tests/setup.ts StrictMode).
const imageUrls = new Map<string, string>()

function EntryImage({ sourceId, index, refreshedAt, has }: {
  sourceId: string; index: number; refreshedAt: string | null; has: boolean
}) {
  const key = `${sourceId}:${index}:${refreshedAt ?? ''}`
  const [url, setUrl] = useState<string | null>(() => imageUrls.get(key) ?? null)
  useEffect(() => {
    if (!has) return
    const cached = imageUrls.get(key)
    if (cached) { setUrl(cached); return }
    let gone = false
    void (async () => {
      try {
        const blob = await api.marketplaceImage(sourceId, index)
        const made = URL.createObjectURL(blob)
        // A remount can have won the race - keep the first URL for the key and
        // drop this one, so the map never leaks a second URL per image.
        const first = imageUrls.get(key)
        if (first) {
          URL.revokeObjectURL(made)
          if (!gone) setUrl(first)
          return
        }
        imageUrls.set(key, made)
        if (!gone) setUrl(made)
      } catch {
        // §22.3: a failed fetch keeps the no-image icon.
      }
    })()
    return () => { gone = true }
  }, [key, has])
  return (
    <div style={{
      width: '100%', aspectRatio: '16 / 9', background: 'var(--bg-inset)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden',
    }}>
      {url
        ? <img src={url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
        : <i className="fa-regular fa-image" data-testid="no-image" style={{ fontSize: 20, color: 'var(--text-deco)' }} />}
    </div>
  )
}

const isYaml = (name: string) => /\.ya?ml$/i.test(name)

// §22.3 add marketplace modal - three ways in, two controls: a drop zone that
// doubles as the native file picker, and one field for a link or a path. Only
// a path or a link ever travels; the backend reads the file itself.
function AddMarketplaceModal({ onClose, onAdded }: {
  onClose: () => void
  onAdded: (source: MarketplaceSource) => void
}) {
  // §9 per-OS copy rule: the machine noun this modal names.
  const copy = usePlatformCopy()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState<false | 'field' | 'drop'>(false)
  const [over, setOver] = useState(false)
  const [error, setError] = useState<{ msg: string; src: 'field' | 'drop' } | null>(null)

  return (
    <Modal onClose={onClose} width={460}>
      {(close) => {
        // §22.4: a 422 (bad catalog) or 409 (already added) shows inline under
        // whichever control produced it.
        const add = async (body: { url?: string; path?: string }, src: 'field' | 'drop') => {
          if (busy) return
          setBusy(src); setError(null)
          try {
            const source = await api.marketplaceAdd(body)
            close()
            onAdded(source)
          } catch (e) { setError({ msg: (e as Error).message, src }); setBusy(false) }
        }
        const submitField = () => {
          const text = value.trim()
          if (!text) return
          void add(text.toLowerCase().startsWith('https://') ? { url: text } : { path: text }, 'field')
        }
        const chooseFile = async () => {
          if (busy) return
          // The pick itself can throw (no bridge, a volume that went away) -
          // that goes on the modal's own error line, never silently nowhere.
          let picked
          try {
            picked = await window.autowright?.openCatalog()
          } catch (e) {
            setError({ msg: (e as Error).message, src: 'drop' })
            return
          }
          if (!picked) return
          await add({ path: picked.path }, 'drop')
        }
        const drop = (e: React.DragEvent) => {
          e.preventDefault()
          setOver(false)
          if (busy) return
          const files = Array.from(e.dataTransfer.files)
          if (files.length !== 1 || !isYaml(files[0].name)) {
            setError({ msg: 'Drop one .yaml file.', src: 'drop' })
            return
          }
          let path = ''
          try { path = window.autowright?.pathForFile(files[0]) ?? '' } catch { path = '' }
          if (!path) { setError({ msg: 'Drop one .yaml file.', src: 'drop' }); return }
          void add({ path }, 'drop')
        }
        return (
          <>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>
              Add marketplace
            </h2>
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '6px 0 0' }}>
              Browse automations someone published - a marketplace catalog file on this {copy.machine}, or one at a link.
            </p>
            <div
              role="button"
              tabIndex={0}
              data-testid="marketplace-drop-zone"
              className="ad-card"
              onClick={() => { void chooseFile() }}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void chooseFile() } }}
              onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true) }}
              onDragLeave={() => setOver(false)}
              onDrop={drop}
              style={{
                marginTop: 18, minHeight: 96, display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', gap: 8, cursor: busy ? 'default' : 'pointer',
                border: `1px dashed ${over ? 'var(--accent)' : 'var(--hairline-strong, var(--hairline))'}`,
                background: over ? 'var(--bg-inset)' : undefined,
              }}
            >
              <i className="fa-solid fa-file-import" style={{ fontSize: 16, color: 'var(--text-faint)' }} />
              <span style={{ fontSize: 12.5, color: 'var(--text-muted)', textAlign: 'center', padding: '0 16px' }}>
                {busy === 'drop' ? 'Reading…' : `Drop a marketplace-catalog.yaml here, or click to choose one on this ${copy.machine}`}
              </span>
            </div>
            {error?.src === 'drop' && errLine(error.msg, 'marketplace-drop-error')}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '18px 0' }}>
              <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
              <Eyebrow>OR</Eyebrow>
              <div style={{ flex: 1, height: 1, background: 'var(--hairline)' }} />
            </div>
            <Eyebrow style={{ margin: '0 0 6px' }}>FROM A LINK OR FILE PATH</Eyebrow>
            <input
              className="ad-input"
              value={value}
              onChange={(e) => { setValue(e.target.value); setError(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') submitField() }}
              autoFocus
              spellCheck={false}
              placeholder="https://… or /path/to/marketplace-catalog.yaml"
              data-testid="marketplace-add-field"
              style={monoInput}
            />
            {error?.src === 'field' ? errLine(error.msg, 'marketplace-add-error') : (
              <p style={caption}>An https link, or the path of a catalog file on this {copy.machine}.</p>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <BtnGhost onClick={close} disabled={!!busy}>Cancel</BtnGhost>
              <button
                className="ad-btn-primary"
                data-testid="marketplace-add-submit"
                onClick={submitField}
                disabled={!value.trim() || !!busy}
              >
                {busy === 'field' ? 'Adding…' : 'Add'}
              </button>
            </div>
          </>
        )
      }}
    </Modal>
  )
}

// §22.3 catalog settings modal - the §22.2 table row's own columns: where
// Refresh reads from, whether the page shows it, whether it refreshes on its
// own. Saves through one PATCH; nothing is fetched.
function CatalogSettingsModal({ source, onClose, onSaved }: {
  source: MarketplaceSource
  onClose: () => void
  onSaved: () => void
}) {
  const [location, setLocation] = useState(source.location ?? '')
  const [shown, setShown] = useState(source.shown)
  const [autoRefresh, setAutoRefresh] = useState(source.autoRefresh)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const noLocation = !location.trim()
  const saved = useRef(false)
  return (
    <Modal onClose={() => (saved.current ? onSaved() : onClose())} width={460} ariaLabel="Catalog settings">
      {(close) => {
        const save = async () => {
          if (busy) return
          setBusy(true); setError(null)
          try {
            await api.marketplaceSettings(source.id, {
              location: location.trim(), shown, autoRefresh: noLocation ? false : autoRefresh,
            })
            saved.current = true
            close()
          } catch (e) { setError((e as Error).message); setBusy(false) }
        }
        return (
          <div data-testid="catalog-settings">
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>Catalog settings</h2>
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '4px 0 0' }}>{source.name}</p>
            <Eyebrow style={{ margin: '18px 0 6px' }}>LOCATION</Eyebrow>
            <input
              className="ad-input"
              value={location}
              onChange={(e) => { setLocation(e.target.value); setError(null) }}
              spellCheck={false}
              placeholder="https://… or /path/to/marketplace-catalog.yaml"
              data-testid="settings-location"
              style={monoInput}
            />
            <p style={caption}>Where Refresh reads this catalog from. Leave it empty to keep only the copy Autowright has.</p>
            <Eyebrow style={{ margin: '18px 0 8px' }}>SHOWN</Eyebrow>
            <label style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: 'var(--text)' }}>
              <Toggle on={shown} onChange={setShown} title="Show this marketplace on the page" />
              Show this marketplace on the page
            </label>
            <Eyebrow style={{ margin: '18px 0 8px' }}>AUTO REFRESH</Eyebrow>
            <label style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: noLocation ? 'var(--text-muted)' : 'var(--text)' }}>
              <Toggle on={!noLocation && autoRefresh} onChange={setAutoRefresh} disabled={noLocation} title="Refresh on its own" />
              Refresh on its own (at launch and every 6 hours)
            </label>
            {noLocation && <p style={caption}>Needs a location.</p>}
            {error && errLine(error, 'settings-error')}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 22 }}>
              <BtnGhost onClick={close} disabled={busy}>Cancel</BtnGhost>
              <button className="ad-btn-primary" data-testid="settings-save" onClick={() => { void save() }} disabled={busy}>
                {busy ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <Spinner size={13} /> Saving…
                  </span>
                ) : 'Save'}
              </button>
            </div>
          </div>
        )
      }}
    </Modal>
  )
}

export default function MarketplacePage() {
  // Per-field selectors (UI-GUIDE): a bare useStore() re-renders this page on
  // every store write anywhere - every toast, every log line of every execution.
  const showToast = useStore((s) => s.showToast)
  const refresh = useStore((s) => s.refresh)
  // §22.4 marketplace.changed - a §20 CLI change or an auto refresh shows
  // without a reload.
  const marketplaceVersion = useStore((s) => s.marketplaceVersion)
  // §9 per-OS copy rule: the machine noun this page names.
  const copy = usePlatformCopy()
  // null until the first §19 answer lands - the page shows PageLoading.
  const [sources, setSources] = useState<MarketplaceSource[] | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [refreshingAll, setRefreshingAll] = useState(false)
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const [removing, setRemoving] = useState<MarketplaceSource | null>(null)
  const [settings, setSettings] = useState<MarketplaceSource | null>(null)
  // §22.7: the catalog editor - a source to edit, or 'create' for a new one.
  const [editing, setEditing] = useState<MarketplaceSource | 'create' | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  // §22.3 install: the parked preview the import modal opens on.
  const [install, setInstall] = useState<
    { token: string; preview: ImportPreview; source: string } | null>(null)
  const [imported, setImported] = useState<
    { name: string; automationId: string; summary: ImportSummary } | null>(null)

  const load = async () => {
    try {
      const r = await api.marketplaceList()
      setSources(r.sources)
    } catch (e) {
      // A failed list leaves the page empty instead of spinning forever.
      setSources((prev) => prev ?? [])
      showToast((e as Error).message)
    }
  }

  // §22.3: on mount, and again whenever a marketplace.changed event lands.
  useEffect(() => { void load() }, [marketplaceVersion])

  // §22.3: every blob URL this page made dies with it.
  useEffect(() => () => {
    for (const made of imageUrls.values()) URL.revokeObjectURL(made)
    imageUrls.clear()
  }, [])

  const refreshAll = async () => {
    if (refreshingAll) return
    setRefreshingAll(true)
    try {
      const r = await api.marketplaceRefreshAll()
      setSources(r.sources)
    } catch (e) { showToast((e as Error).message) }
    setRefreshingAll(false)
  }

  // §22.2: a single refresh answers 200 either way - a failure rides on the
  // source's own `error`, which the Notice below the header shows.
  const refreshOne = async (id: string) => {
    if (refreshing) return
    setRefreshing(id)
    try {
      const source = await api.marketplaceRefresh(id)
      setSources((prev) => prev?.map((s) => (s.id === id ? source : s)) ?? null)
    } catch (e) { showToast((e as Error).message) }
    setRefreshing(null)
  }

  const remove = async (source: MarketplaceSource) => {
    setRemoving(null)
    try {
      await api.marketplaceRemove(source.id)
      await load()
    } catch (e) { showToast((e as Error).message) }
  }

  const startInstall = async (source: MarketplaceSource, entry: MarketplaceEntry) => {
    if (installing) return
    setInstalling(`${source.id}:${entry.index}`)
    try {
      const r = await api.marketplaceEntryPreview(source.id, entry.index)
      // §22.3: the source row reads "<marketplace name> · <entry title>".
      setInstall({ token: r.token, preview: r.preview, source: `${source.name} · ${entry.title}` })
    } catch (e) {
      // §22.4: a 422 (unreachable or invalid archive) toasts the reason.
      showToast((e as Error).message)
    }
    setInstalling(null)
  }

  // §5.2/§22.3: confirm landed the automation - the §9.1 summary modal follows.
  const importDone = async (r: { name: string; automationId: string; summary: ImportSummary }) => {
    setInstall(null)
    await refresh()
    setImported(r)
  }

  const addButton = (label: string) => (
    <button className="ad-btn-primary" data-testid="marketplace-add" onClick={() => setAddOpen(true)}>
      {label}
    </button>
  )
  // §22.7 create: the editor opens empty; the folder is chosen inside it.
  const createButton = (
    <button className="ad-btn-ghost" data-testid="marketplace-create" onClick={() => setEditing('create')}>
      Create catalog…
    </button>
  )
  const iconButton = (icon: string, label: string, onClick: () => void, extra: { danger?: boolean; busy?: boolean; disabled?: boolean } = {}) => (
    <button
      className={`ad-btn-ghost icon${extra.danger ? ' danger' : ''}`}
      onClick={onClick}
      disabled={extra.disabled}
      title={label}
      aria-label={label}
    >
      <i className={extra.busy ? 'fa-solid fa-spinner fa-spin' : `fa-solid ${icon}`} style={{ fontSize: 10.5 }} />
    </button>
  )

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
      <PageTitle
        right={(
          <HeaderActions>
            {createButton}
            {/* §22.3: only a catalog with a location can refresh. */}
            {sources && sources.some((s) => s.location !== null) && (
              <button
                className="ad-btn-ghost"
                data-testid="marketplace-refresh-all"
                onClick={() => { void refreshAll() }}
                disabled={refreshingAll}
              >
                {refreshingAll ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <Spinner size={13} /> Refreshing…
                  </span>
                ) : 'Refresh all'}
              </button>
            )}
            {addButton('Add marketplace…')}
          </HeaderActions>
        )}
      >
        Marketplace
      </PageTitle>
      {sources === null ? <PageLoading /> : sources.length === 0 ? (
        <>
          <EmptyState
            text={(
              <>
                <span style={{ display: 'block', fontSize: 13.5, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
                  No marketplaces yet
                </span>
                Add a marketplace catalog someone shared - drop the file, type its path, or paste its link - to browse the automations it lists.
              </>
            )}
            cta={addButton('Add marketplace…')}
          />
          {/* §22.3: the shape of a catalog, shown only while there is nothing
              to browse - once a catalog exists the user has seen it. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 28 }}>
            <Eyebrow>MAKE YOUR OWN</Eyebrow>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              Create a catalog here and add automations from this {copy.machine}. Or write the YAML by hand - list each archive by its full path on this {copy.machine} or an https link, save it as marketplace-catalog.yaml, then add it here.
            </p>
            <div>{createButton}</div>
            <div className="ad-card" style={{ padding: 14, overflow: 'hidden' }}>
              <pre style={{
                margin: 0, font: `400 12px/1.7 var(--mono)`, color: 'var(--text-2)',
                overflowX: 'auto', userSelect: 'text',
              }}>
                {EXAMPLE_CATALOG}
              </pre>
            </div>
          </div>
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
          {sources.map((s) => (
            <div key={s.id} data-testid="marketplace-source" data-hidden={s.shown ? undefined : 'true'} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{s.name}</span>
                  <span title={s.location ?? KEPT_LABEL} style={{ display: 'inline-flex', minWidth: 0 }}>
                    <MetaChip>
                      <i className={`fa-solid ${locationIcon(s)}`} style={{ fontSize: 10 }} />
                      {locationLabel(s)}
                    </MetaChip>
                  </span>
                  {!s.shown && (
                    <span data-testid="marketplace-hidden-chip" style={{ display: 'inline-flex' }}>
                      <MetaChip>
                        <i className="fa-solid fa-eye-slash" style={{ fontSize: 10 }} />
                        Hidden
                      </MetaChip>
                    </span>
                  )}
                  {/* §22.3: a catalog with a location says when it was last
                      read; one kept by the app says when it was added. */}
                  {s.location !== null ? s.refreshedAt && (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Refreshed {relativeTime(s.refreshedAt)}
                    </span>
                  ) : s.addedAt && (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Added {relativeTime(s.addedAt)}
                    </span>
                  )}
                </div>
                {/* §22.7: a path or null location is on this machine, so it can be edited. */}
                {s.kind !== 'url' && iconButton('fa-pen', 'Edit catalog', () => setEditing(s))}
                {s.location !== null && iconButton('fa-rotate', 'Refresh', () => { void refreshOne(s.id) }, {
                  busy: refreshing === s.id || refreshingAll, disabled: refreshing === s.id || refreshingAll,
                })}
                {iconButton('fa-gear', 'Catalog settings', () => setSettings(s))}
                {iconButton('fa-trash', 'Remove', () => setRemoving(s), { danger: true })}
              </div>
              {s.error && (
                // §22.2: a failed refresh keeps the last good copy beside the
                // reason; a catalog with nothing cached has no copy to show.
                <Notice tone="amber">
                  {!s.cached
                    ? `Couldn't load: ${s.error}.`
                    : `Couldn't refresh: ${s.error}. Showing the last copy.`}
                </Notice>
              )}
              {s.shown && s.description && (
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                  {s.description}
                </p>
              )}
              {s.shown && (s.entries.length === 0 ? (
                <div className="ad-card">
                  <EmptyLine>This marketplace lists no automations yet.</EmptyLine>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))', gap: 14 }}>
                  {s.entries.map((e) => (
                    <div
                      key={e.index}
                      className="ad-card"
                      data-testid="marketplace-entry"
                      style={{ padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
                    >
                      <EntryImage sourceId={s.id} index={e.index} refreshedAt={s.refreshedAt} has={e.image !== null} />
                      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 7, flex: 1 }}>
                        <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{e.title}</div>
                        {e.description && (
                          <p style={{
                            margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)',
                            display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                          }}>
                            {e.description}
                          </p>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'auto', paddingTop: 4 }}>
                          <button
                            className="ad-btn-primary"
                            data-testid="marketplace-install"
                            onClick={() => { void startInstall(s, e) }}
                            disabled={!!installing}
                          >
                            {installing === `${s.id}:${e.index}` ? (
                              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                                <Spinner size={13} /> Installing…
                              </span>
                            ) : 'Install'}
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {addOpen && (
        <AddMarketplaceModal
          onClose={() => setAddOpen(false)}
          onAdded={(source) => {
            setAddOpen(false)
            void load()
            showToast(`Added ${source.name}.`)
          }}
        />
      )}
      {settings && (
        <CatalogSettingsModal
          source={settings}
          onClose={() => setSettings(null)}
          onSaved={() => { setSettings(null); void load() }}
        />
      )}
      {editing && (
        <CatalogEditorModal
          source={editing === 'create' ? null : editing}
          sources={sources ?? []}
          onClose={() => setEditing(null)}
          onSaved={(source) => {
            const created = editing === 'create'
            setEditing(null)
            void load()
            showToast(created ? `Created ${source.name}.` : `Saved ${source.name}.`)
          }}
        />
      )}
      {removing && (
        <ConfirmModal
          title={`Remove “${removing.name}”?`}
          body="Automations you already installed from it stay, and so does every archive file it lists. You can add the marketplace again later."
          confirmLabel="Remove"
          danger
          onConfirm={() => { void remove(removing) }}
          onCancel={() => setRemoving(null)}
        />
      )}
      {install && (
        // §22.3: the §9.1 import modal opened straight on its preview step.
        <ImportModal
          initial={{ ...install, srcKind: 'marketplace' }}
          onDone={(r) => { void importDone(r) }}
          onClose={() => setInstall(null)}
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
    </div>
  )
}
