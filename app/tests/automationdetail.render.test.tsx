// Component tests for the §9.2 capacity popup: pressing Execute now while
// anything is live routes through the modal — Run now (free slot), Queue
// (slots full, queue has room, sends §19 queue: true), or the capacity-full
// notice (no run option). AutomationDetail renders for real (happy-dom) with
// the store seeded and the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Execution, ParamDef, Trigger } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    listExecutions: vi.fn(async () => ({ executions: [], total: 0 })),
    executeNow: vi.fn(async () => ({ executionId: 'e-new', queued: false })),
    // §9.2 PARAMETERS row: the debounced value write
    patchAutomation: vi.fn(async () => ({})),
    deleteAutomation: vi.fn(async () => ({})),
    deleteDraft: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let mockedApi: Record<string, ReturnType<typeof vi.fn>>
let AutomationDetail: typeof import('../src/pages/AutomationDetail').default
let ParamRow: typeof import('../src/pages/detail/ParamRow').ParamRow

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  mockedApi = (await import('../src/api')).api as unknown as Record<string, ReturnType<typeof vi.fn>>
  AutomationDetail = (await import('../src/pages/AutomationDetail')).default
  ParamRow = (await import('../src/pages/detail/ParamRow')).ParamRow
})

const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers: [], triggerChip: 'No triggers',
  allTriggersOff: false, nextAtMs: null, instructions: '', notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 10, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
  ...over,
})

const NOW = 1_700_000_000_000

const queuedRow = (id: string): Execution => ({
  id, automationId: 'a1', automationName: 'Job', automationDeleted: false, versionLabel: 'v1',
  status: 'queued', trigger: 'Manual', triggerSender: null, test: false, duration: '',
  started: 'Today, 8:00 AM', startedMs: NOW, endedMs: 0, queuedMs: NOW - 5_000, durationMs: null, passStartedMs: 0,
  note: null, error: null,
})

const seed = (a: Automation, executions: Execution[] = []) =>
  storeMod.useStore.setState({ page: 'automation', automationId: 'a1', automations: [a], executions, toast: null })

beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  mockedApi.executeNow.mockClear()
  mockedApi.patchAutomation.mockClear()
  mockedApi.deleteAutomation.mockClear()
  mockedApi.deleteDraft.mockClear()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

// ConfirmModal acts on onClose, which fires only after the overlay's exit
// animation. happy-dom runs no animations, so end it by hand.
const finishModalAnim = (name: string) => {
  const dlg = screen.getByRole('alertdialog', { name })
  fireEvent.animationEnd(dlg.parentElement!)
}

const clickExecuteNow = () => {
  // The header's accent primary — reads "Executing…" while anything is live.
  const btn = screen.getAllByRole('button').find((b) => /Execute now|Executing…/.test(b.textContent ?? ''))
  expect(btn).toBeTruthy()
  fireEvent.click(btn!)
}

describe('§9.2 capacity popup', () => {
  it('nothing live → no popup, executes immediately', async () => {
    seed(auto())
    render(<AutomationDetail />)
    clickExecuteNow()
    expect(screen.queryByText('Already executing')).toBeNull()
    await waitFor(() => expect(mockedApi.executeNow).toHaveBeenCalledTimes(1))
    expect(mockedApi.executeNow).toHaveBeenCalledWith('a1', undefined, 'manual', false)
  })

  it('free slot beside a live execution → Run now confirm, plain start', async () => {
    seed(auto({ live: ['e1'], maxParallel: 3 }))
    render(<AutomationDetail />)
    clickExecuteNow()
    expect(mockedApi.executeNow).not.toHaveBeenCalled()
    expect(screen.getByText('Already executing')).toBeTruthy()
    expect(screen.getByText(/1 of 3 slots are busy/)).toBeTruthy()
    expect(screen.queryByText('Queue')).toBeNull()
    fireEvent.click(screen.getByText('Run now'))
    await waitFor(() => expect(mockedApi.executeNow).toHaveBeenCalledTimes(1))
    expect(mockedApi.executeNow).toHaveBeenCalledWith('a1', undefined, 'manual', false)
  })

  it('slots full with queue room → Queue sends queue: true and toasts', async () => {
    mockedApi.executeNow.mockResolvedValueOnce({ executionId: 'e-q', queued: true })
    seed(auto({ live: ['e1'], maxParallel: 1, maxQueued: 10 }))
    render(<AutomationDetail />)
    clickExecuteNow()
    expect(screen.getByText('Already executing')).toBeTruthy()
    expect(screen.getByText(/The slot is busy/)).toBeTruthy()
    expect(screen.queryByText('Run now')).toBeNull()
    fireEvent.click(screen.getByText('Queue'))
    await waitFor(() => expect(mockedApi.executeNow).toHaveBeenCalledTimes(1))
    expect(mockedApi.executeNow).toHaveBeenCalledWith('a1', undefined, 'manual', true)
    await waitFor(() =>
      expect(storeMod.useStore.getState().toast).toBe('Queued — runs as soon as a slot frees up.'))
  })

  it('slots and queue both full → capacity-full notice, no run or queue option', async () => {
    seed(auto({ live: ['e1'], maxParallel: 1, maxQueued: 1 }), [queuedRow('e-wait')])
    render(<AutomationDetail />)
    clickExecuteNow()
    expect(screen.getByText('Execution and queue capacity is full')).toBeTruthy()
    expect(screen.getByText(/1 executing, 1 waiting\./)).toBeTruthy()
    expect(screen.queryByText('Run now')).toBeNull()
    expect(screen.queryByText('Queue')).toBeNull()
    fireEvent.click(screen.getByText('OK'))
    await waitFor(() => expect(screen.queryByText('Execution and queue capacity is full')).toBeNull())
    expect(mockedApi.executeNow).not.toHaveBeenCalled()
  })

  it('maxQueued 0 → capacity-full notice without a waiting count', () => {
    seed(auto({ live: ['e1'], maxParallel: 1, maxQueued: 0 }))
    render(<AutomationDetail />)
    clickExecuteNow()
    expect(screen.getByText('Execution and queue capacity is full')).toBeTruthy()
    expect(screen.getByText(/1 executing\./)).toBeTruthy()
    expect(mockedApi.executeNow).not.toHaveBeenCalled()
  })
})

describe('§9.2 actions menu', () => {
  it('the Export… row is always there and opens the export modal', () => {
    seed(auto())
    render(<AutomationDetail />)
    expect(screen.queryByText('Export…')).toBeNull()  // the menu starts closed
    fireEvent.click(screen.getByLabelText('Automation actions'))
    expect(screen.getByText('Export…')).toBeTruthy()
    expect(screen.getByText('Delete automation…')).toBeTruthy()
    fireEvent.click(screen.getByText('Export…'))
    expect(screen.getByText('Export “Job”')).toBeTruthy()
    expect(screen.getByText('Include parameter values')).toBeTruthy()
  })
})

describe('§9.2 needs-fixing banner', () => {
  it('is absent when the problems list is empty', () => {
    seed(auto())
    render(<AutomationDetail />)
    expect(screen.queryByText('This automation needs fixing')).toBeNull()
  })

  it('secret-unset rows link to the Secrets page', () => {
    seed(auto({ problems: [
      { kind: 'secret-unset', label: 'Secret API_KEY has no value yet - add it on the Secrets page.' },
    ] }))
    render(<AutomationDetail />)
    expect(screen.getByText('This automation needs fixing')).toBeTruthy()
    expect(screen.getByText('Secret API_KEY has no value yet - add it on the Secrets page.')).toBeTruthy()
    fireEvent.click(screen.getByText('Open Secrets'))
    expect(storeMod.useStore.getState().page).toBe('secrets')
  })

  it('grant rows open the editor; package rows carry no action', () => {
    seed(auto({ problems: [
      { kind: 'agent-ungranted', label: "Agent Coder isn't enabled for this automation yet - enable it on the edit page." },
      { kind: 'package-missing', label: "Package pandas isn't installed yet - it installs on the first execution." },
    ] }))
    render(<AutomationDetail />)
    // one Edit per grant row, plus the §9.2 header's own Edit button — the
    // package row adds none (its label already says it self-installs).
    const edits = screen.getAllByText('Edit')
    expect(edits.length).toBe(2)
    expect(screen.queryByText('Open Secrets')).toBeNull()
    // a banner row's Edit opens the §11 editor surface
    fireEvent.click(edits[edits.length - 1])
    expect(storeMod.useStore.getState().surface).toBe('create')
  })

  it('imported unresolved-reference rows open the editor', () => {
    seed(auto({ problems: [
      { kind: 'secret-unresolved', label: 'STRIPE_KEY came from the imported file and has no match on this Mac - pick one of your secrets on the edit page.' },
      { kind: 'agent-unresolved', label: 'Researcher came from the imported file and has no match on this Mac - pick an agent on the edit page.' },
    ] }))
    render(<AutomationDetail />)
    expect(screen.getByText('This automation needs fixing')).toBeTruthy()
    // §9.2: both new §4.1 kinds fall through to the editor link — one Edit per
    // row plus the page header's own.
    const edits = screen.getAllByText('Edit')
    expect(edits.length).toBe(3)
    expect(screen.queryByText('Open Secrets')).toBeNull()
    fireEvent.click(edits[edits.length - 1])
    expect(storeMod.useStore.getState().surface).toBe('create')
  })

  it('output-collapsed rows offer Fix with AI, handing the editor the zero run and the typical count', () => {
    // §9.2/§11 collapse variant: the seed is the latest FINISHED real run —
    // the record the verdict is about.
    const zeroRun: Execution = {
      ...queuedRow('e-zero'), status: 'succeeded', duration: '2s', queuedMs: 0,
    }
    seed(auto({ problems: [
      { kind: 'output-collapsed', typical: 40, label: 'The latest execution returned nothing. Recent executions returned about 40 items each.' },
    ] }), [zeroRun])
    render(<AutomationDetail />)
    expect(screen.getByText('The latest execution returned nothing. Recent executions returned about 40 items each.')).toBeTruthy()
    fireEvent.click(screen.getByText('Fix with AI'))
    expect(storeMod.useStore.getState().fixExec).toEqual({
      executionId: 'e-zero', collapse: { typical: 40 },
    })
    expect(storeMod.useStore.getState().surface).toBe('create')
  })

  it('overdue rows are informational — label shown, no action link', () => {
    seed(auto({ problems: [
      { kind: 'overdue', label: 'Scheduled executions are being missed - it has never run.' },
    ] }))
    render(<AutomationDetail />)
    expect(screen.getByText('This automation needs fixing')).toBeTruthy()
    expect(screen.getByText('Scheduled executions are being missed - it has never run.')).toBeTruthy()
    // §9.2: overdue clears by the automation running again — no button; the
    // page header's own Edit is the only one on screen.
    expect(screen.getAllByText('Edit').length).toBe(1)
    expect(screen.queryByText('Open Secrets')).toBeNull()
  })
})

describe('§9.2 PARAMETERS row', () => {
  const listParam: ParamDef = {
    name: 'sites', kind: 'list', label: 'Sites', help: 'One link per line', lines: ['a.io'],
  }

  it('a resync while a list row is focused keeps what was typed; blur re-arms it', async () => {
    const { rerender } = render(<ParamRow automationId="a1" p={listParam} last />)
    const input = screen.getByDisplayValue('a.io')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'typed.io' } })
    // the debounced PATCH lands, which drops the pending-write guard…
    await waitFor(() => expect(mockedApi.patchAutomation).toHaveBeenCalledTimes(1))
    // …and the focus guard alone has to hold the server value off the row
    rerender(<ParamRow automationId="a1" p={{ ...listParam, lines: ['b.io'] }} last />)
    expect(screen.getByDisplayValue('typed.io')).toBeTruthy()
    // blurring clears it, so the next server value lands as it always did
    fireEvent.blur(screen.getByDisplayValue('typed.io'))
    rerender(<ParamRow automationId="a1" p={{ ...listParam, lines: ['c.io'] }} last />)
    expect(screen.getByDisplayValue('c.io')).toBeTruthy()
  })
})

// §4.3 trigger status text: enabled app_start and message triggers have no
// computable next occurrence, so the line says what the automation is waiting
// for instead of a countdown.
describe('§9.2 trigger status text', () => {
  const discord: Trigger = {
    id: 't-discord', kind: 'discord', channel: '42', secret: 's1', enabled: true,
    label: 'On Discord message in #ops', short: 'Discord',
  }
  const imessage: Trigger = {
    id: 't-imessage', kind: 'imessage', from: 'Dave', enabled: true,
    label: 'On iMessage from Dave', short: 'iMessage',
  }
  const appStart: Trigger = {
    id: 't-app', kind: 'app_start', enabled: true, label: 'On app start', short: 'app start',
  }
  const cron: Trigger = {
    id: 't-cron', kind: 'cron', expression: '0 8 * * *', source: 'spec', enabled: true,
    label: 'Every day at 8:00 AM', short: 'daily 8:00 AM',
  }

  it('a Discord trigger alone reads as listening for Discord messages', () => {
    seed(auto({ triggers: [discord], triggerChip: 'On Discord message', nextAtMs: null }))
    render(<AutomationDetail />)
    expect(screen.getByText(
      'Listening for Discord messages — executes when a matching message arrives. '
      + 'Execute now and the menu bar still work.')).toBeTruthy()
    // no computable next occurrence, so the chip never carries a countdown
    expect(screen.queryByText(/next in/)).toBeNull()
  })

  it('both message kinds collapse to plain "messages"', () => {
    seed(auto({ triggers: [discord, imessage], triggerChip: '2 triggers', nextAtMs: null }))
    render(<AutomationDetail />)
    expect(screen.getByText(
      'Listening for messages — executes when a matching message arrives. '
      + 'Execute now and the menu bar still work.')).toBeTruthy()
  })

  it('an app_start trigger alone names the next app launch', () => {
    seed(auto({ triggers: [appStart], triggerChip: 'On app start', nextAtMs: null }))
    render(<AutomationDetail />)
    expect(screen.getByText(
      'Executes when this app next starts — Execute now and the menu bar still work.')).toBeTruthy()
  })

  it('an enabled schedule with no computable next never leaves a dangling countdown', () => {
    seed(auto({ triggers: [cron], triggerChip: 'Every day at 8:00 AM', nextAtMs: null }))
    render(<AutomationDetail />)
    expect(screen.getByText(
      'No upcoming occurrence — Execute now and the menu bar still work.')).toBeTruthy()
    expect(screen.queryByText(/next in/)).toBeNull()
  })
})

// §9.2 delete: the destructive action states its consequences, and warns when
// a live execution is about to be cancelled by it.
describe('§9.2 delete automation', () => {
  const openDelete = () => {
    fireEvent.click(screen.getByLabelText('Automation actions'))
    fireEvent.click(screen.getByText('Delete automation…'))
  }

  it('spells out what goes and what stays, then deletes on confirm', async () => {
    seed(auto())
    render(<AutomationDetail />)
    openDelete()

    expect(screen.getByText('Delete this automation?')).toBeTruthy()
    // the body names the automation, then what goes and what stays
    expect(screen.getByRole('alertdialog', { name: 'Delete this automation?' }).textContent)
      .toContain('Job will be deleted — its triggers stop, and its versions and memory go with it.'
        + ' Past results stay in Executions.')
    // nothing live, so no cancellation warning
    expect(screen.queryByText('An execution is in progress — deleting cancels it.')).toBeNull()

    fireEvent.click(screen.getByText('Delete automation'))
    finishModalAnim('Delete this automation?')
    await waitFor(() => expect(mockedApi.deleteAutomation).toHaveBeenCalledWith('a1'))
    expect(storeMod.useStore.getState().page).toBe('automations')
  })

  it('warns in amber when an execution is in progress', () => {
    seed(auto({ live: ['e1'] }))
    render(<AutomationDetail />)
    openDelete()
    expect(screen.getByText('An execution is in progress — deleting cancels it.')).toBeTruthy()
  })
})

// §4.4 draft banner: a kept edit session announces itself on the detail page,
// and Discard is the one that throws it away.
describe('§9.2 draft banner', () => {
  it('names the version the draft was based on', () => {
    seed(auto({ version: 1, draft: { spec: null } }))
    render(<AutomationDetail />)
    expect(screen.getByText(
      'Unsaved edit based on v1 — kept from your last edit session. '
      + 'Resume editing to keep working on it.')).toBeTruthy()
  })

  it('Discard deletes the draft and says the version is unchanged', async () => {
    seed(auto({ version: 1, draft: { spec: null } }))
    render(<AutomationDetail />)
    fireEvent.click(screen.getByText('Discard'))
    await waitFor(() => expect(mockedApi.deleteDraft).toHaveBeenCalledWith('a1'))
    await waitFor(() => expect(storeMod.useStore.getState().toast)
      .toBe('Draft discarded — v1 is unchanged.'))
  })

  it('is absent without a kept draft', () => {
    seed(auto())
    render(<AutomationDetail />)
    expect(screen.queryByText(/Unsaved edit based on/)).toBeNull()
  })
})

// §9.2 PARAMETERS row plumbing: the debounce must never swallow the last
// keystroke, and an optimistic toggle must not outlive a failed PATCH.
describe('§9.2 PARAMETERS row writes', () => {
  const textParam: ParamDef = {
    name: 'subject', kind: 'text', label: 'Subject', help: 'Email subject', value: '',
  }
  const toggleParam: ParamDef = {
    name: 'notify', kind: 'toggle', label: 'Notify', help: 'Send a summary', on: false,
  }

  it('unmounting before the debounce fires still saves what was typed', () => {
    vi.useFakeTimers()
    try {
      const { unmount } = render(<ParamRow automationId="a1" p={textParam} last />)
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Daily digest' } })
      expect(mockedApi.patchAutomation).not.toHaveBeenCalled()

      unmount()
      expect(mockedApi.patchAutomation).toHaveBeenCalledTimes(1)
      expect(mockedApi.patchAutomation).toHaveBeenCalledWith(
        'a1', { paramValues: { subject: 'Daily digest' } })
    } finally {
      vi.useRealTimers()
    }
  })

  it('a rejected toggle PATCH rolls the switch back and toasts the reason', async () => {
    mockedApi.patchAutomation.mockRejectedValueOnce(new Error('backend is restarting'))
    storeMod.useStore.setState({ toast: null })
    render(<ParamRow automationId="a1" p={toggleParam} last />)

    const sw = screen.getByRole('switch')
    fireEvent.click(sw)
    expect(sw.getAttribute('aria-checked')).toBe('true')  // optimistic
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('false'))
    expect(storeMod.useStore.getState().toast).toBe('backend is restarting')
  })
})
