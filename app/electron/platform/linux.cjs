// §2 platform layer (shell half), Linux build. The interpreter layout, XDG
// roots, the §3 POSIX shim and login-shell PATH probe (shared with macOS),
// the §9 native-frame chrome, the §13 colored tray assets and any-edge panel
// placement, and the §4.9 XDG-autostart login item are real. What still needs
// infrastructure that does not exist yet (an update feed, a managed-install
// channel) stays an explicit placeholder behind a false capability flag.
// Never imports `electron` (takes app/window objects as arguments) so the
// §15 source guards and stub loaders keep working.
const { execFile } = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')

const roots = require('./roots.cjs')

const OS_TOKEN = 'linux' // §5.1 vocabulary
const OS_NAME = 'Linux' // §4.1 display form

// ---- §5 roots + §3 bundled interpreter layout ------------------------------

function dataRootDefault() { return roots.dataRootDefault('linux') }
function logsRootDefault() { return roots.logsRootDefault('linux') }

// python-build-standalone *-unknown-linux-gnu-install_only layout matches the
// mac one: python/bin/python3 (§3).
function bundledPythonPath(resourcesPath) {
  return path.join(resourcesPath, 'python', 'bin', 'python3')
}

// ---- §9/§13 window chrome, tray, panel -------------------------------------

// §9: native frame for v1 — no custom title bar, no overlay, no vibrancy; the
// OS draws its own bar above the dark client area.
function mainWindowChrome() {
  return {}
}

function panelWindowExtras() {
  return {}
}

function panelAfterCreate(_panel) {}

// §13 (2026-09-01): Linux ships NO tray surface — `trayPanel` is false below,
// so main.cjs never asks for these. StatusNotifier hosts may not render the
// icon at all (stock GNOME needs an extension), activate through a context
// menu the app doesn't have, and don't deliver a reliable `click` — a tray
// that may be invisible or unopenable is worse than none. The functions stand
// in only to keep the module surfaces identical (same idiom as fallback.cjs);
// the checked-in trayLinux PNGs stay for a future revisit.
function panelPosition(pt, display, height) {
  const h = Number.isFinite(height) ? height : 640
  const wa = display.workArea
  const x = Math.max(wa.x + 6, Math.min(pt.x - 167, wa.x + wa.width - 344 - 6))
  const y = pt.y < wa.y + wa.height / 2
    ? wa.y + 6
    : Math.max(wa.y + 6, wa.y + wa.height - h - 6)
  return { x: Math.round(x), y: Math.round(y) }
}

function trayIconSpec(alert) {
  return { file: alert ? 'trayLinuxAlert.png' : 'trayLinux.png', template: false }
}

function setDockIcon(_app, _iconPath) {}

// ---- §3 CLI shim (POSIX, shared with macOS) --------------------------------

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

// §3: GUI apps inherit a stripped PATH, so ask the login shell for the real
// one. Resolves to the PATH string, or null on any failure (= not on PATH).
function readLoginShellPath() {
  return new Promise((resolve) => {
    const shell = process.env.SHELL || '/bin/sh'
    execFile(shell, ['-l', '-c', 'printf %s "$PATH"'], SHELL_PATH_CHILD_OPTIONS, (err, stdout) => {
      resolve(err ? null : String(stdout))
    })
  })
}

// ---- §4.9 login item (XDG autostart) ---------------------------------------

// Electron's setLoginItemSettings is a no-op on Linux — the §4.9 `login`
// setting reconciles a marker-carrying .desktop file in ~/.config/autostart/
// instead (§4.9: written on enable, rewritten when the Exec line drifts,
// deleted on disable; foreign files — no marker — are never touched, the §3
// CLI-shim ownership rules).
const AUTOSTART_MARKER = 'X-Autowright-Login-Item=true'

function autostartPath() {
  const cfg = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config')
  return path.join(cfg, 'autostart', 'ai.autowright.app.desktop')
}

function autostartExec() {
  // A packaged AppImage relaunches through the AppImage itself (the mount
  // point's binary is gone after exit); dev launches use the electron binary.
  const target = process.env.APPIMAGE || process.execPath
  // Desktop-entry Exec quoting: backslash and quote escaped, % is a
  // field-code introducer.
  return `"${target.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/%/g, '%%')}"`
}

function autostartText() {
  return ['[Desktop Entry]', 'Type=Application', 'Name=Autowright',
    `Exec=${autostartExec()}`, AUTOSTART_MARKER, ''].join('\n')
}

function applyLoginItem(app, enabled) {
  const p = autostartPath()
  let current = null
  try { current = fs.readFileSync(p, 'utf-8') } catch { /* absent */ }
  try {
    if (!app.isPackaged) {
      // §4.9: the file's name is shared by every copy of the app, so a dev
      // run must not touch the installed app's registration in either
      // direction — a dev off would delete it, and the dev-harness guard (an
      // unpackaged run's Exec line would point at the bare Electron dev
      // binary) could never write it back. A dev run's whole reconcile is
      // self-cleanup: remove a marker-carrying file only when its Exec line
      // references this very binary (a pre-guard dev leftover), whatever the
      // toggle says — the dev binary must never autostart.
      if (current !== null && current.includes(AUTOSTART_MARKER)
        && current.includes(`Exec=${autostartExec()}`)) fs.unlinkSync(p)
      return
    }
    if (enabled) {
      if (current !== null && !current.includes(AUTOSTART_MARKER)) return // foreign
      const wanted = autostartText()
      if (current !== wanted) {
        fs.mkdirSync(path.dirname(p), { recursive: true })
        fs.writeFileSync(p, wanted)
      }
    } else if (current !== null && current.includes(AUTOSTART_MARKER)) {
      fs.unlinkSync(p)
    }
  } catch { /* best-effort — reconciled again on the next settings poll */ }
}

// ---- §3 desktop integration (launcher entry + icon) ------------------------

// §3: an AppImage is a bare file — until a .desktop file the desktop can see
// names the window's app-id, the running window wears the generic icon and
// nothing lists the app in the launcher. On every packaged launch the shell
// reconciles its own entry and icon under $XDG_DATA_HOME. Same ownership
// rules as the autostart file above (marker-owned, foreign files untouched,
// rewritten only when the bytes drift, dev runs never write), minus the
// toggle: nothing deletes them — TryExec hides the entry once the AppImage is
// gone. The basename matches `desktopName` in package.json, which is what
// Electron hands Wayland as the app-id (and X11 as WM_CLASS), so the desktop
// associates the window with this entry by name alone.
const DESKTOP_ENTRY_ID = 'ai.autowright.app'
const DESKTOP_ENTRY_MARKER = 'X-Autowright-Desktop-Entry=true'

function dataHome() {
  return process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share')
}

function desktopEntryPath() {
  return path.join(dataHome(), 'applications', `${DESKTOP_ENTRY_ID}.desktop`)
}

function desktopIconPath() {
  return path.join(dataHome(), 'icons', 'hicolor', 'scalable', 'apps', `${DESKTOP_ENTRY_ID}.svg`)
}

function desktopEntryText() {
  // TryExec is a bare path (no Exec quoting — the whole value is the path);
  // a path that is no longer executable hides the entry from the launcher.
  const target = process.env.APPIMAGE || process.execPath
  return ['[Desktop Entry]', 'Type=Application', 'Name=Autowright',
    'Comment=Recurring personal automations',
    `Exec=${autostartExec()}`, `TryExec=${target}`,
    `Icon=${DESKTOP_ENTRY_ID}`, `StartupWMClass=${DESKTOP_ENTRY_ID}`,
    'Categories=Utility;', 'Terminal=false', DESKTOP_ENTRY_MARKER, ''].join('\n')
}

function writeIfChanged(p, wanted) {
  let current = null
  try { current = fs.readFileSync(p) } catch { /* absent */ }
  if (current !== null && current.equals(wanted)) return
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, wanted)
}

function applyDesktopEntry(app, iconPath) {
  if (!app.isPackaged) return
  try {
    // Icon first: the desktop's entry-changed event then finds it in place.
    writeIfChanged(desktopIconPath(), fs.readFileSync(iconPath))
    const p = desktopEntryPath()
    let current = null
    try { current = fs.readFileSync(p, 'utf-8') } catch { /* absent */ }
    if (current !== null && !current.includes(DESKTOP_ENTRY_MARKER)) return // foreign
    writeIfChanged(p, Buffer.from(desktopEntryText()))
  } catch { /* best-effort — reconciled again on the next launch */ }
}

// ---- §3 updates -------------------------------------------------------------

// §3 Linux updates: electron-updater's AppImageUpdater against the generic
// provider. The marker is what main.cjs discriminates on — the modules stay
// electron-free, so they name the machinery rather than construct it.
const UPDATER = 'appimage'
const APP_USER_MODEL_ID = null

// §3: the generic provider is pointed at a *directory* (latest-linux.yml + the
// AppImage + its blockmap live under it), never at a single file — the yml is
// rewritten under the repo-root release/linux-x86_64/ by
// linux-scripts/release.sh and fetched raw from GitHub, like the mac and
// Windows feeds. x86-64 is the only Linux arch that ships, so the base URL
// carries no arch switch.
function updateFeedUrl(_arch) {
  return 'https://raw.githubusercontent.com/hansololz/autowright/main/release/linux-x86_64/'
}

// No managed-install channel (distro package) exists for Autowright yet.
function managedInstall() {
  return false
}

const MANAGED_COPY_ERROR = 'This copy is managed by a package manager.'

// ---- §9.4 external links + §5 reveal ----------------------------------------

// No Linux equivalent of the §9 permission-checklist Settings pane is needed
// (iMessage capability is false), so no deep link is allowed.
const SETTINGS_DEEP_LINK = null

// Same rule as macOS: plain data directories open in the file manager;
// anything carrying an extension is revealed, never launched.
function revealPrefersOpen(abs, isDir) {
  return isDir && !path.extname(abs)
}

// ---- §3 ensure-backend diagnostics -------------------------------------------

// §9 failure copy: no Gatekeeper on Linux — a plain line, and the detail
// lives in app.log (whatever serviceDiagnostics captures).
const SERVICE_START_FAILED_DETAIL =
  'The backend service failed to start. Details in app.log.'

// §2 spawn policy mirror (main.cjs SERVICE_CHILD_OPTIONS): systemctl can
// block indefinitely on a wedged domain, so the capture is bounded and its
// output capped — a diagnostics child must never outlive the app.
const DIAGNOSTICS_CHILD_OPTIONS = { timeout: 120_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true }

// After a failed install verification, capture systemd's view of the unit.
function serviceDiagnostics(log) {
  execFile('systemctl', ['--user', '--no-pager', 'status', 'ai.autowright.backend'],
    DIAGNOSTICS_CHILD_OPTIONS, (err, stdout, stderr) => {
      log(`ensure-backend: systemctl status:\n${String(stdout || stderr || err?.message || '').trim()}`)
    })
}

// §13 (2026-09-01): no tray surface on Linux — see the tray block above. With
// no dock and no tray, the §9 close rule quits the UI on the last window
// close; the systemd backend keeps firing regardless.
// §9: no application menu — the native frame would draw Electron's stock
// File/Edit/View/Window bar, which nothing in the app uses, so the shell
// suppresses it (editing shortcuts are Chromium-native and survive).
const capabilities = { trayPanel: false, loginItem: true, dockIcon: false, updates: true, appMenu: false, desktopEntry: true }

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
