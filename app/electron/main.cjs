// Electron main: one app window + a tray (menu-bar) panel window (§9, §13).
const { app, autoUpdater, BrowserWindow, Menu, Notification, Tray, dialog, nativeImage, ipcMain, powerMonitor, session, shell, screen } = require('electron')
const { execFile } = require('child_process')
const { randomUUID } = require('crypto')
const fs = require('fs')
const os = require('os')
const path = require('path')

// §2 platform layer (shell half): every OS-coupled value/helper comes from
// the composed per-OS module — macOS today, a degraded fallback elsewhere.
const plat = require('./platform/index.cjs')

// §9: the shell asks its own platform module which surfaces exist before
// wiring any of them — a module that declares a capability false is never
// asked for its assets or handlers. `dockIcon` doubles as the §9 close rule's
// discriminator: a platform with a dock keeps the app resident behind it, a
// platform without one has only the tray to stay resident in.
const caps = plat.capabilities

// Keep Chromium's profile (Cache, Cookies, Local Storage, …) out of the backend's
// data dir — both default to ~/Library/Application Support/Autowright (§5).
// §15: AUTOWRIGHT_HOME relocates the whole app-support root, profile included —
// an isolated dev/test home must never touch the real profile.
// §5: the base is the platform module's data root — identical to Electron's
// userData default on macOS, but on Windows getPath('userData') is Roaming
// %APPDATA% while the §5 root is %LOCALAPPDATA%; one root holds all app state.
app.setPath('userData', path.join(
  process.env.AUTOWRIGHT_HOME || plat.dataRootDefault(), 'electron'))

// §3 identifiers: on Windows the window's AppUserModelID must match the
// installer shortcut's (taskbar grouping/pinning + toast identity agree).
if (plat.APP_USER_MODEL_ID) app.setAppUserModelId(plat.APP_USER_MODEL_ID)

// Overlay scrollbars: draw on top of content, zero layout space, so content
// never shifts when a scrollbar appears. Without this, macOS "Automatic"/
// "Always" system settings force classic space-taking bars (§14).
app.commandLine.appendSwitch('enable-features', 'OverlayScrollbar')

let win = null
// §9 never-paint-blank guard: true once the current main window's renderer has
// loaded successfully — until then every show path stays hidden.
let winLoaded = false
// §9 deep link held for a renderer that hasn't loaded yet: the `open-target`
// listener only exists once the renderer ran, so a target that arrives before
// then is replayed on the first successful load instead of sent into nothing.
let pendingTarget = null
let panel = null
let tray = null
// True from `before-quit` on — the app is on its way out (§3: the
// update-install answer tells a real quit from an updater that refused one).
let quitting = false
// §3 quit means quit for good: an OS quit (Cmd+Q, the application menu's Quit,
// the dock's Quit) runs the quit-entirely flow. Every quit of our own goes
// through quitUi(), which sets the pass-through before-quit reads; a session
// end (powerMonitor 'shutdown', the main window's 'query-session-end' on
// Windows) passes through too. Any other before-quit is
// the user's Quit and is intercepted below.
let uiQuit = false
let sessionEnding = false
function quitUi() {
  uiQuit = true
  app.quit()
}
// The 60 s tray-alert + shell-settings poll (§13/§4.9). Held so it can be
// stopped at quit and at the §3 reset — a poll that outlives either one
// fetches a backend that is going away and logs into a deleted logs root.
let shellPoll = null

function stopShellPoll() {
  if (shellPoll) { clearInterval(shellPoll); shellPoll = null }
}

// §13 shell-posted notifications: one WebSocket to the backend with the
// `shell` role for as long as the app runs — main is the one shell piece
// alive in the tray-only state, exactly when a notification has nowhere else
// to land. Connected only when the OS notification API is available; without
// it main stays off the hub and the backend keeps posting natively (§6
// carrier rule). Reconnects with the renderer socket's backoff policy (§19):
// 1.5 s doubling to a 15 s cap, reset once a connection comes back.
const SHELL_SOCKET_RETRY_MIN_MS = 1500
const SHELL_SOCKET_RETRY_MAX_MS = 15_000
let shellSocket = null
let shellSocketRetryTimer = null
let shellSocketRetryMs = SHELL_SOCKET_RETRY_MIN_MS
// True once the quit flows (or the §3 reset) closed the socket: no reconnect.
let shellSocketStopped = true
// Open/close are logged once per outage, never per retry.
let shellSocketUp = false
// Shown notifications are held until clicked or closed: a notification object
// the garbage collector takes loses its click handler. Bounded, oldest first.
const liveNotifications = new Set()
const LIVE_NOTIFICATIONS_MAX = 50

function notificationsSupported() {
  try {
    return Boolean(Notification?.isSupported?.())
  } catch {
    return false
  }
}

function startShellSocket() {
  if (!notificationsSupported()) return
  if (!shellSocketStopped) return
  shellSocketStopped = false
  shellSocketRetryMs = SHELL_SOCKET_RETRY_MIN_MS
  connectShellSocket()
}

function stopShellSocket() {
  shellSocketStopped = true
  if (shellSocketRetryTimer) { clearTimeout(shellSocketRetryTimer); shellSocketRetryTimer = null }
  const socket = shellSocket
  // Cleared first, so the close this triggers never schedules a reconnect.
  shellSocket = null
  if (socket) {
    try { socket.close() } catch { /* already closing */ }
  }
  if (shellSocketUp) {
    shellSocketUp = false
    appLog('notifications: shell socket closed')
  }
}

function scheduleShellSocket() {
  if (shellSocketStopped || quitting || shellSocketRetryTimer) return
  const wait = shellSocketRetryMs
  shellSocketRetryMs = Math.min(shellSocketRetryMs * 2, SHELL_SOCKET_RETRY_MAX_MS)
  shellSocketRetryTimer = setTimeout(() => {
    shellSocketRetryTimer = null
    connectShellSocket()
  }, wait)
}

function connectShellSocket() {
  if (shellSocketStopped || quitting) return
  // backend.json is re-read on every attempt: a backend restart binds a new
  // port and token.
  const info = backendInfo()
  if (!info?.port || !info?.token) { scheduleShellSocket(); return }
  let socket
  try {
    socket = new WebSocket(
      `ws://127.0.0.1:${info.port}/ws?token=${encodeURIComponent(info.token)}&role=shell`)
  } catch {
    scheduleShellSocket()
    return
  }
  shellSocket = socket
  socket.addEventListener('open', () => {
    if (shellSocket !== socket) return
    shellSocketRetryMs = SHELL_SOCKET_RETRY_MIN_MS
    if (!shellSocketUp) {
      shellSocketUp = true
      appLog('notifications: shell socket connected')
    }
  })
  socket.addEventListener('message', (event) => {
    if (shellSocket !== socket) return
    let message
    try {
      message = JSON.parse(String(event.data))
    } catch {
      return
    }
    showPostedNotification(message)
  })
  // An error arrives with (or just before) a close; whichever comes first
  // owns the reconnect, the other finds the socket already dropped.
  const dropped = () => {
    if (shellSocket !== socket) return
    shellSocket = null
    if (shellSocketUp) {
      shellSocketUp = false
      appLog('notifications: shell socket closed — reconnecting')
    }
    scheduleShellSocket()
  }
  socket.addEventListener('close', dropped)
  socket.addEventListener('error', dropped)
}

// The event crosses a socket, so its shape is checked before anything is
// shown: string title and body, and a target naming a page and a non-empty
// string id. Any id is accepted (not only a lowercase uuid) — the
// notification always shows; the id is URI-encoded so it can never break out
// of its query value.
function notificationTargetHash(target) {
  if (!target || typeof target !== 'object') return null
  if (target.page === 'execution' && typeof target.executionId === 'string'
    && target.executionId) return `/app?execution=${encodeURIComponent(target.executionId)}`
  if (target.page === 'automation' && typeof target.automationId === 'string'
    && target.automationId) return `/app?automation=${encodeURIComponent(target.automationId)}`
  return null
}

function showPostedNotification(message) {
  // Every other event on this socket is ignored by main.
  if (!message || typeof message !== 'object' || message.event !== 'notification.posted') return
  const { title, body } = message
  if (typeof title !== 'string' || typeof body !== 'string') return
  const hash = notificationTargetHash(message.target)
  if (!hash) return
  try {
    const notification = new Notification({ title, body })
    const release = () => { liveNotifications.delete(notification) }
    notification.on('click', () => {
      release()
      showApp(hash)
    })
    notification.on('close', release)
    liveNotifications.add(notification)
    if (liveNotifications.size > LIVE_NOTIFICATIONS_MAX) {
      liveNotifications.delete(liveNotifications.values().next().value)
    }
    notification.show()
  } catch (err) {
    appLog(`notifications: showing a notification failed: ${String(err?.message || err)}`)
  }
}

// One app process only: a second launch (login item racing a manual open,
// `open -n`) would create a second tray and double-fire §6 app-start triggers.
// §9: the one loser that waits is the Linux AppImage update's relaunch — the
// updater spawns the new instance (APPIMAGE_SILENT_INSTALL in its environment)
// before the old one has exited, so it retries the lock every 250 ms for up to
// 10 s rather than quit into nothing. Any other loser quits at once.
const LOCK_RETRY_MS = 250
const LOCK_RETRY_WINDOW_MS = 10_000
let gotLock = app.requestSingleInstanceLock()
// The pending retry (resolves true once the lock is ours, false on giving
// up); null when no retry runs. The ready chain waits on it.
let lockRetry = null
if (!gotLock && process.env.APPIMAGE_SILENT_INSTALL === 'true') lockRetry = retrySingleInstanceLock()
else if (!gotLock) quitUi()

function retrySingleInstanceLock() {
  return new Promise((resolve) => {
    const deadline = Date.now() + LOCK_RETRY_WINDOW_MS
    const attempt = () => {
      if (app.requestSingleInstanceLock()) {
        gotLock = true
        return resolve(true)
      }
      if (Date.now() >= deadline) {
        quitUi()
        return resolve(false)
      }
      setTimeout(attempt, LOCK_RETRY_MS)
    }
    setTimeout(attempt, LOCK_RETRY_MS)
  })
}
// second-instance can fire before whenReady (the exact login-item race above);
// creating a BrowserWindow before ready throws. The ready path opens the
// window itself, so the early signal needs no replay.
app.on('second-instance', () => { if (app.isReady()) showApp() })

// §5 app-support root — the backend's home (AUTOWRIGHT_HOME overrides it, §15).
function appSupportDir() {
  return process.env.AUTOWRIGHT_HOME
    ? process.env.AUTOWRIGHT_HOME
    : plat.dataRootDefault()
}

function backendInfo() {
  try {
    return JSON.parse(fs.readFileSync(path.join(appSupportDir(), 'backend.json'), 'utf-8'))
  } catch {
    return null
  }
}

function logsDir() {
  return process.env.AUTOWRIGHT_HOME
    ? path.join(process.env.AUTOWRIGHT_HOME, 'logs')
    : plat.logsRootDefault()
}

// §3 reset step 4: the reset writes its own last line and then erases the logs
// root, so from that point the writer is a no-op for every other caller (the
// backend-up poll, the shell-settings interval, the service diagnostics) —
// appLog re-creates the logs dir, so a late line would leave a fresh logs root
// behind on every reset.
let resetting = false

// `force` is the one exception, reserved for the deletion's own failure lines:
// a delete that failed left files there anyway.
function appLog(line, { force = false } = {}) {
  if (resetting && !force) return
  try {
    fs.mkdirSync(logsDir(), { recursive: true })
    fs.appendFileSync(path.join(logsDir(), 'app.log'), `${new Date().toISOString()} ${line}\n`)
  } catch { /* logging must never break startup */ }
}

// §3 ensure-backend: the app owns backend registration. Probe the backend; if
// unreachable, (re)register the LaunchAgent by running the bundled service
// module (`python -m autowright.service install`) — the same single code path
// headless setups run by hand. The app never invokes the CLI (§3).
// A healthy backend is never touched, so an app launch never interrupts
// running executions; a broken registration (fresh install, app bundle moved,
// plist pointing at a deleted interpreter) self-heals here. Dev launches
// (`electron .`) have no bundled Python — scripts/dev.sh installs the service
// from the repo venv before Electron starts, through the same install code.
function bundledPython() {
  const py = plat.bundledPythonPath(process.resourcesPath)
  return fs.existsSync(py) ? py : null
}

// The backend's running version (from /health), or null when it is not ours —
// doubles as the liveness probe and feeds the §3 launch-time version compare.
// §3: a 200 on the recorded port proves nothing on its own. backend.json can
// name a stale port that any other local server now listens on, and treating
// that stranger as our backend would mark ensure-backend 'ok', never install
// the service, and wait forever. Only a /health body that identifies this app
// (`app === 'Autowright'`) and carries a non-empty version counts as healthy;
// non-JSON, a foreign app name and an empty version all read as unreachable,
// so ensure-backend installs (or version-syncs) instead of hanging.
async function backendVersion() {
  // §3: a backend busy under its store lock can miss a single probe, and
  // concluding "unreachable" there would bootout a live backend mid-execution.
  // So only the no-answer case retries - three attempts, 2 s each, 500 ms
  // apart. An answer settles it either way: a foreign app name or an empty
  // version is null on the first reply, never retried.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt) await new Promise((r) => setTimeout(r, 500))
    // backend.json is re-read on every attempt: a backend coming up (or back
    // up) between attempts writes a fresh port there, and a port pinned
    // before the first probe would keep asking the old one for the whole
    // retry window.
    const info = backendInfo()
    if (!info) continue
    let res
    try {
      res = await fetch(`http://127.0.0.1:${info.port}/health`, {
        signal: AbortSignal.timeout(2000),
      })
    } catch {
      continue
    }
    try {
      if (!res.ok) return null
      const body = await res.json()
      if (body?.app !== 'Autowright') return null
      return String(body.version ?? '') || null
    } catch {
      return null
    }
  }
  return null
}

// Healthy ≙ our backend answered and named itself — never merely "something
// answered on that port".
async function backendHealthy() {
  return (await backendVersion()) !== null
}

// §3: an update install or backend restart must never land mid-execution.
// Four-state probe: true/false when the backend answered the question,
// 'unknown' when it answered but refused to (a non-OK status — it is up, so
// something may well be running on it), null when it is unreachable at all.
// Callers decide what each of the two unknowns means for them.
async function executionsLiveProbe() {
  const info = backendInfo()
  if (!info) return null
  try {
    // §3: queued counts as live too — the route repeats `status`. Stopping
    // the backend under a queued firing strands its sender until the next
    // start finishes it `skipped`, so the gate must see it.
    const res = await fetch(`http://127.0.0.1:${info.port}/executions?status=executing&status=queued`, {
      headers: { Authorization: `Bearer ${info.token}` },
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) return 'unknown'
    // §19 envelope: GET /executions answers { executions, total } — never a
    // bare array. Reading .length off the envelope would answer "not busy"
    // forever and neuter every §3 mid-execution gate below.
    return (((await res.json()).executions || []).length > 0)
  } catch {
    return null
  }
}

// §3 update-install rule: an unreachable backend counts as idle — nothing can
// be executing on it. A backend that is up but won't answer counts as busy:
// the gate must never let an install or a stop land on top of a running
// execution because the question came back a 500.
async function executionsLive() {
  const live = await executionsLiveProbe()
  return live === true || live === 'unknown'
}

// One authenticated call against the live backend — the same shape the probes
// above use (port + bearer token from backend.json, a timeout, never a throw).
// Resolves to the Response, or null when the backend is unreachable; callers
// decide what unreachable means for them.
async function backendFetch(route, init = {}, timeoutMs = 10_000) {
  const info = backendInfo()
  if (!info) return null
  try {
    return await fetch(`http://127.0.0.1:${info.port}${route}`, {
      ...init,
      headers: { Authorization: `Bearer ${info.token}`, ...(init.headers || {}) },
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch {
    return null
  }
}

// §3 install verification: launchctl can report success while the job never
// spawns (Gatekeeper silently refuses to exec an unsigned, quarantined bundled
// Python as a LaunchAgent — the GUI app's approval does not extend to launchd).
// Poll /health after install; on failure, capture launchd's view into app.log
// and expose the failure to the renderer (backend-status IPC → §9 boot splash).
let ensureStatus = { state: 'idle', detail: '' }

// §3 quit-all interlock: a spawned `service install` child (ensure-backend or
// version-sync) survives app.quit(), so quit-all's `service stop` could
// interleave with it and leave the backend running after the app quit
// claiming it stopped. Every install spawn goes through runServiceInstall so
// quit-all can wait for the in-flight child and block new ones.
let quittingAll = false
let serviceInstallDone = Promise.resolve()

// §3: a service child that never returns must never hang the flow behind it —
// launchctl/sc can block indefinitely on a wedged domain, and an unbounded
// execFile left quit-all and reset waiting for the life of the app. Every
// spawn is bounded (the child is killed on expiry and the callback carries
// `killed`), its output capped, and every wait on one is bounded too.
// §2 spawn policy: never show a console window for a shell child.
// §3 shipped-bytecode guard: a service child must never depend on writing
// into the sealed bundle. Through 0.13.0 every shipped .pyc was stale, so the
// interpreter's own start-up tried to rewrite them there — and from a child
// of the app that write blocked (pending folder consent), the timeout below
// killed `service install`, and the relaunch after a Quit never got a
// backend. The build now ships valid bytecode; this keeps a stale bundle from
// ever wedging install/stop again (it can only slow them).
const SERVICE_CHILD_TIMEOUT_MS = 120_000
const SERVICE_CHILD_OPTIONS = {
  windowsHide: true,
  timeout: SERVICE_CHILD_TIMEOUT_MS,
  maxBuffer: 4 * 1024 * 1024,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
}

function runServiceInstall(py, cb) {
  // §3: an install dropped because quit-all is already latched records that
  // outcome — the status never latches on 'installing' for a run that was
  // never made. Both callers (ensure-backend and the version sync) land here.
  if (quittingAll) {
    ensureStatus = { state: 'failed', detail: 'quitting' }
    appLog('ensure-backend: install dropped — the app is quitting')
    return
  }
  serviceInstallDone = serviceInstallDone.then(() => new Promise((resolve) => {
    execFile(py, ['-m', 'autowright.service', 'install'], SERVICE_CHILD_OPTIONS, (err, stdout, stderr) => {
      try { cb(err, stdout, stderr) } finally { resolve() }
    })
  }))
}

// §3: the verification window is wall-clock, a deadline and not an iteration
// count — each /health probe carries its own three retries, so a fixed count
// of 2 s iterations ran for minutes while the log line still said 30 s.
const VERIFY_DEADLINE_MS = 30_000

async function verifyBackendUp() {
  const deadline = Date.now() + VERIFY_DEADLINE_MS
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000))
    if (await backendHealthy()) {
      ensureStatus = { state: 'ok', detail: '' }
      appLog('ensure-backend: backend is up')
      return
    }
  }
  // §9: the failure line is per-OS copy from the platform module (the mac one
  // names Gatekeeper; Windows says plainly that the service failed to start),
  // composed with the §2 serviceDiagnostics capture below.
  ensureStatus = { state: 'failed', detail: plat.SERVICE_START_FAILED_DETAIL }
  appLog(`ensure-backend: backend did not come up within ${VERIFY_DEADLINE_MS / 1000} s of install`)
  plat.serviceDiagnostics(appLog)
}

// §3 launch-time version compare: a healthy but outdated backend (the app
// bundle was swapped by an update, or replaced by hand) restarts onto this
// bundle's interpreter — never mid-execution; live executions drain first.
async function syncBackendVersion(py, running) {
  appLog(`ensure-backend: backend ${running} != app ${app.getVersion()} — `
    + 'restarting service once live executions finish')
  // This backend answered /health moments ago, so a transient probe failure
  // (a 5 s timeout while its thread pool is busy mid-execution, or a non-OK
  // answer) must NOT read as idle — §3: the service is never restarted
  // mid-execution. Only a backend that stays unanswering AND fails /health is
  // treated as down (nothing can be executing on it) so the install proceeds.
  let unknown = 0
  while (true) {
    const live = await executionsLiveProbe()
    if (live === false) break
    if (live === null || live === 'unknown') {
      if (++unknown >= 4 && !(await backendHealthy())) break
    } else {
      unknown = 0
    }
    await new Promise((r) => setTimeout(r, 30_000))
  }
  runServiceInstall(py, (err, stdout, stderr) => {
    if (err) {
      appLog(`ensure-backend: version-sync install failed: ${String(stderr || err.message).trim()}`)
      return
    }
    appLog(`ensure-backend: version-sync: ${String(stdout).trim()}`)
    void verifyBackendUp()
  })
}

// §3 ensure-backend: the registration's own line from `service status`, asked
// before any reinstall. Read-only, so it stays outside the install interlock,
// but bounded like every service child; a failed ask reads as whatever it
// printed (usually nothing), which is never "active".
function serviceStatusLine(py) {
  return new Promise((resolve) => {
    execFile(py, ['-m', 'autowright.service', 'status'], SERVICE_CHILD_OPTIONS, (_err, stdout) => {
      resolve(String(stdout || '').trim())
    })
  })
}

// §3: every platform's service manager prints a running job as a line that
// starts with "active" (launchd "active (pid …)", systemd "active (pid …)" /
// "active (unit running)", Task Scheduler "active (task running)"); "loaded,
// not active", "stopped …", "not installed" and a failed ask never do.
const ACTIVE_WAIT_MS = 20_000

// §3: a job the service manager reports active is a backend still coming up
// (the login item and the job start together at login, and the probe window
// is about a second), so keep probing 2 s apart for up to 20 s more before
// an install may bootout it. Non-null ≙ the backend's version once it answers.
async function awaitActiveBackend(py) {
  const line = await serviceStatusLine(py)
  if (!line.startsWith('active')) {
    appLog(`ensure-backend: service status says "${line || 'nothing'}" — installing`)
    return null
  }
  appLog('ensure-backend: the service manager reports the job active — waiting for /health')
  const deadline = Date.now() + ACTIVE_WAIT_MS
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000))
    const running = await backendVersion()
    if (running !== null) return running
  }
  appLog(`ensure-backend: the active job did not answer /health within ${ACTIVE_WAIT_MS / 1000} s — installing`)
  return null
}

async function ensureBackend() {
  const py = bundledPython()
  if (!py) return
  // Non-null ≙ our backend, identified and versioned. A foreign server on a
  // stale backend.json port lands in the install branch below, not in 'ok'.
  let running = await backendVersion()
  if (running === null) {
    ensureStatus = { state: 'installing', detail: '' }
    running = await awaitActiveBackend(py)
  }
  if (running !== null) {
    ensureStatus = { state: 'ok', detail: '' }
    if (running !== app.getVersion()) void syncBackendVersion(py, running)
    return
  }
  ensureStatus = { state: 'installing', detail: '' }
  runServiceInstall(py, (err, stdout, stderr) => {
    if (err) {
      const detail = String(stderr || err.message).trim()
      ensureStatus = { state: 'failed', detail: `Backend install failed: ${detail}` }
      appLog(`ensure-backend: install failed: ${detail}`)
      return
    }
    appLog(`ensure-backend: ${String(stdout).trim()}`)
    void verifyBackendUp()
  })
}

// §6 app-start firing: tell the backend this app process launched, once. The
// backend may still be coming up — re-read backend.json and retry every 2 s
// for up to 60 s, then let the occurrence lapse (no queue).
async function notifyAppStarted() {
  // One id for this app process (§19): the retry below cannot tell "the backend
  // never fired" from "it fired and the reply was lost", so the backend dedupes
  // on this instead of firing every app-start automation twice.
  const launchId = randomUUID()
  for (let i = 0; i < 30; i++) {
    const info = backendInfo()
    if (info) {
      try {
        const res = await fetch(`http://127.0.0.1:${info.port}/app-started`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${info.token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ launchId }),
          signal: AbortSignal.timeout(10_000),
        })
        if (res.ok) return
      } catch { /* backend not answering yet — retry */ }
    }
    await new Promise((r) => setTimeout(r, 2000))
  }
}

// §9.4 external-URL policy: the only schemes we hand to the OS. Result HTML is
// AI-authored and can echo attacker-controlled text from an incoming Discord or
// iMessage message, so a `file:` link in a result must not be able to launch a
// local app when the user clicks it. The one deep link is the §9 permission
// checklist's Settings pane.
const OPENABLE_SCHEMES = ['https:', 'http:', 'mailto:']
const SETTINGS_DEEP_LINK = plat.SETTINGS_DEEP_LINK // null where no deep link exists

function docKey(url) {
  try { const u = new URL(url); return `${u.protocol}//${u.host}${u.pathname}` } catch { return null }
}

// A hand-off the OS refuses (no handler for the scheme, a locked-down desktop)
// rejects the returned promise — logged and dropped, never an unhandled
// rejection: opening a link is best-effort and must not take the app with it.
function handOffFailed(url) {
  return (e) => appLog(`open-external: couldn't open ${url}: ${String(e?.message || e)}`)
}

function openExternalSafely(url) {
  if (typeof url !== 'string') return
  if (SETTINGS_DEEP_LINK && url.startsWith(SETTINGS_DEEP_LINK)) {
    shell.openExternal(url).catch(handOffFailed(url))
    return
  }
  let scheme
  try { scheme = new URL(url).protocol } catch { return }
  if (OPENABLE_SCHEMES.includes(scheme)) shell.openExternal(url).catch(handOffFailed(url))
}

// §9.4: both windows deny popups (routing allowed URLs to the browser) and
// refuse top-frame navigation. The preload hands the renderer the backend
// bearer token, which grants the full local API — it must never become
// reachable from a remote origin that navigated into one of our windows.
function hardenWindow(w) {
  w.webContents.setWindowOpenHandler(({ url }) => {
    openExternalSafely(url)
    return { action: 'deny' }
  })
  w.webContents.on('will-navigate', (e, url) => {
    // Same document (origin + path) is our own renderer — hash routing lives
    // there. Anything else is a real navigation away and is refused; if it is
    // a normal web link, hand it to the browser instead of silently dropping it.
    const here = docKey(w.webContents.getURL())
    if (here && docKey(url) === here) return
    e.preventDefault()
    openExternalSafely(url)
  })
}

function load(w, hash) {
  // AUTOWRIGHT_RENDERER_URL (§15): serve the same renderer source from a dev
  // server (HMR) instead of the built bundle. Configuration only — same code.
  const devUrl = process.env.AUTOWRIGHT_RENDERER_URL
  if (devUrl) {
    const u = new URL(devUrl)
    u.hash = hash
    w.loadURL(u.toString())
  } else {
    w.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
  }
}

// Right-click copy for selected text; text fields get the full edit menu.
function attachContextMenu(w) {
  w.webContents.on('context-menu', (_e, params) => {
    const items = params.isEditable
      ? [
          { role: 'cut', enabled: params.editFlags.canCut },
          { role: 'copy', enabled: params.editFlags.canCopy },
          { role: 'paste', enabled: params.editFlags.canPaste },
          { type: 'separator' },
          { role: 'selectAll' },
        ]
      : params.selectionText.trim()
        ? [{ role: 'copy' }]
        : []
    if (items.length) Menu.buildFromTemplate(items).popup({ window: w })
  })
}

// §9: how long a window may stay unshown before the watchdog below decides
// the app is invisibly alive and does something about it.
const WINDOW_WATCHDOG_MS = 15_000

// §9: the one way the shell says out loud that it cannot paint. The message
// always names app.log, which carries the detail. Never throws — a host with
// no dialog available (a driven test run) must still reach the log line.
// The box is the async one with no parent window: a synchronous error box
// would block the main process (and the watchdog's retry behind it) until
// the user dismissed it.
function showStartupError(detail) {
  showErrorMessage(`${detail}\n\nDetails are in ${path.join(logsDir(), 'app.log')}.`)
}

// An error box that never blocks and never throws: the caller returns at once
// and a host with no dialog available just leaves the appLog line as the
// whole report.
function showErrorMessage(message) {
  try {
    Promise.resolve(dialog.showMessageBox({ type: 'error', title: 'Autowright', message }))
      .catch(() => {})
  } catch { /* no dialog here — the appLog line is the whole report */ }
}

function createWindow(hash) {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    // §2 platform chrome: hidden title bar + pinned traffic lights on macOS
    // (§9 — one fixed spot for every window state, never re-derived from
    // layout), native frame elsewhere.
    ...plat.mainWindowChrome(),
    backgroundColor: '#090d14',
    // §9 never paint an unloaded window: shown on the first successful
    // renderer load below, never as an empty frame.
    show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs') },
  })
  // The window this call owns. The retry timer below outlives a closed window,
  // and reloading a successor would drop its WS and all renderer state — so
  // every deferred handler here checks it is still the current, live window.
  const w = win
  // §3 session end on Windows: Electron never fires powerMonitor 'shutdown'
  // there, so the main window's query-session-end sets the same sticky
  // pass-through. Only Windows emits the event — no platform check needed.
  w.on('query-session-end', () => { sessionEnding = true })
  // §9: a failed main-frame load (a dead §15 AUTOWRIGHT_RENDERER_URL — a
  // packaged dist file load doesn't fail) keeps the window hidden and retries
  // every second until the renderer is really there. Chromium fires
  // did-finish-load even after a failed navigation, so the per-attempt flag
  // is what separates the two. Logged once per failure streak, not per retry.
  winLoaded = false
  let failed = false
  let failStreak = 0
  let rendererDeaths = 0
  // §9: the death count resets only once a (re)loaded renderer has stayed
  // alive 60 s past its load — a reload that paints and then dies again at
  // once is a repeat, not a new death.
  let rendererAliveTimer = null
  // §9: set when a renderer that had already loaded (so the window was shown)
  // died and is being reloaded; that reload's finish leaves the window as the
  // user has it instead of showing and focusing it like a first load would.
  let reloadingShownWindow = false
  // What a failed main-frame load retries: the creation hash, until a
  // renderer-death reload moves it to `/app` (§9: never the creation hash).
  let retryHash = hash || '/app'
  // §9 watchdog: whatever else goes wrong, the app is never left running with
  // no window at all. Armed with the window, disarmed the moment it shows (or
  // the window closes), and fired below when neither happened in time.
  let watchdog = null
  const clearWatchdog = () => {
    if (watchdog) clearTimeout(watchdog)
    watchdog = null
  }
  win.webContents.on('did-start-loading', () => { failed = false })
  win.webContents.on('did-fail-load', (_e, code, desc, _url, isMainFrame) => {
    if (!isMainFrame) return
    failed = true
    failStreak += 1
    if (failStreak === 1) appLog(`window: renderer load failed (${code} ${desc}) — retrying every 1 s`)
    setTimeout(() => { if (win === w && !w.isDestroyed()) load(w, retryHash) }, 1000)
  })
  win.webContents.on('did-finish-load', () => {
    if (win !== w || w.isDestroyed()) return
    if (failed) return
    if (failStreak) {
      appLog(`window: renderer loaded after ${failStreak} failed attempt(s)`)
      failStreak = 0
    }
    // §9: a load that finished painted again; once it has stayed alive 60 s,
    // a later death is a new one and gets its own reload — a death before
    // then is still a repeat.
    if (rendererAliveTimer) clearTimeout(rendererAliveTimer)
    rendererAliveTimer = setTimeout(() => {
      rendererAliveTimer = null
      rendererDeaths = 0
    }, 60_000)
    rendererAliveTimer.unref()
    if (!winLoaded) {
      winLoaded = true
      clearWatchdog()
      if (reloadingShownWindow) reloadingShownWindow = false
      else {
        w.show()
        w.focus()
      }
    }
    // §9: a deep link that arrived while the renderer was still coming up has
    // been held since — its `open-target` listener exists only now, so replay
    // it here, exactly once.
    if (pendingTarget) {
      w.webContents.send('open-target', pendingTarget)
      pendingTarget = null
    }
  })
  // §9: the renderer can die before it ever loads (out of memory, a crash in
  // the bundle) — did-finish-load then never comes and the window would stay
  // hidden forever, a live app with nothing to click. One reload is worth
  // trying; a second death has nothing left to paint, so say so and quit
  // rather than staying resident and invisible.
  win.webContents.on('render-process-gone', (_e, details) => {
    if (win !== w || w.isDestroyed()) return
    if (rendererAliveTimer) {
      clearTimeout(rendererAliveTimer)
      rendererAliveTimer = null
    }
    rendererDeaths += 1
    appLog(`window: renderer process gone (${details?.reason || 'unknown'}) — death ${rendererDeaths}`)
    if (rendererDeaths === 1) {
      // §9: the reload lands on `/app`, never the creation hash (a
      // notification's target can be hours old), and counts as a fresh load:
      // clearing winLoaded makes showApp hold a deep link arriving meanwhile
      // in pendingTarget, which did-finish-load replays once the reloaded
      // renderer has its `open-target` listener again.
      reloadingShownWindow = winLoaded
      winLoaded = false
      retryHash = '/app'
      load(w, retryHash)
      return
    }
    clearWatchdog()
    showStartupError('The app window kept crashing.')
    quitUi()
  })
  load(win, hash || '/app')
  // §9 watchdog: if the window still hasn't shown by now, act rather than sit
  // there invisible. A renderer that merely loaded slowly gets shown anyway;
  // when every attempt so far failed there is nothing to paint, so the failure
  // is reported once instead — the 1 s retry above keeps running behind it and
  // still shows the window if the renderer ever arrives.
  watchdog = setTimeout(() => {
    watchdog = null
    if (winLoaded || !win) return
    if (failStreak > 0) {
      appLog(`window: nothing loaded ${WINDOW_WATCHDOG_MS / 1000} s after creation `
        + `(${failStreak} failed attempt(s)) — still retrying`)
      showStartupError('The app window could not load.')
      return
    }
    appLog(`window: still hidden ${WINDOW_WATCHDOG_MS / 1000} s after creation — showing it anyway`)
    winLoaded = true
    win.show()
    win.focus()
  }, WINDOW_WATCHDOG_MS)
  attachContextMenu(win)
  win.on('closed', () => { clearWatchdog(); win = null; pendingTarget = null })
  hardenWindow(win)
}

function showApp(hash) {
  // Fresh window: load straight at the target. Existing window: hand the
  // target over IPC — a reload would drop the WS and all renderer state. A
  // renderer that hasn't loaded yet has no `open-target` listener, so the
  // target is held instead and replayed on the first successful load — the
  // one flag that knows a load really succeeded (isLoading() reads false
  // between a failed navigation and the 1 s retry, and the deep link used to
  // be sent into that gap and lost).
  // §9: no window is created once a quit has begun: a relaunching update's
  // second-instance (or a deep link) mid-quit would leave one the quit then
  // has to fight.
  if (quitting) return
  if (!win) createWindow(hash)
  else if (hash) {
    if (winLoaded) win.webContents.send('open-target', hash)
    else pendingTarget = hash
  }
  // §9: an unloaded window stays hidden — it shows itself on the first
  // successful load (createWindow's guard), never as an empty frame.
  // §13: a minimized window is restored first — showing one without restoring
  // it looks like a no-op (the same rule as the dock activate below).
  if (winLoaded) {
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
  }
}

// Each variant is decoded from disk once — the 60 s poll below asks for the
// same two images for the life of the app.
const trayImages = new Map()

function trayIcon(alert) {
  const cached = trayImages.get(alert)
  if (cached) return cached
  // §13: red alert dot when any automation failed. Asset + template flag come
  // from the platform module (on macOS the alert variant is a pre-rendered
  // non-template PNG so the dot stays red on light and dark menu bars).
  const spec = plat.trayIconSpec(alert)
  const icon = nativeImage.createFromPath(path.join(__dirname, spec.file))
  icon.setTemplateImage(spec.template)
  trayImages.set(alert, icon)
  return icon
}

// The alert state the tray icon is showing. Both writers (the poll below and
// the renderer's §13 tray-alert IPC) go through setTrayAlert, so the icon is
// re-set only on a real transition and the two can never disagree about it.
let trayAlert = false

function setTrayAlert(alert) {
  if (!tray || alert === trayAlert) return
  trayAlert = alert
  tray.setImage(trayIcon(alert))
}

// §13: a tray that failed to create is remembered, so the 60 s settings poll
// doesn't retry (and log) the same failure every minute; only the
// `menuBarIcon` setting going from off to on tries again. `lastMenuBarIcon`
// is the value the previous reconcile saw (null before the first one).
let trayFailed = false
let lastMenuBarIcon = null

function createTray() {
  try {
    tray = new Tray(trayIcon(false))
    trayAlert = false
    tray.setToolTip('Autowright')
    tray.on('click', () => togglePanel())
  } catch (err) {
    trayFailed = true
    throw err
  }
  trayFailed = false
}

// §13: the renderer feeds the alert dot over IPC while it's alive, but the app
// can sit tray-only with no renderer at all (window closed, panel never
// opened) — main polls the backend itself so a scheduled failure still lights
// the dot and a later success clears it.
async function refreshTrayAlert() {
  if (!tray) return
  const info = backendInfo()
  if (!info) return
  try {
    const res = await fetch(`http://127.0.0.1:${info.port}/automations`, {
      headers: { Authorization: `Bearer ${info.token}` },
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) return
    const autos = await res.json()
    // §13: failed or §4.1 overdue only — same predicate as the renderer's.
    setTrayAlert(autos.some((a) => a.lastStatus === 'failed'
      || (a.problems || []).some((p) => p.kind === 'overdue')))
  } catch { /* backend down — keep the current icon */ }
}

// §4.9: the shell owns two OS-side settings effects — the macOS login item
// (`login`) and the tray icon (`menuBarIcon`). Reconciled from the backend's
// stored settings at startup and on the periodic poll (a tray-only app must
// follow CLI changes too); the renderer pushes the same shape on every
// settings change.
let automaticUpdateTimer = null

// §3: the 24 h check timer is stopped the same way the 60 s shell poll is —
// at quit and at the reset. A timer that outlives either one fires an update
// check against a backend that is going away and logs into a deleted logs
// root. The §4.9 toggle-off path clears it through here too.
function stopUpdateTimer() {
  if (automaticUpdateTimer) { clearInterval(automaticUpdateTimer); automaticUpdateTimer = null }
}

// §5 executions data dir. Relocatable, so its location is only known from the
// backend's settings — the periodic sync above carries it (the renderer's
// apply-settings push never does). Feeds the reveal-path root check below.
let dataRoot = null

// `trusted` says where this shape came from: the backend's own /settings (the
// startup + poll sync) may move the §5 data root, the renderer's
// apply-settings push may not — a dataPath from there would let the renderer
// pick the reveal-path roots for itself.
function applyShellSettings(s, { trusted = false } = {}) {
  // Each effect is guarded on its own: one that throws (a tray that won't
  // create on this host, a login item the OS refuses) must never take the
  // ones after it with it — the §3 update-check timer is last in line.
  try {
    if (trusted && typeof s?.dataPath === 'string' && s.dataPath) dataRoot = s.dataPath
  } catch (err) {
    appLog(`settings: applying the data path failed: ${String(err?.message || err)}`)
  }
  // §4.9 login reconcile is per-OS (§2 applyLoginItem): the Electron login
  // item on macOS/Windows, the XDG-autostart .desktop file on Linux.
  try {
    if (caps.loginItem && typeof s?.login === 'boolean') plat.applyLoginItem(app, s.login)
  } catch (err) {
    appLog(`settings: applying the login item failed: ${String(err?.message || err)}`)
  }
  try {
    if (caps.trayPanel && typeof s?.menuBarIcon === 'boolean') {
      const turnedOn = s.menuBarIcon && lastMenuBarIcon === false
      lastMenuBarIcon = s.menuBarIcon
      if (s.menuBarIcon && !tray && (!trayFailed || turnedOn)) {
        createTray()
        void refreshTrayAlert()
      } else if (!s.menuBarIcon && tray) {
        tray.destroy()
        tray = null
        // §9/§13: the panel is a window with no way left to reach it once the
        // tray is gone — and a merely hidden window still suppresses
        // window-all-closed, so the close rule below would never fire again.
        // Destroy it; the next tray click builds a fresh one lazily.
        destroyPanel()
        // §9 close rule, re-evaluated here: on a platform with no dock the
        // tray was the only thing keeping a windowless app reachable. With
        // both gone there is nothing left to click, so quit rather than sit
        // there running and invisible.
        if (!caps.dockIcon && !win) quitUi()
      }
    }
  } catch (err) {
    appLog(`settings: applying the tray icon failed: ${String(err?.message || err)}`)
  }
  // §3 automatic update check (§4.9, on by default): off→on — which includes a
  // launch with the setting on — checks immediately, then every 24 h; on→off
  // clears the timer. Nothing about past checks is persisted. Failures are
  // silent, and an automatic check never starts a download.
  try {
    if (caps.updates && typeof s?.automaticUpdateCheck === 'boolean') {
      if (s.automaticUpdateCheck && !automaticUpdateTimer) {
        void fetchUpdateState()
        automaticUpdateTimer = setInterval(() => { void fetchUpdateState() }, 24 * 60 * 60_000)
      } else if (!s.automaticUpdateCheck) {
        stopUpdateTimer()
      }
    }
  } catch (err) {
    appLog(`settings: applying the automatic update check failed: ${String(err?.message || err)}`)
  }
}

// Two failures live here and they are not the same thing: the fetch failing
// means the backend is down (silent, expected, retried on the next poll),
// while applyShellSettings throwing is a shell-side bug (tray create, login
// item, update timer) that used to read as "backend down" and vanish. Keep
// them apart so only the first one is silent — the §4.9 apply-settings IPC
// lets the same throw surface too, it just surfaces it to the renderer.
async function syncShellSettings() {
  const info = backendInfo()
  if (!info) return
  let settings = null
  try {
    const res = await fetch(`http://127.0.0.1:${info.port}/settings`, {
      headers: { Authorization: `Bearer ${info.token}` },
      signal: AbortSignal.timeout(5000),
    })
    if (!res.ok) return
    settings = await res.json()
  } catch { /* backend down — keep the current state */ }
  if (!settings) return
  try {
    applyShellSettings(settings, { trusted: true })
  } catch (err) {
    appLog(`settings: applying shell settings failed: ${String(err?.message || err)}`)
  }
}

// Tray-click toggle guard: on macOS the focused panel blurs (and hides)
// before the tray click arrives, so a bare isVisible() check would always
// re-show. A click landing right after a blur-hide means "close" — swallow it.
let panelHiddenAt = 0
// §13 re-anchor: the point the panel was opened from (cursor + display) and
// its current height. `resize-panel` re-runs the platform placement with the
// real new height, so a bottom-anchored panel (Windows) keeps hugging the
// taskbar as it grows; a top-anchored one (macOS) lands on the same pixels.
let panelAnchor = null
let panelHeight = 420

// §13: the panel is deliberately non-closable (Cmd+W must be a no-op for it),
// so every path that needs it gone destroys it outright. A destroyed panel
// forgets its measured height and anchor — the next one opens at the 420 px
// default and re-anchors on its first measurement, never at the dead panel's
// grown height. Idempotent: a dead or missing panel is left alone.
function destroyPanel() {
  if (panel && !panel.isDestroyed()) panel.destroy()
  panel = null
  panelHeight = 420
  panelAnchor = null
}

function repositionPanel() {
  if (!panel || !panelAnchor) return
  const pos = plat.panelPosition(panelAnchor.pt, panelAnchor.display, panelHeight)
  panel.setPosition(pos.x, pos.y)
}

function togglePanel() {
  // §9: no panel is created once a quit has begun (before-quit destroyed it).
  if (quitting) return
  if (panel && panel.isVisible()) { panel.hide(); return }
  if (Date.now() - panelHiddenAt < 250) return
  if (!panel) {
    panel = new BrowserWindow({
      width: 334,
      height: 420,
      show: false,
      frame: false,
      resizable: false,
      movable: false,
      // §13: the default app menu stays active, so Cmd+W/Cmd+M would destroy
      // or minimize the focused panel and strand the tray toggle on a dead
      // reference — the panel opts out of both (and fullscreen).
      closable: false,
      minimizable: false,
      fullscreenable: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      // §2 platform chrome: transparency + vibrancy on macOS, plain elsewhere.
      ...plat.panelWindowExtras(),
      webPreferences: { preload: path.join(__dirname, 'preload.cjs') },
    })
    // §13 (macOS): menu-bar panels follow the user across Spaces — without
    // this, opening the panel over a fullscreen app switches Spaces.
    plat.panelAfterCreate(panel)
    load(panel, '/menubar')
    attachContextMenu(panel)
    hardenWindow(panel)
    const w = panel
    w.on('blur', () => { panelHiddenAt = Date.now(); if (!w.isDestroyed()) w.hide() })
    // §13: only this panel's own close clears the reference; an old panel's
    // late `closed` after a destroy-then-recreate must not wipe the live one.
    w.on('closed', () => { if (panel === w) panel = null })
    // §13: a dead renderer would leave the tray showing a blank panel forever;
    // destroy it so the next tray click builds a fresh one.
    w.webContents.on('render-process-gone', (_e, details) => {
      if (panel !== w) return
      appLog(`panel: renderer process gone (${details?.reason || 'unknown'})`)
      destroyPanel()
    })
  }
  const pt = screen.getCursorScreenPoint()
  panelAnchor = { pt, display: screen.getDisplayNearestPoint(pt) }
  repositionPanel()
  panel.show()
}

// §3 CLI on PATH: the shell owns shim *creation* — explicit (the §4.9 card's
// Install button), silent, and always into the user-owned ~/.local/bin — the
// only shim location; no admin prompt anywhere, and never automatic.
// Interpreter comes from backend.json's `python`, so dev and prod run the
// same code. AUTOWRIGHT_SHIM is the §15 test knob (mirrored in service.py):
// it overrides the location and skips the PATH probe.
const SHIM_MARKER = plat.SHIM_MARKER
const shimText = plat.shimText

function shimPaths() {
  return process.env.AUTOWRIGHT_SHIM ? [process.env.AUTOWRIGHT_SHIM] : [plat.defaultShimPath()]
}

// §3: GUI apps inherit a stripped PATH, so ask the login shell whether
// ~/.local/bin is reachable. Cached per app run; any failure = not on PATH.
// Only feeds the §4.9 card's PATH hint — install goes to ~/.local/bin anyway.
let userBinOnPath = null
async function userBinOnLoginPath() {
  if (process.env.AUTOWRIGHT_SHIM) return true
  if (userBinOnPath !== null) return userBinOnPath
  const loginPath = await plat.readLoginShellPath()
  userBinOnPath = loginPath !== null
    && loginPath.split(path.delimiter).includes(path.dirname(shimPaths()[0]))
  return userBinOnPath
}

// §3: the shim path is only ever written when absent or a regular file. A
// symlink there, dangling or not, is foreign: every read, write and chmod
// would land wherever the link points. lstat, never stat, so the link itself
// is what gets judged.
function shimIsSymlink(shim) {
  try {
    return fs.lstatSync(shim).isSymbolicLink()
  } catch {
    return false
  }
}

async function cliStatus() {
  const python = backendInfo()?.python
  const onPath = await userBinOnLoginPath()
  const shim = shimPaths()[0]
  if (shimIsSymlink(shim)) return { state: 'foreign', path: shim, onPath }
  let current
  try {
    current = fs.readFileSync(shim, 'utf-8')
  } catch {
    return { state: 'missing', path: shim, onPath }
  }
  if (!current.includes(SHIM_MARKER)) return { state: 'foreign', path: shim, onPath }
  // §3: ours but not executable (a write over an existing file keeps its old
  // mode): heal in place, the same probe-time heal a stale-interpreter shim
  // gets. A failed chmod only logs: the next status read retries it.
  try {
    if ((fs.statSync(shim).mode & 0o100) === 0) fs.chmodSync(shim, 0o755)
  } catch (e) {
    appLog(`cli-status: couldn't heal ${shim}: ${e?.message || e}`)
  }
  if (!python || current === shimText(python)) return { state: 'installed', path: shim, onPath }
  // Ours but pointing elsewhere: heal in place (§3 — a user-owned file
  // rewrites without a directory write). A failed rewrite only logs: the
  // next status read retries it.
  try {
    fs.writeFileSync(shim, shimText(python), { mode: 0o755 })
    // `mode` only applies where writeFileSync *creates* the file, so a heal
    // rewrite leaves whatever mode the old shim had — set it outright.
    fs.chmodSync(shim, 0o755)
  } catch (e) {
    appLog(`cli-status: couldn't heal ${shim}: ${e?.message || e}`)
  }
  return { state: 'installed', path: shim, onPath }
}

function cliInstall() {
  const python = backendInfo()?.python
  if (!python) return { ok: false, error: 'The backend is not running yet — try again in a moment.' }
  // §3: plain writes into the user-owned dir — no dialog, no password.
  const shim = shimPaths()[0]
  // Something else already owns that name (another tool's `autowright`, a
  // hand-written script): a file we did not write is never overwritten — the
  // §4.9 card says so and leaves the choice to the user. A symlink is foreign
  // whatever it points at (§3).
  if (shimIsSymlink(shim)) {
    return { ok: false, error: `A different autowright command already exists at ${shim}. Remove it first.` }
  }
  try {
    if (!fs.readFileSync(shim, 'utf-8').includes(SHIM_MARKER)) {
      return { ok: false, error: `A different autowright command already exists at ${shim}. Remove it first.` }
    }
  } catch { /* nothing there yet — the write below creates it */ }
  try {
    fs.mkdirSync(path.dirname(shim), { recursive: true })
    fs.writeFileSync(shim, shimText(python), { mode: 0o755 })
    // `mode` only applies where writeFileSync creates the file: a write over
    // an existing shim keeps its old mode, so set it after every write.
    fs.chmodSync(shim, 0o755)
    appLog(`cli-install: CLI installed at ${shim}`)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: String(e?.message || e) }
  }
}

// §3 cli-uninstall (§4.9 disable confirm): remove the ours-marker shim;
// foreign files never touched. A failed delete reports an error message the
// §4.9 card toasts.
function cliUninstall() {
  const p = shimPaths()[0]
  // §3: a symlink is foreign — never followed, never unlinked.
  if (shimIsSymlink(p)) return { ok: true }
  let text
  try {
    text = fs.readFileSync(p, 'utf-8')
  } catch {
    return { ok: true }
  }
  if (!text.includes(SHIM_MARKER)) return { ok: true }
  try {
    fs.unlinkSync(p)
    appLog(`cli-uninstall: removed ${p}`)
    return { ok: true }
  } catch (e) {
    return { ok: false, hint: `Couldn’t delete ${p} — ${e?.message || e}` }
  }
}

ipcMain.handle('backend-info', () => backendInfo())
ipcMain.handle('backend-status', () => ensureStatus)
ipcMain.handle('cli-status', () => cliStatus())
ipcMain.handle('cli-install', () => cliInstall())
ipcMain.handle('cli-uninstall', () => cliUninstall())
// IPC arguments come from the renderer and are validated here, at the trust
// boundary: a bad type is a no-op, never a throw (read-request-log's basename
// check is the precedent).
ipcMain.handle('open-app', (_e, hash) => {
  if (hash !== undefined && typeof hash !== 'string') return
  showApp(hash)
  if (panel) panel.hide()
})
ipcMain.handle('resize-panel', (_e, h) => {
  if (!Number.isFinite(h)) return
  if (!panel) return
  panelHeight = Math.min(Math.max(Math.round(h), 120), 640)
  panel.setSize(334, panelHeight)
  // §13: re-anchor at the real new height — a bottom-anchored panel would
  // otherwise drift off the taskbar as its content grows.
  repositionPanel()
})

// §5 reveal roots: the only trees a reveal may point into — the app-support
// home (memory, drafts, harness workspaces), the logs dir, and the executions
// data dir. Result HTML is AI-authored and can echo attacker-controlled text
// (§9.4), so a path it hands us must not be able to reach an arbitrary file,
// and openPath on the wrong directory would *launch* something.
function revealRoots() {
  return [appSupportDir(), logsDir(), ...(dataRoot ? [dataRoot] : [])]
}

function insideRevealRoots(abs) {
  return revealRoots().some((root) => {
    // The candidate is a real path (reveal-path resolves symlinks first), so
    // the roots are compared as real paths too: a root that sits behind a
    // symlink (macOS /var -> /private/var) must still match its own files.
    let r = path.resolve(root)
    try { r = fs.realpathSync(r) } catch { /* a missing root keeps its resolved spelling */ }
    return abs === r || abs.startsWith(r + path.sep)
  })
}

ipcMain.handle('reveal-path', async (_e, p) => {
  if (typeof p !== 'string' || !p) return
  // `..` is collapsed here, so a traversal cannot dress itself up as a path
  // under one of the roots.
  const resolved = path.resolve(p === '~' || p.startsWith('~/')
    ? path.join(os.homedir(), p.slice(1))
    : p)
  // §9: the root check and the directory/extension decision run on the real
  // path, so a symlink planted inside the executions dir (a step script could
  // write one) can't point a reveal outside the roots or at a bundle. A path
  // that can't be resolved is a no-op.
  let abs
  try { abs = fs.realpathSync(resolved) } catch { return }
  if (!insideRevealRoots(abs)) {
    // The data dir moves (§5), so a miss may just mean our cached copy is one
    // poll behind — refresh once, then give up.
    await syncShellSettings()
    if (!insideRevealRoots(abs)) return
  }
  let isDir = false
  try { isDir = fs.statSync(abs).isDirectory() } catch { /* fall through */ }
  // §2 platform rule: reveal shows a location, never starts something — on
  // macOS an extension-carrying directory is a bundle and openPath on one
  // *launches* it, so only extension-less plain dirs open in place.
  if (plat.revealPrefersOpen(abs, isDir)) void shell.openPath(abs)
  else shell.showItemInFolder(abs)
})
// §9: every native dialog below is sheet-attached to the main window when
// there is one. `win` is null whenever the window was closed (tray-only) or
// destroyed mid-flow, and Electron wants `undefined` — not null — for a
// parentless dialog, so the intent is spelled out here once instead of
// passing a maybe-null window four times.
function dialogParent() {
  return win ?? undefined
}

ipcMain.handle('pick-folder', async (_e, defaultPath) => {
  const opts = { properties: ['openDirectory', 'createDirectory'] }
  if (typeof defaultPath === 'string' && defaultPath) opts.defaultPath = defaultPath
  const r = await dialog.showOpenDialog(dialogParent(), opts)
  return r.canceled ? null : r.filePaths[0]
})
// §5.1 transfer archives: native save/open dialogs live in main; the renderer
// moves the bytes to/from the backend itself (§19).
const ARCHIVE_MAX_BYTES = 64 * 1024 * 1024
ipcMain.handle('save-file', async (_e, defaultName, data) => {
  // Both arguments cross the trust boundary: the name only ever names a file
  // inside the downloads dir (never a path of its own steering the dialog
  // elsewhere), and the bytes have to really be bytes. Either one wrong is a
  // no-op, never a throw.
  if (typeof defaultName !== 'string' || !defaultName) return null
  if (!(Buffer.isBuffer(data) || data instanceof Uint8Array || data instanceof ArrayBuffer)) return null
  const base = path.basename(defaultName)
  const r = await dialog.showSaveDialog(dialogParent(), {
    defaultPath: path.join(app.getPath('downloads'), base),
    // §5.1/§22.7: an archive save names its own type, so the picked path keeps
    // the .autowright extension the catalog and the importer both require.
    ...(base.toLowerCase().endsWith('.autowright')
      ? { filters: [{ name: 'Autowright automation', extensions: ['autowright'] }] }
      : null),
  })
  if (r.canceled || !r.filePath) return null
  // Async IO: archives run to 64 MB (§5.1) and the target can be a network
  // volume — a sync write here would stall the whole main process.
  try {
    await fs.promises.writeFile(r.filePath, Buffer.from(data))
  } catch (e) {
    // §9.2: the renderer toasts this text (minus the IPC wrapper it strips) —
    // plain words, never an ENOENT dump with the raw path in front.
    const why = e?.code === 'ENOENT' ? 'that folder no longer exists'
      : e?.code === 'EACCES' || e?.code === 'EPERM' || e?.code === 'EROFS' ? 'that location is read-only'
      : e?.code === 'ENOSPC' ? 'the disk is full'
      : String(e?.message || e)
    throw new Error(`Couldn't save the file: ${why}`)
  }
  return r.filePath
})
ipcMain.handle('open-archive', async () => {
  const r = await dialog.showOpenDialog(dialogParent(), {
    properties: ['openFile'],
    filters: [{ name: 'Autowright automation', extensions: ['autowright'] }],
  })
  if (r.canceled || !r.filePaths[0]) return null
  const p = r.filePaths[0]
  try {
    // §5.1: archives cap at 64 MB — a bigger file is refused up front rather
    // than read into the main process to be rejected by the backend.
    const { size } = await fs.promises.stat(p)
    if (size > ARCHIVE_MAX_BYTES) return { error: 'The archive is larger than 64 MB.' }
    return { name: path.basename(p), data: await fs.promises.readFile(p) }
  } catch (e) {
    // A file the user picked but we cannot read (permissions, a volume that
    // went away mid-dialog) answers null — the renderer treats it like a
    // cancel instead of hanging on a read that never lands.
    appLog(`open-archive: couldn't read ${p}: ${e?.message || e}`)
    return null
  }
})
// §22.3 add marketplace: the native picker for a marketplace catalog. Unlike
// open-archive nothing is read here - the backend reads the file itself (§22.4),
// so only the path crosses back.
ipcMain.handle('open-catalog', async () => {
  const r = await dialog.showOpenDialog(dialogParent(), {
    properties: ['openFile'],
    filters: [{ name: 'Marketplace catalog', extensions: ['yaml', 'yml'] }],
  })
  if (r.canceled || !r.filePaths[0]) return null
  return { path: r.filePaths[0] }
})
// §9.3 developer log overlay: tail of each existing log file. Polled by the
// renderer while the overlay is open — no watchers, nothing runs while closed.
const LOG_FILES = ['app.log', 'backend.out.log', 'backend.err.log', 'vite.log']
// Async IO: the overlay polls at 1 Hz, and the logs can sit on a slow or
// network-backed volume — a sync open/stat/read of four files would stall the
// whole main process on every tick.
ipcMain.handle('tail-logs', async () => {
  const dir = logsDir()
  const out = []
  for (const name of LOG_FILES) {
    let handle
    try { handle = await fs.promises.open(path.join(dir, name), 'r') } catch { continue }
    try {
      const { size } = await handle.stat()
      const start = Math.max(0, size - 64 * 1024)
      const buf = Buffer.alloc(size - start)
      // Only what was really read: a file that shrank between the stat and
      // the read (a rotation) would otherwise stringify the buffer's unwritten
      // tail as NUL padding into the overlay.
      const { bytesRead } = await handle.read(buf, 0, buf.length, start)
      let text = buf.subarray(0, bytesRead).toString('utf-8')
      if (start > 0) {
        const nl = text.indexOf('\n')
        if (nl !== -1) text = text.slice(nl + 1)
      }
      out.push({ name, text })
    } catch { /* unreadable mid-rotation — skip this poll */ } finally {
      await handle.close()
    }
  }
  return out
})
// §9.3 Requests tab: §5 request-log files under <logs>/requests — name list
// (descending ≙ newest first, the timestamp prefix makes name order
// chronological) + one-file read. `name` must be a plain basename.
// Async IO for the same reason as tail-logs: the directory grows with every
// request (§5) and both sides of the tab are polled while the overlay is open.
ipcMain.handle('list-request-logs', async () => {
  try {
    return (await fs.promises.readdir(path.join(logsDir(), 'requests')))
      .filter((n) => n.endsWith('.log')).sort().reverse()
  } catch { return [] }
})
ipcMain.handle('read-request-log', async (_e, name) => {
  if (typeof name !== 'string' || name !== path.basename(name)) return null
  try {
    return await fs.promises.readFile(path.join(logsDir(), 'requests', name), 'utf-8')
  } catch { return null }
})
// §9.5 report modal: OS details for the info block — the renderer has no
// other source (getSystemVersion is the marketing macOS version, not the
// Darwin kernel release).
ipcMain.handle('platform-info', () => ({
  platform: process.platform,
  osName: plat.OS_NAME, // §4.1 display form — the §9.5 OS line never hardcodes it
  release: process.getSystemVersion(),
  arch: process.arch,
  // Bundle version — the §9.5 fallback while the store's /state version
  // hasn't landed (the block must never show a bare "v").
  version: app.getVersion(),
  // §4.9: the shell's tray capability — the "Show in the menu bar" row
  // renders only where a tray exists (false on Linux, §13).
  trayPanel: !!caps.trayPanel,
}))
ipcMain.handle('apply-settings', (_e, s) => applyShellSettings(s))
ipcMain.handle('tray-alert', (_e, on) => {
  setTrayAlert(!!on)
})

// §3 in-app updates (electron-updater on every OS): manual-only — nothing here
// runs until the §9.4 "Check for updates" button calls update-check. One feed
// directory per OS/arch under release/ in the repo, served over
// raw.githubusercontent.com and rewritten by each OS's release script.
// Null where the platform declares no update capability, or has one but no
// channel yet: every §3 update path checks it and answers the degraded line,
// so the IPC surface stays byte-identical for the renderer either way.
const UPDATE_FEED = caps.updates ? plat.updateFeedUrl(process.arch) : null

// §3: the one line every update path answers when this platform serves no feed
// — check carries it as its error detail (the §9.4 page renders it instead of
// the generic network copy), download and install refuse with it.
const NO_UPDATES_ERROR = 'Updates are not supported on this platform yet.'

// §9.4 compare: numeric on dot-split parts, ignoring a leading `v`; a
// malformed version counts as not newer.
function isNewerVersion(remote, current) {
  const a = String(remote).replace(/^v/, '').split('.').map(Number)
  const b = String(current).split('.').map(Number)
  if (a.some((n) => !Number.isFinite(n))) return false
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? 0) - (b[i] ?? 0)
    if (d !== 0) return d > 0
  }
  return false
}

// §3 update-available: any check — manual or automatic — that finds a newer
// version remembers it here and tells the main window; an invoke handler
// answers the remembered value so a renderer that boots after the check still
// learns it. A later up-to-date answer clears it (feed rolled back, or the
// user updated by hand); errors leave it alone; otherwise it lives until the
// restart that installs.
let availableVersion = null

function recordAvailable(version) {
  if (availableVersion === version) return
  availableVersion = version
  win?.webContents.send('update-available', version)
}

// §3 per-OS update machinery, chosen by the §2 module's marker — never by
// sniffing process.platform here. All three markers name electron-updater
// classes against the generic provider: `mac` is MacUpdater (Squirrel.Mac
// underneath), `nsis` is NsisUpdater (Windows), `appimage` is AppImageUpdater
// (Linux). One shared code path — the renderer-facing IPC surface is identical
// every way, so no renderer code forks; darwin adds only the Squirrel
// hand-off tail below.

// Built on first use and never at module load: requiring main.cjs must not
// turn into a feed fetch, and electron-updater checks nothing until asked.
let generic = null

// §3: the updater's error stream — the message a refused install carries. The
// NSIS/AppImage classes report a failed installer spawn (or nothing staged)
// through `error` and return nothing from `quitAndInstall`, so the last
// error is what the §9.4 card gets to render instead of a silent no-op.
let lastUpdaterError = null

function genericUpdater() {
  if (generic) return generic
  const { MacUpdater, NsisUpdater, AppImageUpdater } = require('electron-updater')
  const Updater = { mac: MacUpdater, nsis: NsisUpdater, appimage: AppImageUpdater }[plat.UPDATER]
  // No publisherName until a certificate exists (§3 footgun): with one set,
  // electron-updater verifies the downloaded installer's Authenticode
  // identity, and every update against an unsigned artifact fails.
  const u = new Updater({ provider: 'generic', url: UPDATE_FEED })
  // §3 manual-only rule: a check just reads the feed yml, downloads and
  // installs are user-initiated, and nothing may install itself on quit — the
  // update-install handler's live-execution gate is the only way in. (On
  // darwin the off flag also keeps MacUpdater from engaging Squirrel during
  // the download; the hand-off tail below runs that under the handler's
  // control instead.)
  u.autoDownload = false
  u.autoInstallOnAppQuit = false
  // §15 dev parity: an unpackaged (dev/driven) launch runs the same real
  // update path — without this electron-updater silently skips every check
  // while app.isPackaged is false. A packaged app ignores the flag, and the
  // provider config always comes from the constructor options above, never
  // from a dev-app-update.yml (which exists only to name the updater cache
  // directory, §3).
  u.forceDevUpdateConfig = true
  // §3: no .blockmap is published for the mac zip — skip the differential
  // attempt that could only ever fail and fall back to the full download.
  if (plat.UPDATER === 'mac') u.disableDifferentialDownload = true
  const log = (m) => appLog(`update: ${String(m?.stack || m?.message || m)}`)
  u.logger = { info: log, warn: log, error: log, debug: () => {} }
  // Registered once, here: an unlistened `error` on an EventEmitter throws,
  // and the recorded message is what update-install answers with below.
  u.on('error', (err) => { lastUpdaterError = String(err?.message || err) })
  // §3 determinate progress: percent on the update-progress IPC, or null
  // (indeterminate bar) when the server sent no total to divide by.
  u.on('download-progress', (p) => {
    const percent = p?.total && Number.isFinite(p?.percent)
      ? Math.min(100, Math.round(p.percent))
      : null
    win?.webContents.send('update-progress', percent)
  })
  generic = u
  return generic
}

// §3 check: the feed read, mapped onto the `{ state }` shape and the §9.4
// version-compare rule — electron-updater's own "is this newer" answer is
// never consulted, so every platform agrees on what counts as an update.
async function fetchUpdateState() {
  // §3: no feed on this platform — answer the error state carrying the plain
  // no-updates line, so the §9.4 page never tells the user to retry something
  // that cannot succeed. A real feed failure carries no detail (generic copy).
  if (!UPDATE_FEED) return { state: 'error', error: NO_UPDATES_ERROR }
  try {
    const result = await genericUpdater().checkForUpdates()
    const version = String(result?.updateInfo?.version ?? '')
    if (!isNewerVersion(version, app.getVersion())) {
      recordAvailable(null)
      return { state: 'uptodate' }
    }
    recordAvailable(version)
    return { state: 'available', version }
  } catch {
    return { state: 'error' }
  }
}

// §3 darwin tail of update-download: after downloadUpdate(), MacUpdater has
// verified the zip against the feed's sha512 and pointed Squirrel.Mac at its
// own loopback proxy — but with the manual-only flags off it never engages
// Squirrel. Engage it here and settle only once Squirrel staged the bundle:
// the §9.4 bar holds 100% meanwhile, and an unsigned dev build surfaces
// Squirrel's real signature error (no dev fork). Squirrel can also emit none
// of its events (a hand-off it silently drops) — without the give-up timer
// the §9.4 About page would wait on this promise forever. Resolves null on
// staged, the error text otherwise.
// The version Squirrel staged this run: downloadUpdate records it on an ok
// staging; null until one is staged.
let stagedVersion = null

function stageWithSquirrel(updater) {
  return new Promise((resolve) => {
    if (updater.squirrelDownloadedUpdate) return resolve(null)
    let settled = false
    const settle = (error) => {
      if (settled) return
      settled = true
      clearTimeout(giveUp)
      autoUpdater.removeListener('update-downloaded', onDone)
      autoUpdater.removeListener('error', onErr)
      resolve(error)
    }
    const giveUp = setTimeout(() => settle('the updater stopped responding'), 10 * 60_000)
    const onDone = () => settle(null)
    const onErr = (err) => settle(String(err?.message || err))
    autoUpdater.on('update-downloaded', onDone)
    autoUpdater.on('error', onErr)
    try {
      autoUpdater.checkForUpdates()
    } catch (err) {
      settle(String(err?.message || err))
    }
  })
}

// §3 download: electron-updater streams the binary, verifies it against the
// feed, and emits real progress events — no temp files and no re-implemented
// percent math here. The check runs first — with autoDownload off it only
// reads the feed yml — which is what arms downloadUpdate.
// Single-flight: a second caller while one download runs gets the same
// promise (a second click is a no-op), so stageWithSquirrel is never entered
// twice at once.
let downloadInFlight = null

function downloadUpdate() {
  if (!downloadInFlight) {
    downloadInFlight = runDownloadUpdate().finally(() => { downloadInFlight = null })
  }
  return downloadInFlight
}

async function runDownloadUpdate() {
  try {
    const updater = genericUpdater()
    const result = await updater.checkForUpdates()
    const version = String(result?.updateInfo?.version ?? '')
    if (!isNewerVersion(version, app.getVersion())) return { error: 'no update available' }
    // §3 (macOS): Squirrel already staged a different version this run, and
    // electron-updater's squirrelDownloadedUpdate flag never resets, so staging
    // would "succeed" on that flag and install the older staged bundle. A flag
    // already set with no version recorded is a staged bundle of a version we
    // don't know — refused the same way.
    const stagedOther = stagedVersion !== null
      ? stagedVersion !== version
      : Boolean(updater.squirrelDownloadedUpdate)
    if (plat.UPDATER === 'mac' && stagedOther) {
      return { error: 'Restart to finish the update you already downloaded.' }
    }
    await updater.downloadUpdate(result?.cancellationToken)
    win?.webContents.send('update-progress', 100)
    if (plat.UPDATER === 'mac') {
      const error = await stageWithSquirrel(updater)
      if (error) return { error }
      stagedVersion = version
    }
    return { ok: true }
  } catch (err) {
    return { error: String(err?.message || err) }
  }
}

// §3 managed-install detection (Homebrew on macOS): probed fresh on every
// query — never cached — so a brew install/uninstall while the app runs
// reflects without a restart. The probe lives in the platform module;
// AUTOWRIGHT_CASKROOM replaces its list (test escape hatch).
function brewManaged() {
  return plat.managedInstall()
}

ipcMain.handle('update-check', () => fetchUpdateState())
ipcMain.handle('update-available', () => availableVersion)
ipcMain.handle('update-brew-managed', () => brewManaged())

ipcMain.handle('update-download', async () => {
  // §3: the no-feed line comes first. A managed-copy answer only makes sense
  // where a managed channel exists at all — on a platform that serves no feed
  // it would promise an update route this build simply doesn't have.
  if (!UPDATE_FEED) return { error: NO_UPDATES_ERROR }
  if (brewManaged()) return { error: plat.MANAGED_COPY_ERROR }
  return downloadUpdate()
})

// §3 win32: the installer replaces the tree the backend runs from, so the
// service is stopped first (bounded like every service child) — but only once
// the updater holds a staged installer. With nothing staged quitAndInstall
// refuses on its own, and a stop would only take the backend down for no swap.
// BaseUpdater's installerPath is the file its install() requires: null until a
// download staged one this run.
function installerStaged() {
  try {
    return genericUpdater().installerPath != null
  } catch {
    return false
  }
}

// §3: Task Scheduler reports the stop ~1.5 s before the backend's process tree
// is fully gone (the stop-verification gap). NSIS must not start replacing the
// tree inside it.
const PROCESS_TREE_GAP_MS = 1500

// §3: a failed or timed-out stop installs nothing — a half-replaced tree is
// worse than a postponed update.
const INSTALL_STOP_FAILED_ERROR = "couldn't stop the background service — try again"

// Answers whether the stop succeeded; a good stop waits out the process-tree
// gap before answering.
async function stopBackendBeforeInstall() {
  stopShellSocket()
  const err = await runServiceVerb('stop', 'update-install')
  if (err) {
    appLog(`update-install: backend stop failed: ${err}`)
    return false
  }
  await new Promise((r) => setTimeout(r, PROCESS_TREE_GAP_MS))
  return true
}

// An install that ends without a quit after that stop leaves the app up:
// installs may run again, the shell socket comes back (the failed
// quit-entirely rule), and the backend is brought back by the launch's own
// ensure-backend — a refused install never leaves the scheduler stopped until
// the next logon.
function resumeAfterRefusedInstall() {
  quittingAll = false
  startShellSocket()
  void ensureBackend()
}

// §3: single-flight on every OS — a second call while one is pending shares
// its answer (a second `service stop` child could otherwise still be alive
// when NSIS starts).
let installInFlight = null

function installUpdate() {
  if (!installInFlight) {
    installInFlight = runInstallUpdate().finally(() => { installInFlight = null })
  }
  return installInFlight
}

// ShipIt (macOS) swaps the bundle at the same path (the LaunchAgent's
// interpreter path stays valid); the old backend keeps running until the next
// launch's version-compare flow restarts it.
ipcMain.handle('update-install', () => installUpdate())

async function runInstallUpdate() {
  // §3: no feed → nothing can be staged; quitAndInstall with nothing staged
  // must never quit the app for no swap.
  // Same order as update-download: a platform with no feed says so, and only a
  // platform that has one can be managed by someone else's copy of it.
  if (!UPDATE_FEED) return { error: NO_UPDATES_ERROR }
  if (brewManaged()) return { error: plat.MANAGED_COPY_ERROR }
  if (await executionsLive()) return { busy: true }
  appLog('update: quitting to install')
  const stopsBackend = Boolean(plat.stopBackendBeforeInstall) && installerStaged()
  if (stopsBackend && !(await stopBackendBeforeInstall())) {
    resumeAfterRefusedInstall()
    return { error: INSTALL_STOP_FAILED_ERROR }
  }
  const standDown = () => { if (stopsBackend) resumeAfterRefusedInstall() }
  // §3: the same busy-gated, user-initiated install on every platform — only
  // the machinery that performs the swap differs (ShipIt vs. the NSIS
  // installer vs. the AppImage swap electron-updater staged).
  lastUpdaterError = null
  // §3: the updater quits through app.quit() itself — flag it as ours, or the
  // quit-entirely interception below would treat the install's quit as the
  // user's Quit and stop the backend under the swap. Cleared again on every
  // refusal: a later Cmd+Q must still read as the user's.
  uiQuit = true
  // §3/§13: Squirrel's quitAndInstall closes every window before it emits
  // before-quit, and the non-closable panel would veto that close walk —
  // leaving the app resident with no window, no restart, and uiQuit latched.
  // Destroy it here, not only in before-quit.
  destroyPanel()
  try {
    genericUpdater().quitAndInstall()
  } catch (err) {
    uiQuit = false
    standDown()
    appLog(`update: install failed: ${String(err?.message || err)}`)
    return { error: String(err?.message || err) }
  }
  // §3: a quitAndInstall that returns without quitting answers { error } with
  // the updater's own message — the §9.4 card renders it, and a silent no-op
  // is never an acceptable outcome. The NSIS/AppImage classes report a
  // refusal (nothing staged, the installer spawn failed) on the error stream
  // and return nothing, never false, so an error emitted during the call with
  // no quit underway is the refusal.
  if (!quitting && lastUpdaterError !== null) {
    uiQuit = false
    standDown()
    const error = lastUpdaterError
    appLog(`update: install refused: ${error}`)
    return { error }
  }
  // §3: an ok answer arms a 10 s check. MacUpdater with nothing staged
  // registers a listener and returns, so no quit may ever begin; clear uiQuit
  // then, or the next Cmd+Q would be a UI-only quit that leaves the backend
  // running.
  setTimeout(() => {
    if (quitting) return
    uiQuit = false
    standDown()
    appLog('update-install: no quit began within 10 s')
  }, 10_000).unref()
  return { ok: true }
}

// §3: the explicit service command the shell runs — `stop` for the §4.9 QUIT
// and RESET flows. One shared path: the same interpreter resolution as
// ensure-backend and the same install interlock (block new `service install`
// spawns and wait out any in-flight one, so the stop can't be undone by a
// racing install child). Resolves to null on success or the failure text; a
// caller that keeps the app up resets `quittingAll` itself, since future
// ensure/version-sync installs may run.
async function runServiceVerb(verb, label) {
  // Dev launches have no bundled Python — backend.json publishes the
  // interpreter that runs the backend (§3 discovery fields), same code path.
  const py = bundledPython() || backendInfo()?.python
  if (!py) return 'No backend interpreter found'
  quittingAll = true
  // §3: the failure text both bounds answer with. The §4.9 QUIT/RESET cards
  // render it like any other stop failure, so the app stays up and says so
  // instead of waiting on a child that never comes back.
  const timedOut = `service ${verb} timed out`
  // The in-flight install child is bounded itself, but it may be queued behind
  // an earlier one, so the wait for the chain carries its own deadline.
  let waitTimer = null
  const waitFailed = await Promise.race([
    serviceInstallDone.then(() => null),
    new Promise((resolve) => {
      waitTimer = setTimeout(() => resolve(timedOut), SERVICE_CHILD_TIMEOUT_MS)
    }),
  ])
  clearTimeout(waitTimer)
  if (waitFailed) return waitFailed
  return new Promise((resolve) => {
    execFile(py, ['-m', 'autowright.service', verb], SERVICE_CHILD_OPTIONS, (e, stdout, stderr) => {
      appLog(`${label}: ${String(stdout || stderr || '').trim()}`)
      // A child killed on its deadline carries `killed`/`signal` instead of an
      // exit status — report the plain line, never a signal name.
      if (e && (e.killed || e.signal)) { resolve(timedOut); return }
      resolve(e ? String(stdout || stderr || e.message).trim() : null)
    })
  })
}

// §3: how long a successful stop-and-quit waits for app.quit() to end the
// process before app.exit(0) ends it outright (a vetoed quit must not strand
// the UI), and how long an OS quit waits for the renderer to take the flow
// over before the shell runs it natively.
const QUIT_EXIT_FALLBACK_MS = 2000
const QUIT_ASK_RENDERER_MS = 1500

// §3 quit-entirely (the §4.9 QUIT card and every OS quit): stop the backend
// LaunchAgent (bootout plus the stray-process sweep — plist and shim stay; it
// returns at next login or app launch), then quit the app. On any stop
// failure the app stays up — never quit the UI while the backend it promised
// to stop keeps running. `force` (the §4.9 force-confirm modal's retry, the
// native confirm below) skips the live-execution gate: the backend's graceful
// shutdown and the stop's sweep end the running execution. One flight at a
// time: the IPC and the native OS path share it.
let quitFlight = null
let quitAskTimer = null
// §3: the flight covers the native busy dialog too — held for the whole of
// quitNatively (the showMessageBox await included), so a second Cmd+Q, the
// dock's Quit, a second SIGINT or a late quit-all is a no-op rather than a
// second stacked dialog or the renderer's force modal beside the native one.
let nativeQuitPending = false
// §3: true while a reset (§4.9 RESET card) is in flight — it ends the process
// itself with app.exit(0), and a second `service stop` racing its
// deleteAllData would let the app exit half-erased, so an OS quit is ignored.
let resetInFlight = false

function stopAndQuit(force, label) {
  if (quitFlight) return quitFlight
  quitFlight = (async () => {
    if (!force && await executionsLive()) return { busy: true }
    // §3 dev-only corner: no bundled interpreter and no backend.json means
    // no backend to stop — the discovery guard keeps the file present while
    // one lives. Quit the UI rather than refuse with "no interpreter": a dead
    // dev backend must never make the app unquittable.
    if (!bundledPython() && !backendInfo()) {
      appLog(`${label}: no backend to stop — quitting app`)
    } else {
      stopShellSocket()
      const err = await runServiceVerb('stop', label)
      if (err) {
        // The app stays up (§3), so future ensure/version-sync installs may run.
        quittingAll = false
        startShellSocket()
        return { error: err }
      }
      appLog(`${label}: backend stopped, quitting app`)
    }
    quitUi()
    // §3 converse invariant: the backend is stopped, so the UI must not stay
    // resident either — reopened from the dock it would sit on a dead backend
    // (ensure-backend only runs at launch). Nothing of ours vetoes a quit
    // (§9/§13: the non-closable panel is destroyed in before-quit), but if
    // anything ever does, end the process outright: there is nothing to lose.
    setTimeout(() => {
      appLog(`${label}: quit was vetoed — exiting`)
      app.exit(0)
    }, QUIT_EXIT_FALLBACK_MS)
    return { ok: true }
  })().finally(() => { quitFlight = null })
  return quitFlight
}

ipcMain.handle('quit-all', async (_e, opts) => {
  if (resetInFlight) return { error: 'a reset is in flight' }
  // §3: the native busy dialog is up — this flight is already asking. The
  // late call answers success: the quit is going ahead, and an error here
  // would only toast over the renderer's overlay.
  if (nativeQuitPending) return { ok: true }
  // The renderer took an OS quit over (§3): the native fallback stands down.
  if (quitAskTimer) { clearTimeout(quitAskTimer); quitAskTimer = null }
  return stopAndQuit(!!opts?.force, 'quit-all')
})

// §3 OS quit (Cmd+Q, the application menu's Quit, the dock's Quit — and a
// SIGINT/SIGTERM, which Electron turns into the same before-quit): quit means
// quit for good. A loaded main window runs the shared §4.9 quit flow (same
// overlay, same busy question, same quit-all IPC) — it is shown first so the
// overlay is seen. No loaded window, or a renderer that never answers, and the
// shell runs the flow natively.
function quitForGood() {
  if (resetInFlight) {
    appLog('quit: ignored — a reset is in flight')
    return
  }
  // Already underway — a repeated Cmd+Q, or one while the native busy
  // dialog is up.
  if (quitFlight || quitAskTimer || nativeQuitPending) return
  appLog('quit: requested — stopping the backend first')
  if (win && !win.isDestroyed() && winLoaded) {
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
    win.webContents.send('quit-requested')
    quitAskTimer = setTimeout(() => {
      quitAskTimer = null
      appLog('quit: the renderer did not take over — quitting natively')
      void quitNatively()
    }, QUIT_ASK_RENDERER_MS)
    return
  }
  void quitNatively()
}

async function quitNatively() {
  if (nativeQuitPending) return
  nativeQuitPending = true
  try {
    await quitNativelyHeld()
  } finally {
    nativeQuitPending = false
  }
}

// §3: the native flight stood down (a Cancel at its busy dialog, or a stop
// failure). A late quit-all answered { ok } and left the renderer's overlay at
// its "stopping" stage — this push clears it.
function sendQuitCancelled() {
  if (win && !win.isDestroyed()) win.webContents.send('quit-cancelled')
}

async function quitNativelyHeld() {
  let r = await stopAndQuit(false, 'quit')
  if (r.busy) {
    let response = 1
    try {
      ({ response } = await dialog.showMessageBox({
        type: 'warning',
        message: 'An automation is executing',
        detail: 'Shut down everything and quit? The running automation will be killed.',
        buttons: ['Shut down and quit', 'Cancel'],
        defaultId: 1,
        cancelId: 1,
      }))
    } catch { /* no dialog on this host — treat as Cancel */ }
    if (response !== 0) {
      appLog('quit: cancelled at the busy confirm')
      sendQuitCancelled()
      return
    }
    r = await stopAndQuit(true, 'quit')
  }
  if (r.error) {
    sendQuitCancelled()
    // §3: the app stays up — and says so, since no renderer is showing it.
    showErrorMessage(`Autowright couldn't stop its backend, so it stays open.\n\n${r.error}`)
  }
}

// §3 reset steps ------------------------------------------------------------

// §3 reset step 2: the executions dir is user-movable (§4.9) and may live
// outside the data root, so its location is captured from the live backend
// before anything stops it. null when unreachable or unanswered — the
// deletions below then only cover the §5 roots.
async function captureDataPath() {
  const res = await backendFetch('/settings')
  if (!res?.ok) return null
  try {
    const s = await res.json()
    return typeof s?.dataPath === 'string' && s.dataPath ? s.dataPath : null
  } catch {
    return null
  }
}

// §3 reset step 3: only the backend's keyring reaches the Keychain /
// Credential Manager, so the sweep has to run while it is still up. A failure (the §19 unreadable-store 409 included) is logged and the flow
// proceeds: value deletion is best-effort per entry (§4.8), and an unreadable
// secrets.yaml means those ids were unreachable this session anyway.
async function deleteSecrets(label) {
  const res = await backendFetch('/secrets', { method: 'DELETE' }, 30_000)
  if (res?.ok) return
  appLog(`${label}: DELETE /secrets failed `
    + `(${res ? `HTTP ${res.status}` : 'backend unreachable'}) — continuing`)
}

// §3 reset step 5: on Windows a reported service stop precedes the backend's
// file handles actually closing (the §3 stop-verification gap — executions.db
// and the backend's own log file), so a failed delete is retried briefly, up
// to ~10 s. A failure that survives the retries is logged and the flow
// continues — a leftover file must not strand the app mid-reset. The platform
// module names the OS (§2: main.cjs never sniffs process.platform).
async function deletePath(target, label) {
  const deadline = Date.now() + (plat.OS_TOKEN === 'windows' ? 10_000 : 0)
  for (;;) {
    try {
      await fs.promises.rm(target, { recursive: true, force: true })
      return
    } catch (e) {
      if (Date.now() >= deadline) {
        // §3: the deletion's own failure lines are the one exception to the
        // reset's log silence — a delete that failed left files there anyway.
        appLog(`${label}: couldn't delete ${target}: ${e?.message || e}`, { force: true })
        return
      }
      await new Promise((r) => setTimeout(r, 500))
    }
  }
}

// §3 reset step 5: the executions dir is deleted selectively, never wholesale —
// it is user-movable and (the §19 set-time guard notwithstanding) may have
// accumulated foreign files; reset must never delete a file Autowright did not
// write. Only the DB family and per-execution dirs (identified by a contained
// execution.yaml) go, then the dir itself only if that left it empty.
// Selectivity is also what keeps the live Chromium profile safe even from a
// dataPath pointed at the data root itself: the profile matches neither rule.
async function deleteExecutionData(dataPath, label) {
  const dbFamily = new Set(['executions.db', 'executions.db-wal', 'executions.db-shm'])
  let entries = []
  try {
    entries = fs.readdirSync(dataPath, { withFileTypes: true })
  } catch { return } // gone or unreadable (an unmounted volume) — nothing to do
  for (const entry of entries) {
    const p = path.join(dataPath, entry.name)
    if (dbFamily.has(entry.name)
      || (entry.isDirectory() && fs.existsSync(path.join(p, 'execution.yaml')))) {
      await deletePath(p, label)
    }
  }
  try {
    fs.rmdirSync(dataPath)
  } catch { /* foreign files remain (or in use) — the dir stays, they survive */ }
}

// §3 reset step 5: the executions dir (selectively, above), the logs root, and
// every entry of the data root **except** the live Chromium profile — Chromium
// holds open handles on it, so deleting it would fail (on Windows a sharing
// violation outright). The profile is cleared instead, which is what
// matters: every §15 localStorage marker (`ad-cli-installed` among them) goes.
// Its residual Chromium internals are accepted residue (§3). The platform's
// CLI shim directory is kept too when it lives inside the data root (Windows:
// bin\) — the shim survives a reset (§3).
async function deleteAllData(dataPath, label) {
  const root = appSupportDir()
  const profile = path.join(root, 'electron')
  const shimDir = path.dirname(plat.defaultShimPath())
  const keptShimEntry = shimDir.startsWith(root + path.sep)
    ? path.relative(root, shimDir).split(path.sep)[0]
    : null
  if (dataPath) await deleteExecutionData(dataPath, label)
  await deletePath(logsDir(), label)
  let entries = []
  try {
    entries = fs.readdirSync(root)
  } catch { /* no data root at all — nothing to sweep */ }
  for (const name of entries) {
    if (path.join(root, name) === profile) continue
    if (name === keptShimEntry) continue
    await deletePath(path.join(root, name), label)
  }
  try {
    await session.defaultSession.clearStorageData()
    await session.defaultSession.clearCache()
  } catch (e) {
    appLog(`${label}: couldn't clear the browser profile: ${e?.message || e}`, { force: true })
  }
}

// §3 reset (§4.9 RESET card): erase every §5 file and every secret, then quit
// the app; the next launch runs §10 onboarding as on a fresh install. The
// service registration, the CLI shim and the app itself deliberately survive —
// only data is erased.
ipcMain.handle('reset-all', async () => {
  // §3 step 1: the same live-execution gate as quit-all/update-install; an
  // unreachable backend counts as idle.
  // §3: an OS quit arriving from here on is ignored. A successful reset ends
  // in app.exit(0); every other way out — the busy/error answers and any
  // throw — clears the flag, so a reset that failed never leaves the app
  // unquittable.
  resetInFlight = true
  if (await executionsLive()) {
    resetInFlight = false
    return { busy: true }
  }
  try {
    const dataPath = await captureDataPath()
    // §3: each destructive step announces itself as it starts — fire-and-forget
    // stage tokens for the §4.9 reset progress overlay.
    const stage = (s) => win?.webContents.send('reset-progress', s)
    stage('secrets')
    await deleteSecrets('reset')
    stage('service')
    stopShellSocket()
    const err = await runServiceVerb('stop', 'reset')
    if (err) {
      // §3 step 4: a stop failure aborts the reset — the app stays up and
      // nothing has been deleted beyond step 3's secrets.
      quittingAll = false
      startShellSocket()
      return { error: err }
    }
    stage('data')
    // Announced *before* the deletion: appLog re-creates the logs root, so a
    // line written after it left a fresh logs dir behind on every reset.
    // Nothing may log past this point — deleteAllData's own failure lines are
    // the one exception, since a delete that failed left files there anyway.
    appLog('reset: erasing data, then quitting')
    // Past this line the writer is a no-op for everyone else, and the 60 s poll
    // that would otherwise re-create the logs root behind the deletion stops.
    resetting = true
    stopShellPoll()
    stopUpdateTimer()
    await deleteAllData(dataPath, 'reset')
    // §3 step 6: the app quits and stays quit. The next launch finds no
    // backend.json and an empty data root: ensure-backend re-registers and §10
    // onboarding runs as on a fresh install.
    stage('quit')
    app.exit(0)
    return { ok: true }
  } finally {
    resetInFlight = false
  }
})

app.whenReady().then(async () => {
  if (!gotLock && lockRetry) await lockRetry
  if (!gotLock) return
  // The reopen handler is registered before anything else at all — the first
  // createWindow included: whatever else fails at ready, the dock/tray must
  // always be able to bring the window back. The hidden tray panel is also a
  // BrowserWindow, so count only the main window — `getAllWindows().length`
  // would block reopening from the Dock.
  // §9: a not-yet-loaded window stays hidden even on an explicit reopen — it
  // shows itself on the first successful load.
  // §13: a minimized window is restored before it is shown, the same rule the
  // deep-link and second-instance paths follow.
  app.on('activate', () => {
    if (quitting) return
    if (win === null) createWindow()
    else if (winLoaded) { if (win.isMinimized()) win.restore(); win.show(); win.focus() }
  })
  // Every OS-side step below is guarded on its own, the same way the tray is
  // further down: one that throws is logged and the chain carries on — none of
  // them is worth losing the window, the §6 app-start triggers or the §4.9
  // settings reconcile over.
  // Dev launches via `electron .`, which ships the default Electron dock icon —
  // replace it with the AW mark (§14 checked-in icon assets; a no-op on
  // platforms without a dock).
  try {
    if (caps.dockIcon) plat.setDockIcon(app, path.join(__dirname, 'icon', 'icon.png'))
  } catch (err) {
    appLog(`ready: setting the dock icon failed: ${String(err?.message || err)}`)
  }
  // §3 Linux desktop integration: a packaged launch reconciles the launcher
  // entry + hicolor icon under ~/.local/share that give the AppImage's window
  // its icon and an app-grid entry (§2 applyDesktopEntry; a no-op unpackaged).
  try {
    if (caps.desktopEntry) plat.applyDesktopEntry(app, path.join(__dirname, 'icon', 'icon.svg'))
  } catch (err) {
    appLog(`ready: applying the desktop entry failed: ${String(err?.message || err)}`)
  }
  // §9: a platform without an application menu (Linux — the native frame
  // would draw Electron's stock File/Edit/View/Window bar) has it suppressed
  // before any window exists; editing shortcuts are Chromium-native.
  try {
    if (!caps.appMenu) Menu.setApplicationMenu(null)
  } catch (err) {
    appLog(`ready: suppressing the application menu failed: ${String(err?.message || err)}`)
  }
  // §3: logout / restart / shutdown end the LaunchAgent with the session and
  // RunAtLoad brings it back at login — quitting the UI only, never a stop
  // that would delay the logout. Guarded like every other ready step.
  try {
    powerMonitor?.on?.('shutdown', () => { sessionEnding = true })
  } catch (err) {
    appLog(`ready: watching for session end failed: ${String(err?.message || err)}`)
  }
  void ensureBackend()
  createWindow()
  // §13: a tray that fails to create (a broken package missing its icon asset)
  // is logged and skipped. It must never take the rest of the ready chain with
  // it — the §6 app-start triggers, the tray-alert poll and the §4.9 settings
  // reconcile all run either way.
  if (caps.trayPanel) {
    try {
      createTray()
    } catch (err) {
      appLog(`tray: creating the tray icon failed: ${String(err?.message || err)}`)
    }
  }
  void notifyAppStarted()
  void refreshTrayAlert()
  void syncShellSettings()
  shellPoll = setInterval(() => { void refreshTrayAlert(); void syncShellSettings() }, 60_000)
  // §13: the shell's notification socket (a no-op without the OS API).
  startShellSocket()
  // Last resort: nothing in the chain above may die silently.
}).catch((err) => appLog(`ready: startup failed: ${String(err?.message || err)}`))

// §9 close rule, per-OS. §3 holds either way: quitting the app never stops the
// backend — we are always a client (the one exception is the explicit quit-all
// IPC above, which stops the backend first and then quits).
//   • With a dock (macOS): never quit. The app is a tray-and-dock app and stays
//     resident; `activate` reopens the window from the dock.
//   • Without one (Windows): the tray icon is the only way back, so the app
//     stays resident exactly while one is showing and otherwise quits the UI.
//     The check reads the live `tray` reference — never the stored §4.9
//     `menuBarIcon` setting — so a tray that failed to appear can't strand an
//     invisible app with no way to reach it.
app.on('window-all-closed', () => {
  if (caps.dockIcon) return
  if (!tray) quitUi()
})

// The app is on its way out: the §3 update-install answer reads this to tell a
// real quit from an updater that refused to start one, and the 60 s poll and
// the 24 h update-check timer stop here rather than firing a backend fetch
// (and an app.log line) mid-quit.
app.on('before-quit', (e) => {
  // §3 quit means quit for good: a quit that is neither ours (quitUi) nor the
  // session ending is the user's Quit — hold it and run the quit-entirely
  // flow, which quits through quitUi() once the backend is stopped.
  if (!uiQuit && !sessionEnding) {
    e.preventDefault()
    quitForGood()
    return
  }
  quitting = true
  stopShellPoll()
  stopShellSocket()
  stopUpdateTimer()
  // §13: app.quit() closes every window next and is cancelled when one
  // refuses — and the panel is deliberately non-closable (Cmd+W must be a
  // no-op for it). Left alone, a panel that had ever been opened silently
  // vetoed every quit: Cmd+Q, the dock's Quit and quit-all all stopped right
  // here, with the polls already gone and the app still resident. Destroy it
  // (the tray-off transition's move) before Electron walks the windows.
  destroyPanel()
})
