// §9.2 "Catch up if missed": the §4.3 runIfMissed surface: the NO CATCH-UP
// badge a cron/interval/time row wears while the opt-out is stored, and the editor
// checkbox that sets it (checked by default, `runIfMissed: false` on save only
// when unchecked). Both render for real (happy-dom) against the real store
// with the api module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Automation, Trigger } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'granted' })),
    // §19 trigger previews: valid results, so the editor's save button enables
    triggersPreview: vi.fn(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map((t) => ({
        valid: true, label: String(t.expression ?? t.at ?? t.kind),
        short: String(t.kind), nextAtMs: null,
      })),
    })),
  },
}))

let storeMod: typeof import('../src/store')
let TriggersCard: typeof import('../src/pages/detail/TriggersCard').TriggersCard
let TriggerEditor: typeof import('../src/pages/detail/TriggerEditor').TriggerEditor

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  TriggersCard = (await import('../src/pages/detail/TriggersCard')).TriggersCard
  TriggerEditor = (await import('../src/pages/detail/TriggerEditor')).TriggerEditor
})

const trigger = (over: Partial<Trigger> = {}): Trigger => ({
  id: 't1', kind: 'cron', expression: '0 8 * * *', source: 'user', enabled: true,
  label: 'Daily at 8:00', short: 'Daily 8:00', ...over,
} as Trigger)

const auto = (triggers: Trigger[]): Automation => ({
  id: 'a1', name: 'Job', description: '', version: 1, triggers, triggerChip: 'Daily 8:00',
  allTriggersOff: false, nextAtMs: null, notes: '', lastStatus: 'succeeded',
  live: [], maxParallel: 1, maxQueued: 0, resultChip: null, resultStatus: null,
  lastExecutionLabel: 'Today', agentId: null, stepAgents: [], allowedSecrets: [], problems: [],
  unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
})

const onSave = vi.fn()
const editor = (initial?: Trigger) => (
  <TriggerEditor hasAppStart={false} initial={initial} onSave={onSave} onCancel={() => {}} />
)

const saveButton = (text: 'Add' | 'Save') =>
  screen.getByText(text).closest('button') as HTMLButtonElement

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({
    secrets: [],
    platformCapabilities: {
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
    },
  })
})
afterEach(() => cleanup())

describe('§9.2 NO CATCH-UP badge', () => {
  it('marks every cron/interval/time row that stored the opt-out, and no other', () => {
    render(<TriggersCard
      auto={auto([
        trigger({ id: 't1', runIfMissed: false }),
        trigger({ id: 't2', runIfMissed: true }),
        trigger({ id: 't3' }),
        trigger({ id: 't4', kind: 'time', at: '2999-01-01T09:00', runIfMissed: false }),
        trigger({ id: 't5', kind: 'interval', every: 'PT6H', runIfMissed: false }),
        trigger({ id: 't6', kind: 'interval', every: 'P1D' }),
      ])}
      statusText="Next: tomorrow"
    />)
    expect(screen.getAllByText('NO CATCH-UP').length).toBe(3)
  })

  it('is absent when nothing opted out: true and the absent default read alike', () => {
    render(<TriggersCard
      auto={auto([trigger({ id: 't1', runIfMissed: true }), trigger({ id: 't2' })])}
      statusText="Next: tomorrow"
    />)
    expect(screen.queryByText('NO CATCH-UP')).toBeNull()
  })
})

describe('§9.2 "Catch up if missed" checkbox', () => {
  it('a new trigger starts checked, and the one sleep note follows the checkbox', () => {
    render(editor())
    const box = screen.getByLabelText('Catch up if missed') as HTMLInputElement
    expect(box.checked).toBe(true)
    // §9.2: a single note - the state sentence, then the §9 sleepMissNote tail
    expect(screen.getByText(/^If this .* sleeps through a scheduled time, the automation executes once when it wakes\. This is not an issue on .*, but .* will not fire on schedule\.$/))
      .toBeTruthy()
    fireEvent.click(box)
    expect(screen.getByText(/^If this .* sleeps through a scheduled time, that time is skipped\. This is not an issue on/))
      .toBeTruthy()
    expect(screen.queryByText(/executes once when it wakes/)).toBeNull()
  })

  it('editing a stored trigger shows its choice: unchecked only for the opt-out', () => {
    const { unmount } = render(editor(trigger({ runIfMissed: false })))
    expect((screen.getByLabelText('Catch up if missed') as HTMLInputElement).checked).toBe(false)
    unmount()
    render(editor(trigger()))
    expect((screen.getByLabelText('Catch up if missed') as HTMLInputElement).checked).toBe(true)
  })

  it('saving unchecked sends runIfMissed false; checked omits the key entirely', async () => {
    const { unmount } = render(editor(trigger({ runIfMissed: false })))
    await waitFor(() => expect(saveButton('Save').disabled).toBe(false))
    fireEvent.click(saveButton('Save'))
    expect(onSave).toHaveBeenCalledWith({
      kind: 'cron', expression: '0 8 * * *', source: 'user', runIfMissed: false,
    })

    onSave.mockClear()
    unmount()
    render(editor(trigger({ runIfMissed: false })))
    fireEvent.click(screen.getByLabelText('Catch up if missed'))
    await waitFor(() => expect(saveButton('Save').disabled).toBe(false))
    fireEvent.click(saveButton('Save'))
    expect(onSave).toHaveBeenCalledWith({
      kind: 'cron', expression: '0 8 * * *', source: 'user',
    })
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('runIfMissed')
  })

  it('the kind picker keeps it to the schedule and one-time kinds', () => {
    render(editor())
    for (const kind of ['Interval', 'One time']) {
      fireEvent.click(screen.getByText(kind))
      expect(screen.getByLabelText('Catch up if missed')).toBeTruthy()
    }
    for (const kind of ['App start', 'Discord', 'iMessage']) {
      fireEvent.click(screen.getByText(kind))
      expect(screen.queryByLabelText('Catch up if missed')).toBeNull()
    }
  })
})
