// Regression tests for the 2026-10-30 second-sweep editor audit
// (src/pages/CreateFlow.tsx, src/find.tsx, src/pages/createflow/useDraftJob.ts,
// src/pages/createflow/DocEditorModal.tsx, src/steps.tsx,
// src/pages/createflow/SectionCards.tsx): an IME composition owns Enter and
// Escape in the title, description and find fields; a lost poll or a failed
// POST settles the live block it was showing instead of removing it; the
// document editor ignores Save and Escape during its exit; a sync swap asks
// before dropping a typed remember rule; and a New secret lands in the store
// list at once.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Agent, Automation, SecretMeta, Step } from '../src/types'
import { StepList, type StepEditing } from '../src/steps'
import { DocEditorModal } from '../src/pages/createflow/DocEditorModal'

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
const cardOf = (el: Element): HTMLElement => el.closest('.ad-card') as HTMLElement
const spinnersIn = (el: Element) =>
  [...el.querySelectorAll('span')].filter((s) => ((s as HTMLElement).style.animation || '').includes('adSpin'))

describe('U1: an IME composition owns Enter and Escape', () => {
  it('the title rename neither commits nor discards mid-composition', () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByTitle('Rename'))
    const input = screen.getByDisplayValue('My auto')
    fireEvent.change(input, { target: { value: 'Half composed' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('Half composed')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
    expect(screen.getByDisplayValue('Half composed')).toBeTruthy()
    // once the composition ends, Enter commits as always
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', { name: 'Half composed' })
  })

  it('the description edit neither commits nor discards mid-composition', () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByTitle('Edit the description'))
    const input = screen.getByDisplayValue('Old lede')
    fireEvent.change(input, { target: { value: 'New lede' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue('New lede')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
    expect(screen.getByDisplayValue('New lede')).toBeTruthy()
  })

  it('the find bar neither steps nor closes mid-composition', () => {
    const steps: Step[] = [{ name: 'Fetch', description: '', code: 'log("a")\nlog("b")', file: '01-a.py' }]
    const editing: StepEditing = { disabled: false, validate: async () => [], commit: () => {}, addRef: { current: null } }
    render(<StepList variant="editor" steps={steps} availAgents={AGENTS} allAgents={AGENTS} secrets={[]} packages={[]} editing={editing} />)
    fireEvent.click(screen.getAllByText('Fetch')[0])
    fireEvent.click(screen.getByLabelText('Find in script'))
    const input = screen.getByPlaceholderText('Find in script') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'log' } })
    expect(screen.getByTestId('find-counter').textContent).toBe('1 of 2')
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(screen.getByTestId('find-counter').textContent).toBe('1 of 2')
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true })
    expect(screen.getByPlaceholderText('Find in script')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('find-counter').textContent).toBe('2 of 2')
  })
})

describe('U2: a lost poll or a failed POST settles the shown block', () => {
  it('a chat poll that never answered settles the pre-poll block and says the job was lost', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(async () => { throw new Error('backend gone') })
    render(<CreateFlow />)
    send('hello')
    await waitFor(() => expect(within(thread()).getByText('The job was lost: backend gone')).toBeTruthy(), { timeout: 8000 })
    expect(within(thread()).getByText('Working on the request…')).toBeTruthy()
    expect(within(thread()).getByText('• Choosing what to do')).toBeTruthy()
    expect(spinnersIn(thread()).length).toBe(0)
  })

  it('a chat poll that gives up after a stage flip settles every shown stage', async () => {
    let calls = 0
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      calls += 1
      if (calls === 1) {
        return {
          id: 'j1', status: 'building', stage: 'Updating the documents', detail: null, error: null,
          mode: 'chat', draft: null, events: [{ time: 1, text: 'Rewriting the spec…', stage: 'Updating the documents' }],
        }
      }
      throw new Error('backend gone')
    })
    render(<CreateFlow />)
    send('hello')
    await waitFor(() => expect(within(thread()).getByText('The job was lost: backend gone')).toBeTruthy(), { timeout: 8000 })
    const t = thread()
    const neutral = within(t).getByText('Working on the request…')
    const documents = within(t).getByText('Updating the documents…')
    expect(neutral.compareDocumentPosition(documents) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(t).getByText('• Rewriting the spec…')).toBeTruthy()
    expect(spinnersIn(t).length).toBe(0)
  })

  it('a chat POST error settles the live block above the error entry', async () => {
    ;(mockedApi.postDraftJob as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('the backend refused'))
    render(<CreateFlow />)
    send('hello')
    await waitFor(() => expect(within(thread()).getByText('the backend refused')).toBeTruthy())
    const block = within(thread()).getByText('Working on the request…')
    const error = within(thread()).getByText('the backend refused')
    expect(block.compareDocumentPosition(error) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a sync POST error settles the live sync block', async () => {
    ;(mockedApi.postDraftJob as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('the backend refused'))
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('the backend refused'))
    expect(within(thread()).getByText('Syncing the workflow…')).toBeTruthy()
    expect(within(thread()).getByText('• Building the steps from the spec')).toBeTruthy()
  })
})

describe('U3: the document editor ignores Save and Escape during its exit', () => {
  it('Save and ⌘S after "Discard edits" never save the discarded text', async () => {
    const onSave = vi.fn()
    const onDiscard = vi.fn()
    render(<DocEditorModal kind="spec" text="changed" original="original" onChange={() => {}} onSave={onSave} onDiscard={onDiscard} />)
    const card = screen.getByTestId('doc-editor')
    fireEvent.click(within(card).getByText('Cancel'))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Discard edits'))
    // the confirm's own exit runs first; its settle starts the card's exit
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    fireEvent.click(within(card).getByText('Save'))
    await waitFor(() => expect(onDiscard).toHaveBeenCalledTimes(1))
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)) })
    expect(onSave).not.toHaveBeenCalled()
  })

  it('Escape during a Save exit raises no discard confirm', async () => {
    const onSave = vi.fn()
    render(<DocEditorModal kind="notes" text="changed" original="original" onChange={() => {}} onSave={onSave} onDiscard={() => {}} />)
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Save'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
  })
})

describe('U4: a sync swap asks before dropping a typed remember rule', () => {
  it('a rule with no field change raises the discard confirm', () => {
    const editing: StepEditing = { disabled: false, validate: async () => [], commit: () => {}, addRef: { current: null } }
    const first: Step[] = [{ name: 'Fetch', description: '', code: 'log("a")', file: '01-a.py' }]
    const { rerender } = render(<StepList variant="editor" steps={first} availAgents={AGENTS} allAgents={AGENTS} secrets={[]} packages={[]} editing={editing} />)
    fireEvent.click(screen.getAllByText('Fetch')[0])
    fireEvent.click(screen.getByLabelText('Edit script'))
    fireEvent.click(screen.getByLabelText('Remember this edit as a build rule'))
    fireEvent.change(screen.getByLabelText('Build rule'), { target: { value: 'keep the log line' } })
    const swapped: Step[] = [{ name: 'Fetch', description: '', code: 'log("synced")', file: '01-a.py' }]
    rerender(<StepList variant="editor" steps={swapped} availAgents={AGENTS} allAgents={AGENTS} secrets={[]} packages={[]} editing={editing} />)
    expect(screen.getByRole('alertdialog').getAttribute('aria-label')).toBe('Discard your script edits?')
    expect((screen.getByLabelText('Build rule') as HTMLInputElement).value).toBe('keep the log line')
  })
})

describe('U5: a New secret from the editor lands in the store list at once', () => {
  it('the allowed count and the new row are right before any /state refresh', async () => {
    render(<CreateFlow />)
    const card = () => cardOf(screen.getByText('SECRETS · ALLOWED FOR STEPS'))
    expect(within(card()).getByText('2 of 2 allowed')).toBeTruthy()
    fireEvent.click(within(card()).getByRole('button', { name: /New secret/ }))
    fireEvent.change(screen.getByPlaceholderText(/A short name, like MAIL_PASSWORD/), { target: { value: 'NEW_KEY' } })
    const save = within(screen.getByRole('dialog')).getAllByRole('button').find((b) => /^Save/.test(b.textContent ?? ''))!
    fireEvent.click(save)
    await waitFor(() => expect(mockedApi.createSecret).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(within(card()).getByText('3 of 3 allowed')).toBeTruthy())
    expect(storeMod.useStore.getState().secrets.map((s) => s.name)).toContain('NEW_KEY')
    expect(within(card()).getAllByText('NEW_KEY').some((el) => el.closest('[role="checkbox"]'))).toBe(true)
    expect(mockedApi.state).not.toHaveBeenCalled()
  })
})
