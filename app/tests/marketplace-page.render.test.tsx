// §22.3/§22.6 Marketplace page: the preview gate on the nav row and the page
// itself (the §4.9 developerMode setting), the empty state's example catalog,
// a seeded source's grid, Install opening the §9.1 import modal on its preview
// step, and the add modal's inline 422. App renders for real (happy-dom) with
// the api module mocked, `settings-gating` style.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ImportPreview, MarketplaceSource, Settings } from '../src/types'

const SETTINGS: Settings = {
  login: false, menuBarIcon: false, keepAwake: false, automaticUpdateCheck: false,
  notifications: 'attention', days: 30, keepForever: false, developerMode: true, cliEnabled: false,
  dataPath: '/tmp', dataSize: '0 B',
}

// App's boot reads §19 /state, which lands the served settings over whatever
// the store was seeded with - the gate tests move both together.
let servedSettings: Settings = SETTINGS

const marketplaceList = vi.fn<() => Promise<{ sources: MarketplaceSource[] }>>()
const marketplaceAdd = vi.fn()
const marketplaceEntryPreview = vi.fn()
const marketplaceImage = vi.fn(() => Promise.reject(new Error('no image')))

vi.mock('../src/api', () => ({
  connectInfo: vi.fn(async () => true),
  openWs: vi.fn(() => () => {}),
  api: {
    state: vi.fn(async () => ({
      version: '0.3.0', automations: [], executions: [], agents: [], secrets: [],
      settings: servedSettings, pendingDraft: null,
    })),
    triggersPreview: vi.fn(async () => ({ triggers: [] })),
    marketplaceList: () => marketplaceList(),
    marketplaceAdd: (body: { url?: string; path?: string }) => marketplaceAdd(body),
    marketplaceRefresh: vi.fn(),
    marketplaceRefreshAll: vi.fn(),
    marketplaceRemove: vi.fn(),
    marketplaceEntryPreview: (id: string, index: number) => marketplaceEntryPreview(id, index),
    marketplaceImage: () => marketplaceImage(),
  },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default
let MarketplacePage: typeof import('../src/pages/MarketplacePage').default

const openCatalog = vi.fn()

beforeAll(async () => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(null),
    onUpdateAvailable: () => {},
    onUpdateProgress: () => {},
    backendStatus: () => Promise.resolve({ state: 'ok', detail: '' }),
    openCatalog,
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
  // jsdom/happy-dom may ship neither half of the blob-URL pair the §22.3 image
  // loader uses - the page must still render its cards.
  const u = URL as unknown as Record<string, unknown>
  if (!u.createObjectURL) u.createObjectURL = () => 'blob:stub'
  if (!u.revokeObjectURL) u.revokeObjectURL = () => {}
  storeMod = await import('../src/store')
  App = (await import('../src/App')).default
  MarketplacePage = (await import('../src/pages/MarketplacePage')).default
})

const source = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => ({
  id: 's1', kind: 'url', origin: 'https://example.com/shared/marketplace.yaml',
  name: 'Community', description: 'Automations I use.',
  url: 'https://example.com/shared/marketplace-catalog.yaml',
  addedAt: new Date().toISOString(), refreshedAt: new Date().toISOString(), error: null,
  cached: true,
  entries: [{
    index: 0, title: 'Manga chapter watcher',
    description: 'Checks the series you follow every morning at 8.',
    archive: 'https://example.com/shared/automations/manga.autowright', image: false,
  }],
  ...over,
})

const preview = (): ImportPreview => ({
  name: 'Manga chapter watcher', landsAs: 'Manga chapter watcher', description: 'Checks every morning.',
  steps: [], params: [], triggers: [], packages: [], agents: [], secrets: [],
  os: 'macos', osMismatch: false,
})

// §22.3 preview gate: the setting as both the store and the served snapshot see it.
const setDeveloperMode = (on: boolean) => {
  servedSettings = { ...SETTINGS, developerMode: on }
  storeMod.useStore.setState({ settings: { ...servedSettings } })
}

beforeEach(() => {
  servedSettings = { ...SETTINGS }
  marketplaceList.mockReset()
  marketplaceList.mockResolvedValue({ sources: [] })
  marketplaceAdd.mockReset()
  marketplaceEntryPreview.mockReset()
  openCatalog.mockReset()
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'automations', automations: [],
    executions: [], agents: [], secrets: [], settings: { ...SETTINGS },
    updateAvailable: null, reportOpen: false, version: '0.3.0', marketplaceVersion: 0,
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

describe('§22.3 preview gate', () => {
  it('the nav row renders only while Developer mode is on', async () => {
    setDeveloperMode(false)
    render(<App />)
    await screen.findByTestId('nav-rail')
    expect(screen.queryByTestId('nav-marketplace')).toBeNull()
    cleanup()
    setDeveloperMode(true)
    render(<App />)
    expect(await screen.findByTestId('nav-marketplace')).toBeTruthy()
  })

  it('the setting dropping while the page is open lands on Automations', async () => {
    storeMod.useStore.setState({ page: 'marketplace' })
    render(<App />)
    await screen.findByText('Marketplace', { selector: 'h1' })
    setDeveloperMode(false)
    await waitFor(() => expect(storeMod.useStore.getState().page).toBe('automations'))
    expect(screen.queryByTestId('nav-marketplace')).toBeNull()
  })
})

describe('§22.3 Marketplace page', () => {
  it('the empty state shows the example catalog', async () => {
    render(<MarketplacePage />)
    expect(await screen.findByText('No marketplaces yet')).toBeTruthy()
    expect(screen.getByText('MAKE YOUR OWN')).toBeTruthy()
    expect(screen.getByText(/format_version: 1/)).toBeTruthy()
    // §22.1: the example shows the `url` a refresh would download.
    expect(screen.getByText(/url: https:\/\//)).toBeTruthy()
    // §22.3: Refresh all needs at least one source.
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
  })

  it('a source renders its entry grid, origin chip and Refreshed line', async () => {
    marketplaceList.mockResolvedValue({ sources: [source()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    expect(screen.getByText('Community')).toBeTruthy()
    expect(screen.getByText('example.com')).toBeTruthy()
    expect(screen.getByText(/^Refreshed Today, /)).toBeTruthy()
    expect(screen.getAllByTestId('marketplace-entry')).toHaveLength(1)
    expect(screen.getByText('Manga chapter watcher')).toBeTruthy()
    expect(screen.getByTestId('marketplace-refresh-all')).toBeTruthy()
    expect(screen.getByLabelText('Refresh')).toBeTruthy()
    expect(screen.getByLabelText('Remove')).toBeTruthy()
    // §22.3: the example catalog is the empty state's alone.
    expect(screen.queryByText('MAKE YOUR OWN')).toBeNull()
  })

  it('a source whose catalog declares no url offers no Refresh', async () => {
    marketplaceList.mockResolvedValue({ sources: [source({ url: null })] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    // §22.3: a one-time download has nothing to refresh from - it says when it
    // was added instead, and keeps only Remove.
    expect(screen.queryByLabelText('Refresh')).toBeNull()
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
    expect(screen.getByText(/^Added Today, /)).toBeTruthy()
    expect(screen.queryByText(/^Refreshed /)).toBeNull()
    expect(screen.getByLabelText('Remove')).toBeTruthy()
  })

  it('a failed refresh keeps the last copy beside the reason', async () => {
    marketplaceList.mockResolvedValue({ sources: [source({ error: 'the server did not answer' })] })
    render(<MarketplacePage />)
    expect(await screen.findByText("Couldn't refresh: the server did not answer. Showing the last copy."))
      .toBeTruthy()
    expect(screen.getAllByTestId('marketplace-entry')).toHaveLength(1)
  })

  it('a source with nothing cached says it could not load at all', async () => {
    marketplaceList.mockResolvedValue({ sources: [source({
      cached: false, url: null, entries: [],
      error: "the saved copy couldn't be read - remove this marketplace and add it again",
    })] })
    render(<MarketplacePage />)
    expect(await screen.findByText(
      "Couldn't load: the saved copy couldn't be read - remove this marketplace and add it again."))
      .toBeTruthy()
    expect(screen.getByText('This marketplace lists no automations yet.')).toBeTruthy()
  })

  it('Install previews the entry and opens the import modal on its preview step', async () => {
    marketplaceList.mockResolvedValue({ sources: [source()] })
    marketplaceEntryPreview.mockResolvedValue({ token: 'tok', preview: preview() })
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-install'))
    await waitFor(() => expect(marketplaceEntryPreview).toHaveBeenCalledWith('s1', 0))
    // §22.3: the modal skips the input step - its source row names the entry.
    expect(await screen.findByText('Community · Manga chapter watcher')).toBeTruthy()
    expect(screen.getByText('Import')).toBeTruthy()
    expect(screen.queryByText(/Choose an \.autowright file/)).toBeNull()
  })

  it('the add modal shows a 409 inline instead of closing', async () => {
    marketplaceList.mockResolvedValue({ sources: [source()] })
    marketplaceAdd.mockRejectedValue(
      Object.assign(new Error('that marketplace is already added'), { status: 409 }))
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-add'))
    const field = await screen.findByPlaceholderText(/link to a marketplace-catalog\.yaml file/)
    fireEvent.change(field, { target: { value: 'https://example.com/marketplace.yaml' } })
    fireEvent.click(screen.getByText('Add'))
    expect(await screen.findByText('that marketplace is already added')).toBeTruthy()
    await waitFor(() => expect(marketplaceAdd)
      .toHaveBeenCalledWith({ url: 'https://example.com/marketplace.yaml' }))
    expect(screen.getByText('Add marketplace')).toBeTruthy()
  })
})
