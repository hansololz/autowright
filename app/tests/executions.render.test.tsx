// Component tests for the §7 executions list: the three-section stack that
// belongs to All alone (Executing / Queued / Finished), the Queued table's own
// columns, and the drain order — a §6 queued firing must read top-down in the
// order it will actually run. Every other segment shows exactly one table:
// Executing and Queued their live rows straight out of the §19 window (never a
// fetch), a terminal segment only that status's finished rows.
// ExecutionsList renders for real (happy-dom) with the store seeded and the
// api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Execution } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let ExecutionsList: typeof import('../src/pages/ExecutionsList').default
let ExecutionPage: typeof import('../src/pages/ExecutionPage').default

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  ExecutionsList = (await import('../src/pages/ExecutionsList')).default
  ExecutionPage = (await import('../src/pages/ExecutionPage')).default
})

const NOW = 1_700_000_000_000

const ex = (id: string, over: Partial<Execution> = {}): Execution => ({
  id, automationId: 'a1', automationName: 'Automation', automationDeleted: false, versionLabel: 'v1',
  status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false, duration: '1.0s',
  started: 'Today, 8:00 AM', startedMs: NOW, endedMs: NOW, queuedMs: 0,
  note: null, error: null, ...over,
})

// §19: the store holds the window; executionsTotal counts every header the
// backend has — seed them apart only when the test wants rows past the window.
const seed = (executions: Execution[], executionsTotal = executions.length) =>
  storeMod.useStore.setState({ page: 'executions', executions, executionsTotal })

beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  mockedApi.listExecutions.mockReset()
  mockedApi.listExecutions.mockResolvedValue({ executions: [], total: 0 })
  storeMod.useStore.setState({
    page: 'executions', executions: [], executionsTotal: 0, automations: [], toast: null,
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

// The filter buttons live in the header's segmented group — a row's status
// badge carries the same words, so the group scopes the query.
const filterButton = (label: string) =>
  within(screen.getByRole('group', { name: 'Filter executions' })).getByText(label)

describe('executions list sections (§7)', () => {
  it('splits queued firings out of Executing into their own Queued section', () => {
    seed([
      ex('e-run', { status: 'executing', duration: '', endedMs: 0 }),
      ex('e-wait', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 5_000, trigger: 'Discord' }),
      ex('e-done'),
    ])
    const { container } = render(<ExecutionsList />)

    expect(screen.getByText('EXECUTING')).toBeTruthy()
    expect(screen.getByText('QUEUED')).toBeTruthy()
    expect(screen.getByText('FINISHED')).toBeTruthy()
    // section membership read from document order (labels precede their tables):
    // the executing row sits between EXECUTING and QUEUED, the queued row after QUEUED
    const text = container.textContent!
    expect(text.indexOf('e-run')).toBeGreaterThan(text.indexOf('EXECUTING'))
    expect(text.indexOf('e-run')).toBeLessThan(text.indexOf('QUEUED'))
    expect(text.indexOf('e-wait')).toBeGreaterThan(text.indexOf('QUEUED'))
  })

  it('gives the Queued table its own columns — a queued row has no duration and never started', () => {
    seed([ex('e-wait', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 65_000 })])
    render(<ExecutionsList />)

    expect(screen.getByText('QUEUED FOR')).toBeTruthy()
    expect(screen.getByText('QUEUED AT')).toBeTruthy()
    expect(screen.getByText('1m 5s')).toBeTruthy()
    // DURATION / STARTED belong to the other tables, and neither is rendered here
    expect(screen.queryByText('DURATION')).toBeNull()
    expect(screen.queryByText('STARTED')).toBeNull()
  })

  it('orders Queued oldest-first — the §6 drain order, so the next to run reads top', () => {
    seed([
      ex('e-new', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 2_000 }),
      ex('e-old', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 30_000 }),
    ])
    const { container } = render(<ExecutionsList />)

    const text = container.textContent!
    expect(text.indexOf('e-old')).toBeGreaterThan(text.indexOf('QUEUED'))
    expect(text.indexOf('e-old')).toBeLessThan(text.indexOf('e-new'))
  })

  it('stays a single unlabelled table when nothing is live or queued', () => {
    seed([ex('e-done')])
    render(<ExecutionsList />)

    expect(screen.queryByText('EXECUTING')).toBeNull()
    expect(screen.queryByText('QUEUED')).toBeNull()
    expect(screen.queryByText('FINISHED')).toBeNull()
  })

  it('lists §11 test executions like any run, printing "Test" once in the trigger column', () => {
    seed([ex('e-test', { test: true, trigger: 'Test', versionLabel: 'Test', automationName: 'New automation' })])
    const { container } = render(<ExecutionsList />)

    expect(screen.getByText('e-test')).toBeTruthy()
    // trigger and versionLabel labels are both "Test" — the row never prints the pair (§7)
    expect(container.textContent).not.toContain('Test · Test')
    expect(screen.getByText('Test')).toBeTruthy()
  })

  it('hides the live rows under a terminal filter — that segment is one table of finished rows', async () => {
    seed([
      ex('e-run', { status: 'executing', duration: '', endedMs: 0 }),
      ex('e-wait', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 1_000 }),
      ex('e-ok'),
      ex('e-bad', { status: 'failed' }),
    ])
    render(<ExecutionsList />)

    fireEvent.click(filterButton('Succeeded'))
    await waitFor(() => expect(mockedApi.listExecutions).toHaveBeenCalled())
    expect(mockedApi.listExecutions).toHaveBeenCalledWith({ status: 'succeeded', limit: 50 })
    // only the matching finished row survives — the live rows are not stacked above it
    expect(screen.getAllByTestId('execution-row').length).toBe(1)
    expect(screen.getByText('e-ok')).toBeTruthy()
    expect(screen.queryByText('e-run')).toBeNull()
    expect(screen.queryByText('e-wait')).toBeNull()
    expect(screen.queryByText('e-bad')).toBeNull()
    // and a single table carries no section labels
    expect(screen.queryByText('EXECUTING')).toBeNull()
    expect(screen.queryByText('QUEUED')).toBeNull()
    expect(screen.queryByText('FINISHED')).toBeNull()
  })
})

// §7 live segments: Executing and Queued each show exactly one table read
// straight out of the §19 window — no section label, no fetch, no paging.
describe('executions list live segments (§7)', () => {
  const liveMix = () => [
    ex('e-run', { status: 'executing', duration: '2.0s', endedMs: 0 }),
    ex('q-new', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 2_000 }),
    ex('q-old', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 30_000 }),
    ex('e-done'),
  ]

  it('shows only executing rows under Executing and never fetches', () => {
    seed(liveMix())
    render(<ExecutionsList />)

    fireEvent.click(filterButton('Executing'))
    expect(screen.getAllByTestId('execution-row').length).toBe(1)
    expect(screen.getByText('e-run')).toBeTruthy()
    expect(screen.queryByText('q-old')).toBeNull()
    expect(screen.queryByText('e-done')).toBeNull()
    // normal columns, and no section label above a lone table
    expect(screen.getByText('DURATION')).toBeTruthy()
    expect(screen.queryByText('QUEUED FOR')).toBeNull()
    expect(screen.queryByText('EXECUTING')).toBeNull()
    expect(screen.queryByText('QUEUED')).toBeNull()
    expect(screen.queryByText('FINISHED')).toBeNull()
    // the window always holds every live row (§19) — nothing to page in
    expect(mockedApi.listExecutions).not.toHaveBeenCalled()
  })

  it('keeps the Queued segment\'s own columns and drain order, and never fetches', () => {
    seed(liveMix())
    const { container } = render(<ExecutionsList />)

    fireEvent.click(filterButton('Queued'))
    const rows = screen.getAllByTestId('execution-row')
    expect(rows.length).toBe(2)
    expect(screen.getByText('QUEUED FOR')).toBeTruthy()
    expect(screen.getByText('QUEUED AT')).toBeTruthy()
    expect(screen.queryByText('DURATION')).toBeNull()
    expect(screen.queryByText('STARTED')).toBeNull()
    // §6 drain order: the oldest wait reads top
    expect(rows[0].textContent).toContain('q-old')
    expect(rows[1].textContent).toContain('q-new')
    const text = container.textContent!
    expect(text.indexOf('q-old')).toBeLessThan(text.indexOf('q-new'))
    expect(mockedApi.listExecutions).not.toHaveBeenCalled()
  })

  it('names the live segment in its empty state', () => {
    seed([ex('e-run', { status: 'executing', duration: '', endedMs: 0 })])
    const { unmount } = render(<ExecutionsList />)

    fireEvent.click(filterButton('Queued'))
    expect(screen.getByText('No queued executions')).toBeTruthy()
    expect(screen.getByText('Executions matching this filter will appear here.')).toBeTruthy()
    unmount()

    seed([ex('e-wait', { status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - 1_000 })])
    render(<ExecutionsList />)
    fireEvent.click(filterButton('Executing'))
    expect(screen.getByText('No executing executions')).toBeTruthy()
    expect(mockedApi.listExecutions).not.toHaveBeenCalled()
  })
})

// §7 status filter: All, the two live segments, and the five §4.6 terminal
// statuses. Picking a terminal filter fetches that status's own newest page
// (§19), because the window may hold only a slice of it.
describe('executions list status filter (§7)', () => {
  it('renders All, the live segments, and the five terminal statuses in the sections\' order', () => {
    seed([ex('e-done')])
    render(<ExecutionsList />)

    const group = screen.getByRole('group', { name: 'Filter executions' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'All', 'Executing', 'Queued', 'Succeeded', 'Failed', 'Cancelled', 'Skipped', 'Interrupted',
    ])
  })

  it('fetches the filter\'s own page and merges it with the window\'s matching rows', async () => {
    mockedApi.listExecutions.mockResolvedValue({
      executions: [ex('e-fetch', { status: 'failed', startedMs: NOW - 5_000, endedMs: NOW - 5_000 })],
      total: 2,
    })
    seed([
      ex('e-win', { status: 'failed' }),
      ex('e-ok'),
    ])
    render(<ExecutionsList />)

    fireEvent.click(filterButton('Failed'))
    await waitFor(() => expect(screen.getByText('e-fetch')).toBeTruthy())
    expect(mockedApi.listExecutions).toHaveBeenCalledWith({ status: 'failed', limit: 50 })
    expect(screen.getByText('e-win')).toBeTruthy()      // the window's matching row stays
    expect(screen.queryByText('e-ok')).toBeNull()       // a non-matching one does not
    expect(screen.getAllByTestId('execution-row').length).toBe(2)
  })

  it('names the filter in the empty state when nothing matches', async () => {
    seed([ex('e-done')])
    render(<ExecutionsList />)

    fireEvent.click(filterButton('Cancelled'))
    await waitFor(() => expect(mockedApi.listExecutions).toHaveBeenCalled())
    expect(screen.getByText('No cancelled executions')).toBeTruthy()
  })

  it('renders a fetched row the window already holds exactly once — the window wins', async () => {
    mockedApi.listExecutions.mockResolvedValue({
      executions: [ex('e-dup', { status: 'failed', automationName: 'Stale copy' })],
      total: 1,
    })
    seed([ex('e-dup', { status: 'failed', automationName: 'Window copy' })])
    render(<ExecutionsList />)

    fireEvent.click(filterButton('Failed'))
    await waitFor(() => expect(mockedApi.listExecutions).toHaveBeenCalled())
    expect(screen.getAllByTestId('execution-row').length).toBe(1)
    expect(screen.getByText('Window copy')).toBeTruthy()
    expect(screen.queryByText('Stale copy')).toBeNull()
  })
})

// §7 Finished paging: retention can be off entirely (`keepForever`), so the
// finished list is unbounded — it moves in pages of 50 behind the pager under
// the finished table. Prev, and any page whose rows are already in hand,
// re-slices with no request; Next past them fetches the next §19 keyset page.
describe('executions list finished paging (§7)', () => {
  const finishedRows = (n: number, from = 0) =>
    Array.from({ length: n }, (_, i) =>
      ex(`e-${String(from + i).padStart(4, '0')}`, {
        endedMs: NOW - (from + i) * 1000, startedMs: NOW - (from + i) * 1000,
      }))

  const pager = () => screen.getByTestId('executions-pager')
  const pagerButton = (label: string) =>
    within(pager()).getByText(label).closest('button')! as HTMLButtonElement

  it('renders one 50-row page under the pager, Prev disabled on the first', () => {
    seed([
      ...Array.from({ length: 3 }, (_, i) =>
        ex(`r-${i}`, { status: 'executing', duration: '', endedMs: 0 })),
      ...finishedRows(50),
    ], 1243)
    render(<ExecutionsList />)

    // 3 executing + one 50-row finished page — never the whole history at once
    expect(screen.getAllByTestId('execution-row').length).toBe(53)
    // 1243 headers minus the 3 live rows, thousands-separated
    expect(within(pager()).getByText('1–50 of 1,240')).toBeTruthy()
    expect(pagerButton('Prev').disabled).toBe(true)
    expect(pagerButton('Next').disabled).toBe(false)
    expect(screen.getByText('e-0000')).toBeTruthy()
    expect(screen.getByText('e-0049')).toBeTruthy()
  })

  it('fetches the next keyset page on Next, then advances the slice', async () => {
    const windowRows = finishedRows(50)
    mockedApi.listExecutions.mockResolvedValue({ executions: finishedRows(50, 50), total: 120 })
    seed(windowRows, 120)
    render(<ExecutionsList />)

    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(screen.getByText('e-0050')).toBeTruthy())
    // §19 keyset cursor: the last finished row in hand
    expect(mockedApi.listExecutions).toHaveBeenCalledWith({
      status: 'finished', limit: 50,
      before: { startedMs: windowRows[49].startedMs, id: windowRows[49].id },
    })
    expect(screen.getAllByTestId('execution-row').length).toBe(50)
    expect(within(pager()).getByText('51–100 of 120')).toBeTruthy()
    expect(screen.queryByText('e-0049')).toBeNull()
  })

  it('keeps deeper pages seamless when a /state refresh shifts the window (§7 absorption)', async () => {
    const windowRows = finishedRows(50)
    mockedApi.listExecutions.mockResolvedValue({ executions: finishedRows(50, 50), total: 120 })
    seed(windowRows, 120)
    render(<ExecutionsList />)

    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(screen.getByText('e-0050')).toBeTruthy())

    // Two new executions finish and a /state refresh replaces the window
    // wholesale: the newest 50 finished are the 2 new rows + old rows 0-47.
    // Old rows 48-49 leave the window — the accumulated set must absorb
    // them, or page 2 silently loses two rows and shifts against the readout.
    const fresh = [
      ex('e-new-1', { startedMs: NOW + 2000, endedMs: NOW + 2000 }),
      ex('e-new-2', { startedMs: NOW + 1000, endedMs: NOW + 1000 }),
    ]
    seed([...fresh, ...finishedRows(48)], 122)

    await waitFor(() => expect(within(pager()).getByText('51–100 of 122')).toBeTruthy())
    expect(screen.getByText('e-0048')).toBeTruthy()
    expect(screen.getByText('e-0049')).toBeTruthy()
    expect(screen.getAllByTestId('execution-row').length).toBe(50)
  })

  it('re-slices on Prev with no request', async () => {
    mockedApi.listExecutions.mockResolvedValue({ executions: finishedRows(50, 50), total: 120 })
    seed(finishedRows(50), 120)
    render(<ExecutionsList />)

    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(screen.getByText('e-0050')).toBeTruthy())
    expect(mockedApi.listExecutions).toHaveBeenCalledTimes(1)

    fireEvent.click(pagerButton('Prev'))
    expect(screen.getByText('e-0000')).toBeTruthy()
    expect(screen.getByText('e-0049')).toBeTruthy()
    expect(within(pager()).getByText('1–50 of 120')).toBeTruthy()
    expect(pagerButton('Prev').disabled).toBe(true)
    expect(mockedApi.listExecutions).toHaveBeenCalledTimes(1)
  })

  it('re-slices on Next when the page\'s rows are already in hand', async () => {
    mockedApi.listExecutions.mockResolvedValue({ executions: finishedRows(50, 50), total: 120 })
    seed(finishedRows(50), 120)
    render(<ExecutionsList />)

    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(screen.getByText('e-0050')).toBeTruthy())
    fireEvent.click(pagerButton('Prev'))

    // the second page is fetched already — going forward again asks for nothing
    fireEvent.click(pagerButton('Next'))
    expect(screen.getByText('e-0050')).toBeTruthy()
    expect(within(pager()).getByText('51–100 of 120')).toBeTruthy()
    expect(mockedApi.listExecutions).toHaveBeenCalledTimes(1)
  })

  it('renders no pager when the total fits one page', () => {
    seed(finishedRows(50))
    render(<ExecutionsList />)

    expect(screen.getAllByTestId('execution-row').length).toBe(50)
    expect(screen.queryByTestId('executions-pager')).toBeNull()
  })

  it('toasts a failed page fetch and stays on the current page, pager in place', async () => {
    mockedApi.listExecutions.mockRejectedValue(new Error('backend is offline'))
    seed(finishedRows(50), 120)
    render(<ExecutionsList />)

    fireEvent.click(pagerButton('Next'))
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('backend is offline'))
    expect(within(pager()).getByText('1–50 of 120')).toBeTruthy()
    expect(screen.getByText('e-0000')).toBeTruthy()
    expect(screen.getAllByTestId('execution-row').length).toBe(50)
  })

  it('never caps or pages Executing and Queued', () => {
    seed([
      ...Array.from({ length: 60 }, (_, i) =>
        ex(`r-${String(i).padStart(2, '0')}`, { status: 'executing', duration: '', endedMs: 0 })),
      ...Array.from({ length: 55 }, (_, i) =>
        ex(`q-${String(i).padStart(2, '0')}`, {
          status: 'queued', duration: '', endedMs: 0, queuedMs: NOW - i * 1000,
        })),
      ...finishedRows(50),
    ])
    render(<ExecutionsList />)

    // 60 executing + 55 queued, both whole, above the 50-row finished page
    expect(screen.getAllByTestId('execution-row').length).toBe(165)
    expect(screen.getByText('EXECUTING')).toBeTruthy()
    expect(screen.getByText('QUEUED')).toBeTruthy()
    // 165 headers minus the 115 live rows is exactly one page — no pager
    expect(screen.queryByTestId('executions-pager')).toBeNull()

    fireEvent.click(filterButton('Executing'))
    expect(screen.getAllByTestId('execution-row').length).toBe(60)
    expect(screen.queryByTestId('executions-pager')).toBeNull()
    expect(mockedApi.listExecutions).not.toHaveBeenCalled()
  })
})

// §7 log cap: the LOGS pane shows the last 2000 lines and says so when earlier
// lines were dropped. Log sequences are gapless from 1 (§5), so a kept head past
// 1 is the truncation signal.
describe('execution page log cap (§7)', () => {
  const line = (sequence: number) => ({ time: '00:00', kind: 'out' as const, sequence, text: `line ${sequence}` })
  const withLogs = (lines: ReturnType<typeof line>[]) => {
    const full: Execution = {
      ...ex('e1'),
      steps: [{
        name: 'Step one', status: 'succeeded', duration: '1s',
        attempts: [{ number: 1, status: 'succeeded', duration: '1s', startedMs: NOW }],
      }],
      result: null,
    }
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [ex('e1')],
      executionFull: { e1: full },
      execLogs: { e1: { '0.1': lines } },
    })
  }

  it('shows the truncation notice when the kept log starts past line 1', () => {
    withLogs([line(51), line(52), line(53)])
    render(<ExecutionPage />)

    expect(screen.getByText('Truncated — showing the last 2000 lines. The full log is on disk.')).toBeTruthy()
    expect(screen.getByText('line 51')).toBeTruthy()
  })

  it('shows no notice for a whole log', () => {
    withLogs([line(1), line(2)])
    render(<ExecutionPage />)

    expect(screen.getByText('line 1')).toBeTruthy()
    expect(screen.queryByText(/Truncated/)).toBeNull()
  })
})

describe('execution page LOGS rail keys and selection (§7)', () => {
  const attempt = { number: 1, status: 'succeeded' as const, duration: '1s', startedMs: NOW }
  const seedThree = () => {
    const full: Execution = {
      ...ex('e1'),
      steps: [
        { name: 'Fetch page', status: 'succeeded', duration: '1s', attempts: [attempt] },
        { name: 'Parse it', status: 'succeeded', duration: '1s', attempts: [attempt] },
        { name: 'Send mail', status: 'succeeded', duration: '1s', attempts: [attempt] },
      ],
      result: null,
    }
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [ex('e1')],
      executionFull: { e1: full }, execLogs: {},
    })
  }
  // the LOGS pane header names the selected row (the "Setup log" eyebrow for
  // the pseudo-row); the rail's selected block carries aria-current
  const selectedRow = () => document.querySelector('[aria-current]') as HTMLElement
  // the LOGS pane header repeats the selected step's name, so pick the rail's own row
  const railRow = (name: string) => screen.getAllByText(name)
    .map((el) => el.closest('button, [aria-current]'))
    .find((el): el is HTMLElement => el instanceof HTMLElement)!

  it('← / → move the selection one rail row, through the Setup log, and stop at both ends', () => {
    seedThree()
    render(<ExecutionPage />)
    // a settled execution with no failure auto-selects the last attempted step
    expect(selectedRow().textContent).toContain('Send mail')
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(selectedRow().textContent).toContain('Send mail')
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Parse it')
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Setup log')
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Setup log')
    fireEvent.keyDown(document, { key: 'ArrowRight' })
    expect(selectedRow().textContent).toContain('Fetch page')
  })

  it('the LOGS pane header counts the selected step as LOG k OF n; the Setup log carries no counter', () => {
    seedThree()
    render(<ExecutionPage />)
    // §7: the rail is headed LOGS on the page too, the same eyebrow as the §11 modal's rail
    expect(screen.getByText('LOGS')).toBeTruthy()
    expect(screen.getByText('LOG 3 OF 3')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.getByText('LOG 2 OF 3')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(screen.queryByText(/LOG \d+ OF/)).toBeNull()
    // the pseudo-row's plain eyebrow, plus the rail row itself
    expect(screen.getAllByText('Setup log').length).toBe(2)
  })

  it('the selected row is an unfocusable, text-selectable block and the others are buttons; a click swaps them', () => {
    seedThree()
    render(<ExecutionPage />)
    const viewed = railRow('Send mail')
    expect(viewed.tagName).toBe('DIV')
    expect(viewed.getAttribute('tabindex')).toBeNull()
    expect(viewed.style.userSelect).toBe('text')
    expect(railRow('Fetch page').tagName).toBe('BUTTON')
    expect(railRow('Setup log').tagName).toBe('BUTTON')
    // a clicked row unmounts as it becomes the selected block — focus falls to the body
    railRow('Fetch page').focus()
    fireEvent.click(railRow('Fetch page'))
    expect(railRow('Fetch page').tagName).toBe('DIV')
    expect(railRow('Send mail').tagName).toBe('BUTTON')
    expect(document.activeElement).toBe(document.body)
  })

  it('a key flip drops focus from whatever holds it and ignores editable targets', () => {
    seedThree()
    render(<ExecutionPage />)
    const other = railRow('Fetch page') as HTMLButtonElement
    other.focus()
    expect(document.activeElement).toBe(other)
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Parse it')
    expect(document.activeElement).toBe(document.body)
    // typing in a field never flips the selection
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    fireEvent.keyDown(input, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Parse it')
    input.remove()
  })

  it('a key flip is the user\'s own selection: the live auto-follow stops', () => {
    const full: Execution = {
      ...ex('e1', { status: 'executing', duration: '' }),
      steps: [
        { name: 'Fetch page', status: 'succeeded', duration: '1s', attempts: [attempt] },
        { name: 'Parse it', status: 'executing', duration: '', attempts: [{ ...attempt, status: 'executing' }] },
      ],
      result: null,
    }
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [ex('e1', { status: 'executing' })],
      executionFull: { e1: full }, execLogs: {},
    })
    render(<ExecutionPage />)
    expect(selectedRow().textContent).toContain('Parse it')
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Fetch page')
    // a later store write while still live no longer re-follows the executing step
    storeMod.useStore.setState({ executionFull: { e1: { ...full } } })
    expect(selectedRow().textContent).toContain('Fetch page')
  })

  it('the page\'s rail yields to an open modal', async () => {
    seedThree()
    const { Modal } = await import('../src/ui')
    render(
      <>
        <ExecutionPage />
        <Modal onClose={() => {}} width={300}>{() => <div>Over the page</div>}</Modal>
      </>,
    )
    expect(selectedRow().textContent).toContain('Send mail')
    fireEvent.keyDown(document, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Send mail')
  })
})

describe('execution page LOGS pane header controls + find in log (§7)', () => {
  const attempt = { number: 1, status: 'succeeded' as const, duration: '1s', startedMs: NOW }
  const line = (sequence: number, text: string) => ({ sequence, time: '12:00:00', kind: 'out' as const, text })
  const seed = () => {
    const full: Execution = {
      ...ex('e1'),
      steps: [
        { name: 'Fetch page', status: 'succeeded', duration: '1s', attempts: [attempt] },
        { name: 'Parse it', status: 'succeeded', duration: '1s', attempts: [attempt] },
        { name: 'Send mail', status: 'succeeded', duration: '1s', attempts: [attempt] },
      ],
      result: null,
    }
    storeMod.useStore.setState({
      page: 'execution', executionId: 'e1', executions: [ex('e1')],
      executionFull: { e1: full },
      execLogs: { e1: {
        [storeMod.logKey(2, 1)]: [line(1, 'Sending mail to alice'), line(2, 'sent 3 mails'), line(3, 'done')],
        [storeMod.logKey(1, 1)]: [line(1, 'parsed')],
      } },
    })
  }
  const selectedRow = () => document.querySelector('[aria-current]') as HTMLElement
  const btn = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement
  const marks = () => Array.from(document.querySelectorAll('mark')).map((m) => `${m.textContent}:${m.getAttribute('data-match')}`)

  it('the header counts the loaded log\'s lines, singular at one', () => {
    seed()
    render(<ExecutionPage />)
    expect(screen.getByText('3 lines')).toBeTruthy()
    fireEvent.click(btn('Previous log'))
    expect(screen.getByText('1 line')).toBeTruthy()
  })

  it('previous / next log chevrons walk the rail like ← / →, disabled at the ends', () => {
    seed()
    render(<ExecutionPage />)
    expect(selectedRow().textContent).toContain('Send mail')
    expect(btn('Next log').disabled).toBe(true)
    expect(btn('Previous log').disabled).toBe(false)
    fireEvent.click(btn('Previous log'))
    expect(selectedRow().textContent).toContain('Parse it')
    fireEvent.click(btn('Previous log'))
    fireEvent.click(btn('Previous log'))
    expect(selectedRow().textContent).toContain('Setup log')
    expect(btn('Previous log').disabled).toBe(true)
    fireEvent.click(btn('Next log'))
    expect(selectedRow().textContent).toContain('Fetch page')
  })

  it('the find button opens the bar; typing marks hits over the text with a counter, and the query survives a log flip', () => {
    seed()
    render(<ExecutionPage />)
    expect(screen.queryByTestId('find-bar')).toBeNull()
    fireEvent.click(btn('Find in log'))
    expect(btn('Find in log').getAttribute('aria-pressed')).toBe('true')
    const field = screen.getByPlaceholderText('Find in log') as HTMLInputElement
    fireEvent.change(field, { target: { value: 'mail' } })
    expect(marks()).toEqual(['mail:current', 'mail:hit'])
    expect(screen.getByTestId('find-counter').textContent).toBe('1 of 2')
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(marks()).toEqual(['mail:hit', 'mail:current'])
    expect(screen.getByTestId('find-counter').textContent).toBe('2 of 2')
    // the times are not searched
    fireEvent.change(field, { target: { value: '12:00' } })
    expect(marks()).toEqual([])
    expect(screen.getByTestId('find-counter').textContent).toBe('No matches')
    // arrow keys in the field never flip the log; the flip keeps the bar and query
    fireEvent.change(field, { target: { value: 'e' } })
    fireEvent.keyDown(field, { key: 'ArrowLeft' })
    expect(selectedRow().textContent).toContain('Send mail')
    fireEvent.click(btn('Previous log'))
    expect(selectedRow().textContent).toContain('Parse it')
    expect((screen.getByPlaceholderText('Find in log') as HTMLInputElement).value).toBe('e')
    expect(marks()).toEqual(['e:current'])
    // Escape closes the bar and clears the query
    fireEvent.keyDown(screen.getByPlaceholderText('Find in log'), { key: 'Escape' })
    expect(screen.queryByTestId('find-bar')).toBeNull()
    expect(marks()).toEqual([])
  })

  it('⌘F opens the find bar', () => {
    seed()
    render(<ExecutionPage />)
    fireEvent.keyDown(document, { key: 'f', metaKey: true })
    expect(screen.getByTestId('find-bar')).toBeTruthy()
  })
})
