// §2 platform layer (shell half), Windows build. The interpreter layout,
// roots, the §3 .cmd shim and the process-env PATH probe are real; so are the
// §9 window chrome, the §13 panel placement and tray assets, the §9
// ensure-backend failure copy and the §3 update feed (electron-updater's
// generic provider). What still needs infrastructure that does not exist yet
// (a managed-install channel, service diagnostics) stays an explicit
// placeholder behind a false capability flag. Never imports
// `electron` (takes app/window objects as arguments) so the §15 source
// guards and stub loaders keep working.
const { execFile } = require('child_process')
const os = require('os')
const path = require('path')

const roots = require('./roots.cjs')

const OS_TOKEN = 'windows' // §5.1 vocabulary
const OS_NAME = 'Windows' // §4.1 display form

// ---- §5 roots + §3 bundled interpreter layout ------------------------------

function dataRootDefault() { return roots.dataRootDefault('win32') }
function logsRootDefault() { return roots.logsRootDefault('win32') }

// python-build-standalone *-pc-windows-msvc-install_only layout is flat:
// python.exe at the root, no bin/ directory (§3).
function bundledPythonPath(resourcesPath) {
  return path.join(resourcesPath, 'python', 'python.exe')
}

// §9 window chrome: hidden title bar with a native `titleBarOverlay` — the OS
// draws minimize/maximize/close at the top-right over the app's own background,
// so the §14 look survives without hand-rolled frameless buttons. `color` is
// `--bg-titlebar` (the renderer paints a full-width 40px title bar in that
// shade, so the button cluster blends into the bar), `symbolColor` the §14
// `--text-2` hex, and `height` matches the renderer's title bar exactly. No
// trafficLightPosition anywhere: that option is macOS-only and lives in
// darwin.cjs.
function mainWindowChrome() {
  return {
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#141820', symbolColor: '#c8ccd4', height: 40 },
  }
}

function panelWindowExtras() {
  return {}
}

function panelAfterCreate(_panel) {}

// §13 placement: the Windows taskbar (and its tray) sits at the bottom by
// default, so anchor the panel's bottom edge just above the work area's
// bottom — at the panel's *real* height, which main.cjs re-anchors on every
// resize-panel, never at the 640 px cap. Clamped to the work area's top so a
// taskbar docked elsewhere still gets a fully on-screen panel, and to the work
// area on **both** x edges — a tray icon at the left of a secondary display
// must not push the panel off-screen. `height` is optional (the panel's
// current height); the 640 px window cap stands in when a caller has no
// measurement yet.
function panelPosition(pt, display, height) {
  const h = Number.isFinite(height) ? height : 640
  const wa = display.workArea
  const x = Math.max(wa.x + 6, Math.min(pt.x - 167, wa.x + wa.width - 344 - 6))
  const y = Math.max(wa.y + 6, wa.y + wa.height - h - 6)
  return { x: Math.round(x), y: Math.round(y) }
}

// §13: real colored assets for the Windows notification area — a light glyph
// legible on the dark taskbar, never the mac black template images (which
// disappear there). Rendered by scripts/gen_tray_icon.py beside the mac PNGs;
// @2x picked up by nativeImage for 200 % DPI.
function trayIconSpec(alert) {
  return { file: alert ? 'trayWinAlert.png' : 'trayWin.png', template: false }
}

function setDockIcon(_app, _iconPath) {}

// ---- §3 CLI shim (.cmd form) ------------------------------------------------

const SHIM_MARKER = 'rem autowright CLI shim'

function defaultShimPath() {
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
  return path.join(local, 'Autowright', 'bin', 'autowright.cmd')
}

function shimText(python) {
  return `@echo off\r\n${SHIM_MARKER}\r\n"${python}" -m autowright.cli %*\r\n`
}

// §3: Windows GUI apps inherit the full user PATH — no login shell exists to
// ask, so the probe reads the process environment directly.
function readLoginShellPath() {
  return Promise.resolve(process.env.PATH || null)
}

// ---- §4.9 login item --------------------------------------------------------

// §4.9 login reconcile: the OS login item via Electron — an HKCU Run value
// named by the §3 AUMID, one shared name for every copy of the app.
const RUN_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'

// §4.9 legacy sweep: builds before the AUMID let Electron name the Run value
// electron.app.<name> — slots nothing reconciles anymore, so a leftover keeps
// launching the app with the toggle off. electron.app.Autowright can only
// ever be Autowright's own stale slot and is deleted outright (absent is
// fine); electron.app.Electron is the generic dev-shell name and may belong
// to another app, so it goes only when its command references this very
// binary. Best-effort: reg.exe failures are reconciled again next run.
function sweepLegacyLoginItems(exec) {
  exec('reg', ['delete', RUN_KEY, '/v', 'electron.app.Autowright', '/f'], () => {})
  exec('reg', ['query', RUN_KEY, '/v', 'electron.app.Electron'], (err, stdout) => {
    if (err || typeof stdout !== 'string') return
    if (stdout.toLowerCase().includes(process.execPath.toLowerCase())) {
      exec('reg', ['delete', RUN_KEY, '/v', 'electron.app.Electron', '/f'], () => {})
    }
  })
}

let legacySwept = false
// Because the Run value's name is the shared AUMID — not this binary — only a
// packaged run may touch it: a dev off would delete the installed app's
// registration, and the dev-harness guard (an unpackaged run's registration
// would enroll the bare Electron dev binary) could never write it back. A dev
// run's whole reconcile is the legacy sweep. Packaged: off is asserted
// unconditionally, never guarded by the OS reading (which can be stale); on
// writes only when the OS view differs.
function applyLoginItem(app, enabled, exec = execFile) {
  if (!legacySwept) { legacySwept = true; sweepLegacyLoginItems(exec) }
  if (!app.isPackaged) return
  if (!enabled) {
    app.setLoginItemSettings({ openAtLogin: false })
  } else if (!app.getLoginItemSettings().openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: true })
  }
}

// ---- §3 updates -------------------------------------------------------------

// §3 Windows updates: electron-updater's NsisUpdater against the generic
// provider. The marker is what main.cjs discriminates on — the modules stay
// electron-free, so they name the machinery rather than construct it.
const UPDATER = 'nsis'
// §3 identifiers: matches the installer shortcut's AUMID and the appId.
const APP_USER_MODEL_ID = 'ai.autowright.app'

// §3: the generic provider is pointed at a *directory* (latest.yml + the
// installer + its blockmap live under it), never at a single file — the yml
// is rewritten under the repo-root release/win32-x86_64/ by
// windows-scripts/release.ps1 and fetched raw from GitHub, like the mac feeds.
// x86_64 is the only Windows arch that ships, so the base URL carries no arch
// switch.
function updateFeedUrl(_arch) {
  return 'https://raw.githubusercontent.com/hansololz/autowright/main/release/win32-x86_64/'
}

// No managed-install channel (winget/Chocolatey) exists for Autowright yet.
function managedInstall() {
  return false
}

const MANAGED_COPY_ERROR = 'This copy is managed by a package manager.'

// ---- §9.4 external links + §5 reveal ----------------------------------------

// No Windows equivalent of the §9 permission-checklist Settings pane is
// needed (iMessage capability is false), so no deep link is allowed.
const SETTINGS_DEEP_LINK = null

// Same rule as macOS: plain data directories open in Explorer; anything
// carrying an extension is revealed, never launched.
function revealPrefersOpen(abs, isDir) {
  return isDir && !path.extname(abs)
}

// ---- §3 ensure-backend diagnostics -------------------------------------------

// §9 failure copy: no Gatekeeper on Windows — a plain line, and the detail
// lives in app.log (whatever serviceDiagnostics captures).
const SERVICE_START_FAILED_DETAIL =
  'The backend service failed to start. Details in app.log.'

// No extra diagnostics to capture: the §3 service result lines already land in app.log.
function serviceDiagnostics(_log) {}

// §3 desktop integration is a Linux-only surface (the AppImage launcher entry).
function applyDesktopEntry(_app, _iconPath) {}

const capabilities = { trayPanel: true, loginItem: true, dockIcon: false, updates: true, appMenu: true, desktopEntry: false }

module.exports = {
  OS_TOKEN,
  OS_NAME,
  dataRootDefault,
  logsRootDefault,
  bundledPythonPath,
  mainWindowChrome,
  panelWindowExtras,
  panelAfterCreate,
  panelPosition,
  trayIconSpec,
  setDockIcon,
  SHIM_MARKER,
  defaultShimPath,
  shimText,
  readLoginShellPath,
  applyLoginItem,
  applyDesktopEntry,
  sweepLegacyLoginItems,
  UPDATER,
  APP_USER_MODEL_ID,
  updateFeedUrl,
  managedInstall,
  MANAGED_COPY_ERROR,
  SETTINGS_DEEP_LINK,
  revealPrefersOpen,
  SERVICE_START_FAILED_DETAIL,
  serviceDiagnostics,
  capabilities,
}
