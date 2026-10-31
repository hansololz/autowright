// Regression tests for the 2026-10-31 editor audit (src/find.tsx, src/ui.tsx,
// src/steps.tsx, src/pages/createflow/useDraftJob.ts): an IME composition
// owns Escape in the find bar and a plain Modal (§14); a step timeout above
// the §4.1 one-year cap is invalid; a chat start request that fails after the
// cancel adds nothing; and the pressed move control keeps keyboard focus in
// the step's new row (§11).
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import type { Agent, Automation, SecretMeta, Step } from '../src/types'
import { StepList, type StepEditing } from '../src/steps'
import { Modal, parseStepTimeout, STEP_TIMEOUT_MAX_S } from '../src/ui'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    instructions: vi.fn(async () => ({ framework: '# Framework', build: '- build rules' })),
    postDraftJob: vi.fn(async () => ({ jobId: 'j1' })),
    validateSteps: vi.fn(async () => ({ errors: [] })),
    patchAutomation: vi.fn(async () => ({})),
    getDraftJob: vi.fn(() => new Promise(() => { /* poll never answers in tests */ })),
    cancelDraftJob: vi.fn(async () => ({})),
    ackDraftJob: vi.fn(async () => ({})),
    putDraft: vi.fn(async () => ({})),
    deleteDraft: vi.fn(async () => ({})),
    getDraft: vi.fn(async () => ({ draft: null, agentId: null })),
    openDraft: vi.fn(async () => ({})),
    getChat: vi.fn(async () => ({ chat: [] })),
    putChat: vi.fn(async () => ({})),
    checkPackages: vi.fn(async () => ({ packages: [] })),
    outdatedPackages: vi.fn(async () => ({ packages: [] })),
    postTest: vi.fn(async () => ({ executionId: 'e1' })),
    cancelExecution: vi.fn(async () => ({})),
    skipStep: vi.fn(async () => ({})),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(async () => ({ lines: [] })),
    analyzeExec: vi.fn(async () => ({})),
    getAutomation: vi.fn(async () => ({})),
    versionDiff: vi.fn(async () => ({ from: 1, to: 2, files: [] })),
    saveVersion: vi.fn(async () => ({ version: 2 })),
    createAutomation: vi.fn(async () => ({ id: 'a2' })),
    createSecret: vi.fn(async () => ({ id: '33333333-3333-3333-3333-333333333333', name: 'NEW_KEY', description: '', set: true, usedBy: [] })),
    state: vi.fn(async () => ({})),
    triggersPreview: vi.fn(async (triggers: Array<Record<string, unknown>>) => ({
      triggers: triggers.map((t) => ({
        valid: true, label: String(t.expression ?? t.channel ?? t.from ?? t.kind),
        short: String(t.kind), nextAtMs: null,
      })),
    })),
  },
}))

let storeMod: typeof import('../src/store')
let CreateFlow: typeof import('../src/pages/CreateFlow').default
let mockedApi: typeof import('../src/api').api

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  CreateFlow = (await import('../src/pages/CreateFlow')).default
  mockedApi = (await import('../src/api')).api
})

const AGENTS: Agent[] = [
  { id: 'g1', name: 'Cloud writer', harness: 'Claude Code', mode: 'default', model: null, default: true },
]
const MAIL_ID = '11111111-1111-1111-1111-111111111111'
const CRM_ID = '22222222-2222-2222-2222-222222222222'
const SECRETS: SecretMeta[] = [
  { id: MAIL_ID, name: 'MAIL_PASSWORD', description: '', set: true, usedBy: [] },
  { id: CRM_ID, name: 'CRM_API_KEY', description: '', set: true, usedBy: [] },
]
const AUTO = {
  id: 'a1', name: 'My auto', description: 'Old lede', version: 1,
  triggers: [], triggerChip: 'No triggers', allTriggersOff: false, nextAtMs: null,
  lastStatus: 'none', live: [], resultChip: null, resultStatus: null, lastExecutionLabel: '',
  agentId: 'g1', stepAgents: ['g1'], allowedSecrets: [MAIL_ID, CRM_ID], problems: [],
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true },
  specMeta: '', params: [],
  steps: [{ file: '01-a.py', name: 'Fetch pages', description: '', code: 'log("a")' }],
  spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Does things.' }],
  packages: [], versions: [], draft: null,
} as unknown as Automation

beforeEach(() => {
  vi.clearAllMocks()
  ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ chat: [] }))
  ;(mockedApi.getDraft as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ draft: null, agentId: null }))
  ;(mockedApi.postDraftJob as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ jobId: 'j1' }))
  ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => { /* never answers */ }))
  ;(mockedApi.getAutomation as ReturnType<typeof vi.fn>).mockImplementation(async () => storeMod.useStore.getState().automations[0] ?? {})
  storeMod.useStore.setState({
    surface: 'create', createFrom: 'edit', page: 'automations', automationId: 'a1',
    automations: [AUTO], agents: AGENTS, secrets: SECRETS,
    executions: [], executionFull: {}, execLogs: {}, toast: null, test: null,
  })
})
afterEach(() => cleanup())

const thread = () => screen.getByTestId('chat-thread')
const send = (text: string) => {
  fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'), { target: { value: text } })
  fireEvent.click(screen.getByText('Send'))
}
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })

describe('U1: an IME composition owns Escape (§14)', () => {
  it('a composing Escape in the find bar leaves the step modal and the find bar open', async () => {
    const steps: Step[] = [{ name: 'Fetch', description: '', code: 'log("a")\nlog("b")', file: '01-a.py' }]
    const editing: StepEditing = { disabled: false, validate: async () => [], commit: () => {}, addRef: { current: null } }
    render(<StepList variant="editor" steps={steps} availAgents={AGENTS} allAgents={AGENTS} secrets={[]} packages={[]} editing={editing} />)
    fireEvent.click(screen.getAllByText('Fetch')[0])
    fireEvent.click(screen.getByLabelText('Find in script'))
    const input = screen.getByPlaceholderText('Find in script') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'log' } })
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
    await settle()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByPlaceholderText('Find in script')).toBeTruthy()
  })

  it('a composing Escape does not close a plain Modal', async () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose} ariaLabel="Plain">{() => <div>body</div>}</Modal>)
    fireEvent.keyDown(document, { key: 'Escape', isComposing: true })
    await settle()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})

describe('E1: the step timeout is capped at one year (§4.1)', () => {
  it('9000h is invalid and 8760h is valid', () => {
    expect(STEP_TIMEOUT_MAX_S).toBe(31536000)
    expect(parseStepTimeout('9000h')).toBeNull()
    expect(parseStepTimeout('8760h')).toEqual({ timeout: 31536000 })
    expect(parseStepTimeout('31536001')).toBeNull()
  })
})

describe('U2: a start request that fails after the cancel adds nothing (§11)', () => {
  it('a cancelled chat whose POST then rejects adds no failed block and no error line', async () => {
    let reject: (e: Error) => void = () => {}
    ;(mockedApi.postDraftJob as ReturnType<typeof vi.fn>).mockImplementationOnce(() => new Promise((_resolve, rej) => { reject = rej }))
    render(<CreateFlow />)
    send('hello')
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.getByText('Send')).toBeTruthy()
    await act(async () => { reject(new Error('the backend refused')) })
    await settle()
    expect(within(thread()).queryByText('the backend refused')).toBeNull()
    expect(within(thread()).queryByText('Working on the request…')).toBeNull()
  })
})

describe('U5: the pressed move control keeps keyboard focus (§11)', () => {
  function Harness({ initial }: { initial: Step[] }) {
    const [steps, setSteps] = useState(initial)
    const editing: StepEditing = { disabled: false, validate: async () => [], commit: (next) => setSteps(next), addRef: { current: null } }
    return <StepList variant="editor" steps={steps} availAgents={AGENTS} allAgents={AGENTS} secrets={[]} packages={[]} editing={editing} />
  }

  it('after Move down, focus is on the Move down button in the new row', () => {
    render(<Harness initial={[
      { name: 'A', description: '', code: 'a', file: '01-a.py' },
      { name: 'B', description: '', code: 'b', file: '02-b.py' },
      { name: 'C', description: '', code: 'c', file: '03-c.py' },
      { name: 'D', description: '', code: 'd', file: '04-d.py' },
    ]} />)
    fireEvent.click(screen.getAllByText('A')[0])
    fireEvent.click(screen.getByLabelText('Edit script'))
    const control = (label: string) => within(screen.getByTestId('step-controls')).getByLabelText(label) as HTMLButtonElement
    control('Move step down').focus()
    fireEvent.click(control('Move step down'))
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Step 2 of 4: A')
    expect(document.activeElement).toBe(control('Move step down'))
    // a second press moves again
    fireEvent.click(document.activeElement as HTMLButtonElement)
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Step 3 of 4: A')
    expect(document.activeElement).toBe(control('Move step down'))
    // a control pressed without focus (the mouse case in jsdom) is not refocused:
    // the 2026-10-30 rule keeps focus where it was. Tab to it first.
    control('Move step up').focus()
    fireEvent.click(control('Move step up'))
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Step 2 of 4: A')
    expect(document.activeElement).toBe(control('Move step up'))
    // landing at an end: the pressed control is disabled there, so focus goes
    // to the opposite move control instead of the body
    fireEvent.click(document.activeElement as HTMLButtonElement)
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Step 1 of 4: A')
    expect(control('Move step up').disabled).toBe(true)
    expect(document.activeElement).toBe(control('Move step down'))
  })
})
