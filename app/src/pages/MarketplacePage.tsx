// Marketplace page (§22.3): every source the user added, each listing the
// entries of its catalog; Install runs the ordinary §5.2 two-phase import.
// The page and its nav row render only while the §4.9 developerMode setting is
// on (§22 preview gate) - nothing else here is gated.
import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { usePlatformCopy } from '../platformCopy'
import { useStore } from '../store'
import type { ImportPreview, ImportSummary, MarketplaceEntry, MarketplaceSource } from '../types'
import {
  BtnGhost, ConfirmModal, EmptyLine, EmptyState, Eyebrow, HeaderActions, MetaChip,
  Modal, Notice, PageLoading, PageTitle, Spinner,
} from '../ui'
import ImportModal from './ImportModal'
import { ImportSummaryModal } from './AutomationsList'

// §22.1 example catalog - the MAKE YOUR OWN section's code box, verbatim from
// the spec so an author can copy it and fill it in.
const EXAMPLE_CATALOG = `format_version: 1
name: "Community automations"        # optional (max 80 chars): the source's title
description: "Automations I use."    # optional (max 500 chars)
url: https://example.com/shelf/marketplace-catalog.yaml  # optional: where this file is published, so Refresh can fetch it
entries:                             # required list, may be empty, max 200 entries
  - title: "Manga chapter watcher"   # required, non-empty, max 120 chars
    description: "Checks the series you follow every morning at 8."  # optional, max 1000
    path: automations/manga.autowright   # required: https URL or a relative reference
    image: images/manga.png              # optional: https URL or a relative reference`

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

/** §22.3 origin chip text: a link source shows its host, a file source the
 * catalog's file name; the full origin rides in the `title` attribute. */
function originLabel(source: MarketplaceSource): string {
  if (source.kind === 'url') {
    try { return new URL(source.origin).hostname } catch { return source.origin }
  }
  return source.origin.split(/[\\/]/).pop() || source.origin
}

// §22.3 preview images ride the authenticated §19 route, so they are fetched as
// bytes and shown as blob URLs - cached per (source, entry, refreshedAt) for
// the session. The page revokes and drops every URL it made when it unmounts,
// which also re-arms the cache for the next mount (tests/setup.ts StrictMode).
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
        // §22.3: a failed image fetch simply leaves the placeholder.
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
        : <i className="fa-solid fa-bolt" style={{ fontSize: 20, color: 'var(--text-deco)' }} />}
    </div>
  )
}

// §22.3 add marketplace modal - the §9.1 import modal's input step, retitled:
// an https link to a catalog, or a catalog file picked through the §3
// open-catalog IPC (only the path travels; the backend reads the file).
function AddMarketplaceModal({ onClose, onAdded }: {
  onClose: () => void
  onAdded: (source: MarketplaceSource) => void
}) {
  // §9 per-OS copy rule: the machine noun this modal names.
  const copy = usePlatformCopy()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<false | 'url' | 'file'>(false)
  const [error, setError] = useState<{ msg: string; src: 'url' | 'file' } | null>(null)

  const errLine = (msg: string) => (
    <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--red-text)', margin: '8px 0 0' }}>
      {msg}
    </p>
  )

  return (
    <Modal onClose={onClose} width={460}>
      {(close) => {
        // §22.4: a 422 (bad catalog) or 409 (already added) shows inline under
        // whichever control produced it.
        const add = async (body: { url?: string; path?: string }, src: 'url' | 'file') => {
          if (busy) return
          setBusy(src); setError(null)
          try {
            const source = await api.marketplaceAdd(body)
            close()
            onAdded(source)
          } catch (e) { setError({ msg: (e as Error).message, src }); setBusy(false) }
        }
        const chooseFile = async () => {
          if (busy) return
          // The pick itself can throw (no bridge, a volume that went away) -
          // that goes on the modal's own error line, never silently nowhere.
          let picked
          try {
            picked = await window.autowright?.openCatalog()
          } catch (e) {
            setError({ msg: (e as Error).message, src: 'file' })
            return
          }
          if (!picked) return
          await add({ path: picked.path }, 'file')
        }
        return (
          <>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text)' }}>
              Add marketplace
            </h2>
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', margin: '6px 0 0' }}>
              Browse automations someone published - from a link, or a marketplace catalog on this {copy.machine}.
            </p>
            <Eyebrow style={{ margin: '18px 0 6px' }}>FROM A LINK</Eyebrow>
            <input
              className="ad-input"
              value={url}
              onChange={(e) => { setUrl(e.target.value); setError(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') void add({ url: url.trim() }, 'url') }}
              autoFocus
              spellCheck={false}
              placeholder="https://… link to a marketplace-catalog.yaml file"
              style={{
                width: '100%', boxSizing: 'border-box', color: 'var(--text)',
                font: `400 12.5px var(--mono)`, padding: '9px 11px',
              }}
            />
            {error?.src === 'url' ? errLine(error.msg) : (
              <p style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-faint)', margin: '7px 0 0' }}>
                An https link to a marketplace-catalog.yaml file.
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
              {busy === 'file' ? 'Reading…' : `Choose a marketplace catalog on this ${copy.machine}…`}
            </button>
            {error?.src === 'file' && errLine(error.msg)}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <BtnGhost onClick={close} disabled={!!busy}>Cancel</BtnGhost>
              <button
                className="ad-btn-primary"
                onClick={() => { void add({ url: url.trim() }, 'url') }}
                disabled={!url.trim() || !!busy}
              >
                {busy === 'url' ? 'Adding…' : 'Add'}
              </button>
            </div>
          </>
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
  // §22.4 marketplace.changed - a §20 CLI change shows without a reload.
  const marketplaceVersion = useStore((s) => s.marketplaceVersion)
  // §9 per-OS copy rule: the machine noun this page names.
  const copy = usePlatformCopy()
  // null until the first §19 answer lands - the page shows PageLoading.
  const [sources, setSources] = useState<MarketplaceSource[] | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [refreshingAll, setRefreshingAll] = useState(false)
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const [removing, setRemoving] = useState<MarketplaceSource | null>(null)
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

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
      <PageTitle
        right={(
          <HeaderActions>
            {/* §22.3: only a source whose catalog declares a url can refresh. */}
            {sources && sources.some((s) => s.url) && (
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
                Add a marketplace catalog someone shared - from a link, or a file on this {copy.machine} - to browse the automations it lists.
              </>
            )}
            cta={addButton('Add marketplace…')}
          />
          {/* §22.3: the shape of a catalog, shown only while there is nothing
              to browse - once a source exists the user has seen it. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 28 }}>
            <Eyebrow>MAKE YOUR OWN</Eyebrow>
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              A marketplace catalog is a YAML file that lists .autowright archives. Save it as marketplace-catalog.yaml next to the archives it points to, then add it here. Put the link you publish it at in <code>url</code> so Refresh can fetch what you add later.
            </p>
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
            <div key={s.id} data-testid="marketplace-source" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>{s.name}</span>
                  <span title={s.origin} style={{ display: 'inline-flex', minWidth: 0 }}>
                    <MetaChip>
                      <i
                        className={`fa-solid ${s.kind === 'url' ? 'fa-link' : 'fa-file-lines'}`}
                        style={{ fontSize: 10 }}
                      />
                      {originLabel(s)}
                    </MetaChip>
                  </span>
                  {/* §22.3: a refreshable source says when it was last fetched;
                      a one-time download (no url) says when it was added. */}
                  {s.url ? s.refreshedAt && (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Refreshed {relativeTime(s.refreshedAt)}
                    </span>
                  ) : s.addedAt && (
                    <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      Added {relativeTime(s.addedAt)}
                    </span>
                  )}
                </div>
                {s.url && (
                  <button
                    className="ad-btn-ghost icon"
                    onClick={() => { void refreshOne(s.id) }}
                    disabled={refreshing === s.id || refreshingAll}
                    title="Refresh"
                    aria-label="Refresh"
                  >
                    <i
                      className={refreshing === s.id || refreshingAll
                        ? 'fa-solid fa-spinner fa-spin'
                        : 'fa-solid fa-rotate'}
                      style={{ fontSize: 11 }}
                    />
                  </button>
                )}
                <button
                  className="ad-btn-ghost icon danger"
                  onClick={() => setRemoving(s)}
                  title="Remove"
                  aria-label="Remove"
                >
                  <i className="fa-solid fa-trash" style={{ fontSize: 10 }} />
                </button>
              </div>
              {s.error && (
                // §22.2: a failed refresh keeps the last good copy beside the
                // reason; a source with nothing cached has no copy to show.
                <Notice tone="amber">
                  {!s.cached
                    ? `Couldn't load: ${s.error}.`
                    : `Couldn't refresh: ${s.error}. Showing the last copy.`}
                </Notice>
              )}
              {s.description && (
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                  {s.description}
                </p>
              )}
              {s.entries.length === 0 ? (
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
                      <EntryImage sourceId={s.id} index={e.index} refreshedAt={s.refreshedAt} has={e.image} />
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
              )}
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
      {removing && (
        <ConfirmModal
          title={`Remove “${removing.name}”?`}
          body="Automations you already installed from it stay. You can add the marketplace again later."
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
