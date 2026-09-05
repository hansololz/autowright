// Component tests for the §11 create/edit page: grant checkboxes keeping
// unchecked agents/secrets out of every drafting-job payload (§8), the BUILD
// and TEST cards with the test-run modal, blockers thread entries, applying
// chat responses, the footer action block, and the left-column cards.
// CreateFlow renders for real
// (happy-dom) in edit mode with the store seeded and the api module mocked;
// payload assertions read the exact POST /drafts bodies.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { Agent, Automation, SecretMeta } from '../src/types'
// §11 stale-outcome rule: the card's own hash, so the assertions never restate it
import { stepsFingerprint } from '../src/pages/createflow/model'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    instructions: vi.fn(async () => ({ framework: '# Framework', defaultBuild: '- rules' })),
    postDraftJob: vi.fn(async () => ({ jobId: 'j1' })),
    patchAutomation: vi.fn(async () => ({})),
    getDraftJob: vi.fn(() => new Promise(() => { /* poll never answers in tests */ })),
    cancelDraftJob: vi.fn(async () => ({})),
    // §19 background continuation: the editor consumes settled outcomes
    ackDraftJob: vi.fn(async () => ({})),
    // §19 one draft-container surface (owner = automation id | 'pending')
    putDraft: vi.fn(async () => ({})),
    deleteDraft: vi.fn(async () => ({})),
    getDraft: vi.fn(async () => ({ draft: null, agentId: null })),
    openDraft: vi.fn(async () => ({})),
    // §19/§4.4 chat-thread surface — the thread outlives the draft
    getChat: vi.fn(async () => ({ chat: [] })),
    putChat: vi.fn(async () => ({})),
    checkPackages: vi.fn(async () => ({ packages: [] })),
    outdatedPackages: vi.fn(async () => ({ packages: [] })),
    postTest: vi.fn(async () => ({ executionId: 'e1' })),
    // §7 run controls the test-run modal drives on the tracked record
    cancelExecution: vi.fn(async () => ({})),
    skipStep: vi.fn(async () => ({})),
    getExecution: vi.fn(() => Promise.reject(new Error('offline'))),
    getExecutionLogs: vi.fn(async () => ({ lines: [] })),
    analyzeExec: vi.fn(async () => ({})),
    getAutomation: vi.fn(async () => ({})),
    // §4.4/§19 delete an old version (editor version menu)
    deleteVersion: vi.fn(async () => ({ automation: {} })),
    state: vi.fn(async () => ({})),
    // §19 trigger previews — labels echo enough shape for the chip/tab renders
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
  { id: 'g2', name: 'Fast local', harness: 'OpenCode', mode: 'ollama', model: 'qwen3:8b' },
]
// §4.8: secrets carry uuids — grants and step references key on them
const MAIL_ID = '11111111-1111-1111-1111-111111111111'
const CRM_ID = '22222222-2222-2222-2222-222222222222'
const SECRETS: SecretMeta[] = [
  { id: MAIL_ID, name: 'MAIL_PASSWORD', description: '', set: true, usedBy: [] },
  { id: CRM_ID, name: 'CRM_API_KEY', description: '', set: true, usedBy: [] },
]
const AUTO = {
  id: 'a1', name: 'My auto', description: '', version: 1,
  triggers: [], triggerChip: 'No triggers', allTriggersOff: false, nextAtMs: null,
  instructions: '- keep it simple',
  lastStatus: 'none', live: [], resultChip: null, resultStatus: null, lastExecutionLabel: '',
  agentId: 'g1', stepAgents: ['g1', 'g2'], allowedSecrets: [MAIL_ID, CRM_ID], problems: [],
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true },
  specMeta: '', params: [],
  steps: [{ file: '01-a.py', name: 'Fetch pages', description: '', code: 'log("a")' }],
  spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Does things.' }],
  packages: [], versions: [], draft: null,
} as unknown as Automation

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({
    surface: 'create', createFrom: 'edit', page: 'automations', automationId: 'a1',
    automations: [AUTO], agents: AGENTS, secrets: SECRETS,
    executions: [], executionFull: {}, execLogs: {}, toast: null, test: null,
  })
})
afterEach(() => cleanup())

const draftBody = (call: number) =>
  (mockedApi.postDraftJob as ReturnType<typeof vi.fn>).mock.calls[call][0] as Record<string, unknown>

// The ui.tsx Collapse always keeps its children mounted (happy-dom renders
// them), so "collapsed" is asserted through the .ad-collapse open class.
const collapseOf = (el: Element) => el.closest('.ad-collapse')!
// A review card is the nearest ancestor carrying the §14 .ad-card chrome.
const cardOf = (el: Element): HTMLElement => el.closest('.ad-card') as HTMLElement
// §11 status-aware collapsed lines preview the granted names / first doc line,
// duplicating row text — target the element inside an .ad-hover-row (checklist
// rows), never the preview line (clicking that would toggle the card).
const rowText = (text: string) =>
  screen.getAllByText(text).find((el) => el.closest('.ad-hover-row'))!
// Same ambiguity for markdown card bodies: pick the rendered list item.
const bodyLi = (text: string) =>
  screen.getAllByText(text).find((el) => el.tagName === 'LI')!
// Spinner renders a bare span animated with adSpin — the only way to find it.
const spinnersIn = (el: Element) =>
  [...el.querySelectorAll('span')].filter((s) => ((s as HTMLElement).style.animation || '').includes('adSpin'))
// Reset getDraftJob to the never-answering default before each of the newer
// suites — a prior test's mockResolvedValue would otherwise leak through
// vi.clearAllMocks (which clears calls, not implementations). Also make
// getAutomation echo the seeded automation: the mount-time loadAuto stores its
// response verbatim, and the default `{}` would erase the auto (no id match)
// the moment the test awaits anything.
const armPendingPoll = () => {
  ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(() => new Promise(() => { /* poll never answers */ }))
  ;(mockedApi.getAutomation as ReturnType<typeof vi.fn>).mockImplementation(async () => storeMod.useStore.getState().automations[0] ?? {})
}

const BLOCKED_SYNC = {
  id: 'j1', status: 'blocked', stage: null, detail: null, error: null, draft: null,
  mode: 'sync', blockedAt: 'steps',
  blockers: [{ reason: 'Needs a channel id.', fix: 'Name it in the spec.' }],
}

describe('CreateFlow grant checkboxes → drafting payloads (§8/§11)', () => {
  it('unchecking an agent and a secret keeps both out of the sync job', async () => {
    render(<CreateFlow />)
    expect(screen.getByText('2 of 2 enabled')).toBeTruthy()
    expect(screen.getByText('2 of 2 allowed')).toBeTruthy()

    // §9/§11: each grant row is a role="checkbox" button around the §14 CheckBox glyph
    const agentRow = screen.getByText('Fast local').closest('[role="checkbox"]') as HTMLElement
    expect(agentRow.getAttribute('aria-checked')).toBe('true')
    expect(agentRow.querySelector('.ad-check[data-on]')).toBeTruthy()
    fireEvent.click(screen.getByText('Fast local'))     // uncheck agent g2
    expect(screen.getByText('1 of 2 enabled')).toBeTruthy()
    expect(agentRow.getAttribute('aria-checked')).toBe('false')
    expect(agentRow.querySelector('.ad-check[data-on]')).toBeNull()
    fireEvent.click(screen.getByText('CRM_API_KEY'))    // disallow the secret
    expect(screen.getByText('1 of 2 allowed')).toBeTruthy()

    // grant toggles alone never mark the workflow out of sync (§11) — the
    // panel still offers the on-demand sync
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    const body = draftBody(0)
    expect(body.mode).toBe('sync')
    expect(body.automationId).toBe('a1')
    expect(body.enabledAgents).toEqual(['g1'])                    // g2 gone
    expect(body.allowedSecrets).toEqual([MAIL_ID])        // CRM_API_KEY gone
    // the serialized in-editor draft carries the same trimmed grants
    const current = body.current as { stepAgents: string[]; allowedSecrets: string[] }
    expect(current.stepAgents).toEqual(['g1'])
    expect(current.allowedSecrets).toEqual([MAIL_ID])
  })

  it('unchecking everything sends explicit empty arrays, not missing keys', async () => {
    render(<CreateFlow />)
    fireEvent.click(rowText('Cloud writer'))
    fireEvent.click(rowText('Fast local'))
    fireEvent.click(rowText('MAIL_PASSWORD'))
    fireEvent.click(rowText('CRM_API_KEY'))
    expect(screen.getByText('0 of 2 enabled')).toBeTruthy()
    expect(screen.getByText('0 of 2 allowed')).toBeTruthy()

    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    const body = draftBody(0)
    // §19: [] means "unchecked" — absent keys would fall back to stored grants
    expect(body.enabledAgents).toEqual([])
    expect(body.allowedSecrets).toEqual([])
    expect('enabledAgents' in body && 'allowedSecrets' in body).toBe(true)
  })

  it('the chat job carries the live checkbox state too', async () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('CRM_API_KEY'))    // disallow one secret

    const input = screen.getByPlaceholderText('Change something, or ask a question…')
    fireEvent.change(input, { target: { value: 'Also check on weekends' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    const body = draftBody(0)
    expect(body.mode).toBe('chat')
    expect(body.text).toBe('Also check on weekends')
    expect(body.enabledAgents).toEqual(['g1', 'g2'])              // agents untouched
    expect(body.allowedSecrets).toEqual([MAIL_ID])        // unchecked secret gone
    // §19: the recent thread rides the body (empty on a fresh editor)
    expect(Array.isArray(body.chat)).toBe(true)
    // the message renders as a user entry in the thread
    expect(screen.getByText('Also check on weekends')).toBeTruthy()
  })

  it('re-checking a grant restores it in the next payload (check/uncheck is a no-op)', async () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Fast local'))     // uncheck…
    fireEvent.click(screen.getByText('Fast local'))     // …and re-check
    expect(screen.getByText('2 of 2 enabled')).toBeTruthy()

    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    expect(draftBody(0).enabledAgents).toEqual(['g1', 'g2'])
  })
})

describe('CreateFlow BUILD and TEST cards (§11)', () => {
  it('out of sync (grant gap): Sync now shows, Test disables with the sync-first hint', async () => {
    // an agent step pinned to g2 — unchecking g2 opens a derived grant gap
    storeMod.useStore.setState({
      automations: [{
        ...AUTO,
        steps: [{ file: '01-a.py', name: 'Judge', description: '', code: 'log("a")', agent: true, why: 'w', agents: [{ id: 'g2' }] }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    // the step tag renders the same name — target the checkbox row through its
    // model line ('qwen3:8b' is unique to it); the click bubbles to the row
    const agentRow = () => screen.getByText('qwen3:8b')
    fireEvent.click(agentRow())     // uncheck the agent the step calls
    expect(screen.getByText('Sync now')).toBeTruthy()
    expect(screen.getByText(/a step’s agent isn’t enabled/)).toBeTruthy()
    // the Test button stays visible but disabled, with the §11 hint
    const testBtn = screen.getByText('Test draft').closest('button')!
    expect(testBtn.disabled).toBe(true)
    expect(screen.getByText('Sync the steps before testing.')).toBeTruthy()
    // re-checking the grant clears the gap instantly — Test re-enables
    fireEvent.click(agentRow())
    expect(screen.getByText('Sync spec')).toBeTruthy()
    expect((screen.getByText('Test draft').closest('button')!).disabled).toBe(false)
  })

  it('in sync: quiet cards — no indicator dot, no accent button, one test row', () => {
    render(<CreateFlow />)
    const buildCard = screen.getByTestId('build-card')
    const testCard = screen.getByTestId('test-card')
    // §11 quiet posture: the in-sync BUILD card carries no dot and no accent
    // button — just the muted line with the faint sync escape hatch
    expect(within(buildCard).getByText(/In sync with the spec/)).toBeTruthy()
    expect(buildCard.querySelector('.ad-btn-primary')).toBeNull()
    expect(testCard.querySelector('.ad-btn-primary')).toBeNull()
    // §11 button treatment: compact borderless text buttons — main action
    // muted, the sync escape hatch faint; never bordered or filled boxes
    const testBtn = within(testCard).getByText('Test draft').closest('button')!
    expect(testBtn.disabled).toBe(false)
    expect(testBtn.classList.contains('ad-btn-text')).toBe(true)
    const syncBtn = within(buildCard).getByText('Sync spec').closest('button')!
    expect(syncBtn.classList.contains('ad-btn-text')).toBe(true)
    expect(syncBtn.classList.contains('dim')).toBe(true)
  })

  it('Test draft opens the modal: setup shows every option at once, only Run test starts it', async () => {
    armPendingPoll()
    storeMod.useStore.setState({
      automations: [{
        ...AUTO,
        params: [{ name: 'city', kind: 'text', label: 'City', help: '', value: 'Oslo' }],
        triggers: [{ kind: 'discord', channel: '#general', secret: 'DISCORD_TOKEN', enabled: true }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    // closed: no modal, no Run test
    expect(screen.queryByTestId('test-modal')).toBeNull()
    expect(screen.queryByText('Run test')).toBeNull()
    // the launcher opens the modal — it never starts a test
    fireEvent.click(within(screen.getByTestId('test-card')).getByText('Test draft'))
    expect(mockedApi.postTest).not.toHaveBeenCalled()
    // the modal is portaled to the body; both option groups render together
    const modal = screen.getByTestId('test-modal')
    expect(within(modal).getByTestId('test-setup')).toBeTruthy()
    expect(within(modal).getByText('PARAMETER VALUES · THIS TEST ONLY')).toBeTruthy()
    expect(within(modal).getByText('TRIGGER MESSAGE · THIS TEST ONLY')).toBeTruthy()
    // Run test is the only control that starts a test
    fireEvent.click(within(modal).getByText('Run test'))
    await waitFor(() => expect(mockedApi.postTest).toHaveBeenCalledTimes(1))
    const body = (mockedApi.postTest as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<string, unknown>
    expect(body.paramValues).toEqual({ city: 'Oslo' })
    expect(body.triggerMock).toBeUndefined() // empty message → no payload
    // §11: starting the test keeps the modal open and flips it to the run
    // phase — the setup form (and its Run test) is gone
    await waitFor(() => expect(screen.queryByText('Run test')).toBeNull())
    expect(screen.getByTestId('test-modal')).toBeTruthy()
  })

  it('drafted §8 test_values drive a never-opened-modal run and seed the setup editors', async () => {
    armPendingPoll()
    storeMod.useStore.setState({
      automations: [{
        ...AUTO,
        params: [{ name: 'city', kind: 'text', label: 'City', help: '', value: 'Oslo' }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    // a sync delivers steps + params + the drafted best-effort test values
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'sync',
      draft: {
        steps: [{ file: '01-a.py', name: 'Fetch', description: '', code: 'log("a")' }],
        params: [{ name: 'city', kind: 'text', label: 'City', help: '', default: '' }],
        packages: [],
        testValues: { city: 'Bergen' },
      },
    })
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 3000 })
    // §11 turn action row: the Test-the-draft pill starts the test with the
    // modal never opened — the drafted values still ride the run
    const row = screen.getByTestId('chat-turn-actions')
    fireEvent.click(within(row).getByText('Test draft'))
    await waitFor(() => expect(mockedApi.postTest).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const body = (mockedApi.postTest as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<string, unknown>
    expect(body.paramValues).toEqual({ city: 'Bergen' })
    // the pill opens the modal too — the user asked to watch the run
    await waitFor(() => expect(screen.getByTestId('test-modal')).toBeTruthy())
    // §11 setup seeding: the drafted value lands over the stored/default base
    await waitFor(() => expect(storeMod.useStore.getState().test).toBeTruthy())
    storeMod.useStore.setState({ test: null })
    // with no tracked record left the open modal falls back to the setup phase
    const modal = screen.getByTestId('test-modal')
    await waitFor(() => expect(within(modal).getByTestId('test-setup')).toBeTruthy())
    expect(within(modal).getByDisplayValue('Bergen')).toBeTruthy()
  })

  // §4.5 test record for a settled tracked test — the card renders off it
  const settledRun = (over: Record<string, unknown> = {}) => ({
    id: 'e1', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'Test',
    status: 'succeeded', trigger: 'Test', triggerSender: null, test: true,
    duration: '1s', started: '', startedMs: 1, endedMs: 2, queuedMs: 0, note: null, error: null,
    steps: [{ name: 'Fetch pages', status: 'succeeded', duration: '1s', attempts: [{ number: 1, status: 'succeeded', duration: '1s', startedMs: 1 }] }],
    ...over,
  })
  const seedSettled = (over: Record<string, unknown> = {}) => {
    const run = settledRun(over)
    storeMod.useStore.setState({
      test: { executionId: 'e1' }, executions: [run] as never, executionFull: { e1: run } as never,
    })
  }

  it('a running sync gates the card over a settled outcome (§11 state 2)', () => {
    armPendingPoll()
    seedSettled()
    render(<CreateFlow />)
    const card = () => screen.getByTestId('test-card')
    expect(within(card()).getByText('Test succeeded.')).toBeTruthy()
    // §11: the sync is about to rewrite the steps — the outcome gives way to
    // the gate rather than flashing an answer about steps that are leaving
    fireEvent.click(screen.getByText('Sync spec'))
    expect(within(card()).queryByText('Test succeeded.')).toBeNull()
    expect(within(card()).getByText('Sync the steps before testing.')).toBeTruthy()
    expect((within(card()).getByText('Test draft').closest('button')!).disabled).toBe(true)
  })

  it('a chat-armed sync gates it too — nothing was rewritten, the sync alone gates (§11 state 2)', async () => {
    armPendingPoll()
    // an answer-only response that chains a sync: the draft stays in sync, so
    // only the armed/running sync can gate the TEST card
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat',
      draft: { spec: null, answer: 'On it.', actions: { sync: true } },
    })
    seedSettled()
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: 'Rebuild the steps' } })
    fireEvent.click(screen.getByText('Send'))
    // the chained sync's POST went out and its poll never answers — it stays in flight
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(draftBody(1).mode).toBe('sync')
    expect(within(screen.getByTestId('build-card')).getByText(/In sync with the spec/)).toBeTruthy()
    const card = screen.getByTestId('test-card')
    expect(within(card).getByText('Sync the steps before testing.')).toBeTruthy()
    expect(within(card).queryByText('Test succeeded.')).toBeNull()
    expect((within(card).getByText('Test draft').closest('button')!).disabled).toBe(true)
  })

  it('a landed sync makes the outcome stale: Test the new changes, the setup phase on open (§11 state 3)', async () => {
    armPendingPoll()
    render(<CreateFlow />)
    const card = () => screen.getByTestId('test-card')
    // run a test against today's steps — the POST carries their fingerprint
    fireEvent.click(within(card()).getByText('Test draft'))
    fireEvent.click(within(screen.getByTestId('test-modal')).getByText('Run test'))
    await waitFor(() => expect(mockedApi.postTest).toHaveBeenCalledTimes(1))
    const body = (mockedApi.postTest as ReturnType<typeof vi.fn>).mock.calls[0][0] as Record<string, unknown>
    expect(body.stepsFingerprint).toBe(stepsFingerprint(AUTO.steps!))
    expect(typeof body.stepsFingerprint).toBe('string')
    fireEvent.click(within(screen.getByTestId('test-modal')).getByLabelText('Close'))
    await waitFor(() => expect(screen.queryByTestId('test-modal')).toBeNull())
    // the run settles failed — the settled row offers the repair loop
    act(() => seedSettled({
      status: 'failed', endedMs: 3, error: { step: 'Fetch pages', message: 'boom', reason: null },
      steps: [{ name: 'Fetch pages', status: 'failed', duration: '1s', attempts: [{ number: 1, status: 'failed', duration: '1s', startedMs: 1 }] }],
    }))
    expect(within(card()).getByText('Test failed.')).toBeTruthy()
    expect(within(card()).getByText('Analyze failure')).toBeTruthy()
    // a sync lands rewritten step code — the outcome no longer describes the steps
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'sync',
      draft: {
        steps: [{ file: '01-a.py', name: 'Fetch pages', description: '', code: 'log("b")' }],
        params: [], packages: [],
      },
    })
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 3000 })
    const stale = within(card()).getByText('Test the new changes.')
    expect(stale.getAttribute('title'))
      .toBe('The steps were rewritten after this test — its outcome no longer applies.')
    expect(within(card()).queryByText('Test failed.')).toBeNull()
    // the launcher stays live, and a stale outcome never offers the repair loop
    expect((within(card()).getByText('Test draft').closest('button')!).disabled).toBe(false)
    expect(within(card()).queryByText('Analyze failure')).toBeNull()
    // opening it lands on the setup phase for the new steps, never the old run
    fireEvent.click(within(card()).getByText('Test draft'))
    const modal = screen.getByTestId('test-modal')
    expect(within(modal).getByText('TEST DRAFT')).toBeTruthy()
    expect(within(modal).getByTestId('test-setup')).toBeTruthy()
    expect(within(modal).queryByText('Run again')).toBeNull()
    expect(within(modal).queryByText('View execution')).toBeNull()
  })

  // §11 state 5: a resumed draft's persisted last-test summary (test.yaml)
  const withLastTest = (test: Record<string, unknown>) => ({
    ...AUTO,
    draft: {
      spec: AUTO.spec, steps: AUTO.steps, instructions: AUTO.instructions, notes: '',
      params: [], packages: [], test,
    },
  } as unknown as Automation)

  it('a resumed summary whose fingerprint moved on reads as stale (§11 state 3)', () => {
    armPendingPoll()
    storeMod.useStore.setState({
      automations: [withLastTest({ status: 'succeeded', when: '2 h ago', executionId: 'e-old', stepsFingerprint: 'stale-fp' })],
    })
    render(<CreateFlow />)
    const card = screen.getByTestId('test-card')
    expect(within(card).getByText('Test the new changes.')).toBeTruthy()
    expect(within(card).queryByText('Last test succeeded — 2 h ago.')).toBeNull()
  })

  it('a resumed summary without a fingerprint is never stale (§21 old shape)', () => {
    // null and absent alike: unknown steps behind an outcome never make it stale
    for (const fp of [null, undefined]) {
      armPendingPoll()
      storeMod.useStore.setState({
        automations: [withLastTest({ status: 'succeeded', when: '2 h ago', executionId: 'e-old', stepsFingerprint: fp })],
      })
      render(<CreateFlow />)
      const card = screen.getByTestId('test-card')
      expect(within(card).getByText('Last test succeeded — 2 h ago.')).toBeTruthy()
      expect(within(card).queryByText('Test the new changes.')).toBeNull()
      cleanup()
    }
  })

  it('a diagnosed blocked sync lands a thread blockers entry with the build-failure headline', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'blocked', stage: null, detail: null, error: null, draft: null,
      mode: 'sync', blockedAt: 'steps', diagnosed: true,
      blockers: [{ reason: 'The build failed validation.', fix: 'Simplify the spec.' }],
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(
      () => expect(screen.getByText('The build failed — your AI suggests these fixes')).toBeTruthy(),
      { timeout: 3000 },
    )
    // same agent-output rendering + apply action as an agent-refusal blocker
    expect(screen.getByText('The build failed validation.')).toBeTruthy()
    expect(screen.getByText('Apply to the spec & sync')).toBeTruthy()
  })

  it('an undiagnosed blocked sync keeps the agent-refusal headline', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'blocked', stage: null, detail: null, error: null, draft: null,
      mode: 'sync', blockedAt: 'steps',
      blockers: [{ reason: 'Needs a channel id.', fix: 'Name it in the spec.' }],
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(
      () => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(),
      { timeout: 3000 },
    )
  })

  it('a blocked sync carrying §8 blocker notes applies them with a "Notes updated." chip', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...BLOCKED_SYNC, draft: { spec: null, notes: '- the feed needs auth' },
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    // the notes land like a chat notes rewrite — chip after the blockers entry
    expect(screen.getByText('Notes updated.')).toBeTruthy()
  })
})

describe('CreateFlow test-run modal (§11)', () => {
  beforeEach(armPendingPoll)
  // The modal drives navigation through the store's go — swap it for a spy and
  // put the real action back, since the shared beforeEach never restores it.
  let realGo: ReturnType<typeof storeMod.useStore.getState>['go']
  beforeEach(() => { realGo = storeMod.useStore.getState().go })
  afterEach(() => storeMod.useStore.setState({ go: realGo }))

  // §4.5 test record: `test: true`, versionLabel "Test", the draft's steps
  const testRun = (over: Record<string, unknown> = {}) => ({
    id: 'e9', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'Test',
    status: 'executing', trigger: 'Test', triggerSender: null, test: true,
    duration: '', started: '', startedMs: 1, endedMs: 0, queuedMs: 0, note: null, error: null,
    steps: [
      { name: 'Fetch pages', status: 'succeeded', duration: '1s', attempts: [{ number: 1, status: 'succeeded', duration: '1s', startedMs: 1 }] },
      { name: 'Send mail', status: 'executing', duration: '', attempts: [{ number: 1, status: 'executing', duration: '', startedMs: 2 }] },
    ],
    ...over,
  })
  const seedRun = (over: Record<string, unknown> = {}) => {
    const run = testRun(over)
    storeMod.useStore.setState({
      test: { executionId: 'e9' }, executions: [run] as never, executionFull: { e9: run } as never,
    })
  }

  it('a live run: Skip step + Cancel in the toolbar, the progress line in the footer; Escape never cancels', async () => {
    seedRun()
    render(<CreateFlow />)
    // the live card opens the modal on the run — the run phase, never the setup
    fireEvent.click(within(screen.getByTestId('test-card')).getByText('Open test'))
    const modal = screen.getByTestId('test-modal')
    expect(within(modal).queryByText('TEST DRAFT')).toBeNull()
    expect(within(modal).getByText('LOGS')).toBeTruthy()
    // §11 live toolbar: faint Skip step + muted Cancel
    expect(within(modal).getByText('Skip step')).toBeTruthy()
    expect(within(modal).getByText('Cancel')).toBeTruthy()
    expect(within(modal).queryByText('Run again')).toBeNull()
    // the footer is the run's status line
    expect(within(screen.getByTestId('test-footer')).getByText('Executing — step 2 of 2 · Send mail')).toBeTruthy()
    // Skip step skips the live step by index (§7)
    fireEvent.click(within(modal).getByText('Skip step'))
    await waitFor(() => expect(mockedApi.skipStep).toHaveBeenCalledWith('e9', 1))
    // §11: closing never cancels the test — the card keeps showing the run
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('test-modal')).toBeNull())
    expect(mockedApi.cancelExecution).not.toHaveBeenCalled()
    expect(within(screen.getByTestId('test-card')).getByText('Open test')).toBeTruthy()
  })

  it('a settled failed run: Run again returns to the setup phase, View execution opens the run', async () => {
    seedRun({
      status: 'failed', duration: '2s', endedMs: 3,
      error: { step: 'Send mail', message: 'boom', reason: null },
      steps: [
        { name: 'Fetch pages', status: 'succeeded', duration: '1s', attempts: [{ number: 1, status: 'succeeded', duration: '1s', startedMs: 1 }] },
        { name: 'Send mail', status: 'failed', duration: '1s', attempts: [{ number: 1, status: 'failed', duration: '1s', startedMs: 2 }] },
      ],
    })
    // the spy stands in for the store's go before the first render — the card
    // reads it through a selector, so swapping it mid-test would need a flush
    const go = vi.fn()
    storeMod.useStore.setState({ go })
    render(<CreateFlow />)
    // the settled card opens the modal on its run
    fireEvent.click(within(screen.getByTestId('test-card')).getByText('Test draft'))
    const modal = screen.getByTestId('test-modal')
    expect(within(modal).getByText('Run again')).toBeTruthy()
    expect(within(modal).getByText('View execution')).toBeTruthy()
    expect(within(modal).getByText('Analyze failure')).toBeTruthy()
    expect(within(modal).queryByText('Skip step')).toBeNull()
    // the footer names the failing step and the message
    expect(within(screen.getByTestId('test-footer')).getByText('Test failed at step “Send mail” — boom.')).toBeTruthy()
    // View execution closes the modal and opens the run's §7 execution page
    fireEvent.click(within(modal).getByText('View execution'))
    expect(go).toHaveBeenCalledWith('execution', { executionId: 'e9' })
    await waitFor(() => expect(screen.queryByTestId('test-modal')).toBeNull())
    // Run again returns the reopened modal to the setup phase — nothing starts
    fireEvent.click(within(screen.getByTestId('test-card')).getByText('Test draft'))
    fireEvent.click(within(screen.getByTestId('test-modal')).getByText('Run again'))
    const setup = screen.getByTestId('test-modal')
    expect(within(setup).getByText('TEST DRAFT')).toBeTruthy()
    expect(within(setup).getByTestId('test-setup')).toBeTruthy()
    expect(mockedApi.postTest).not.toHaveBeenCalled()
  })

  it('Open test reopens the modal on the live run after it was closed', async () => {
    seedRun()
    render(<CreateFlow />)
    const card = () => screen.getByTestId('test-card')
    fireEvent.click(within(card()).getByText('Open test'))
    fireEvent.click(within(screen.getByTestId('test-modal')).getByLabelText('Close'))
    await waitFor(() => expect(screen.queryByTestId('test-modal')).toBeNull())
    // re-attaching: the same run, still the run phase
    fireEvent.click(within(card()).getByText('Open test'))
    const modal = screen.getByTestId('test-modal')
    expect(within(modal).getByText('Cancel')).toBeTruthy()
    expect(within(modal).queryByText('TEST DRAFT')).toBeNull()
  })
})

describe('CreateFlow blockers thread entries (§11)', () => {
  beforeEach(armPendingPoll)

  it('Apply gates while a job is busy and ungates when it settles; text renders as agent output', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce(BLOCKED_SYNC)
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    // the blocked job's activity glyph is the amber check (§11 outcome glyph)
    const glyph = document.querySelector('[data-testid="chat-thread"] .fa-check') as HTMLElement
    expect(glyph.style.color).toBe('var(--amber)')
    // sync-source explainer + the blocker text as read-only agent output (no textareas)
    expect(screen.getByText('It couldn’t sync the steps with the spec.')).toBeTruthy()
    expect(screen.getByText('Name it in the spec.')).toBeTruthy()
    expect(screen.queryByDisplayValue('Name it in the spec.')).toBeNull()
    expect((screen.getByText('Apply to the spec & sync').closest('button')!).disabled).toBe(false)
    // a second sync (never answering) disables the primary
    fireEvent.click(screen.getByText('Sync spec'))
    expect((screen.getByText('Apply to the spec & sync').closest('button')!).disabled).toBe(true)
    // the composer Cancel settles the job — the primary ungates
    fireEvent.click(screen.getByText('Cancel'))
    expect((screen.getByText('Apply to the spec & sync').closest('button')!).disabled).toBe(false)
  })

  it('viewing an old version disables Apply; Dismiss still collapses the entry', async () => {
    storeMod.useStore.setState({
      automations: [{
        ...AUTO, version: 2,
        versions: [{ version: 1, when: 'Jul 1', note: null, spec: AUTO.spec, steps: AUTO.steps, instructions: '', notes: '', params: [], packages: [] }],
      } as unknown as Automation],
    })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce(BLOCKED_SYNC)
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    // browse v1 from the version menu — the thread survives, Apply gated
    fireEvent.click(screen.getByText('Draft'))
    fireEvent.click(screen.getByText('v1'))
    expect((screen.getByText('Apply to the spec & sync').closest('button')!).disabled).toBe(true)
    expect((screen.getByPlaceholderText('Back to the draft to edit or ask.') as HTMLTextAreaElement).disabled).toBe(true)
    // Dismiss is never gated — the entry collapses to the one-line summary
    fireEvent.click(screen.getByText('Dismiss'))
    expect(screen.getByText('1 blocker — dismissed')).toBeTruthy()
  })

  it('version menu: only older rows carry delete; confirming calls the DELETE and toasts', async () => {
    const edited = {
      ...AUTO, version: 2,
      versions: [{ version: 1, when: 'created Jul 1, 2026', note: null, spec: AUTO.spec, steps: AUTO.steps, instructions: '', notes: '', params: [], packages: [] }],
    } as unknown as Automation
    storeMod.useStore.setState({ automations: [edited] })
    ;(mockedApi.getAutomation as ReturnType<typeof vi.fn>).mockResolvedValue({ ...edited, versions: [] })
    render(<CreateFlow />)
    fireEvent.click(screen.getByTestId('version-menu'))
    // §4.4: the current version is an inert header, never a selectable option
    expect(screen.getByText('v2 · current')).toBeTruthy()
    expect(screen.getByText(/Your draft builds on this/)).toBeTruthy()
    // §4.4: hidden, not disabled — the Draft row and the header carry no trash
    expect(screen.getByTestId('delete-version-1')).toBeTruthy()
    expect(screen.queryByTestId('delete-version-2')).toBeNull()
    fireEvent.click(screen.getByTestId('delete-version-1'))
    // danger ConfirmModal; confirming fires the §19 DELETE and reloads the automation
    expect(screen.getByText('Delete v1?')).toBeTruthy()
    fireEvent.click(screen.getByText('Delete v1', { exact: true }))
    await waitFor(() => expect(mockedApi.deleteVersion).toHaveBeenCalledWith('a1', 1))
    await waitFor(() => expect(mockedApi.getAutomation).toHaveBeenCalledWith('a1'))
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('v1 deleted.'))
  })

  it('a fresh draft’s blocked first message: the reply is an ordinary chat message', async () => {
    storeMod.useStore.setState({ createFrom: 'app' })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'blocked', stage: null, detail: null, error: null, draft: null,
      mode: 'chat', blockedAt: 'chat',
      blockers: [{ reason: 'Which folder?', fix: 'Name the folder to watch.' }],
    })
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'Watch my Downloads folder' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    // chat-source explainer, no primary button — the composer is the answer path
    expect(screen.getByText('Reply below — your answer is sent back and the spec is rewritten.')).toBeTruthy()
    expect(screen.queryByText('Answer & rewrite the spec')).toBeNull()
    expect(screen.queryByText('Apply to the spec & sync')).toBeNull()
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'The Downloads folder' } })
    fireEvent.click(screen.getByText('Send'))
    // entry auto-dismisses, the reply lands as a user entry, another chat job
    // starts — the thread's CONVERSATION context carries the clarification
    expect(screen.getByText('1 blocker — dismissed')).toBeTruthy()
    expect(screen.getByText('The Downloads folder')).toBeTruthy()
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(2))
    const body = draftBody(1)
    expect(body.mode).toBe('chat')
    expect(body.text).toBe('The Downloads folder')
  })

  it('chat blockers auto-dismiss when the reply goes out as a chat message', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ ...BLOCKED_SYNC, mode: 'chat', blockedAt: 'chat' })
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: 'Send it to Discord too' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('Reply below — your answer is sent back and the spec is rewritten.')).toBeTruthy()
    expect(screen.queryByText('Answer & rewrite the spec')).toBeNull()
    // the blocked job settled — the reply goes out through the composer
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: 'Channel 42' } })
    fireEvent.click(screen.getByText('Send'))
    expect(screen.getByText('1 blocker — dismissed')).toBeTruthy()
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(2))
    const body = draftBody(1)
    expect(body.mode).toBe('chat')
    expect(body.text).toBe('Channel 42')
  })

  it('a fresh draft’s blocked chained sync keeps the landed spec out of sync', async () => {
    storeMod.useStore.setState({ createFrom: 'app' })
    const spec = [{ kind: 'h1', text: 'Folder watcher' }, { kind: 'p', text: 'Watches things.' }]
    // the chat job lands the spec + sync action, then the chained sync blocks
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        id: 'j1', status: 'done', stage: null, detail: null, error: null,
        mode: 'chat', draft: { spec, actions: { sync: true } },
      })
      .mockResolvedValue({
        id: 'j1', status: 'blocked', stage: null, detail: null, error: null,
        mode: 'sync', blockedAt: 'steps', draft: null,
        blockers: [{ reason: 'Needs a channel id.', fix: 'Name it in the spec.' }],
      })
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'Watch my Downloads folder' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 5000 })
    expect(screen.getByText('It couldn’t sync the steps with the spec.')).toBeTruthy()
    expect(screen.getByText('Apply to the spec & sync')).toBeTruthy()
    // the chat rewrite landed the spec and the workflow is out of sync
    expect(screen.getByText('Watches things.')).toBeTruthy()
    expect(screen.getByText('Out of sync — steps still match the old spec.')).toBeTruthy()
  })

  it('a markdown link in a blocker renders as a clickable anchor', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...BLOCKED_SYNC,
      blockers: [{ reason: 'Transmission isn’t installed.',
        fix: 'Download it from [transmissionbt.com](https://transmissionbt.com) and install it.' }],
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(() => expect(screen.getByText('Your AI hit a blocker')).toBeTruthy(), { timeout: 3000 })
    const a = screen.getByText('transmissionbt.com').closest('a')!
    expect(a.getAttribute('href')).toBe('https://transmissionbt.com')
    expect(a.getAttribute('target')).toBe('_blank')
  })

  it('a user-action blocker offers Dismiss only under the needs-you headline', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...BLOCKED_SYNC,
      blockers: [{ reason: 'Transmission isn’t installed.',
        fix: 'Install Transmission, then run the automation again.', kind: 'user-action' }],
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('Sync spec'))
    await waitFor(
      () => expect(screen.getByText('Your AI needs you to do something first')).toBeTruthy(),
      { timeout: 3000 },
    )
    // no source explainer, no Apply — the Mac isn't ready, nothing to amend
    expect(screen.queryByText('It couldn’t sync the steps with the spec.')).toBeNull()
    expect(screen.queryByText('Apply to the spec & sync')).toBeNull()
    fireEvent.click(screen.getByText('Dismiss'))
    expect(screen.getByText('1 blocker — dismissed')).toBeTruthy()
  })
})

describe('CreateFlow per-stage activity entries (§11)', () => {
  beforeEach(armPendingPoll)

  it('a fresh draft’s first turn settles each finished stage as its own activity entry', async () => {
    storeMod.useStore.setState({ createFrom: 'app' })
    const spec = [{ kind: 'h1', text: 'Watcher' }, { kind: 'p', text: 'Watches.' }]
    const specEvent = { time: 1, text: 'Thinking about the spec…', stage: 'Updating the documents' }
    const stepsEvent = { time: 2, text: 'Writing the manifest…', stage: 'Syncing the workflow' }
    // the chat job walks request → documents and lands the spec + sync action;
    // the chained sync job walks the workflow phase and delivers the steps
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        id: 'j1', status: 'building', stage: 'Updating the documents', detail: null,
        error: null, mode: 'chat', draft: null, events: [specEvent],
      })
      .mockResolvedValueOnce({
        id: 'j1', status: 'done', stage: 'Updating the documents', detail: null,
        error: null, mode: 'chat', draft: { spec, actions: { sync: true } },
        events: [specEvent],
      })
      .mockResolvedValue({
        id: 'j1', status: 'done', stage: 'Syncing the workflow', detail: null,
        error: null, mode: 'sync',
        draft: { steps: [], params: [], packages: [] },
        events: [stepsEvent],
      })
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'Watch my folder' } })
    fireEvent.click(screen.getByText('Send'))
    const thread = () => document.querySelector('[data-testid="chat-thread"]') as HTMLElement
    await waitFor(
      () => expect(within(thread()).getByText('Steps synced with the spec.')).toBeTruthy(),
      { timeout: 5000 },
    )
    // every displayed stage survives as a settled entry — the seeded neutral
    // deciding phase (with its canned bullet) first, then documents, then
    // the chained sync's workflow phase — each with its own slice of the feed
    const t = thread()
    const neutralEntry = within(t).getByText('Working on the request…')
    expect(within(t).getByText('• Choosing what to do')).toBeTruthy()
    const specEntry = within(t).getByText('Updating the documents…')
    const stepsEntry = within(t).getByText('Syncing the workflow…')
    expect(neutralEntry.compareDocumentPosition(specEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(specEntry.compareDocumentPosition(stepsEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // §11 operation blocks: feed lines render as flush-left `• ` bullets
    const feedSpec = within(t).getByText('• Thinking about the spec…')
    const feedSteps = within(t).getByText('• Writing the manifest…')
    expect(specEntry.compareDocumentPosition(feedSpec) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(feedSpec.compareDocumentPosition(stepsEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(stepsEntry.compareDocumentPosition(feedSteps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // all settled with a green check, no spinner left
    expect(spinnersIn(t).length).toBe(0)
  })
})

describe('CreateFlow per-step durations (§8 stage timing / §11)', () => {
  beforeEach(armPendingPoll)

  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }
  // §11: the duration stamp sits beside its label in the same row — the
  // bullet's flex div. A bullet carrying no duration, and every block header's
  // title row (never stamped), end the row at the label itself.
  const stampBeside = (label: HTMLElement) => {
    const last = label.parentElement!.lastElementChild as HTMLElement
    return last === label ? null : last.textContent
  }
  const bullet = (text: string) => screen.getByText(`• ${text}`)
  // §11 one ticking stamp: every stamped line in a block is a bullet label
  // whose row carries a second child — count them to prove only one line ticks.
  const stampedBullets = (block: Element) =>
    [...block.querySelectorAll('span')]
      .filter((s) => (s.textContent ?? '').startsWith('•') && stampBeside(s as HTMLElement) !== null)
  // one chat job walking the deciding phase into the documents phase, with
  // §8 stamps for both stages and the settle
  const STAMPED_EVENTS = [
    { time: 1000.6, text: 'Writing the answer', stage: 'Working on the request' },
    { time: 1002.4, text: 'Writing the spec', stage: 'Updating the documents' },
    { time: 1004.8, text: 'Writing the notes', stage: 'Updating the documents' },
  ]
  const settled = (over: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: 'Updating the documents', detail: null,
    error: null, mode: 'chat', draft: { answer: 'Looked into it.' },
    events: STAMPED_EVENTS, ...over,
  })

  it('a settled job stamps every step span from the §8 stamps, never a title row', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(settled({
      stageTimes: [
        // the deciding phase opened a beat before its first milestone (1.6s),
        // the documents phase landed one right away (0.4s)
        { stage: 'Working on the request', time: 999 },
        { stage: 'Updating the documents', time: 1002 },
      ],
      endedTime: 1008.2,
    }))
    render(<CreateFlow />)
    send('Check the docs')
    await waitFor(() => expect(screen.getByText('Looked into it.')).toBeTruthy(), { timeout: 3000 })
    // §11: a block's title row is a pure label — the bullets carry the time
    expect(stampBeside(screen.getByText('Working on the request…'))).toBeNull()
    expect(stampBeside(screen.getByText('Updating the documents…'))).toBeNull()
    // §11 one identity: the deciding phase's material leading gap settles as
    // its first timed bullet — the stage's canned description, the exact
    // waiting line the live block ticked, frozen in place
    const lead = bullet('Choosing what to do')
    expect(stampBeside(lead)).toBe('1.6s')
    expect(lead.compareDocumentPosition(bullet('Writing the answer'))
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // …while the documents phase's sub-second gap leaves the stage clean — its
    // own canned line never lands, and the label Thinking… renders nowhere
    expect(screen.getAllByText('• Choosing what to do').length).toBe(1)
    expect(screen.queryByText('• Writing the documents')).toBeNull()
    expect(screen.getByTestId('chat-thread').textContent).not.toContain('Thinking…')
    // step spans: each event runs to the next milestone in its own stage, the
    // stage's last event to that stage's end
    expect(stampBeside(bullet('Writing the answer'))).toBe('1.4s')
    expect(stampBeside(bullet('Writing the spec'))).toBe('2.4s')
    expect(stampBeside(bullet('Writing the notes'))).toBe('3.4s')
  })

  it('a payload without §8 stamps settles with unbounded last steps and no leading line', async () => {
    // an older backend sends events but neither stageTimes nor endedTime
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(settled({}))
    render(<CreateFlow />)
    send('Check the docs')
    await waitFor(() => expect(screen.getByText('Looked into it.')).toBeTruthy(), { timeout: 3000 })
    // a title row is never stamped, with or without stamps to draw on
    expect(stampBeside(screen.getByText('Working on the request…'))).toBeNull()
    expect(stampBeside(screen.getByText('Updating the documents…'))).toBeNull()
    // no stage start to measure a leading gap against — no canned leading
    // line on either stage, so each block opens on its first real event
    expect(screen.queryByText('• Choosing what to do')).toBeNull()
    expect(screen.queryByText('• Writing the documents')).toBeNull()
    expect(screen.getByTestId('chat-thread').textContent).not.toContain('Thinking…')
    // an event still bounded by the next event in its stage keeps its span…
    expect(stampBeside(bullet('Writing the spec'))).toBe('2.4s')
    // …while a stage's last event has no next milestone to run to
    expect(stampBeside(bullet('Writing the answer'))).toBeNull()
    expect(stampBeside(bullet('Writing the notes'))).toBeNull()
  })

  it('an empty-feed stage settles with the canned bullet carrying the stage span', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'done', stage: 'Working on the request', detail: null,
      error: null, mode: 'chat', draft: { answer: 'All good.' }, events: [],
      stageTimes: [{ stage: 'Working on the request', time: 1000 }],
      endedTime: 1001.5,
    })
    render(<CreateFlow />)
    send('Anything to improve?')
    await waitFor(() => expect(screen.getByText('All good.')).toBeTruthy(), { timeout: 3000 })
    // the title row stays a pure label…
    expect(stampBeside(screen.getByText('Working on the request…'))).toBeNull()
    // …and the canned description, the stage's only action, takes its whole span
    expect(stampBeside(bullet('Choosing what to do'))).toBe('1.5s')
  })

  it('the live waiting line has one identity: the canned bullet ticks, then freezes (§11)', async () => {
    // a pinned wall clock makes the ticking stamps exact — the 700 ms poll and
    // the once-a-second tick both run on the fake timers
    const T0 = 1_700_000_000
    vi.useFakeTimers()
    vi.setSystemTime(T0 * 1000)
    try {
      const building = (over: Record<string, unknown>) => ({
        id: 'j1', status: 'building', stage: 'Working on the request', detail: null,
        error: null, mode: 'chat', draft: null, events: [],
        // the stage opened two seconds before the clock was pinned
        stageTimes: [{ stage: 'Working on the request', time: T0 - 2 }], ...over,
      })
      ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(building({}))
      render(<CreateFlow />)
      send('Check the docs')
      await act(async () => { await vi.advanceTimersByTimeAsync(700) })
      // no events, no detail: the stage's canned description holds the feed's
      // place, ticking whole seconds from the §8 stage stamp
      expect(stampBeside(bullet('Choosing what to do'))).toBe('2s')
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(stampBeside(bullet('Choosing what to do'))).toBe('3s')
      // the backend's Thinking… detail never renders — the canned line
      // subsumes it, so the waiting line is never relabeled mid-tick
      ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
        .mockResolvedValue(building({ detail: 'Thinking…' }))
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(screen.getByTestId('chat-thread').textContent).not.toContain('Thinking…')
      expect(stampBeside(bullet('Choosing what to do'))).toBe('4s')
      // the first milestone lands 1.6s after the stage start: the canned line
      // freezes in place above it, carrying that gap as a settled span, and
      // the newest line takes over the one ticking stamp
      ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(building({
        events: [{ time: T0 - 0.4, text: 'Writing the answer', stage: 'Working on the request' }],
      }))
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      const lead = bullet('Choosing what to do')
      expect(stampBeside(lead)).toBe('1.6s')
      expect(lead.compareDocumentPosition(bullet('Writing the answer'))
        & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      // 3.4s elapsed at the fourth tick (T0 + 3s, the event stamped T0 − 0.4s)
      expect(stampBeside(bullet('Writing the answer'))).toBe('3s')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a detail that is a different activity renders unstamped — one ticking stamp (§11)', async () => {
    const T0 = 1_700_000_000
    vi.useFakeTimers()
    vi.setSystemTime(T0 * 1000)
    try {
      ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
        id: 'j1', status: 'building', stage: 'Working on the request',
        // a tool event landed after the document stream's throttled line: the
        // detail is a DIFFERENT message, not an extension of the last event
        detail: 'Writing step 1 of 3 — 01-a.py · 20 lines',
        error: null, mode: 'chat', draft: null,
        events: [{ time: T0 - 3.4, text: 'Running a command — ls…', stage: 'Working on the request' }],
        // a sub-second leading gap drops the canned waiting line
        stageTimes: [{ stage: 'Working on the request', time: T0 - 4 }],
      })
      render(<CreateFlow />)
      send('Check the docs')
      await act(async () => { await vi.advanceTimersByTimeAsync(700) })
      // the last event keeps the tick…
      expect(stampBeside(bullet('Running a command — ls…'))).toBe('3s')
      // …and the detail line renders bare beneath it
      expect(stampBeside(bullet('Writing step 1 of 3 — 01-a.py · 20 lines'))).toBeNull()
      expect(stampedBullets(screen.getByTestId('chat-progress')).length).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a detail extending the last event replaces it and inherits the tick (§11)', async () => {
    const T0 = 1_700_000_000
    vi.useFakeTimers()
    vi.setSystemTime(T0 * 1000)
    try {
      ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
        id: 'j1', status: 'building', stage: 'Working on the request',
        // the same message, growing line count — the detail extends the event
        detail: 'Running a command — ls… · 20 lines',
        error: null, mode: 'chat', draft: null,
        events: [{ time: T0 - 3.4, text: 'Running a command — ls…', stage: 'Working on the request' }],
        stageTimes: [{ stage: 'Working on the request', time: T0 - 4 }],
      })
      render(<CreateFlow />)
      send('Check the docs')
      await act(async () => { await vi.advanceTimersByTimeAsync(700) })
      // the event never shows twice — the detail bullet took its place…
      expect(screen.queryByText('• Running a command — ls…')).toBeNull()
      // …carrying the event's ticking stamp, still the block's only one
      expect(stampBeside(bullet('Running a command — ls… · 20 lines'))).toBe('3s')
      expect(stampedBullets(screen.getByTestId('chat-progress')).length).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a stored activity entry renders its stamps; a pre-field one renders bare (§21.4)', async () => {
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ chat: [
      { id: 'd1', kind: 'activity', title: 'Updating the documents…', outcome: 'done',
        text: 'Writing the answer\nWriting the spec\nWriting the notes',
        eventDurationsMs: [1400, null, 3400] },
      { id: 'd2', kind: 'activity', title: 'Syncing the workflow…', outcome: 'done',
        text: 'Writing the manifest' },
    ] })
    render(<CreateFlow />)
    await screen.findByText('Syncing the workflow…')
    // §11: a stored entry's title row is a pure label too
    expect(stampBeside(screen.getByText('Updating the documents…'))).toBeNull()
    expect(stampBeside(bullet('Writing the answer'))).toBe('1.4s')
    // §4.4: a null element is a line no stamp bounded — that line stays bare
    expect(stampBeside(bullet('Writing the spec'))).toBeNull()
    expect(stampBeside(bullet('Writing the notes'))).toBe('3.4s')
    // §21.4 pre-field entry: no key at all, so no stamp anywhere on it
    expect(stampBeside(screen.getByText('Syncing the workflow…'))).toBeNull()
    expect(stampBeside(bullet('Writing the manifest'))).toBeNull()
  })

  it('the stamps pair with the raw text lines, so blank lines never shift them', async () => {
    // §4.4 eventDurationsMs is parallel to the entry's raw `text` lines; the
    // empty-line filter runs after the pairing, so index 2 stays index 2
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ chat: [
      { id: 'd1', kind: 'activity', title: 'Syncing the workflow…', outcome: 'done',
        text: 'Writing the manifest\n\nWriting 01-check.py',
        eventDurationsMs: [1400, null, 3400] },
    ] })
    render(<CreateFlow />)
    await screen.findByText('Syncing the workflow…')
    expect(stampBeside(bullet('Writing the manifest'))).toBe('1.4s')
    expect(stampBeside(bullet('Writing 01-check.py'))).toBe('3.4s')
  })
})

describe('CreateFlow chat response application (§11)', () => {
  beforeEach(armPendingPoll)

  const done = (draft: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat', draft,
  })
  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }

  it('answer renders before the spec rewrite, which dirties the draft and toasts', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Now with weekends.' }],
      answer: 'Sure — done.',
    }))
    render(<CreateFlow />)
    send('Also weekends')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    // §11 order: the answer entry lands before the rewrite entry
    const answer = screen.getByText('Sure — done.')
    const rewrite = screen.getByText('Spec updated.')
    expect(answer.compareDocumentPosition(rewrite) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // the rewrite applied to the spec card and marked the workflow out of sync
    expect(screen.getByText('Now with weekends.')).toBeTruthy()
    expect(screen.getByText('Out of sync — steps still match the old spec.')).toBeTruthy()
    expect(storeMod.useStore.getState().toast)
      .toBe('Spec updated — the workflow is out of sync. Sync the steps before saving.')
  })

  it('answer headers: The plan beside rewrites, Question for you on a question (§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Now with weekends.' }],
      answer: 'Here is what I changed.',
    }))
    render(<CreateFlow />)
    send('Also weekends')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    // a reply arriving with a rewrite is the plan
    expect(screen.getByText('The plan')).toBeTruthy()
    // an agent-declared question (§19 answerKind) gets the question header
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'Which folder should I watch?', answerKind: 'question',
    }))
    send('Watch stuff')
    await waitFor(() => expect(screen.getByText('Which folder should I watch?')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('Question for you')).toBeTruthy()
    // §11: while the question is the newest entry the composer invites the answer
    expect(screen.getByPlaceholderText('Answer here…')).toBeTruthy()
    // replying reverts the prompt once entries follow the question
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'Got it — watching Downloads.',
    }))
    fireEvent.change(screen.getByPlaceholderText('Answer here…'), { target: { value: 'The Downloads folder' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Got it — watching Downloads.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByPlaceholderText('Change something, or ask a question…')).toBeTruthy()
  })

  it('a first-turn question wins the composer prompt over the fresh-create invite (§11)', async () => {
    // a fresh create: no spec and no steps yet, so the composer would otherwise
    // keep the describe invite
    storeMod.useStore.setState({ createFrom: 'app' })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'Which folder should I watch?', answerKind: 'question',
    }))
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'Watch a folder' } })
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(screen.getByText('Which folder should I watch?')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('Question for you')).toBeTruthy()
    // §11: the pending question wins over the whole describe/change rule
    expect(screen.getByPlaceholderText('Answer here…')).toBeTruthy()
    expect(screen.queryByPlaceholderText('Describe the job — one sentence is enough.')).toBeNull()
  })

  it('a reply merely ending with ? stays From your AI — no answerKind, no question header (§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'It runs daily at 8. Does that answer your question?',
    }))
    render(<CreateFlow />)
    send('When does it run?')
    await waitFor(() => expect(screen.getByText(/Does that answer your question/)).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('From your AI')).toBeTruthy()
    expect(screen.queryByText('Question for you')).toBeNull()
  })

  it('the plan lands mid-job at the flip; the settle updates it in place (§8/§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        id: 'j1', status: 'building', stage: 'Updating the documents', detail: null,
        error: null, mode: 'chat', draft: null, events: [],
        plan: 'I will add weekends to the schedule.',
      })
      .mockResolvedValue(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Now with weekends.' }],
        answer: 'I will add weekends and Fridays.',
      }))
    render(<CreateFlow />)
    send('Also weekends')
    // §11: "The plan" message block renders while the job is still building
    await waitFor(() => expect(screen.getByText('I will add weekends to the schedule.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('The plan')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    // the settle appended no second answer entry — the shown plan's text was
    // updated in place to the settled payload's answer (repair-round prose)
    expect(screen.getAllByText('The plan').length).toBe(1)
    expect(screen.queryByText('I will add weekends to the schedule.')).toBeNull()
    expect(screen.getByText('I will add weekends and Fridays.')).toBeTruthy()
  })

  it('turn action row: Test draft when in sync; Sync now + Undo after a rewrite (§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({ answer: 'All good.' }))
    render(<CreateFlow />)
    send('Anything to improve?')
    await waitFor(() => expect(screen.getByText('All good.')).toBeTruthy(), { timeout: 3000 })
    // in sync with steps → the Test pill; no sync or undo to offer
    const row = screen.getByTestId('chat-turn-actions')
    expect(within(row).queryByText('Sync now')).toBeNull()
    expect(within(row).queryByText('Undo this change')).toBeNull()
    // the pill starts a draft test right away (§11) — same run as Run test
    fireEvent.click(within(row).getByText('Test draft'))
    await waitFor(() => expect(mockedApi.postTest).toHaveBeenCalledTimes(1), { timeout: 3000 })
    // let the tracked test settle out of the way before the rewrite half
    storeMod.useStore.setState({ test: null })
    // a rewrite pulls the workflow out of sync → Sync now + Undo, Test hidden
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten.' }],
    }))
    send('Rewrite it')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    const row2 = screen.getByTestId('chat-turn-actions')
    expect(within(row2).getByTestId('chat-sync-now')).toBeTruthy()
    expect(within(row2).getByText('Undo this change')).toBeTruthy()
    expect(within(row2).queryByText('Test draft')).toBeNull()
  })

  it('a settled job persists its event feed as an activity entry before the outcome', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...done({ answer: 'Looked into it.' }),
      events: [{ time: 1, text: 'Reading https://example.com/docs…' }, { time: 2, text: 'Writing the reply…' }],
    })
    render(<CreateFlow />)
    send('Check the docs')
    await waitFor(() => expect(screen.getByText('Looked into it.')).toBeTruthy(), { timeout: 3000 })
    // the stage label survives the job with a check where the spinner was
    expect(screen.getByText('Working on the request…')).toBeTruthy()
    expect(spinnersIn(document.body).length).toBe(0)
    expect(document.querySelector('[data-testid="chat-thread"] .fa-check')).toBeTruthy()
    // the feed lines survive too, dim `• ` bullets above the answer (§11)
    const feedLine = screen.getByText('• Reading https://example.com/docs…')
    expect(screen.getByText('• Writing the reply…')).toBeTruthy()
    expect(feedLine.compareDocumentPosition(screen.getByText('Looked into it.')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a failed job’s activity entry settles into a red X, not a check (§11 outcome glyph)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'j1', status: 'failed', stage: null, detail: null, error: 'The harness crashed.',
      mode: 'chat', draft: null,
      events: [{ time: 1, text: 'Reading the spec…' }],
    })
    render(<CreateFlow />)
    send('Change it')
    await waitFor(() => expect(screen.getByText('The harness crashed.')).toBeTruthy(), { timeout: 3000 })
    // the trail survives with the failed glyph; the feed line is kept
    const thread = document.querySelector('[data-testid="chat-thread"]')!
    expect(thread.querySelector('.fa-xmark')).toBeTruthy()
    expect(thread.querySelector('.fa-check')).toBeNull()
    expect(screen.getByText('• Reading the spec…')).toBeTruthy()
  })

  it('a notes rewrite applies without marking the workflow out of sync (§4.1)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: null, notes: '- The site rate-limits at 10 rpm',
    }))
    render(<CreateFlow />)
    send('Remember the rate limit')
    await waitFor(() => expect(screen.getByText('Notes updated.')).toBeTruthy(), { timeout: 3000 })
    expect(bodyLi('The site rate-limits at 10 rpm')).toBeTruthy() // NOTES card content
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
    expect(screen.queryByText(/Out of sync/)).toBeNull()
  })

  it('actions.sync chains a sync job right after the rewrite lands', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Synced spec.' }],
      answer: 'Rewrote it.', actions: { sync: true },
    }))
    render(<CreateFlow />)
    send('Rewrite and sync')
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 5000 })
    expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(2)
    expect(draftBody(0).mode).toBe('chat')
    expect(draftBody(1).mode).toBe('sync')
    // the chained sync cleared the dirty flag again
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
  })

  it('actions.test is dropped with the system chip when the chained sync blocks', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Test me.' }],
        actions: { test: true },
      }))
      .mockResolvedValue(BLOCKED_SYNC)
    render(<CreateFlow />)
    send('Change it and test it')
    await waitFor(
      () => expect(screen.getByText('Test skipped — the steps aren’t in sync with the spec.')).toBeTruthy(),
      { timeout: 5000 },
    )
    // the armed test chained a sync first; its block kept the steps stale
    expect(draftBody(1).mode).toBe('sync')
    expect(mockedApi.postTest).not.toHaveBeenCalled()
  })

  it('a chat rename into a taken name is skipped with the system chip (§4.1)', async () => {
    storeMod.useStore.setState({ automations: [AUTO, { ...AUTO, id: 'a2', name: 'Other auto' }] })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'Renaming it.', actions: { name: 'OTHER auto' },
    }))
    render(<CreateFlow />)
    send('rename it to Other auto')
    await waitFor(() => expect(
      screen.getByText('Rename to “OTHER auto” skipped — an automation with that name already exists.'),
    ).toBeTruthy(), { timeout: 3000 })
    // the skipped rename never rides the PATCH and the title keeps the old name
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    expect(screen.getAllByText('My auto').length).toBeGreaterThan(0)
    expect(screen.queryByText('OTHER auto')).toBeNull()
  })
})

describe('CreateFlow chat staged actions (§8 param_values / triggers ops)', () => {
  beforeEach(armPendingPoll)

  const done = (draft: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat', draft,
  })
  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }
  // The §4.4 debounced draft PUT is the observable for staged editor state —
  // its payload is exactly what a kept draft (and a save) carries.
  const lastDraftPut = async () => {
    await waitFor(() => expect(mockedApi.putDraft).toHaveBeenCalled(), { timeout: 3000 })
    return (mockedApi.putDraft as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1] as {
      triggers: Array<Record<string, unknown>>
      paramValues?: Record<string, unknown>
      params: Array<Record<string, unknown>>
    }
  }

  const TRIGGERED = {
    ...AUTO,
    triggers: [
      { id: 't1', kind: 'cron', expression: '0 8 * * *', enabled: true, source: 'spec', label: 'Daily at 8:00', short: 'Daily 8:00' },
      { id: 't2', kind: 'discord', channel: '123', secret: 'CRM_API_KEY', enabled: true, label: 'Discord · 123', short: 'Discord' },
    ],
    params: [{ name: 'greeting', kind: 'text', label: 'Greeting', help: 'What to say.', default: 'hello', value: 'hello' }],
  } as unknown as Automation
  beforeEach(() => storeMod.useStore.setState({ automations: [TRIGGERED] }))

  it('an add matching an existing trigger is a no-op — chip lands, list unchanged', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      answer: 'That schedule is already set up.',
      actions: { triggers: [{ op: 'add', trigger: { kind: 'cron', expression: '0 8 * * *', enabled: true, source: 'user' } }] },
    }))
    render(<CreateFlow />)
    send('add an 8am schedule')
    await waitFor(() => expect(screen.getByText('That trigger already exists.')).toBeTruthy(), { timeout: 3000 })
    const d = await lastDraftPut()
    expect(d.triggers.map((t) => t.id)).toEqual(['t1', 't2'])
  })

  it('adding a new trigger keeps every existing trigger untouched', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      actions: { triggers: [{ op: 'add', trigger: { kind: 'cron', expression: '0 21 * * *', enabled: true, source: 'user' } }] },
    }))
    render(<CreateFlow />)
    send('also run at 9pm')
    await waitFor(() => expect(screen.getByText('Cron trigger added.')).toBeTruthy(), { timeout: 3000 })
    // TRIGGERS card shows the old chips and the new one (preview-mock labels
    // re-fetch async after the list changes)
    await waitFor(() => expect(screen.getByText('0 21 * * *')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('0 8 * * *')).toBeTruthy()
    expect(screen.getByText('123')).toBeTruthy()
    const d = await lastDraftPut()
    expect(d.triggers.map((t) => [t.id, t.enabled])).toEqual([['t1', true], ['t2', true], [undefined, true]])
    expect(d.triggers[2]).toMatchObject({ kind: 'cron', expression: '0 21 * * *', source: 'user' })
  })

  it('an enable op flips only the trigger it names — the others keep their state', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      actions: { triggers: [{ op: 'enable', index: 2, enabled: false }] },
    }))
    render(<CreateFlow />)
    send('pause the discord trigger')
    await waitFor(() => expect(screen.getByText('Discord trigger 2 turned off.')).toBeTruthy(), { timeout: 3000 })
    const d = await lastDraftPut()
    expect(d.triggers.map((t) => [t.id, t.enabled])).toEqual([['t1', true], ['t2', false]])
  })

  it('a disabled trigger renders its chip grayed out — enabled ones keep the accent pair', async () => {
    storeMod.useStore.setState({
      automations: [{
        ...TRIGGERED,
        triggers: [TRIGGERED.triggers[0], { ...TRIGGERED.triggers[1], enabled: false }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    await waitFor(() => expect(screen.getByText('123')).toBeTruthy(), { timeout: 3000 })
    // a disabled trigger falls back to the neutral MetaChip tint (§14)
    const off = screen.getByText('123') as HTMLElement
    expect(off.style.color).toBe('var(--text-muted)')
    expect(off.style.background).toBe('var(--hairline-dim)')
    const on = screen.getByText('0 8 * * *') as HTMLElement
    expect(on.style.color).toBe('var(--accent)')
    expect(on.style.background).toBe('var(--accent-chip-bg)')
  })

  it('param_values stage in the draft only — no PATCH, stored defs untouched, save carries the map', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      actions: { paramValues: { greeting: 'hi' } },
    }))
    render(<CreateFlow />)
    send('set greeting to hi')
    await waitFor(() => expect(screen.getByText('Parameter “greeting” staged — applies when you save.')).toBeTruthy(), { timeout: 3000 })
    // Parameters card marks the unsaved value and shows the staged summary
    expect(screen.getByText('STAGED')).toBeTruthy()
    expect(screen.getByText('hi')).toBeTruthy()
    // §4.2: staged is draft state only — the automation is never PATCHed now
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    const d = await lastDraftPut()
    expect(d.paramValues).toEqual({ greeting: 'hi' })
    // the param definitions (what versions store) keep their old value
    expect(d.params[0]).toMatchObject({ name: 'greeting', value: 'hello' })
    // and the live automation in the store still holds the stored value
    expect((storeMod.useStore.getState().automations[0].params![0] as { value?: string }).value).toBe('hello')
  })

  it('hold-and-flush: a sync-arming response lands its staged chip beneath the sync trail (§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'With greeting.' }],
        actions: { paramValues: { greeting: 'hi' }, sync: true },
      }))
      .mockResolvedValue(done({}))
    render(<CreateFlow />)
    send('set greeting to hi and sync')
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 5000 })
    // the workflow chip group sits contiguously beneath the sync trail — the
    // staged chip was held through the chained sync and flushed after it
    const synced = screen.getByText('Steps synced with the spec.')
    const staged = screen.getByText('Parameter “greeting” staged — applies when you save.')
    expect(synced.compareDocumentPosition(staged) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // the staging itself applied at response time regardless
    const d = await lastDraftPut()
    expect(d.paramValues).toEqual({ greeting: 'hi' })
  })

  it('the derived out-of-sync line closes an unsynced rewrite turn and clears when a sync lands (§11)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten.' }],
        actions: { paramValues: { greeting: 'hi' } },
      }))
      .mockResolvedValue(done({}))
    render(<CreateFlow />)
    send('rewrite it, stage greeting')
    await waitFor(() => expect(screen.getByTestId('chat-outofsync-note')).toBeTruthy(), { timeout: 3000 })
    // no sync armed → the staged chip landed at apply time, the amber line after it
    const staged = screen.getByText('Parameter “greeting” staged — applies when you save.')
    const note = screen.getByTestId('chat-outofsync-note')
    expect(staged.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // a sync clears it — the line is derived, never persisted
    fireEvent.click(screen.getByTestId('chat-sync-now'))
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 5000 })
    expect(screen.queryByTestId('chat-outofsync-note')).toBeNull()
  })

  it('a concurrency action stages in the draft only — chip, STAGED row, no PATCH, PUT carries the object', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      actions: { concurrency: { maxParallel: 2 } },
    }))
    render(<CreateFlow />)
    // CONCURRENCY card always renders its two rows — defaults before staging
    expect(screen.getByText('Max parallel executions')).toBeTruthy()
    expect(screen.getByText('Max queued executions')).toBeTruthy()
    send('let two run at once')
    await waitFor(() => expect(screen.getByText('Concurrency staged — applies when you save.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('STAGED')).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
    // §8: staged is draft state only — the automation is never PATCHed now
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    const d = await lastDraftPut()
    expect((d as { concurrency?: Record<string, number> }).concurrency).toEqual({ maxParallel: 2 })
  })
})

describe('CreateFlow draft undo (§11)', () => {
  beforeEach(armPendingPoll)

  const done = (draft: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat', draft,
  })
  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }

  it('one Undo reverts everything one response rewrote — spec, instructions, and notes', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten body.' }],
      instructions: '- be bold',
      notes: '- Learned a quirk',
    }))
    render(<CreateFlow />)
    send('Change everything')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('Rewritten body.')).toBeTruthy()
    expect(bodyLi('be bold')).toBeTruthy()
    expect(bodyLi('Learned a quirk')).toBeTruthy()
    // the standalone undo row is the page's only undo affordance
    const undos = screen.getAllByText('Undo this change')
    expect(undos).toHaveLength(1)
    fireEvent.click(undos[0])
    // every rewritten document came back, and the dirty flag with them
    expect(screen.getByText('Does things.')).toBeTruthy()
    expect(bodyLi('keep it simple')).toBeTruthy()
    expect(screen.queryByText(/Learned a quirk/)).toBeNull()
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
    expect(screen.queryByText('Undo this change')).toBeNull() // single-level: the snapshot cleared
    // the thread records the rollback for the agent's CONVERSATION context
    expect(screen.getByText('Last change undone — the rewrites above no longer apply.')).toBeTruthy()
    expect(storeMod.useStore.getState().toast).toBe('Last change undone.')
  })

  it('an instructions-only response renders the undo row beneath its system chip', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: null, instructions: '- be bold',
    }))
    render(<CreateFlow />)
    send('Toughen the rules')
    await waitFor(() => expect(screen.getByText('Build instructions updated.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText(/Out of sync/)).toBeTruthy()
    const undos = screen.getAllByText('Undo this change')
    expect(undos).toHaveLength(1)
    // the row sits directly beneath the anchoring chip
    const chip = screen.getByText('Build instructions updated.')
    expect(chip.compareDocumentPosition(undos[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(undos[0])
    expect(bodyLi('keep it simple')).toBeTruthy()
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
  })

  it('a notes-only undo restores the notes and stays in sync', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: null, notes: '- The site rate-limits at 10 rpm',
    }))
    render(<CreateFlow />)
    send('Remember the rate limit')
    await waitFor(() => expect(screen.getByText('Notes updated.')).toBeTruthy(), { timeout: 3000 })
    expect(bodyLi('The site rate-limits at 10 rpm')).toBeTruthy()
    const undos = screen.getAllByText('Undo this change')
    expect(undos).toHaveLength(1)
    fireEvent.click(undos[0])
    expect(screen.queryByText(/rate-limits at 10 rpm/)).toBeNull()
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
  })

  it('undo after a chained sync restores the pre-request steps too', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Synced body.' }],
        actions: { sync: true },
      }))
      .mockResolvedValue({
        id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'sync',
        draft: {
          steps: [{ file: '01-new.py', name: 'Fetch feeds', description: '', code: 'log("new")' }],
          params: [], packages: [], triggers: [],
        },
      })
    render(<CreateFlow />)
    send('Rewrite and sync')
    await waitFor(() => expect(screen.getByText('Steps synced with the spec.')).toBeTruthy(), { timeout: 5000 })
    expect(screen.getByText(/Fetch feeds/)).toBeTruthy() // the sync replaced the steps
    // the completed sync kept the snapshot — Undo reverts the whole request —
    // and re-anchored the row below its own "Steps synced" chip
    const undos = screen.getAllByText('Undo this change')
    expect(undos).toHaveLength(1)
    const synced = screen.getByText('Steps synced with the spec.')
    expect(synced.compareDocumentPosition(undos[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(undos[0])
    expect(screen.getByText('Does things.')).toBeTruthy()
    expect(screen.getByText(/Fetch pages/)).toBeTruthy()
    expect(screen.queryByText(/Fetch feeds/)).toBeNull()
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
  })

  it('the agent triggers the restore via the §8 undo action; a repeat finds nothing', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(done({
        spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten body.' }],
      }))
      .mockResolvedValue(done({ answer: 'Rolling the draft back.', actions: { undo: true } }))
    render(<CreateFlow />)
    send('Change it')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getByText('Rewritten body.')).toBeTruthy()
    send('undo that')
    await waitFor(
      () => expect(screen.getByText('Last change undone — the rewrites above no longer apply.')).toBeTruthy(),
      { timeout: 3000 },
    )
    // same restore as the button: draft back, snapshot consumed, row gone
    expect(screen.getByText('Does things.')).toBeTruthy()
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
    expect(screen.queryByText('Undo this change')).toBeNull()
    expect(storeMod.useStore.getState().toast).toBe('Last change undone.')
    send('undo that again')
    await waitFor(() => expect(screen.getByText('Nothing to undo.')).toBeTruthy(), { timeout: 3000 })
  })

  it('a manual spec Save clears the snapshot — no Undo over newer manual work', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten body.' }],
    }))
    render(<CreateFlow />)
    send('Change it')
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    expect(screen.getAllByText('Undo this change')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('spec-edit'))
    fireEvent.change(screen.getByTestId('spec-editor'),
      { target: { value: '# My auto\nHand-tuned body.' } })
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Save'))
    // §14: the document editor plays its exit before the save applies
    await waitFor(() => expect(screen.getByText('Hand-tuned body.')).toBeTruthy())
    expect(screen.queryByText('Undo this change')).toBeNull()
  })
})

describe('CreateFlow thread progress entry + input lock (§11)', () => {
  beforeEach(armPendingPoll)

  it('chat job: the thread shows the stage with the only spinner; Cancel in the composer (a1354db)', () => {
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'),
      { target: { value: 'Do a thing' } })
    fireEvent.click(screen.getByText('Send'))
    // thread progress entry + the header save hint share the §11 label
    expect(screen.getAllByText('Working on the request…').length).toBe(2)
    const thread = screen.getByTestId('chat-thread')
    expect(within(thread).getByText('Working on the request…')).toBeTruthy()
    expect(spinnersIn(document.body).length).toBe(1)
    expect(spinnersIn(thread).length).toBe(1) // the progress entry's
    for (const card of [screen.getByTestId('build-card'), screen.getByTestId('test-card')]) {
      expect(spinnersIn(card).length).toBe(0)
      expect(within(card).queryByText('Cancel')).toBeNull()
    }
    expect(screen.getAllByText('Cancel').length).toBe(1) // the composer's
  })

  it('sync job: the sync line lives in the thread and the Save hint, never the cards; spinner in the thread, Cancel in the composer', () => {
    render(<CreateFlow />)
    const cards = () => [screen.getByTestId('build-card'), screen.getByTestId('test-card')]
    const buildBefore = screen.getByTestId('build-card').textContent
    fireEvent.click(screen.getByText('Sync spec'))
    // the same live line renders in the thread and as the Save hint (one
    // unified stage vocabulary; no agent · model attribution — the composer's
    // picker names the agent) — and nowhere in the cards: §11 a sync in
    // flight is never a card state
    expect(screen.getAllByText('Syncing the workflow…').length).toBe(2)
    for (const card of cards()) expect(within(card).queryByText('Syncing the workflow…')).toBeNull()
    // the BUILD card keeps its in-sync body byte for byte — it never moves
    expect(screen.getByTestId('build-card').textContent).toBe(buildBefore)
    const buildCard = screen.getByTestId('build-card')
    expect(within(buildCard).getByText(/In sync with the spec\./)).toBeTruthy()
    // §11: the TEST card treats a running sync exactly like out of sync — the
    // steps are about to be rewritten, so it holds the gate row instead
    const testCard = screen.getByTestId('test-card')
    expect(within(testCard).getByText('Sync the steps before testing.')).toBeTruthy()
    expect((within(testCard).getByText('Test draft').closest('button')!).disabled).toBe(true)
    // §11: never an empty section — the live entry shows the stage's canned
    // description bullet until the stream produces a feed
    expect(screen.getByText('• Building the steps from the spec')).toBeTruthy()
    expect(spinnersIn(document.body).length).toBe(1)
    expect(spinnersIn(screen.getByTestId('chat-thread')).length).toBe(1)
    for (const card of cards()) expect(spinnersIn(card).length).toBe(0)
    for (const card of cards()) expect(within(card).queryByText('Cancel')).toBeNull()
    expect(screen.getAllByText('Cancel').length).toBe(1)
    // the BUILD card's sync button disables instead of turning into a cancel
    expect((within(buildCard).getByText('Sync spec').closest('button')!).disabled).toBe(true)
  })

  it('Sync now: its own sync hides the out-of-sync row at the click — BUILD goes quiet, TEST keeps the gate (§11)', () => {
    storeMod.useStore.setState({
      automations: [{
        ...AUTO,
        steps: [{ file: '01-a.py', name: 'Judge', description: '', code: 'log("a")', agent: true, why: 'w', agents: [{ id: 'g2' }] }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('qwen3:8b')) // open a grant gap → out of sync
    const buildCard = () => screen.getByTestId('build-card')
    const testCard = () => screen.getByTestId('test-card')
    expect(within(buildCard()).getByText('Sync now')).toBeTruthy()
    fireEvent.click(screen.getByText('Sync now'))
    // the out-of-sync row is gone: no amber reason line, no Sync now — the
    // in-sync row takes its place with its control locked, while the TEST card
    // keeps the §11 gate row (a running sync gates it exactly like out of sync)
    expect(within(buildCard()).queryByText('Sync now')).toBeNull()
    expect(within(buildCard()).queryByText(/Out of sync/)).toBeNull()
    expect(within(testCard()).getByText('Sync the steps before testing.')).toBeTruthy()
    expect(within(buildCard()).queryByText('Syncing the workflow…')).toBeNull()
    expect((within(buildCard()).getByText('Sync spec').closest('button')!).disabled).toBe(true)
    expect((within(testCard()).getByText('Test draft').closest('button')!).disabled).toBe(true)
    // the live surface is the thread progress entry (plus the Save hint)
    expect(screen.getAllByText('Syncing the workflow…').length).toBe(2)
  })

  it('first turn: the unified stage walk — request → documents → chained sync, installs as bullets', async () => {
    storeMod.useStore.setState({ createFrom: 'app' })
    const spec = [{ kind: 'h1', text: 'Folder watcher' }, { kind: 'p', text: 'Watches.' }]
    const building = (mode: string, stage: string, detail: string | null) => ({
      id: 'j1', status: 'building', stage, detail, error: null, mode, draft: null,
    })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(building('chat', 'Updating the documents', 'Writing the spec · 3 lines'))
      .mockResolvedValueOnce({
        id: 'j1', status: 'done', stage: 'Updating the documents', detail: null,
        error: null, mode: 'chat', draft: { spec, actions: { sync: true } },
      })
      .mockResolvedValue(building('sync', 'Syncing the workflow', 'Installing requests…'))
    render(<CreateFlow />)
    fireEvent.change(screen.getByPlaceholderText('Describe the job — one sentence is enough.'),
      { target: { value: 'Watch my Downloads folder' } })
    fireEvent.click(screen.getByText('Send'))
    // the chat job opens at the neutral deciding stage (the pre-poll default)
    expect(screen.getByTestId('chat-progress').textContent).toContain('Working on the request…')
    // the documents phase shows the finer detail line as a bullet
    await waitFor(() => expect(screen.getByText('• Writing the spec · 3 lines')).toBeTruthy(), { timeout: 3000 })
    // §8: installs are bullets under the chained sync's workflow stage, never
    // a stage label of their own
    await waitFor(() => expect(screen.getByText('• Installing requests…')).toBeTruthy(), { timeout: 5000 })
    expect(screen.getAllByText('Syncing the workflow…').length).toBeGreaterThan(0)
    expect(screen.queryByText('Installing the packages…')).toBeNull()
    // §11: the chained sync never moves the BUILD card — no out-of-sync row
    // (the rewrite dirtied the draft, but the armed/running sync counts as in
    // sync for the cards), the sync line only in the thread + Save hint
    const buildCard = screen.getByTestId('build-card')
    expect(within(buildCard).queryByText('Sync now')).toBeNull()
    expect(within(buildCard).queryByText(/Out of sync/)).toBeNull()
    expect(within(buildCard).queryByText('Syncing the workflow…')).toBeNull()
    expect(within(buildCard).getByText(/In sync with the spec\./)).toBeTruthy()
  })

  it('Esc cancels a chat job like the composer Cancel and returns the prompt to the input', () => {
    render(<CreateFlow />)
    const input = () => screen.getByPlaceholderText('Change something, or ask a question…') as HTMLTextAreaElement
    fireEvent.change(input(), { target: { value: 'Do a thing' } })
    fireEvent.click(screen.getByText('Send'))
    expect(screen.getByText('Cancel')).toBeTruthy()
    expect(input().value).toBe('')
    fireEvent.keyDown(document, { key: 'Escape' })
    // job settled: Send is back and the request text returned to the input,
    // which takes focus with the caret at the end (§11 composer cancel)
    expect(screen.queryByText('Cancel')).toBeNull()
    expect(screen.getByText('Send')).toBeTruthy()
    expect(input().value).toBe('Do a thing')
    expect(document.activeElement).toBe(input())
    expect(input().selectionStart).toBe('Do a thing'.length)
    expect(input().selectionEnd).toBe('Do a thing'.length)
  })

  it('the composer Cancel button also refocuses the input with the caret at the end', () => {
    render(<CreateFlow />)
    const input = () => screen.getByPlaceholderText('Change something, or ask a question…') as HTMLTextAreaElement
    fireEvent.change(input(), { target: { value: 'Another thing' } })
    fireEvent.click(screen.getByText('Send'))
    fireEvent.click(screen.getByText('Cancel'))
    expect(input().value).toBe('Another thing')
    expect(document.activeElement).toBe(input())
    expect(input().selectionStart).toBe('Another thing'.length)
  })

  it('Esc cancels a running sync; idle Esc is inert', async () => {
    render(<CreateFlow />)
    fireEvent.keyDown(document, { key: 'Escape' }) // no job — nothing happens
    expect(screen.getByText('Send')).toBeTruthy()
    fireEvent.click(screen.getByText('Sync spec'))
    expect(screen.getByText('Cancel')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Cancel')).toBeNull()
    // the cancel landed while the POST was in flight — the gen-guard cancels
    // the freshly created job once the POST resolves
    await waitFor(() => expect(mockedApi.cancelDraftJob).toHaveBeenCalled())
  })

  it('a live test disables the input with the wait placeholder', () => {
    storeMod.useStore.setState({
      test: { executionId: 'e9' },
      executions: [{
        id: 'e9', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'v1',
        status: 'executing', trigger: 'Test', triggerSender: null, test: true,
        duration: '', started: '', startedMs: 1, endedMs: 0, queuedMs: 0, note: null, error: null,
      }] as never,
    })
    render(<CreateFlow />)
    const input = screen.getByPlaceholderText('Wait for the test to finish.') as HTMLTextAreaElement
    expect(input.disabled).toBe(true)
    expect((screen.getByText('Send').closest('button')!).disabled).toBe(true)
  })
})

describe('CreateFlow clear chat (§11)', () => {
  beforeEach(armPendingPoll)
  const done = (draft: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat', draft,
  })
  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'), { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }
  const clearBtn = () => screen.getByTestId('chat-clear') as HTMLButtonElement

  it('disabled on an empty thread and while a job runs; confirm empties the thread and the undo snapshot', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'Rewritten body.' }],
    }))
    render(<CreateFlow />)
    expect(clearBtn().disabled).toBe(true) // empty thread
    send('Change it')
    expect(clearBtn().disabled).toBe(true) // job in flight
    await waitFor(() => expect(screen.getByText('Spec updated.')).toBeTruthy(), { timeout: 3000 })
    expect(clearBtn().disabled).toBe(false)
    expect(screen.getAllByText('Undo this change')).toHaveLength(1)
    // confirm step — cancelling keeps the thread
    fireEvent.click(clearBtn())
    expect(screen.getByText('Clear this conversation?')).toBeTruthy()
    fireEvent.click(screen.getByText('Cancel'))
    expect(screen.getByText('Spec updated.')).toBeTruthy()
    // confirming empties the thread; the undo row's snapshot clears with it
    fireEvent.click(clearBtn())
    fireEvent.click(document.querySelector('.ad-btn-danger-ghost') as HTMLButtonElement)
    await waitFor(() => expect(screen.queryByText('Spec updated.')).toBeNull())
    expect(screen.queryByText('Undo this change')).toBeNull()
    expect(clearBtn().disabled).toBe(true) // empty again
    // the draft itself is untouched: still out of sync from the rewrite
    expect(screen.getByText('Out of sync — steps still match the old spec.')).toBeTruthy()
    // the composer still works
    expect((screen.getByPlaceholderText('Change something, or ask a question…') as HTMLTextAreaElement).disabled).toBe(false)
  })
})

describe('CreateFlow left-column cards + test-failure repair (§11)', () => {
  beforeEach(armPendingPoll)

  it('agents and secrets cards default collapsed with counts; warnings force them open', () => {
    storeMod.useStore.setState({
      automations: [{
        ...AUTO,
        steps: [{ file: '01-a.py', name: 'Judge', description: '', code: `x = secrets["${CRM_ID}"]`, agent: true, why: 'w', agents: [{ id: 'g2' }] }],
      } as unknown as Automation],
    })
    render(<CreateFlow />)
    expect(screen.getByText('2 of 2 enabled')).toBeTruthy()
    expect(screen.getByText('2 of 2 allowed')).toBeTruthy()
    expect(collapseOf(screen.getByText('Cloud writer')).classList.contains('open')).toBe(false)
    expect(collapseOf(screen.getByText('MAIL_PASSWORD')).classList.contains('open')).toBe(false)
    // unchecking the called agent opens the card on its warning…
    fireEvent.click(screen.getByText('qwen3:8b'))
    expect(collapseOf(rowText('Cloud writer')).classList.contains('open')).toBe(true)
    expect(screen.getByText(/isn’t enabled here/)).toBeTruthy()
    // …and re-checking collapses it again (never sticky)
    fireEvent.click(screen.getByText('qwen3:8b'))
    expect(collapseOf(rowText('Cloud writer')).classList.contains('open')).toBe(false)
    // same for a disallowed secret the steps use (the step tag renders the
    // name too — target the card's own checkbox row)
    const secCard = cardOf(screen.getByText('SECRETS · ALLOWED FOR STEPS'))
    fireEvent.click(within(secCard).getByText('CRM_API_KEY'))
    expect(collapseOf(rowText('MAIL_PASSWORD')).classList.contains('open')).toBe(true)
    expect(screen.getByText(/isn’t allowed here/)).toBeTruthy()
  })

  it('NOTES card: collapsed by default, view/edit works, and never marks the workflow out of sync', async () => {
    storeMod.useStore.setState({
      automations: [{ ...AUTO, notes: '- Site rate-limits at 10 rpm' } as unknown as Automation],
    })
    render(<CreateFlow />)
    const body = bodyLi('Site rate-limits at 10 rpm')
    expect(collapseOf(body).classList.contains('open')).toBe(false)
    fireEvent.click(screen.getByText('NOTES'))
    expect(collapseOf(body).classList.contains('open')).toBe(true)
    const card = cardOf(screen.getByText('NOTES'))
    fireEvent.click(within(card).getByText('Edit'))
    fireEvent.change(screen.getByTestId('notes-editor'), { target: { value: '- Pruned' } })
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Save'))
    await waitFor(() => expect(bodyLi('Pruned')).toBeTruthy())
    // §4.1: notes never mark the workflow out of sync or block saving
    expect(screen.getByText(/In sync with the spec/)).toBeTruthy()
    expect(screen.queryByText(/Out of sync/)).toBeNull()
    expect((screen.getByText('Save as v2').closest('button')!).disabled).toBe(false)
  })

  it('Analyze failure posts the canned chat message with the run id', async () => {
    const failed = {
      id: 'e9', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'v1',
      status: 'failed', trigger: 'Test', triggerSender: null, test: true,
      duration: '1s', started: '', startedMs: 1, endedMs: 2, queuedMs: 0, note: null,
      error: { step: 'Fetch pages', message: 'boom', reason: null }, steps: [],
    }
    storeMod.useStore.setState({
      test: { executionId: 'e9' }, executions: [failed] as never, executionFull: { e9: failed } as never,
    })
    render(<CreateFlow />)
    expect(screen.getByText('Test failed.')).toBeTruthy()
    fireEvent.click(screen.getByText('Analyze failure'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    const body = draftBody(0)
    expect(body.mode).toBe('chat')
    expect(body.executionId).toBe('e9')
    expect(body.text).toBe('The test failed at step Fetch pages — figure out why. If the automation is at fault, fix it; if it’s something I need to do on this Mac, tell me what to do and how instead.')
    // the canned message lands as a user entry in the thread
    expect(screen.getByText(body.text as string)).toBeTruthy()
    // §11: while the chat job runs the button disables — never hidden
    const analyze = screen.getByText('Analyze failure').closest('button')!
    expect(analyze.disabled).toBe(true)
  })

  it('NOTES card: Edit is offered even while the notes are empty', async () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('NOTES'))
    const card = cardOf(screen.getByText('NOTES'))
    expect(within(card).getAllByText(/No notes yet/).length).toBeGreaterThan(0)
    fireEvent.click(within(card).getByText('Edit'))
    // §11: Edit opens the document editor on an empty notes.md
    const modal = screen.getByTestId('doc-editor')
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Edit notes.md')
    expect(within(modal).getByTestId('doc-lines').textContent).toBe('0 lines')
    const ta = screen.getByTestId('notes-editor')
    fireEvent.change(ta, { target: { value: '- Added by hand' } })
    fireEvent.click(within(modal).getByText('Save'))
    await waitFor(() => expect(bodyLi('Added by hand')).toBeTruthy())
  })

  it('rename pencils hide on the create empty state and show once a revision exists', () => {
    storeMod.useStore.setState({ createFrom: 'app', automationId: null })
    render(<CreateFlow />)
    expect(screen.getByText('New automation')).toBeTruthy()
    expect(screen.queryByTitle('Rename')).toBeNull()
    expect(screen.queryByTitle('Edit the description')).toBeNull()
    cleanup()
    // edit mode viewing the draft: a revision exists — both pencils render
    storeMod.useStore.setState({ createFrom: 'edit', automationId: 'a1' })
    render(<CreateFlow />)
    expect(screen.getByTitle('Rename')).toBeTruthy()
    expect(screen.getByTitle('Edit the description')).toBeTruthy()
  })

  it('title rename into another automation’s name shows the inline error and posts nothing (§4.1)', () => {
    storeMod.useStore.setState({ automations: [AUTO, { ...AUTO, id: 'a2', name: 'Other auto' }] })
    render(<CreateFlow />)
    fireEvent.click(screen.getByTitle('Rename'))
    const input = screen.getByDisplayValue('My auto')
    // case-insensitive and trimmed — padding can't dodge the check
    fireEvent.change(input, { target: { value: ' other AUTO ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('An automation named other AUTO already exists — pick a different name.')).toBeTruthy()
    expect(mockedApi.patchAutomation).not.toHaveBeenCalled()
    // the input stayed open; typing clears the error and a free name commits
    fireEvent.change(input, { target: { value: 'Fresh name' } })
    expect(screen.queryByText(/already exists/)).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockedApi.patchAutomation).toHaveBeenCalledWith('a1', { name: 'Fresh name' })
  })

  it('zero agents: edit mode redirects to Agents with the toast', () => {
    storeMod.useStore.setState({ agents: [] })
    render(<CreateFlow />)
    const s = storeMod.useStore.getState()
    expect(s.surface).toBe('app')
    expect(s.page).toBe('agents')
    expect(s.toast).toBe('No agent yet — add one here first. Creating and editing automations needs an AI.')
  })

  it('drafting-agent picker: selecting toasts, a busy rewrite disables it', () => {
    render(<CreateFlow />)
    const pick = screen.getByTitle('The agent that writes the spec and generates the steps') as HTMLButtonElement
    expect(pick.textContent).toContain('Cloud writer · Default model')
    fireEvent.click(pick)
    fireEvent.click(within(pick.parentElement!).getByText('Fast local'))
    expect(storeMod.useStore.getState().toast).toBe('Fast local · qwen3:8b now writes the spec and steps here.')
    expect(pick.textContent).toContain('Fast local · qwen3:8b')
    // a running sync locks the picker
    fireEvent.click(screen.getByText('Sync spec'))
    expect(pick.disabled).toBe(true)
  })
})

describe('CreateFlow boundary markers + history-inert thread (§4.4/§11)', () => {
  beforeEach(armPendingPoll)
  const getChatMock = () => mockedApi.getChat as ReturnType<typeof vi.fn>

  it('renders the marker with the history explainer; a marker-terminated thread offers no actions', async () => {
    getChatMock().mockResolvedValueOnce({ chat: [
      { id: 'h1', kind: 'user', text: 'old request' },
      { id: 'h2', kind: 'blockers', source: 'sync', blockers: [{ reason: 'r', fix: 'f' }] },
      { id: 'm1', kind: 'system', icon: 'fa-flag-checkered', boundary: true, text: 'Draft saved as v2.' },
    ] })
    render(<CreateFlow />)
    // the stored thread merges in with the marker as its last entry
    await screen.findByText('Draft saved as v2.')
    // §11: the marker is the one system chip with a description bullet — the
    // derived history explainer
    screen.getByText(/The messages above are from that draft — your AI no longer reads them/)
    // §11: no divider while the marker is the thread's last entry — the rule
    // only sits between a settled conversation and the next one
    expect(screen.queryByTestId('chat-boundary-divider')).toBeNull()
    // §11 history-inert: the turn action row never renders under a settled
    // session — the in-sync draft would otherwise offer the Test-draft pill
    expect(screen.queryByTestId('chat-turn-actions')).toBeNull()
    // a history blockers entry collapses to its dismissed summary whatever its
    // stored flag says — its Dismiss/Apply buttons are gone with it
    screen.getByText('1 blocker — dismissed')
    expect(screen.queryByText('Your AI hit a blocker')).toBeNull()
    expect(screen.queryByText('Apply to the spec & sync')).toBeNull()
  })

  it('entries after the marker act normally — the turn action row returns with the new session', async () => {
    getChatMock().mockResolvedValueOnce({ chat: [
      { id: 'm1', kind: 'system', icon: 'fa-flag-checkered', boundary: true, text: 'Draft discarded.' },
      { id: 'n1', kind: 'system', icon: 'fa-vial', text: 'Draft execution succeeded.' },
    ] })
    render(<CreateFlow />)
    await screen.findByText('Draft discarded.')
    // an entry follows the marker → the divider renders, and only under the
    // marker (the plain system chip after it carries none)
    expect(screen.getAllByTestId('chat-boundary-divider')).toHaveLength(1)
    // post-boundary entry at the end → the in-sync draft's Test pill is back
    await waitFor(() => expect(screen.getByTestId('chat-turn-actions')).toBeTruthy())
    screen.getByTestId('chat-test-draft')
  })

  it('create mode: persisted error entries render with no Try again anywhere', async () => {
    // The removed create pipeline's spec-call errors carried a Try-again pill;
    // unified chat failures never do — legacy persisted entries render plain.
    // A pending draft resumes here, so the slot thread merges (§4.4).
    storeMod.useStore.setState({ createFrom: 'app', automationId: null })
    ;(mockedApi.getDraft as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      draft: { spec: [{ kind: 'h1', text: 'Kept' }, { kind: 'p', text: 'Body.' }], steps: [] },
      agentId: null,
    })
    getChatMock().mockResolvedValueOnce({ chat: [
      { id: 'e1', kind: 'error', source: 'spec', text: 'old failure' },
      { id: 'm1', kind: 'system', icon: 'fa-flag-checkered', boundary: true, text: 'Draft discarded.' },
      { id: 'e2', kind: 'error', source: 'spec', text: 'fresh failure' },
    ] })
    render(<CreateFlow />)
    await screen.findByText('fresh failure')
    screen.getByText('old failure') // history stays visible…
    // …and neither session's failure offers a retry pill
    expect(screen.queryByText('Try again')).toBeNull()
  })

  it('fresh create entry discards a leftover slot thread — the suggestions stay (§4.4 fresh-entry clear)', async () => {
    // No pending draft to resume: the settled session's thread must never
    // replay over the create empty state — it is dropped and unlinked.
    storeMod.useStore.setState({ createFrom: 'app', automationId: null })
    getChatMock().mockResolvedValueOnce({ chat: [
      { id: 'a1', kind: 'activity', title: 'Working on the request…', text: 'Choosing what to do', outcome: 'done' },
      { id: 'm1', kind: 'system', icon: 'fa-flag-checkered', boundary: true, text: 'Draft discarded.' },
    ] })
    render(<CreateFlow />)
    await waitFor(() => expect(mockedApi.putChat).toHaveBeenCalledWith('pending', []))
    // the empty state stands; nothing from the old session renders
    screen.getByRole('heading', { name: 'What should Autowright do for you?' })
    expect(screen.queryByText('Working on the request…')).toBeNull()
    expect(screen.queryByText('Draft discarded.')).toBeNull()
  })

  it('a resumed pending draft keeps its slot thread (§4.4 — the clear is entry-without-draft only)', async () => {
    storeMod.useStore.setState({ createFrom: 'app', automationId: null })
    ;(mockedApi.getDraft as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      draft: { spec: [{ kind: 'h1', text: 'Kept' }, { kind: 'p', text: 'Body.' }], steps: [] },
      agentId: null,
    })
    getChatMock().mockResolvedValueOnce({ chat: [
      { id: 'm1', kind: 'system', icon: 'fa-flag-checkered', boundary: true, text: 'Draft discarded.' },
    ] })
    render(<CreateFlow />)
    await screen.findByText('Draft discarded.')
    expect(mockedApi.putChat).not.toHaveBeenCalledWith('pending', [])
  })
})

describe('CreateFlow old-version view: thread survival + test gating (§11)', () => {
  beforeEach(armPendingPoll)
  const V1_ROW = {
    version: 1, when: 'Jul 1', note: null,
    spec: AUTO.spec, steps: AUTO.steps, instructions: '', notes: '', params: [], packages: [],
  }
  const testRow = (status: string) => ({
    id: 'e9', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'Test',
    status, trigger: 'Test', triggerSender: null, test: true, steps: [],
    duration: '', started: '', startedMs: 1, endedMs: status === 'executing' ? 0 : 2,
    queuedMs: 0, note: null, error: null,
  })

  it('a test chip landed while viewing v1 survives Back to draft (shown blocks never removed)', async () => {
    storeMod.useStore.setState({
      automations: [{ ...AUTO, version: 2, versions: [V1_ROW] } as unknown as Automation],
      test: { executionId: 'e9' },
      executions: [testRow('executing')] as never,
      executionFull: { e9: testRow('executing') } as never,
    })
    render(<CreateFlow />)
    // touch the draft so leaving the Draft view stashes it (§4.4)
    fireEvent.click(rowText('Fast local'))
    fireEvent.click(screen.getByTestId('version-menu'))
    fireEvent.click(screen.getByText('v1'))
    expect(screen.getByText(/Loaded v1 from history/)).toBeTruthy()
    // the live test settles while v1 is viewed - the run chip lands in the thread
    storeMod.useStore.setState({
      executions: [testRow('succeeded')] as never,
      executionFull: { e9: testRow('succeeded') } as never,
    })
    // §11: the settled TEST card carries the same short line - the chip is the
    // thread's own, so both assertions read the thread
    const thread = () => screen.getByTestId('chat-thread')
    await waitFor(() => expect(within(thread()).getByText('Test succeeded.')).toBeTruthy())
    // returning to the draft keeps the live thread - the chip never vanishes
    fireEvent.click(screen.getByText('Back to draft'))
    expect(screen.queryByText(/Loaded v1 from history/)).toBeNull()
    expect(within(thread()).getByText('Test succeeded.')).toBeTruthy()
  })

  it('viewing an old version disables the test controls - an old version is never tested', async () => {
    storeMod.useStore.setState({
      automations: [{ ...AUTO, version: 2, versions: [V1_ROW] } as unknown as Automation],
    })
    render(<CreateFlow />)
    expect((screen.getByTestId('test-draft-toggle') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByTestId('version-menu'))
    fireEvent.click(screen.getByText('v1'))
    const toggle = screen.getByTestId('test-draft-toggle') as HTMLButtonElement
    expect(toggle.disabled).toBe(true)
    fireEvent.click(toggle) // inert - the modal never opens
    expect(screen.queryByTestId('test-modal')).toBeNull()
    expect(screen.queryByText('Run test')).toBeNull()
    expect(mockedApi.postTest).not.toHaveBeenCalled()
    // back on the draft the toggle re-enables
    fireEvent.click(screen.getByText('Back to draft'))
    expect((screen.getByTestId('test-draft-toggle') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('CreateFlow send/sync edit guard + settle flush + poll retry (§11)', () => {
  beforeEach(armPendingPoll)
  const done = (draft: Record<string, unknown>) => ({
    id: 'j1', status: 'done', stage: null, detail: null, error: null, mode: 'chat', draft,
  })
  const send = (text: string) => {
    fireEvent.change(screen.getByPlaceholderText('Change something, or ask a question…'), { target: { value: text } })
    fireEvent.click(screen.getByText('Send'))
  }
  const STAGED_CHIP = 'Parameter “greeting” staged — applies when you save.'

  it('leaving with Keep draft mid-chained-sync flushes the held workflow chips (hold-and-flush)', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValueOnce(done({
      spec: [{ kind: 'h1', text: 'My auto' }, { kind: 'p', text: 'With greeting.' }],
      actions: { paramValues: { greeting: 'hi' }, sync: true },
    }))
    render(<CreateFlow />)
    send('stage greeting and sync')
    // the chat settles, the chained sync starts and never answers (held chips)
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(screen.queryByText(STAGED_CHIP)).toBeNull() // held, not landed
    // back (the §4.4 keep path) - the settle flush lands the receipts
    fireEvent.click(screen.getAllByText('My auto').find((el) => el.closest('button'))!)
    await waitFor(() => {
      const calls = (mockedApi.putChat as ReturnType<typeof vi.fn>).mock.calls
      expect(calls.some((c) => (c[1] as Array<{ text?: string }>).some((e) => e.text === STAGED_CHIP))).toBe(true)
    })
    await waitFor(() => expect(screen.getByText(STAGED_CHIP)).toBeTruthy())
  })

  it('leaving mid-chat-job never cancels it — the job keeps building in the background (§19)', async () => {
    render(<CreateFlow />)
    send('rename everything')
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    // leave via the header back — §19 background continuation: the exit
    // flushes the thread with the pending user entry as-is (no "Edit stopped"
    // chip: the turn is still running) and never DELETEs the job.
    fireEvent.click(screen.getAllByText('My auto').find((el) => el.closest('button'))!)
    await waitFor(() => {
      const calls = (mockedApi.putChat as ReturnType<typeof vi.fn>).mock.calls
      const flushed = calls.map((c) => c[1] as Array<{ kind: string; text?: string }>)
      expect(flushed.some((list) =>
        list.some((e) => e.kind === 'user' && e.text === 'rename everything'))).toBe(true)
      expect(flushed.some((list) =>
        list.some((e) => e.text === 'Edit stopped — the spec is unchanged.'))).toBe(false)
    })
    expect(mockedApi.cancelDraftJob).not.toHaveBeenCalled()
  })

  it('sending under an unsaved spec edit asks first; cancel keeps both texts, confirm proceeds', async () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByTestId('spec-edit'))
    fireEvent.change(screen.getByTestId('spec-editor'), { target: { value: '# My auto\nTyped change.' } })
    const input = screen.getByPlaceholderText('Change something, or ask a question…') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'and also weekends' } })
    fireEvent.click(screen.getByText('Send'))
    // the discard confirm gates the send - no job yet
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Discard your spec edits?')).toBeTruthy()
    expect(mockedApi.postDraftJob).not.toHaveBeenCalled()
    // cancelling aborts: composer text and the open editor both survive
    fireEvent.click(within(dialog).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(input.value).toBe('and also weekends')
    expect((screen.getByTestId('spec-editor') as HTMLTextAreaElement).value).toBe('# My auto\nTyped change.')
    expect(mockedApi.postDraftJob).not.toHaveBeenCalled()
    // confirming discards the edits and the send proceeds
    fireEvent.click(screen.getByText('Send'))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Discard edits'))
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1))
    expect(draftBody(0).text).toBe('and also weekends')
    expect(screen.queryByTestId('spec-editor')).toBeNull()
  })

  it('starting a sync under an unsaved instructions edit asks the same way', async () => {
    render(<CreateFlow />)
    const card = cardOf(screen.getByText('BUILD INSTRUCTIONS'))
    fireEvent.click(screen.getByText('BUILD INSTRUCTIONS'))
    fireEvent.click(within(card).getByText('Edit'))
    fireEvent.change(screen.getByTestId('instructions-editor'), { target: { value: '- new rule' } })
    fireEvent.click(screen.getByText('Sync spec'))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Discard your instruction edits?')).toBeTruthy()
    fireEvent.click(within(dialog).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(mockedApi.postDraftJob).not.toHaveBeenCalled()
    expect((screen.getByTestId('instructions-editor') as HTMLTextAreaElement).value).toBe('- new rule')
  })

  it('Fix with AI waits for the stored thread - the job carries the kept history and the seed', async () => {
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ chat: [
      { id: 'c1', at: '2026-08-01T00:00:00Z', kind: 'user', text: 'Earlier question' },
    ] })
    const failed = {
      id: 'e7', automationId: 'a1', automationName: 'My auto', automationDeleted: false, versionLabel: 'v1',
      status: 'failed', trigger: 'Manual', triggerSender: null, test: false, steps: [],
      duration: '1s', started: '', startedMs: 1, endedMs: 2, queuedMs: 0, note: null,
      error: { step: 'Fetch pages', message: 'boom', reason: null },
    }
    storeMod.useStore.setState({ fixExec: 'e7', executions: [failed] as never, executionFull: { e7: failed } as never })
    render(<CreateFlow />)
    await waitFor(() => expect(mockedApi.postDraftJob).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const body = draftBody(0)
    expect(body.executionId).toBe('e7')
    expect(String(body.text)).toMatch(/^This execution failed/)
    const chat = body.chat as Array<{ kind: string; text?: string }>
    expect(chat.some((e) => e.text === 'Earlier question')).toBe(true)
    expect(chat.some((e) => e.kind === 'system' && /Execution failed at step Fetch pages/.test(e.text ?? ''))).toBe(true)
  })

  it('one transient poll error never fails the job - the next tick recovers it', async () => {
    let calls = 0
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      calls += 1
      if (calls === 1) throw new Error('socket hiccup')
      return done({ answer: 'All good' })
    })
    render(<CreateFlow />)
    send('hello')
    await waitFor(() => expect(screen.getByText('All good')).toBeTruthy(), { timeout: 6000 })
    expect(screen.queryByText('Something went wrong')).toBeNull()
  })

  it('three consecutive poll failures give up with the error entry', async () => {
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      throw new Error('backend gone')
    })
    render(<CreateFlow />)
    send('hello')
    await waitFor(() => expect(screen.getByText('Something went wrong')).toBeTruthy(), { timeout: 8000 })
    expect(screen.getByText('backend gone')).toBeTruthy()
  })
})

describe('CreateFlow background continuation & re-attach (§11/§19)', () => {
  beforeEach(() => {
    armPendingPoll()
    storeMod.useStore.setState({ draftJobs: [] })
  })
  const USER_ENTRY = { id: 'u1', at: '2026-08-20T08:00:00', kind: 'user' as const, text: 'add weekends' }
  const jobRow = (status: string, mode: 'chat' | 'sync' = 'chat') =>
    [{ owner: 'a1', jobId: 'jx', status, mode }]

  it('re-attaches a building chat job on entry — poll resumes, no cancel, no chip', async () => {
    storeMod.useStore.setState({ draftJobs: jobRow('building') })
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({ chat: [USER_ENTRY] })
    render(<CreateFlow />)
    await waitFor(() => expect(mockedApi.getDraftJob).toHaveBeenCalledWith('jx'))
    // the composer swaps Send for the running job's Cancel (§11 in-flight rules)
    await waitFor(() => expect(screen.getByText('Cancel')).toBeTruthy())
    expect(screen.queryByText('Edit stopped — the spec is unchanged.')).toBeNull()
    expect(mockedApi.postDraftJob).not.toHaveBeenCalled()
    expect(mockedApi.cancelDraftJob).not.toHaveBeenCalled()
  })

  it('applies a held chat outcome on entry exactly like a live settle, then acks', async () => {
    storeMod.useStore.setState({ draftJobs: jobRow('done') })
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({ chat: [USER_ENTRY] })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'jx', status: 'done', stage: 'Working on the request', detail: null, error: null,
      mode: 'chat', events: [], draft: { spec: null, answer: 'All set.' },
    })
    render(<CreateFlow />)
    await waitFor(() => expect(screen.getByText('All set.')).toBeTruthy(), { timeout: 4000 })
    expect(screen.getByText('From your AI')).toBeTruthy()
    await waitFor(() => expect(mockedApi.ackDraftJob).toHaveBeenCalledWith('jx'))
    // the settled stage trail landed too — never a bare outcome
    expect(screen.getByText(/Working on the request/)).toBeTruthy()
  })

  it('a vanished job closes the orphaned turn with the Edit-stopped chip', async () => {
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({ chat: [USER_ENTRY] })
    render(<CreateFlow />)
    await waitFor(() => expect(screen.getByText('Edit stopped — the spec is unchanged.')).toBeTruthy())
    expect(mockedApi.getDraftJob).not.toHaveBeenCalled()
  })

  it('a background settle drops trigger ops when the base list is not the one the agent saw', async () => {
    storeMod.useStore.setState({ draftJobs: jobRow('done') })
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({ chat: [USER_ENTRY] })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'jx', status: 'done', stage: 'Working on the request', detail: null, error: null,
      mode: 'chat', events: [],
      // the agent saw a one-entry list; the editor's base list is empty now
      sentTriggers: [{ kind: 'cron', expression: '0 8 * * *', enabled: true }],
      draft: { spec: null, actions: { triggers: [{ op: 'add', trigger: { kind: 'cron', expression: '0 9 * * *', enabled: true } }] } },
    })
    render(<CreateFlow />)
    await waitFor(() => expect(screen.getByText('Trigger changes dropped — the triggers changed while your AI worked. Ask again.')).toBeTruthy(), { timeout: 4000 })
    expect(screen.queryByText('Cron trigger added.')).toBeNull()
  })

  it('a background settle applies trigger ops when the base list still matches', async () => {
    storeMod.useStore.setState({ draftJobs: jobRow('done') })
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({ chat: [USER_ENTRY] })
    ;(mockedApi.getDraftJob as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'jx', status: 'done', stage: 'Working on the request', detail: null, error: null,
      mode: 'chat', events: [], sentTriggers: [],
      draft: { spec: null, actions: { triggers: [{ op: 'add', trigger: { kind: 'cron', expression: '0 9 * * *', enabled: true } }] } },
    })
    render(<CreateFlow />)
    await waitFor(() => expect(screen.getByText('Cron trigger added.')).toBeTruthy(), { timeout: 4000 })
    expect(screen.queryByText(/Trigger changes dropped/)).toBeNull()
  })

  it('a slot that owns a building job keeps its thread and re-attaches (fresh-entry clear skipped)', async () => {
    storeMod.useStore.setState({
      createFrom: 'new' as never, automationId: null,
      draftJobs: [{ owner: 'pending', jobId: 'jp', status: 'building', mode: 'chat' }],
    })
    ;(mockedApi.getDraft as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      draft: null, agentId: null, job: { jobId: 'jp', status: 'building', mode: 'chat' },
    })
    ;(mockedApi.getChat as ReturnType<typeof vi.fn>).mockResolvedValue({
      chat: [{ id: 'u9', at: '2026-08-20T08:00:00', kind: 'user', text: 'watch a price' }],
    })
    render(<CreateFlow />)
    // §11: the job ref counts as something to resume — the thread stays
    // (never PUT [] over it) and the poll re-attaches to the slot's job.
    await waitFor(() => expect(mockedApi.getDraftJob).toHaveBeenCalledWith('jp'))
    expect(mockedApi.putChat).not.toHaveBeenCalledWith('pending', [])
    expect(screen.getByText('watch a price')).toBeTruthy()
  })
})

// §5.1/§11: an imported automation whose agent / secret references matched
// nothing on this Mac. The §4.1 unresolvedReferences map names the archive
// records, so the editor's warnings and red rows read as names, not short ids.
describe('§5.1/§11 imported unresolved references', () => {
  const IMP_SECRET_ID = '33333333-3333-4333-8333-333333333333'
  const IMP_AGENT_ID = '44444444-4444-4444-8444-444444444444'
  const IMPORTED = {
    ...AUTO,
    steps: [{
      file: '01-a.py', name: 'Fetch pages', description: '', agent: true,
      agents: [{ id: IMP_AGENT_ID }], code: `k = secrets["${IMP_SECRET_ID}"]`,
    }],
    unresolvedReferences: {
      [IMP_SECRET_ID]: { kind: 'secret', name: 'STRIPE_KEY', description: 'billing token' },
      [IMP_AGENT_ID]: { kind: 'agent', name: 'Researcher', description: 'reads the web' },
    },
  } as unknown as Automation

  it('the agents and secrets cards name the archive records', () => {
    storeMod.useStore.setState({ automations: [IMPORTED] })
    render(<CreateFlow />)
    expect(screen.getByText(
      'Step 1 calls Researcher from the imported file, which has no match on this Mac'
      + ' - pick an agent or ask your AI to fix it.')).toBeTruthy()
    expect(screen.getByText(
      'STRIPE_KEY came from the imported file and has no match on this Mac'
      + ' - pick one of your secrets or ask your AI to fix it.')).toBeTruthy()
    // the missing-secret row heads with the archive name, not the short id
    expect(screen.getByText(
      'used by step 1 - no match on this Mac; pick a secret or ask your AI to fix it')).toBeTruthy()
    expect(screen.queryByText('33333333…')).toBeNull()
  })

  it('without the map the same ids keep the deleted-reference wording', () => {
    storeMod.useStore.setState({ automations: [{ ...IMPORTED, unresolvedReferences: {} } as unknown as Automation] })
    render(<CreateFlow />)
    expect(screen.getByText(
      '44444444… isn’t one of your agents — the execution would fail at step 1.')).toBeTruthy()
    expect(screen.getByText(
      'Step 1 uses a secret that no longer exists (33333333…) — the execution would fail there.'
      + ' Sync the steps to rewrite them.')).toBeTruthy()
    expect(screen.queryByText('STRIPE_KEY')).toBeNull()
  })
})

describe('document-editor modal (§11)', () => {
  beforeEach(armPendingPoll)

  // The card's Edit is the only way in; the modal portals to document.body, so
  // every assertion inside it scopes to the doc-editor testid (the toolbar
  // eyebrow repeats the card's own).
  const openSpec = () => {
    fireEvent.click(screen.getByTestId('spec-edit'))
    return screen.getByTestId('doc-editor')
  }
  const openNotes = () => {
    fireEvent.click(screen.getByText('NOTES'))
    fireEvent.click(within(cardOf(screen.getByText('NOTES'))).getByText('Edit'))
    return screen.getByTestId('doc-editor')
  }

  it('spec Edit opens the editor on spec.md — toolbar, live line count, Save gated on a change', () => {
    render(<CreateFlow />)
    const modal = openSpec()
    expect(screen.getByRole('dialog').getAttribute('aria-label')).toBe('Edit spec.md')
    expect(within(modal).getByText('SPEC')).toBeTruthy()
    expect(within(modal).getByText('spec.md')).toBeTruthy()
    // the seeded spec serializes to "# My auto" + "Does things."
    expect(within(modal).getByTestId('doc-lines').textContent).toBe('2 lines')
    expect((within(modal).getByText('Save') as HTMLButtonElement).disabled).toBe(true)
    expect(within(modal).getByText('Saving rewrites the steps to match the new spec.')).toBeTruthy()
    fireEvent.change(screen.getByTestId('spec-editor'), { target: { value: '# My auto\nOne\nTwo\n' } })
    // a trailing newline is not a line of its own
    expect(within(modal).getByTestId('doc-lines').textContent).toBe('3 lines')
    expect((within(modal).getByText('Save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('Cancel with unchanged text closes silently', async () => {
    render(<CreateFlow />)
    const modal = openSpec()
    fireEvent.click(within(modal).getByText('Cancel'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await waitFor(() => expect(screen.queryByTestId('doc-editor')).toBeNull())
    expect(screen.getByText('Does things.')).toBeTruthy()
  })

  it('Cancel with changed text asks first: its Cancel keeps the text, Discard edits drops it', async () => {
    render(<CreateFlow />)
    fireEvent.click(screen.getByTestId('spec-edit'))
    fireEvent.change(screen.getByTestId('spec-editor'), { target: { value: '# My auto\nTyped change.' } })
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Cancel'))
    const dialog = screen.getByRole('alertdialog')
    expect(within(dialog).getByText('Discard your spec edits?')).toBeTruthy()
    // cancelling the confirm returns to the editor with the typing intact
    fireEvent.click(within(dialog).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect((screen.getByTestId('spec-editor') as HTMLTextAreaElement).value).toBe('# My auto\nTyped change.')
    // discarding closes both cards and leaves the spec as it was
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Cancel'))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Discard edits'))
    await waitFor(() => expect(screen.queryByTestId('doc-editor')).toBeNull())
    expect(screen.getByText('Does things.')).toBeTruthy()
    expect(screen.queryByText('Typed change.')).toBeNull()
  })

  it('Escape and a backdrop click raise the same discard confirm', async () => {
    render(<CreateFlow />)
    openNotes()
    fireEvent.change(screen.getByTestId('notes-editor'), { target: { value: '- Typed by hand' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(within(screen.getByRole('alertdialog')).getByText('Discard your notes edits?')).toBeTruthy()
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    // the backdrop is the dialog card's own parent (§14 Modal)
    fireEvent.mouseDown(screen.getByRole('dialog').parentElement!)
    expect(within(screen.getByRole('alertdialog')).getByText('Discard your notes edits?')).toBeTruthy()
    expect(screen.getByTestId('doc-editor')).toBeTruthy()
  })

  it('⌘S saves the spec; with nothing typed it does nothing', async () => {
    render(<CreateFlow />)
    openSpec()
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    expect(screen.getByTestId('doc-editor')).toBeTruthy()
    expect(storeMod.useStore.getState().toast).toBeNull()
    fireEvent.change(screen.getByTestId('spec-editor'), { target: { value: '# My auto\nHand-tuned body.' } })
    fireEvent.keyDown(document, { key: 's', metaKey: true })
    await waitFor(() => expect(screen.queryByTestId('doc-editor')).toBeNull())
    expect(screen.getByText('Hand-tuned body.')).toBeTruthy()
    expect(screen.getByText('Out of sync — steps still match the old spec.')).toBeTruthy()
    expect(storeMod.useStore.getState().toast)
      .toBe('Spec saved — the workflow is out of sync. Sync the steps before saving.')
  })

  it('the build-instructions editor carries Reset to default, which disables once applied', async () => {
    const { instructionCache } = await import('../src/pages/createflow/model')
    instructionCache.defaultBuild = '- rules' // the mocked GET /instructions payload
    render(<CreateFlow />)
    fireEvent.click(screen.getByText('BUILD INSTRUCTIONS'))
    const card = cardOf(screen.getByText('BUILD INSTRUCTIONS'))
    fireEvent.click(within(card).getByText('Edit'))
    const modal = screen.getByTestId('doc-editor')
    expect((within(modal).getByText('Reset to default') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(within(modal).getByText('Reset to default'))
    expect((screen.getByTestId('instructions-editor') as HTMLTextAreaElement).value).toBe('- rules')
    expect((within(modal).getByText('Reset to default') as HTMLButtonElement).disabled).toBe(true)
  })

  it('one document at a time: the notes Save closes the editor and lands in the card', async () => {
    storeMod.useStore.setState({
      automations: [{ ...AUTO, notes: '- Site rate-limits at 10 rpm' } as unknown as Automation],
    })
    render(<CreateFlow />)
    openNotes()
    expect(screen.getAllByTestId('doc-editor')).toHaveLength(1)
    fireEvent.change(screen.getByTestId('notes-editor'), { target: { value: '- Pruned by hand' } })
    fireEvent.click(within(screen.getByTestId('doc-editor')).getByText('Save'))
    await waitFor(() => expect(screen.queryByTestId('doc-editor')).toBeNull())
    expect(bodyLi('Pruned by hand')).toBeTruthy()
  })
})
