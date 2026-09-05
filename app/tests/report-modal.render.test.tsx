// §9/§9.5 report issue: the permanent nav row (directly above About, below the
// update row) and the report modal — prefill URL assembly, info-block toggle.
// App renders for real (happy-dom) with the api module mocked to a connected,
// onboarded snapshot.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Settings } from '../src/types'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: false, cliEnabled: false,
  dataPath: '/tmp', dataSize: '0 B',
}

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => true),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(async () => ({
      version: '0.3.0', automations: [], executions: [], agents: [], secrets: [],
      settings: SETTINGS, pendingDraft: null,
    })),
  },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(null),
    onUpdateAvailable: () => {},
    updateCheck: () => Promise.resolve({ state: 'uptodate' }),
    onUpdateProgress: () => {},
    backendStatus: () => Promise.resolve({ state: 'ok', detail: '' }),
    platformInfo: () => Promise.resolve({ platform: 'darwin', release: '15.5', arch: 'arm64', version: '7.7.7' }),
  }
  const ls = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k: string) => ls.get(k) ?? null,
      setItem: (k: string, v: string) => { ls.set(k, String(v)) },
      removeItem: (k: string) => { ls.delete(k) },
    },
  })
  localStorage.setItem('ad-onboarded', '1')
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
})

beforeEach(() => {
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'automations', automations: [],
    executions: [], agents: [], secrets: [], settings: SETTINGS,
    updateAvailable: null, reportOpen: false, version: '0.3.0',
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

const openModal = async () => {
  render(<App />)
  fireEvent.click(await screen.findByTestId('nav-report-issue'))
  return await screen.findByTestId('report-modal')
}
const openHref = () => (screen.getByTestId('report-open') as HTMLAnchorElement).href

describe('report issue nav row (§9)', () => {
  it('always renders, directly above About and below the update row', async () => {
    storeMod.useStore.setState({ updateAvailable: '9.9.9' })
    render(<App />)
    const rail = await screen.findByTestId('nav-rail')
    const rows = Array.from(rail.querySelectorAll('button')).map((b) => b.textContent ?? '')
    const iUpdate = rows.findIndex((t) => t.includes('Update available'))
    const iReport = rows.findIndex((t) => t.includes('Report an issue'))
    const iAbout = rows.findIndex((t) => t.includes('About'))
    expect(iUpdate).toBeGreaterThan(-1)
    expect(iReport).toBe(iUpdate + 1)
    expect(iAbout).toBe(iReport + 1)
  })

  it('opens the modal without navigating', async () => {
    await openModal()
    expect(storeMod.useStore.getState().page).toBe('automations')
    expect(storeMod.useStore.getState().reportOpen).toBe(true)
  })
})

describe('report modal (§9.5)', () => {
  it('assembles the prefill URL: bug label, title, text, environment block', async () => {
    await openModal()
    fireEvent.change(screen.getByPlaceholderText('Summary'),
      { target: { value: 'Executions page freezes' } })
    fireEvent.change(screen.getByPlaceholderText(/^What did you expect, and what happened instead\?/),
      { target: { value: 'it broke' } })
    const href = openHref()
    expect(href).toContain('github.com/hansololz/autowright/issues/new')
    expect(href).toContain('labels=bug')
    expect(new URL(href).searchParams.get('title')).toBe('Executions page freezes')
    const body = new URL(href).searchParams.get('body')!
    expect(body).toContain('### What happened')
    expect(body).toContain('it broke')
    expect(body).toContain('### Environment')
    expect(body).toContain('Autowright v0.3.0')
    // §9.5: exactly version + OS — no location/backend/update lines
    expect(body).not.toContain('Location:')
    expect(body).not.toContain('Backend:')
    expect(body).not.toContain('Update available:')
  })

  it('an empty store version falls back to the bundle version — never a bare "v"', async () => {
    await openModal()
    storeMod.useStore.setState({ version: '' })
    const body = () => new URL(openHref()).searchParams.get('body')!
    // platformInfo lands async — wait for the bundle-version fallback
    await waitFor(() => expect(body()).toContain('Autowright v7.7.7'))
    expect(body()).not.toContain('Autowright v\n')
  })

  it('feature toggle switches label, prompt, and body heading; text survives; info toggle drops the environment', async () => {
    await openModal()
    fireEvent.change(screen.getByPlaceholderText(/^What did you expect, and what happened instead\?/),
      { target: { value: 'keep me' } })
    fireEvent.click(screen.getByText('Feature request'))
    expect(openHref()).toContain('labels=enhancement')
    // §9.5: details field follows the type — label/placeholder swap, text stays
    expect(screen.getByText('What do you need?')).toBeTruthy()
    const area = screen.getByPlaceholderText('What would it do, and why do you need it?') as HTMLTextAreaElement
    expect(area.value).toBe('keep me')
    expect(new URL(openHref()).searchParams.get('body')).toContain('### What do you need')
    fireEvent.click(screen.getByTitle('Include environment info'))
    expect(new URL(openHref()).searchParams.get('body')).not.toContain('### Environment')
  })
})
