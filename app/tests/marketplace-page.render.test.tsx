// §22.3/§22.6 Marketplace page: the preview gate on the nav row and the page
// itself (the §4.9 developerMode setting), the empty state's example catalog,
// a seeded catalog's grid, a hidden catalog collapsing to its header, the
// settings modal's PATCH, Install opening the §9.1 import modal on its preview
// step, the add modal's three ways in, and the §22.7 authoring flow (the Edit
// button, the catalog editor, the automation picker, the EXPORT FOLDER row,
// Save's body, the discard confirm, and create mode's folder chooser). App
// renders for real (happy-dom) with the api module mocked, `settings-gating`
// style.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
const marketplaceImage = vi.fn(() => Promise.reject(new Error('no image')))
// §22.7 authoring
const marketplaceCatalogCreate = vi.fn()
const marketplaceCatalogRead = vi.fn<(id: string) => Promise<MarketplaceCatalog>>()
const marketplaceCatalogSave = vi.fn()

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
    marketplaceCatalogCreate: (body: unknown) => marketplaceCatalogCreate(body),
    marketplaceCatalogRead: (id: string) => marketplaceCatalogRead(id),
    marketplaceCatalogSave: (id: string, body: unknown) => marketplaceCatalogSave(id, body),
  },
}))

let storeMod: typeof import('../src/store')
let App: typeof import('../src/App').default
let MarketplacePage: typeof import('../src/pages/MarketplacePage').default

const openCatalog = vi.fn()
// §22.3: the dropped file's path, which is all that ever travels.
const pathForFile = vi.fn<(file: File) => string>()
// §22.7: the native pickers the create flow and the editor's file button use.
const pickFolder = vi.fn()
const openArchivePath = vi.fn()

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
    pathForFile,
    pickFolder,
    openArchivePath,
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
    archive: 'https://example.com/shared/automations/manga.autowright', image: false,
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

beforeEach(() => {
  servedSettings = { ...SETTINGS }
  marketplaceList.mockReset()
  marketplaceList.mockResolvedValue({ sources: [] })
  marketplaceAdd.mockReset()
  marketplaceSettings.mockReset()
  marketplaceSettings.mockResolvedValue(source())
  marketplaceEntryPreview.mockReset()
  openCatalog.mockReset()
  pathForFile.mockReset()
  marketplaceCatalogCreate.mockReset()
  marketplaceCatalogRead.mockReset()
  marketplaceCatalogRead.mockResolvedValue(catalog())
  marketplaceCatalogSave.mockReset()
  pickFolder.mockReset()
  openArchivePath.mockReset()
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
    // §22.1: an entry names its archive by reference, never by a copy.
    expect(screen.getByText(/path: \/Users\/you\//)).toBeTruthy()
    // §22.3: Refresh all needs at least one catalog with a location.
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
    // §22.7: Create catalog… in the header and again in MAKE YOUR OWN.
    expect(screen.getAllByTestId('marketplace-create')).toHaveLength(2)
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
    expect(screen.getByLabelText('Refresh')).toBeTruthy()
    expect(screen.getByLabelText('Catalog settings')).toBeTruthy()
    expect(screen.getByLabelText('Remove')).toBeTruthy()
    // §22.3: the example catalog is the empty state's alone.
    expect(screen.queryByText('MAKE YOUR OWN')).toBeNull()
  })

  it('a file catalog names its file and refreshes like a link', async () => {
    marketplaceList.mockResolvedValue({ sources: [fileSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    expect(screen.getByText('marketplace-catalog.yaml')).toBeTruthy()
    // §22.2: a path is re-read like a link - both refresh.
    expect(screen.getByLabelText('Refresh')).toBeTruthy()
    expect(screen.getByTestId('marketplace-refresh-all')).toBeTruthy()
    expect(screen.getByText(/^Refreshed Today, /)).toBeTruthy()
  })

  it('a catalog kept by Autowright has nothing to refresh from', async () => {
    marketplaceList.mockResolvedValue({ sources: [keptSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    // §22.2: a null location is not refreshable - it says when it was added.
    expect(screen.getByText('Kept by Autowright')).toBeTruthy()
    expect(screen.queryByLabelText('Refresh')).toBeNull()
    expect(screen.queryByTestId('marketplace-refresh-all')).toBeNull()
    expect(screen.getByText(/^Added Today, /)).toBeTruthy()
    expect(screen.queryByText(/^Refreshed /)).toBeNull()
    expect(screen.getByLabelText('Remove')).toBeTruthy()
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
    expect(screen.getByLabelText('Catalog settings')).toBeTruthy()
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

describe('§22.3 catalog settings', () => {
  // Opens the gear modal on the served catalog.
  const openSettings = async (s: MarketplaceSource) => {
    marketplaceList.mockResolvedValue({ sources: [s] })
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByLabelText('Catalog settings'))
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
  // served catalog to land.
  const openEditor = async (s: MarketplaceSource = fileSource()) => {
    marketplaceList.mockResolvedValue({ sources: [s] })
    render(<MarketplacePage />)
    fireEvent.click(await screen.findByLabelText('Edit catalog'))
    return await screen.findByTestId('catalog-name') as HTMLInputElement
  }

  it('Edit shows only for a catalog on this machine', async () => {
    marketplaceList.mockResolvedValue({ sources: [fileSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByLabelText('Edit catalog')).toBeTruthy()
    cleanup()
    // §22.7: a catalog the app keeps the only copy of is edited in place.
    marketplaceList.mockResolvedValue({ sources: [keptSource()] })
    render(<MarketplacePage />)
    expect(await screen.findByLabelText('Edit catalog')).toBeTruthy()
    cleanup()
    // §22.7: a link catalog's file isn't here to edit.
    marketplaceList.mockResolvedValue({ sources: [source()] })
    render(<MarketplacePage />)
    expect(await screen.findByTestId('marketplace-source')).toBeTruthy()
    expect(screen.queryByLabelText('Edit catalog')).toBeNull()
  })

  it('the editor opens on the catalog file, not the cache', async () => {
    const name = await openEditor()
    await waitFor(() => expect(marketplaceCatalogRead).toHaveBeenCalledWith('s1'))
    expect(screen.getByTestId('catalog-editor')).toBeTruthy()
    expect(name.value).toBe('Community')
    expect((screen.getByTestId('catalog-description') as HTMLInputElement).value)
      .toBe('Automations I use.')
    // §22.7: where the catalog lives, and one row per entry naming its archive.
    expect(screen.getByTestId('catalog-where').textContent)
      .toBe('/Users/x/shelf/marketplace-catalog.yaml')
    expect(screen.getAllByTestId('catalog-row')).toHaveLength(1)
    expect((screen.getAllByTestId('catalog-row-title')[0] as HTMLInputElement).value)
      .toBe('Manga chapter watcher')
    expect(screen.getByText('/Users/x/shelf/automations/manga.autowright')).toBeTruthy()
  })

  it('a catalog kept by Autowright says so where the path would be', async () => {
    await openEditor(keptSource())
    expect(screen.getByTestId('catalog-where').textContent).toBe('Kept by Autowright')
  })

  it('Add automation… lists this app’s automations, filters, and appends a row', async () => {
    storeMod.useStore.setState({
      automations: [auto(), auto({ id: 'a2', name: 'Digest', description: 'Sends the mail.' })],
    })
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    expect(await screen.findByText('Add automation')).toBeTruthy()
    expect(screen.getAllByTestId('catalog-picker-row')).toHaveLength(2)
    // §22.7: the search filters by name substring
    fireEvent.change(screen.getByTestId('catalog-picker-search'), { target: { value: 'watch' } })
    expect(screen.getAllByTestId('catalog-picker-row')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('catalog-picker-row'))
    await waitFor(() => expect(screen.getAllByTestId('catalog-row')).toHaveLength(2))
    const rows = screen.getAllByTestId('catalog-row')
    expect((rows[1].querySelector('input') as HTMLInputElement).value).toBe('Watcher')
    // §22.7: the archive doesn't exist yet - the save exports it.
    expect(screen.getByText('Exported on save')).toBeTruthy()
  })

  it('Save sends the §22.7 body - a path for the kept row, an id for the new one', async () => {
    storeMod.useStore.setState({ automations: [auto()] })
    marketplaceCatalogSave.mockResolvedValue(fileSource({ name: 'Shelf' }))
    const name = await openEditor()
    fireEvent.change(name, { target: { value: 'Shelf' } })
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    fireEvent.click(await screen.findByTestId('catalog-picker-row'))
    await waitFor(() => expect(screen.getAllByTestId('catalog-row')).toHaveLength(2))
    // §22.7: a file location exports beside the catalog - no EXPORT FOLDER row.
    expect(screen.queryByTestId('catalog-export-folder')).toBeNull()
    fireEvent.click(screen.getByTestId('catalog-save'))
    await waitFor(() => expect(marketplaceCatalogSave).toHaveBeenCalledWith('s1', {
      name: 'Shelf',
      description: 'Automations I use.',
      entries: [
        {
          title: 'Manga chapter watcher',
          description: 'Checks the series you follow every morning at 8.',
          path: '/Users/x/shelf/automations/manga.autowright',
        },
        { title: 'Watcher', description: 'Checks the feed.', automationId: 'a1' },
      ],
    }))
  })

  it('a catalog with no location needs an export folder before it can save', async () => {
    storeMod.useStore.setState({ automations: [auto()] })
    pickFolder.mockResolvedValue('/Users/x/exports')
    marketplaceCatalogSave.mockResolvedValue(keptSource())
    await openEditor(keptSource())
    // §22.7: the row appears only once an automation from this app is listed.
    expect(screen.queryByTestId('catalog-export-folder')).toBeNull()
    fireEvent.click(screen.getByTestId('catalog-add-automation'))
    fireEvent.click(await screen.findByTestId('catalog-picker-row'))
    await waitFor(() => expect(screen.getAllByTestId('catalog-row')).toHaveLength(2))
    expect(screen.getByTestId('catalog-export-folder').textContent).toBe('No folder chosen yet')
    expect((screen.getByTestId('catalog-save') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId('catalog-choose-export-folder'))
    await waitFor(() => expect(screen.getByTestId('catalog-export-folder').textContent)
      .toBe('/Users/x/exports'))
    expect((screen.getByTestId('catalog-save') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByTestId('catalog-save'))
    await waitFor(() => expect(marketplaceCatalogSave).toHaveBeenCalledWith('s1', {
      name: 'Community',
      description: 'Automations I use.',
      exportFolder: '/Users/x/exports',
      entries: [
        {
          title: 'Manga chapter watcher',
          description: 'Checks the series you follow every morning at 8.',
          path: '/Users/x/shelf/automations/manga.autowright',
        },
        { title: 'Watcher', description: 'Checks the feed.', automationId: 'a1' },
      ],
    }))
  })

  it('a rejected save shows the reason inline and keeps the editor open', async () => {
    marketplaceCatalogSave.mockRejectedValue(new Error('entry 0: it has no title'))
    await openEditor()
    fireEvent.click(screen.getByTestId('catalog-save'))
    expect((await screen.findByTestId('catalog-error')).textContent)
      .toBe('entry 0: it has no title')
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

  it('Create catalog… opens the empty editor and POSTs the chosen folder', async () => {
    pickFolder.mockResolvedValue('/Users/x/shelf')
    marketplaceCatalogCreate.mockResolvedValue(fileSource({ id: 's9', name: 'shelf' }))
    render(<MarketplacePage />)
    // The empty state renders a second Create catalog… button - the header's is first.
    fireEvent.click((await screen.findAllByTestId('marketplace-create'))[0])
    // §22.7 create mode: no GET, empty fields, and the app keeping the copy
    // until a folder is chosen.
    expect(await screen.findByTestId('catalog-editor')).toBeTruthy()
    expect(screen.getByText('Create catalog')).toBeTruthy()
    expect(marketplaceCatalogRead).not.toHaveBeenCalled()
    expect(screen.queryByTestId('catalog-where')).toBeNull()
    expect(screen.getByTestId('catalog-folder').textContent).toBe('Kept by Autowright')
    expect(screen.getByText('No automations yet.')).toBeTruthy()
    // §22.7: choosing a folder names the catalog after it
    fireEvent.click(screen.getByTestId('catalog-choose-folder'))
    await waitFor(() => expect(screen.getByTestId('catalog-folder').textContent)
      .toBe('/Users/x/shelf'))
    expect((screen.getByTestId('catalog-name') as HTMLInputElement).value).toBe('shelf')
    const save = screen.getByTestId('catalog-save') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    expect(save.textContent).toBe('Create')
    fireEvent.click(save)
    await waitFor(() => expect(marketplaceCatalogCreate).toHaveBeenCalledWith({
      folder: '/Users/x/shelf', name: 'shelf', description: '', entries: [],
    }))
  })

  it('Create with no folder keeps the only copy in the app', async () => {
    pickFolder.mockResolvedValue(null)
    marketplaceCatalogCreate.mockResolvedValue(keptSource({ id: 's9', name: 'Mine' }))
    render(<MarketplacePage />)
    // The empty state renders a second Create catalog… button - the header's is first.
    fireEvent.click((await screen.findAllByTestId('marketplace-create'))[0])
    // §22.7: a cancelled picker leaves the save location as it was.
    fireEvent.click(await screen.findByTestId('catalog-choose-folder'))
    await waitFor(() => expect(pickFolder).toHaveBeenCalled())
    expect(screen.getByTestId('catalog-folder').textContent).toBe('Kept by Autowright')
    expect((screen.getByTestId('catalog-name') as HTMLInputElement).value).toBe('')
    fireEvent.change(screen.getByTestId('catalog-name'), { target: { value: 'Mine' } })
    fireEvent.click(screen.getByTestId('catalog-save'))
    // §22.7: no folder means no `folder` in the body - the row's copy is it.
    await waitFor(() => expect(marketplaceCatalogCreate).toHaveBeenCalledWith({
      name: 'Mine', description: '', entries: [],
    }))
  })
})
