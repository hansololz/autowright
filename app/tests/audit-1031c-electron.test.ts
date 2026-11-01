// audit 2026-10-31 third sweep, Electron main layer: the reveal-path reply
// says whether the reveal happened (spec/ui-shell.md §9), and the CLI shim
// writes land atomically (§3). main.cjs exports nothing, so, as in
// audit-1031b-electron.test.ts, the handlers are reached by evaluating the
// real source against a stub `electron` module: real code, real handlers,
// only the Electron surface underneath is a double.
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

const ELECTRON_DIR = join(__dirname, '..', 'electron')
const src = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
const realRequire = createRequire(join(ELECTRON_DIR, 'main.cjs'))
const platMod = realRequire(join(ELECTRON_DIR, 'platform', 'index.cjs')) as {
  SHIM_MARKER: string
  shimText: (python: string) => string
}

const savedHome = process.env.AUTOWRIGHT_HOME
const savedShim = process.env.AUTOWRIGHT_SHIM

interface ShellRecord {
  revealed: string[]
  opened: string[]
  openFailure: string
}

interface MainStub {
  invoke: (channel: string, ...args: unknown[]) => unknown
  shell: ShellRecord
  home: string
}

function loadMain(): MainStub {
  const handlers = new Map<string, (e: unknown, ...args: unknown[]) => unknown>()
  const home = mkdtempSync(join(tmpdir(), 'aw-main-'))
  process.env.AUTOWRIGHT_HOME = home
  const shell: ShellRecord = { revealed: [], opened: [], openFailure: '' }

  class FakeUpdater {
    autoDownload = true
    autoInstallOnAppQuit = true
    logger: unknown = null
    squirrelDownloadedUpdate = false
    installerPath = null
    on() {}
    async checkForUpdates() { return null }
    async downloadUpdate() { return [] }
    quitAndInstall() {}
  }

  class FakeWindow {
    webContents = {
      on() {}, once() {}, send() {}, setWindowOpenHandler() {},
      isLoading: () => false, getURL: () => '',
    }

    loadFile() {} loadURL() {}
    on() {} once() {}
    show() {} focus() {} hide() {} restore() {}
    isMinimized() { return false }
    setSize() {} setPosition() {}
    isVisible() { return false }
    setVisibleOnAllWorkspaces() {}
    destroy() {}
    isDestroyed() { return false }
  }

  const electron = {
    app: {
      setPath() {},
      getPath: () => home,
      getVersion: () => '0.0.0',
      commandLine: { appendSwitch() {} },
      requestSingleInstanceLock: () => true,
      on() {},
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
    autoUpdater: { on() {}, removeListener() {}, setFeedURL() {}, checkForUpdates() {}, quitAndInstall() {} },
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
      // The real openPath answers an error string, empty on success.
      openPath: async (p: string) => { shell.opened.push(p); return shell.openFailure },
      showItemInFolder: (p: string) => { shell.revealed.push(p) },
      openExternal: () => Promise.resolve(),
    },
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
    shell, home,
  }
}

function writeBackendJson(m: MainStub, python: string) {
  writeFileSync(join(m.home, 'backend.json'), JSON.stringify({ port: 65000, token: 't', python }))
}

afterEach(() => {
  if (savedHome === undefined) delete process.env.AUTOWRIGHT_HOME
  else process.env.AUTOWRIGHT_HOME = savedHome
  if (savedShim === undefined) delete process.env.AUTOWRIGHT_SHIM
  else process.env.AUTOWRIGHT_SHIM = savedShim
})

describe('AF reveal-path answers whether the reveal happened (§9)', () => {
  it('a path inside the roots reveals and answers ok: true', async () => {
    const m = loadMain()
    const target = join(m.home, 'automations', 'demo.txt')
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, 'x')
    expect(await m.invoke('reveal-path', target)).toEqual({ ok: true })
    expect(m.shell.revealed).toEqual([realpathSync(target)])
  })

  it('a missing path answers ok: false with the reason and reveals nothing', async () => {
    const m = loadMain()
    const answer = await m.invoke('reveal-path', join(m.home, 'not-there')) as { ok: boolean; error?: string }
    expect(answer).toEqual({ ok: false, error: 'the path no longer exists' })
    expect(m.shell.revealed).toEqual([])
    expect(m.shell.opened).toEqual([])
  })

  it('a path outside the roots answers ok: false', async () => {
    const m = loadMain()
    const outside = mkdtempSync(join(tmpdir(), 'aw-outside-'))
    const answer = await m.invoke('reveal-path', outside) as { ok: boolean; error?: string }
    expect(answer.ok).toBe(false)
    expect(answer.error).toContain('outside')
    expect(m.shell.revealed).toEqual([])
    expect(m.shell.opened).toEqual([])
  })

  it('a shell refusal answers ok: false with the shell\'s words', async () => {
    const m = loadMain()
    // An extension-less plain directory opens in place on every platform.
    const folder = join(m.home, 'memory', 'demo')
    mkdirSync(folder, { recursive: true })
    m.shell.openFailure = 'Failed to open path'
    const answer = await m.invoke('reveal-path', folder)
    if (m.shell.opened.length) expect(answer).toEqual({ ok: false, error: 'Failed to open path' })
    else expect(answer).toEqual({ ok: true }) // this platform reveals folders instead
  })
})

describe('AI the CLI shim writes land atomically (§3)', () => {
  const tempLeft = (dir: string) => readdirSync(dir).filter((n) => n.endsWith('.tmp'))

  it('cli-install writes the marker-carrying shim and leaves no temp file', async () => {
    const m = loadMain()
    writeBackendJson(m, '/usr/bin/python3')
    const binDir = join(m.home, 'bin')
    const shim = join(binDir, 'autowright')
    process.env.AUTOWRIGHT_SHIM = shim
    expect(await m.invoke('cli-install')).toEqual({ ok: true })
    const text = readFileSync(shim, 'utf-8')
    expect(text).toContain(platMod.SHIM_MARKER)
    expect(text).toBe(platMod.shimText('/usr/bin/python3'))
    expect(tempLeft(binDir)).toEqual([])
  })

  it('cli-status heals a stale shim in place through the same atomic write', async () => {
    const m = loadMain()
    const binDir = join(m.home, 'bin')
    mkdirSync(binDir, { recursive: true })
    const shim = join(binDir, 'autowright')
    process.env.AUTOWRIGHT_SHIM = shim
    writeFileSync(shim, platMod.shimText('/old/python'), { mode: 0o755 })
    writeBackendJson(m, '/new/python')
    const status = await m.invoke('cli-status') as { state: string }
    expect(status.state).toBe('installed')
    expect(readFileSync(shim, 'utf-8')).toBe(platMod.shimText('/new/python'))
    expect(existsSync(shim)).toBe(true)
    expect(tempLeft(binDir)).toEqual([])
  })
})
