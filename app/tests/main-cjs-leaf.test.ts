// §2 CLI-leaf invariant guard over the Electron main layer — main.cjs plus
// the §2 platform modules under electron/platform/ (spec §15). The app
// registers the backend via `python -m autowright.service` and must never
// execute the CLI; §3 shim writes only ever target the user-local location —
// no admin prompt exists, and nothing ever writes to the legacy
// /usr/local/bin (the pre-08-15 bug was a silent best-effort write there).
// main.cjs has no importable module structure, so the guard reads the source.
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, symlinkSync, truncateSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, sep } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const ELECTRON_DIR = join(__dirname, '..', 'electron')
const src = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
// The platform modules are part of the same trust surface: every guard that
// scans main.cjs scans them too (union), so a §2 extraction can't smuggle a
// forbidden call out of the guard's sight.
const PLATFORM_DIR = join(ELECTRON_DIR, 'platform')
const platFiles = readdirSync(PLATFORM_DIR).filter((n) => n.endsWith('.cjs'))
const platSrc = platFiles.map((n) => readFileSync(join(PLATFORM_DIR, n), 'utf-8')).join('\n')
const union = `${src}\n${platSrc}`

// The arguments of the call whose opening parenthesis sits at `open`, each one
// verbatim. Parentheses, brackets, braces and string literals nest, so the
// split walks the source rather than cutting on every comma — a multi-line
// options object and a template literal carrying `${…}` both stay one
// argument.
function callArguments(source: string, open: number): string[] {
  const args: string[] = []
  let depth = 0
  let start = open + 1
  for (let i = open; i < source.length; i++) {
    const c = source[i]
    if (c === "'" || c === '"' || c === '`') {
      for (i++; i < source.length; i++) {
        if (source[i] === '\\') i++
        else if (source[i] === c) break
      }
      continue
    }
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') {
      depth--
      if (depth === 0) { args.push(source.slice(start, i).trim()); return args }
    } else if (c === ',' && depth === 1) {
      args.push(source.slice(start, i).trim())
      start = i + 1
    }
  }
  return args
}

// Every child-process call site in the scanned source, with its options
// argument resolved: the third argument verbatim when it is an object
// literal, otherwise the body of the `const NAME = { … }` it names. `exec` is
// in the list because win32's login-item sweep takes its runner as a
// parameter — the guard follows the call, not the callee's name.
function childProcessCalls(source: string) {
  return [...source.matchAll(/(?<![.\w])(execFile|spawn|exec)\(/g)].map((m) => {
    const open = m.index + m[0].length - 1
    const options = callArguments(source, open)[2] ?? ''
    const named = options.startsWith('{')
      ? options
      : source.match(new RegExp(`const ${options} = (\\{[^}]*\\})`))?.[1] ?? ''
    return { callee: m[1], site: source.slice(m.index, open + 60).split('\n')[0], options: named }
  })
}

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
    expect(verbs).toHaveLength(3)
    // §3 quit-entirely: the QUIT card's IPC and the OS quit share one
    // stop-and-quit, labelled by its caller; reset has its own stop, and so
    // does the win32 update-install.
    expect(src).toContain("runServiceVerb('stop', label)")
    expect(src).toContain("runServiceVerb('stop', 'reset')")
    expect(src).toContain("runServiceVerb('stop', 'update-install')")
    expect(src).toContain("stopAndQuit(!!opts?.force, 'quit-all')")
    expect(src).toContain("stopAndQuit(false, 'quit')")
    expect(src).toContain("stopAndQuit(true, 'quit')")
  })

  it('quit-all gates on live executions unless the renderer forces it (§3)', () => {
    // The §4.9 force-confirm modal's retry passes { force: true }, the one way
    // past the gate — and the gate still runs before the stop for every
    // unforced call.
    expect(src).toContain("ipcMain.handle('quit-all', async (_e, opts) => {")
    expect(src).toContain("stopAndQuit(!!opts?.force, 'quit-all')")
    expect(src).toMatch(
      /if \(!force && await executionsLive\(\)\) return \{ busy: true \}[\s\S]{0,600}runServiceVerb\('stop', label\)/)
  })

  it('every quit of our own passes through quitUi; only stop-and-quit and quitUi call app.quit (§3)', () => {
    // §3 quit means quit for good: a bare app.quit() anywhere else would be
    // intercepted by before-quit as the user's Quit and stop the backend.
    const bare = [...src.matchAll(/^(?!\s*\/\/).*\bapp\.quit\(\)/gm)].map((m) => m[0].trim())
    expect(bare).toEqual(['app.quit()'])
    expect(src).toContain('function quitUi() {\n  uiQuit = true\n  app.quit()\n}')
    for (const site of ['if (!gotLock) quitUi()', "showStartupError('The app window kept crashing.')\n    quitUi()",
      'if (!caps.dockIcon && !win) quitUi()', 'if (!tray) quitUi()']) {
      expect(src).toContain(site)
    }
    // The updater quits by itself — flagged before the call, cleared on refusal.
    expect(src).toMatch(/uiQuit = true\n[\s\S]{0,400}destroyPanel\(\)\n  try \{\n    genericUpdater\(\)\.quitAndInstall\(\)/)
  })

  it('never executes the CLI — autowright.cli appears only inside the shim file text', () => {
    // main.cjs itself never mentions the CLI; the platform modules mention it
    // exactly once each, as the shim file's contents (the shimText run line,
    // §3: POSIX or Windows .cmd form) — never a child-process invocation by
    // the app.
    expect(src).not.toContain('autowright.cli')
    const shimForms = [
      'exec ${quoted} -m autowright.cli "$@"', // POSIX shim (darwin/linux/fallback, §3 single-quoted path)
      '"${String(python).replace(/%/g, \'%%\')}" -m autowright.cli %*', // Windows .cmd shim (win32, §3 `%` doubled)
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

  it('every child-process call site hides the console window (§2 spawn policy)', () => {
    // Scanned, not listed by callee name: a spawn added anywhere in main.cjs
    // or a platform module has to pass options carrying `windowsHide` — a
    // console program started without it flashes a window on Windows — so a
    // call that puts its callback where the options belong fails here too.
    const calls = childProcessCalls(union)
    expect(calls.length).toBeGreaterThanOrEqual(8)
    for (const { callee, site, options } of calls) {
      expect(options, `${callee} at \`${site}\``).toContain('windowsHide: true')
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
    // The darwin Squirrel hand-off is the one per-OS fork, keyed on the
    // marker, and it runs strictly inside update-install, behind the
    // live-execution gate — never in the download handler (§3 2026-10-30).
    expect(src).toMatch(/if \(await executionsLive\(\)\) return \{ busy: true \}\n[\s\S]{0,200}if \(plat\.UPDATER === 'mac'\) \{\n\s*const updater = genericUpdater\(\)\n\s*const error = await stageWithSquirrel\(updater\)/)
    expect(src.match(/await stageWithSquirrel\(/g) ?? []).toHaveLength(1)
    const download = src.slice(src.indexOf('async function runDownloadUpdate()'))
    expect(download.slice(0, download.indexOf('\n}\n'))).not.toContain('stageWithSquirrel')
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
    // The body check itself is the shared healthVersion helper (pinned on its
    // own in the serviceChecks describe below), fed the same backend.json
    // read the probe used for the port.
    expect(src).toMatch(/async function backendVersion\(\)[\s\S]{0,1400}return healthVersion\(await res\.json\(\), info\)/)
    // …and every caller still reads "healthy" off exactly that answer, so the
    // stricter probe can't be routed around.
    expect(src).toContain('return (await backendVersion()) !== null')
    expect(src).toMatch(/let running = await backendVersion\(\)\n\s*if \(running === null\) \{/)
    expect(src).toMatch(/running = await awaitActiveBackend\(py\)\n\s*\}\n\s*if \(running !== null\) \{/)
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
    // All three spawn sites (install, the stop/reset verb, and ensure-backend's
    // status ask) carry the same options object…
    const spawns = src.match(/'autowright\.service', (?:'install'|'status'|verb)\], SERVICE_CHILD_OPTIONS/g) ?? []
    expect(spawns).toHaveLength(3)
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
// updater. Like the real MacUpdater (manual-only flags off), downloadUpdate
// never engages Squirrel; the stub Electron autoUpdater models Squirrel.Mac
// instead — its checkForUpdates stages "instantly" (flips
// squirrelDownloadedUpdate and emits update-downloaded), emits `error` when
// nativeError is armed, or stays silent when nativeSilent is set.
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
  // §3 install: what quitAndInstall answers (the NSIS/AppImage classes
  // return nothing, refusal included), the error it dispatches on its way
  // out, and the throw a broken installer produces instead.
  installResult: unknown
  installError: Error | null
  installThrows: Error | null
  // Called first thing inside quitAndInstall — lets a test read state at the
  // moment of the call (§3: the panel is gone before it).
  onInstall: (() => void) | null
  listeners: Map<string, (arg: unknown) => void>
  // §3 win32: BaseUpdater's installerPath — the staged installer file, null
  // until a download staged one. The fake download sets it.
  installerPath: string | null
  // §3 macOS: MacUpdater's squirrelDownloadedUpdate (never resets once set).
  squirrelDownloadedUpdate: boolean
  // §3 macOS Squirrel double: native checkForUpdates calls, the error it
  // emits instead of staging, and a Squirrel that emits nothing at all.
  nativeChecks: number
  nativeError: Error | null
  nativeSilent: boolean
}

// Per-window record: show/focus/load/destroy counts plus ways to fire the
// webContents and window events at the real handlers main.cjs registered
// (§9 never-paint guard, §13 panel lifetime).
interface WinRecord {
  shows: number
  focuses: number
  loads: number
  // §9: the hash each load asked for, in order (the dist-file load's
  // `{ hash }` option; the dev-server load's URL hash).
  loadedHashes: string[]
  destroys: number
  // §13: hide() calls — a panel the tray toggle put away, and the one a late
  // blur must not put away again.
  hides: number
  // §13: restore() calls, and every setPosition the panel placement made.
  restores: number
  positions: [number, number][]
  fire: (event: string, ...args: unknown[]) => void
  close: () => void
  // §13: put the window in the state the OS would (minimized), and drive the
  // §9.4 will-navigate handler with a real event object.
  minimize: () => void
  navigate: (url: string) => boolean
  // §13: the window-level blur the OS can deliver after the window is gone.
  blur: () => void
  // Fire any other window-level event main.cjs listens to (§3 Windows
  // `query-session-end`).
  windowEvent: (event: string) => void
}

interface MainStub {
  invoke: (channel: string, ...args: unknown[]) => unknown
  // Fire an app-level event main.cjs subscribed to (window-all-closed, …).
  emit: (event: string) => boolean
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
  dialogAnswer: { canceled: boolean, filePaths: string[], filePath: string | null, response: number }
  powerListeners: Map<string, () => void>
  // §13: fire the tray's own click handler, the only way to the panel.
  clickTray: () => void
  // §9: a second launch's `second-instance` signal (a relaunching update).
  secondInstance: () => void
  // §9 watchdog / renderer-death reporting: every error box (an async
  // showMessageBox of type 'error') the shell put up, and the app.log lines
  // behind them.
  errors: [string, string][]
  log: () => string
  // §3 reset: the exit codes app.exit was called with.
  exits: number[]
  // §13 shell-posted notifications: every Notification main.cjs constructed.
  notifications: NotificationRecord[]
  // §13: every Tray construction attempted, the throwing ones included.
  trayAttempts: number
}

// §13: one shown OS notification — its options, its show() count, and a way
// to fire the click the OS would deliver.
interface NotificationRecord {
  options: { title?: string, body?: string }
  shows: number
  click: () => void
}

const realRequire = createRequire(join(ELECTRON_DIR, 'main.cjs'))
const savedHome = process.env.AUTOWRIGHT_HOME

// §3 service-child double: main.cjs's own `require` hands this to it instead
// of child_process.execFile, so a test can hang a child or kill it on its
// deadline without ever spawning anything.
type ServiceChildOptions = {
  windowsHide: boolean, timeout: number, maxBuffer: number, env: Record<string, string | undefined>,
}
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
  // §3 native busy confirm: answer showMessageBox from the test (a promise the
  // test resolves), so a quit can be held while the dialog is up.
  showMessageBox?: () => Promise<{ response: number }>
  // Patch individual `fs` functions main.cjs sees (the §9.3 log-rotation race
  // is otherwise unreachable from a single-threaded test).
  fs?: Record<string, unknown>
  // §13: what Notification.isSupported() answers (false by default, so no
  // other test ever opens the shell socket).
  notificationSupported?: boolean
  // §2: patch individual exports of the platform module main.cjs selects
  // (the real one for this host underneath), so a per-OS flag can be read
  // as another OS declares it.
  platform?: Record<string, unknown>
  // §9: what app.requestSingleInstanceLock() answers on each call (true by
  // default — the one instance).
  singleInstanceLock?: () => boolean
  // §13: make every Tray construction throw (a package missing its icon).
  trayThrows?: boolean
}

function loadMain(options: LoadOptions = {}): MainStub {
  const handlers = new Map<string, (e: unknown, ...args: unknown[]) => unknown>()
  // Listeners take the Electron event: `before-quit` calls preventDefault on it.
  const appEvents = new Map<string, (e: { preventDefault: () => void }) => void>()
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
  const dialogAnswer: { canceled: boolean, filePaths: string[], filePath: string | null, response: number } = {
    canceled: true, filePaths: [], filePath: null, response: 1,
  }
  const trayEvents = new Map<string, () => void>()
  const powerListeners = new Map<string, () => void>()
  let quits = 0
  let trayAttempts = 0
  const exits: number[] = []
  const notifications: NotificationRecord[] = []
  const home = mkdtempSync(join(tmpdir(), 'aw-main-'))
  process.env.AUTOWRIGHT_HOME = home

  const updater: UpdaterRecord = {
    options: null, autoDownload: null, autoInstallOnAppQuit: null,
    checks: 0, downloads: 0, installs: 0,
    check: { updateInfo: { version: '9.9.9' } },
    checkError: null, downloadError: null, listeners: new Map(),
    installResult: undefined, installError: null, installThrows: null, onInstall: null,
    installerPath: null, squirrelDownloadedUpdate: false,
    nativeChecks: 0, nativeError: null, nativeSilent: false,
  }
  const nativeListeners = new Map<string, Set<(arg?: unknown) => void>>()
  const nativeEmit = (event: string, arg?: unknown) => {
    for (const fn of [...(nativeListeners.get(event) ?? [])]) fn(arg)
  }

  class FakeUpdater {
    autoDownload = true
    autoInstallOnAppQuit = true
    get squirrelDownloadedUpdate() { return updater.squirrelDownloadedUpdate }
    set squirrelDownloadedUpdate(value: boolean) { updater.squirrelDownloadedUpdate = value }
    logger: unknown = null

    constructor(options: { provider?: string, url?: string }) { updater.options = options }
    get installerPath() { return updater.installerPath }
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
      updater.installerPath = 'installer.exe'
      return ['installer.exe']
    }

    quitAndInstall() {
      updater.onInstall?.()
      updater.installs += 1
      // The real BaseUpdater dispatches the failure on its error stream and
      // returns nothing — never false.
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
      shows: 0, focuses: 0, loads: 0, loadedHashes: [], destroys: 0, hides: 0, restores: 0, positions: [],
      fire: (event, ...args) => { this.wcListeners.get(event)?.({}, ...args) },
      close: () => { this.listeners.get('closed')?.() },
      blur: () => { this.listeners.get('blur')?.() },
      windowEvent: (event) => { this.listeners.get(event)?.() },
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
    loadFile(_file: string, opts?: { hash?: string }) {
      this.record.loads += 1
      this.record.loadedHashes.push(opts?.hash ?? '')
    }

    loadURL(url: string) {
      this.record.loads += 1
      this.record.loadedHashes.push(new URL(url).hash.replace(/^#/, ''))
    }

    on(event: string, fn: (...a: unknown[]) => void) { this.listeners.set(event, fn) }
    minimized = false
    show() { this.record.shows += 1 } focus() { this.record.focuses += 1 }
    // The real one throws on a destroyed window, which is the whole point of
    // the §13 blur guard.
    hide() {
      if (this.destroyed) throw new Error('Object has been destroyed')
      this.record.hides += 1
    }
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
      requestSingleInstanceLock: () => (options.singleInstanceLock ? options.singleInstanceLock() : true),
      on(event: string, fn: (e: { preventDefault: () => void }) => void) { appEvents.set(event, fn) },
      // A ready-chain load is a ready app: second-instance only acts on one.
      isReady: () => Boolean(options.ready),
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
        if (updater.nativeError) { nativeEmit('error', updater.nativeError); return }
        // MacUpdater's own native listener flips its flag on update-downloaded.
        updater.squirrelDownloadedUpdate = true
        nativeEmit('update-downloaded')
      },
      quitAndInstall() {},
    },
    BrowserWindow: FakeWindow,
    Notification: class {
      static isSupported() { return Boolean(options.notificationSupported) }
      listeners = new Map<string, () => void>()
      constructor(opts: { title?: string, body?: string }) {
        notifications.push({
          options: opts, shows: 0,
          click: () => { this.listeners.get('click')?.() },
        })
      }

      on(event: string, fn: () => void) { this.listeners.set(event, fn) }
      show() { notifications[notifications.length - 1].shows += 1 }
    },
    Menu: { buildFromTemplate: () => ({ popup() {} }) },
    Tray: class {
      constructor(icon: unknown) {
        trayAttempts += 1
        if (options.trayThrows) throw new Error('tray icon asset missing')
        trays.push(icon)
      }
      setToolTip() {}
      on(event: string, fn: () => void) { trayEvents.set(event, fn) }
      setImage() {} destroy() {}
    },
    dialog: {
      // §9: the synchronous box would block the main process; any call is a
      // regression.
      showErrorBox: () => { throw new Error('showErrorBox blocks the main process — use showMessageBox') },
      showOpenDialog: async (_w: unknown, opts: Record<string, unknown>) => {
        dialogs.push(['open', opts])
        return { canceled: dialogAnswer.canceled, filePaths: dialogAnswer.filePaths }
      },
      showSaveDialog: async (_w: unknown, opts: Record<string, unknown>) => {
        dialogs.push(['save', opts])
        return { canceled: dialogAnswer.canceled, filePath: dialogAnswer.filePath }
      },
      // §3 native busy confirm on a windowless OS quit.
      // An error box (type 'error') is recorded under `errors` and never
      // answers — the user hasn't dismissed it — so a caller that awaited it
      // would hang right here.
      showMessageBox: (opts: Record<string, unknown>) => {
        if (opts.type === 'error') {
          errors.push([String(opts.title), String(opts.message)])
          return new Promise(() => {})
        }
        dialogs.push(['message', opts])
        if (options.showMessageBox) return options.showMessageBox()
        return Promise.resolve({ response: dialogAnswer.response })
      },
    },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    // §3 session end: logout/restart/shutdown pass the quit through.
    powerMonitor: { on: (event: string, fn: () => void) => { powerListeners.set(event, fn) } },
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
      if (id === './platform/index.cjs' && options.platform) {
        return { ...realRequire(id) as object, ...options.platform }
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
    // Answers whether the listener called preventDefault (§3: an intercepted
    // OS quit does).
    emit: (event) => {
      const fn = appEvents.get(event)
      if (!fn) throw new Error(`no app listener for ${event}`)
      let prevented = false
      fn({ preventDefault: () => { prevented = true } })
      return prevented
    },
    clickTray: () => {
      const fn = trayEvents.get('click')
      if (!fn) throw new Error('no tray click handler')
      fn()
    },
    secondInstance: () => {
      const fn = appEvents.get('second-instance')
      if (!fn) throw new Error('no second-instance listener')
      fn({ preventDefault: () => {} })
    },
    opened, externals, revealed, windows, wins, trays, loginItem, aumids, sent, updater,
    home, errors, exits, dialogs, dialogAnswer, powerListeners, notifications,
    // AUTOWRIGHT_HOME points app.log at this test's own home (§15).
    log: () => {
      try { return readFileSync(join(home, 'logs', 'app.log'), 'utf-8') } catch { return '' }
    },
    get quits() { return quits },
    get trayAttempts() { return trayAttempts },
  }
}

describe('main.cjs IPC argument validation', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('reveal-path shows a path inside the app-support home', async () => {
    const m = loadMain()
    const target = join(m.home, 'automations', 'demo')
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, 'x')
    await m.invoke('reveal-path', target)
    // §9: the reveal acts on the real path (the temp root itself may sit
    // behind a symlink, macOS /var -> /private/var).
    expect(m.revealed).toEqual([realpathSync(target)])
  })

  it('reveal-path refuses a symlink inside the executions dir that points outside the roots', async () => {
    if (process.platform === 'win32') return // symlinks need elevation there
    const m = loadMain()
    const outside = mkdtempSync(join(tmpdir(), 'aw-outside-'))
    try {
      writeFileSync(join(outside, 'secret.txt'), 'x')
      // A step script can plant a link inside the executions dir (§9).
      const link = join(m.home, 'executions', 'run-1', 'escape')
      mkdirSync(dirname(link), { recursive: true })
      symlinkSync(outside, link)
      await m.invoke('reveal-path', link)
      await m.invoke('reveal-path', join(link, 'secret.txt'))
      expect(m.revealed).toEqual([])
      expect(m.opened).toEqual([])
      // A path that does not resolve at all is a no-op too.
      await m.invoke('reveal-path', join(m.home, 'not-there'))
      expect(m.revealed).toEqual([])
    } finally {
      rmSync(outside, { recursive: true, force: true })
    }
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

  it('save-file names the archive type, and leaves other files unfiltered (§5.1)', async () => {
    const m = loadMain()
    m.dialogAnswer.canceled = true
    // §5.1/§22.7: an .autowright save offers its own type, so the path the
    // user picks keeps the extension the importer and the §22.1 catalog need.
    await m.invoke('save-file', 'Watcher.autowright', Buffer.from('x'))
    expect(m.dialogs[0][1].filters)
      .toEqual([{ name: 'Autowright automation', extensions: ['autowright'] }])
    // §22.3 Export hands the catalog file to the same dialog under a .yaml
    // name — that one keeps the unfiltered dialog.
    await m.invoke('save-file', 'marketplace-catalog.yaml', Buffer.from('x'))
    expect(m.dialogs[1][1]).not.toHaveProperty('filters')
    // The name still only ever names a file inside the downloads dir.
    expect(m.dialogs[1][1].defaultPath).toBe(join(m.home, 'marketplace-catalog.yaml'))
  })

  it('apply-settings never moves the §5 data root — only the backend sync does', async () => {
    const m = loadMain()
    const elsewhere = mkdtempSync(join(tmpdir(), 'aw-data-'))
    const target = join(elsewhere, 'e-1')
    writeFileSync(target, 'x')
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
        expect(m.revealed).toEqual([realpathSync(target)])
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

  it('a failed load after a renderer-death reload retries `/app`, never the creation hash (§9)', () => {
    vi.useFakeTimers()
    const m = loadMain()
    const stale = '/app?execution=9b2f4c1e-7a3d-4e5f-8a6b-1c2d3e4f5a6b'
    m.invoke('open-app', stale)
    const w = m.wins[0]
    // Before any death, a failed load retries the hash the window was made with.
    w.fire('did-start-loading')
    w.fire('did-fail-load', -102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/', true)
    w.fire('did-finish-load')
    vi.advanceTimersByTime(1000)
    expect(w.loadedHashes).toEqual([stale, stale])
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    // The death reload lands on `/app` (a notification target can be hours
    // old) — and so does the retry of that reload when it fails.
    w.fire('render-process-gone', { reason: 'oom' })
    expect(w.loadedHashes).toEqual([stale, stale, '/app'])
    w.fire('did-start-loading')
    w.fire('did-fail-load', -102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/', true)
    w.fire('did-finish-load')
    vi.advanceTimersByTime(1000)
    expect(w.loadedHashes).toEqual([stale, stale, '/app', '/app'])
  })

  it('a reload alive 60 s past its load resets the death count — a later death gets its own reload (§9)', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    w.fire('render-process-gone', { reason: 'oom' })
    expect(w.loads).toBe(2)
    // The reload painted and stayed alive 60 s, so a death days later is a
    // new one, not a repeat.
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    vi.advanceTimersByTime(60_000)
    w.fire('render-process-gone', { reason: 'crashed' })
    expect(w.loads).toBe(3)
    expect(m.errors).toEqual([])
    expect(m.quits).toBe(0)
    // …but a second death before that reload finished still reports and quits.
    w.fire('render-process-gone', { reason: 'crashed' })
    expect(w.loads).toBe(3)
    expect(m.errors).toHaveLength(1)
    expect(m.quits).toBe(1)
  })

  it('a reload that dies again within 60 s of its load is a repeat — report and quit (§9)', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    vi.advanceTimersByTime(60_000)
    w.fire('render-process-gone', { reason: 'oom' })
    expect(w.loads).toBe(2)
    // The reload loads, then the renderer dies again on its first steps: the
    // finished load alone no longer clears the count.
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    vi.advanceTimersByTime(59_000)
    w.fire('render-process-gone', { reason: 'crashed' })
    expect(w.loads).toBe(2)
    expect(m.errors).toHaveLength(1)
    expect(m.quits).toBe(1)
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
  defaultShimPath: () => string
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

  it('before-quit destroys the panel so a non-closable window cannot veto the quit (§13)', async () => {
    if (!caps.trayPanel) return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain()
    m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(m.wins).toHaveLength(1)
    const panel = m.wins[0]
    // A quit of our own (here quit-entirely with nothing to stop) passes
    // through before-quit. The panel is closable: false (Cmd+W must be a
    // no-op for it) — and app.quit() is cancelled by any window that refuses
    // to close. Once the panel had been opened, Cmd+Q and quit-all both died
    // right after before-quit, with the polls stopped and the app resident.
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    expect(m.emit('before-quit')).toBe(false)
    expect(panel.destroys).toBe(1)
    // Idempotent: a second before-quit (app.quit() called twice) never
    // touches the dead window.
    m.emit('before-quit')
    expect(panel.destroys).toBe(1)
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
    expect(src).toMatch(/if \(await executionsLive\(\)\) return \{ busy: true \}[\s\S]{0,2000}quitAndInstall\(\)/)
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
    // A signal name is never reported as the failure, and the spawn carried
    // the bound plus a capped buffer and the §3 no-bytecode-writes environment
    // (the app's own environment plus PYTHONDONTWRITEBYTECODE=1).
    expect(options).toHaveLength(1)
    const { env, ...bounds } = options[0]
    expect(bounds).toEqual({ windowsHide: true, timeout: 120_000, maxBuffer: 4 * 1024 * 1024 })
    expect(env).toEqual({ ...process.env, PYTHONDONTWRITEBYTECODE: '1' })
    // §3: the app stays up on any stop failure.
    expect(m.quits).toBe(0)
  })

  it('quit-all ends the process outright if the quit is vetoed (§3)', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ execFile: (_py, _args, _o, cb) => { cb(null, 'stopped', '') } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    // A normal quit first (before-quit runs: polls stop, the panel goes).
    expect(m.quits).toBe(1)
    expect(m.exits).toEqual([])
    // The backend is already stopped, so a UI that is somehow still here 2 s
    // later is a UI resident on a dead backend — end it.
    vi.advanceTimersByTime(1999)
    expect(m.exits).toEqual([])
    vi.advanceTimersByTime(1)
    expect(m.exits).toEqual([0])
    expect(m.log()).toContain('quit-all: quit was vetoed — exiting')
  })

  it('an OS quit with a loaded window is held and handed to the renderer (§3)', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    const m = loadMain({ execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    w.minimize()
    // Cmd+Q / the dock's Quit: before-quit arrives with nothing of ours having
    // asked — it is held, the window is brought up, the renderer is asked.
    expect(m.emit('before-quit')).toBe(true)
    expect(m.sent).toEqual([['quit-requested', undefined]])
    expect(w.restores).toBe(1)
    expect(w.shows).toBe(2)
    expect(m.quits).toBe(0)
    expect(stops).toEqual([])
    // A repeated Cmd+Q while the ask is out changes nothing.
    expect(m.emit('before-quit')).toBe(true)
    expect(m.sent).toHaveLength(1)
    // The renderer takes over through the same quit-all IPC the QUIT card
    // uses; the native fallback stands down.
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    expect(stops).toEqual([['-m', 'autowright.service', 'stop']])
    expect(m.quits).toBe(1)
    expect(m.log()).toContain('quit-all: backend stopped, quitting app')
    // …and that final quit passes straight through before-quit.
    expect(m.emit('before-quit')).toBe(false)
    vi.advanceTimersByTime(5000)
    expect(m.dialogs).toEqual([])
    expect(stops).toHaveLength(1)
  })

  it('an OS quit the renderer never answers falls back to the native flow (§3)', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    const m = loadMain({ execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    try {
      m.invoke('open-app', '/app')
      m.wins[0].fire('did-start-loading')
      m.wins[0].fire('did-finish-load')
      expect(m.emit('before-quit')).toBe(true)
      expect(m.sent).toEqual([['quit-requested', undefined]])
      await vi.advanceTimersByTimeAsync(1499)
      expect(stops).toEqual([])
      await vi.advanceTimersByTimeAsync(1)
      await vi.advanceTimersByTimeAsync(1000)
      // Unreachable backend counts as idle: no dialog, straight to the stop.
      expect(stops).toEqual([['-m', 'autowright.service', 'stop']])
      expect(m.dialogs).toEqual([])
      expect(m.quits).toBe(1)
      expect(m.log()).toContain('quit: the renderer did not take over — quitting natively')
      expect(m.log()).toContain('quit: backend stopped, quitting app')
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('a windowless OS quit under a live execution asks natively; Cancel keeps everything up (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    const m = loadMain({ execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    // Up but answering 500: the gate reads busy.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 500 } as Response)
    try {
      // No window at all (the dock's Quit with the window closed).
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.dialogs).toHaveLength(1))
      const [kind, opts] = m.dialogs[0]
      expect(kind).toBe('message')
      expect(opts).toMatchObject({
        type: 'warning',
        message: 'An automation is executing',
        detail: 'Shut down everything and quit? The running automation will be killed.',
        buttons: ['Shut down and quit', 'Cancel'],
        cancelId: 1,
      })
      // Cancel (the default answer): nothing stops, nothing quits.
      await vi.waitFor(() => expect(m.log()).toContain('quit: cancelled at the busy confirm'))
      expect(stops).toEqual([])
      expect(m.quits).toBe(0)
      // Shut down and quit: the forced stop runs and the app quits.
      m.dialogAnswer.response = 0
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.quits).toBe(1))
      expect(stops).toEqual([['-m', 'autowright.service', 'stop']])
      expect(m.dialogs).toHaveLength(2)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('a native stop failure keeps the app up and says so in an error box (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ execFile: (_py, _args, _o, cb) => {
      cb(Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed: launchd still reports the job', '')
    } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    try {
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.errors).toHaveLength(1))
      expect(m.errors[0][1]).toContain("couldn't stop its backend, so it stays open")
      expect(m.errors[0][1]).toContain('stop failed: launchd still reports the job')
      expect(m.quits).toBe(0)
      // The interlock is released: a later quit-all can run its own stop.
      expect(await m.invoke('quit-all', { force: true })).toEqual({ error: 'stop failed: launchd still reports the job' })
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('the native busy dialog is inside the one flight: a second quit opens no second dialog (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    let answer: (r: { response: number }) => void = () => {}
    const m = loadMain({
      execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') },
      showMessageBox: () => new Promise((resolve) => { answer = resolve }),
    })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    // Up but answering 500: the gate reads busy.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 500 } as Response)
    try {
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.dialogs).toHaveLength(1))
      // While the dialog is up: a second Cmd+Q / SIGINT is a no-op, and a late
      // quit-all from the renderer answers without a stop or a force modal.
      expect(m.emit('before-quit')).toBe(true)
      // §3: a late quit-all answers success — never an error the renderer
      // would toast over the overlay of a quit that is going ahead.
      const late = await m.invoke('quit-all', {}) as Record<string, unknown>
      expect(late).toEqual({ ok: true })
      expect(late).not.toHaveProperty('error')
      await new Promise((resolve) => setTimeout(resolve, 20))
      expect(m.dialogs).toHaveLength(1)
      expect(stops).toEqual([])
      // Shut down and quit: the one forced stop runs, the app quits.
      answer({ response: 0 })
      await vi.waitFor(() => expect(m.quits).toBe(1))
      expect(stops).toEqual([['-m', 'autowright.service', 'stop']])
      expect(m.dialogs).toHaveLength(1)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('a Cancel at the native busy dialog pushes quit-cancelled to the window (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    const m = loadMain({
      execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') },
      showMessageBox: async () => ({ response: 1 }),
    })
    // A window that never loaded: the OS quit runs the native flight.
    m.invoke('open-app', '/app')
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    // Up but answering 500: the gate reads busy.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 500 } as Response)
    try {
      expect(m.emit('before-quit')).toBe(true)
      // A late quit-all answered { ok } and left the overlay up — the
      // stand-down tells the renderer to drop it.
      await vi.waitFor(() => expect(m.sent).toContainEqual(['quit-cancelled', undefined]))
      expect(m.log()).toContain('quit: cancelled at the busy confirm')
      expect(stops).toEqual([])
      expect(m.quits).toBe(0)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('a native stop failure pushes quit-cancelled to the window too (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ execFile: (_py, _args, _o, cb) => {
      cb(Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed: launchd still reports the job', '')
    } })
    m.invoke('open-app', '/app')
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    try {
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.errors).toHaveLength(1))
      expect(m.sent).toContainEqual(['quit-cancelled', undefined])
      expect(m.quits).toBe(0)
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('with no backend to stop (dev, no discovery file) quit-all quits the UI instead of refusing (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const stops: string[][] = []
    const m = loadMain({ execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, '', '') } })
    // No backend.json and no bundled interpreter: nothing is running here.
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    expect(stops).toEqual([])
    expect(m.quits).toBe(1)
    expect(m.log()).toContain('quit-all: no backend to stop — quitting app')
  })

  it('a session end passes the quit through untouched (§3)', () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ ready: true })
    return vi.waitFor(() => {
      const on = m.powerListeners.get('shutdown')
      expect(on).toBeTypeOf('function')
      on!()
      // Logout: the UI quits, the backend is left to launchd — no ask, no stop.
      expect(m.emit('before-quit')).toBe(false)
      expect(m.sent).toEqual([])
    })
  })

  it('a wedged install child cannot hang the stop that waits for it', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    try {
      // No backend.json and no registration, so ensure-backend goes on to
      // `service install` — and that child never calls back. quit-all used to
      // sit on `await serviceInstallDone` for the life of the app.
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => { if (args[2] === 'status') cb(null, 'not installed', '') },
      })
      // Two 500 ms gaps: with no backend.json the probe still runs its three
      // attempts (§3 re-reads the file each one) before concluding absent.
      await vi.advanceTimersByTimeAsync(1000)
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

// ---- §3 /health probe retries ----------------------------------------------
// A backend busy under its store lock can miss a probe. Concluding
// "unreachable" from one miss made the launch bootout a live backend, so the
// probe asks three times before it believes the answer.

describe('main.cjs /health probe retries (§3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('two missed probes followed by a healthy one never install the service', async () => {
    vi.useFakeTimers()
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    let probes = 0
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (!String(input).includes('/health')) return Promise.reject(new Error('offline in tests'))
      probes += 1
      if (probes <= 2) return Promise.reject(new Error('busy under the store lock'))
      // The app's own version (the stub's getVersion), so nothing version-syncs.
      return Promise.resolve({ ok: true, json: async () => ({ app: 'Autowright', version: '0.0.0' }) } as Response)
    })
    const children: string[][] = []
    try {
      const m = loadMain({ ready: true, execFile: (_py, args) => { children.push(args) } })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      // Two 500 ms gaps between the three attempts.
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(500)
      await vi.advanceTimersByTimeAsync(500)
      expect(probes).toBe(3)
      expect(await m.invoke('backend-status')).toEqual({ state: 'ok', detail: '' })
      expect(children).toEqual([])
    } finally {
      fetchSpy.mockRestore()
      rmSync(resources, { recursive: true, force: true })
    }
  })

  // §3: when every probe goes unanswered, ensure-backend asks `service status`
  // before reinstalling. A bundled interpreter makes this a packaged launch.
  function withBundledPython(): string {
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    return resources
  }

  it('a job the service manager reports active is waited for, never reinstalled', async () => {
    vi.useFakeTimers()
    const resources = withBundledPython()
    let healthy = false
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (!String(input).includes('/health') || !healthy) return Promise.reject(new Error('still coming up'))
      return Promise.resolve({ ok: true, json: async () => ({ app: 'Autowright', version: '0.0.0' }) } as Response)
    })
    const children: string[][] = []
    try {
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => { children.push(args); cb(null, 'active (pid 4242) · port 65000\n', '') },
      })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      // The first probe's three unanswered attempts, then the status ask.
      await vi.advanceTimersByTimeAsync(1000)
      expect(children).toEqual([['-m', 'autowright.service', 'status']])
      expect(m.log()).toContain('reports the job active — waiting for /health')
      // A few seconds later the backend answers: 'ok', and no install ever ran.
      await vi.advanceTimersByTimeAsync(6000)
      healthy = true
      await vi.advanceTimersByTimeAsync(4000)
      expect(await m.invoke('backend-status')).toEqual({ state: 'ok', detail: '' })
      await vi.advanceTimersByTimeAsync(30_000)
      expect(children).toEqual([['-m', 'autowright.service', 'status']])
    } finally {
      fetchSpy.mockRestore()
      rmSync(resources, { recursive: true, force: true })
    }
  })

  it('an active job still silent after 20 s is reinstalled', async () => {
    vi.useFakeTimers()
    const resources = withBundledPython()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('silent'))
    const children: string[][] = []
    try {
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => {
          children.push(args)
          if (args[2] === 'status') cb(null, 'active (pid 4242)\n', '')
        },
      })
      await vi.advanceTimersByTimeAsync(1000)
      expect(children).toEqual([['-m', 'autowright.service', 'status']])
      // Still inside the 20 s window: no install yet.
      await vi.advanceTimersByTimeAsync(15_000)
      expect(children).toHaveLength(1)
      await vi.advanceTimersByTimeAsync(10_000)
      expect(children).toEqual([
        ['-m', 'autowright.service', 'status'],
        ['-m', 'autowright.service', 'install'],
      ])
      expect(m.log()).toContain('did not answer /health within 20 s — installing')
    } finally {
      fetchSpy.mockRestore()
      rmSync(resources, { recursive: true, force: true })
    }
  })

  for (const line of ['stopped (plist present) — returns at next login or app launch', 'not installed',
    'loaded, not active (pid -)', 'launchctl timed out']) {
    it(`a registration that says "${line}" is installed right away`, async () => {
      vi.useFakeTimers()
      const resources = withBundledPython()
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('nothing listening'))
      const children: string[][] = []
      try {
        const m = loadMain({
          ready: true,
          execFile: (_py, args, _o, cb) => {
            children.push(args)
            if (args[2] === 'status') cb(null, `${line}\n`, '')
          },
        })
        await vi.advanceTimersByTimeAsync(1000)
        expect(children).toEqual([
          ['-m', 'autowright.service', 'status'],
          ['-m', 'autowright.service', 'install'],
        ])
        expect(await m.invoke('backend-status')).toEqual({ state: 'installing', detail: '' })
      } finally {
        fetchSpy.mockRestore()
        rmSync(resources, { recursive: true, force: true })
      }
    })
  }
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

  it('a symlink at the shim path is foreign — never read, written or removed through (§3)', async () => {
    if (process.platform === 'win32') return // symlinks need elevation there
    const { m, shim } = loadWithShim()
    // A link to a file carrying our own marker still counts as foreign: every
    // operation would land wherever the link points.
    const target = join(shimDir!, 'elsewhere')
    writeFileSync(target, platMod.shimText('/old/python'), { mode: 0o644 })
    symlinkSync(target, shim)
    expect(await m.invoke('cli-status')).toMatchObject({ state: 'foreign', path: shim })
    expect(await m.invoke('cli-install')).toEqual({
      ok: false,
      error: `A different autowright command already exists at ${shim}. Remove it first.`,
    })
    expect(await m.invoke('cli-uninstall')).toEqual({ ok: true })
    expect(lstatSync(shim).isSymbolicLink()).toBe(true)
    expect(readFileSync(target, 'utf-8')).toBe(platMod.shimText('/old/python'))
    expect(statSync(target).mode & 0o777).toBe(0o644)
  })

  it('a dangling symlink at the shim path is foreign, not missing (§3)', async () => {
    if (process.platform === 'win32') return // symlinks need elevation there
    const { m, shim } = loadWithShim()
    const target = join(shimDir!, 'nowhere')
    symlinkSync(target, shim)
    expect(await m.invoke('cli-status')).toMatchObject({ state: 'foreign', path: shim })
    expect((await m.invoke('cli-install') as { ok: boolean }).ok).toBe(false)
    // The install never created the file the link points at.
    expect(existsSync(target)).toBe(false)
  })

  it('a missing shim path still reads as missing', async () => {
    const { m, shim } = loadWithShim()
    expect(await m.invoke('cli-status')).toMatchObject({ state: 'missing', path: shim })
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

  it('cli-status heals a current but non-executable shim of ours in place', async () => {
    if (process.platform === 'win32') return // no executable bit there
    const { m, shim } = loadWithShim()
    writeFileSync(shim, platMod.shimText('/usr/bin/python3'), { mode: 0o644 })
    chmodSync(shim, 0o644)
    expect(await m.invoke('cli-status')).toMatchObject({ state: 'installed', path: shim })
    expect(statSync(shim).mode & 0o777).toBe(0o755)
  })

  it('cli-install makes an existing non-executable shim of ours executable', async () => {
    if (process.platform === 'win32') return // no executable bit there
    const { m, shim } = loadWithShim()
    // Ours and current, but 0644: a write over an existing file keeps its old
    // mode, so the install sets 0755 after every write (§3).
    writeFileSync(shim, platMod.shimText('/usr/bin/python3'), { mode: 0o644 })
    chmodSync(shim, 0o644)
    expect(await m.invoke('cli-install')).toEqual({ ok: true })
    expect(statSync(shim).mode & 0o777).toBe(0o755)
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
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('a quitAndInstall that refuses on the error stream reports the updater\'s own error', async () => {
    if (!HAS_UPDATER) return // no feed here — the no-updates line is covered above
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain()
    // The NSIS/AppImage shape: the failure goes out on the error stream and
    // quitAndInstall returns nothing — never false.
    m.updater.installError = new Error('spawn Autowright Setup.exe ENOENT')
    m.updater.installResult = undefined
    expect(await m.invoke('update-install'))
      .toEqual({ error: 'spawn Autowright Setup.exe ENOENT' })
    expect(m.quits).toBe(0)
    expect(m.log()).toContain('update: install refused: spawn Autowright Setup.exe ENOENT')
    // uiQuit was cleared on the refusal: a later Cmd+Q is the user's Quit
    // again, held and run through quit-entirely.
    expect(m.emit('before-quit')).toBe(true)
  })

  it('an install with nothing on the error stream is a quit underway, not a refusal', async () => {
    if (!HAS_UPDATER) return
    const m = loadMain()
    expect(await m.invoke('update-install')).toEqual({ ok: true })
    // uiQuit stays set: the updater's own quit passes through before-quit.
    expect(m.emit('before-quit')).toBe(false)
  })

  // §3 win32: a packaged launch (bundled interpreter) whose platform stops the
  // backend before the installer runs. Every /health probe is recorded — the
  // launch's ensure-backend opens with one, so a probe after the answer is
  // the backend being brought back.
  function win32Install(execFile: ServiceChildStub) {
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    const health: string[] = []
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/health')) health.push(String(input))
      return Promise.reject(new Error('offline in tests'))
    })
    const m = loadMain({ platform: { stopBackendBeforeInstall: true }, execFile })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 4321, token: 'token', python: '/usr/bin/python3' }))
    const done = () => {
      fetchSpy.mockRestore()
      rmSync(resources, { recursive: true, force: true })
    }
    return { m, health, done }
  }

  it('on win32 a staged installer stops the backend, waits out the process-tree gap, then installs (§3)', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    const order: string[] = []
    const { m, done } = win32Install((_py, args, _o, cb) => { order.push(`service ${args[2]}`); cb(null, 'stopped', '') })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      m.updater.onInstall = () => { order.push('quitAndInstall') }
      const answer = m.invoke('update-install') as Promise<unknown>
      await vi.advanceTimersByTimeAsync(1_499)
      expect(order).toEqual(['service stop'])
      await vi.advanceTimersByTimeAsync(1)
      expect(await answer).toEqual({ ok: true })
      expect(order).toEqual(['service stop', 'quitAndInstall'])
    } finally {
      done()
    }
  })

  it('macOS and Linux leave the backend running across the install (§3)', async () => {
    if (!HAS_UPDATER) return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    try {
      const run = async (flag: unknown) => {
        const order: string[] = []
        const m = loadMain({
          platform: { stopBackendBeforeInstall: flag },
          execFile: (_py, args, _o, cb) => { order.push(`service ${args[2]}`); cb(null, 'stopped', '') },
        })
        writeFileSync(join(m.home, 'backend.json'),
          JSON.stringify({ port: 4321, token: 'token', python: '/usr/bin/python3' }))
        m.updater.installerPath = 'staged'
        m.updater.onInstall = () => { order.push('quitAndInstall') }
        expect(await m.invoke('update-install')).toEqual({ ok: true })
        return order
      }
      const darwin = realRequire('./platform/darwin.cjs') as { stopBackendBeforeInstall: unknown }
      const linux = realRequire('./platform/linux.cjs') as { stopBackendBeforeInstall: unknown }
      const win32 = realRequire('./platform/win32.cjs') as { stopBackendBeforeInstall: unknown }
      expect(win32.stopBackendBeforeInstall).toBe(true)
      expect(await run(darwin.stopBackendBeforeInstall)).toEqual(['quitAndInstall'])
      expect(await run(linux.stopBackendBeforeInstall)).toEqual(['quitAndInstall'])
    } finally {
      fetchSpy.mockRestore()
      restoreResourcesPath()
    }
  })

  it('on win32 with nothing staged nothing is stopped and the updater\'s own refusal answers (§3)', async () => {
    if (!HAS_UPDATER) return
    const stops: string[][] = []
    const { m, done } = win32Install((_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') })
    try {
      // BaseUpdater.install() with no installer dispatches this and returns.
      m.updater.installError = new Error('No update filepath provided, can\'t quit and install')
      expect(await m.invoke('update-install'))
        .toEqual({ error: 'No update filepath provided, can\'t quit and install' })
      expect(stops).toEqual([])
    } finally {
      done()
    }
  })

  it('a failed win32 stop answers { error } and installs nothing; the backend is brought back (§3)', async () => {
    if (!HAS_UPDATER) return
    const { m, health, done } = win32Install((_py, args, _o, cb) => {
      if (args[2] === 'stop') cb(new Error('boom'), '', 'boom')
    })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      expect(await m.invoke('update-install'))
        .toEqual({ error: 'couldn\'t stop the background service — try again' })
      expect(m.updater.installs).toBe(0)
      expect(m.log()).toContain('update-install: backend stop failed: boom')
      // The launch's ensure-backend ran again: it opens with a /health probe.
      await vi.waitFor(() => expect(health.length).toBeGreaterThan(0))
      // uiQuit was never set: a later Cmd+Q is still the user's Quit.
      expect(m.emit('before-quit')).toBe(true)
    } finally {
      done()
    }
  })

  it('a win32 install refused after a good stop brings the backend back before answering (§3)', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    const { m, health, done } = win32Install((_py, _args, _o, cb) => { cb(null, 'stopped', '') })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      m.updater.installError = new Error('spawn Autowright Setup.exe ENOENT')
      const answer = m.invoke('update-install') as Promise<unknown>
      // Nothing probed the backend on the way in (the stop and the gap).
      expect(health).toEqual([])
      await vi.advanceTimersByTimeAsync(1_500)
      expect(await answer).toEqual({ error: 'spawn Autowright Setup.exe ENOENT' })
      expect(health.length).toBeGreaterThan(0)
    } finally {
      done()
    }
  })

  it('a win32 install that throws after a good stop brings the backend back too (§3)', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    const { m, health, done } = win32Install((_py, _args, _o, cb) => { cb(null, 'stopped', '') })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      m.updater.installThrows = new Error('installer locked')
      const answer = m.invoke('update-install') as Promise<unknown>
      // Nothing probed the backend on the way in (the stop and the gap).
      expect(health).toEqual([])
      await vi.advanceTimersByTimeAsync(1_500)
      expect(await answer).toEqual({ error: 'installer locked' })
      expect(health.length).toBeGreaterThan(0)
    } finally {
      done()
    }
  })

  it('a win32 install with no quit within 10 s brings the backend back (§3)', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    const { m, health, done } = win32Install((_py, _args, _o, cb) => { cb(null, 'stopped', '') })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      const answer = m.invoke('update-install') as Promise<unknown>
      await vi.advanceTimersByTimeAsync(1_500)
      expect(await answer).toEqual({ ok: true })
      expect(health).toEqual([])
      await vi.advanceTimersByTimeAsync(10_000)
      expect(m.log()).toContain('update-install: no quit began within 10 s')
      expect(health.length).toBeGreaterThan(0)
    } finally {
      done()
    }
  })

  it('update-install is single-flight: a second call while one is pending shares its answer (§3)', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    const stops: string[][] = []
    const { m, done } = win32Install((_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') })
    try {
      m.updater.installerPath = 'Autowright Setup.exe'
      const first = m.invoke('update-install')
      const second = m.invoke('update-install')
      expect(second).toBe(first)
      await vi.advanceTimersByTimeAsync(1_500)
      expect(await first).toEqual({ ok: true })
      expect(stops).toHaveLength(1)
      expect(m.updater.installs).toBe(1)
    } finally {
      done()
    }
  })

  it('update-install destroys the panel before quitAndInstall (§3/§13)', async () => {
    if (!HAS_UPDATER || !caps.trayPanel) return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    try {
      const m = loadMain()
      m.invoke('apply-settings', { menuBarIcon: true })
      m.clickTray()
      expect(m.wins).toHaveLength(1)
      const panel = m.wins[0]
      // Squirrel walks the windows closed before before-quit ever runs, so
      // the non-closable panel must already be gone when the call is made.
      let destroysAtInstall = -1
      m.updater.onInstall = () => { destroysAtInstall = panel.destroys }
      expect(await m.invoke('update-install')).toEqual({ ok: true })
      expect(m.updater.installs).toBe(1)
      expect(destroysAtInstall).toBe(1)
      // The next panel starts fresh — the tray click builds a new window.
      m.clickTray()
      expect(m.wins).toHaveLength(2)
    } finally {
      restoreResourcesPath()
    }
  })

  it('a throwing install is answered, never left to reject the IPC', async () => {
    if (!HAS_UPDATER) return
    const m = loadMain()
    m.updater.installThrows = new Error('ShipIt is missing')
    expect(await m.invoke('update-install')).toEqual({ error: 'ShipIt is missing' })
  })

  it('an ok install with no quit within 10 s clears uiQuit and logs the stall', async () => {
    if (!HAS_UPDATER) return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.useFakeTimers()
    try {
      const m = loadMain()
      // MacUpdater with nothing staged: registers a listener and returns, so
      // the answer is ok but no quit ever begins.
      expect(await m.invoke('update-install')).toEqual({ ok: true })
      await vi.advanceTimersByTimeAsync(9_999)
      expect(m.log()).not.toContain('update-install: no quit began within 10 s')
      await vi.advanceTimersByTimeAsync(1)
      expect(m.log()).toContain('update-install: no quit began within 10 s')
      // uiQuit is cleared: the next Cmd+Q is the user's quit-entirely again.
      expect(m.emit('before-quit')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('an ok install whose quit began leaves uiQuit alone at the 10 s check', async () => {
    if (!HAS_UPDATER) return
    vi.useFakeTimers()
    try {
      const m = loadMain()
      expect(await m.invoke('update-install')).toEqual({ ok: true })
      // The updater's own quit passes through before-quit.
      expect(m.emit('before-quit')).toBe(false)
      await vi.advanceTimersByTimeAsync(10_000)
      expect(m.log()).not.toContain('update-install: no quit began within 10 s')
    } finally {
      vi.useRealTimers()
    }
  })
})

// ---- §3 a second macOS download ----------------------------------------------
// electron-updater's squirrelDownloadedUpdate flag never resets, so a second
// download for a newer version would "stage" on it and install the older
// bundle Squirrel already holds.

describe('main.cjs second macOS download (§3)', () => {
  const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM

  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
  })

  it('a download for a different version than the staged one asks for a restart', async () => {
    if (platMod.UPDATER !== 'mac') return // only Squirrel.Mac stages
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.check = { updateInfo: { version: '0.14.0' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    // The same version again still resolves on the staged bundle.
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    // A newer release lands after 0.14.0 was staged.
    m.updater.check = { updateInfo: { version: '0.14.1' } }
    expect(await m.invoke('update-check')).toEqual({ state: 'available', version: '0.14.1' })
    const downloadsBefore = m.updater.downloads
    expect(await m.invoke('update-download'))
      .toEqual({ error: 'Restart to finish the update you already downloaded.' })
    expect(m.updater.downloads).toBe(downloadsBefore)
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
    // The rotation race: the stat sees the file at its old size, so the buffer
    // is bigger than anything the read can fill.
    const m = loadMain({
      fs: {
        promises: {
          ...realFs.promises,
          open: async (file: string, flags: string) => {
            const handle = await realFs.promises.open(file, flags)
            return {
              stat: async () => {
                const st = await handle.stat()
                return { ...st, size: st.size + 200 }
              },
              read: handle.read.bind(handle),
              close: handle.close.bind(handle),
            }
          },
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

  it('answers every readable log, newest 64 KB only, and skips the missing ones', async () => {
    const m = loadMain()
    mkdirSync(join(m.home, 'logs'), { recursive: true })
    writeFileSync(join(m.home, 'logs', 'app.log'), 'app line\n')
    // Over the 64 KB bound: the tail starts after the first newline inside the
    // window, so a half line never opens the overlay's text.
    const long = `${'x'.repeat(70 * 1024)}\nlast line\n`
    writeFileSync(join(m.home, 'logs', 'backend.out.log'), long)
    const out = await m.invoke('tail-logs') as { name: string, text: string }[]
    // backend.err.log and vite.log were never written — no entry at all.
    expect(out.map((f) => f.name)).toEqual(['app.log', 'backend.out.log'])
    expect(out[0].text).toBe('app line\n')
    expect(out[1].text).toBe('last line\n')
  })
})

// ---- §9.3 request logs ---------------------------------------------------

describe('main.cjs request-log handlers (§9.3)', () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('lists .log basenames newest first and reads one by name', async () => {
    const m = loadMain()
    const dir = join(m.home, 'logs', 'requests')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, '20260101-000000-aaa.log'), 'older')
    writeFileSync(join(dir, '20260102-000000-bbb.log'), 'newer')
    writeFileSync(join(dir, 'notes.txt'), 'not a request log')
    expect(await m.invoke('list-request-logs'))
      .toEqual(['20260102-000000-bbb.log', '20260101-000000-aaa.log'])
    expect(await m.invoke('read-request-log', '20260101-000000-aaa.log')).toBe('older')
    // `name` must be a plain basename, and an unreadable one answers null
    // rather than throwing into the overlay.
    expect(await m.invoke('read-request-log', '../app.log')).toBe(null)
    expect(await m.invoke('read-request-log', 42)).toBe(null)
    expect(await m.invoke('read-request-log', 'gone.log')).toBe(null)
  })

  it('a missing requests directory lists empty', async () => {
    const m = loadMain()
    expect(await m.invoke('list-request-logs')).toEqual([])
  })

  it('the developer-log handlers never block the main process (§2 async IO)', () => {
    // The overlay polls at 1 Hz while it is open: a sync open/stat/read of
    // four log files (plus the requests directory) would stall every frame,
    // every timer and every other IPC call behind it.
    const from = src.indexOf("ipcMain.handle('tail-logs'")
    const to = src.indexOf("ipcMain.handle('platform-info'")
    expect(from).toBeGreaterThan(0)
    expect(to).toBeGreaterThan(from)
    const block = src.slice(from, to)
    expect(block).toContain("ipcMain.handle('list-request-logs'")
    expect(block).toContain("ipcMain.handle('read-request-log'")
    expect(block).not.toMatch(/Sync\(/)
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

  it('a blur that lands after the panel is destroyed hides nothing (§13)', async () => {
    if (!caps.trayPanel) return // §13: Linux ships no tray surface at all
    const m = loadMain()
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    const panel = m.wins[0]
    // §4.9 "Show in the menu bar" off destroys the panel and drops the
    // module-level reference - the blur the OS still delivers must act on the
    // window the handler was armed for, and only while it is alive.
    await m.invoke('apply-settings', { menuBarIcon: false })
    expect(panel.destroys).toBe(1)
    expect(() => panel.blur()).not.toThrow()
    expect(panel.hides).toBe(0)
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

  it('a panel whose renderer dies is destroyed, and the next click builds a fresh one', async () => {
    if (!caps.trayPanel) return // §13: Linux ships no tray surface at all
    const m = loadMain()
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(m.wins).toHaveLength(1)
    m.wins[0].fire('render-process-gone', { reason: 'crashed' })
    expect(m.wins[0].destroys).toBe(1)
    expect(m.log()).toContain('panel: renderer process gone (crashed)')
    m.clickTray()
    expect(m.wins).toHaveLength(2)
  })

  it("an old panel's late closed leaves the new panel's reference intact", async () => {
    if (!caps.trayPanel) return // §13: Linux ships no tray surface at all
    const m = loadMain()
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    const old = m.wins[0]
    // Destroy-then-recreate: tray off and on, then a click builds panel two.
    await m.invoke('apply-settings', { menuBarIcon: false })
    await m.invoke('apply-settings', { menuBarIcon: true })
    m.clickTray()
    expect(m.wins).toHaveLength(2)
    // The old window's `closed` lands late; it must not wipe panel two.
    old.close()
    m.clickTray()
    expect(m.wins).toHaveLength(2)
    expect(m.wins[1].shows).toBe(2)
  })
})

// ---- §9 no window once a quit has begun -------------------------------------

describe('main.cjs no window or panel once a quit has begun (§9)', () => {
  afterEach(() => {
    vi.useRealTimers()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('second-instance, activate and a tray click create no window while quitting', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const m = loadMain({ ready: true })
    await vi.advanceTimersByTimeAsync(0)
    if (caps.trayPanel) await m.invoke('apply-settings', { menuBarIcon: true })
    // The window is closed (tray/dock-only), so every path below would
    // otherwise build a fresh one.
    m.wins[0].close()
    const before = m.windows.length
    // A session end lets the quit through, which sets the quit flag.
    m.powerListeners.get('shutdown')!()
    expect(m.emit('before-quit')).toBe(false)
    m.secondInstance()
    m.emit('activate')
    if (caps.trayPanel) m.clickTray()
    await m.invoke('open-app', '/app?automation=a1')
    expect(m.windows).toHaveLength(before)
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

  it('an OS quit during a reset is ignored and quit-all answers an error (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new Error('offline in tests'))
    const stops: ((err: ServiceChildError | null, stdout: string, stderr: string) => void)[] = []
    try {
      // The reset's own `service stop` is held until the test releases it.
      const m = loadMain({ execFile: (_py, _a, _o, cb) => { stops.push(cb) } })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      const reset = m.invoke('reset-all') as Promise<unknown>
      await vi.waitFor(() => expect(stops).toHaveLength(1))
      // Cmd+Q mid-reset: held (never a UI-only quit) but nothing runs — no
      // second stop racing the erase, no ask to the renderer.
      expect(m.emit('before-quit')).toBe(true)
      expect(m.log()).toContain('quit: ignored — a reset is in flight')
      expect(await m.invoke('quit-all', {})).toEqual({ error: 'a reset is in flight' })
      expect(stops).toHaveLength(1)
      expect(m.sent.filter(([channel]) => channel === 'quit-requested')).toEqual([])
      // A stop failure aborts the reset — and releases the guard.
      stops[0](Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed', '')
      expect(await reset).toEqual({ error: 'stop failed' })
      const quit = m.invoke('quit-all', { force: true }) as Promise<unknown>
      await vi.waitFor(() => expect(stops).toHaveLength(2))
      stops[1](null, 'stopped', '')
      expect(await quit).toEqual({ ok: true })
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('a reset that throws releases the guard: the next OS quit runs (§3)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    // The backend answers /settings with an executions dir (so deleteAllData
    // walks it) and is otherwise unreachable (idle gate, secrets best-effort).
    const dataPath = mkdtempSync(join(tmpdir(), 'aw-data-'))
    mkdirSync(join(dataPath, 'run-1'))
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).includes('/settings')) {
        return { ok: true, status: 200, json: async () => ({ dataPath }) } as Response
      }
      throw new Error('offline in tests')
    })
    const stops: string[][] = []
    try {
      const m = loadMain({
        execFile: (_py, args, _o, cb) => { stops.push(args); cb(null, 'stopped', '') },
        // deleteAllData's execution-dir probe throws (an unguarded fs failure).
        fs: {
          existsSync: (p: string) => {
            if (String(p).endsWith('execution.yaml')) throw new Error('EIO: i/o error')
            return (realRequire('fs') as typeof import('node:fs')).existsSync(p)
          },
        },
      })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
      await expect(m.invoke('reset-all') as Promise<unknown>).rejects.toThrow('EIO: i/o error')
      expect(m.exits).toEqual([])
      expect(stops).toHaveLength(1)
      // Cmd+Q is no longer ignored: the quit-entirely flow runs its own stop
      // and the app quits.
      expect(m.emit('before-quit')).toBe(true)
      await vi.waitFor(() => expect(m.quits).toBe(1))
      expect(stops).toHaveLength(2)
      // …and quit-all is no longer refused with the reset line.
      expect(await m.invoke('quit-all', { force: true })).not.toEqual({ error: 'a reset is in flight' })
    } finally {
      fetchSpy.mockRestore()
      rmSync(dataPath, { recursive: true, force: true })
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

  it('reset keeps the CLI shim directory when it lives inside the data root (§3 step 5)', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new Error('offline in tests'))
    try {
      const m = loadMain({ execFile: (_py, _a, _o, cb) => { cb(null, 'stopped', '') } })
      // The win32 placement: the shim sits in <data root>\bin.
      const shimSpy = vi.spyOn(platMod, 'defaultShimPath')
        .mockReturnValue(join(m.home, 'bin', 'autowright.cmd'))
      try {
        writeFileSync(join(m.home, 'backend.json'),
          JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
        mkdirSync(join(m.home, 'bin'))
        writeFileSync(join(m.home, 'bin', 'autowright.cmd'), 'shim')
        mkdirSync(join(m.home, 'automations'))
        writeFileSync(join(m.home, 'settings.yaml'), 'x: 1')
        expect(await m.invoke('reset-all')).toEqual({ ok: true })
        expect(existsSync(join(m.home, 'bin', 'autowright.cmd'))).toBe(true)
        expect(existsSync(join(m.home, 'automations'))).toBe(false)
        expect(existsSync(join(m.home, 'settings.yaml'))).toBe(false)
        expect(existsSync(join(m.home, 'backend.json'))).toBe(false)
      } finally {
        shimSpy.mockRestore()
      }
    } finally {
      fetchSpy.mockRestore()
    }
  })
})

// ---- §13 shell-posted notifications ---------------------------------------
// Main keeps one `role=shell` WebSocket to the backend while the OS
// notification API exists, shows each `notification.posted` event as an OS
// notification, and a click deep-links through showApp. The socket is a
// double planted as the global WebSocket — the Node global main.cjs uses.

class FakeSocket {
  static instances: FakeSocket[] = []
  url: string
  closed = false
  listeners = new Map<string, ((event: unknown) => void)[]>()
  constructor(url: string) { this.url = url; FakeSocket.instances.push(this) }
  addEventListener(type: string, fn: (event: unknown) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }

  close() { this.closed = true }
  emit(type: string, event: unknown = {}) { for (const fn of this.listeners.get(type) ?? []) fn(event) }
  message(payload: unknown) { this.emit('message', { data: typeof payload === 'string' ? payload : JSON.stringify(payload) }) }
}

describe('main.cjs shell notification socket (§13)', () => {
  const EXECUTION_ID = '9b2f4c1e-7a3d-4e5f-8a6b-1c2d3e4f5a6b'
  const AUTOMATION_ID = '123e4567-e89b-12d3-a456-426614174000'
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    FakeSocket.instances = []
    vi.stubGlobal('WebSocket', FakeSocket)
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    fetchSpy.mockRestore()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  const loadWithBackend = (options: LoadOptions = {}) => {
    const m = loadMain({ ready: true, ...options })
    // Written synchronously, before the ready chain's microtask runs.
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 4321, token: 'tok/en', python: '/usr/bin/python3' }))
    return m
  }

  const posted = (over: Record<string, unknown> = {}) => ({
    event: 'notification.posted', id: 'n1', kind: 'execution',
    title: 'Job failed', body: 'Step 2 raised an error',
    target: { page: 'execution', executionId: EXECUTION_ID }, ...over,
  })

  it('never connects when the OS notification API is unavailable', async () => {
    const m = loadWithBackend({ notificationSupported: false })
    await vi.waitFor(() => expect(m.wins).toHaveLength(1))
    await new Promise((r) => setTimeout(r, 20))
    expect(FakeSocket.instances).toHaveLength(0)
  })

  it('connects with the shell role and the backend.json token', async () => {
    loadWithBackend({ notificationSupported: true })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    expect(FakeSocket.instances[0].url).toBe('ws://127.0.0.1:4321/ws?token=tok%2Fen&role=shell')
  })

  it('a notification.posted event shows a notification whose click opens the execution', async () => {
    const m = loadWithBackend({ notificationSupported: true })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const socket = FakeSocket.instances[0]
    socket.emit('open')
    socket.message(posted())
    expect(m.notifications).toHaveLength(1)
    expect(m.notifications[0].options).toEqual({ title: 'Job failed', body: 'Step 2 raised an error' })
    expect(m.notifications[0].shows).toBe(1)
    m.notifications[0].click()
    // The ready chain's window has not loaded yet: the target is held and
    // replayed on the first successful load (the showApp deep-link rule).
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(m.sent).toContainEqual(['open-target', `/app?execution=${EXECUTION_ID}`])
  })

  it('an automation target opens the automation', async () => {
    const m = loadWithBackend({ notificationSupported: true })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    FakeSocket.instances[0].message(posted({
      kind: 'overdue', target: { page: 'automation', automationId: AUTOMATION_ID },
    }))
    m.notifications[0].click()
    expect(m.sent).toContainEqual(['open-target', `/app?automation=${AUTOMATION_ID}`])
  })

  it('malformed payloads and every other event are ignored', async () => {
    const m = loadWithBackend({ notificationSupported: true })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const socket = FakeSocket.instances[0]
    socket.message('not json {')
    socket.message({ event: 'execution.finished', title: 'x', body: 'y' })
    socket.message(posted({ title: 42 }))
    socket.message(posted({ body: null }))
    socket.message(posted({ target: { page: 'execution', executionId: '' } }))
    socket.message(posted({ target: { page: 'automation', automationId: 42 } }))
    socket.message(posted({ target: { page: 'settings' } }))
    socket.message(posted({ target: null }))
    socket.message(null)
    expect(m.notifications).toHaveLength(0)
  })

  it('any non-empty id shows the notification, its click deep-linking with the id encoded', async () => {
    const m = loadWithBackend({ notificationSupported: true })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    const socket = FakeSocket.instances[0]
    socket.message(posted({ target: { page: 'execution', executionId: 'my-report' } }))
    socket.message(posted({
      kind: 'overdue', target: { page: 'automation', automationId: AUTOMATION_ID.toUpperCase() },
    }))
    socket.message(posted({ target: { page: 'execution', executionId: '../a&b=c' } }))
    expect(m.notifications.map((n) => n.shows)).toEqual([1, 1, 1])
    for (const n of m.notifications) n.click()
    expect(m.sent).toEqual([
      ['open-target', '/app?execution=my-report'],
      ['open-target', `/app?automation=${AUTOMATION_ID.toUpperCase()}`],
      ['open-target', '/app?execution=..%2Fa%26b%3Dc'],
    ])
  })

  it('reconnects with the 1.5 s doubling backoff, reset after a connection comes back', async () => {
    vi.useFakeTimers()
    const m = loadWithBackend({ notificationSupported: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.instances).toHaveLength(1)
    FakeSocket.instances[0].emit('open')
    FakeSocket.instances[0].emit('close')
    await vi.advanceTimersByTimeAsync(1499)
    expect(FakeSocket.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(2)
    // An error and its close are one drop, never two retries.
    FakeSocket.instances[1].emit('error')
    FakeSocket.instances[1].emit('close')
    await vi.advanceTimersByTimeAsync(2999)
    expect(FakeSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(3)
    FakeSocket.instances[2].emit('open')
    FakeSocket.instances[2].emit('close')
    await vi.advanceTimersByTimeAsync(1500)
    expect(FakeSocket.instances).toHaveLength(4)
    // Logged once per outage, not per retry.
    const log = m.log()
    expect(log.match(/shell socket connected/g)).toHaveLength(2)
    expect(log.match(/shell socket closed/g)).toHaveLength(2)
  })

  it('the quit-entirely flow closes the socket before the backend is asked to stop', async () => {
    let closedAtStop: boolean | null = null
    const m = loadWithBackend({
      notificationSupported: true,
      execFile: (_py, args, _o, cb) => {
        if (args[2] === 'stop') closedAtStop = FakeSocket.instances[0]?.closed ?? null
        cb(null, 'stopped', '')
      },
    })
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    FakeSocket.instances[0].emit('open')
    expect(await m.invoke('quit-all', { force: true })).toEqual({ ok: true })
    expect(closedAtStop).toBe(true)
  })

  it('a failed stop keeps the app up and the socket comes back', async () => {
    vi.useFakeTimers()
    const m = loadWithBackend({
      notificationSupported: true,
      execFile: (_py, args, _o, cb) => { if (args[2] === 'stop') cb(new Error('boom'), '', 'boom') },
    })
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.instances).toHaveLength(1)
    const result = m.invoke('quit-all', { force: true }) as Promise<unknown>
    await vi.advanceTimersByTimeAsync(0)
    expect(await result).toEqual({ error: 'boom' })
    expect(FakeSocket.instances[0].closed).toBe(true)
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('a quit passing through before-quit closes the socket', async () => {
    vi.useFakeTimers()
    const m = loadWithBackend({ notificationSupported: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.instances).toHaveLength(1)
    m.powerListeners.get('shutdown')!()
    expect(m.emit('before-quit')).toBe(false)
    expect(FakeSocket.instances[0].closed).toBe(true)
    // …and no reconnect follows the close.
    FakeSocket.instances[0].emit('close')
    await vi.advanceTimersByTimeAsync(20_000)
    expect(FakeSocket.instances).toHaveLength(1)
  })
})

// ---- 2026-10-28 shell fixes ------------------------------------------------

describe('main.cjs AppImage relaunch lock retry (§9)', () => {
  const savedSilent = process.env.APPIMAGE_SILENT_INSTALL

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    restoreResourcesPath()
    if (savedSilent === undefined) delete process.env.APPIMAGE_SILENT_INSTALL
    else process.env.APPIMAGE_SILENT_INSTALL = savedSilent
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  function lockOnCall(n: number) {
    let calls = 0
    const lock = () => { calls += 1; return calls >= n }
    return { lock, calls: () => calls }
  }

  it('an updater relaunch retries the lock every 250 ms and starts once it wins', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    process.env.APPIMAGE_SILENT_INSTALL = 'true'
    const lock = lockOnCall(4)
    const m = loadMain({ ready: true, singleInstanceLock: lock.lock })
    await vi.advanceTimersByTimeAsync(0)
    expect(m.quits).toBe(0)
    expect(m.windows).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(500)
    expect(lock.calls()).toBe(3)
    expect(m.windows).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(250)
    expect(lock.calls()).toBe(4)
    expect(m.quits).toBe(0)
    expect(m.windows).toHaveLength(1)
  })

  it('an updater relaunch that never wins the lock gives up after 10 s', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    process.env.APPIMAGE_SILENT_INSTALL = 'true'
    const m = loadMain({ ready: true, singleInstanceLock: () => false })
    await vi.advanceTimersByTimeAsync(9_750)
    expect(m.quits).toBe(0)
    await vi.advanceTimersByTimeAsync(250)
    expect(m.quits).toBe(1)
    expect(m.windows).toHaveLength(0)
    // The quit is ours: before-quit lets it through.
    expect(m.emit('before-quit')).toBe(false)
  })

  it('any other loser quits at once', async () => {
    vi.useFakeTimers()
    delete process.env.APPIMAGE_SILENT_INSTALL
    const lock = lockOnCall(2)
    const m = loadMain({ ready: true, singleInstanceLock: lock.lock })
    expect(m.quits).toBe(1)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(lock.calls()).toBe(1)
    expect(m.windows).toHaveLength(0)
  })
})

describe('main.cjs staged-but-unknown macOS bundle (§3)', () => {
  const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM

  afterEach(() => {
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
  })

  it('a Squirrel flag already set with no recorded version refuses the download', async () => {
    if (platMod.UPDATER !== 'mac') return // only Squirrel.Mac stages
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.squirrelDownloadedUpdate = true
    expect(await m.invoke('update-download'))
      .toEqual({ error: 'Restart to finish the update you already downloaded.' })
    expect(m.updater.downloads).toBe(0)
  })
})

describe('main.cjs error boxes never block (§9/§3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('the watchdog\'s box is the async parentless one, and the handler returns without awaiting it', () => {
    vi.useFakeTimers()
    const m = loadMain()
    m.invoke('open-app', '/app')
    const w = m.wins[0]
    w.fire('did-start-loading')
    w.fire('did-fail-load', -6, 'ERR_FILE_NOT_FOUND', 'file:///dist/index.html', true)
    w.fire('did-finish-load')
    // The harness's error box never answers; a handler that awaited it would
    // stall the timers queued behind it.
    vi.advanceTimersByTime(15_000)
    expect(m.errors).toHaveLength(1)
    expect(m.errors[0][0]).toBe('Autowright')
    // The main process went on with the box still up: a late renderer still
    // shows the window.
    w.fire('did-start-loading')
    w.fire('did-finish-load')
    expect(w.shows).toBe(1)
    expect(src).not.toContain('showErrorBox')
  })
})

describe('main.cjs Windows session end (§3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('the main window\'s query-session-end lets the next before-quit through', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain({ ready: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(m.wins).toHaveLength(1)
    m.wins[0].windowEvent('query-session-end')
    expect(m.emit('before-quit')).toBe(false)
  })
})

describe('main.cjs tray that failed to create (§13)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
  })

  it('is not retried by the 60 s poll, only when menuBarIcon goes from off to on', async () => {
    if (!caps.trayPanel) return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (!String(input).includes('/settings')) return Promise.reject(new Error('offline in tests'))
      return Promise.resolve({ ok: true, json: async () => ({ menuBarIcon: true }) } as Response)
    })
    const m = loadMain({ ready: true, trayThrows: true })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    await vi.advanceTimersByTimeAsync(0)
    // The ready chain's attempt.
    expect(m.trayAttempts).toBe(1)
    await vi.advanceTimersByTimeAsync(3 * 60_000)
    expect(m.trayAttempts).toBe(1)
    // Still on: the renderer's push is no transition either.
    m.invoke('apply-settings', { menuBarIcon: true })
    expect(m.trayAttempts).toBe(1)
    // Off, then on again: the one retry.
    m.invoke('apply-settings', { menuBarIcon: false })
    m.invoke('apply-settings', { menuBarIcon: true })
    expect(m.trayAttempts).toBe(2)
  })
})

// ---- audit 2026-10-30: ensure-backend checks, macOS staging order, ---------
// ---- reset re-entrancy, quit-begun window guard (§3/§9) --------------------

describe('serviceChecks helpers (§3)', () => {
  const checks = realRequire(join(PLATFORM_DIR, 'serviceChecks.cjs')) as {
    serviceFailureDetail: (err: { message?: string } | null, stdout: string, stderr: string) => string
    healthVersion: (body: unknown, info: unknown) => string | null
  }

  it('the failure detail is the verb\'s stdout result line first, never the bare exec error', () => {
    const err = { message: 'Command failed: /py -m autowright.service install\n' }
    expect(checks.serviceFailureDetail(err, 'install failed: launchctl timed out\n', ''))
      .toBe('install failed: launchctl timed out')
    expect(checks.serviceFailureDetail(err, '', 'Traceback: boom\n')).toBe('Traceback: boom')
    expect(checks.serviceFailureDetail(err, '', '')).toBe('Command failed: /py -m autowright.service install')
  })

  it('/health is ours only with our app name, a version and the backend.json pid', () => {
    const info = { port: 65000, pid: 4242 }
    expect(checks.healthVersion({ app: 'Autowright', version: '1.2.3', pid: 4242 }, info)).toBe('1.2.3')
    // Another Autowright backend squatting the recorded port.
    expect(checks.healthVersion({ app: 'Autowright', version: '1.2.3', pid: 999 }, info)).toBeNull()
    expect(checks.healthVersion({ app: 'Autowright', version: '1.2.3' }, info)).toBeNull()
    expect(checks.healthVersion({ app: 'Other', version: '1.2.3', pid: 4242 }, info)).toBeNull()
    expect(checks.healthVersion({ app: 'Autowright', version: '', pid: 4242 }, info)).toBeNull()
    // A backend.json with no pid recorded keeps the name-and-version check.
    expect(checks.healthVersion({ app: 'Autowright', version: '1.2.3', pid: 999 }, { port: 65000 })).toBe('1.2.3')
    expect(checks.healthVersion(null, info)).toBeNull()
  })
})

describe('main.cjs audit 2026-10-30 (§3/§9)', () => {
  const savedCaskroom = process.env.AUTOWRIGHT_CASKROOM

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    restoreResourcesPath()
    if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
    else process.env.AUTOWRIGHT_HOME = savedHome
    if (savedCaskroom === undefined) delete process.env.AUTOWRIGHT_CASKROOM
    else process.env.AUTOWRIGHT_CASKROOM = savedCaskroom
  })

  function withBundledPython(): string {
    const resources = mkdtempSync(join(tmpdir(), 'aw-res-'))
    const bundled = platMod.bundledPythonPath(resources)
    mkdirSync(dirname(bundled), { recursive: true })
    writeFileSync(bundled, '#!/bin/sh\n')
    nodeProcess.resourcesPath = resources
    return resources
  }

  const commandFailed = () => Object.assign(
    new Error('Command failed: /py -m autowright.service install\n'), { code: 1 })

  it('a failed ensure-backend install records the verb\'s stdout reason', async () => {
    vi.useFakeTimers()
    const resources = withBundledPython()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('nothing listening'))
    try {
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => {
          if (args[2] === 'status') cb(null, 'not installed\n', '')
          else cb(commandFailed(), 'install failed: launchctl timed out\n', '')
        },
      })
      await vi.advanceTimersByTimeAsync(1000)
      expect(await m.invoke('backend-status'))
        .toEqual({ state: 'failed', detail: 'Backend install failed: install failed: launchctl timed out' })
      expect(m.log()).toContain('ensure-backend: install failed: install failed: launchctl timed out')
      expect(m.log()).not.toContain('Command failed')
    } finally {
      rmSync(resources, { recursive: true, force: true })
    }
  })

  it('a failed version-sync reinstall records failed with the stdout reason', async () => {
    vi.useFakeTimers()
    const resources = withBundledPython()
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/health')) {
        // An older backend than the app (the stub's getVersion is 0.0.0).
        return Promise.resolve({ ok: true, json: async () => ({ app: 'Autowright', version: '0.0.-1', pid: 4242 }) } as Response)
      }
      if (url.includes('/executions')) {
        return Promise.resolve({ ok: true, json: async () => ({ executions: [], total: 0 }) } as Response)
      }
      return Promise.reject(new Error('offline in tests'))
    })
    const children: string[][] = []
    try {
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => {
          children.push(args)
          cb(commandFailed(), 'install failed: couldn\'t write the plist\n', '')
        },
      })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3', pid: 4242 }))
      await vi.advanceTimersByTimeAsync(1000)
      expect(children).toEqual([['-m', 'autowright.service', 'install']])
      expect(await m.invoke('backend-status'))
        .toEqual({ state: 'failed', detail: 'Backend install failed: install failed: couldn\'t write the plist' })
      expect(m.log()).toContain('version-sync install failed: install failed: couldn\'t write the plist')
    } finally {
      rmSync(resources, { recursive: true, force: true })
    }
  })

  it('another Autowright backend on the recorded port (wrong pid) is not ours: ensure-backend installs', async () => {
    vi.useFakeTimers()
    const resources = withBundledPython()
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (!String(input).includes('/health')) return Promise.reject(new Error('offline in tests'))
      return Promise.resolve({ ok: true, json: async () => ({ app: 'Autowright', version: '0.0.0', pid: 999 }) } as Response)
    })
    const children: string[][] = []
    try {
      const m = loadMain({
        ready: true,
        execFile: (_py, args, _o, cb) => {
          children.push(args)
          if (args[2] === 'status') cb(null, 'not installed\n', '')
        },
      })
      writeFileSync(join(m.home, 'backend.json'),
        JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3', pid: 4242 }))
      await vi.advanceTimersByTimeAsync(1000)
      expect(children).toEqual([
        ['-m', 'autowright.service', 'status'],
        ['-m', 'autowright.service', 'install'],
      ])
    } finally {
      rmSync(resources, { recursive: true, force: true })
    }
  })

  it('macOS: update-download never engages Squirrel; update-install stages, then quits to install', async () => {
    if (platMod.UPDATER !== 'mac') return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.check = { updateInfo: { version: '0.14.0' } }
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    expect(m.updater.nativeChecks).toBe(0)
    expect(m.updater.squirrelDownloadedUpdate).toBe(false)
    let stagedAtInstall: boolean | null = null
    m.updater.onInstall = () => { stagedAtInstall = m.updater.squirrelDownloadedUpdate }
    expect(await m.invoke('update-install')).toEqual({ ok: true })
    expect(m.updater.nativeChecks).toBe(1)
    expect(stagedAtInstall).toBe(true)
    expect(m.updater.installs).toBe(1)
  })

  it('macOS: a busy backend stages nothing; a Squirrel error answers { error } and installs nothing', async () => {
    if (platMod.UPDATER !== 'mac') return
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    let busy = true
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (String(input).includes('/executions')) {
        return Promise.resolve({ ok: true, json: async () => ({ executions: busy ? [{ id: 'e1' }] : [], total: busy ? 1 : 0 }) } as Response)
      }
      return Promise.reject(new Error('offline in tests'))
    })
    const m = loadMain()
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    expect(await m.invoke('update-download')).toEqual({ ok: true })
    expect(await m.invoke('update-install')).toEqual({ busy: true })
    expect(m.updater.nativeChecks).toBe(0)
    busy = false
    m.updater.nativeError = new Error('Code signature at URL did not pass validation')
    expect(await m.invoke('update-install'))
      .toEqual({ error: 'Code signature at URL did not pass validation' })
    expect(m.updater.nativeChecks).toBe(1)
    expect(m.updater.installs).toBe(0)
    expect(m.updater.squirrelDownloadedUpdate).toBe(false)
  })

  it('macOS: a Squirrel that never answers gives up after 10 minutes with nothing installed', async () => {
    if (platMod.UPDATER !== 'mac') return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.nativeSilent = true
    let answer: unknown = null
    void (m.invoke('update-install') as Promise<unknown>).then((r) => { answer = r })
    await vi.advanceTimersByTimeAsync(9 * 60_000)
    expect(answer).toBeNull()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(answer).toEqual({ error: 'the updater stopped responding' })
    expect(m.updater.installs).toBe(0)
  })

  it('reset-all refuses while another reset is in flight', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const stops: ((err: ServiceChildError | null, stdout: string, stderr: string) => void)[] = []
    const m = loadMain({ execFile: (_py, _a, _o, cb) => { stops.push(cb) } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const first = m.invoke('reset-all') as Promise<unknown>
    await vi.waitFor(() => expect(stops).toHaveLength(1))
    expect(await m.invoke('reset-all')).toEqual({ error: 'another operation is in progress' })
    expect(stops).toHaveLength(1)
    stops[0](Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed', '')
    expect(await first).toEqual({ error: 'stop failed' })
  })

  it('reset-all refuses while a quit flight is in progress', async () => {
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const stops: ((err: ServiceChildError | null, stdout: string, stderr: string) => void)[] = []
    const m = loadMain({ execFile: (_py, _a, _o, cb) => { stops.push(cb) } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    const quit = m.invoke('quit-all', { force: true }) as Promise<unknown>
    await vi.waitFor(() => expect(stops).toHaveLength(1))
    expect(await m.invoke('reset-all')).toEqual({ error: 'another operation is in progress' })
    expect(stops).toHaveLength(1)
    stops[0](Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed', '')
    expect(await quit).toEqual({ error: 'stop failed' })
  })

  it('macOS: reset-all refuses while an update install is in progress', async () => {
    if (platMod.UPDATER !== 'mac') return
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const m = loadMain()
    process.env.AUTOWRIGHT_CASKROOM = join(m.home, 'no-caskroom')
    m.updater.nativeSilent = true
    void m.invoke('update-install')
    await vi.advanceTimersByTimeAsync(0)
    expect(await m.invoke('reset-all')).toEqual({ error: 'another operation is in progress' })
    await vi.advanceTimersByTimeAsync(10 * 60_000)
  })

  it('no window or panel is created while a quit flight is still stopping the backend (§9)', async () => {
    vi.useFakeTimers()
    nodeProcess.resourcesPath = join(tmpdir(), 'aw-no-resources')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in tests'))
    const stops: ((err: ServiceChildError | null, stdout: string, stderr: string) => void)[] = []
    const m = loadMain({ ready: true, execFile: (_py, _a, _o, cb) => { stops.push(cb) } })
    writeFileSync(join(m.home, 'backend.json'),
      JSON.stringify({ port: 65000, token: 't', python: '/usr/bin/python3' }))
    await vi.advanceTimersByTimeAsync(0)
    if (caps.trayPanel) await m.invoke('apply-settings', { menuBarIcon: true })
    m.wins[0].close()
    const before = m.windows.length
    const quit = m.invoke('quit-all', { force: true }) as Promise<unknown>
    await vi.advanceTimersByTimeAsync(0)
    expect(stops.length).toBeGreaterThan(0)
    m.secondInstance()
    m.emit('activate')
    if (caps.trayPanel) m.clickTray()
    await m.invoke('open-app', '/app?automation=a1')
    expect(m.windows).toHaveLength(before)
    // A failed stop keeps the app up: the guard lifts with the flight.
    stops[stops.length - 1](Object.assign(new Error('exit 1'), { code: 1 }), 'stop failed', '')
    expect(await quit).toEqual({ error: 'stop failed' })
    m.emit('activate')
    expect(m.windows.length).toBeGreaterThan(before)
  })
})
