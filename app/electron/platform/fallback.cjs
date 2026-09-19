// §2 platform layer (shell half), degraded build for any platform with no
// module of its own — macOS, Windows and Linux each route to theirs, so
// nothing shipped lands here. Not an implementation: explicit placeholders so
// an unknown platform degrades in plain words (native window frame, no tray,
// no login item, no update feed) instead of crashing on options only one OS
// understands. Every export darwin.cjs has exists here, so main.cjs never
// forks on which module it got; giving a new platform a real module of its own
// with the same surface is the entire shell-side port surface.
const { execFile } = require('child_process')
const os = require('os')
const path = require('path')

const roots = require('./roots.cjs')

// §5.1 vocabulary / §4.1 display form. An unknown platform has no name of its
// own to serve, so it answers the POSIX-shaped defaults the rest of this
// module is built on.
const OS_TOKEN = 'linux'
const OS_NAME = 'Linux'

function dataRootDefault() { return roots.dataRootDefault(process.platform) }
function logsRootDefault() { return roots.logsRootDefault(process.platform) }

function bundledPythonPath(resourcesPath) {
  return path.join(resourcesPath, 'python', 'bin', 'python3')
}

// Native frame and decorations: the only chrome every windowing system draws.
function mainWindowChrome() {
  return {}
}

function panelWindowExtras() {
  return {}
}

function panelAfterCreate(_panel) {}

// Top-of-display placeholder placement; a real port anchors to the tray.
function panelPosition(pt, display) {
  const x = Math.min(pt.x - 167, display.bounds.x + display.bounds.width - 344)
  return { x: Math.round(x), y: display.workArea.y + 6 }
}

// Tray assets are per-OS and this platform has none of its own. The
// `trayPanel` capability is false, so main.cjs never asks — the checked-in
// PNGs stand in only to keep the module surfaces identical.
function trayIconSpec(alert) {
  return { file: alert ? 'trayAlert.png' : 'trayTemplate.png', template: false }
}

function setDockIcon(_app, _iconPath) {}

// §3 shim in its POSIX form — the shape every non-Windows platform uses (the
// Windows .cmd shim lives in win32.cjs).
const SHIM_MARKER = '# autowright CLI shim'

function defaultShimPath() {
  return path.join(os.homedir(), '.local', 'bin', 'autowright')
}

function shimText(python) {
  return `#!/bin/sh\n${SHIM_MARKER}\nexec "${python}" -m autowright.cli "$@"\n`
}

// §2 spawn policy: never show a console window for a shell child, and cap
// what it can print — a chatty rc file must not buffer an unbounded stream
// into the main process.
const SHELL_PATH_CHILD_OPTIONS = { windowsHide: true, timeout: 2000, maxBuffer: 1024 * 1024 }

function readLoginShellPath() {
  return new Promise((resolve) => {
    const shell = process.env.SHELL || '/bin/sh'
    execFile(shell, ['-l', '-c', 'printf %s "$PATH"'], SHELL_PATH_CHILD_OPTIONS, (err, stdout) => {
      resolve(err ? null : String(stdout))
    })
  })
}

// Login item: no mechanism here — the capability flag is false, so main.cjs
// never asks; the no-op keeps the module surfaces identical.
function applyLoginItem(_app, _enabled) {}

// No update machinery at all here — the capability flag is false, so main.cjs
// never asks; the marker exists only to keep the module surfaces identical.
const UPDATER = null
const APP_USER_MODEL_ID = null

// No update channel yet: update-check reports a plain error state.
function updateFeedUrl(_arch) {
  return null
}

// §3 managed-install detection: a managed channel is per-OS (Homebrew is the
// macOS one) and this platform declares none, so no copy here is ever managed.
function managedInstall() {
  return false
}

// Kept only so the module surfaces stay identical — with no feed and no
// managed channel, nothing on this platform can ever answer it.
const MANAGED_COPY_ERROR = 'This copy is managed by a package manager.'

const SETTINGS_DEEP_LINK = null

function revealPrefersOpen(_abs, isDir) {
  return isDir
}

// §9 failure copy: the Gatekeeper line is macOS copy (darwin.cjs) — with no
// knowledge of this platform's service manager, the plain line is all we can
// honestly say.
const SERVICE_START_FAILED_DETAIL =
  'The backend service failed to start. Details in app.log.'

function serviceDiagnostics(_log) {}

// §3 desktop integration is a Linux-only surface (the AppImage launcher entry).
function applyDesktopEntry(_app, _iconPath) {}

const capabilities = { trayPanel: false, loginItem: false, dockIcon: false, updates: false, appMenu: true, desktopEntry: false }

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
