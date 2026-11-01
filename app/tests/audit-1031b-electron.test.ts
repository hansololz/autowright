// audit 2026-10-31 second sweep: the Windows staged-installer pre-check, the
// narrowed update-install hand-off span, the dropped late-staging listener,
// the result-iframe frame-navigation rule, the quit-dropped install status,
// and the single-flight version sync (spec/packaging.md §3, spec/ui-shell.md
// §9.4). main.cjs exports nothing, so, as in audit-1031-electron.test.ts, the
// handlers are reached by evaluating the real source against a stub
// `electron` module: real code, real handlers, only the Electron surface
// underneath is a double. A test may swap in the real win32 platform module.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

const ELECTRON_DIR = join(__dirname, '..', 'electron')
const PLATFORM_DIR = join(ELECTRON_DIR, 'platform')
const src = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
const realRequire = createRequire(join(ELECTRON_DIR, 'main.cjs'))
const platMod = realRequire(join(PLATFORM_DIR, 'index.cjs')) as { UPDATER: string }
const win32 = realRequire(join(PLATFORM_DIR, 'win32.cjs')) as Record<string, unknown> & {
  bundledPythonPath: (resources: string) => string
  shimText: (python: string) => string
}

const savedHome = process.env.AUTOWRIGHT_HOME
const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM
const savedLocalAppData = process.env.LOCALAPPDATA
const nodeProcess = process as unknown as { resourcesPath?: string }
const savedResourcesPath = nodeProcess.resourcesPath

type ServiceChildCallback = (err: Error | null, stdout: string, stderr: string) => void

interface UpdaterRecord {
  checks: number
  downloads: number
  installs: number
  nativeChecks: number
  nativeSilent: boolean
  check: unknown
  squirrelDownloadedUpdate: boolean
  installerPath: string | null
}

interface FrameEvent {
  isMainFrame: boolean
  url: string
  preventDefault: () => void
}

interface MainStub {
  invoke: (channel: string, ...args: unknown[]) => unknown
  emit: (event: string) => boolean
  openLoadedWindow: () => void
  frameNavigate: (event: Omit<FrameEvent, 'preventDefault'>) => boolean
  nativeEmit: (event: string) => void
  sent: [string, unknown][]
  externals: string[]
  updater: UpdaterRecord
  home: string
  log: () => string
}

interface LoadOptions {
  execFile: (py: string, args: string[], options: unknown, cb: ServiceChildCallback) => void
  // Run the ready chain (ensure-backend among it).
  ready?: boolean
  // Evaluate main.cjs over the real win32 platform module instead of this host's.
  windows?: boolean
}

function loadMain(options: LoadOptions): MainStub {
  const handlers = new Map<string, (e: unknown, ...args: unknown[]) => unknown>()
  const appEvents = new Map<string, (e: { preventDefault: () => void }) => void>()
  const sent: [string, unknown][] = []
  const externals: string[] = []
  const home = mkdtempSync(join(tmpdir(), 'aw-main-'))
  process.env.AUTOWRIGHT_HOME = home
  const updater: UpdaterRecord = {
    checks: 0, downloads: 0, installs: 0, nativeChecks: 0, nativeSilent: false,
    check: { updateInfo: { version: '9.9.9' } }, squirrelDownloadedUpdate: false, installerPath: null,
  }
  const nativeListeners = new Map<string, Set<(arg?: unknown) => void>>()
  const nativeEmit = (event: string, arg?: unknown) => {
    for (const fn of [...(nativeListeners.get(event) ?? [])]) fn(arg)
  }

  class FakeUpdater {
    autoDownload = true
    autoInstallOnAppQuit = true
    logger: unknown = null
    get squirrelDownloadedUpdate() { return updater.squirrelDownloadedUpdate }
    set squirrelDownloadedUpdate(value: boolean) { updater.squirrelDownloadedUpdate = value }
    get installerPath() { return updater.installerPath }
    on() {}
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
    on() {} once() {}
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
      isReady: () => Boolean(options.ready),
      quit() {},
      exit() {},
      whenReady: () => (options.ready ? Promise.resolve() : new Promise(() => {})),
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
    Menu: { buildFromTemplate: () => ({ popup() {} }), setApplicationMenu() {} },
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
    shell: {
      openPath() {},
      showItemInFolder() {},
      openExternal: (url: string) => { externals.push(url); return Promise.resolve() },
    },
    screen: {
      getCursorScreenPoint: () => ({ x: 400, y: 12 }),
      getDisplayNearestPoint: () => ({
        bounds: { x: 0, y: 0, width: 1440, height: 900 },
        workArea: { x: 0, y: 0, width: 1440, height: 875 },
      }),
    },
  }

  // The real win32 module, minus its one host probe (serviceDiagnostics shells
  // out to schtasks), so the Windows-only update-install branch runs here.
  const windowsPlatform = { ...win32, serviceDiagnostics: () => {} }
  const load = new Function('require', 'module', 'exports', '__dirname', '__filename', src)
  load(
    (id: string) => {
      if (id === 'electron') return electron
      if (id === './platform/index.cjs' && options.windows) return windowsPlatform
      if (id === 'electron-updater') {
        return { MacUpdater: FakeUpdater, NsisUpdater: FakeUpdater, AppImageUpdater: FakeUpdater }
      }
      if (id === 'child_process') return { ...realRequire('child_process') as object, execFile: options.execFile }
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
    // Answers whether the main window's frame-navigation handler prevented it.
    frameNavigate: (event) => {
      const fn = wcListeners[wcListeners.length - 1]?.get('will-frame-navigate')
      if (!fn) throw new Error('no will-frame-navigate listener')
      let prevented = false
      fn({ ...event, preventDefault: () => { prevented = true } })
      return prevented
    },
    nativeEmit,
    sent, externals, updater, home,
    log: () => {
      try { return readFileSync(join(home, 'logs', 'app.log'), 'utf-8') } catch { return '' }
    },
  }
}

const STOP = ['-m', 'autowright.service', 'stop']
const INSTALL = ['-m', 'autowright.service', 'install']

function writeBackendJson(m: MainStub) {
  writeFileSync(join(m.home, 'backend.json'),
    JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
}

describe('main.cjs audit 2026-10-31 second sweep (§3, §9.4)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    if (savedResourcesPath === undefined) delete nodeProcess.resourcesPath
    else nodeProcess.resourcesPath = savedResourcesPath
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
    if (savedLocalAppData === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = savedLocalAppData
  })

  // ---- P: the staged installer must be readable before the backend stop

  it('Windows: a missing staged installer refuses { error } and stops nothing', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const children: string[][] = []
    const m = loadMain({ windows: true, execFile: (_py, args, _o, cb) => { children.push(args); cb(null, 'stopped', '') } })
    writeBackendJson(m)
    m.updater.installerPath = join(m.home, 'quarantined', 'Autowright-Setup-9.9.9.exe')
    const answer = await m.invoke('update-install') as { error?: string }
    expect(answer.error).toContain('ENOENT')
    expect(children).toEqual([])
    expect(m.updater.installs).toBe(0)
    expect(m.log()).toContain('update: the staged installer can\'t be read')
  })

  it('Windows: a readable staged installer stops the backend, then hands off', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const children: string[][] = []
    const m = loadMain({ windows: true, execFile: (_py, args, _o, cb) => { children.push(args); cb(null, 'stopped', '') } })
    writeBackendJson(m)
    m.updater.installerPath = join(m.home, 'Autowright-Setup-9.9.9.exe')
    writeFileSync(m.updater.installerPath, 'MZ')
    let answer: unknown = null
    void (m.invoke('update-install') as Promise<unknown>).then((r) => { answer = r })
    await vi.advanceTimersByTimeAsync(1500)
    expect(answer).toEqual({ ok: true })
    expect(children).toEqual([STOP])
    expect(m.updater.installs).toBe(1)
  })

  // ---- W: only the stop → quitAndInstall span refuses quits

  // A Windows install held inside its backend stop: the stop child answers
  // only when the test releases it.
  function holdHandOff() {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const children: string[][] = []
    let release: (() => void) | null = null
    const m = loadMain({
      windows: true,
      execFile: (_py, args, _o, cb) => {
        children.push(args)
        release = () => cb(null, 'stopped', '')
      },
    })
    writeBackendJson(m)
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.installerPath = join(m.home, 'Autowright-Setup-9.9.9.exe')
    writeFileSync(m.updater.installerPath, 'MZ')
    const install = m.invoke('update-install') as Promise<unknown>
    return { m, children, install, release: () => release?.() }
  }

  it('quit-all during the install\'s backend stop answers the in-progress error and stops nothing more', async () => {
    const held = holdHandOff()
    await vi.waitFor(() => expect(held.children).toEqual([STOP]))
    expect(await held.m.invoke('quit-all', { force: true }))
      .toEqual({ error: 'another operation is in progress' })
    expect(held.children).toEqual([STOP])
    held.release()
    expect(await held.install).toEqual({ ok: true })
  })

  it('an OS quit during the install\'s backend stop is ignored with a log line', async () => {
    const held = holdHandOff()
    await vi.waitFor(() => expect(held.children).toEqual([STOP]))
    held.m.openLoadedWindow()
    expect(held.m.emit('before-quit')).toBe(true)
    expect(held.m.sent.filter(([channel]) => channel === 'quit-requested')).toEqual([])
    expect(held.children).toEqual([STOP])
    expect(held.m.log()).toContain('quit: ignored, an update install is in flight')
    held.release()
    expect(await held.install).toEqual({ ok: true })
  })

  it('macOS: quit-all during the staging wait is an ordinary quit, and the install then stands down', async () => {
    if (platMod.UPDATER !== 'mac') return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const children: string[][] = []
    const m = loadMain({ execFile: (_py, args, _o, cb) => { children.push(args); cb(null, 'stopped', '') } })
    writeBackendJson(m)
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.check = { updateInfo: { version: '0.14.0' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    m.updater.nativeSilent = true
    let answer: unknown = null
    void (m.invoke('update-install') as Promise<unknown>).then((r) => { answer = r })
    await vi.advanceTimersByTimeAsync(0)
    expect(m.updater.nativeChecks).toBe(1)
    expect(answer).toBeNull()
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    expect(children).toEqual([STOP])
    // Squirrel finishes staging after the quit began: nothing is handed off.
    m.updater.squirrelDownloadedUpdate = true
    m.nativeEmit('update-downloaded')
    await vi.advanceTimersByTimeAsync(0)
    expect(answer).toEqual({ error: 'another operation is in progress' })
    expect(m.updater.installs).toBe(0)
  })

  it('macOS: after the 10-minute give-up a new download is accepted', async () => {
    if (platMod.UPDATER !== 'mac') return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain({ execFile: (_py, _args, _o, cb) => { cb(null, 'stopped', '') } })
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.check = { updateInfo: { version: '0.14.0' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    m.updater.nativeSilent = true
    let answer: unknown = null
    void (m.invoke('update-install') as Promise<unknown>).then((r) => { answer = r })
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(answer).toEqual({ error: 'the updater stopped responding' })
    m.updater.check = { updateInfo: { version: '0.14.1' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    expect(m.updater.downloads).toBe(2)
  })

  // ---- AJ (M3): the external-URL policy covers frames

  it('a result-iframe navigation to a remote site is cancelled and routed to the browser', () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain({ execFile: (_py, _args, _o, cb) => { cb(null, '', '') } })
    m.openLoadedWindow()
    expect(m.frameNavigate({ isMainFrame: false, url: 'https://evil.example/' })).toBe(true)
    expect(m.externals).toEqual(['https://evil.example/'])
    // A scheme the policy does not open is cancelled and dropped.
    expect(m.frameNavigate({ isMainFrame: false, url: 'file:///etc/passwd' })).toBe(true)
    expect(m.externals).toEqual(['https://evil.example/'])
    // The frame's own document, and the top frame (will-navigate's job), pass.
    expect(m.frameNavigate({ isMainFrame: false, url: 'about:srcdoc' })).toBe(false)
    expect(m.frameNavigate({ isMainFrame: false, url: 'about:blank' })).toBe(false)
    expect(m.frameNavigate({ isMainFrame: true, url: 'https://evil.example/' })).toBe(false)
    expect(m.externals).toEqual(['https://evil.example/'])
  })

  // ---- AJ (M4): an install dropped by a quit leaves the status alone

  it('an install dropped because a quit-all latched never shows the raw "quitting"', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const platform = realRequire(join(PLATFORM_DIR, 'index.cjs')) as { bundledPythonPath: (r: string) => string }
    const bundled = platform.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    let releaseHealth = () => {}
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/health')) {
        return new Promise((resolve) => {
          releaseHealth = () => resolve({ ok: false, status: 503 } as Response)
        })
      }
      return Promise.reject(new Error('offline in tests'))
    })
    const children: string[][] = []
    const m = loadMain({ ready: true, execFile: (_py, args, _o, cb) => { children.push(args); cb(null, 'stopped', '') } })
    writeBackendJson(m)
    await vi.advanceTimersByTimeAsync(0)
    expect(await m.invoke('quit-all', {})).toEqual({ ok: true })
    releaseHealth()
    await vi.advanceTimersByTimeAsync(0)
    const status = await m.invoke('backend-status') as { state: string, detail: string }
    expect(status).toEqual({ state: 'failed', detail: 'Autowright is quitting.' })
    expect(m.log()).toContain('ensure-backend: install dropped')
    expect(children).not.toContainEqual(INSTALL)
  })

  // ---- AJ (M5): the version sync is single-flight and re-probes first

  it('Windows: a refused install\'s ensure-backend joins the running version sync, which skips an already-current backend', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = win32.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '')
    nodeProcess.resourcesPath = resources
    let running = '0.0.-1'
    let live = true
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/health')) {
        return Promise.resolve({ ok: true, json: async () => ({ app: 'Autowright', version: running }) } as Response)
      }
      if (url.includes('/executions')) {
        const executions = live ? [{ id: 'e1' }] : []
        return Promise.resolve({ ok: true, json: async () => ({ executions, total: executions.length }) } as Response)
      }
      return Promise.reject(new Error('offline in tests'))
    })
    const children: string[][] = []
    const m = loadMain({
      ready: true,
      windows: true,
      execFile: (_py, args, _o, cb) => {
        children.push(args)
        // A failed stop refuses the install, whose resume re-runs ensure-backend.
        if (args[2] === 'stop') cb(Object.assign(new Error('Command failed'), { code: 1 }), 'stop failed', '')
        else cb(null, 'ok', '')
      },
    })
    writeBackendJson(m)
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    await vi.advanceTimersByTimeAsync(0)
    expect(m.log().match(/restarting service once live executions finish/g)).toHaveLength(1)
    // Between drain polls: nothing live, a staged installer, a refused stop.
    live = false
    m.updater.installerPath = join(m.home, 'Autowright-Setup-9.9.9.exe')
    writeFileSync(m.updater.installerPath, 'MZ')
    expect(await m.invoke('update-install'))
      .toEqual({ error: 'couldn\'t stop the background service — try again' })
    await vi.advanceTimersByTimeAsync(0)
    // The resume's ensure-backend saw the same outdated backend: one loop.
    expect(m.log().match(/restarting service once live executions finish/g)).toHaveLength(1)
    // The backend reached the app's version meanwhile: the drain ends and the
    // re-probe skips the install.
    running = '0.0.0'
    await vi.advanceTimersByTimeAsync(30_000)
    expect(m.log()).toContain('version-sync: the backend is already current')
    expect(children).not.toContainEqual(INSTALL)
  })
})

describe('§3 Windows CLI shim under %LOCALAPPDATA% (2026-10-31 second sweep)', () => {
  afterEach(() => {
    if (savedLocalAppData === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = savedLocalAppData
  })

  it('writes a per-user interpreter path relative to the variable', () => {
    process.env.LOCALAPPDATA = 'C:\\Users\\José\\AppData\\Local'
    expect(win32.shimText('C:\\Users\\José\\AppData\\Local\\Programs\\Autowright\\resources\\python\\python.exe'))
      .toBe('@echo off\r\nrem autowright CLI shim\r\n'
        + '"%LOCALAPPDATA%\\Programs\\Autowright\\resources\\python\\python.exe" -m autowright.cli %*\r\n')
    // Case-insensitive, and a trailing separator on the variable is ignored.
    process.env.LOCALAPPDATA = 'c:\\users\\josé\\appdata\\local\\'
    expect(win32.shimText('C:\\Users\\José\\AppData\\Local\\Programs\\py.exe'))
      .toContain('"%LOCALAPPDATA%\\Programs\\py.exe" -m autowright.cli %*')
  })

  it('keeps a path outside it literal, and only at a separator', () => {
    process.env.LOCALAPPDATA = 'C:\\Users\\José\\AppData\\Local'
    expect(win32.shimText('C:\\Program Files\\Autowright\\resources\\python\\python.exe'))
      .toContain('"C:\\Program Files\\Autowright\\resources\\python\\python.exe" -m autowright.cli %*')
    expect(win32.shimText('C:\\Users\\José\\AppData\\LocalLow\\py.exe'))
      .toContain('"C:\\Users\\José\\AppData\\LocalLow\\py.exe" -m autowright.cli %*')
  })

  it('doubles a % in the rest, never the variable reference', () => {
    process.env.LOCALAPPDATA = 'C:\\Users\\100%dev\\AppData\\Local'
    expect(win32.shimText('C:\\Users\\100%dev\\AppData\\Local\\Programs\\50%\\python.exe'))
      .toContain('"%LOCALAPPDATA%\\Programs\\50%%\\python.exe" -m autowright.cli %*')
  })
})
