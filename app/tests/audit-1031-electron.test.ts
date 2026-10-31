// audit 2026-10-31: §3 step 0 stop-and-quit lock between update-install and
// the quit paths, and the late-staging download refusal (spec/packaging.md §3).
// main.cjs exports nothing, so, as in main-cjs-leaf.test.ts, the handlers are
// reached by evaluating the real source against a stub `electron` module:
// real code, real handlers, only the Electron surface underneath is a double.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

const ELECTRON_DIR = join(__dirname, '..', 'electron')
const PLATFORM_DIR = join(ELECTRON_DIR, 'platform')
const src = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
const realRequire = createRequire(join(ELECTRON_DIR, 'main.cjs'))
const platMod = realRequire(join(PLATFORM_DIR, 'index.cjs')) as {
  UPDATER: string
  capabilities: { updates: boolean }
}
const HAS_UPDATER = platMod.capabilities.updates

const savedHome = process.env.AUTOWRIGHT_HOME
const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM
const nodeProcess = process as unknown as { resourcesPath?: string }
const savedResourcesPath = nodeProcess.resourcesPath

type ServiceChildCallback = (err: Error | null, stdout: string, stderr: string) => void

// The electron-updater double: one fake under every class name, with the
// Electron autoUpdater (Squirrel.Mac) modelled as either staging instantly or
// staying silent (nativeSilent), as in main-cjs-leaf.test.ts.
interface UpdaterRecord {
  checks: number
  downloads: number
  installs: number
  nativeChecks: number
  nativeSilent: boolean
  check: unknown
  squirrelDownloadedUpdate: boolean
}

interface MainStub {
  invoke: (channel: string, ...args: unknown[]) => unknown
  emit: (event: string) => boolean
  openLoadedWindow: () => void
  sent: [string, unknown][]
  updater: UpdaterRecord
  home: string
  log: () => string
}

function loadMain(execFile: (py: string, args: string[], options: unknown, cb: ServiceChildCallback) => void): MainStub {
  const handlers = new Map<string, (e: unknown, ...args: unknown[]) => unknown>()
  const appEvents = new Map<string, (e: { preventDefault: () => void }) => void>()
  const sent: [string, unknown][] = []
  const home = mkdtempSync(join(tmpdir(), 'aw-main-'))
  process.env.AUTOWRIGHT_HOME = home
  const updater: UpdaterRecord = {
    checks: 0, downloads: 0, installs: 0, nativeChecks: 0, nativeSilent: false,
    check: { updateInfo: { version: '9.9.9' } }, squirrelDownloadedUpdate: false,
  }
  const nativeListeners = new Map<string, Set<(arg?: unknown) => void>>()
  const nativeEmit = (event: string, arg?: unknown) => {
    for (const fn of [...(nativeListeners.get(event) ?? [])]) fn(arg)
  }
  const updaterListeners = new Map<string, (arg: unknown) => void>()

  class FakeUpdater {
    autoDownload = true
    autoInstallOnAppQuit = true
    logger: unknown = null
    get squirrelDownloadedUpdate() { return updater.squirrelDownloadedUpdate }
    set squirrelDownloadedUpdate(value: boolean) { updater.squirrelDownloadedUpdate = value }
    get installerPath() { return null }
    on(event: string, fn: (arg: unknown) => void) { updaterListeners.set(event, fn) }
    async checkForUpdates() { updater.checks += 1; return updater.check }
    async downloadUpdate() { updater.downloads += 1; return [] }
    quitAndInstall() { updater.installs += 1 }
  }

  const wcListeners: Map<string, (...a: unknown[]) => void>[] = []

  class FakeWindow {
    wc = new Map<string, (...a: unknown[]) => void>()
    destroyed = false
    webContents = {
      on: (event: string, fn: (...a: unknown[]) => void) => { this.wc.set(event, fn) },
      once: (event: string, fn: (...a: unknown[]) => void) => { this.wc.set(event, fn) },
      send: (channel: string, payload: unknown) => { sent.push([channel, payload]) },
      setWindowOpenHandler() {},
      isLoading: () => false, getURL: () => '',
    }

    constructor() { wcListeners.push(this.wc) }
    loadFile() {} loadURL() {}
    on() {}
    show() {} focus() {} hide() {} restore() {}
    isMinimized() { return false }
    setSize() {} setPosition() {}
    isVisible() { return false }
    setVisibleOnAllWorkspaces() {}
    destroy() { this.destroyed = true }
    isDestroyed() { return this.destroyed }
  }

  const electron = {
    app: {
      setPath() {},
      getPath: () => home,
      getVersion: () => '0.0.0',
      commandLine: { appendSwitch() {} },
      requestSingleInstanceLock: () => true,
      on(event: string, fn: (e: { preventDefault: () => void }) => void) { appEvents.set(event, fn) },
      isReady: () => false,
      quit() {},
      exit() {},
      whenReady: () => new Promise(() => {}),
      isPackaged: true,
      getLoginItemSettings: () => ({ openAtLogin: false }),
      setAppUserModelId() {},
      setLoginItemSettings() {},
      dock: { setIcon() {} },
    },
    autoUpdater: {
      on(event: string, fn: (arg?: unknown) => void) {
        if (!nativeListeners.has(event)) nativeListeners.set(event, new Set())
        nativeListeners.get(event)!.add(fn)
      },
      removeListener(event: string, fn: (arg?: unknown) => void) { nativeListeners.get(event)?.delete(fn) },
      setFeedURL() {},
      checkForUpdates() {
        updater.nativeChecks += 1
        if (updater.nativeSilent) return
        updater.squirrelDownloadedUpdate = true
        nativeEmit('update-downloaded')
      },
      quitAndInstall() {},
    },
    BrowserWindow: FakeWindow,
    Notification: class {
      static isSupported() { return false }
      on() {} show() {}
    },
    Menu: { buildFromTemplate: () => ({ popup() {} }) },
    Tray: class { setToolTip() {} on() {} setImage() {} destroy() {} },
    dialog: {
      showErrorBox: () => { throw new Error('showErrorBox blocks the main process') },
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
      showSaveDialog: async () => ({ canceled: true, filePath: null }),
      showMessageBox: () => new Promise(() => {}),
    },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    powerMonitor: { on() {} },
    session: { defaultSession: { clearStorageData: async () => {}, clearCache: async () => {} } },
    ipcMain: { handle: (name: string, fn: never) => { handlers.set(name, fn) } },
    shell: { openPath() {}, showItemInFolder() {}, openExternal: () => Promise.resolve() },
    screen: {
      getCursorScreenPoint: () => ({ x: 400, y: 12 }),
      getDisplayNearestPoint: () => ({
        bounds: { x: 0, y: 0, width: 1440, height: 900 },
        workArea: { x: 0, y: 0, width: 1440, height: 875 },
      }),
    },
  }

  const load = new Function('require', 'module', 'exports', '__dirname', '__filename', src)
  load(
    (id: string) => {
      if (id === 'electron') return electron
      if (id === 'electron-updater') {
        return { MacUpdater: FakeUpdater, NsisUpdater: FakeUpdater, AppImageUpdater: FakeUpdater }
      }
      if (id === 'child_process') return { ...realRequire('child_process') as object, execFile }
      return realRequire(id)
    },
    { exports: {} }, {}, ELECTRON_DIR, join(ELECTRON_DIR, 'main.cjs'),
  )

  return {
    invoke: (channel, ...args) => {
      const fn = handlers.get(channel)
      if (!fn) throw new Error(`no handler for ${channel}`)
      return fn({}, ...args)
    },
    // Answers whether the listener called preventDefault.
    emit: (event) => {
      const fn = appEvents.get(event)
      if (!fn) throw new Error(`no app listener for ${event}`)
      let prevented = false
      fn({ preventDefault: () => { prevented = true } })
      return prevented
    },
    openLoadedWindow: () => {
      void (handlers.get('open-app')!)({}, '/app')
      const wc = wcListeners[wcListeners.length - 1]
      wc.get('did-start-loading')?.({})
      wc.get('did-finish-load')?.({})
    },
    sent, updater, home,
    log: () => {
      try { return readFileSync(join(home, 'logs', 'app.log'), 'utf-8') } catch { return '' }
    },
  }
}

describe('main.cjs audit 2026-10-31 (§3 step 0, late staging)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    if (savedResourcesPath === undefined) delete nodeProcess.resourcesPath
    else nodeProcess.resourcesPath = savedResourcesPath
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
  })

  // An update install held at its live-execution gate: the /executions probe
  // answers only when the test releases it, so installInFlight stays set.
  function holdInstall() {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    let release: (() => void) | null = null
    let probes = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/executions')) {
        probes += 1
        return new Promise<Response>((resolve) => {
          release = () => resolve({ ok: true, json: async () => ({ executions: [{ id: 'e1' }], total: 1 }) } as Response)
        })
      }
      return Promise.reject(new Error('offline in tests'))
    })
    const stops: string[][] = []
    const m = loadMain((_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    const install = m.invoke('update-install') as Promise<unknown>
    return { m, stops, install, probes: () => probes, release: () => release?.() }
  }

  it('quit-all during an update install answers the in-progress error and stops nothing', async () => {
    if (!HAS_UPDATER) return
    const held = holdInstall()
    await vi.waitFor(() => expect(held.probes()).toBe(1))
    expect(await held.m.invoke('quit-all', { force: true }))
      .toEqual({ error: 'another operation is in progress' })
    expect(held.stops).toEqual([])
    held.release()
    expect(await held.install).toEqual({ busy: true })
  })

  it('an OS quit during an update install is ignored with a log line', async () => {
    if (!HAS_UPDATER) return
    const held = holdInstall()
    await vi.waitFor(() => expect(held.probes()).toBe(1))
    held.m.openLoadedWindow()
    // Held (preventDefault), but no quit flow starts: no renderer ask, no stop.
    expect(held.m.emit('before-quit')).toBe(true)
    expect(held.m.sent.filter(([channel]) => channel === 'quit-requested')).toEqual([])
    expect(held.stops).toEqual([])
    expect(held.m.log()).toContain('quit: ignored, an update install is in flight')
    expect(held.m.log()).not.toContain('quit: requested')
    held.release()
    expect(await held.install).toEqual({ busy: true })
  })

  it('update-install while the renderer is being asked to quit answers the in-progress error', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const stops: string[][] = []
    const m = loadMain((_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') })
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.openLoadedWindow()
    // Cmd+Q with a loaded window arms quitAskTimer and asks the renderer.
    expect(m.emit('before-quit')).toBe(true)
    expect(m.sent).toContainEqual(['quit-requested', undefined])
    expect(await m.invoke('update-install')).toEqual({ error: 'another operation is in progress' })
    expect(m.updater.nativeChecks).toBe(0)
    expect(m.updater.installs).toBe(0)
    expect(stops).toEqual([])
  })

  it('macOS: a download while a given-up staging\'s late listener is armed asks for a restart', async () => {
    if (platMod.UPDATER !== 'mac') return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain((_py, _args, _o, cb) => { cb(null, 'stopped', '') })
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.check = { updateInfo: { version: '0.14.0' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    m.updater.nativeSilent = true
    let answer: unknown = null
    void (m.invoke('update-install') as Promise<unknown>).then((r) => { answer = r })
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(answer).toEqual({ error: 'the updater stopped responding' })
    // Nothing staged yet and the flag unset, but the late listener is armed:
    // a newer download is refused before MacUpdater is touched.
    m.updater.check = { updateInfo: { version: '0.14.1' } }
    const checksBefore = m.updater.checks
    expect(await m.invoke('update-download'))
      .toEqual({ error: 'Restart to finish the update you already downloaded.' })
    expect(m.updater.checks).toBe(checksBefore)
    expect(m.updater.downloads).toBe(1)
  })
})
