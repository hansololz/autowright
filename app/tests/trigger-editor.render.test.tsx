// Component tests for the §9.2 trigger editor: the one-time kind's segmented
// time entry (paste spill, arrow stepping, the out-of-range preview), the
// interval pair's compose/decompose, and the
// §4.3 payloads each kind saves - one-time `at`, the interval `every`, the
// Discord field set with its
// deduped sender ids, and the timezone the picker rides along. The editor
// renders for real (happy-dom) against the real store with the api module
// mocked; §19 previews come back valid so the save button enables.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../src/api'
import type { SecretMeta, Trigger } from '../src/types'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(() => Promise.reject(new Error('offline'))),
    getAutomation: vi.fn(() => Promise.reject(new Error('offline'))),
    patchAutomation: vi.fn(async () => ({})),
    imessagePermissions: vi.fn(async () => ({ fullDisk: true, automation: 'granted' })),
    // §19 trigger previews: valid results, so the editor's save button enables.
    // The one exception is a half-minute interval - the §4.3 dialect's floor,
    // the invalid reply the interval tests need.
    triggersPreview: vi.fn(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map((t) => (
        t.every === 'PT10S'
          ? { valid: false, error: 'an interval must be at least 15 seconds', label: '', short: '', nextAtMs: null }
          : t.every
          ? { valid: true, label: 'Every 6 hours', short: 'Every 6h', nextAtMs: 1, nextLabel: 'in 6 hours' }
          : { valid: true, label: 'Every day', short: 'Every day', nextAtMs: null }
      )),
    })),
  },
}))

let storeMod: typeof import('../src/store')
let TriggerEditor: typeof import('../src/pages/detail/TriggerEditor').TriggerEditor

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  TriggerEditor = (await import('../src/pages/detail/TriggerEditor')).TriggerEditor
})

const secret = (over: Partial<SecretMeta> = {}): SecretMeta =>
  ({ id: 'sec-1', name: 'DISCORD_TOKEN', description: '', set: true, usedBy: [], ...over })

const onSave = vi.fn()
const editor = (initial?: Trigger) => (
  <TriggerEditor hasAppStart={false} initial={initial} onSave={onSave} onCancel={() => {}} />
)

// The Discord setup guide names an <b>Add</b> step of its own, so the submit
// button is reached by role rather than by its text.
const saveButton = (text: 'Add' | 'Save') =>
  screen.getByRole('button', { name: text }) as HTMLButtonElement

const timePart = (label: 'hours' | 'minutes' | 'seconds') =>
  screen.getByLabelText(label) as HTMLInputElement

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

describe('§9.2 one-time trigger', () => {
  const pickOneTime = () => {
    render(editor())
    fireEvent.click(screen.getByText('One time'))
  }

  it('saves the date and the segmented time as one local `at` stamp', async () => {
    pickOneTime()
    fireEvent.change(document.querySelector('input[type="date"]')!, {
      target: { value: '2026-09-10' },
    })
    fireEvent.change(timePart('hours'), { target: { value: '08' } })
    fireEvent.change(timePart('minutes'), { target: { value: '30' } })
    // §9.2: seconds pre-fill 00, so only hour + minute need typing
    expect(timePart('seconds').value).toBe('00')
    await waitFor(() => expect(saveButton('Add').disabled).toBe(false))
    fireEvent.click(saveButton('Add'))
    expect(onSave).toHaveBeenCalledWith({ kind: 'time', at: '2026-09-10T08:30:00' })
    // §4.3: local time stores no timezone
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('timezone')
  })

  it('a pasted run of digits spills across the three segments', () => {
    pickOneTime()
    fireEvent.focus(timePart('hours'))
    fireEvent.change(timePart('hours'), { target: { value: '083015' } })
    expect(timePart('hours').value).toBe('08')
    expect(timePart('minutes').value).toBe('30')
    expect(timePart('seconds').value).toBe('15')
  })

  it('arrow stepping wraps at the top and starts an empty segment at 00', () => {
    pickOneTime()
    fireEvent.change(timePart('hours'), { target: { value: '23' } })
    fireEvent.keyDown(timePart('hours'), { key: 'ArrowUp' })
    expect(timePart('hours').value).toBe('00')
    fireEvent.keyDown(timePart('minutes'), { key: 'ArrowDown' })
    expect(timePart('minutes').value).toBe('00')
  })

  it('an out-of-range hour states the ranges and holds the save button', () => {
    pickOneTime()
    fireEvent.change(timePart('hours'), { target: { value: '25' } })
    expect(screen.getByText('Hours go 0–23, minutes and seconds 0–59')).toBeTruthy()
    expect(saveButton('Add').disabled).toBe(true)
  })
})

describe('§9.2 Discord trigger', () => {
  it('shows a deleted secret as its id prefix, never as a name', () => {
    render(editor({
      id: 't1', kind: 'discord', channel: '123', secret: 'sec-abcdefgh-1',
      enabled: true, label: 'On Discord message', short: 'Discord',
    }))
    expect(screen.getByText('sec-abcd…')).toBeTruthy()
  })

  it('saves the channel, the chosen secret and deduped sorted sender ids', () => {
    storeMod.useStore.setState({ secrets: [secret()] })
    render(editor())
    fireEvent.click(screen.getByText('Discord'))
    fireEvent.change(screen.getByPlaceholderText(/^Channel id/), { target: { value: '123' } })
    fireEvent.click(screen.getByTitle('The secret holding your Discord bot token'))
    fireEvent.click(screen.getByText('DISCORD_TOKEN'))
    fireEvent.change(screen.getByPlaceholderText(/^Sender filter/), { target: { value: '7, 7, 5' } })
    // §9.2: mention-only is the default for a new trigger
    expect((screen.getByLabelText('Only when the bot is mentioned') as HTMLInputElement).checked)
      .toBe(true)
    expect(saveButton('Add').disabled).toBe(false)
    fireEvent.click(saveButton('Add'))
    expect(onSave).toHaveBeenCalledWith({
      kind: 'discord', channel: '123', secret: 'sec-1', mention: true, author: ['5', '7'],
    })
    // §4.3: the optional message filter stays out of the payload while empty
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('pattern')
  })
})

describe('§9.2 timezone picker', () => {
  const cronEditor = () => {
    render(editor())
    fireEvent.change(
      screen.getByPlaceholderText(/^0 8 \* \* \*/), { target: { value: '0 8 * * *' } })
    fireEvent.click(screen.getByTitle("Timezone the trigger's times read in"))
  }

  it('a picked zone rides the saved trigger', async () => {
    cronEditor()
    fireEvent.change(screen.getByPlaceholderText('Filter timezones…'), { target: { value: 'berl' } })
    fireEvent.click(screen.getByText('Europe/Berlin'))
    await waitFor(() => expect(saveButton('Add').disabled).toBe(false))
    fireEvent.click(saveButton('Add'))
    expect(onSave).toHaveBeenCalledWith({
      kind: 'cron', expression: '0 8 * * *', source: 'user', timezone: 'Europe/Berlin',
    })
  })

  it('a filter matching nothing says so', () => {
    cronEditor()
    fireEvent.change(screen.getByPlaceholderText('Filter timezones…'), { target: { value: 'zzz' } })
    expect(screen.getByText('No timezone matches.')).toBeTruthy()
  })
})

describe('§9.2 interval trigger', () => {
  const pickInterval = () => {
    render(editor())
    fireEvent.click(screen.getByText('Interval'))
  }
  const amountInput = () => screen.getByLabelText('interval amount') as HTMLInputElement
  const unitPill = () => screen.getByTitle('The unit the interval counts in')
  // the pill wears the chosen unit word too, so the menu's row is the later node
  const pickUnit = (unit: string) => {
    fireEvent.click(unitPill())
    fireEvent.click(screen.getAllByText(unit).at(-1)!)
  }
  const stored = (every: string): Trigger => ({
    id: 't1', kind: 'interval', every, source: 'user', enabled: true,
    label: 'Every 6 hours', short: 'Every 6h',
  })

  it('opens on 1 hours and saves the composed §4.3 duration', async () => {
    pickInterval()
    expect(amountInput().value).toBe('1')
    expect(unitPill().textContent).toContain('hours')
    // §4.3: an interval has no wall clock, so the form has no timezone picker
    expect(screen.queryByTitle("Timezone the trigger's times read in")).toBeNull()
    fireEvent.change(amountInput(), { target: { value: '6' } })
    pickUnit('hours')
    await waitFor(() => expect(saveButton('Add').disabled).toBe(false))
    expect(vi.mocked(api.triggersPreview).mock.calls.at(-1)![0]).toEqual([
      { kind: 'interval', every: 'PT6H', source: 'user' },
    ])
    expect(screen.getByText('Every 6 hours · next: in 6 hours')).toBeTruthy()
    fireEvent.click(saveButton('Add'))
    // §4.3: runIfMissed is carried only when off, like the cron arm
    expect(onSave).toHaveBeenCalledWith({ kind: 'interval', every: 'PT6H', source: 'user' })
  })

  it('unchecking "Catch up if missed" stores the §4.3 opt-out', async () => {
    pickInterval()
    fireEvent.click(screen.getByLabelText('Catch up if missed'))
    await waitFor(() => expect(saveButton('Add').disabled).toBe(false))
    fireEvent.click(saveButton('Add'))
    expect(onSave).toHaveBeenCalledWith({
      kind: 'interval', every: 'PT1H', source: 'user', runIfMissed: false,
    })
  })

  it('a duration the preview rejects reddens the amount and holds Add', async () => {
    pickInterval()
    fireEvent.change(amountInput(), { target: { value: '10' } })
    pickUnit('seconds')
    await waitFor(() =>
      expect(screen.getByText('an interval must be at least 15 seconds')).toBeTruthy())
    expect(amountInput().className).toContain('invalid')
    expect(saveButton('Add').disabled).toBe(true)
  })

  it('an empty amount reddens the input and holds Add, with nothing to preview', () => {
    pickInterval()
    fireEvent.change(amountInput(), { target: { value: '' } })
    expect(amountInput().className).toContain('invalid')
    expect(saveButton('Add').disabled).toBe(true)
  })

  it('an edit swap decomposes the stored canonical duration into the pair', () => {
    const { unmount } = render(editor(stored('PT90S')))
    expect(amountInput().value).toBe('90')
    expect(unitPill().textContent).toContain('seconds')
    unmount()
    render(editor(stored('P1D')))
    expect(amountInput().value).toBe('1')
    expect(unitPill().textContent).toContain('days')
  })
})
