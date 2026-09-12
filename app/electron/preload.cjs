const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('autowright', {
  backendInfo: () => ipcRenderer.invoke('backend-info'),
  // §3 ensure-backend outcome for the §9 boot splash
  backendStatus: () => ipcRenderer.invoke('backend-status'),
  // §3 CLI on PATH: shim state + explicit privileged install (§10 step 3,
  // §4.9 COMMAND LINE card)
  cliStatus: () => ipcRenderer.invoke('cli-status'),
  cliInstall: () => ipcRenderer.invoke('cli-install'),
  cliUninstall: () => ipcRenderer.invoke('cli-uninstall'),
  openApp: (hash) => ipcRenderer.invoke('open-app', hash),
  pickFolder: (defaultPath) => ipcRenderer.invoke('pick-folder', defaultPath),
  resizePanel: (h) => ipcRenderer.invoke('resize-panel', h),
  // §5.1 transfer archives: native dialogs + file IO for export/import
  saveFile: (defaultName, data) => ipcRenderer.invoke('save-file', defaultName, data),
  openArchive: () => ipcRenderer.invoke('open-archive'),
  // §22.3 add marketplace: pick a catalog file - only its path travels, the
  // backend reads the file itself.
  openCatalog: () => ipcRenderer.invoke('open-catalog'),
  // §22.7 catalog editor: pick an .autowright file - path only, the backend
  // copies it.
  openArchivePath: () => ipcRenderer.invoke('open-archive-path'),
  revealPath: (p) => ipcRenderer.invoke('reveal-path', p),
  // §9.5 report modal: OS details for the info block
  platformInfo: () => ipcRenderer.invoke('platform-info'),
  // §4.9 shell-owned settings effects (login item + tray icon)
  applySettings: (s) => ipcRenderer.invoke('apply-settings', s),
  // §9.3 developer log overlay
  tailLogs: () => ipcRenderer.invoke('tail-logs'),
  listRequestLogs: () => ipcRenderer.invoke('list-request-logs'),
  readRequestLog: (name) => ipcRenderer.invoke('read-request-log', name),
  trayAlert: (on) => ipcRenderer.invoke('tray-alert', on),
  // §9.4 in-app updates (§3): manual check → download → restart-install
  updateCheck: () => ipcRenderer.invoke('update-check'),
  updateDownload: () => ipcRenderer.invoke('update-download'),
  updateInstall: () => ipcRenderer.invoke('update-install'),
  // §3 Homebrew-managed detection: true while the Caskroom dir exists — the
  // §9.4 row swaps Download for the brew upgrade notice.
  updateBrewManaged: () => ipcRenderer.invoke('update-brew-managed'),
  // §4.9 QUIT card: stop the backend service (plus stray-process sweep), then
  // quit the app (§3 explicit-quit exception). `force` skips the
  // live-execution gate — the §4.9 force-confirm modal's retry.
  quitAll: (force) => ipcRenderer.invoke('quit-all', { force: !!force }),
  // §4.9 RESET card: erase every §5 file and every secret, then quit the app
  // (§3 reset flow; the next launch runs onboarding as a fresh install)
  resetAll: () => ipcRenderer.invoke('reset-all'),
  // §3 reset-progress stage tokens for the §4.9 reset progress overlay.
  // Re-registering replaces the previous listener, like onUpdateProgress.
  // Each on* below returns an unsubscribe so an unmounting page can drop its
  // listener instead of leaving a dead setter closure registered.
  onResetProgress: (cb) => _push('reset-progress', cb),
  // Download percent (null = size unknown). Re-registering replaces the
  // previous listener — the About page re-subscribes on every mount.
  onUpdateProgress: (cb) => _push('update-progress', cb),
  // §3 update-available: known newer version (or null) + push on later finds.
  updateAvailable: () => ipcRenderer.invoke('update-available'),
  onUpdateAvailable: (cb) => _push('update-available', cb),
  // Deep-link target ('/app?automation=<id>') pushed by main when the window already
  // exists — a reload would drop the WS and all renderer state. Re-registering
  // replaces the previous listener, like the two above: under §15's renderer
  // dev server a re-evaluated module would otherwise stack subscribers.
  onOpenTarget: (cb) => _push('open-target', cb),
})

// One push-channel subscription at a time (replace on re-register), returning
// an unsubscribe for effect cleanup.
function _push(channel, cb) {
  ipcRenderer.removeAllListeners(channel)
  const listener = (_e, value) => cb(value)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}
