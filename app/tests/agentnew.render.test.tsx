// Component tests for the §12 New/Edit agent form: harness/mode/model payloads,
// name validation, the Ollama gate for local-model mode, and model pulls.
// The page renders for real (happy-dom) against the real store with the api
// module mocked.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => false),
  openWs: vi.fn(() => () => {}),
  api: {
    ollamaStatus: vi.fn(async () => ({ ready: false, installed: false, models: [] })),
    ollamaPull: vi.fn(async () => ({})),
    installHarness: vi.fn(async () => ({})),
    installStatus: vi.fn(async () => ({ state: 'idle' })),
    detectAgents: vi.fn(async () => ['claude', 'gemini', 'codex', 'opencode'].map((id) => (
      { id, name: id, installed: true, signedIn: true, detail: '' }))),
    loginHarness: vi.fn(async () => ({ ok: true, method: 'terminal' })),
    signinStatus: vi.fn(async () => ({ installed: true, signedIn: true })),
    addAgent: vi.fn(async () => ({ id: 'new' })),
    patchAgent: vi.fn(async () => ({})),
    checkAgent: vi.fn(async () => ({ status: 'ready' })),
    state: vi.fn(async () => ({})),
  },
}))

let storeMod: typeof import('../src/store')
let AgentNewPage: typeof import('../src/pages/AgentNewPage').default
let mockedApi: typeof import('../src/api').api

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
  }
  storeMod = await import('../src/store')
  AgentNewPage = (await import('../src/pages/AgentNewPage')).default
  mockedApi = (await import('../src/api')).api
})

beforeEach(() => {
  vi.clearAllMocks()
  storeMod.useStore.setState({
    surface: 'app', page: 'agentNew', agents: [], secrets: [],
    harnessInstall: {}, ollamaPull: null, toast: null,
    // §2 gating: macOS-identical unless a test says otherwise.
    platformCapabilities: {
      imessage: true, notifications: true, keepAwake: true, service: true, agentInstall: true,
    },
  })
})
afterEach(() => cleanup())

const status = (over: Partial<{ ready: boolean; installed: boolean; models: string[] }>) =>
  (mockedApi.ollamaStatus as ReturnType<typeof vi.fn>)
    .mockResolvedValue({ ready: false, installed: false, models: [], ...over })

describe('AgentNewPage (§12)', () => {
  it('default-mode save posts harness + null model; navigates back to Agents', async () => {
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))          // harness card → mode 'default'
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: ' My writer ' } })
    fireEvent.change(
      screen.getByPlaceholderText('What this agent is for. The authoring agent reads this when picking an agent for non-trivial tasks'),
      { target: { value: 'Cloud drafting' } })
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
    expect((mockedApi.addAgent as ReturnType<typeof vi.fn>).mock.calls[0][0]).toEqual({
      harness: 'Claude Code', mode: 'default', model: null,
      name: 'My writer', description: 'Cloud drafting',
    })
    expect(storeMod.useStore.getState().page).toBe('agents')
  })

  it('submitting without a name shows the inline error, scrolls to and focuses the name field, posts nothing', async () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Codex'))
    fireEvent.click(screen.getByText('Add agent'))
    expect(await screen.findByText('A name is required. Give this agent a name before saving.')).toBeTruthy()
    const nameInput = screen.getByPlaceholderText('Name this agent')
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(scrollSpy.mock.instances[0]).toBe(nameInput)
    expect(document.activeElement).toBe(nameInput)
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
    scrollSpy.mockRestore()
  })

  it('a duplicate effective grant name shows the inline taken error, posts nothing (§4.7)', async () => {
    storeMod.useStore.setState({
      agents: [
        // an unnamed agent grants under its harness name — "Codex" is taken too
        { id: 'g0', name: null, harness: 'Codex', mode: 'default', model: null },
        { id: 'g1', name: 'Fast local', harness: 'OpenCode', mode: 'ollama', model: 'qwen3:8b' },
      ] as never,
    })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))
    // case-insensitive collision with the named agent
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'fast LOCAL' } })
    fireEvent.click(screen.getByText('Add agent'))
    expect(await screen.findByText('An agent named fast LOCAL already exists. Pick a different name.')).toBeTruthy()
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
    // collision with the unnamed agent's harness fallback
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'codex' } })
    fireEvent.click(screen.getByText('Add agent'))
    expect(await screen.findByText('An agent named codex already exists. Pick a different name.')).toBeTruthy()
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
    // a free name clears the error and saves
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Fresh name' } })
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
  })

  it('custom mode posts the typed model string verbatim', async () => {
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A specific model'))
    fireEvent.change(screen.getByPlaceholderText('e.g. anthropic/claude-opus-4-8'),
      { target: { value: 'anthropic/claude-opus-4-8' } })
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Pinned' } })
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
    expect((mockedApi.addAgent as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatchObject({
      harness: 'OpenCode', mode: 'custom', model: 'anthropic/claude-opus-4-8',
    })
  })

  it('local-model mode renders for every harness — disabled for Gemini CLI (§4.7)', async () => {
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))
    expect(screen.getByText('A local model')).toBeTruthy()
    fireEvent.click(screen.getByText('Codex'))
    expect(screen.getByText('A local model')).toBeTruthy()
    fireEvent.click(screen.getByText('Gemini CLI'))
    const row = screen.getByText('A local model').closest('button')!
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(screen.getByText('Gemini CLI can’t drive local models.')).toBeTruthy()
    fireEvent.click(row) // a disabled row never selects
    expect(row.getAttribute('aria-checked')).toBe('false')
  })

  it('picking an installed model saves a Claude Code ollama-mode agent', async () => {
    status({ ready: true, installed: true, models: ['qwen3:8b'] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Claude Code'))
    fireEvent.click(screen.getByText('A local model'))
    fireEvent.click(await screen.findByText('qwen3:8b'))
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Local Claude' } })
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
    expect((mockedApi.addAgent as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatchObject({
      harness: 'Claude Code', mode: 'ollama', model: 'qwen3:8b', name: 'Local Claude',
    })
  })

  it('missing Ollama gates local mode behind the inline install (§19 install)', async () => {
    status({ ready: false, installed: false })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    expect(await screen.findByText('Local models need Ollama, which isn’t installed on this Mac yet.')).toBeTruthy()

    fireEvent.click(screen.getByText('Install Ollama'))
    await waitFor(() => expect(mockedApi.installHarness).toHaveBeenCalledWith('ollama'))
    expect(screen.getByText(/Installing Ollama…/)).toBeTruthy()

    // the harness.install stream finishing ok re-polls status → gate lifts
    status({ ready: true, installed: true, models: [] })
    storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'ollama', done: true, ok: true })
    expect(await screen.findByText('Ollama is installed and active.')).toBeTruthy()
    // still unsavable without a model — the submit stays gated
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Local' } })
    fireEvent.click(screen.getByText('Add agent'))
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
  })

  it('picking an installed model saves an ollama-mode agent', async () => {
    status({ ready: true, installed: true, models: ['qwen3:8b'] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    fireEvent.click(await screen.findByText('qwen3:8b'))        // radio row
    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Fast local' } })
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
    expect((mockedApi.addAgent as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatchObject({
      harness: 'OpenCode', mode: 'ollama', model: 'qwen3:8b', name: 'Fast local',
    })
  })

  it('suggestion chips fill the pull input; Download starts the real pull', async () => {
    status({ ready: true, installed: true, models: [] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    // chip fills the input — it must NOT start the pull (§12)
    fireEvent.click(await screen.findByText('qwen3-coder:30b'))
    expect(mockedApi.ollamaPull).not.toHaveBeenCalled()
    const input = screen.getByPlaceholderText('e.g. qwen3-coder:30b') as HTMLInputElement
    expect(input.value).toBe('qwen3-coder:30b')

    fireEvent.click(screen.getByText('Download'))
    await waitFor(() => expect(mockedApi.ollamaPull).toHaveBeenCalledWith('qwen3-coder:30b'))
    expect(screen.getByText(/Downloading/)).toBeTruthy()
    // §12: the whole SUGGESTED section hides while a pull runs — the chips
    // fill an input the download card replaced.
    expect(screen.queryByText('SUGGESTED')).toBeNull()
    expect(screen.queryByText('gemma4:e4b')).toBeNull()

    // §12: the UI renders the event's overall percent (computed in the
    // backend — never parsed out of the raw per-layer line), with the raw
    // line shown under the bar.
    storeMod.useStore.getState().applyEvent({
      event: 'ollama.pull', model: 'qwen3-coder:30b', line: 'pulling 3f2a… 45% 8.6 GB/19 GB', percent: 45, done: false,
    })
    expect(await screen.findByText('45%')).toBeTruthy()
    expect(screen.getByText('pulling 3f2a… 45% 8.6 GB/19 GB')).toBeTruthy()
  })

  it('an installed suggested model loses its chip; the section returns after a pull (§12)', async () => {
    status({ ready: true, installed: true, models: ['gemma4:e4b'] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    // installed model's chip is gone; the others still render
    expect(await screen.findByText('qwen3-coder:30b')).toBeTruthy()
    expect(screen.getByText('deepseek-coder:6.7b')).toBeTruthy()
    const chips = screen.getAllByText('gemma4:e4b')             // radio row only, no chip
    expect(chips.every((el) => el.closest('.ad-chip-btn') === null)).toBe(true)

    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getByText('qwen3-coder:30b'))      // chip fills the input
      fireEvent.click(screen.getByText('Download'))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(screen.queryByText('SUGGESTED')).toBeNull()        // hidden while pulling

      status({ ready: true, installed: true, models: ['gemma4:e4b', 'qwen3-coder:30b'] })
      await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    } finally {
      vi.useRealTimers()
    }
    // pull done: section is back with the finished model's chip gone too
    expect(screen.getByText('SUGGESTED')).toBeTruthy()
    expect(screen.getByText('deepseek-coder:6.7b')).toBeTruthy()
    const pulled = screen.getAllByText('qwen3-coder:30b')
    expect(pulled.every((el) => el.closest('.ad-chip-btn') === null)).toBe(true)
  })

  it('a bare name already installed as :latest is not re-pulled (§12/§19 rule)', async () => {
    status({ ready: true, installed: true, models: ['qwen3:latest'] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    fireEvent.change(await screen.findByPlaceholderText('e.g. qwen3-coder:30b'),
      { target: { value: 'qwen3' } })
    fireEvent.click(screen.getByText('Download'))
    expect(mockedApi.ollamaPull).not.toHaveBeenCalled()
    expect(storeMod.useStore.getState().toast).toContain('qwen3:latest is already installed')
  })

  it('a bare-name pull completes when Ollama lists its :latest variant', async () => {
    status({ ready: true, installed: true, models: [] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    fireEvent.change(await screen.findByPlaceholderText('e.g. qwen3-coder:30b'),
      { target: { value: 'qwen3' } })

    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getByText('Download'))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      expect(mockedApi.ollamaPull).toHaveBeenCalledWith('qwen3')
      expect(screen.getByText(/Downloading/)).toBeTruthy()

      // Ollama stores the bare name under :latest — the poll must match it.
      status({ ready: true, installed: true, models: ['qwen3:latest'] })
      await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    } finally {
      vi.useRealTimers()
    }
    expect(screen.queryByText(/Downloading/)).toBeNull()
    expect(storeMod.useStore.getState().toast).toContain('qwen3:latest installed')
  })

  it('a failed pull clears the progress card and toasts the failure line', async () => {
    status({ ready: true, installed: true, models: [] })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    fireEvent.change(await screen.findByPlaceholderText('e.g. qwen3-coder:30b'),
      { target: { value: 'bogus:1b' } })
    fireEvent.click(screen.getByText('Download'))
    await waitFor(() => expect(mockedApi.ollamaPull).toHaveBeenCalledWith('bogus:1b'))

    storeMod.useStore.getState().applyEvent({
      event: 'ollama.pull', model: 'bogus:1b', line: 'pull model manifest: file does not exist', done: true, ok: false,
    })
    await waitFor(() => expect(screen.queryByText(/Downloading/)).toBeNull())
    expect(storeMod.useStore.getState().toast).toContain('Download failed')
  })

  // §12 install gating — an uninstalled picked harness hides the model section,
  // gates saving, and offers the real download-and-set-up flow.
  const detect = (installed: Record<string, boolean>) =>
    (mockedApi.detectAgents as ReturnType<typeof vi.fn>).mockResolvedValue(
      ['claude', 'gemini', 'codex', 'opencode'].map((id) => (
        { id, name: id, installed: installed[id] ?? true, signedIn: installed[id] ?? true, detail: '' })))

  it('an uninstalled harness gates saving behind Download & set up (§12)', async () => {
    detect({ claude: false })
    render(<AgentNewPage />)
    expect(await screen.findByText('NOT INSTALLED')).toBeTruthy()
    fireEvent.click(screen.getByText('Claude Code'))
    expect(await screen.findByText(/isn’t installed on this Mac yet/)).toBeTruthy()
    expect(screen.queryByText('Default model')).toBeNull()      // model section hidden

    fireEvent.change(screen.getByPlaceholderText('Name this agent'), { target: { value: 'Writer' } })
    fireEvent.click(screen.getByText('Add agent'))
    expect(mockedApi.addAgent).not.toHaveBeenCalled()
    expect(storeMod.useStore.getState().toast).toContain('Download and set up Claude Code first.')

    fireEvent.click(screen.getByText('Download & set up'))
    await waitFor(() => expect(mockedApi.installHarness).toHaveBeenCalledWith('claude'))
    expect(screen.getByText(/Installing Claude Code…/)).toBeTruthy()

    // install stream finishes ok; already signed in → re-detect ungates the form
    detect({})
    storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'claude', done: true, ok: true })
    expect(await screen.findByText('Default model')).toBeTruthy()
    fireEvent.click(screen.getByText('Add agent'))
    await waitFor(() => expect(mockedApi.addAgent).toHaveBeenCalledTimes(1))
  })

  // §2/§9 gating: with `agentInstall` false the §19 install endpoint answers
  // 409, so the card hides the action and shows the manual-install line whose
  // provider name links to the vendor's install page.
  const noAgentInstall = () => storeMod.useStore.setState({
    platformCapabilities: {
      imessage: false, notifications: false, keepAwake: false, service: true, agentInstall: false,
    },
  })

  it('agentInstall false: an uninstalled harness shows the manual line, no install action (§9)', async () => {
    noAgentInstall()
    detect({ claude: false })
    render(<AgentNewPage />)
    expect(await screen.findByText('NOT INSTALLED')).toBeTruthy()
    fireEvent.click(screen.getByText('Claude Code'))
    const line = await screen.findByTestId('manual-install-line')
    expect(line.textContent).toBe(
      'Install Claude Code by hand, then come back — Autowright detects it automatically.')
    const link = line.querySelector('a') as HTMLAnchorElement
    expect(link.textContent).toBe('Claude Code')
    expect(link.getAttribute('href')).toBe('https://claude.com/product/claude-code')
    // §9.4 external-URL policy: the window-open handler routes it to the browser.
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    // The gated actions are gone — hidden, never disabled.
    expect(screen.queryByText('Download & set up')).toBeNull()
    expect(screen.queryByText(/isn’t installed on this Mac yet/)).toBeNull()
    expect(mockedApi.installHarness).not.toHaveBeenCalled()
    // Detection keeps working: the model section stays gated behind the install.
    expect(screen.queryByText('Default model')).toBeNull()
  })

  it('agentInstall false: missing Ollama shows the manual line instead of Install Ollama', async () => {
    noAgentInstall()
    status({ ready: false, installed: false })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('OpenCode'))
    fireEvent.click(screen.getByText('A local model'))
    const line = await screen.findByTestId('manual-install-line')
    expect(line.textContent).toBe(
      'Install Ollama by hand, then come back — Autowright detects it automatically.')
    expect((line.querySelector('a') as HTMLAnchorElement).getAttribute('href'))
      .toBe('https://ollama.com/download')
    expect(screen.queryByText('Install Ollama')).toBeNull()
    expect(mockedApi.installHarness).not.toHaveBeenCalled()
  })

  it('a finished install that is signed out starts the sign-in help (§12)', async () => {
    detect({ codex: false })
    ;(mockedApi.signinStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ installed: true, signedIn: false })
    ;(mockedApi.loginHarness as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, method: 'browser' })
    render(<AgentNewPage />)
    fireEvent.click(screen.getByText('Codex'))
    fireEvent.click(await screen.findByText('Download & set up'))
    await waitFor(() => expect(mockedApi.installHarness).toHaveBeenCalledWith('codex'))

    storeMod.useStore.getState().applyEvent({ event: 'harness.install', id: 'codex', done: true, ok: true })
    await waitFor(() => expect(mockedApi.loginHarness).toHaveBeenCalledWith('codex'))
    expect(await screen.findByText(/Finish signing in\. Autowright opened your browser/)).toBeTruthy()
  })
})
