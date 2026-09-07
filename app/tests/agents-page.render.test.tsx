// Component tests for the §4.7/§12 Agents page: the per-card connection check
// (the timed "Check connection" on a ready card, the 'connecting' reconnect on
// a needs-setup one), the inert chip a gone automation leaves behind, the
// empty state, and the two in-flight rows. The page renders for real
// (happy-dom) against the real store with the api module mocked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Agent } from '../src/types'
import { useStore, type AgentCheck } from '../src/store'
import AgentsPage from '../src/pages/AgentsPage'
import { api } from '../src/api'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: { checkAgent: vi.fn(async () => ({ status: 'ready' })) },
}))
const checkAgent = vi.mocked(api.checkAgent)

const AGENT: Agent = {
  id: 'g1', name: 'Writer', description: 'Cloud drafting',
  harness: 'Claude Code', mode: 'default', model: null,
}

// §12 session cache: the page's own effect checks every agent with no cached
// status, so a seeded cache entry is what keeps a test's card in one state.
const seed = (check: AgentCheck, over: Partial<Agent> = {}) => {
  useStore.setState({
    page: 'agents', agents: [{ ...AGENT, ...over }], automations: [],
    agentChecks: { [over.id ?? AGENT.id]: check }, toast: null,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  checkAgent.mockResolvedValue({ status: 'ready' })
  useStore.setState({ agents: [], automations: [], agentChecks: {}, toast: null, page: 'agents' })
})
afterEach(() => cleanup())

describe('AgentsPage (§4.7/§12)', () => {
  it('a ready card: the plug button runs the timed check and toasts the answer time', async () => {
    seed('ready')
    render(<AgentsPage />)
    fireEvent.click(screen.getByLabelText('Check connection'))
    await waitFor(() => expect(checkAgent).toHaveBeenCalledWith('g1'))
    await waitFor(() => expect(useStore.getState().toast)
      .toMatch(/^Writer answered in \d+\.\d s\. Ready\.$/))
  })

  it('a needs-setup card: the plug button checks in connecting mode, toasting either answer', async () => {
    seed('needs')
    render(<AgentsPage />)
    // §12: the reconnect check paints the card Connecting, not Checking.
    checkAgent.mockResolvedValue({ status: 'needs' })
    fireEvent.click(screen.getByLabelText('Reconnect'))
    expect(useStore.getState().agentChecks.g1).toBe('connecting')
    await waitFor(() => expect(useStore.getState().toast)
      .toBe('Still signed out. Finish signing in, then try again.'))

    checkAgent.mockResolvedValue({ status: 'ready' })
    fireEvent.click(screen.getByLabelText('Reconnect'))
    expect(useStore.getState().agentChecks.g1).toBe('connecting')
    await waitFor(() => expect(useStore.getState().toast).toBe('Connected. Signed in as you.'))
  })

  it('a usedBy entry with no such automation renders as an inert static chip', () => {
    seed('ready', { usedBy: [{ id: 'gone', name: 'Old job' }] })
    render(<AgentsPage />)
    const chip = screen.getByText('Old job')
    expect(chip.tagName).toBe('SPAN')
    expect(chip.className).toBe('ad-chip-btn static')
    expect(chip.closest('button')).toBeNull()
  })

  it('no agents: the empty state offers the first-agent action', () => {
    useStore.setState({ agents: [], agentChecks: {} })
    render(<AgentsPage />)
    expect(screen.getByText(
      'No agents yet. Existing automations still execute on schedule, but you need an agent to create or edit them.',
    )).toBeTruthy()
    fireEvent.click(screen.getByText('Add your first agent'))
    expect(useStore.getState().page).toBe('agentNew')
  })

  it('a check in flight: the plug button is disabled and the card carries the busy row', () => {
    seed('checking')
    render(<AgentsPage />)
    expect((screen.getByLabelText('Checking…') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Checking locally…')).toBeTruthy()
    cleanup()

    seed('connecting')
    render(<AgentsPage />)
    expect((screen.getByLabelText('Checking…') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Reconnecting…')).toBeTruthy()
  })
})
