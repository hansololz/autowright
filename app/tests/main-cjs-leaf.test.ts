// §2 CLI-leaf invariant guard over the Electron main layer — main.cjs plus
// the §2 platform modules under electron/platform/ (spec §15). The app
// registers the backend via `python -m autowright.service` and must never
// execute the CLI; §3 shim writes only ever target the user-local location —
// no admin prompt exists, and nothing ever writes to the legacy
// /usr/local/bin (the pre-08-15 bug was a silent best-effort write there).
// main.cjs has no importable module structure, so the guard reads the source.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, truncateSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, sep } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

const ELECTRON_DIR = join(__dirname, '..', 'electron')
const src = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
// The platform modules are part of the same trust surface: every guard that
// scans main.cjs scans them too (union), so a §2 extraction can't smuggle a
// forbidden call out of the guard's sight.
const PLATFORM_DIR = join(ELECTRON_DIR, 'platform')
const platFiles = readdirSync(PLATFORM_DIR).filter((n) => n.endsWith('.cjs'))
const platSrc = platFiles.map((n) => readFileSync(join(PLATFORM_DIR, n), 'utf-8')).join('\n')
const union = `${src}\n${platSrc}`

describe('main.cjs CLI-leaf invariant (§2)', () => {
  it('registers the backend via -m autowright.service', () => {
    expect(src).toContain("'-m', 'autowright.service', 'install'")
  })

  it('quit-all and reset drive -m autowright.service, nothing else', () => {
    // Both explicit service commands share one runner (§3: same interpreter
    // resolution + install interlock), so the verbs are pinned here rather
    // than one literal argv per flow.
    expect(src).toContain("execFile(py, ['-m', 'autowright.service', verb]")
    const verbs = [...src.matchAll(/runServiceVerb\('([a-z]+)'/g)].map((m) => m[1])
    expect(new Set(verbs)).toEqual(new Set(['stop']))
    expect(verbs).toHaveLength(2)
    expect(src).toContain("runServiceVerb('stop', 'quit-all')")
    expect(src).toContain("runServiceVerb('stop', 'reset')")
  })

  it('quit-all gates on live executions unless the renderer forces it (§3)', () => {
    // The §4.9 force-confirm modal's retry passes { force: true }, the one way
    // past the gate — and the gate still runs before the stop for every
    // unforced call.
    expect(src).toContain("ipcMain.handle('quit-all', async (_e, opts) => {")
    expect(src).toMatch(
      /if \(!opts\?\.force && await executionsLive\(\)\) return \{ busy: true \}[\s\S]{0,200}runServiceVerb\('stop', 'quit-all'\)/)
  })

  it('never executes the CLI — autowright.cli appears only inside the shim file text', () => {
    // main.cjs itself never mentions the CLI; the platform modules mention it
    // exactly once each, as the shim file's contents (the shimText run line,
    // §3: POSIX or Windows .cmd form) — never a child-process invocation by
    // the app.
    expect(src).not.toContain('autowright.cli')
    const shimForms = [
      'exec "${python}" -m autowright.cli "$@"', // POSIX shim (darwin/fallback)
      '"${python}" -m autowright.cli %*', // Windows .cmd shim (win32)
    ]
    const lines = union.split('\n').filter((l) => l.includes('autowright.cli'))
    expect(lines.length).toBeGreaterThanOrEqual(1)
    for (const line of lines) {
      expect(shimForms.some((form) => line.includes(form))).toBe(true)
      expect(line).not.toMatch(/execFile|spawn/)
    }
  })

  it('spawns only the service managers, the login shell, the registry, and the backend python', () => {
    // Every child-process call site across main.cjs + platform modules:
    // execFile('launchctl'|'systemctl'|'reg'|shell|py, …). `shell` is the §3
    // login-shell PATH probe (printf $PATH, nothing else); 'launchctl' /
    // 'systemctl' are the §2 serviceDiagnostics captures; 'reg' is the §4.9
    // Windows legacy login-item sweep. The pre-0.6.1 mac
    // update flow's hdiutil/ditto helpers are gone — electron-updater
    // downloads the zip directly (§3). `spawn` is not used at all (the word
    // may appear in comments only).
    const calls = [...union.matchAll(/(?<![.\w])(?:execFile|spawn|exec)\(\s*([^,)]+)/g)].map((m) => m[1].trim())
    for (const first of calls) {
      expect(["'launchctl'", "'systemctl'", "'reg'", 'shell', 'py']).toContain(first)
    }
    expect(calls.length).toBeGreaterThanOrEqual(3)
    expect(union).not.toContain("'hdiutil'")
    expect(union).not.toContain("'ditto'")
    // …and every python call site runs the service module, nothing else.
    const pyCalls = [...union.matchAll(/execFile\(\s*py\s*,\s*\[([^\]]*)\]/g)].map((m) => m[1])
    expect(pyCalls.length).toBeGreaterThanOrEqual(1)
    for (const args of pyCalls) {
      expect(args).toContain("'-m', 'autowright.service'")
    }
  })

  it('shim writes are user-local only — no admin prompt, no /usr/local/bin write (§3)', () => {
    // No osascript admin flow exists at all.
    expect(union).not.toContain('with administrator privileges')
    expect(union).not.toContain("'osascript'")
    // The silent-failure regression: no direct write targeting the legacy
    // shim location — cli-install writes shimPaths()[0] (user-local), and
    // the heal only rewrites an already-ours file.
    expect(union).not.toMatch(/writeFileSync\(\s*'\/usr\/local\/bin/)
    expect(union).not.toMatch(/writeFileSync\(\s*SYSTEM_SHIM/)
    expect(src).toMatch(/writeFileSync\(shim, shimText\(python\)/)
  })

  it('electron-updater is required lazily, behind the feed gate (§3)', () => {
    // The mac bundle ships only electron-updater's runtime closure, and
    // constructing an updater must never happen at module load (requiring
    // main.cjs must not turn into network machinery). The one require sits
    // inside the constructor helper.
    const hits = union.match(/require\('electron-updater'\)/g) ?? []
    expect(hits).toHaveLength(1)
    expect(src).toMatch(/function genericUpdater\(\) \{[\s\S]{0,200}require\('electron-updater'\)/)
    // …and every path that can reach the helper refuses first without a feed
    // (fetchUpdateState + both handlers open on the UPDATE_FEED gate), so on a
    // feedless platform nothing can reach the require at all.
    const gates = src.match(/if \(!UPDATE_FEED\) return \{ (state: 'error', )?error: NO_UPDATES_ERROR \}/g) ?? []
    expect(gates).toHaveLength(3)
    // The marker decides the class — never a process.platform sniff in
    // main.cjs — and all three OSes go through the same electron-updater map.
    expect(src).toMatch(/\{ mac: MacUpdater, nsis: NsisUpdater, appimage: AppImageUpdater \}\[plat\.UPDATER\]/)
    expect(src).not.toContain("process.platform === 'win32'")
    expect(src).not.toContain("process.platform === 'linux'")
    // The darwin Squirrel hand-off tail is the one per-OS fork, keyed on the
    // marker, and it runs strictly inside the download handler's flow.
    expect(src).toMatch(/if \(plat\.UPDATER === 'mac'\) \{\n\s*const error = await stageWithSquirrel\(updater\)/)
  })

  it('no auto-install: cli-install is reachable only via its IPC handler (§3)', () => {
    // Exactly two mentions of cliInstall: the definition and the IPC handler.
    const hits = src.match(/cliInstall(?!l)/g) ?? []
    expect(hits).toHaveLength(2)
    expect(src).toContain("ipcMain.handle('cli-install', () => cliInstall())")
  })

  it('the /health probe proves the backend is ours, not merely that something answered (§3)', () => {
    // The hole this closes: backend.json can name a stale port some other
    // local server now owns. Counting its 200 as our backend marked
    // ensure-backend 'ok', skipped the service install, and waited forever.
    // The probe now reads the body: our app name, and a version that isn't
    // empty. Anything else (non-JSON throws into the catch) answers null, so
    // ensureBackend falls through to the install branch.
    expect(src).toMatch(/async function backendVersion\(\)[\s\S]{0,400}body\?\.app !== 'Autowright'/)
    expect(src).toMatch(/backendVersion\(\)[\s\S]{0,500}String\(body\.version \?\? ''\) \|\| null/)
    // …and every caller still reads "healthy" off exactly that answer, so the
    // stricter probe can't be routed around.
    expect(src).toContain('return (await backendVersion()) !== null')
    expect(src).toMatch(/const running = await backendVersion\(\)\n\s*if \(running !== null\) \{/)
  })

  it('the ready chain survives a throwing tray (§9/§13)', () => {
    // createTray() used to run first with no try/catch and no .catch on the
    // chain: a bad tray image in a broken package silently killed the §6
    // app-start triggers, the §13 tray poll, the §4.9 settings reconcile and
    // the macOS activate handler with it.
    const ready = src.slice(src.indexOf('app.whenReady()'))
    // The reopen handler is registered before anything that can throw.
    expect(ready.indexOf("app.on('activate'")).toBeLessThan(ready.indexOf('createTray()'))
    expect(ready).toMatch(/if \(caps\.trayPanel\) \{\n\s*try \{\n\s*createTray\(\)\n\s*\} catch \(err\) \{[\s\S]{0,120}appLog\(/)
    // …and the polls/reconcile sit after the catch, so they run either way.
    for (const call of ['void notifyAppStarted()', 'void refreshTrayAlert()', 'void syncShellSettings()']) {
      expect(ready.indexOf(call)).toBeGreaterThan(ready.indexOf('createTray()'))
    }
    // Last resort: nothing in the chain may reject into the void.
    expect(ready).toMatch(/\}\)\.catch\(\(err\) => appLog\(/)
  })

  it('the reopen handler is registered before every step that can throw (§9)', () => {
    const ready = src.slice(src.indexOf('app.whenReady()'))
    const activate = ready.indexOf("app.on('activate'")
    for (const step of ['plat.setDockIcon(', 'plat.applyDesktopEntry(',
      'Menu.setApplicationMenu(null)', 'createWindow()']) {
      expect(activate).toBeLessThan(ready.indexOf(step))
    }
    // …and each OS-side step carries its own try/catch, so one that fails on
    // a given host never takes the ones after it with it.
    const guarded = ready.match(/try \{\n\s*if \(!?caps\.\w+\)[\s\S]{0,220}?\} catch \(err\) \{\n\s*appLog\(/g) ?? []
    expect(guarded).toHaveLength(3)
  })

  it('syncShellSettings tells a down backend apart from a shell-side throw', () => {
    // One catch used to swallow both, so a throw out of applyShellSettings
    // (tray create, login item, update timer) read as "backend down" and left
    // no trace anywhere.
    const fn = src.slice(src.indexOf('async function syncShellSettings()'))
      .slice(0, src.slice(src.indexOf('async function syncShellSettings()')).indexOf('\n}\n') + 3)
    expect(fn).toMatch(/catch \{ \/\* backend down/)
    expect(fn).toMatch(/try \{\n\s*applyShellSettings\(settings, \{ trusted: true \}\)\n\s*\} catch \(err\) \{\n\s*appLog\(/)
  })

  it('every service child is bounded, and so is every wait on one (§3)', () => {
    // launchctl/sc can block indefinitely on a wedged domain; an unbounded
    // execFile left quit-all and reset waiting for the life of the app.
    const bounds = src.match(/timeout: SERVICE_CHILD_TIMEOUT_MS/g) ?? []
    expect(bounds).toHaveLength(1)
    expect(src).toContain('const SERVICE_CHILD_TIMEOUT_MS = 120_000')
    // Both spawn sites carry the same options object…
    const spawns = src.match(/'autowright\.service', (?:'install'|verb)\], SERVICE_CHILD_OPTIONS/g) ?? []
    expect(spawns).toHaveLength(2)
    // …and the wait for an in-flight install child is raced against its own
    // deadline rather than awaited outright.
    expect(src).toMatch(/await Promise\.race\(\[\n\s*serviceInstallDone\.then\(\(\) => null\)/)
    expect(src).not.toContain('await serviceInstallDone')
  })

  it('reset logs before it erases the logs root (§3)', () => {
    const handler = src.slice(
      src.indexOf("ipcMain.handle('reset-all'"), src.indexOf('app.whenReady()'))
    // appLog re-creates the logs dir, so a line written after the deletion
    // left a fresh logs root behind on every single reset.
    expect(handler).toMatch(
      /appLog\('reset: erasing data, then quitting'\)[\s\S]{0,400}await deleteAllData\(/)
    expect(handler.slice(handler.indexOf('await deleteAllData('))).not.toContain('appLog(')
  })

  it('cli-uninstall deletes only marker-carrying shims, via its IPC handler (§3)', () => {
    expect(src).toContain("ipcMain.handle('cli-uninstall', () => cliUninstall())")
    // The marker gate sits before the unlink — foreign files are never touched.
    expect(src).toMatch(/if \(!text\.includes\(SHIM_MARKER\)\) return \{ ok: true \}[\s\S]{0,160}unlinkSync/)
  })
})

// ---- IPC argument validation ----------------------------------------------
// main.cjs exports nothing, so the handlers are reached by evaluating the same
// source against a stub `electron` module: `ipcMain.handle` collects them and
// the `shell`/`BrowserWindow` stubs record what a call actually did. Real code,
// real handlers — only the Electron surface underneath is a double.

// The §3 electron-updater double (darwin MacUpdater / win32 NsisUpdater /
// linux AppImageUpdater — one fake serves as all three): electron-updater is
// required lazily by main.cjs, so the stub `require` hands it this fake under
// every class name — the same "patch the layer underneath, run the real
// handler" rule as the electron stub itself. Canned answers live on the
// record so a test can arm them before the handler ever constructs the
// updater. Like the real MacUpdater, downloadUpdate flips
// squirrelDownloadedUpdate, so the darwin hand-off tail settles immediately
// instead of waiting on the stub autoUpdater's silence.
interface UpdaterRecord {
  options: { provider?: string, url?: string } | null
  autoDownload: boolean | null
  autoInstallOnAppQuit: boolean | null
  checks: number
  downloads: number
  installs: number
  check: unknown
  checkError: Error | null
  downloadError: Error | null
  // §3 install: what quitAndInstall answers (the NSIS/AppImage classes return
  // false when they refuse), the error it dispatches on its way out, and the
  // throw a broken installer produces instead.
  installResult: unknown
  installError: Error | null
  installThrows: Error | null
  listeners: Map<string, (arg: unknown) => void>
}

// Per-window record: show/focus/load/destroy counts plus ways to fire the
// webContents and window events at the real handlers main.cjs registered
// (§9 never-paint guard, §13 panel lifetime).
interface WinRecord {
  shows: number
  focuses: number
  loads: number
  destroys: number
  // §13: restore() calls, and every setPosition the panel placement made.
  restores: number
  positions: [number, number][]
  fire: (event: string, ...args: unknown[]) => void
  close: () => void
  // §13: put the window in the state the OS would (minimized), and drive the
  // §9.4 will-navigate handler with a real event object.
  minimize: () => void
  navigate: (url: string) => boolean
}

interface MainStub {
  invoke: (channel: string, ...args: unknown[]) => unknown
  // Fire an app-level event main.cjs subscribed to (window-all-closed, …).
  emit: (event: string) => void
  opened: string[]
  // §9.4 external hand-offs (shell.openExternal), in call order.
  externals: string[]
  revealed: string[]
  windows: unknown[]
  wins: WinRecord[]
  trays: unknown[]
  loginItem: boolean[]
  aumids: string[]
  sent: [string, unknown][]
  updater: UpdaterRecord
  quits: number
  home: string
  // §5.1 native dialogs: what main.cjs asked each one for, and the canned
  // answer it gets back (a test arms it before invoking).
  dialogs: [string, Record<string, unknown>][]
  dialogAnswer: { canceled: boolean, filePaths: string[], filePath: string | null }
  // §13: fire the tray's own click handler, the only way to the panel.
  clickTray: () => void
  // §9 watchdog / renderer-death reporting: every dialog.showErrorBox the
  // shell put up, and the app.log lines behind them.
  errors: [string, string][]
  log: () => string
  // §3 reset: the exit codes app.exit was called with.
  exits: number[]
}

const realRequire = createRequire(join(ELECTRON_DIR, 'main.cjs'))
const savedHome = process.env.AUTOWRIGHT_HOME

// §3 service-child double: main.cjs's own `require` hands this to it instead
// of child_process.execFile, so a test can hang a child or kill it on its
// deadline without ever spawning anything.
type ServiceChildOptions = { windowsHide: boolean, timeout: number, maxBuffer: number }
type ServiceChildError = Error & { killed?: boolean, signal?: string }
type ServiceChildStub = (
  py: string, args: string[], options: ServiceChildOptions,
  cb: (err: ServiceChildError | null, stdout: string, stderr: string) => void,
) => void

interface LoadOptions {
  // Let the ready chain actually run. The default stub never resolves
  // whenReady, so every other test drives main.cjs through its IPC handlers.
  ready?: boolean
  // §9: make the platform's dock-icon step throw, to prove the ready chain
  // survives an OS-side step that fails.
  dockIconThrows?: boolean
  // §9.4: make every external hand-off the OS is asked for reject.
  openExternalRejects?: boolean
  execFile?: ServiceChildStub
  // Patch individual `fs` functions main.cjs sees (the §9.3 log-rotation race
  // is otherwise unreachable from a single-threaded test).
  fs?: Record<string, unknown>
}

function loadMain(options: LoadOptions = {}): MainStub {
  const handlers = new Map<string, (e: unknown, ...args: unknown[]) => unknown>()
  const appEvents = new Map<string, () => void>()
  const opened: string[] = []
  const externals: string[] = []
  const revealed: string[] = []
  const windows: unknown[] = []
  const trays: unknown[] = []
  const loginItem: boolean[] = []
  const aumids: string[] = []
  const sent: [string, unknown][] = []
  const errors: [string, string][] = []
  const dialogs: [string, Record<string, unknown>][] = []
  const dialogAnswer: { canceled: boolean, filePaths: string[], filePath: string | null } = {
    canceled: true, filePaths: [], filePath: null,
  }
  const trayEvents = new Map<string, () => void>()
  let quits = 0
  const exits: number[] = []
  const home = mkdtempSync(join(tmpdir(), 'aw-main-'))
  process.env.AUTOWRIGHT_HOME = home

  const updater: UpdaterRecord = {
    options: null, autoDownload: null, autoInstallOnAppQuit: null,
    checks: 0, downloads: 0, installs: 0,
    check: { updateInfo: { version: '9.9.9' } },
    checkError: null, downloadError: null, listeners: new Map(),
    installResult: undefined, installError: null, installThrows: null,
  }

  class FakeUpdater {
    autoDownload = true
    autoInstallOnAppQuit = true
    squirrelDownloadedUpdate = false
    logger: unknown = null

    constructor(options: { provider?: string, url?: string }) { updater.options = options }
    on(event: string, fn: (arg: unknown) => void) { updater.listeners.set(event, fn) }

    async checkForUpdates() {
      // Read the flags as the handler left them: an autoDownload that is
      // still true would mean a check downloads (§3 forbids it).
      updater.autoDownload = this.autoDownload
      updater.autoInstallOnAppQuit = this.autoInstallOnAppQuit
      updater.checks += 1
      if (updater.checkError) throw updater.checkError
      return updater.check
    }

    async downloadUpdate() {
      updater.downloads += 1
      if (updater.downloadError) throw updater.downloadError
      // The real MacUpdater sets this once Squirrel staged the bundle; the
      // fake stages "instantly" so main.cjs's stageWithSquirrel resolves.
      this.squirrelDownloadedUpdate = true
      return ['installer.exe']
    }

    quitAndInstall() {
      updater.installs += 1
      // The real BaseUpdater dispatches the failure on its error stream and
      // only then answers false.
      if (updater.installError) updater.listeners.get('error')?.(updater.installError)
      if (updater.installThrows) throw updater.installThrows
      return updater.installResult
    }
  }

  const wins: WinRecord[] = []

  class FakeWindow {
    wcListeners = new Map<string, (...a: unknown[]) => void>()
    listeners = new Map<string, (...a: unknown[]) => void>()
    destroyed = false
    record: WinRecord = {
      shows: 0, focuses: 0, loads: 0, destroys: 0, restores: 0, positions: [],
      fire: (event, ...args) => { this.wcListeners.get(event)?.({}, ...args) },
      close: () => { this.listeners.get('closed')?.() },
      minimize: () => { this.minimized = true },
      navigate: (url) => {
        let prevented = false
        this.wcListeners.get('will-navigate')?.(
          { preventDefault: () => { prevented = true } }, url)
        return prevented
      },
    }

    webContents = {
      on: (event: string, fn: (...a: unknown[]) => void) => { this.wcListeners.set(event, fn) },
      once: (event: string, fn: (...a: unknown[]) => void) => { this.wcListeners.set(event, fn) },
      send: (channel: string, payload: unknown) => { sent.push([channel, payload]) },
      setWindowOpenHandler() {},
      isLoading: () => false, getURL: () => '',
    }

    constructor(opts: unknown) { windows.push(opts); wins.push(this.record) }
    loadFile() { this.record.loads += 1 } loadURL() { this.record.loads += 1 }
    on(event: string, fn: (...a: unknown[]) => void) { this.listeners.set(event, fn) }
    minimized = false
    show() { this.record.shows += 1 } focus() { this.record.focuses += 1 } hide() {}
    isMinimized() { return this.minimized }
    restore() { this.minimized = false; this.record.restores += 1 }
    setSize() {}
    setPosition(x: number, y: number) { this.record.positions.push([x, y]) }
    isVisible() { return false }
    setVisibleOnAllWorkspaces() {}
    destroy() { this.destroyed = true; this.record.destroys += 1 }
    isDestroyed() { return this.destroyed }
  }

  const electron = {
    app: {
      setPath() {},
      getPath: () => home,
      getVersion: () => '0.0.0',
      commandLine: { appendSwitch() {} },
      requestSingleInstanceLock: () => true,
      on(event: string, fn: () => void) { appEvents.set(event, fn) },
      isReady: () => false,
      quit() { quits += 1 },
      // §3 reset step 6: the app quits and stays quit.
      exit(code: number) { exits.push(code) },
      whenReady: () => (options.ready ? Promise.resolve() : new Promise(() => {})),
      // §4.9 dev-harness guard: registration happens only from a packaged
      // run, so the leaf harness models one.
      isPackaged: true,
      getLoginItemSettings: () => ({ openAtLogin: false }),
      setAppUserModelId: (id: string) => { aumids.push(id) },
      setLoginItemSettings(s: { openAtLogin: boolean }) { loginItem.push(s.openAtLogin) },
      dock: {
        setIcon() {
          if (options.dockIconThrows) throw new Error('no dock on this host')
        },
      },
    },
    autoUpdater: { on() {}, removeListener() {}, setFeedURL() {}, checkForUpdates() {}, quitAndInstall() {} },
    BrowserWindow: FakeWindow,
    Menu: { buildFromTemplate: () => ({ popup() {} }) },
    Tray: class {
      constructor(icon: unknown) { trays.push(icon) }
      setToolTip() {}
      on(event: string, fn: () => void) { trayEvents.set(event, fn) }
      setImage() {} destroy() {}
    },
    dialog: {
      showErrorBox: (title: string, body: string) => { errors.push([title, body]) },
      showOpenDialog: async (_w: unknown, opts: Record<string, unknown>) => {
        dialogs.push(['open', opts])
        return { canceled: dialogAnswer.canceled, filePaths: dialogAnswer.filePaths }
      },
      showSaveDialog: async (_w: unknown, opts: Record<string, unknown>) => {
        dialogs.push(['save', opts])
        return { canceled: dialogAnswer.canceled, filePath: dialogAnswer.filePath }
      },
    },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    // §3 reset step 5: the Chromium profile is cleared, never deleted.
    session: {
      defaultSession: {
        clearStorageData: async () => {},
        clearCache: async () => {},
      },
    },
    ipcMain: { handle: (name: string, fn: never) => { handlers.set(name, fn) } },
    shell: {
      openPath: (p: string) => { opened.push(p) },
      showItemInFolder: (p: string) => { revealed.push(p) },
      // §9.4: the real one answers a promise, and the OS can refuse.
      openExternal: (url: string) => {
        externals.push(url)
        return options.openExternalRejects
          ? Promise.reject(new Error('no application knows how to open this'))
          : Promise.resolve()
      },
    },
    // §13 panel placement: the platform module reads a cursor point and the
    // display under it, so one fixed screen stands in for the real one.
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
      // One fake under all three class names — main.cjs picks by the §2 marker.
      if (id === 'electron-updater') {
        return { MacUpdater: FakeUpdater, NsisUpdater: FakeUpdater, AppImageUpdater: FakeUpdater }
      }
      if (id === 'fs' && options.fs) {
        return { ...realRequire('fs') as object, ...options.fs }
      }
      if (id === 'child_process' && options.execFile) {
        return { ...realRequire('child_process') as object, execFile: options.execFile }
      }
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
    emit: (event) => {
      const fn = appEvents.get(event)
      if (!fn) throw new Error(`no app listener for ${event}`)
      fn()
    },
    clickTray: () => {
      const fn = trayEvents.get('click')
      if (!fn) throw new Error('no tray click handler')
      fn()
    },
    opened, externals, revealed, windows, wins, trays, loginItem, aumids, sent, updater,
    home, errors, exits, dialogs, dialogAnswer,
    // AUTOWRIGHT_HOME points app.log at this test's own home (§15).
    log: () => {
      try { return readFileSync(join(home, 'logs', 'app.log'), 'utf-8') } catch { return '' }
    },
    get quits() { return quits },
  }
}

describe('main.cjs IPC argument validation', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('reveal-path shows a path inside the app-support home', async () => {
    const m = loadMain()
    await m.invoke('reveal-path', join(m.home, 'automations', 'demo'))
    expect(m.revealed).toEqual([join(m.home, 'automations', 'demo')])
  })

  it('reveal-path ignores a path outside the known roots', async () => {
    const m = loadMain()
    await m.invoke('reveal-path', '/etc/passwd')
    await m.invoke('reveal-path', join(m.home, '..', '..', 'etc', 'passwd'))
    // A sibling directory whose name merely starts with the root's name.
    await m.invoke('reveal-path', `${m.home}-elsewhere${sep}x`)
    expect(m.revealed).toEqual([])
    expect(m.opened).toEqual([])
  })

  it('reveal-path ignores a non-string argument instead of throwing', async () => {
    const m = loadMain()
    await expect(m.invoke('reveal-path', 42)).resolves.toBeUndefined()
    await expect(m.invoke('reveal-path', null)).resolves.toBeUndefined()
    await expect(m.invoke('reveal-path', undefined)).resolves.toBeUndefined()
    expect(m.revealed).toEqual([])
    expect(m.opened).toEqual([])
  })

  it('open-app ignores a non-string hash — no window is created', () => {
    const m = loadMain()
    m.invoke('open-app', { hash: '/app' })
    m.invoke('open-app', 7)
    expect(m.windows).toEqual([])
    // …and a real deep link still opens the window.
    m.invoke('open-app', '/app?automation=abc')
    expect(m.windows).toHaveLength(1)
  })

  it('pick-folder only hands the dialog a real default path', async () => {
    const m = loadMain()
    await m.invoke('pick-folder', 42)
    await m.invoke('pick-folder', '')
    await m.invoke('pick-folder', undefined)
    for (const [, opts] of m.dialogs) expect(opts).not.toHaveProperty('defaultPath')
    await m.invoke('pick-folder', join(m.home, 'executions'))
    expect(m.dialogs[3][1].defaultPath).toBe(join(m.home, 'executions'))
  })

  it('save-file refuses a bad name or bad bytes, and never lets the name steer the path', async () => {
    const m = loadMain()
    expect(await m.invoke('save-file', 42, Buffer.from('x'))).toBeNull()
    expect(await m.invoke('save-file', '', Buffer.from('x'))).toBeNull()
    expect(await m.invoke('save-file', 'demo.autowright', 'not bytes')).toBeNull()
    expect(await m.invoke('save-file', 'demo.autowright', { length: 3 })).toBeNull()
    // None of them even reached the native dialog.
    expect(m.dialogs).toEqual([])
    // A name only names a file inside the downloads dir — the `..` walk is
    // collapsed to a basename before it can steer the dialog anywhere.
    m.dialogAnswer.canceled = false
    m.dialogAnswer.filePath = join(m.home, 'out.autowright')
    expect(await m.invoke('save-file', join('..', '..', 'evil.autowright'), Buffer.from('hi')))
      .toBe(join(m.home, 'out.autowright'))
    expect(m.dialogs[0][1].defaultPath).toBe(join(m.home, 'evil.autowright'))
    expect(readFileSync(join(m.home, 'out.autowright'), 'utf-8')).toBe('hi')
  })

  it('apply-settings never moves the §5 data root — only the backend sync does', async () => {
    const m = loadMain()
    const elsewhere = mkdtempSync(join(tmpdir(), 'aw-data-'))
    const target = join(elsewhere, 'e-1')
    try {
      // The renderer claiming the executions dir moved would otherwise let it
      // pick the reveal roots for itself.
      m.invoke('apply-settings', { dataPath: elsewhere })
      await m.invoke('reveal-path', target)
      expect(m.revealed).toEqual([])
      // The backend's own /settings is the one source that may move it: the
      // reveal-path miss refreshes from there, and then the same path passes.
      writeFileSync(join(m.home, 'backend.json'), JSON.stringify({ port: 65000, token: 't' }))
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
        ok: true, json: async () => ({ dataPath: elsewhere }),
      } as Response)
      try {
        await m.invoke('reveal-path', target)
        expect(m.revealed).toEqual([target])
      } finally {
        fetchSpy.mockRestore()
      }
    } finally {
      rmSync(elsewhere, { recursive: true, force: true })
    }
  })

  it('resize-panel ignores a non-numeric height instead of throwing', () => {
    const m = loadMain()
    // No panel window exists in this process, so the observable contract is
    // simply that a bad height never reaches Math.round/setSize as a throw.
    expect(() => m.invoke('resize-panel', 'tall')).not.toThrow()
    expect(() => m.invoke('resize-panel', undefined)).not.toThrow()
    expect(() => m.invoke('resize-panel', 240)).not.toThrow()
  })
})

// ---- §9 never paint an unloaded window -------------------------------------
// The main window is created hidden and shows itself only on the renderer's
// first successful load; a failed main-frame load (dead dev-server URL) stays
// hidden and retries every second. Chromium fires did-finish-load even after
// a failed navigation, so the tests replay that exact sequence.

describe('main.cjs §9 never-paint-blank window guard', () => {
  afterEach(() => {
    vi.useRealTimers()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('created hidden; a failed load never shows and retries after 1 s', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    expect(m.windows).toHaveLength(1)
    expect((m.windows[0] as { show?: boolean }).show).toBe(false)
    const w = m.wins[0]
    expect(w.loads).toBe(1) // createWindow's initial load
    // Dead dev server: did-fail-load, then Chromium's did-finish-load for the
    // same failed navigation — still hidden, and a retry is scheduled.
    w.fire('did-start-loading')
    w.fire('did-fail-load', -102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/', true)
    w.fire('did-finish-load')
    expect(w.shows).toBe(0)
    vi.advanceTimersByTime(1000)
    expect(w.loads).toBe(2)
  })

  it('shows exactly once on the first successful load — subframe failures don\'t count', () => {
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-fail-load', -6, 'ERR_FILE_NOT_FOUND', 'http://x/img.png', false) // subframe
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    expect(w.focuses).toBe(1)
    // A later reload (HMR, navigation) never re-fires the show.
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
  })

  it('a slow renderer is shown anyway once the 15 s watchdog fires', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    // Nothing at all happens: no failure, no finish. Without the watchdog the
    // app would sit here alive with no window for the rest of its life.
    vi.advanceTimersByTime(14_000)
    expect(w.shows).toBe(0)
    vi.advanceTimersByTime(1_000)
    expect(w.shows).toBe(1)
    expect(w.focuses).toBe(1)
    expect(m.errors).toEqual([]) // there was something to paint — no error box
    expect(m.log()).toContain('showing it anyway')
    // …and a late did-finish-load doesn't show it a second time.
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
  })

  it('a successful load disarms the watchdog — no second show, no error box', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    vi.advanceTimersByTime(60_000)
    expect(w.shows).toBe(1)
    expect(m.errors).toEqual([])
  })

  it('nothing to paint after 15 s: the error box names app.log, the retry keeps running', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-fail-load', -6, 'ERR_FILE_NOT_FOUND', 'file:///dist/index.html', true)
    w.fire('did-finish-load')
    vi.advanceTimersByTime(15_000)
    // Never shown blank — the window has nothing in it. The failure is told
    // instead, and it points at the log.
    expect(w.shows).toBe(0)
    expect(m.errors).toHaveLength(1)
    expect(m.errors[0][0]).toBe('Autowright')
    expect(m.errors[0][1]).toContain(join(m.home, 'logs', 'app.log'))
    // The 1 s retry still ran (and would keep running against a real
    // Chromium, which re-fires did-fail-load), so a renderer that arrives late
    // still wins.
    expect(w.loads).toBe(2)
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    // …and the box is a one-shot: the watchdog already fired.
    vi.advanceTimersByTime(60_000)
    expect(m.errors).toHaveLength(1)
  })

  it('a dead renderer reloads once; a second death reports and quits (§9)', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    expect(w.loads).toBe(1)
    // did-finish-load never comes when the renderer process dies — without a
    // handler the window would stay hidden forever with no error at all.
    w.fire('render-process-gone', { reason: 'oom' })
    expect(w.loads).toBe(2)
    expect(m.quits).toBe(0)
    expect(m.errors).toEqual([])
    expect(m.log()).toContain('renderer process gone (oom)')
    // A second death has nothing left to try: say so and quit rather than
    // staying resident and invisible.
    w.fire('render-process-gone', { reason: 'crashed' })
    expect(w.loads).toBe(2)
    expect(m.errors).toHaveLength(1)
    expect(m.errors[0][1]).toContain(join(m.home, 'logs', 'app.log'))
    expect(m.quits).toBe(1)
    // The watchdog was disarmed with it — no box on top of the box.
    vi.advanceTimersByTime(60_000)
    expect(m.errors).toHaveLength(1)
    expect(w.shows).toBe(0)
  })

  it('a deep link that arrives before the load is replayed once, never dropped', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    m.invoke('open-app', '/app?automation=abc')
    expect(m.sent).toEqual([])
    // A failed navigation still fires did-finish-load, and isLoading() reads
    // false in the gap before the 1 s retry — the target used to be sent into
    // that gap, at a renderer that had never registered its listener.
    w.fire('did-start-loading')
    w.fire('did-fail-load', -102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/', true)
    w.fire('did-finish-load')
    expect(m.sent).toEqual([])
    // The first load that really succeeds replays it, exactly once.
    vi.advanceTimersByTime(1000)
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(m.sent).toEqual([['open-target', '/app?automation=abc']])
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(m.sent).toEqual([['open-target', '/app?automation=abc']])
  })

  it('handlers deferred from a closed window never act on its successor (§9)', () => {
    const m = loadMain()
    m.invoke('open-app', '/app')
    const first = m.wins[0]
    first.close()
    m.invoke('open-app', '/app')
    expect(m.wins).toHaveLength(2)
    const second = m.wins[1]
    // Chromium still fires the dead window's own handlers; acting on the
    // module-level `win` would show and reload the successor instead.
    first.fire('did-start-loading')
    first.fire('did-finish-load')
    expect(second.shows).toBe(0)
    first.fire('render-process-gone', { reason: 'oom' })
    expect(second.loads).toBe(1)
    expect(m.quits).toBe(0)
  })

  it('showApp on an unloaded window stays hidden; after the load it shows again', () => {
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    // Deep link while still loading: no blank show.
    m.invoke('open-app', '/app?automation=abc')
    expect(w.shows).toBe(0)
    // First successful load shows it…
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    // …and from then on showApp shows as before.
    m.invoke('open-app', '/app?automation=abc')
    expect(w.shows).toBe(2)
  })
})

// ---- §9 capability wiring + the per-OS close rule --------------------------
// main.cjs asks its own platform module (`plat.capabilities`) before wiring the
// tray, the login item, the dock icon and the update machinery — so these run
// against whichever module this OS selects, and assert the capability's rule
// rather than one platform's answer.

type Point = { x: number, y: number }
type Rect = { x: number, y: number, width: number, height: number }
type Display = { bounds: Rect, workArea: Rect }

const platMod = realRequire(join(PLATFORM_DIR, 'index.cjs')) as {
  capabilities: { trayPanel: boolean, loginItem: boolean, dockIcon: boolean, updates: boolean, appMenu: boolean, desktopEntry: boolean }
  UPDATER: string | null
  updateFeedUrl: (arch: string) => string | null
  bundledPythonPath: (resourcesPath: string) => string
  shimText: (python: string) => string
  applyLoginItem: (app: { isPackaged: boolean }, enabled: boolean, exec?: unknown) => void
  panelPosition: (pt: Point, display: Display, height?: number) => { x: number, y: number }
}
const caps = platMod.capabilities
// §3: every platform with a feed drives electron-updater against the generic
// provider (`mac` on darwin, `nsis` on win32, `appimage` on linux); a
// platform without one (fallback) has no machinery at all. The tests below
// assert the rule for whichever this platform declares.
const HAS_UPDATER = caps.updates

describe('main.cjs platform capability wiring (§2/§9)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('tray creation follows trayPanel', () => {
    const m = loadMain()
    m.invoke('apply-settings', { menuBarIcon: true })
    expect(m.trays).toHaveLength(caps.trayPanel ? 1 : 0)
  })

  it('sets the platform AppUserModelID at boot — Windows only (§3 identifiers)', () => {
    const m = loadMain()
    expect(m.aumids).toEqual(
      process.platform === 'win32' ? ['ai.autowright.app'] : [],
    )
  })

  it('login-item reconcile follows loginItem', () => {
    if (process.platform === 'linux') {
      // §4.9 on Linux: the §2 applyLoginItem seam reconciles the XDG
      // autostart .desktop file — Electron's login-item API (a no-op there)
      // is never asked.
      const dir = mkdtempSync(join(tmpdir(), 'autowright-login-'))
      const prevXdg = process.env.XDG_CONFIG_HOME
      process.env.XDG_CONFIG_HOME = dir
      try {
        const m = loadMain()
        m.invoke('apply-settings', { login: true })
        const entry = join(dir, 'autostart', 'ai.autowright.app.desktop')
        expect(existsSync(entry)).toBe(true)
        expect(m.loginItem).toEqual([])
        m.invoke('apply-settings', { login: false })
        expect(existsSync(entry)).toBe(false)
      } finally {
        if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME
        else process.env.XDG_CONFIG_HOME = prevXdg
        rmSync(dir, { recursive: true, force: true })
      }
      return
    }
    if (process.platform === 'win32') {
      // §4.9 win32 legacy sweep runs once per process through reg.exe — trip
      // its memo with a noop exec so the leaf run never touches the real
      // registry. platMod is win32.cjs here, the same module instance
      // main.cjs's own require resolves.
      platMod.applyLoginItem({ isPackaged: false }, true, () => {})
    }
    const m = loadMain()
    m.invoke('apply-settings', { login: true })
    expect(m.loginItem).toEqual(caps.loginItem ? [true] : [])
  })

  it('the automatic update check arms a timer only where updates is true (§3)', () => {
    const m = loadMain()
    // The immediate check would hit the real feed on a platform that has one.
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new Error('offline in tests'))
    const timerSpy = vi.spyOn(globalThis, 'setInterval')
    try {
      m.invoke('apply-settings', { automaticUpdateCheck: true })
      if (!caps.updates) {
        expect(timerSpy).not.toHaveBeenCalled()
        return
      }
      expect(timerSpy).toHaveBeenCalled()
    } finally {
      timerSpy.mockRestore()
      fetchSpy.mockRestore()
      // Don't leave a 24 h interval behind in the test process.
      if (caps.updates) m.invoke('apply-settings', { automaticUpdateCheck: false })
    }
  })

  it('update-check answers the no-updates line as its error detail without a feed (§3)', async () => {
    const m = loadMain()
    if (!caps.updates) {
      // No feed on this platform: the check answers the error state carrying
      // the plain line, so the §9.4 page renders it instead of the generic
      // "Couldn't reach GitHub" network copy.
      expect(await m.invoke('update-check')).toEqual({
        state: 'error', error: 'Updates are not supported on this platform yet.',
      })
      return
    }
    // With a feed, a failed read stays a bare error state — no detail, so
    // the generic network copy still stands, on every platform.
    m.updater.checkError = new Error('offline in tests')
    expect(await m.invoke('update-check')).toEqual({ state: 'error' })
  })

  it('window-all-closed: a dock keeps the app resident; without one, only a live tray does (§9)', () => {
    const m = loadMain()
    m.emit('window-all-closed')
    if (caps.dockIcon) {
      // macOS: a tray-and-dock app never quits on close.
      expect(m.quits).toBe(0)
      return
    }
    // No dock: with no tray icon showing there is no way back — quit the UI.
    expect(m.quits).toBe(1)
    if (!caps.trayPanel) return
    // …and with a tray showing, stay resident. The rule reads the live tray
    // reference, so this only changes after the tray actually exists.
    m.invoke('apply-settings', { menuBarIcon: true })
    m.emit('window-all-closed')
    expect(m.quits).toBe(1)
  })

  it('turning the tray off destroys the panel and re-checks the close rule (§9/§13)', () => {
    if (!caps.trayPanel) return
    const m = loadMain()
    m.invoke('apply-settings', { menuBarIcon: true })
    expect(m.trays).toHaveLength(1)
    // The panel is built lazily on the first tray click, and it is the only
    // window in this process — no main window was ever opened.
    m.clickTray()
    expect(m.wins).toHaveLength(1)
    const panel = m.wins[0]
    m.invoke('apply-settings', { menuBarIcon: false })
    // Left merely hidden it would be an unreachable window that also keeps
    // window-all-closed from ever firing again.
    expect(panel.destroys).toBe(1)
    // …and the next tray click builds a fresh one rather than touching it.
    m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(m.wins).toHaveLength(2)
    expect(m.wins[1].destroys).toBe(0)
    if (caps.dockIcon) {
      // macOS stays resident behind the dock whatever the tray does.
      expect(m.quits).toBe(0)
      return
    }
    // No dock, no tray, no main window: nothing left to click, so the app
    // quits instead of running on invisibly.
    expect(m.quits).toBe(1)
  })

  it('the ensure-backend failure detail is per-OS copy from the platform module (§9)', () => {
    // main.cjs holds no OS-specific failure copy of its own any more.
    expect(src).toContain('detail: plat.SERVICE_START_FAILED_DETAIL')
    expect(src).not.toContain('may be blocking an unsigned build')
    // …and it still composes it with the §2 serviceDiagnostics capture.
    expect(src).toMatch(/SERVICE_START_FAILED_DETAIL[\s\S]{0,400}plat\.serviceDiagnostics\(appLog\)/)
  })
})

// ---- §3 electron-updater updates (darwin MacUpdater / win32 NsisUpdater /
// linux AppImageUpdater). The whole block runs wherever the platform module
// serves a feed; the real handlers run against the fake updater the stub
// `require` supplies, so nothing here touches the network.

describe.skipIf(!HAS_UPDATER)('main.cjs electron-updater path (§3)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('is not constructed at module load — nothing checks until it is asked', async () => {
    const m = loadMain()
    expect(m.updater.options).toBeNull()
    expect(m.updater.checks).toBe(0)
    await m.invoke('update-check')
    // …and only then, against the §2 module's generic-provider feed.
    expect(m.updater.options).toEqual({
      provider: 'generic', url: platMod.updateFeedUrl(process.arch),
    })
    expect(m.updater.checks).toBe(1)
  })

  it('a check only reads the feed: autoDownload and install-on-quit are off (§3)', async () => {
    const m = loadMain()
    await m.invoke('update-check')
    expect(m.updater.autoDownload).toBe(false)
    expect(m.updater.autoInstallOnAppQuit).toBe(false)
    expect(m.updater.downloads).toBe(0)
    expect(m.updater.installs).toBe(0)
  })

  it('maps the feed onto the same {state} shape and §9.4 compare rule', async () => {
    const m = loadMain() // the stub app reports version 0.0.0
    m.updater.check = { updateInfo: { version: '9.9.9' } }
    expect(await m.invoke('update-check')).toEqual({ state: 'available', version: '9.9.9' })
    // …and the remembered version reached the renderer (§3 update-available).
    expect(m.sent).toEqual([])  // no window yet — recorded, not lost
    expect(await m.invoke('update-available')).toBe('9.9.9')

    m.updater.check = { updateInfo: { version: '0.0.0' } }
    expect(await m.invoke('update-check')).toEqual({ state: 'uptodate' })
    expect(await m.invoke('update-available')).toBeNull()

    // Malformed counts as not newer, exactly like the mac feed compare.
    m.updater.check = { updateInfo: { version: 'not-a-version' } }
    expect(await m.invoke('update-check')).toEqual({ state: 'uptodate' })

    m.updater.checkError = new Error('ENOTFOUND raw.githubusercontent.com')
    expect(await m.invoke('update-check')).toEqual({ state: 'error' })
  })

  it('update-download drives determinate progress over the update-progress IPC', async () => {
    const m = loadMain()
    m.invoke('open-app', '/app') // a window to receive the events
    const result = await m.invoke('update-download')
    expect(result).toEqual({ ok: true })
    expect(m.updater.downloads).toBe(1)

    // The handler subscribed to electron-updater's own progress events and
    // forwards percent — null when the download reports no total to divide by
    // (the §9.4 bar goes indeterminate).
    const onProgress = m.updater.listeners.get('download-progress')
    expect(onProgress).toBeTypeOf('function')
    onProgress?.({ percent: 42.4, total: 1000, transferred: 424 })
    onProgress?.({ percent: 0, total: 0, transferred: 0 })
    onProgress?.(undefined)
    expect(m.sent.filter(([channel]) => channel === 'update-progress'))
      .toEqual([['update-progress', 100], ['update-progress', 42],
        ['update-progress', null], ['update-progress', null]])
  })

  it('update-download reports the updater\'s own failure, and never a stale ok', async () => {
    const m = loadMain()
    m.updater.downloadError = new Error('net::ERR_CONNECTION_RESET')
    expect(await m.invoke('update-download')).toEqual({ error: 'net::ERR_CONNECTION_RESET' })

    // Nothing newer in the feed: refuse rather than arm an install of nothing.
    const m2 = loadMain()
    m2.updater.check = { updateInfo: { version: '0.0.0' } }
    expect(await m2.invoke('update-download')).toEqual({ error: 'no update available' })
    expect(m2.updater.downloads).toBe(0)
  })

  it('update-install quits to install, behind the live-execution gate (§3)', async () => {
    const m = loadMain()
    expect(await m.invoke('update-install')).toEqual({ ok: true })
    expect(m.updater.installs).toBe(1)
    // The gate itself is one shared code path for every platform: the busy
    // check runs before either updater is asked to quit.
    expect(src).toMatch(/if \(await executionsLive\(\)\) return \{ busy: true \}[\s\S]{0,400}quitAndInstall\(\)/)
  })
})

// ---- §3 Homebrew-managed detection ----------------------------------------
// brewManaged() probes the Caskroom dir fresh on every call; the tests pin the
// probe to a known path via the AUTOWRIGHT_CASKROOM escape hatch so they never
// depend on what's brew-installed on the machine running them.

describe('main.cjs Homebrew-managed updates (§3)', () => {
  const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM

  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
  })

  it('update-brew-managed answers whether the Caskroom dir exists — probed per call', async () => {
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = m.home // any existing dir stands in for the Caskroom
    if (process.platform !== 'darwin') {
      // §2: only darwin has a managed-install channel — every other platform
      // module answers false regardless of the escape hatch.
      expect(await m.invoke('update-brew-managed')).toBe(false)
      return
    }
    expect(await m.invoke('update-brew-managed')).toBe(true)
    // Fresh probe, not a cached launch-time answer: the same loaded main flips
    // with the dir (a brew install/uninstall while the app runs).
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'not-there')
    expect(await m.invoke('update-brew-managed')).toBe(false)
  })

  it('update-download and update-install refuse on a brew-managed copy', async () => {
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = m.home
    if (process.platform !== 'darwin') {
      // §2: only darwin has a managed-install channel, so the escape hatch
      // changes nothing here. Without a feed both actions answer the plain
      // no-updates line; with one (win32/linux) they run the real update path.
      if (!caps.updates) {
        expect(await m.invoke('update-download')).toEqual({ error: 'Updates are not supported on this platform yet.' })
        expect(await m.invoke('update-install')).toEqual({ error: 'Updates are not supported on this platform yet.' })
        return
      }
      expect(await m.invoke('update-download')).toEqual({ ok: true })
      expect(await m.invoke('update-install')).toEqual({ ok: true })
      return
    }
    expect(await m.invoke('update-download')).toEqual({ error: 'This copy is managed by Homebrew.' })
    expect(await m.invoke('update-install')).toEqual({ error: 'This copy is managed by Homebrew.' })
  })

  it('probes only the two Caskroom locations, inside the platform managedInstall()', () => {
    const hits = union.match(/Caskroom\/autowright/g) ?? []
    expect(hits).toHaveLength(2)
    const darwinSrc = readFileSync(join(PLATFORM_DIR, 'darwin.cjs'), 'utf-8')
    expect(darwinSrc).toMatch(/function managedInstall\(\)[\s\S]{0,300}\/opt\/homebrew\/Caskroom\/autowright[\s\S]{0,120}\/usr\/local\/Caskroom\/autowright/)
  })
})

// ---- §3 bounded service children -------------------------------------------
// `service install` and `service stop` are the only children the app spawns,
// and launchctl/sc can wedge on a broken domain. Every spawn and every wait on
// one carries a 120 s bound, so quit-all and reset always settle — with the
// plain timeout line when the child never came back.

// Electron sets process.resourcesPath and §3's bundled-python probe joins onto
// it; a plain node run has none, so these tests plant one.
const nodeProcess = process as unknown as { resourcesPath?: string }
const savedResourcesPath = nodeProcess.resourcesPath

function restoreResourcesPath() {
  if (savedResourcesPath === undefined) delete nodeProcess.resourcesPath
  else nodeProcess.resourcesPath = savedResourcesPath
}

describe('main.cjs bounded service children (§3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a child killed on its own deadline answers the plain timeout line', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const options: ServiceChildOptions[] = []
    // Model child_process' own `timeout`: kill the child at the deadline and
    // call back with the killed error, exactly as the real one does.
    const m = loadMain({
      execFile: (_py, _args, o, cb) => {
        options.push(o)
        setTimeout(() => {
          cb(Object.assign(new Error('Command failed'), { killed: true, signal: 'SIGTERM' }), '', '')
        }, o.timeout)
      },
    })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const result = m.invoke('quit-all', { force: true }) as Promise<unknown>
    await vi.advanceTimersByTimeAsync(120_000)
    expect(await result).toEqual({ error: 'service stop timed out' })
    // A signal name is never reported as the failure — and the spawn carried
    // the bound plus a capped buffer.
    expect(options).toEqual([{ windowsHide: true, timeout: 120_000, maxBuffer: 4 * 1024 * 1024 }])
    // §3: the app stays up on any stop failure.
    expect(m.quits).toBe(0)
  })

  it('a wedged install child cannot hang the stop that waits for it', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    try {
      // No backend.json, so ensure-backend goes straight to `service install`
      // — and that child never calls back. quit-all used to sit on
      // `await serviceInstallDone` for the life of the app.
      const m = loadMain({ ready: true, execFile: () => {} })
      await vi.advanceTimersByTimeAsync(0)
      const result = m.invoke('quit-all', { force: true }) as Promise<unknown>
      await vi.advanceTimersByTimeAsync(120_000)
      expect(await result).toEqual({ error: 'service stop timed out' })
      expect(m.quits).toBe(0)
    } finally {
      rmSync(resources, { recursive: true, force: true })
    }
  })
})

// ---- §3 live-execution gate ------------------------------------------------
// The gate in front of every install, stop and reset asks the backend what is
// running. Only a backend that cannot be reached at all counts as idle.

describe('main.cjs live-execution gate (§3)', () => {
  afterEach(() => {
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a backend that will not answer counts as busy; an unreachable one as idle', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ execFile: (_py, _args, _o, cb) => { cb(null, 'stopped', '') } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValue({ ok: false, status: 500 } as Response)
    try {
      // Up, but the question came back a 500: stopping it now could land on
      // top of a running execution.
      expect(await m.invoke('quit-all', {})).toEqual({ busy: true })
      // Unreachable is the one idle answer — nothing can be executing on it.
      fetchSpy.mockRejectedValue(new Error('offline in tests'))
      expect(await m.invoke('quit-all', {})).toEqual({ ok: true })
      expect(m.quits).toBe(1)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('the version-sync drain treats a non-OK probe like an unreachable one (§3)', () => {
    // Both unknowns keep the drain waiting; only the /health cross-check ends
    // it, so a version-sync install still never lands mid-execution.
    expect(src).toContain("if (live === null || live === 'unknown') {")
    expect(src).toContain("return live === true || live === 'unknown'")
  })
})

// ---- §3 CLI shim writes ----------------------------------------------------
// AUTOWRIGHT_SHIM (§15) points the shim at a temp file, so these drive the
// real cli-status / cli-install handlers without touching ~/.local/bin.

describe('main.cjs CLI shim writes (§3)', () => {
  const savedShim = process.env.AUTOWRIGHT_SHIM
  let shimDir: string | null = null

  afterEach(() => {
    if (shimDir) rmSync(shimDir, { recursive: true, force: true })
    shimDir = null
    if (savedShim === undefined) delete process.env.AUTOWRIGHT_SHIM
    else process.env.AUTOWRIGHT_SHIM = savedShim
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  // One loaded main with a temp shim location and a backend.json naming the
  // interpreter the shim should run (§3 discovery fields).
  function loadWithShim(): { m: MainStub, shim: string } {
    shimDir = mkdtempSync(join(tmpdir(), 'aw-shim-'))
    const shim = join(shimDir, 'autowright')
    process.env.AUTOWRIGHT_SHIM = shim
    const m = loadMain()
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    return { m, shim }
  }

  it('cli-install refuses to clobber a foreign autowright command', async () => {
    const { m, shim } = loadWithShim()
    writeFileSync(shim, '#!/bin/sh\necho someone elses tool\n')
    expect(await m.invoke('cli-install')).toEqual({
      ok: false,
      error: `A different autowright command already exists at ${shim}. Remove it first.`,
    })
    // Untouched: a file we did not write is never overwritten.
    expect(readFileSync(shim, 'utf-8')).toContain('someone elses tool')
  })

  it('cli-install writes the shim where there is nothing in the way', async () => {
    const { m, shim } = loadWithShim()
    expect(await m.invoke('cli-install')).toEqual({ ok: true })
    expect(readFileSync(shim, 'utf-8')).toBe(platMod.shimText('/usr/bin/python3'))
  })

  it('a healed shim keeps its executable bit', async () => {
    const { m, shim } = loadWithShim()
    // Ours, but pointing at an interpreter that moved, and not executable —
    // writeFileSync's `mode` only applies where it creates the file, so the
    // heal used to leave the old mode in place.
    writeFileSync(shim, platMod.shimText('/old/python'), { mode: 0o644 })
    expect(await m.invoke('cli-status')).toMatchObject({ state: 'installed', path: shim })
    expect(readFileSync(shim, 'utf-8')).toBe(platMod.shimText('/usr/bin/python3'))
    // Windows has no executable bit to keep.
    if (process.platform !== 'win32') expect(statSync(shim).mode & 0o777).toBe(0o755)
  })
})

// ---- §9 ready chain --------------------------------------------------------
// The reopen handler is registered first and every OS-side step is guarded on
// its own, so the app always ends up with a window it can paint.

describe('main.cjs ready chain (§9)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('an OS-side step that throws still leaves the app with a window', () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ ready: true, dockIconThrows: true })
    return vi.advanceTimersByTimeAsync(0).then(() => {
      expect(m.windows).toHaveLength(1)
      // Where there is a dock, the throw really happened and was logged.
      if (caps.dockIcon) expect(m.log()).toContain('setting the dock icon failed')
    })
  })
})

// ---- §3 update install ------------------------------------------------------
// The updater's error stream is listened to, and an install that returns
// without quitting answers { error } with its message (§3, §9.4) — a silent
// no-op leaves the card stuck on "Restart to update" forever.

describe('main.cjs update-install refusals (§3)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a quitAndInstall that answers false reports the updater\'s own error', async () => {
    if (!HAS_UPDATER) return // no feed here — the no-updates line is covered above
    const m = loadMain()
    // The NSIS/AppImage shape: the failure goes out on the error stream and
    // quitAndInstall then answers false.
    m.updater.installError = new Error('spawn Autowright Setup.exe ENOENT')
    m.updater.installResult = false
    expect(await m.invoke('update-install'))
      .toEqual({ error: 'spawn Autowright Setup.exe ENOENT' })
    expect(m.quits).toBe(0)
    expect(m.log()).toContain('update: install refused: spawn Autowright Setup.exe ENOENT')
  })

  it('a refusal with nothing on the error stream still answers an error', async () => {
    if (!HAS_UPDATER) return
    const m = loadMain()
    m.updater.installResult = false
    expect(await m.invoke('update-install'))
      .toEqual({ error: 'the updater could not install this update' })
  })

  it('a throwing install is answered, never left to reject the IPC', async () => {
    if (!HAS_UPDATER) return
    const m = loadMain()
    m.updater.installThrows = new Error('ShipIt is missing')
    expect(await m.invoke('update-install')).toEqual({ error: 'ShipIt is missing' })
  })
})

// ---- §9.4 external hand-offs ------------------------------------------------

describe('main.cjs external link hand-off (§9.4)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('an openExternal the OS refuses is logged, not an unhandled rejection', async () => {
    const m = loadMain({ openExternalRejects: true })
    m.invoke('open-app', '/app')
    // §9.4: a link out of the renderer is refused as a navigation and handed
    // to the browser instead.
    expect(m.wins[0].navigate('https://autowright.ai/docs')).toBe(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(m.externals).toEqual(['https://autowright.ai/docs'])
    expect(m.log()).toContain("open-external: couldn't open https://autowright.ai/docs")
  })
})

// ---- §5.1 open-archive ------------------------------------------------------

describe('main.cjs open-archive hardening (§5.1)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a readable archive comes back as name + bytes', async () => {
    const m = loadMain()
    const file = join(m.home, 'ok.autowright')
    writeFileSync(file, 'PK\u0003\u0004')
    m.dialogAnswer.canceled = false
    m.dialogAnswer.filePaths = [file]
    const r = await m.invoke('open-archive') as { name: string, data: Buffer }
    expect(r.name).toBe('ok.autowright')
    expect(r.data.toString()).toBe('PK\u0003\u0004')
  })

  it('an unreadable pick answers null instead of throwing at the renderer', async () => {
    const m = loadMain()
    m.dialogAnswer.canceled = false
    m.dialogAnswer.filePaths = [join(m.home, 'not-there.autowright')]
    expect(await m.invoke('open-archive')).toBeNull()
    expect(m.log()).toContain("open-archive: couldn't read")
  })

  it('an archive over the 64 MB cap is refused before it is read', async () => {
    const m = loadMain()
    const big = join(m.home, 'big.autowright')
    writeFileSync(big, '')
    truncateSync(big, 64 * 1024 * 1024 + 1) // sparse — no bytes on disk
    m.dialogAnswer.canceled = false
    m.dialogAnswer.filePaths = [big]
    expect(await m.invoke('open-archive'))
      .toEqual({ error: 'The archive is larger than 64 MB.' })
  })
})

// ---- §9.3 log tail ----------------------------------------------------------

describe('main.cjs tail-logs (§9.3)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('stringifies only what was really read — no NUL padding', async () => {
    const realFs = realRequire('fs') as typeof import('node:fs')
    // The rotation race: fstat sees the file at its old size, so the buffer is
    // bigger than anything the read can fill.
    const m = loadMain({
      fs: {
        fstatSync: (fd: number) => {
          const st = realFs.fstatSync(fd)
          return { ...st, size: st.size + 200 }
        },
      },
    })
    mkdirSync(join(m.home, 'logs'), { recursive: true })
    writeFileSync(join(m.home, 'logs', 'app.log'), 'one line\n')
    const out = await m.invoke('tail-logs') as { name: string, text: string }[]
    const tail = out.find((f) => f.name === 'app.log')
    expect(tail?.text).toBe('one line\n')
    expect(tail?.text).not.toContain('\u0000')
  })
})

// ---- §13 panel placement + lifetime -----------------------------------------

describe('main.cjs §13 panel placement and lifetime', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it("clamps the panel's x to the work area on both edges", () => {
    // A secondary display to the left of the primary one: a tray icon at its
    // very left edge used to push the panel off-screen.
    const display: Display = {
      bounds: { x: -1920, y: 0, width: 1920, height: 1080 },
      workArea: { x: -1920, y: 0, width: 1920, height: 1032 },
    }
    for (const name of ['win32.cjs', 'darwin.cjs', 'linux.cjs']) {
      const mod = realRequire(join(PLATFORM_DIR, name)) as { panelPosition: typeof platMod.panelPosition }
      expect(mod.panelPosition({ x: -1918, y: 8 }, display, 420).x)
        .toBe(display.workArea.x + 6)
      expect(mod.panelPosition({ x: -10, y: 8 }, display, 420).x)
        .toBe(display.workArea.x + display.workArea.width - 344 - 6)
    }
  })

  it('a destroyed panel forgets its measured height', async () => {
    if (!caps.trayPanel) return // §13: Linux ships no tray surface at all
    const m = loadMain()
    const place = vi.spyOn(platMod, 'panelPosition')
    // The tray is the only way to the panel; the §4.9 setting creates it.
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(place.mock.calls.at(-1)?.[2]).toBe(420)
    // It grows with its content, re-anchoring at the real height (§13).
    await m.invoke('resize-panel', 600)
    expect(place.mock.calls.at(-1)?.[2]).toBe(600)
    // Tray off destroys the panel; the next one opens at the default again.
    await m.invoke('apply-settings', { menuBarIcon: false })
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(place.mock.calls.at(-1)?.[2]).toBe(420)
  })
})

// ---- §13 window restore -----------------------------------------------------

describe('main.cjs minimized window restore (§13)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a minimized window is restored before it is shown', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ ready: true })
    await vi.advanceTimersByTimeAsync(0)
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    // §13 row click: showing a minimized window without restoring it looks
    // like a no-op — nothing comes to the front.
    w.minimize()
    await m.invoke('open-app', '/app?automation=a1')
    expect(w.restores).toBe(1)
    expect(w.shows).toBe(2)
    // The dock/tray reopen follows the same rule.
    w.minimize()
    m.emit('activate')
    expect(w.restores).toBe(2)
    expect(w.shows).toBe(3)
  })
})

// ---- §3 quit/reset log + status quiet ---------------------------------------

describe('main.cjs quit and reset quiet the shell (§3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('an install dropped because a quit-all latched records that outcome', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    // ensure-backend parks on the /health probe until the test releases it, so
    // quit-all latches quittingAll while the install is still ahead of it.
    let releaseHealth = () => {}
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/health')) {
        return new Promise((resolve) => {
          releaseHealth = () => resolve({ ok: false, status: 503 } as Response)
        })
      }
      return Promise.reject(new Error('offline in tests'))
    })
    try {
      const m = loadMain({ ready: true, execFile: (_py, _a, _o, cb) => { cb(null, 'stopped', '') } })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      await vi.advanceTimersByTimeAsync(0)
      expect(await m.invoke('quit-all', {})).toEqual({ ok: true })
      releaseHealth()
      await vi.advanceTimersByTimeAsync(0)
      // §3: never latched on 'installing' for a run that was never made.
      expect(await m.invoke('backend-status')).toEqual({ state: 'failed', detail: 'quitting' })
      expect(m.log()).toContain('ensure-backend: install dropped')
    } finally {
      fetchSpy.mockRestore()
      rmSync(resources, { recursive: true, force: true })
    }
  })

  it('nothing writes app.log once the reset has erased it', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new Error('offline in tests'))
    try {
      const m = loadMain({ execFile: (_py, _a, _o, cb) => { cb(null, 'stopped', '') } })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      expect(await m.invoke('reset-all')).toEqual({ ok: true })
      expect(m.exits).toEqual([0])
      expect(existsSync(join(m.home, 'logs'))).toBe(false)
      // Any later caller is a no-op: appLog re-creates the logs root, and a
      // reset that leaves a fresh one behind is the regression (§3 step 4).
      m.dialogAnswer.canceled = false
      m.dialogAnswer.filePaths = [join(m.home, 'not-there.autowright')]
      expect(await m.invoke('open-archive')).toBeNull()
      expect(existsSync(join(m.home, 'logs'))).toBe(false)
      expect(m.log()).toBe('')
    } finally {
      fetchSpy.mockRestore()
    }
  })
})
