// §22.3/§22.6 Marketplace page: the preview gate on the nav row and the page
// itself (the §4.9 developerMode setting), the empty state's example catalog,
// a seeded catalog's grid, a hidden catalog collapsing to its header, the
// settings modal's PATCH, Install opening the §9.1 import modal on its preview
// step, Export handing the catalog file to the save dialog, the add modal's
// three ways in, and the §22.7 authoring flow (the Edit catalog… row, the two-column
// catalog editor, the typed add form - title, description, path, image - that
// appends an entry without exporting or opening anything, Save's body, the
// discard confirm, and create mode, which has no save location at all). App
// renders for real (happy-dom) with the api module mocked, `settings-gating`
// style.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type {
  Automation, ImportPreview, MarketplaceCatalog, MarketplaceSource, Settings,
} from '../src/types'

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
const marketplaceSettings = vi.fn()
const marketplaceEntryPreview = vi.fn()
const marketplaceImage = vi.fn<() => Promise<Blob>>(() => Promise.reject(new Error('no image')))
// §22.3 Export: the app's copy of the catalog, handed to the save dialog.
const marketplaceCatalogFile = vi.fn<(id: string) => Promise<ArrayBuffer>>()
// §22.7 authoring
const marketplaceCatalogCreate = vi.fn()
const marketplaceCatalogRead = vi.fn<(id: string) => Promise<MarketplaceCatalog>>()
const marketplaceCatalogSave = vi.fn()
// §22.7: the editor never exports - the add form takes a reference as typed.
// The mock is here so the tests can prove it stays unused.
const exportAutomation = vi.fn<(automationId: string, values: boolean) => Promise<ArrayBuffer>>()

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
    marketplaceSettings: (id: string, body: unknown) => marketplaceSettings(id, body),
    marketplaceRefresh: vi.fn(),
    marketplaceRefreshAll: vi.fn(),
    marketplaceRemove: vi.fn(),
    marketplaceEntryPreview: (id: string, index: number) => marketplaceEntryPreview(id, index),
    marketplaceImage: () => marketplaceImage(),
    marketplaceCatalogFile: (id: string) => marketplaceCatalogFile(id),
    marketplaceCatalogCreate: (body: unknown) => marketplaceCatalogCreate(body),
    marketplaceCatalogRead: (id: string) => marketplaceCatalogRead(id),
    marketplaceCatalogSave: (id: string, body: unknown) => marketplaceCatalogSave(id, body),
    exportAutomation: (id: string, values: boolean) => exportAutomation(id, values),
  },
}))


const openCatalog = vi.fn()
// §22.3: the dropped file's path, which is all that ever travels.
const pathForFile = vi.fn<(file: File) => string>()
// §22.3 Export saves through the save dialog. The §22.7 editor never chooses a
// folder or opens a file, so `pickFolder` is only here to stay unused.
const pickFolder = vi.fn()
const saveFile = vi.fn<(defaultName: string, data: ArrayBuffer) => Promise<string | null>>()

// Module scope, not `beforeAll`: the §22 visibility constant gates the tests
// below through `it.skipIf`, which vitest reads while it collects them.
;(() => {
  ;(window as unknown as Record<string, unknown>).autowright = {
    onOpenTarget: () => {},
    trayAlert: () => Promise.resolve(),
    applySettings: () => Promise.resolve(),
    updateAvailable: () => Promise.resolve(null),
    onUpdateAvailable: () => {},
    onUpdateProgress: () => {},
    backendStatus: () => Promise.resolve({ state: 'ok', detail: '' }),
    openCatalog,
    pathForFile,
    pickFolder,
    saveFile,
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
})()
const storeMod = await import('../src/store')
const appMod = await import('../src/App')
const App = appMod.default
const MARKETPLACE_HIDDEN = appMod.MARKETPLACE_HIDDEN
const MarketplacePage = (await import('../src/pages/MarketplacePage')).default

// §22.2 catalog-table row as §22.4 serves it: a link location by default.
const source = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => ({
  id: 's1', kind: 'url', location: 'https://example.com/shared/marketplace-catalog.yaml',
  shown: true, autoRefresh: false,
  name: 'Community', description: 'Automations I use.',
  addedAt: new Date().toISOString(), refreshedAt: new Date().toISOString(), error: null,
  cached: true,
  entries: [{
    index: 0, title: 'Manga chapter watcher',
    description: 'Checks the series you follow every morning at 8.',
    archive: 'https://example.com/shared/automations/manga.autowright', image: null,
  }],
  ...over,
})

// §22.2: a catalog read from a file on this machine - refreshable like a link,
// and editable because the copy is here.
const fileSource = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => source({
  kind: 'file', location: '/Users/x/shelf/marketplace-catalog.yaml', ...over,
})

// §22.2: no location at all - the app's copy is the only copy.
const keptSource = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => source({
  kind: 'none', location: null, refreshedAt: null, ...over,
})

// §22.7 picker, A CATALOG tab: another catalog on the page, listing an entry
// whose archive and image are §22.1 references a pick copies as written.
const otherSource = (over: Partial<MarketplaceSource> = {}): MarketplaceSource => source({
  id: 's2', name: 'Neighbours', description: 'What they share.',
  entries: [{
    index: 0, title: 'Inbox sweeper', description: 'Files the mail.',
    archive: 'https://example.com/shared/automations/inbox.autowright',
    image: 'https://example.com/shared/images/inbox.png',
  }],
  ...over,
})

// §22.3 preview image: a catalog kept by Autowright never stamps refreshedAt,
// so the entry's own reference is all that changes when the image is edited.
const imaged = (image: string): MarketplaceSource => keptSource({
  entries: [{
    index: 0, title: 'Manga chapter watcher', description: '',
    archive: '/Users/x/shelf/automations/manga.autowright', image,
  }],
})

const preview = (): ImportPreview => ({
  name: 'Manga chapter watcher', landsAs: 'Manga chapter watcher', description: 'Checks every morning.',
  steps: [], params: [], triggers: [], packages: [], agents: [], secrets: [],
  os: 'macos', osMismatch: false,
})

// §22.7 GET …/catalog: the catalog file as written - §22.1 references, so an
// absolute path beside the catalog.
const catalog = (over: Partial<MarketplaceCatalog> = {}): MarketplaceCatalog => ({
  name: 'Community', description: 'Automations I use.',
  entries: [{
    index: 0, title: 'Manga chapter watcher',
    description: 'Checks the series you follow every morning at 8.',
    path: '/Users/x/shelf/automations/manga.autowright', image: '',
  }],
  ...over,
})

// §22.7 picker: the automations in this app, as the §5 store holds them.
const auto = (over: Partial<Automation> = {}): Automation => ({
  id: 'a1', name: 'Watcher', description: 'Checks the feed.', version: 1, triggers: [],
  triggerChip: 'No triggers', allTriggersOff: false, nextAtMs: null, notes: '',
  lastStatus: 'succeeded', live: [], maxParallel: 1, maxQueued: 0, resultChip: null,
  resultStatus: null, lastExecutionLabel: '', agentId: null, stepAgents: [],
  allowedSecrets: [], problems: [], unresolvedReferences: {},
  snapshotSettings: { preVersion: true, preClear: true, preRestore: true }, specMeta: '',
  ...over,
})

// §22.3 preview gate: the setting as both the store and the served snapshot see it.
const setDeveloperMode = (on: boolean) => {
  servedSettings = { ...SETTINGS, developerMode: on }
  storeMod.useStore.setState({ settings: { ...servedSettings } })
}

// §22.3: the row's actions live behind one ellipsis button, so every per-row
// action is reached by opening that catalog's menu first. A fresh render needs
// a fresh open - the menu unmounts with the page.
const openActions = async (index = 0) => {
  fireEvent.click((await screen.findAllByTestId('marketplace-actions'))[index])
}
const menuRow = (label: string) => screen.getByRole('button', { name: label })
// The open menu's rows, top to bottom - the icons carry no text.
const menuRowLabels = () =>
  Array.from(document.querySelectorAll('.ad-menu-row')).map((row) => row.textContent)

beforeEach(() => {
  servedSettings = { ...SETTINGS }
  marketplaceList.mockReset()
  marketplaceList.mockResolvedValue({ sources: [] })
  marketplaceAdd.mockReset()
  marketplaceSettings.mockReset()
  marketplaceSettings.mockResolvedValue(source())
  marketplaceEntryPreview.mockReset()
  marketplaceImage.mockReset()
  marketplaceImage.mockRejectedValue(new Error('no image'))
  marketplaceCatalogFile.mockReset()
  marketplaceCatalogFile.mockResolvedValue(new ArrayBuffer(4))
  openCatalog.mockReset()
  pathForFile.mockReset()
  marketplaceCatalogCreate.mockReset()
  marketplaceCatalogRead.mockReset()
  marketplaceCatalogRead.mockResolvedValue(catalog())
  marketplaceCatalogSave.mockReset()
  pickFolder.mockReset()
  exportAutomation.mockReset()
  exportAutomation.mockResolvedValue(new ArrayBuffer(8))
  saveFile.mockReset()
  saveFile.mockResolvedValue('/Users/x/archives/Watcher.autowright')
  storeMod.useStore.setState({
    connected: true, surface: 'app', page: 'automations', automations: [],
    executions: [], agents: [], secrets: [], settings: { ...SETTINGS },
    updateAvailable: null, reportOpen: false, version: '0.3.0', marketplaceVersion: 0,
  })
})
afterEach(() => { cleanup(); storeMod.useStore.getState().disconnect() })

// §22 visibility: MARKETPLACE_HIDDEN parks the feature for everyone for now
// (unpolished, design not settled). While it holds, the nav row renders for
// nobody and the page is left however it was reached; the preview-gate tests
// below cover the constant's other value so flipping it back is a one-line
// change here too.
describe('§22.3 preview gate', () => {
  it.skipIf(!MARKETPLACE_HIDDEN)('while parked, the nav row renders for nobody and the page is left', async () => {
    setDeveloperMode(true)
    storeMod.useStore.setState({ page: 'marketplace' })
    render(<App />)
    await screen.findByTestId('nav-rail')
    expect(screen.queryByTestId('nav-marketplace')).toBeNull()
    await waitFor(() => expect(storeMod.useStore.getState().page).toBe('automations'))
    // §22: not one frame of the parked page mounts, so nothing ever asks the
    // §19 marketplace routes for a list.
    expect(marketplaceList).not.toHaveBeenCalled()
  })

  it.skipIf(MARKETPLACE_HIDDEN)('the nav row renders only while Developer mode is on', async () => {
    setDeveloperMode(false)
    render(<App />)
    await screen.findByTestId('nav-rail')
    expect(screen.queryByTestId('nav-marketplace')).toBeNull()
    cleanup()
    setDeveloperMode(true)
    render(<App />)
    expect(await screen.findByTestId('nav-marketplace')).toBeTruthy()
  })

  it.skipIf(MARKETPLACE_HIDDEN)('the setting dropping while the page is open lands on Automations', async () => {
    storeMod.useStore.setState({ page: 'marketplace' })
    render(<App />)
    await screen.findByText('Marketplace', { selector: 'h1' })
    setDeveloperMode(false)
    await waitFor(() => expect(storeMod.useStore.getState().page).toBe('automations'))
    expect(screen.queryByTestId('nav-marketplace')).toBeNull()
  })
})

describe('§22.3 Marketplace page', () => {
  it('the empty state is the headline and Add marketplace… alone', async () => {
    render(<MarketplacePage />)
    expect(await screen.findByText('No marketplaces yet')).toBeTruthy()
    // §22.3: no MAKE YOUR OWN section, no example catalog (removed 2026-09-19).
    expect(screen.queryByText('MAKE YOUR OWN')).toBeNull()
    expect(screen.queryByText(/format_version: 1/)).toBeNull()
    // §22.3: Refresh all needs at least one catalog with a location.
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
    // §22.7: one Create catalog… - the header's.
    expect(screen.getAllByTestId('marketplace-create')).toHaveLength(1)
  })

  it('a link catalog renders its entry grid, host chip and Refreshed line', async () => {
    marketplaceList.mockResolvedValue({ sources: [source()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    expect(screen.getByText('Community')).toBeTruthy()
    expect(screen.getByText('example.com')).toBeTruthy()
    expect(screen.getByText(/^Refreshed Today, /)).toBeTruthy()
    expect(screen.getAllByTestId('marketplace-entry')).toHaveLength(1)
    expect(screen.getByText('Manga chapter watcher')).toBeTruthy()
    // §22.3: an entry with no image keeps the no-image icon.
    expect(screen.getByTestId('no-image')).toBeTruthy()
    expect(screen.getByTestId('marketplace-refresh-all')).toBeTruthy()
    await openActions()
    expect(menuRow('Refresh')).toBeTruthy()
    expect(menuRow('Catalog settings…')).toBeTruthy()
    expect(menuRow('Remove…')).toBeTruthy()
  })

  it('the row actions menu lists what this catalog can do, in order', async () => {
    marketplaceList.mockResolvedValue({ sources: [source()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    await openActions()
    // §22.3: one settled order, and no Edit catalog… for a link - its file
    // isn't on this machine.
    expect(menuRowLabels()).toEqual(['Export catalog…', 'Refresh', 'Catalog settings…', 'Remove…'])
    expect(screen.getByTestId('marketplace-actions').getAttribute('aria-expanded')).toBe('true')
    // §22.3: picking a row does that row's thing - the gear opens settings.
    fireEvent.click(menuRow('Catalog settings…'))
    expect(await screen.findByTestId('catalog-settings')).toBeTruthy()
  })

  it('a file catalog names its file and refreshes like a link', async () => {
    marketplaceList.mockResolvedValue({ sources: [fileSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    expect(screen.getByText('marketplace-catalog.yaml')).toBeTruthy()
    // §22.2: a path is re-read like a link - both refresh.
    await openActions()
    expect(menuRow('Refresh')).toBeTruthy()
    expect(screen.getByTestId('marketplace-refresh-all')).toBeTruthy()
    expect(screen.getByText(/^Refreshed Today, /)).toBeTruthy()
  })

  it('a catalog kept by Autowright has nothing to refresh from', async () => {
    marketplaceList.mockResolvedValue({ sources: [keptSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    // §22.2: a null location is not refreshable - it says when it was added.
    expect(screen.getByText('Stored by Autowright')).toBeTruthy()
    await openActions()
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull()
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
    expect(screen.getByText(/^Added Today, /)).toBeTruthy()
    expect(screen.queryByText(/^Refreshed /)).toBeNull()
    expect(menuRow('Remove…')).toBeTruthy()
  })

  it('a hidden catalog collapses to its header row', async () => {
    marketplaceList.mockResolvedValue({ sources: [source({ shown: false })] })
    render(<MarketplacePage />)
    const section = await screen.findByTestId('marketplace-source')
    expect(section.getAttribute('data-hidden')).toBe('true')
    expect(screen.getByTestId('marketplace-hidden-chip')).toBeTruthy()
    // §22.3: no description, no grid - the header row and nothing else.
    expect(screen.queryByText('Automations I use.')).toBeNull()
    expect(screen.queryByTestId('marketplace-entry')).toBeNull()
    await openActions()
    expect(menuRow('Catalog settings…')).toBeTruthy()
  })

  it('a failed refresh keeps the last copy beside the reason', async () => {
    marketplaceList.mockResolvedValue({ sources: [source({ error: 'the server did not answer' })] })
    render(<MarketplacePage />)
    expect(await screen.findByText("Couldn't refresh: the server did not answer. Showing the last copy."))
      .toBeTruthy()
    expect(screen.getAllByTestId('marketplace-entry')).toHaveLength(1)
  })

  it('a catalog with nothing cached says it could not load at all', async () => {
    marketplaceList.mockResolvedValue({ sources: [keptSource({
      cached: false, entries: [],
      error: "the saved copy couldn't be read - remove this marketplace and add it again",
    })] })
    render(<MarketplacePage />)
    expect(await screen.findByText(
      "Couldn't load: the saved copy couldn't be read - remove this marketplace and add it again."))
      .toBeTruthy()
    expect(screen.getByText('This marketplace lists no automations yet.')).toBeTruthy()
  })

  it('Export hands the catalog file to the save dialog and says where it landed', async () => {
    const data = new ArrayBuffer(16)
    marketplaceCatalogFile.mockResolvedValue(data)
    saveFile.mockResolvedValue('/Users/x/out/marketplace-catalog.yaml')
    marketplaceList.mockResolvedValue({ sources: [keptSource()] })
    render(<MarketplacePage />)
    await openActions()
    fireEvent.click(menuRow('Export catalog…'))
    // §22.3: the bytes come from the §19 file route, the name is the catalog's.
    await waitFor(() => expect(marketplaceCatalogFile).toHaveBeenCalledWith('s1'))
    await waitFor(() => expect(saveFile)
      .toHaveBeenCalledWith('marketplace-catalog.yaml', data))
    await waitFor(() => expect(storeMod.useStore.getState().toast)
      .toBe('Exported to /Users/x/out/marketplace-catalog.yaml.'))
  })

  it('Export is absent with nothing cached, and a failed fetch toasts the reason', async () => {
    // §22.3: there is no copy to hand out until a refresh lands one.
    marketplaceList.mockResolvedValue({ sources: [source({ cached: false, entries: [] })] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    await openActions()
    expect(screen.queryByRole('button', { name: 'Export catalog…' })).toBeNull()
    cleanup()
    marketplaceCatalogFile.mockRejectedValue(
      Object.assign(new Error("the saved copy couldn't be read - refresh to fetch it again"),
        { status: 422 }))
    marketplaceList.mockResolvedValue({ sources: [fileSource()] })
    render(<MarketplacePage />)
    await openActions()
    fireEvent.click(menuRow('Export catalog…'))
    await waitFor(() => expect(storeMod.useStore.getState().toast)
      .toBe("the saved copy couldn't be read - refresh to fetch it again"))
    expect(saveFile).not.toHaveBeenCalled()
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
})

describe('§22.3 preview images', () => {
  it('an edited image reference fetches the new picture', async () => {
    marketplaceImage.mockResolvedValue(new Blob(['one']))
    marketplaceList.mockResolvedValue({ sources: [imaged('/Users/x/shelf/images/manga.png')] })
    render(<MarketplacePage />)
    await screen.findByTestId('marketplace-entry')
    await waitFor(() => expect(marketplaceImage).toHaveBeenCalled())
    const before = marketplaceImage.mock.calls.length
    // The §22.7 editor pointed the entry at another file; marketplace.changed
    // reloads the page with the same catalog, at the same (null) refreshedAt.
    marketplaceList.mockResolvedValue({ sources: [imaged('/Users/x/shelf/images/manga-2.png')] })
    act(() => { storeMod.useStore.setState({ marketplaceVersion: 1 }) })
    // §22.3: the reference is part of the cache key, so the old blob is not
    // shown for the new picture.
    await waitFor(() => expect(marketplaceImage.mock.calls.length).toBeGreaterThan(before))
  })

  it('an image that lands after the page is gone is revoked, never cached', async () => {
    let land: (b: Blob) => void = () => {}
    marketplaceImage.mockImplementation(() => new Promise<Blob>((res) => { land = res }))
    const created = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:late')
    const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    marketplaceList.mockResolvedValue({ sources: [imaged('/Users/x/shelf/images/manga.png')] })
    render(<MarketplacePage />)
    await screen.findByTestId('marketplace-entry')
    await waitFor(() => expect(marketplaceImage).toHaveBeenCalled())
    cleanup()
    revoked.mockClear()
    await act(async () => { land(new Blob(['late'])) })
    // §22.3: the cache it would have landed in is cleared, so the URL dies now
    // - nothing is left for the next mount to show and never revoke.
    expect(created).toHaveBeenCalled()
    expect(revoked).toHaveBeenCalledWith('blob:late')
    const before = marketplaceImage.mock.calls.length
    render(<MarketplacePage />)
    await waitFor(() => expect(marketplaceImage.mock.calls.length).toBeGreaterThan(before))
    created.mockRestore()
    revoked.mockRestore()
  })

  it('a slow list answer never lands over a newer one', async () => {
    const pending: ((r: { sources: MarketplaceSource[] }) => void)[] = []
    marketplaceList.mockImplementation(() => new Promise((res) => { pending.push(res) }))
    render(<MarketplacePage />)
    await waitFor(() => expect(pending.length).toBeGreaterThan(0))
    const stale = pending[pending.length - 1]
    const asked = pending.length
    // §22.4: a marketplace.changed event asks for the list again.
    act(() => { storeMod.useStore.setState({ marketplaceVersion: 1 }) })
    await waitFor(() => expect(pending.length).toBe(asked + 1))
    await act(async () => { pending[pending.length - 1]({ sources: [source({ name: 'Newer' })] }) })
    expect(screen.getByText('Newer')).toBeTruthy()
    await act(async () => { stale({ sources: [source({ name: 'Older' })] }) })
    // §22.3: the older answer is dropped, not painted over the newer list.
    expect(screen.queryByText('Older')).toBeNull()
    expect(screen.getByText('Newer')).toBeTruthy()
  })
})

describe('§22.3 catalog settings', () => {
  // Opens the gear modal on the served catalog.
  const openSettings = async (s: MarketplaceSource) => {
    marketplaceList.mockResolvedValue({ sources: [s] })
    render(<MarketplacePage />)
    await openActions()
    fireEvent.click(menuRow('Catalog settings…'))
    return await screen.findByTestId('catalog-settings')
  }

  it('Save PATCHes the location, SHOWN and AUTO REFRESH', async () => {
    await openSettings(fileSource())
    const location = screen.getByTestId('settings-location') as HTMLInputElement
    expect(location.value).toBe('/Users/x/shelf/marketplace-catalog.yaml')
    fireEvent.click(screen.getByLabelText('Show this marketplace on the page'))
    fireEvent.click(screen.getByLabelText('Refresh on its own'))
    fireEvent.change(location, { target: { value: '/Users/x/moved/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('settings-save'))
    await waitFor(() => expect(marketplaceSettings).toHaveBeenCalledWith('s1', {
      location: '/Users/x/moved/marketplace-catalog.yaml', shown: false, autoRefresh: true,
    }))
    // §22.3: a success closes and refetches, with no toast.
    await waitFor(() => expect(screen.queryByTestId('catalog-settings')).toBeNull(), { timeout: 3000 })
  })

  it('AUTO REFRESH is disabled, and saved off, while LOCATION is empty', async () => {
    await openSettings(keptSource())
    const autoToggle = screen.getByLabelText('Refresh on its own') as HTMLButtonElement
    expect((screen.getByTestId('settings-location') as HTMLInputElement).value).toBe('')
    expect(autoToggle.disabled).toBe(true)
    expect(screen.getByText('Needs a location.')).toBeTruthy()
    fireEvent.click(autoToggle)
    fireEvent.click(screen.getByTestId('settings-save'))
    // §22.2: auto refresh is meaningless, and kept false, without a location.
    await waitFor(() => expect(marketplaceSettings).toHaveBeenCalledWith('s1', {
      location: '', shown: true, autoRefresh: false,
    }))
  })

  it('a typed location enables AUTO REFRESH again', async () => {
    await openSettings(keptSource())
    fireEvent.change(screen.getByTestId('settings-location'),
      { target: { value: 'https://example.com/marketplace-catalog.yaml' } })
    await waitFor(() => expect(
      (screen.getByLabelText('Refresh on its own') as HTMLButtonElement).disabled).toBe(false))
    expect(screen.queryByText('Needs a location.')).toBeNull()
  })

  it('a rejected save shows the reason inline and keeps the modal open', async () => {
    marketplaceSettings.mockRejectedValue(
      Object.assign(new Error('that marketplace is already added'), { status: 409 }))
    await openSettings(fileSource())
    fireEvent.click(screen.getByTestId('settings-save'))
    expect((await screen.findByTestId('settings-error')).textContent)
      .toBe('that marketplace is already added')
    expect(screen.getByTestId('catalog-settings')).toBeTruthy()
  })
})

describe('§22.3 add marketplace modal', () => {
  const openAdd = async () => {
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-add'))
    return await screen.findByTestId('marketplace-add-field') as HTMLInputElement
  }

  it('a pasted link is added as a url, and a 409 shows inline', async () => {
    marketplaceAdd.mockRejectedValue(
      Object.assign(new Error('that marketplace is already added'), { status: 409 }))
    const field = await openAdd()
    fireEvent.change(field, { target: { value: 'https://example.com/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('marketplace-add-submit'))
    await waitFor(() => expect(marketplaceAdd)
      .toHaveBeenCalledWith({ url: 'https://example.com/marketplace-catalog.yaml' }))
    expect((await screen.findByTestId('marketplace-add-error')).textContent)
      .toBe('that marketplace is already added')
    expect(screen.getByText('Add marketplace')).toBeTruthy()
  })

  it('a typed path is added as a path', async () => {
    marketplaceAdd.mockResolvedValue(fileSource())
    const field = await openAdd()
    fireEvent.change(field, { target: { value: '/Users/x/shelf/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('marketplace-add-submit'))
    await waitFor(() => expect(marketplaceAdd)
      .toHaveBeenCalledWith({ path: '/Users/x/shelf/marketplace-catalog.yaml' }))
  })

  it('a dropped .yaml file travels as its path', async () => {
    marketplaceAdd.mockResolvedValue(fileSource())
    pathForFile.mockReturnValue('/Users/x/shelf/marketplace-catalog.yaml')
    await openAdd()
    const file = new File(['format_version: 1'], 'marketplace-catalog.yaml', { type: 'text/yaml' })
    fireEvent.drop(screen.getByTestId('marketplace-drop-zone'), { dataTransfer: { files: [file] } })
    // §22.3: only the path travels - the backend reads the file itself.
    await waitFor(() => expect(pathForFile).toHaveBeenCalledWith(file))
    await waitFor(() => expect(marketplaceAdd)
      .toHaveBeenCalledWith({ path: '/Users/x/shelf/marketplace-catalog.yaml' }))
  })

  it('dropping anything else adds nothing and says so', async () => {
    await openAdd()
    fireEvent.drop(screen.getByTestId('marketplace-drop-zone'), {
      dataTransfer: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] },
    })
    expect((await screen.findByTestId('marketplace-drop-error')).textContent)
      .toBe('Drop one .yaml file.')
    expect(marketplaceAdd).not.toHaveBeenCalled()
    // §22.3: several files are refused the same way.
    fireEvent.drop(screen.getByTestId('marketplace-drop-zone'), {
      dataTransfer: {
        files: [new File(['x'], 'a.yaml'), new File(['x'], 'b.yaml')],
      },
    })
    expect(screen.getByTestId('marketplace-drop-error').textContent).toBe('Drop one .yaml file.')
    expect(marketplaceAdd).not.toHaveBeenCalled()
  })

  it('the added catalog reaches the page only after the §14 exit animation', async () => {
    let land: (s: MarketplaceSource) => void = () => {}
    marketplaceAdd.mockImplementation(() => new Promise((res) => { land = res }))
    const field = await openAdd()
    fireEvent.change(field, { target: { value: '/Users/x/shelf/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('marketplace-add-submit'))
    await waitFor(() => expect(marketplaceAdd).toHaveBeenCalled())
    await act(async () => { land(fileSource()) })
    // §14: the card is still on screen, running its exit - the page has not
    // been told yet, so nothing unmounts the portal mid-animation.
    expect(screen.getByText('Add marketplace')).toBeTruthy()
    expect(storeMod.useStore.getState().toast).not.toBe('Added Community.')
    await waitFor(() => expect(storeMod.useStore.getState().toast).toBe('Added Community.'),
      { timeout: 3000 })
    expect(screen.queryByText('Add marketplace')).toBeNull()
  })

  it('the page refetches its list after an add (§22.3)', async () => {
    marketplaceAdd.mockResolvedValue(fileSource())
    const field = await openAdd()
    const listed = marketplaceList.mock.calls.length
    fireEvent.change(field, { target: { value: '/Users/x/shelf/marketplace-catalog.yaml' } })
    fireEvent.click(screen.getByTestId('marketplace-add-submit'))
    await waitFor(() => expect(marketplaceAdd).toHaveBeenCalled())
    // §22.3: every action the page takes is followed by its own list - the
    // new row lands without waiting for anything else to ask.
    await waitFor(() => expect(marketplaceList.mock.calls.length).toBeGreaterThan(listed),
      { timeout: 3000 })
  })

  it('clicking the drop zone opens the native picker and adds the path', async () => {
    openCatalog.mockResolvedValue({ path: '/Users/x/shelf/marketplace-catalog.yaml' })
    marketplaceAdd.mockResolvedValue(fileSource())
    await openAdd()
    fireEvent.click(screen.getByTestId('marketplace-drop-zone'))
    await waitFor(() => expect(marketplaceAdd)
      .toHaveBeenCalledWith({ path: '/Users/x/shelf/marketplace-catalog.yaml' }))
  })
})

describe('§22.7 catalog authoring', () => {
  // Opens the editor on a catalog that lives on this machine and waits for the
  // served catalog to land on the details form, which is viewed on open.
  const openEditor = async (s: MarketplaceSource = fileSource()) => {
    marketplaceList.mockResolvedValue({ sources: [s] })
    render(<MarketplacePage />)
    await openActions()
    fireEvent.click(menuRow('Edit catalog…'))
    return await screen.findByTestId('catalog-name') as HTMLInputElement
  }

  // §22.7: the add form, filled and accepted - Add appends the entry, views
  // it, and closes the form (a §14 200 ms exit).
  const addAutomation = async (fields: { title: string; description?: string; path: string; image?: string }) => {
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    fireEvent.change(await screen.findByTestId('catalog-picker-title'), { target: { value: fields.title } })
    if (fields.description !== undefined) {
      fireEvent.change(screen.getByTestId('catalog-picker-description'), { target: { value: fields.description } })
    }
    fireEvent.change(screen.getByTestId('catalog-picker-path'), { target: { value: fields.path } })
    if (fields.image !== undefined) {
      fireEvent.change(screen.getByTestId('catalog-picker-image'), { target: { value: fields.image } })
    }
    fireEvent.click(screen.getByTestId('catalog-picker-add'))
    await waitFor(() => expect(screen.queryByTestId('catalog-picker')).toBeNull(), { timeout: 3000 })
  }

  it('Edit shows only for a catalog on this machine', async () => {
    marketplaceList.mockResolvedValue({ sources: [fileSource()] })
    render(<MarketplacePage />)
    await openActions()
    expect(menuRow('Edit catalog…')).toBeTruthy()
    cleanup()
    // §22.7: a catalog the app keeps the only copy of is edited in place.
    marketplaceList.mockResolvedValue({ sources: [keptSource()] })
    render(<MarketplacePage />)
    await openActions()
    expect(menuRow('Edit catalog…')).toBeTruthy()
    cleanup()
    // §22.7: a link catalog's file isn't here to edit.
    marketplaceList.mockResolvedValue({ sources: [source()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    await openActions()
    expect(screen.queryByRole('button', { name: 'Edit catalog…' })).toBeNull()
  })

  it('the editor opens on the catalog file with the details form viewed', async () => {
    const name = await openEditor()
    await waitFor(() => expect(marketplaceCatalogRead).toHaveBeenCalledWith('s1'))
    expect(screen.getByTestId('catalog-editor')).toBeTruthy()
    expect(screen.getByText('EDIT CATALOG')).toBeTruthy()
    // §22.7: the details row is viewed on open, and names where the file lives.
    expect(screen.getByTestId('catalog-nav-details').getAttribute('aria-current')).toBe('true')
    expect(screen.getByText('DETAILS')).toBeTruthy()
    expect(screen.getByTestId('catalog-where').textContent)
      .toBe('/Users/x/shelf/marketplace-catalog.yaml')
    // §22.7: the details form leads with the full path (the row only has its tail).
    expect(screen.getByTestId('catalog-location').textContent)
      .toBe('/Users/x/shelf/marketplace-catalog.yaml')
    expect(name.value).toBe('Community')
    expect((screen.getByTestId('catalog-description') as HTMLInputElement).value)
      .toBe('Automations I use.')
    // §22.7: one navigator row per entry - its title over its reference label.
    const rows = screen.getAllByTestId('catalog-nav-row')
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toBe('Manga chapter watchermanga.autowright')
  })

  it('a catalog kept by Autowright says so where the path would be', async () => {
    await openEditor(keptSource())
    expect(screen.getByTestId('catalog-where').textContent).toBe('Stored by Autowright')
    expect(screen.getByTestId('catalog-location').textContent).toBe('Stored by Autowright')
  })

  it('clicking a row views its entry form, editable in place', async () => {
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-nav-row'))
    expect((await screen.findByTestId('catalog-entry-title') as HTMLInputElement).value)
      .toBe('Manga chapter watcher')
    expect((screen.getByTestId('catalog-entry-description') as HTMLInputElement).value)
      .toBe('Checks the series you follow every morning at 8.')
    expect((screen.getByTestId('catalog-entry-path') as HTMLInputElement).value)
      .toBe('/Users/x/shelf/automations/manga.autowright')
    expect(screen.getByText('AUTOMATION 1 OF 1')).toBeTruthy()
    // §22.7: the form pane holds one form at a time - Details is not in it.
    expect(screen.queryByTestId('catalog-name')).toBeNull()
    fireEvent.change(screen.getByTestId('catalog-entry-title'), { target: { value: 'Renamed' } })
    await waitFor(() => expect(screen.getByTestId('catalog-nav-row').textContent)
      .toBe('Renamedmanga.autowright'))
  })

  it('the add form appends the entry as typed, viewed, with nothing exported', async () => {
    storeMod.useStore.setState({ automations: [auto()] })
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    expect(await screen.findByTestId('catalog-picker')).toBeTruthy()
    // §22.7: the four entry fields and nothing else - no tabs, no lists.
    expect(screen.queryByTestId('catalog-picker-row')).toBeNull()
    expect(screen.getByTestId('catalog-picker-add').hasAttribute('disabled')).toBe(true)
    fireEvent.change(screen.getByTestId('catalog-picker-title'), { target: { value: '  Weekly digest ' } })
    expect(screen.getByTestId('catalog-picker-add').hasAttribute('disabled')).toBe(false)
    fireEvent.change(screen.getByTestId('catalog-picker-description'), { target: { value: 'Sends the mail. ' } })
    fireEvent.change(screen.getByTestId('catalog-picker-path'),
      { target: { value: ' /Users/x/archives/Weekly digest.autowright' } })
    fireEvent.change(screen.getByTestId('catalog-picker-image'),
      { target: { value: 'https://example.com/shared/images/digest.png' } })
    fireEvent.keyDown(screen.getByTestId('catalog-picker-image'), { key: 'Enter' })
    await waitFor(() => expect(screen.queryByTestId('catalog-picker')).toBeNull(), { timeout: 3000 })
    // §22.7: Add appends the row (trimmed) and views it; the editor exported
    // nothing and opened no dialog.
    const rows = screen.getAllByTestId('catalog-nav-row')
    expect(rows).toHaveLength(2)
    expect(rows[1].textContent).toBe('Weekly digestWeekly digest.autowright')
    expect((screen.getByTestId('catalog-entry-title') as HTMLInputElement).value).toBe('Weekly digest')
    expect((screen.getByTestId('catalog-entry-description') as HTMLInputElement).value).toBe('Sends the mail.')
    expect((screen.getByTestId('catalog-entry-path') as HTMLInputElement).value)
      .toBe('/Users/x/archives/Weekly digest.autowright')
    expect((screen.getByTestId('catalog-entry-image') as HTMLInputElement).value)
      .toBe('https://example.com/shared/images/digest.png')
    expect(exportAutomation).not.toHaveBeenCalled()
    expect(saveFile).not.toHaveBeenCalled()
  })

  it('the add form takes a GitHub file page as the archive and as the image', async () => {
    await openEditor()
    await addAutomation({
      title: 'Inbox',
      path: 'https://github.com/alice/shelf/blob/main/inbox.autowright',
      image: 'https://github.com/alice/shelf/blob/main/cover.svg',
    })
    // §22.1: a GitHub page is an https reference like any other - Add appends
    // it exactly as typed, with nothing rejected.
    expect(screen.getAllByTestId('catalog-nav-row')).toHaveLength(2)
    expect((screen.getByTestId('catalog-entry-path') as HTMLInputElement).value)
      .toBe('https://github.com/alice/shelf/blob/main/inbox.autowright')
    expect((screen.getByTestId('catalog-entry-image') as HTMLInputElement).value)
      .toBe('https://github.com/alice/shelf/blob/main/cover.svg')
    expect(screen.queryByTestId('catalog-error')).toBeNull()
  })

  it('a bad reference or a bad image stops Add in place', async () => {
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    fireEvent.change(await screen.findByTestId('catalog-picker-title'), { target: { value: 'Digest' } })
    fireEvent.change(screen.getByTestId('catalog-picker-path'), { target: { value: 'archives/Digest.autowright' } })
    fireEvent.click(screen.getByTestId('catalog-picker-add'))
    // §22.7: the same checks Save runs, here, and nothing is appended.
    expect((await screen.findByTestId('catalog-picker-error')).textContent)
      .toBe('Give an https link or an absolute path to an .autowright file.')
    expect(screen.getAllByTestId('catalog-nav-row')).toHaveLength(1)
    fireEvent.change(screen.getByTestId('catalog-picker-path'),
      { target: { value: 'https://example.com/Digest.autowright' } })
    fireEvent.change(screen.getByTestId('catalog-picker-image'), { target: { value: '/Users/x/cover.bmp' } })
    fireEvent.click(screen.getByTestId('catalog-picker-add'))
    expect(screen.getByTestId('catalog-picker-error').textContent)
      .toBe('Give an https link or an absolute path to a .png, .jpg, .jpeg, .webp, .gif, or .svg image.')
    expect(screen.getAllByTestId('catalog-nav-row')).toHaveLength(1)
    // …and the form is still open with what was typed.
    expect((screen.getByTestId('catalog-picker-title') as HTMLInputElement).value).toBe('Digest')
    fireEvent.change(screen.getByTestId('catalog-picker-image'), { target: { value: '' } })
    fireEvent.click(screen.getByTestId('catalog-picker-add'))
    await waitFor(() => expect(screen.queryByTestId('catalog-picker')).toBeNull(), { timeout: 3000 })
    expect(screen.getAllByTestId('catalog-nav-row')).toHaveLength(2)
  })

  it('Remove from catalog drops the viewed entry', async () => {
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-nav-row'))
    fireEvent.click(await screen.findByTestId('catalog-entry-remove'))
    // §22.7: with nothing left, the details form is viewed again.
    await waitFor(() => expect(screen.queryByTestId('catalog-nav-row')).toBeNull())
    expect(screen.getByTestId('catalog-name')).toBeTruthy()
    expect(screen.getByText('No automations yet.')).toBeTruthy()
  })

  it('Save sends the §22.7 body - every row by its path, the image when set', async () => {
    marketplaceCatalogSave.mockResolvedValue(fileSource({ name: 'Shelf' }))
    const name = await openEditor()
    fireEvent.change(name, { target: { value: 'Shelf' } })
    // a local archive and a linked one, typed in
    await addAutomation({ title: 'Watcher', description: 'Checks the feed.', path: '/Users/x/archives/Watcher.autowright' })
    await addAutomation({ title: 'Digest', path: 'https://example.com/shared/automations/digest.autowright',
      image: 'https://example.com/shared/images/digest.png' })
    // the kept row gets an image reference
    fireEvent.click(screen.getAllByTestId('catalog-nav-row')[0])
    fireEvent.change(await screen.findByTestId('catalog-entry-image'),
      { target: { value: '/Users/x/shelf/images/manga.png' } })
    fireEvent.click(screen.getByTestId('catalog-nav-details'))
    await screen.findByTestId('catalog-name')
    fireEvent.click(screen.getByTestId('catalog-save'))
    await waitFor(() => expect(marketplaceCatalogSave).toHaveBeenCalledWith('s1', {
      name: 'Shelf',
      description: 'Automations I use.',
      entries: [
        {
          title: 'Manga chapter watcher',
          description: 'Checks the series you follow every morning at 8.',
          image: '/Users/x/shelf/images/manga.png',
          path: '/Users/x/shelf/automations/manga.autowright',
        },
        {
          title: 'Watcher', description: 'Checks the feed.',
          path: '/Users/x/archives/Watcher.autowright',
        },
        {
          title: 'Digest', description: '',
          image: 'https://example.com/shared/images/digest.png',
          path: 'https://example.com/shared/automations/digest.autowright',
        },
      ],
    }))
  })

  it('a blank title, a bad reference and a bad image stop Save on the entry', async () => {
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-nav-row'))
    fireEvent.change(await screen.findByTestId('catalog-entry-title'), { target: { value: '  ' } })
    fireEvent.click(screen.getByTestId('catalog-nav-details'))
    await screen.findByTestId('catalog-name')
    fireEvent.click(screen.getByTestId('catalog-save'))
    // §22.7: the editor checks itself first - nothing travels, and the entry
    // with the problem is the one it views.
    expect(screen.getByTestId('catalog-error').textContent).toBe('Give this automation a title.')
    expect(await screen.findByTestId('catalog-entry-title')).toBeTruthy()
    fireEvent.change(screen.getByTestId('catalog-entry-title'), { target: { value: 'Manga' } })
    fireEvent.change(screen.getByTestId('catalog-entry-path'), { target: { value: 'manga.autowright' } })
    fireEvent.click(screen.getByTestId('catalog-save'))
    expect(screen.getByTestId('catalog-error').textContent)
      .toBe('Give an https link or an absolute path to an .autowright file.')
    fireEvent.change(screen.getByTestId('catalog-entry-path'),
      { target: { value: 'https://example.com/manga.autowright' } })
    fireEvent.change(screen.getByTestId('catalog-entry-image'), { target: { value: '/Users/x/cover.bmp' } })
    fireEvent.click(screen.getByTestId('catalog-save'))
    expect(screen.getByTestId('catalog-error').textContent)
      .toBe('Give an https link or an absolute path to a .png, .jpg, .jpeg, .webp, .gif, or .svg image.')
    expect(marketplaceCatalogSave).not.toHaveBeenCalled()
  })

  it('a rejected save shows the reason in the footer and views the entry it names', async () => {
    marketplaceCatalogSave.mockRejectedValue(new Error('entry 0: it has no title'))
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-save'))
    expect((await screen.findByTestId('catalog-error')).textContent)
      .toBe('entry 0: it has no title')
    // §22.7: a message naming an entry views it, and the editor stays open.
    expect(screen.getByTestId('catalog-entry-title')).toBeTruthy()
    expect(screen.getByTestId('catalog-editor')).toBeTruthy()
  })

  it('Escape with unsaved edits raises the discard confirm', async () => {
    const name = await openEditor()
    fireEvent.change(name, { target: { value: 'Renamed' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(await screen.findByText('Discard your catalog edits?')).toBeTruthy()
    expect(screen.getByTestId('catalog-editor')).toBeTruthy()
    fireEvent.click(screen.getByText('Discard'))
    // the confirm's exit animation, then the editor's own - both fall back to
    // the §14 200 ms timer in happy-dom
    await waitFor(() => expect(screen.queryByTestId('catalog-editor')).toBeNull(),
      { timeout: 3000 })
    expect(marketplaceCatalogSave).not.toHaveBeenCalled()
  })

  it('Create catalog… opens the empty editor, kept by Autowright', async () => {
    marketplaceCatalogCreate.mockResolvedValue(keptSource({ id: 's9', name: 'Mine' }))
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByTestId('marketplace-create'))
    // §22.7 create mode: no GET, empty fields, and no LOCATION line - one note
    // says to create first and export from the Marketplace page afterward.
    expect(await screen.findByTestId('catalog-editor')).toBeTruthy()
    expect(screen.getByText('CREATE CATALOG')).toBeTruthy()
    expect(marketplaceCatalogRead).not.toHaveBeenCalled()
    expect(screen.getByTestId('catalog-where').textContent).toBe('Stored by Autowright')
    expect(screen.queryByTestId('catalog-location')).toBeNull()
    expect(screen.queryByText('LOCATION')).toBeNull()
    expect(screen.getByTestId('catalog-create-note').textContent).toBe(
      'Create the catalog first. You can export its file from the Marketplace page afterward.')
    expect(screen.queryByTestId('catalog-folder')).toBeNull()
    expect(screen.queryByTestId('catalog-choose-folder')).toBeNull()
    expect(screen.getByText('No automations yet.')).toBeTruthy()
    const name = screen.getByTestId('catalog-name') as HTMLInputElement
    expect(name.value).toBe('')
    expect(name.placeholder).toBe('My catalog')
    fireEvent.change(name, { target: { value: 'Mine' } })
    const save = screen.getByTestId('catalog-save') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    expect(save.textContent).toBe('Create')
    fireEvent.click(save)
    // §22.7: the create body is the content alone - never a `folder`.
    await waitFor(() => expect(marketplaceCatalogCreate).toHaveBeenCalledWith({
      name: 'Mine', description: '', entries: [],
    }))
    expect(pickFolder).not.toHaveBeenCalled()
  })
})
