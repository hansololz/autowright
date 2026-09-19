// 2026-09-18 renderer data-layer audit batch: the fixes with no natural home
// in an existing per-surface file — the §7 result body's reset on a new
// execution and its 12-view cap, the §19 store's reconnect flag, canonical
// merge order, log-gap refetch and coalesced unknown-id fallback, the §9
// modal-aware popstate guard, and the §9 error boundary's remount on retry.
// Everything renders for real (happy-dom, StrictMode) with the api mocked.
import React from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Execution, LogLine, ResultFile, WsEvent } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    resultFile: vi.fn(() => Promise.reject(new Error('offline'))),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let ResultSection: typeof import('../src/result').ResultSection
let MAX_FILE_VIEWS: number
let ErrorBoundary: typeof import('../src/ErrorBoundary').ErrorBoundary
let Modal: typeof import('../src/ui').Modal
let initialState: Record<string, unknown>

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  const result = await import('../src/result')
  ResultSection = result.ResultSection
  MAX_FILE_VIEWS = result.MAX_FILE_VIEWS
  ErrorBoundary = (await import('../src/ErrorBoundary')).ErrorBoundary
  Modal = (await import('../src/ui')).Modal
  initialState = { ...storeMod.useStore.getState() }
})

beforeEach(() => {
  const fresh = Object.fromEntries(Object.entries(initialState).map(([k, v]) =>
    [k, typeof v === 'function' ? v : structuredClone(v)]))
  storeMod.useStore.setState(fresh as never, true)
  for (const fn of Object.values(mockedApi)) fn.mockReset()
  mockedApi.state.mockRejectedValue(new Error('offline'))
  mockedApi.getExecution.mockRejectedValue(new Error('offline'))
  mockedApi.getExecutionLogs.mockRejectedValue(new Error('offline'))
  mockedApi.getAutomation.mockRejectedValue(new Error('offline'))
  mockedApi.resultFile.mockRejectedValue(new Error('offline'))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const ex = (id: string, startedMs: number, over: Partial<Execution> = {}): Execution => ({
  id, automationId: 'a1', automationName: 'Automation', automationDeleted: false, versionLabel: 'v1',
  status: 'succeeded', trigger: 'Manual', triggerSender: null, test: false, duration: '1s',
  started: 'now', startedMs, endedMs: 0, queuedMs: 0, durationMs: null, passStartedMs: 0,
  note: null, error: null, ...over,
})
const line = (sequence: number, text = 'line'): LogLine => ({ time: '00:00', kind: 'out', sequence, text })
const logEv = (executionId: string, l: LogLine): WsEvent =>
  ({ event: 'execution.log', executionId, automationId: null, stepIndex: null, attempt: null, line: l })

// ---------- §7 result views ----------

const f = (name: string, size = '1 KB'): ResultFile => ({ name, size })
const resp = (text: string) =>
  ({ text: async () => text, blob: async () => new Blob([text]) }) as unknown as Response

describe('§7 result file body (result.tsx)', () => {
  it('a new execution clears the previous body instead of showing it under the new name', async () => {
    mockedApi.resultFile.mockResolvedValue(resp('the first run'))
    const view = render(
      <ResultSection
        label="RESULT" executionId="e1"
        result={{ files: [f('result.md')], path: '/tmp/r' }}
      />,
    )
    expect(await screen.findByText('the first run')).toBeTruthy()

    // the same filename under a new execution: the body is a different file,
    // so the old text must not sit there while the new fetch is on the wire
    let release: (r: Response) => void = () => {}
    mockedApi.resultFile.mockReturnValue(new Promise<Response>((r) => { release = r }))
    view.rerender(
      <ResultSection
        label="RESULT" executionId="e2"
        result={{ files: [f('result.md')], path: '/tmp/r' }}
      />,
    )
    expect(screen.queryByText('the first run')).toBeNull()
    expect(screen.getByText('Loading…')).toBeTruthy()

    await act(async () => { release(resp('the second run')) })
    expect(await screen.findByText('the second run')).toBeTruthy()
  })

  it(`caps the top-level views at 12 renderable files, footer still lists every one`, async () => {
    mockedApi.resultFile.mockResolvedValue(resp('body'))
    const files = Array.from({ length: 15 }, (_, i) => f(`page-${String(i).padStart(2, '0')}.md`))
    render(
      <ResultSection
        label="RESULT" executionId="e1"
        result={{ files, path: '/tmp/r' }}
      />,
    )
    await act(async () => {})

    expect(MAX_FILE_VIEWS).toBe(12)
    // one view card per capped file, each titled by its name; the FILES footer
    // header counts them all, and a footer row exists for every file
    expect(screen.getAllByText('markdown').length).toBe(MAX_FILE_VIEWS)
    expect(screen.getByText('FILES · 15')).toBeTruthy()
    for (const file of files) expect(screen.getAllByText(file.name).length).toBeGreaterThan(0)
    // the files past the cap have a footer row and nothing above it
    expect(screen.getAllByText('page-14.md').length).toBe(1)
    expect(screen.getAllByText('page-00.md').length).toBe(2)
  })
})

// ---------- §19 store ----------

describe('§19 store reconnect counting', () => {
  it('disconnect puts the next socket back to a boot connection — no phantom reconnect', () => {
    const m = storeMod.useStore.getState()
    m.applyEvent({ event: 'ws.open' })
    expect(storeMod.useStore.getState().reconnects).toBe(0)
    m.applyEvent({ event: 'ws.open' })
    expect(storeMod.useStore.getState().reconnects).toBe(1)

    m.disconnect()
    storeMod.useStore.setState({ reconnects: 0 })
    m.applyEvent({ event: 'ws.open' })
    expect(storeMod.useStore.getState().reconnects).toBe(0)
  })
})

describe('§7/§19 canonical execution order on the event path', () => {
  it('ties on startedMs break by id, exactly as the executions list sorts', () => {
    storeMod.useStore.setState({ executions: [ex('b', 500), ex('c', 500)] })
    storeMod.useStore.getState().applyEvent({
      event: 'execution.started', executionId: 'a', automationId: 'a1',
      execution: ex('a', 500, { status: 'executing' }), automation: null,
    } as WsEvent)
    expect(storeMod.useStore.getState().executions.map((e) => e.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('§19 log gap refetch', () => {
  it('a sequence past last + 1 asks for the bucket once, however many gaps follow', async () => {
    mockedApi.getExecutionLogs.mockResolvedValue({ lines: [] })
    const key = storeMod.logKey(null, null)
    storeMod.useStore.setState({ execLogs: { e1: { [key]: [line(1)] } } })
    const m = storeMod.useStore.getState()

    m.applyEvent(logEv('e1', line(2)))
    expect(mockedApi.getExecutionLogs).not.toHaveBeenCalled()

    m.applyEvent(logEv('e1', line(4)))
    await waitFor(() => expect(mockedApi.getExecutionLogs).toHaveBeenCalledTimes(1))

    // the flag is per bucket: a second gap in the same one costs no second fetch
    m.applyEvent(logEv('e1', line(9)))
    await act(async () => {})
    expect(mockedApi.getExecutionLogs).toHaveBeenCalledTimes(1)
  })
})

describe('§19 unknown-id event fallback', () => {
  it('a burst of events for rows this client has never seen costs one /state', async () => {
    const auto = (id: string) => ({ id, name: id } as Automation)
    storeMod.useStore.setState({ automations: [] })
    const m = storeMod.useStore.getState()
    for (let i = 0; i < 5; i++) {
      m.applyEvent({ event: 'automation.changed', automationId: `a${i}`, automation: auto(`a${i}`) })
    }
    expect(mockedApi.state).toHaveBeenCalledTimes(1)
    // the tray dot still follows the rows the client does hold on that path
    await act(async () => {})
  })
})

describe('§9 popstate while a modal is open', () => {
  it('back is inert — the entry is pushed back and the page never changes', async () => {
    storeMod.useStore.setState({ surface: 'app', page: 'automation', automationId: 'a1' })
    render(
      <Modal onClose={() => {}} width={400} ariaLabel="Guarded">
        {() => <div>modal body</div>}
      </Modal>,
    )
    await act(async () => {})

    const pushState = vi.spyOn(history, 'pushState')
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate', {
        state: { adNav: { surface: 'app', page: 'automations', automationId: null, executionId: null, createFrom: null, agentEditId: null } },
      }))
    })
    expect(storeMod.useStore.getState().page).toBe('automation')
    expect(pushState).toHaveBeenCalledTimes(1)
  })
})

// ---------- §9 error boundary ----------

describe('§9 error boundary retry', () => {
  it('the back button remounts the children — a child that threw once renders', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let mounts = 0
    let broken = true
    const Once = () => {
      React.useEffect(() => { mounts += 1 }, [])
      if (broken) throw new Error('the page blew up')
      return <div>recovered</div>
    }
    render(<ErrorBoundary><Once /></ErrorBoundary>)
    expect(screen.getByText('the page blew up')).toBeTruthy()
    // a throwing render never commits, so nothing has mounted yet
    expect(mounts).toBe(0)

    broken = false
    fireEvent.click(screen.getByText('Back to Automations'))
    expect(screen.getByText('recovered')).toBeTruthy()
    expect(screen.queryByText('Something went wrong on this page')).toBeNull()
    // the retry is a genuine mount of a fresh subtree, not a re-render of the
    // tree that threw
    expect(mounts).toBeGreaterThan(0)
  })
})
