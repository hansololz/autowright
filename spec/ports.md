# Port worksheet — Windows & Linux open items

Temporary working notes (SPEC §17), **not a numbered spec section**. The ports themselves
shipped into the spec (§2 platform layer both halves, §3 per-OS service / notifier /
packaging / update blocks, §9 per-OS copy table, §13 tray, §18 per-OS script pairs); this
file holds only what remains open, consolidated from the former root `WINDOWS.md` /
`LINUX.md` worksheets. Every item below was re-verified against the working tree on
2026-08-27 (v0.7.1); resolved items were dropped (the Linux first-feed publish —
`release/linux-x86_64/latest-linux.yml` exists at 0.6.0 — and the e2e per-OS copy-helper
selectors, done via `app/e2e/harness.ts` `COPY`). Each remaining item moves into the
§-sections as it ships; delete the file when empty.

## Shared (both OSes)

- **`managedInstall` answers false on both** (`win32.cjs:122-124`, `linux.cjs:153-156`);
  the probe may later detect a distro-package / winget-style managed install.
- **Release messaging: Windows is advertised as a regular build; Linux is advertised as
  experimental, as a text link rather than a button.** The `experimental` tag on the download page's "Download for Windows" button and
  the README's Windows "unstable" wording were dropped together (2026-09-02); the build
  is still unsigned, so both keep the SmartScreen note. The page (`docs/index.html`,
  §17) offers the Windows installer as a ghost-style button that reads the
  `win32-x86_64` entry of `downloads.json`. Linux stays **unstable** in the README
  (lagging, and failing on Ubuntu 24.04+); on the page it is the underlined
  "Linux (Experimental)" text link in the mono platform line under the buttons, which
  reads the `linux-x86_64` entry (the AppImage) the same way (2026-09-02). Linux gets no
  button until its port is stable - when it is, add its button + copy the same way.

## Windows

- **Shipped 0.5.0 exe is cut off from updates — remedy still undecided.** It has the old
  `https://autowright.ai/updates/win32-x86_64/` feed URL baked in, which 404s since the
  feeds moved to GitHub raw (0c4e6f5). No win32 bridge exists (`release/` holds only the
  darwin `feed.json` Squirrel bridge); the 0.6.0 release (feed at
  `release/win32-x86_64/latest.yml`, 2026-08-23) did not restore the legacy path. Decide
  clean break vs restored legacy feed — and, per the shared release-messaging item,
  whether the site advertises the 0.6.0 exe it already indexes.
- **Verify `appMenu: true` on a real Windows build.** `spec/ui-shell.md` claims
  `titleBarStyle: 'hidden'` never draws the stock menu bar on Windows and
  `win32.cjs:151` relies on it (`main.cjs:1274` gates `Menu.setApplicationMenu(null)` on
  `!caps.appMenu`). If a real build does draw it, ship the same one-line
  `appMenu: false` Linux got (Ctrl+C/V/X/A stay Blink-native).
- **NSIS-specific updater behavior has zero test coverage on macOS hosts.** The
  electron-updater describe in `app/tests/main-cjs-leaf.test.ts` runs on every OS, so the
  shared handler logic is covered on mac hosts — but NsisUpdater-specific behavior
  (installer swap, differential/blockmap) still is not, and neither is the §3
  `installer.nsh` preInit hook (same-version relaunch): its vitest guard only pins the
  script's shape. Run the renderer suite (and e2e) on a real Windows host before the
  next Windows release, and run the freshly built installer twice — the second run must
  open the app with no install progress window.

## Linux

- **Published 0.5.0 AppImage is broken and should be pulled from the v0.5.0 GitHub
  release** (still attached as of 2026-08-27: `Autowright-0.5.0-linux-x86_64.AppImage`,
  3 downloads). Built before the AppImage-updater commit (ee2a847), so it ships
  `updates: false` forever and carries no block map; it also hits the AppArmor userns
  abort below on stock Ubuntu 24.04+. Users can reach it through the site's static
  latest-release fallback link. Delete the asset until a real `linux-scripts/release.sh`
  run publishes a working one.
- **New-agent page swallows the Linux sign-in instruction.**
  `app/src/pages/AgentNewPage.tsx:189-190` treats every 409 from `/agents/login` as
  "already signed in — ready to save". On Linux, `installer.login` raises the "run this
  command in your terminal" instruction for claude/gemini/opencode, which `api.py` turns
  into a 409 — so the user gets a false success toast and never sees the command.
  `Onboarding.tsx:289` already does this right by matching on the "already signed in"
  message text; the page must use the same check.
- ~~Tray stranding on GNOME / no context menu / unreliable `click`~~ — **resolved
  2026-09-01 by decision: Linux ships no tray surface at all** (`trayPanel` false in
  `linux.cjs`; §13 holds the rationale). No probe, menu, or residency rule needed —
  closing the last window quits the UI (§9 close rule) and the systemd backend keeps
  firing.
- **AppImage runtime needs FUSE (`libfuse2`) on some distros** — decide whether the
  download page documents `--appimage-extract-and-run` as the fallback.
- **Ubuntu 24.04+ AppArmor userns restriction**
  (`kernel.apparmor_restrict_unprivileged_userns=1`): unconfined Chromium cannot create
  its namespace sandbox and falls back to the SUID helper, which inside an AppImage can
  never be setuid root — the packaged app aborts exactly like a dev checkout did. Decide
  the packaged answer before the Linux release (ship an AppArmor profile with the app?
  document a one-time sysctl?). The dev loop already heals its own checkout:
  `linux-scripts/dev.sh` fixes `chrome-sandbox` ownership/mode via sudo (§18).
- **Dev-setup notes** (Debian/Ubuntu): `apt install python3.14-venv` (ensurepip is split
  out of the base python package, so `python3.14 -m venv` fails without it). Node lives
  user-local at `~/.local/opt/node-v24.19.0-linux-x64` (symlinked into `~/.local/bin`).
  Audit `scripts/dev.sh`/`build.sh` for GNU-vs-BSD flag drift before first use here.

## Added 2026-10-13 (audit)

- **Windows: a kill can wedge the drafting thread on the stdin writer.** `harness.PipeWriter`
  has no pollable pipe on Windows, so `defuse()` falls back to a cross-thread
  `TextIOWrapper.close()`, which waits on the buffer lock a blocked `write` holds. With an
  escaped grandchild holding the stdin read handle, the watchdog's kill wedges in
  `defuse()`, the main thread's `close()` then blocks on `_close_lock`, and the job never
  settles. Plan: cancel the writer thread's synchronous I/O (`CancelSynchronousIo` on the
  thread handle via ctypes) or close the raw handle instead of the text layer; needs a real
  Windows host to verify.
- **Windows: the §7 step-end group kill cannot reach a reaped root.** `_watch_exit` waits
  for the executor and then runs `taskkill /F /T`, which cannot address an exited pid, so a
  background child a step left behind survives, and (because the Windows defuse redirects
  the fd rather than waking the read) can hold the step "executing" until it exits. Plan:
  run each step in a Job object with kill-on-close.
- **Linux: the systemd unit's `ExecStart` is `sys.executable`,** which in a packaged
  AppImage sits under the runtime's `/tmp/.mount_*` FUSE mount that disappears when the app
  exits. If so, the backend cannot start at login without the app. Verify on a real host;
  likely remedy is extracting the Python tree to a stable location at install time.
  (2026-10-14 audit: confirmed by reading — `bundledPythonPath(process.resourcesPath)`
  lands under the mount, and the unit has `Restart=always` with `StartLimitIntervalSec=0`,
  so after a reboot systemd retries a missing `ExecStart` every 2 s; the CLI shim points
  there too. Same remedy.)
- **Linux: a logout runs the quit-entirely flow.** Electron's `powerMonitor 'shutdown'` on
  Linux comes from logind's `PrepareForShutdown`, which fires on poweroff/reboot only; a
  logout reaches the app as SIGTERM → `before-quit`, which §3 treats as a user quit and
  stops the user unit. With lingering enabled the backend then stays stopped across the
  next login. Verify on a real host; likely remedy is treating SIGTERM/SIGHUP from the
  session manager as a session end (UI-only quit) the way `'shutdown'` is.
- **Windows: cross-thread pipe close can wedge on the buffer lock.** Two sites, both
  traced through CPython's Windows `communicate` (helper threads blocked in `read()` hold
  the `BufferedReader` lock, so a `close()` from another thread waits on it): the
  `packages.py` pip drain after the 2 s `communicate(timeout=)` (`_pip_lock` held, so a
  wedge locks out every later install until restart), and `PipeWriter.defuse()`'s
  `_fd is None` branch closing the stdin pipe while the writer thread is blocked in
  `write()`. POSIX is covered by the wakeable raw pipes; Windows needs the
  `CancelSynchronousIo` plan above or an overlapped-I/O writer. Verify on a real host.
