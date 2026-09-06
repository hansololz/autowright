// Executions list (§7): every execution across all automations, stacked as
// Executing and Queued (§6 firing queue) sections above Finished. The store
// holds only the §19 window (live rows plus the newest finished page); the
// §7 filter modal — statuses, automations, a started-time range — and the
// pager bring deeper history in via GET /executions. Every filter is one
// predicate applied server-side and to the window's rows alike.
import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { Badge, BtnGhost, EmptyNotice, Eyebrow, HeaderActions, MetaChip, PageTitle, PULSE, waitedLabel } from '../ui'
import type { Execution } from '../types'
import FilterModal, {
  DEFAULT_FILTERS, LIVE_STATUSES, activeCount, filtersActive, resolveRange, statusLabel, timeLabel,
} from './FilterModal'
import type { ExecutionFilters } from './FilterModal'

const GRID = '2fr 1.1fr .8fr .6fr 1fr'

function Row({ e, onOpen, queued }: { e: Execution; onOpen: () => void; queued?: boolean }) {
  return (
    <button
      className="ad-btn-bare ad-hover-row ad-focus-inset"
      data-testid="execution-row"
      onClick={onOpen}
      style={{
        display: 'grid', gridTemplateColumns: GRID, gap: 10, padding: '9px 18px',
        borderBottom: '1px solid var(--hairline-dim)', alignItems: 'center', cursor: 'pointer',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {e.automationName}
          </span>
          {e.automationDeleted && (
            <span style={{ fontSize: 12, color: 'var(--text-faint)', flex: 'none' }}>(deleted)</span>
          )}
        </div>
        <div style={{
          fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)', marginTop: 2,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {/* §7: short id — first 8 chars, same as the detail page's RECENT EXECUTIONS rows */}
          {e.id.slice(0, 8)}
        </div>
      </div>
      <div>
        <Badge
          status={e.status}
          style={e.status === 'executing' ? { animation: PULSE } : undefined}
        />
      </div>
      {/* §4.5: message-triggered rows read "Discord · Dave · v3"; a test row's
        * trigger and ver labels are both "Test" — print it once (§7). */}
      <span style={{ fontSize: 12, color: 'var(--text-muted)', minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {e.trigger + (e.triggerSender ? ' · ' + e.triggerSender : '') + (e.versionLabel && e.versionLabel !== e.trigger ? ' · ' + e.versionLabel : '')}
      </span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 11.5, color: 'var(--text-muted)' }}>
        {/* A queued row has no duration — it hasn't started (§7). */}
        {queued ? waitedLabel(Date.now() - (e.queuedMs || e.startedMs)) : e.duration}
      </span>
      {/* Admission stamps started_at = queued_at, and promotion re-stamps it —
        * a row still in this section was never promoted, so `started` is
        * exactly when it was queued. */}
      <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{e.started}</span>
    </button>
  )
}

function Table({ rows, go, queued }: {
  rows: Execution[]; go: (page: 'execution', ids: { executionId: string }) => void; queued?: boolean
}) {
  return (
    <div className="ad-card" style={{ overflow: 'hidden' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: GRID, gap: 10, padding: '10px 18px',
        borderBottom: '1px solid var(--hairline)',
      }}>
        <Eyebrow>AUTOMATION</Eyebrow>
        <Eyebrow>STATUS</Eyebrow>
        <Eyebrow>TRIGGER</Eyebrow>
        <Eyebrow>{queued ? 'QUEUED FOR' : 'DURATION'}</Eyebrow>
        <Eyebrow>{queued ? 'QUEUED AT' : 'STARTED'}</Eyebrow>
      </div>
      {rows.map((e) => (
        <Row key={e.id} e={e} queued={queued} onOpen={() => go('execution', { executionId: e.id })} />
      ))}
    </div>
  )
}

// §14 section rhythm: the eyebrow sits 10 px above its table, and
// eyebrow-labelled sections are 26 px apart.
const sectionLabel: React.CSSProperties = { marginBottom: 10 }

// §7 Finished paging: retention defaults to 90 days and `keepForever` turns
// cleanup off entirely, so history is unbounded — it moves in pages of 50,
// the same size as the §19 /state finished window.
const PAGE = 50

// §7 canonical order: startedMs desc, id asc on ties — the §19 keyset order,
// which is what lets fetched pages line up with the live window.
const byCanonicalOrder = (a: Execution, b: Execution) =>
  b.startedMs - a.startedMs || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

export default function ExecutionsList() {
  const executions = useStore((s) => s.executions)
  const executionsTotal = useStore((s) => s.executionsTotal)
  const automations = useStore((s) => s.automations)
  const showToast = useStore((s) => s.showToast)
  const go = useStore((s) => s.go)
  // §7 filters: view state like the pager — reset on unmount, never stored.
  const [filters, setFilters] = useState<ExecutionFilters>(DEFAULT_FILTERS)
  const [modalOpen, setModalOpen] = useState(false)
  const filtered = filtersActive(filters)
  // §7: one predicate, the same on the server and over the window's rows —
  // status ∈ selection (when any), automation id ∈ selection (when any),
  // startedMs within the inclusive bounds. Presets resolve against the clock
  // at every use.
  const range = resolveRange(filters.time, Date.now())
  const selectedStatuses = new Set<string>(filters.statuses)
  const selectedIds = new Set(filters.automations)
  const matches = (e: Execution) =>
    (selectedStatuses.size === 0 || selectedStatuses.has(e.status)) &&
    (selectedIds.size === 0 || (e.automationId !== null && selectedIds.has(e.automationId))) &&
    (range.from === undefined || e.startedMs >= range.from) &&
    (range.to === undefined || e.startedMs <= range.to)
  // §7: the Finished section exists only while the status selection is empty
  // or names a terminal status — live statuses alone render no table and
  // fetch nothing.
  const terminalSelected = filters.statuses.filter((s) => !LIVE_STATUSES.includes(s))
  const hasFinished = filters.statuses.length === 0 || terminalSelected.length > 0
  // §19 query for every fetch: each selected terminal status (`finished` =
  // every terminal status when none is selected) plus the other dimensions.
  const query = {
    status: terminalSelected.length > 0 ? terminalSelected : 'finished',
    ...(filters.automations.length > 0 ? { automation: filters.automations } : {}),
    ...(range.from !== undefined ? { startedFromMs: range.from } : {}),
    ...(range.to !== undefined ? { startedToMs: range.to } : {}),
  }
  const filterKey = JSON.stringify(filters)
  // §7 fetched pages and the page number: view state only — reset on unmount
  // and on every filter change. `serverTotal` is the current filter's match
  // count from the last fetch (null until one lands).
  const [fetched, setFetched] = useState<Execution[]>([])
  const [serverTotal, setServerTotal] = useState<number | null>(null)
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const fetchSeq = useRef(0)

  // §7: the filter applies to every section — the live rows filter
  // client-side (the window always holds every live row, so they never fetch).
  const executing = executions
    .filter((e) => e.status === 'executing' && matches(e))
    .sort((a, b) => b.startedMs - a.startedMs)
  // §6 firing queue: oldest wait first — the drain order, so the next one to
  // run reads top.
  const queued = executions
    .filter((e) => e.status === 'queued' && matches(e))
    .sort((a, b) => (a.queuedMs || a.startedMs) - (b.queuedMs || b.startedMs))

  const matchesFinished = (e: Execution) =>
    e.status !== 'queued' && e.status !== 'executing' && hasFinished && matches(e)
  // §7 merge: fetched pages join the live window, window wins on an id both
  // hold (it is fresher — events land there), in the canonical order.
  const windowFinished = executions.filter(matchesFinished)
  const windowIds = new Set(windowFinished.map((e) => e.id))
  const finished = [...windowFinished, ...fetched.filter((e) => !windowIds.has(e.id) && matchesFinished(e))]
    .sort(byCanonicalOrder)

  // §7: a filter fetches its own first Finished page — the window may hold
  // only a slice of the matches (it shows its matching rows while this is in
  // flight, but never the empty card — that means "the server answered
  // empty", not "the answer hasn't arrived"). Unfiltered, the window is the
  // first page and nothing fetches; a live-only status selection has no
  // Finished section to fetch for.
  const [firstFetchDone, setFirstFetchDone] = useState(true)
  useEffect(() => {
    const n = ++fetchSeq.current
    setFetched([])
    setServerTotal(null)
    setPage(0)
    if (!filtered || !hasFinished) {
      setFirstFetchDone(true)
      return
    }
    setFirstFetchDone(false)
    void api.listExecutions({ ...query, limit: PAGE }).then((r) => {
      if (n !== fetchSeq.current) return
      setFetched(r.executions)
      setServerTotal(r.total)
    }, (err: Error) => { if (n === fetchSeq.current) showToast(err.message) })
      .finally(() => { if (n === fetchSeq.current) setFirstFetchDone(true) })
  }, [filterKey])

  // §7 absorption: a /state refresh replaces the window wholesale, and new
  // finishes push old rows out of it — a row that leaves the window
  // mid-session must survive in the accumulated set, or the page the user is
  // on silently loses it and every deeper page shifts against the readout.
  // The inverse prune rides along: an accumulated row that sorts INSIDE the
  // window's span but isn't in the window can only have been deleted
  // server-side (an automation delete, a retention sweep) — keeping it would
  // show a ghost row.
  useEffect(() => {
    const finishedRows = executions
      .filter((e) => e.status !== 'queued' && e.status !== 'executing')
      .sort(byCanonicalOrder)
    if (finishedRows.length === 0) return
    setFetched((f) => {
      const ids = new Set(finishedRows.map((e) => e.id))
      const oldest = finishedRows[finishedRows.length - 1]
      return [...finishedRows,
              ...f.filter((e) => !ids.has(e.id) && byCanonicalOrder(e, oldest) > 0)]
    })
  }, [executions])

  // §7: the QUEUED FOR column counts up — one timer for the whole section,
  // running only while something is actually queued.
  const [, tick] = useState(0)
  const anyQueued = queued.length > 0
  useEffect(() => {
    if (!anyQueued) return
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [anyQueued])

  // Labels appear as soon as the page holds more than one section (§7).
  const liveSections = (executing.length > 0 ? 1 : 0) + (queued.length > 0 ? 1 : 0)
  const labelled = liveSections + (hasFinished ? 1 : 0) > 1

  // §7 pager: the filter's match total sizes the readout. Unfiltered, the
  // total ALWAYS derives from the pill count minus live rows — executionsTotal
  // is trued up by every /state refresh, while a fetch's serverTotal freezes
  // at fetch time (pinning it would strand the last page's newest rows behind
  // a disabled Next). A filter has only its fetches to go by.
  const total = !filtered
    ? Math.max(0, executionsTotal - executing.length - queued.length)
    : (serverTotal ?? finished.length)
  // Clamp the page when the total shrinks beneath it (a retention sweep, a
  // filter's true count landing) — never an empty slice with rows in hand.
  const maxPage = Math.max(0, Math.ceil(total / PAGE) - 1)
  const p = Math.min(page, maxPage)
  const visible = finished.slice(p * PAGE, p * PAGE + PAGE)

  // §7 Next: a page whose rows are already in hand re-slices with no request;
  // past the rows in hand it fetches the next keyset page — cursor at the last
  // finished row in hand — and advances only when it lands.
  const next = () => {
    if (busy || finished.length === 0) return
    const target = p + 1
    if (finished.length >= Math.min((target + 1) * PAGE, total)) {
      setPage(target)
      return
    }
    const last = finished[finished.length - 1]
    const n = fetchSeq.current
    setBusy(true)
    void api.listExecutions({
      ...query,
      limit: PAGE,
      before: { startedMs: last.startedMs, id: last.id },
    }).then((r) => {
      if (n !== fetchSeq.current) return
      setFetched((f) => [...f, ...r.executions])
      setServerTotal(r.total)
      if (r.executions.length > 0) setPage(target)
    }, (err: Error) => { if (n === fetchSeq.current) showToast(err.message) })
      .finally(() => setBusy(false))
  }

  // §7 filter line: one chip per selected status, per selected automation,
  // one for the time range, then Clear filters.
  const nameOf = (id: string) => automations.find((a) => a.id === id)?.name ?? id.slice(0, 8)
  const timeChip = timeLabel(filters.time)
  const count = activeCount(filters)

  // §7 empty copy: unfiltered, the page's own words; under any filter, one
  // card for every case.
  const emptyTitle = filtered ? 'No matching executions'
    : labelled ? 'No finished executions yet' : 'No executions yet'
  const emptyBody = filtered ? 'Executions matching these filters will appear here.'
    : labelled ? 'Finished executions will appear here.'
      : 'Execute an automation — every execution will appear right here.'
  // §7: a filter that leaves no section with rows shows the one card as the
  // whole body — but never while its first fetch is still on the wire.
  const nothing = liveSections === 0 && finished.length === 0

  return (
    <div className="ad-anim-page" style={{ maxWidth: 1200, margin: '0 auto', padding: '26px 30px 70px' }}>
      {modalOpen && (
        <FilterModal
          filters={filters}
          onApply={(f) => setFilters(f)}
          onClose={() => setModalOpen(false)}
        />
      )}
      <PageTitle
        style={filtered ? { marginBottom: 12 } : undefined}
        right={
          <HeaderActions>
            <BtnGhost onClick={() => setModalOpen(true)} title="Filter by status, automation, and start time">
              {count > 0 ? `Filter · ${count}` : 'Filter'}
            </BtnGhost>
          </HeaderActions>
        }
      >
        Executions
      </PageTitle>
      {filtered && (
        <div
          data-testid="executions-filter-line"
          style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 20 }}
        >
          {filters.statuses.map((s) => <MetaChip key={s}>{statusLabel(s)}</MetaChip>)}
          {filters.automations.map((id) => <MetaChip key={id}>{nameOf(id)}</MetaChip>)}
          {timeChip && <MetaChip>{timeChip}</MetaChip>}
          <button className="ad-btn-text dim" onClick={() => setFilters(DEFAULT_FILTERS)} style={{ marginLeft: 4 }}>
            Clear filters
          </button>
        </div>
      )}

      {nothing ? (
        firstFetchDone ? <EmptyNotice title={emptyTitle} body={emptyBody} /> : null
      ) : (
        <>
          {executing.length > 0 && (
            <>
              {labelled && <Eyebrow style={sectionLabel}>EXECUTING</Eyebrow>}
              <Table rows={executing} go={go} />
            </>
          )}
          {queued.length > 0 && (
            <>
              {labelled && <Eyebrow style={{ ...sectionLabel, marginTop: executing.length > 0 ? 26 : 0 }}>QUEUED</Eyebrow>}
              <Table rows={queued} go={go} queued />
            </>
          )}
          {hasFinished && (
            <>
              {labelled && <Eyebrow style={{ ...sectionLabel, marginTop: liveSections > 0 ? 26 : 0 }}>FINISHED</Eyebrow>}
              {/* §7: no empty card while the first fetch is on the wire — the
                * card means the server answered empty. */}
              {finished.length === 0 && !firstFetchDone ? null
              : finished.length === 0 ? (
                <EmptyNotice title={emptyTitle} body={emptyBody} />
              ) : (
                <>
                  <Table rows={visible} go={go} />
                  {/* §7 pager: only when the total exceeds one page — a short
                    * table looks exactly as it did before paging existed. */}
                  {total > PAGE && (
                    <div
                      data-testid="executions-pager"
                      style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, marginTop: 12 }}
                    >
                      <button
                        className="ad-btn-text dim"
                        disabled={p === 0}
                        onClick={() => setPage(p - 1)}
                      >
                        Prev
                      </button>
                      <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>·</span>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-faint)' }}>
                        {`${(p * PAGE + 1).toLocaleString('en-US')}–${(p * PAGE + visible.length).toLocaleString('en-US')} of ${total.toLocaleString('en-US')}`}
                      </span>
                      <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>·</span>
                      <button
                        className="ad-btn-text dim"
                        disabled={busy || p * PAGE + visible.length >= total}
                        onClick={next}
                      >
                        Next
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
