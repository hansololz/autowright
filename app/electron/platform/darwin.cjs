// §2 platform layer (shell half), macOS build: every mac-coupled value and
// helper main.cjs uses. Composition, not inheritance — this module exports a
// plain object; a future per-OS port adds a sibling module with the same
// surface. Never imports `electron` (takes app/window objects as arguments)
// so the §15 source guards and stub loaders keep working.
const { execFile } = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')

const roots = require('./roots.cjs')

const OS_TOKEN = 'macos' // §5.1 vocabulary
const OS_NAME = 'macOS' // §4.1 display form

// ---- §5 roots + §3 bundled interpreter layout ------------------------------

function dataRootDefault() { return roots.dataRootDefault('darwin') }
function logsRootDefault() { return roots.logsRootDefault('darwin') }

function bundledPythonPath(resourcesPath) {
  return path.join(resourcesPath, 'python', 'bin', 'python3')
}

// ---- §9/§13 window chrome, tray, panel -------------------------------------

function mainWindowChrome() {
  return {
    titleBarStyle: 'hidden',
    // §9: lights pinned at one fixed spot for every window state — never moved
    // or re-derived from layout, so they can't jitter between states.
    trafficLightPosition: { x: 14, y: 14 },
  }
}

// §13 panel look: vibrancy behind the renderer's translucent background.
function panelWindowExtras() {
  return { transparent: true, vibrancy: 'menu', visualEffectState: 'active' }
}

// §13: menu-bar panels follow the user across Spaces — without this, opening
// the panel over a fullscreen app switches Spaces.
function panelAfterCreate(panel) {
  panel.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
}

// §13 placement: centered under the cursor's menu-bar position, clamped to the
// display's work area on **both** x edges (a tray icon at the left of a
// secondary display must not push the panel off-screen), just below the (top)
// menu bar.
function panelPosition(pt, display) {
  const wa = display.workArea
  const x = Math.max(wa.x + 6, Math.min(pt.x - 167, wa.x + wa.width - 344 - 6))
  return { x: Math.round(x), y: wa.y + 6 }
}

// §13: red alert dot when any automation failed. The alert variant is a
// pre-rendered non-template PNG (neutral gray glyph + red dot) so the dot
// stays red on light and dark menu bars; the normal icon is a template.
function trayIconSpec(alert) {
  return { file: alert ? 'trayAlert.png' : 'trayTemplate.png', template: !alert }
}

function setDockIcon(app, iconPath) {
  app.dock.setIcon(iconPath)
}

// ---- §3 CLI shim ------------------------------------------------------------

const SHIM_MARKER = '# autowright CLI shim'

function defaultShimPath() {
  return path.join(os.homedir(), '.local', 'bin', 'autowright')
}

function shimText(python) {
  return `#!/bin/sh\n${SHIM_MARKER}\nexec "${python}" -m autowright.cli "$@"\n`
}

// §3: GUI apps inherit a stripped PATH, so ask the login shell for the real
// one. Resolves to the PATH string, or null on any failure (= not on PATH).
function readLoginShellPath() {
  return new Promise((resolve) => {
    const shell = process.env.SHELL || '/bin/zsh'
    execFile(shell, ['-l', '-c', 'printf %s "$PATH"'], { timeout: 2000 }, (err, stdout) => {
      resolve(err ? null : String(stdout))
    })
  })
}

// ---- §4.9 login item --------------------------------------------------------

// §4.9 login reconcile: the OS login item via Electron. Registration is only
// valid from a packaged run: an unpackaged (dev-harness) run would enroll the
// bare Electron dev binary as the login item, not Autowright. macOS names the
// registration per-binary (unlike the Windows/Linux shared-name slots), so a
// dev off can only ever clear a stale registration for its own binary — off
// is therefore asserted unconditionally by any run, never guarded by the OS
// reading (which can be stale or describe a different copy); on writes only
// when the OS view differs.
function applyLoginItem(app, enabled) {
  if (!enabled) {
    app.setLoginItemSettings({ openAtLogin: false })
  } else if (app.isPackaged && !app.getLoginItemSettings().openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: true })
  }
}

// ---- §3 updates -------------------------------------------------------------

// §3: which update machinery main.cjs drives here — electron-updater's
// MacUpdater (Squirrel.Mac underneath; its ShipIt helper already ships in the
// bundle). The module names it rather than constructing it: platform modules
// never import `electron`.
const UPDATER = 'mac'
// AppUserModelID is a Windows concept — no-op here (§3).
const APP_USER_MODEL_ID = null

// §3: the generic provider is pointed at a *directory* per arch
// (latest-mac.yml lives under it; electron-updater appends the darwin channel
// file name itself), rewritten under the repo-root release/ by release.sh and
// fetched raw from GitHub, like the Windows and Linux feeds. The legacy
// Squirrel feed.json beside it belongs to pre-0.6.1 installs only (§3 bridge).
function updateFeedUrl(arch) {
  return `https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-${arch === 'x64' ? 'x86_64' : 'arm64'}/`
}

// §3 Homebrew-managed detection: the install is brew-managed while the cask's
// Caskroom metadata dir exists. Probed fresh on every query — never cached —
// so a brew install/uninstall while the app runs reflects without a restart.
// AUTOWRIGHT_CASKROOM replaces the probe list (test escape hatch).
function managedInstall() {
  const dirs = process.env.AUTOWRIGHT_CASKROOM
    ? [process.env.AUTOWRIGHT_CASKROOM]
    : ['/opt/homebrew/Caskroom/autowright', '/usr/local/Caskroom/autowright']
  return dirs.some((dir) => fs.existsSync(dir))
}

const MANAGED_COPY_ERROR = 'This copy is managed by Homebrew.'

// ---- §9.4 external links + §5 reveal ----------------------------------------

// The one allowed deep link: the §9 permission checklist's Settings pane.
const SETTINGS_DEEP_LINK = 'x-apple.systempreferences:'

// A macOS bundle (.app, .pkg, …) is a directory, and openPath on one *launches*
// it — reveal is meant to show a location, never to start something. Bundles
// all carry an extension; plain data dirs (the ones this is for) do not.
function revealPrefersOpen(abs, isDir) {
  return isDir && !path.extname(abs)
}

// ---- §3 ensure-backend diagnostics -------------------------------------------

// §9 failure copy: launchctl can report success while the job never spawns —
// Gatekeeper silently refuses to exec an unsigned, quarantined bundled Python
// as a LaunchAgent — so the mac line names it.
const SERVICE_START_FAILED_DETAIL =
  'The backend service was registered but never started — macOS '
  + 'Gatekeeper may be blocking an unsigned build. Details in app.log.'

// After a failed install verification, capture launchd's view of the job.
function serviceDiagnostics(log) {
  execFile('launchctl', ['print', `gui/${process.getuid()}/ai.autowright.backend`],
    (err, stdout, stderr) => {
      log(`ensure-backend: launchctl print:\n${String(stdout || stderr || err?.message || '').trim()}`)
    })
}

// §3 desktop integration is a Linux-only surface (the AppImage launcher entry).
function applyDesktopEntry(_app, _iconPath) {}

const capabilities = { trayPanel: true, loginItem: true, dockIcon: true, updates: true, appMenu: true, desktopEntry: false }

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
