// §2 platform layer (shell half): the per-OS values main.cjs consumes but
// can't be asked for on this machine — window chrome (§9), panel placement
// (§13), tray assets (§13) and the ensure-backend failure copy (§9). The
// modules never import `electron`, so every one of them loads on any OS and
// each platform's shape is pinned here regardless of where the suite runs.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const require = createRequire(__filename)
const ELECTRON_DIR = join(__dirname, '..', 'electron')
const darwin = require('../electron/platform/darwin.cjs')
const win32 = require('../electron/platform/win32.cjs')
const linux = require('../electron/platform/linux.cjs')

// A 1920×1080 display with a 48 px taskbar docked at the bottom.
const DISPLAY = {
  bounds: { x: 0, y: 0, width: 1920, height: 1080 },
  workArea: { x: 0, y: 0, width: 1920, height: 1032 },
}

describe('§9 window chrome is per-OS', () => {
  it('Windows: hidden title bar + a native titleBarOverlay, no mac-only options', () => {
    const chrome = win32.mainWindowChrome()
    expect(chrome).toEqual({
      titleBarStyle: 'hidden',
      // color = --bg-titlebar (the renderer's full-width title bar behind the
      // overlay), symbolColor = the §14 --text-2 hex, height = the title bar.
      titleBarOverlay: { color: '#141820', symbolColor: '#c8ccd4', height: 40 },
    })
    // trafficLightPosition is a macOS-only option and must never leak in.
    expect(chrome).not.toHaveProperty('trafficLightPosition')
  })

  it('macOS keeps its pinned traffic lights and no overlay', () => {
    const chrome = darwin.mainWindowChrome()
    expect(chrome).toEqual({
      titleBarStyle: 'hidden',
      trafficLightPosition: { x: 14, y: 14 },
    })
  })

  it('Linux uses the native frame — no custom chrome at all', () => {
    expect(linux.mainWindowChrome()).toEqual({})
    expect(linux.panelWindowExtras()).toEqual({})
  })
})

describe('§13 panel placement is per-OS', () => {
  it('Windows hugs the work area bottom at the panel\'s real height', () => {
    // 420 px panel: bottom edge 6 px above the taskbar, never at the 640 cap.
    expect(win32.panelPosition({ x: 900, y: 1050 }, DISPLAY, 420))
      .toEqual({ x: 733, y: 1032 - 420 - 6 })
    // It re-anchors as the panel grows — the bottom edge stays put.
    expect(win32.panelPosition({ x: 900, y: 1050 }, DISPLAY, 560))
      .toEqual({ x: 733, y: 1032 - 560 - 6 })
    // No measurement yet: the 640 px window cap stands in.
    expect(win32.panelPosition({ x: 900, y: 1050 }, DISPLAY))
      .toEqual(win32.panelPosition({ x: 900, y: 1050 }, DISPLAY, 640))
    // A panel taller than the work area is clamped on screen, not pushed off.
    expect(win32.panelPosition({ x: 900, y: 1050 }, DISPLAY, 5000).y)
      .toBe(DISPLAY.workArea.y + 6)
  })

  it('macOS anchors under the menu bar, height-independent', () => {
    const top = { x: 733, y: 6 }
    expect(darwin.panelPosition({ x: 900, y: 10 }, DISPLAY)).toEqual(top)
    expect(darwin.panelPosition({ x: 900, y: 10 }, DISPLAY, 420)).toEqual(top)
    expect(darwin.panelPosition({ x: 900, y: 10 }, DISPLAY, 640)).toEqual(top)
  })

  it('Linux anchors to the click point on any edge', () => {
    // Top-panel click (top half of the work area): the macOS shape.
    expect(linux.panelPosition({ x: 900, y: 10 }, DISPLAY, 420))
      .toEqual({ x: 733, y: 6 })
    // Bottom-taskbar click: the Windows height-aware shape, re-anchoring as
    // the panel grows.
    expect(linux.panelPosition({ x: 900, y: 1050 }, DISPLAY, 420))
      .toEqual({ x: 733, y: 1032 - 420 - 6 })
    expect(linux.panelPosition({ x: 900, y: 1050 }, DISPLAY, 560))
      .toEqual({ x: 733, y: 1032 - 560 - 6 })
    // No measurement yet: the 640 px window cap stands in.
    expect(linux.panelPosition({ x: 900, y: 1050 }, DISPLAY))
      .toEqual(linux.panelPosition({ x: 900, y: 1050 }, DISPLAY, 640))
    // Clamped inside the work area horizontally on a left/right-edge panel.
    expect(linux.panelPosition({ x: 2, y: 500 }, DISPLAY, 420).x).toBe(6)
    expect(linux.panelPosition({ x: 1918, y: 500 }, DISPLAY, 420).x)
      .toBe(1920 - 344 - 6)
    // A panel taller than the work area is clamped on screen, not pushed off.
    expect(linux.panelPosition({ x: 900, y: 1050 }, DISPLAY, 5000).y).toBe(6)
  })
})

describe('§13 tray assets are per-OS', () => {
  it('Windows uses the checked-in colored PNGs, never the mac template images', () => {
    expect(win32.trayIconSpec(false)).toEqual({ file: 'trayWin.png', template: false })
    expect(win32.trayIconSpec(true)).toEqual({ file: 'trayWinAlert.png', template: false })
    for (const name of ['trayWin', 'trayWinAlert']) {
      expect(existsSync(join(ELECTRON_DIR, `${name}.png`))).toBe(true)
      expect(existsSync(join(ELECTRON_DIR, `${name}@2x.png`))).toBe(true)
    }
  })

  it('macOS keeps the template image for the normal state', () => {
    expect(darwin.trayIconSpec(false)).toEqual({ file: 'trayTemplate.png', template: true })
    expect(darwin.trayIconSpec(true)).toEqual({ file: 'trayAlert.png', template: false })
  })

  it('Linux uses its own checked-in colored PNGs (StatusNotifier hosts do not recolor)', () => {
    expect(linux.trayIconSpec(false)).toEqual({ file: 'trayLinux.png', template: false })
    expect(linux.trayIconSpec(true)).toEqual({ file: 'trayLinuxAlert.png', template: false })
    for (const name of ['trayLinux', 'trayLinuxAlert']) {
      expect(existsSync(join(ELECTRON_DIR, `${name}.png`))).toBe(true)
      expect(existsSync(join(ELECTRON_DIR, `${name}@2x.png`))).toBe(true)
    }
  })
})

describe('§9 ensure-backend failure copy is per-OS', () => {
  it('macOS names Gatekeeper; Windows says plainly that the service failed to start', () => {
    expect(darwin.SERVICE_START_FAILED_DETAIL)
      .toBe('The backend service was registered but never started — macOS '
        + 'Gatekeeper may be blocking an unsigned build. Details in app.log.')
    expect(win32.SERVICE_START_FAILED_DETAIL)
      .toBe('The backend service failed to start. Details in app.log.')
    expect(win32.SERVICE_START_FAILED_DETAIL).not.toContain('Gatekeeper')
    expect(linux.SERVICE_START_FAILED_DETAIL)
      .toBe('The backend service failed to start. Details in app.log.')
  })

  it('every platform module answers the whole shell surface', () => {
    // A missing export is a silent `undefined` in main.cjs, so the modules are
    // pinned to one another's shape.
    const fallback = require('../electron/platform/fallback.cjs')
    const keys = Object.keys(darwin).sort()
    // win32 carries one extra export on top of the shared surface: the §4.9
    // legacy Run-value sweep, a Windows-only helper main.cjs never calls
    // (applyLoginItem drives it) that the §4.9 tests exercise directly.
    expect(Object.keys(win32).sort()).toEqual([...keys, 'sweepLegacyLoginItems'].sort())
    expect(Object.keys(linux).sort()).toEqual(keys)
    expect(Object.keys(fallback).sort()).toEqual(keys)
  })
})

describe('§9 capability flags', () => {
  it('macOS declares every shell surface; Windows has everything but a dock', () => {
    expect(darwin.capabilities).toEqual({
      trayPanel: true, loginItem: true, dockIcon: true, updates: true, appMenu: true, desktopEntry: false,
    })
    expect(win32.capabilities).toEqual({
      trayPanel: true, loginItem: true, dockIcon: false, updates: true, appMenu: true, desktopEntry: false,
    })
    // §13 (2026-09-01): Linux ships no tray surface at all — StatusNotifier
    // hosts may not render the icon, activate via a menu the app doesn't
    // have, and don't deliver a reliable click; with no dock either, the §9
    // close rule quits the UI on last window close. §9: no application menu —
    // the stock File/Edit/View/Window bar is suppressed. §3: the AppImage
    // launcher entry is the one desktop-integration surface, Linux-only.
    expect(linux.capabilities).toEqual({
      trayPanel: false, loginItem: true, dockIcon: false, updates: true, appMenu: false, desktopEntry: true,
    })
  })

  it('every module\'s updates flag and updateFeedUrl agree', () => {
    // The two must not drift apart: a feed URL nobody is allowed to fetch, or
    // a declared capability with no feed behind it, are both dead ends.
    const fallback = require('../electron/platform/fallback.cjs')
    for (const mod of [darwin, win32, linux, fallback]) {
      expect(mod.capabilities.updates).toBe(mod.updateFeedUrl('x64') !== null)
    }
  })
})

describe('§3 update machinery is per-OS', () => {
  it('Windows serves the generic-provider feed directory and names NsisUpdater', () => {
    // §3: latest.yml + installer + blockmap live under this directory — the
    // generic provider takes the base URL, never a single file. x86_64 is the
    // only Windows arch that ships, so the answer carries no arch switch.
    const url = 'https://raw.githubusercontent.com/hansololz/autowright/main/release/win32-x86_64/'
    expect(win32.updateFeedUrl('x64')).toBe(url)
    expect(win32.updateFeedUrl('arm64')).toBe(url)
    expect(url.endsWith('/')).toBe(true)
    expect(win32.UPDATER).toBe('nsis')
  })

  it('macOS serves per-arch generic-provider feed directories and names MacUpdater', () => {
    // §3: latest-mac.yml lives under these directories — the generic provider
    // takes the base URL and appends the darwin channel file name itself.
    // Per-arch (unlike the single win32/linux base): the two mac arches ship
    // separate zips, and a feed that lists only its own arch keeps
    // MacUpdater's arm64 file filtering trivially satisfied. Never the legacy
    // feed.json beside them — that file is the frozen §3 0.6.0 bridge.
    expect(darwin.updateFeedUrl('x64')).toBe('https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-x86_64/')
    expect(darwin.updateFeedUrl('arm64')).toBe('https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-arm64/')
    expect(darwin.updateFeedUrl('arm64')!.endsWith('/')).toBe(true)
    expect(darwin.UPDATER).toBe('mac')
  })

  it('Linux serves the generic-provider feed directory and names AppImageUpdater', () => {
    // §3: latest-linux.yml lives under this directory (the block map is
    // embedded in the AppImage itself, not a separate feed file) — the generic
    // provider takes the base URL, never a single file. x86_64 is the only
    // Linux arch that ships, so the answer carries no arch switch.
    const url = 'https://raw.githubusercontent.com/hansololz/autowright/main/release/linux-x86_64/'
    expect(linux.updateFeedUrl('x64')).toBe(url)
    expect(linux.updateFeedUrl('arm64')).toBe(url)
    expect(url.endsWith('/')).toBe(true)
    expect(linux.UPDATER).toBe('appimage')
  })

  it('a platform with no feed names no machinery either', () => {
    const fallback = require('../electron/platform/fallback.cjs')
    expect(fallback.UPDATER).toBeNull()
    expect(fallback.updateFeedUrl('x64')).toBeNull()
  })

  it('the §3 publisherName footgun is set nowhere', () => {
    // Setting it before a certificate exists makes every update fail against
    // the unsigned artifacts electron-updater would then try to verify.
    // The name may be *mentioned* (the rule is written down where it bites);
    // what must not exist is a key or assignment that sets it.
    const pkg = readFileSync(join(__dirname, '..', 'package.json'), 'utf-8')
    const mainSrc = readFileSync(join(ELECTRON_DIR, 'main.cjs'), 'utf-8')
    for (const src of [pkg, mainSrc, readFileSync(join(ELECTRON_DIR, 'platform', 'win32.cjs'), 'utf-8')]) {
      expect(src).not.toMatch(/["']?publisherName["']?\s*[:=]/)
    }
  })
})

describe('§3 Windows packaging config (electron-builder)', () => {
  const pkg = require('../package.json') as { desktopName: string, build: unknown }
  const build = pkg.build as {
    appId: string
    productName: string
    artifactName: string
    directories: { output: string }
    publish: { provider: string, url: string }[]
    files: string[]
    extraResources: { from: string, to: string }[]
    win: { target: { target: string, arch: string[] }[], icon: string }
    linux: {
      target: { target: string, arch: string[] }[], artifactName: string,
      icon: string, publish: { provider: string, url: string }[], syncDesktopName: boolean,
    }
    nsis: Record<string, unknown>
  }

  it('aligns the Linux app-id with the launcher entry the app installs (§3)', () => {
    // Electron hands Wayland `desktopName` minus `.desktop` as the app-id (and
    // X11 the same as WM_CLASS); the §3 reconcile installs an entry of that
    // basename, and syncDesktopName makes the entry *inside* the AppImage
    // match too — so every desktop associates the window by name alone.
    expect(pkg.desktopName).toBe('ai.autowright.app.desktop')
    expect(build.linux.syncDesktopName).toBe(true)
    expect(build.appId).toBe('ai.autowright.app')
  })

  it('targets Windows NSIS and Linux AppImage — never mac', () => {
    // prod.sh keeps @electron/packager; nothing here may declare a mac
    // target that a stray `electron-builder` run could act on.
    expect(build.win.target).toEqual([{ target: 'nsis', arch: ['x64'] }])
    expect(build.linux.target).toEqual([{ target: 'AppImage', arch: ['x64'] }])
    expect(Object.keys(build)).not.toContain('mac')
  })

  it('pins the §3 Linux artifact name, icon, and the generic feed', () => {
    expect(build.linux.artifactName).toBe('Autowright-${version}-linux-x86_64.${ext}')
    // §3: linux.publish overrides the top-level (win32) entry, so the
    // app-update.yml electron-builder embeds in the AppImage points at the
    // Linux feed — the same base the §2 linux module serves — never the
    // Windows one.
    expect(build.linux.publish).toEqual([
      { provider: 'generic', url: linux.updateFeedUrl('x64') },
    ])
    expect(build.linux.icon).toBe('electron/icon/icon.png')
    expect(existsSync(join(ELECTRON_DIR, 'icon', 'icon.png'))).toBe(true)
    // §3 bundled Python: the shared extraResources staging lands at
    // resources/python, where the §2 linux module's bundledPythonPath looks.
    expect(linux.bundledPythonPath('/opt/app/resources'))
      .toBe('/opt/app/resources/python/bin/python3')
  })

  it('carries the §3 identifiers, artifact name and generic feed', () => {
    expect(build.appId).toBe('ai.autowright.app')
    expect(build.productName).toBe('Autowright')
    expect(build.artifactName).toBe('Autowright-${version}-win32-x86_64.${ext}')
    // The publish entry is what puts app-update.yml (this URL) in the package,
    // so it must be the same base the §2 win32 module serves.
    expect(build.publish).toEqual([
      { provider: 'generic', url: win32.updateFeedUrl('x64') },
    ])
  })

  it('pins the per-user NSIS shape and the stable GUID', () => {
    // §3: no admin prompt, install dir under %LOCALAPPDATA%\Programs (the
    // per-user one-click default), and a GUID that must NEVER change — an
    // upgrade finds the previous install by it, unsigned→signed included.
    expect(build.nsis.perMachine).toBe(false)
    expect(build.nsis.oneClick).toBe(true)
    expect(build.nsis.guid).toBe('3E71053D-7CAA-4BF9-A643-93ABDA35B1F3')
    // §3 Windows notifier: the AUMID only exists while a Start-menu shortcut
    // carries it, so this flag is load-bearing, not cosmetic.
    expect(build.nsis.createStartMenuShortcut).toBe(true)
    expect(build.nsis.shortcutName).toBe('Autowright')
  })

  it('reopens an already-installed same-version app instead of reinstalling (§3)', () => {
    // NSIS never runs under vitest, so this pins the hook's shape: the include
    // is wired in, it is a preInit (before the running-app check), it steps
    // aside for silent and updater runs, and it launches the recorded install
    // only when the recorded version is this installer's own.
    expect(build.nsis.include).toBe('electron/installer.nsh')
    const nsh = readFileSync(join(ELECTRON_DIR, 'installer.nsh'), 'utf-8')
    expect(nsh).toMatch(/^!macro preInit$/m)
    expect(nsh).toMatch(/\$\{ifNot\} \$\{Silent\}/)
    expect(nsh).toMatch(/\$\{AndIfNot\} \$\{isUpdated\}/)
    expect(nsh).toMatch(/ReadRegStr \$R0 HKCU "\$\{UNINSTALL_REGISTRY_KEY\}" "DisplayVersion"/)
    expect(nsh).toMatch(/ReadRegStr \$R1 HKCU "\$\{INSTALL_REGISTRY_KEY\}" "InstallLocation"/)
    expect(nsh).toMatch(/\$\{if\} \$R0 == "\$\{VERSION\}"/)
    expect(nsh).toMatch(/\$\{andIf\} \$\{FileExists\} "\$R1\\\$\{APP_EXECUTABLE_FILENAME\}"/)
    expect(nsh).toMatch(/\$\{StdUtils\.ExecShellAsUser\} \$0 "\$R1\\\$\{APP_EXECUTABLE_FILENAME\}" "open" ""\n(\s+;[^\n]*\n)*\s+SetErrorLevel 0\n\s+Quit\n/)
    // A per-user install (§3) — never a machine hive.
    expect(nsh).not.toMatch(/HKLM|SHELL_CONTEXT/)
  })

  it('ships the renderer, the shell and the staged interpreter — and no more', () => {
    expect(build.files).toContain('dist/**/*')
    expect(build.files).toContain('electron/**/*')
    // The renderer's own dependencies are bundled into dist/ by vite, so the
    // heavy ones are excluded from the package (electron-builder still ships
    // electron-updater's runtime closure, which main.cjs requires at runtime).
    for (const dep of ['@fontsource', '@fortawesome', 'react', 'react-dom',
      'react-markdown', 'remark-gfm', 'zustand']) {
      expect(build.files).toContain(`!node_modules/${dep}/**`)
    }
    expect(build.files).not.toContain('!node_modules/electron-updater/**')
    // §3 bundled Python: the staged interpreter lands at resources\python,
    // where the §2 win32 module's bundledPythonPath looks for it.
    expect(build.extraResources).toEqual([{ from: '../build/python', to: 'python' }])
    expect(win32.bundledPythonPath('X:/app/resources').replace(/\\/g, '/'))
      .toBe('X:/app/resources/python/python.exe')
    expect(build.win.icon).toBe('electron/icon/icon.ico')
    expect(existsSync(join(ELECTRON_DIR, 'icon', 'icon.ico'))).toBe(true)
  })
})

describe('§13/§17 the per-OS tray assets and their generator agree', () => {
  it('scripts/gen_tray_icon.py renders the checked-in Windows and Linux PNGs', () => {
    const gen = readFileSync(join(__dirname, '..', '..', 'scripts', 'gen_tray_icon.py'), 'utf-8')
    for (const name of ['trayWin.png', 'trayWin@2x.png', 'trayWinAlert.png', 'trayWinAlert@2x.png',
      'trayLinux.png', 'trayLinux@2x.png', 'trayLinuxAlert.png', 'trayLinuxAlert@2x.png']) {
      expect(gen).toContain(`"${name}"`)
    }
  })
})

describe('§4.9 login item is per-OS (applyLoginItem)', () => {
  it('macOS/Windows: packaged runs register on OS-view drift, assert off unconditionally', () => {
    for (const mod of [darwin, win32]) {
      const calls: unknown[] = []
      let osView = true
      const app = {
        isPackaged: true,
        getLoginItemSettings: () => ({ openAtLogin: osView }),
        setLoginItemSettings: (v: unknown) => calls.push(v),
      }
      // The third argument is win32's exec seam: every call in this suite
      // stubs it so the §4.9 legacy sweep never reaches the real reg.exe
      // (darwin ignores the extra argument).
      mod.applyLoginItem(app, true, () => {}) // already registered: no write
      expect(calls).toEqual([])
      // Off never trusts the OS reading (stale, or scoped to another copy):
      // it is asserted on every reconcile, even when the OS already says off.
      osView = false
      mod.applyLoginItem(app, false, () => {})
      mod.applyLoginItem(app, false, () => {})
      expect(calls).toEqual([{ openAtLogin: false }, { openAtLogin: false }])
    }
  })

  it('macOS: unpackaged (dev) runs never register but still assert removal', () => {
    const calls: unknown[] = []
    const app = {
      isPackaged: false,
      getLoginItemSettings: () => ({ openAtLogin: false }),
      setLoginItemSettings: (v: unknown) => calls.push(v),
    }
    darwin.applyLoginItem(app, true) // would enroll the bare Electron binary
    expect(calls).toEqual([])
    // macOS names the registration per-binary, so a dev off can only ever
    // clear this dev binary's own stale registration — never the installed
    // app's.
    darwin.applyLoginItem(app, false)
    expect(calls).toEqual([{ openAtLogin: false }])
  })

  it('Windows: unpackaged (dev) runs never touch the login item in either direction', () => {
    const calls: unknown[] = []
    const app = {
      isPackaged: false,
      getLoginItemSettings: () => ({ openAtLogin: false }),
      setLoginItemSettings: (v: unknown) => calls.push(v),
    }
    // The Run value is named by the shared AUMID, not by this binary, so a
    // dev off would delete the installed app's registration and the dev
    // guard could never write it back: a dev run asks the OS nothing.
    win32.applyLoginItem(app, true, () => {})
    expect(calls).toEqual([])
    win32.applyLoginItem(app, false, () => {})
    expect(calls).toEqual([])
  })

  it('Windows sweeps the pre-AUMID electron.app.* Run values (§4.9 legacy sweep)', () => {
    const RUN_KEY = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'
    // Drives sweepLegacyLoginItems with a recording fake exec: the query's
    // callback is held so each case can answer it differently.
    function sweep(): { calls: [string, string[]][], answer: (err: Error | null, stdout?: string) => void } {
      const calls: [string, string[]][] = []
      let query: ((err: Error | null, stdout?: string) => void) | null = null
      win32.sweepLegacyLoginItems((cmd: string, args: string[],
        cb: (err: Error | null, stdout?: string) => void) => {
        calls.push([cmd, args])
        if (args[0] === 'query') query = cb
      })
      return { calls, answer: (err, stdout) => query?.(err, stdout) }
    }

    // Autowright's own stale slot goes unconditionally; the generic dev-shell
    // name is only queried until its command is known.
    const own = sweep()
    expect(own.calls).toEqual([
      ['reg', ['delete', RUN_KEY, '/v', 'electron.app.Autowright', '/f']],
      ['reg', ['query', RUN_KEY, '/v', 'electron.app.Electron']],
    ])
    // A command naming this very binary is ours — matched case-insensitively,
    // because reg.exe echoes whatever casing the value was written with.
    own.answer(null, `electron.app.Electron  REG_SZ  "${process.execPath.toUpperCase()}"`)
    expect(own.calls[2]).toEqual(
      ['reg', ['delete', RUN_KEY, '/v', 'electron.app.Electron', '/f']])

    // Another app's dev shell owns the generic name: never deleted.
    const foreign = sweep()
    foreign.answer(null, 'electron.app.Electron  REG_SZ  "C:\\Elsewhere\\electron.exe"')
    expect(foreign.calls).toHaveLength(2)

    // No such value (or reg.exe failed): nothing to delete either.
    const missing = sweep()
    missing.answer(new Error('not found'))
    expect(missing.calls).toHaveLength(2)
  })

  it('Linux reconciles a marker-carrying XDG autostart .desktop file', () => {
    const dir = mkdtempSync(join(tmpdir(), 'autowright-autostart-'))
    const prevXdg = process.env.XDG_CONFIG_HOME
    process.env.XDG_CONFIG_HOME = dir
    const entry = join(dir, 'autostart', 'ai.autowright.app.desktop')
    const packaged = { isPackaged: true }
    const dev = { isPackaged: false }
    try {
      // Enable writes the entry: marker-owned, Exec quoted at this binary.
      linux.applyLoginItem(packaged, true)
      const text = readFileSync(entry, 'utf-8')
      expect(text).toContain('[Desktop Entry]')
      expect(text).toContain('X-Autowright-Login-Item=true')
      expect(text).toContain(`Exec="${process.execPath}"`)
      // Idempotent: a second enable leaves the same bytes.
      linux.applyLoginItem(packaged, true)
      expect(readFileSync(entry, 'utf-8')).toBe(text)
      // Disable deletes the ours-marker entry…
      linux.applyLoginItem(packaged, false)
      expect(existsSync(entry)).toBe(false)
      // Unpackaged (dev) enable never writes: the Exec line would point at
      // the bare Electron binary.
      linux.applyLoginItem(dev, true)
      expect(existsSync(entry)).toBe(false)
      // A dev run's whole reconcile is self-cleanup: a marker-carrying entry
      // whose Exec line names this very binary is a pre-guard dev leftover,
      // and it goes whatever the toggle says — the dev binary must never
      // autostart. (The packaged enable above writes Exec at process.execPath,
      // so the entry it leaves is self-pointing for the dev stub too.)
      linux.applyLoginItem(packaged, true)
      linux.applyLoginItem(dev, false)
      expect(existsSync(entry)).toBe(false)
      linux.applyLoginItem(packaged, true)
      linux.applyLoginItem(dev, true)
      expect(existsSync(entry)).toBe(false)
      // A marker-carrying entry pointing somewhere else is the *installed*
      // app's registration — the file name is shared by every copy — so a dev
      // run leaves it alone in both directions…
      const installed = ['[Desktop Entry]', 'Type=Application', 'Name=Autowright',
        'Exec="/opt/Autowright.AppImage"', 'X-Autowright-Login-Item=true', ''].join('\n')
      writeFileSync(entry, installed)
      linux.applyLoginItem(dev, false)
      expect(readFileSync(entry, 'utf-8')).toBe(installed)
      linux.applyLoginItem(dev, true)
      expect(readFileSync(entry, 'utf-8')).toBe(installed)
      // …while a packaged disable still deletes it: the marker is ours.
      linux.applyLoginItem(packaged, false)
      expect(existsSync(entry)).toBe(false)
      // …but a foreign file (no marker) is never touched, either direction.
      writeFileSync(entry, '[Desktop Entry]\nName=SomethingElse\n')
      linux.applyLoginItem(packaged, true)
      expect(readFileSync(entry, 'utf-8')).toBe('[Desktop Entry]\nName=SomethingElse\n')
      linux.applyLoginItem(packaged, false)
      expect(existsSync(entry)).toBe(true)
    } finally {
      if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME
      else process.env.XDG_CONFIG_HOME = prevXdg
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('§3 Linux desktop integration (applyDesktopEntry)', () => {
  const ICON = join(ELECTRON_DIR, 'icon', 'icon.svg')
  const withDataHome = (fn: (dir: string) => void) => {
    const dir = mkdtempSync(join(tmpdir(), 'autowright-desktop-'))
    const prev = process.env.XDG_DATA_HOME
    const prevAppImage = process.env.APPIMAGE
    process.env.XDG_DATA_HOME = dir
    delete process.env.APPIMAGE
    try { fn(dir) } finally {
      if (prev === undefined) delete process.env.XDG_DATA_HOME
      else process.env.XDG_DATA_HOME = prev
      if (prevAppImage !== undefined) process.env.APPIMAGE = prevAppImage
      rmSync(dir, { recursive: true, force: true })
    }
  }
  const packaged = { isPackaged: true }
  const dev = { isPackaged: false }

  it('installs a marker-owned launcher entry and the scalable icon under XDG_DATA_HOME', () => {
    withDataHome((dir) => {
      const entry = join(dir, 'applications', 'ai.autowright.app.desktop')
      const icon = join(dir, 'icons', 'hicolor', 'scalable', 'apps', 'ai.autowright.app.svg')
      linux.applyDesktopEntry(packaged, ICON)
      const text = readFileSync(entry, 'utf-8')
      // The basename equals package.json's desktopName minus the suffix — the
      // app-id Electron hands Wayland — and the entry names it again for the
      // desktops that index StartupWMClass instead.
      expect(text).toContain('[Desktop Entry]')
      expect(text).toContain('Type=Application')
      expect(text).toContain('Name=Autowright')
      expect(text).toContain(`Exec="${process.execPath}"`)
      expect(text).toContain(`TryExec=${process.execPath}`)
      expect(text).toContain('Icon=ai.autowright.app')
      expect(text).toContain('StartupWMClass=ai.autowright.app')
      expect(text).toContain('Categories=Utility;')
      expect(text).toContain('X-Autowright-Desktop-Entry=true')
      expect(readFileSync(icon)).toEqual(readFileSync(ICON))
      // Idempotent: a second launch leaves both files' bytes and mtimes alone.
      const before = [statSync(entry).mtimeMs, statSync(icon).mtimeMs]
      linux.applyDesktopEntry(packaged, ICON)
      expect([statSync(entry).mtimeMs, statSync(icon).mtimeMs]).toEqual(before)
      expect(readFileSync(entry, 'utf-8')).toBe(text)
    })
  })

  it('points Exec/TryExec at the AppImage and rewrites them when it moves', () => {
    withDataHome((dir) => {
      const entry = join(dir, 'applications', 'ai.autowright.app.desktop')
      process.env.APPIMAGE = '/opt/Autowright 1.AppImage'
      linux.applyDesktopEntry(packaged, ICON)
      let text = readFileSync(entry, 'utf-8')
      expect(text).toContain('Exec="/opt/Autowright 1.AppImage"')
      expect(text).toContain('TryExec=/opt/Autowright 1.AppImage')
      // Moved: the next launch rewrites the drifted lines (marker is ours).
      process.env.APPIMAGE = '/home/u/Apps/Autowright.AppImage'
      linux.applyDesktopEntry(packaged, ICON)
      text = readFileSync(entry, 'utf-8')
      expect(text).toContain('Exec="/home/u/Apps/Autowright.AppImage"')
      expect(text).not.toContain('/opt/')
    })
  })

  it('never writes unpackaged, never touches a foreign entry, never deletes', () => {
    withDataHome((dir) => {
      const entry = join(dir, 'applications', 'ai.autowright.app.desktop')
      const icon = join(dir, 'icons', 'hicolor', 'scalable', 'apps', 'ai.autowright.app.svg')
      // A dev run: the Exec line would name the bare Electron binary.
      linux.applyDesktopEntry(dev, ICON)
      expect(existsSync(entry)).toBe(false)
      expect(existsSync(icon)).toBe(false)
      // A user's own hand-written launcher (no marker) wins; the icon is
      // still installed — it is ours by name and harmless beside theirs.
      mkdirSync(join(dir, 'applications'), { recursive: true })
      writeFileSync(entry, '[Desktop Entry]\nName=My Autowright\n')
      linux.applyDesktopEntry(packaged, ICON)
      expect(readFileSync(entry, 'utf-8')).toBe('[Desktop Entry]\nName=My Autowright\n')
      expect(existsSync(icon)).toBe(true)
      // A missing icon source is best-effort: nothing throws.
      expect(() => linux.applyDesktopEntry(packaged, join(dir, 'nope.svg'))).not.toThrow()
    })
  })
})
