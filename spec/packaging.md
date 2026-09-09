# Autowright SPEC — Packaging & process lifecycle

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 3. Packaging & process lifecycle (decided)

**The Python backend runs as a per-user OS service, independent of the Electron app** — a
launchd LaunchAgent on macOS, a Task Scheduler task on Windows, a systemd user unit on
Linux (their blocks below). Primary use case: a machine left running unattended for days
must keep firing triggers with no UI open.

Everything OS-coupled in this section sits behind the §2 platform layer. launchd is the
macOS `ServiceManager` (`service.py` is its implementation module); Task Scheduler is the
Windows one (the **Windows service** block below); systemd is the Linux one (the **Linux
service** block below — on a non-systemd host the `service` actions degrade to the plain
"not supported on <OS> yet" failure line, exit 1 via the result-code rule below, instead
of crashing on a missing `systemctl`). Future per-OS decisions are recorded in the
port plan, not here, until they ship.

**Implementation status:** the launchd/CLI/discovery half is implemented (`service.py`, `cli.py`,
`backend.json`), and so is app-launch registration (the ensure-backend step below). The
distributable build is implemented (`./scripts/prod.sh`, §18):
`Autowright.app` with the bundled relocatable Python in `Contents/Resources/python/` plus a DMG
and the §3 update zip,
always Developer-ID-signed with hardened runtime and notarized — there is no ad-hoc fallback.
(An ad-hoc build is not distributable: on a downloaded, quarantined copy, Gatekeeper silently
refuses to spawn the unsigned bundled Python as a LaunchAgent — see the install-verification
note below.) The identity comes from `CODESIGN_IDENTITY`, or is auto-detected as the single
"Developer ID Application" identity in the Keychain; the script aborts up front if neither
yields one. Notarization: `prod.sh` submits a zip of the signed app via
`xcrun notarytool submit --wait` (Keychain credentials profile named by `NOTARY_PROFILE`, default
`autowright-notary`), staples the ticket to the app, builds the update zip and the DMG from the
stapled app, signs
the DMG, then submits and staples the DMG as well — so both artifacts pass Gatekeeper offline
(the update zip carries the stapled app and needs no submission of its own).
The launch-time version-compare/re-register flow and the in-app updater are implemented (see
the update bullets below).

- **Identifiers (decided):** reverse-DNS of the product domain `autowright.ai` — app bundle id
  `ai.autowright.app` (set by `prod.sh` at packaging time; the same string is the Windows
  appId and AppUserModelID — the Electron main process calls
  `app.setAppUserModelId('ai.autowright.app')` on Windows so taskbar grouping/pinning and
  the §3 toast identity all agree with the installer's Start-menu shortcut), backend
  LaunchAgent label / Task Scheduler task name `ai.autowright.backend`. The Keychain
  service name is the plain string `Autowright` (§4.8), not reverse-DNS.
- The backend ships inside the Electron `.app` bundle
  (`Contents/Resources/python/`). **Ensure-backend:** at every app launch, the Electron main
  process probes the backend (`backend.json` + unauthenticated `GET /health`, short timeout); if
  unreachable, it runs the bundled service module — `Contents/Resources/python/bin/python3 -m
  autowright.service install` — which writes the LaunchAgent plist and bootstraps it via
  `launchctl`. **The probe validates the payload, not just reachability** (`backendVersion()`):
  a response counts as our backend only when it parses as JSON whose `app` field equals
  `Autowright` and whose `version` field is a non-empty string. Anything else - a bare 200, an
  HTML error page, another service's JSON - is treated as unhealthy, so a foreign server
  squatting the port a stale `backend.json` records can never satisfy ensure-backend, and the
  version-compare bullet below (which reads the same validated `version`) can never be driven
  by a stranger's payload. An unhealthy answer takes the same path as an unreachable one:
  `service install` runs and re-publishes `backend.json` with the real port. `service.py` owns this and is directly runnable (`python -m autowright.service
  install|uninstall|status|restart`); the §20 CLI's `autowright service …` group is a thin
  wrapper over the same functions. **The UI and the backend never invoke the CLI** — the CLI is
  a pure client leaf (it calls the backend API and the service module; nothing calls it). The
  app may *install* the CLI shim (the CLI-on-PATH bullet below) but never executes it. This is
  the same single `service install` code path headless setups run by hand; there is no separate
  registration mechanism (`SMAppService` was considered and dropped — it would need a native
  helper for no gain). No sudo required. A healthy backend is never touched, so an app launch
  never interrupts running executions. Registration output/errors append to `app.log`
  (visible in the §9.3 developer log overlay). Dev launches (`electron .` from the repo) have no
  bundled Python, so the ensure step is a no-op there — `scripts/dev.sh` installs the service
  from the repo venv before launching Electron, through the same `service install` code.
  **Install verification:** `launchctl` can report success while the job never spawns (observed:
  Gatekeeper silently refuses to exec an unsigned, quarantined bundled Python as a LaunchAgent —
  the GUI app's user approval does not extend to launchd). So after a `service install`, the main
  process polls `/health` every 2 s for up to 30 s. Success and failure both append to `app.log`;
  on failure the main process also captures `launchctl print gui/<uid>/ai.autowright.backend`
  into `app.log` and records a failed ensure-backend status. The renderer reads that status over
  the preload bridge (`backend-status` IPC: `{ state: 'idle'|'installing'|'ok'|'failed', detail }`)
  and the §9 boot splash shows the failure detail instead of waiting silently. The renderer keeps
  retrying regardless — a late backend still connects.
- **Bundled Python (decided):** the app ships its own relocatable CPython (python-build-standalone
  builds) inside the bundle (`Contents/Resources/python/`; on Windows the packaged layout is
  `resources\python\` and the interpreter sits flat at `python\python.exe` — the
  `*-pc-windows-msvc-install_only` builds have no `bin/` directory — resolved per-OS by the §2
  platform layer's `bundledPythonPath`). The backend, the engine, and every
  step script execute on this one interpreter. The system/user Python is never used, never required,
  and never installed — users install nothing, and every Mac gets the identical interpreter
  version. The launchd plist points at the bundled interpreter by absolute path (`sys.executable`
  at install time; python-build-standalone resolves its own home relative to the binary, so no
  `PYTHONHOME` is needed). Moving the app bundle breaks that path — launchd then can't start the
  backend, and the next app launch's ensure-backend step re-registers with the new path.
  pip-generated `bin/` entry-point scripts carry absolute staging-path shebangs from the build,
  so nothing may invoke them inside the bundle — the backend/CLI always run as
  `python3 -m autowright.main` / `-m autowright.cli`. **Signing procedure:** every Mach-O gets a
  hardened-runtime, timestamped Developer ID signature, signed inside-out and explicitly — never
  `codesign --deep`, which leaves Electron Framework's nested dylibs (libEGL, libGLESv2,
  libffmpeg, libvk_swiftshader…) unsigned/un-timestamped and breaks the outer seal (notarization
  rejects with "signature of the binary is invalid"). Order: (1) all `.so`/`.dylib` + executables
  in the Python tree, the Mach-O executables with the interpreter entitlement below, (2) every
  Mach-O inside `Contents/Frameworks`, detected by file content
  rather than name/location — executables hide in odd places (Squirrel's `Resources/ShipIt`,
  Electron's `Helpers/chrome_crashpad_handler`),
  (3) each `.framework` bundle, (4) each helper `.app` with the Electron entitlements,
  (5) the main app bundle with the same entitlements. Entitlements (generated by `prod.sh`):
  the helper apps and the main bundle get `com.apple.security.cs.allow-jit` and
  `com.apple.security.cs.allow-unsigned-executable-memory` (required for Electron/V8 under
  hardened runtime). The Python-tree Mach-O executables get the **interpreter entitlement**
  `com.apple.security.cs.disable-library-validation`, and only they need it: hardened-runtime
  library validation lets a process load only code signed by Apple or by its own Team ID, while
  the §6.2 declared packages are pip wheels whose extension modules arrive ad-hoc signed with no
  Team ID. Without the exception every native wheel (numpy, pandas, pillow, cryptography, ...)
  installs fine (pip is pure Python) and then fails at the step's import with dlopen's
  "mapping process and mapped file (non-platform) have different Team IDs", which numpy wraps as
  "Importing the numpy C-extensions failed". Every release up to 0.10.1 shipped without it;
  the dev loop never shows the failure because the repo venv's interpreter is unsigned. The
  entitlement goes on the interpreter executables only (a process's entitlements come from its
  main executable), never on the `.so`/`.dylib` files and never on the Electron side, so the
  Electron helpers keep full library validation. **Post-sign probe** (after step 5, before the
  seal re-verify): copy one bundled extension module outside the bundle, re-sign the copy ad-hoc
  (`codesign -s -`, the same signature a wheel carries), and `dlopen` it under the signed
  interpreter (`ctypes.CDLL`, run with `PYTHONDONTWRITEBYTECODE=1` so the probe writes nothing
  into the sealed tree). The build fails there the moment the entitlement drops off, instead of
  shipping a DMG whose every native wheel is dead. **Native-wheel probe** (right after it): the
  dlopen probe proves the entitlement, this proves the whole §6.2 path a user's step takes —
  the signed interpreter's own pip installs a real wheel with a compiled extension (`numpy`,
  the package the 0.10.2 report named) with the same `--only-binary :all:` / `--target` form
  `packages.ensure` uses, into a directory outside the bundle (`build/wheel-probe`, deleted
  afterwards), and then imports it from that directory under the signed interpreter (again
  with `PYTHONDONTWRITEBYTECODE=1`). Network is already a build prerequisite (the CPython
  download, notarization), and pip's own HTTP cache keeps repeat builds cheap. A wheel that
  fails to install or import fails the build with the same words a user's step would show.
  A §15 drift guard pins the entitlement text and both probes in `prod.sh`. The bundled-interpreter smoke test
  must run **before** signing (and with `PYTHONDONTWRITEBYTECODE=1`): importing packages writes
  `.pyc` files into `Resources/python`, and any write after signing breaks the bundle's resource
  seal — notarization then rejects the main binary even though the pre-write local verify passed.
  The seal is re-verified immediately before submission.
- **Bundle trimming (decided):** the distributable ships only what runs. `prod.sh` trims in
  three places, all **before signing** (the seal covers the final tree), and the in-bundle
  smoke check runs on the trimmed tree, so a pruned module the backend or a curated package
  still needs fails the build instead of shipping. (1) **The bundled Python tree**, right
  after the pip install and before the copy into the bundle: the Tcl/Tk stack goes
  (`lib/tcl*`, `lib/tk*`, `lib/itcl*`, `lib/thread*`, `lib/libtcl*`, `lib/libtk*`,
  `lib/python3.X/tkinter`, `idlelib`, `turtledemo`, `turtle.py`, `lib-dynload/_tkinter*`: a
  GUI toolkit; the backend and every step run headless under launchd), `ensurepip` goes (pip
  is already installed and upgraded; nothing in the bundle creates venvs), the C-API build
  scaffolding goes (`include/`, `share/`, `lib/pkgconfig`, `lib/python3.X/config-*`,
  `site-packages/lxml/includes`: §6.2 installs `--only-binary :all:`, so no extension is ever
  compiled against the bundled interpreter), and every fat Mach-O (`.so`/`.dylib`) is thinned
  to the host arch with `lipo -thin` (universal2 wheels such as lxml carry a dead Intel slice
  on arm64 and vice versa; the file is rewritten in place so its mode survives). **Never
  trimmed:** `pip` (the §6.2 installer), `__pycache__` (the sealed tree is read-only at
  runtime; without shipped bytecode the interpreter would try to write it into the bundle on
  every import), any curated package or any part of one (`lxml.objectify` included: the
  curated list is the user-facing step environment), and the stdlib beyond the modules named
  above (`pydoc`, `unittest`, `venv`, `_pyrepl` stay: a step may reasonably use them).
  (2) **Electron locales:** the app is English-only, so every `*.lproj` other than
  `en*.lproj` is removed from both `Contents/Resources/` (the empty markers `@electron/packager`
  creates; they are what macOS reads to pick the app's localization) and
  `Electron Framework.framework/Versions/A/Resources/` (the `locale.pak` payloads, ~12 MB
  compressed for the 200+ non-English locales). Chromium falls back to `en-US` for anything
  unmatched. The Windows/Linux electron-builder legs get the same trim through
  `build.electronLanguages` (`en`, `en-US`, `en-GB`) in `app/package.json`. (3) **The asar:**
  `@electron/packager` packages an allowlist (`electron/`, `dist/`, `package.json`, and the
  computed electron-updater `node_modules` closure) and ignores every other top-level entry
  under `app/`, so a stray gitignored directory can never ride along again (through 0.11.2 the
  ignore list was a denylist, and vitest's `coverage/` report, the design-sync `.ds-css/` font
  cache, and `tsconfig.test.json` all shipped inside `app.asar`). (4) **DMG format:** `hdiutil
  create -format ULMO` (lzma). Measured on the untrimmed 0.11.2 bundle: UDZO (zlib, the format
  through 0.11.2) 225 MB / 11 s to create / 4 s to mount, ULFO (lzfse) 199 MB / 10 s, UDBZ
  (bzip2) 183 MB / 40 s / 9 s to mount, ULMO 156 MB / ~16 min / 7 s to mount. ULMO's
  single-threaded compression is the one cost, and a release cut (already minutes of
  notarization) absorbs it; UDBZ is the documented fallback if that ever bites. The update zip
  stays deflate (`ditto -c -k`): Squirrel.Mac unpacks it with `ditto` and reads nothing else. A §15 drift guard pins the trim steps' position
  (Python trim between the pip install and the copy into the bundle, locale trim before the
  codesign step), the never-trimmed names, the packager allowlist, the DMG format, and the
  electron-builder language list.
- **CLI on PATH (decided):** the CLI ships only inside the bundle — never via pip/PyPI (a second
  channel would reintroduce a user-provided Python and version skew between CLI and backend,
  which the one-`VERSION` design excludes by construction). The command is a shim script named
  `autowright`: `#!/bin/sh` with an `# autowright CLI shim` marker line, then
  `exec "<python>" -m autowright.cli "$@"` (module form per the shebang rule above; `<python>`
  is the backend's real interpreter). On Windows (§2 groundwork, defined by `win32.cjs`) the
  shim is a batch file `autowright.cmd`: `@echo off`, a `rem autowright CLI shim` marker line,
  then `"<python>" -m autowright.cli %*`, CRLF line endings, installed at
  `%LOCALAPPDATA%\Autowright\bin\autowright.cmd` (user-owned, same no-privilege rule; the
  backend `service install` healing half below covers this `.cmd` shim too, through the
  Windows ServiceManager in `platform/windows.py`). The command name is `autowright` (no
  short alias for now).
  **One install location per OS:** `~/.local/bin/autowright` (macOS/Linux) /
  `%LOCALAPPDATA%\Autowright\bin\autowright.cmd` (Windows) — user-owned, so no privilege and no
  password, ever. There is no admin-prompt (osascript) flow anywhere anymore. Creation happens
  in exactly two ways, both silent and user-local: the §4.9 COMMAND LINE card (its `cliEnabled`
  toggle, or the missing-row Reinstall button) and a **one-shot first-run install**. The
  one-shot runs at renderer boot once the settings snapshot has loaded and the preload bridge
  exists: if `cliEnabled` (default true, §4.9) is on and the `ad-cli-installed` localStorage
  marker (§15) is unset, the renderer reads `cli-status` and fires `cli-install` when the
  state is `missing`. The marker records "first run settled": it is set on install success and
  when the state is already `installed` or `foreign` (foreign files are never
  touched); a failed install leaves it unset so the next launch
  retries, and, unlike the toggle flow, never patches the setting to false (a transient
  failure must not become a permanent opt-out). Successful card installs set the marker too.
  Once the marker is set the app never creates a shim on its own again: a hand-deleted shim
  stays deleted (the §4.9 missing row is the explicit way back). Turning the toggle off also
  deletes the shim, behind the §4.9 disable confirm — never silently. The preference itself
  is the stored
  `cliEnabled` setting (§4.9); the shim files on disk stay the truth about what's installed. `~/.local/bin` may be absent from the user's PATH — the shell checks the
  **login-shell** PATH (GUI apps inherit a stripped one, so it asks
  `$SHELL -l -c 'printf %s "$PATH"'` with a ~2 s timeout, caches the answer per app run, and
  counts any failure as not-on-PATH) and the card shows the one-line fix
  (`export PATH="$HOME/.local/bin:$PATH"`) after install rather than editing anyone's
  dotfiles. On Windows the PATH check reads the process environment's `PATH` instead (GUI
  apps there inherit the full user PATH — no login shell exists to ask). The per-OS location
  above is the **only** shim location — nothing outside the
  user's profile is ever read, written, or deleted. Ownership of the two halves is split:
  - **Creation is the Electron shell's job — explicit and silent.** The shell exposes two
    IPCs on the preload bridge: `cli-status` (reads the shim; states `installed` — marker
    present and exec line points at the current backend interpreter from `backend.json`
    (an ours shim pointing at another interpreter is healed in place right here — a
    user-owned file rewrites without a directory write) — `missing`, and `foreign` — the
    file exists without the marker; never touched. The result also carries `path` — the
    shim path — and `onPath` — whether `~/.local/bin` is on the
    login-shell PATH — so the §4.9 card can name the location and show the PATH hint) and
    `cli-install` (plain unprivileged writes to `~/.local/bin/autowright`: `mkdir -p`, write
    the shim, `chmod 755`. No dialog, no password). A third IPC, `cli-uninstall`
    (fired by the §4.9 disable confirm), removes the ours-marker shim — same
    rules as `service uninstall` below: marker required, foreign files never touched, and a
    failed delete is reported back as an error message the §4.9 card toasts.
    The interpreter path comes from `backend.json`'s `python` field, so the same code works
    in dev (repo venv) and prod (bundled interpreter) — no dev-only path.
  - **Healing is `service install`'s job — silent and sudo-free** (§3 has no sudo anywhere in
    the plumbing): when the shim exists, carries the marker, and its exec line names a
    different interpreter (moved bundle, dev↔prod
    switch, update), install rewrites it in place — rewriting a user-owned file needs no
    directory write. It never *creates* a shim (creation is the shell flow above) and never
    touches a foreign file (no marker). The install result line reports the shim state either
    way.
  `service uninstall` removes the shim when the marker identifies it as
  ours (a failed delete puts the error on the result
  line). Users who skip the shim always have the
  module form: `<python> -m autowright.cli`.
- launchd keeps it alive: `RunAtLoad` + `KeepAlive` (restart on crash). launchd also guarantees a
  single backend instance — the UI and CLI are always clients, never owners.
- **Windows service (decided): Task Scheduler**, implemented in `platform/windows.py` —
  the closest match to `RunAtLoad` + `KeepAlive` (logon trigger + restart-on-failure) with no
  extra supervisor process. The launchd contract above maps as:
  - Task name `ai.autowright.backend` (the same reverse-DNS label as the LaunchAgent).
    Action: the backend interpreter's `pythonw.exe -m autowright.main` — `pythonw.exe` sits
    beside `python.exe` in both the venv and the python-build-standalone layouts, and it
    keeps a console window from flashing at every logon.
  - Registration goes through the PowerShell ScheduledTasks cmdlets, never bare
    `schtasks.exe`: restart-on-failure (`New-ScheduledTaskSettingsSet -RestartCount
    -RestartInterval`, the KeepAlive equivalent) is not expressible from the schtasks CLI.
    Logon trigger (`New-ScheduledTaskTrigger -AtLogOn`, the RunAtLoad equivalent); install
    starts the task immediately (`Start-ScheduledTask`, the `launchctl kickstart`
    equivalent).
  - Verb mapping: `install` = register (or update in place) + start + the same health-poll
    verification as macOS; `uninstall` = stop + `Unregister-ScheduledTask`; `status` =
    `Get-ScheduledTask` state line; `stop` = `Stop-ScheduledTask` only — the task stays
    registered and returns at next logon (mirrors the macOS "bootout only" rule); `restart`
    = stop + start. Every verb answers a §3-style result line so `service.result_code`
    keeps working, and every PowerShell invocation carries a timeout with a plain-word
    failure on expiry (the same never-hang rule as `launchctl`'s 30 s cap).
  - Log routing: Task Scheduler does not capture stdout/stderr the way launchd does — on
    Windows the backend opens and rotates its own log file under the §5 logs root
    (`main.py`), and the writer must never keep the handle open across the startup trim
    (a held handle turns the trim into a sharing violation).
  - With this shipped, `capabilities.service` is true on Windows and the §3 shim-heal half
    of `service install` covers the `.cmd` shim.
  - **Windows notifier (decided, ships with the packaging step):** toasts via a PowerShell
    WinRT invocation (`ToastNotificationManager` + toast XML, no extra dependency), posted
    under the AppUserModelID `ai.autowright.app`. An unpackaged app cannot own an AUMID —
    Windows requires a Start-menu shortcut carrying it, which the §3 NSIS installer
    creates — so the notifier ships with the packaging step, degrades silently when the
    AUMID isn't registered (dev runs), and `capabilities.notifications` flips true only in
    the packaged build's environment (probed, not assumed).
  - **Semantics that differ from launchd, accepted:** `Stop-ScheduledTask` terminates the
    task's process tree outright — Windows has no SIGTERM-to-the-job analogue — so the
    graceful-shutdown pass (hard-killing live step groups, cancelling drafting jobs,
    unlinking `backend.json`) never runs on a Windows stop/restart/uninstall. A stale
    `backend.json` therefore survives a stop until the next boot rewrites it — covered by
    the stale-file readers above, and never a crash. Stop verification is the manager's
    view (task state left `Running`), which flips ~1.5 s before the process tree is fully
    gone; install's stop→register→start sequence outlasts that gap. `status` has no pid to
    print (`Get-ScheduledTask` exposes none): the Windows lines are `active (task
    running)`, `stopped (task present) — returns at next logon or app launch` (exit 0), and
    `not installed` (exit 1) — same three states as macOS, pid replaced by the task view.
    Windows `stop` runs the same stray-process sweep as macOS (quit-entirely bullet below)
    after `Stop-ScheduledTask` verifies: `kill_matching` enumerates `Win32_Process` command
    lines via `Get-CimInstance` with plain `.Contains()` marker matches (never `-like`
    wildcards, so marker paths need no escaping), skips rows with a NULL `CommandLine`
    (never kill what can't be verified), and excludes both the enumerating PowerShell
    (`$PID` — its own command line embeds the marker literals) and the sweeping python's
    pid; each match dies by the `taskkill /F /T /PID` tree kill, both TERM/KILL grades
    collapsing to it as everywhere on Windows. The sweep is the compensation for the
    graceful pass never running here: it clears the own-process-group children the tree
    kill orphans (pip, executors, stray CLI invocations).
- **Linux service (decided): systemd user unit**, implemented in `platform/linux.py` —
  the closest match to `RunAtLoad` + `KeepAlive` (enabled unit + `Restart=always`) with no
  extra supervisor process. The launchd contract maps as:
  - Unit file `ai.autowright.backend.service` in `~/.config/systemd/user/` (the same
    reverse-DNS label as the LaunchAgent). `ExecStart` = the backend interpreter's
    `python3 -m autowright.main` (absolute path, systemd-quoted); `Restart=always` with
    `RestartSec=2` and `StartLimitIntervalSec=0` — KeepAlive's never-give-up restart,
    throttled the way launchd throttles; `[Install] WantedBy=default.target`, enabled
    (the RunAtLoad equivalent — starts at the user session's login).
  - Registration goes through `systemctl --user`: install writes the unit file,
    `daemon-reload`s, `enable`s (idempotent), then `restart`s — a restart, not a start,
    so a rewritten unit (moved install, dev↔prod switch, update) is always adopted, the
    unload-then-load rule in systemd clothes. Success is only ever the unit polling
    `active` afterwards (the same never-trust-the-accept rule as launchctl/Task
    Scheduler), and the app's ensure-backend step runs the same §3 health-poll
    verification as macOS on top. Every `systemctl --user` invocation carries the same
    30 s timeout with a plain-word failure on expiry ("systemctl timed out") as
    `launchctl`'s cap — never a hang, never a traceback.
  - Verb mapping: `install` = write unit + reload + enable + restart + active poll (+ the
    shim-heal half, the POSIX shim shared with macOS); `uninstall` = `disable --now` +
    delete unit + reload; `status` = unit-file presence + `is-active` (+ `MainPID` and the
    discovery-port note) mapped to the same three states as macOS: `active (pid N)`,
    `stopped (unit present) — returns at next login or app launch` (exit 0 — stopped on
    purpose is not a failure), `not installed` (exit 1); `stop` = `systemctl --user stop`
    only — the unit stays enabled and returns at next login (the "bootout only" rule) —
    plus the §3 stray-process sweep; `restart` = `systemctl --user restart` + active poll.
  - Log routing: `StandardOutput=append:` / `StandardError=append:` to the §5 logs root's
    `backend.out.log` / `backend.err.log` — the same file capture as launchd, so the §9.3
    log overlay and `main.py`'s startup trim work unchanged (journal-only routing would
    orphan them). `append:` needs systemd ≥ 240 (everywhere current); install creates the
    logs directory first — systemd creates the files but not their directory (same rule
    as launchd).
  - **Semantics that differ from launchd, accepted:** `systemctl stop` TERMs every process
    in the unit's cgroup (`KillMode=control-group`, the default), so own-session children
    (executors, pip) receive the TERM directly instead of surviving to the sweep — the
    backend's graceful shutdown still runs, and the sweep still covers whatever escapes
    the cgroup (directly-spawned dev backends, stray CLI invocations).
  - Non-systemd hosts (no `systemctl` on PATH) compose the degraded fallback service
    manager and `service: false` — plain "not supported" failure lines, never a crash.
    Not supported for v1, documented. Linger is not needed: the unit is per-login-session,
    same as the LaunchAgent, and the §3 stay-logged-in guidance carries over.
  - **Linux notifier (decided):** `notify-send` (libnotify), with the darwin contract —
    best-effort, never raises, never blocks past a 10 s timeout. The binary is probed once
    at platform composition and `capabilities.notifications` answers its presence (absent
    on minimal installs — probed, never assumed).
- Step processes die with their backend: graceful shutdown hard-kills every live step group,
  and startup recovery SIGKILLs any group a crashed backend orphaned (via the record's
  persisted `pgid`, §4.5, with a pid-reuse guard) before marking its record interrupted —
  otherwise the orphan keeps writing `memory/` while the next cron tick starts a second copy.
  The repair trusts the yaml, not the §5 index row it iterates: a crash between an
  execution's final yaml write and its sqlite commit leaves the row one transition behind,
  and a record whose yaml already holds a terminal status is re-adopted as it stands (index
  row re-synced), never rewritten as interrupted or skipped — the yaml is authoritative (§5).
  Graceful shutdown is time-boxed: uvicorn runs with `timeout_graceful_shutdown` set to the
  `main.py` `SHUTDOWN_GRACE_S` constant (5 s), because the renderer keeps its §19 `/ws`
  socket open during quit-all and reset, and an unbounded shutdown waits on open WebSockets
  forever, blowing the stop's 10 s deregistration wait. At the bound uvicorn force-closes
  the connections and the lifespan shutdown still runs. Every piece of the backend's
  shutdown work lives in that lifespan: uvicorn re-raises the captured SIGTERM once its
  `run()` returns, killing the process before any code after `run()` (a `finally` in
  `main()` included) can execute, so `main()` registers its cleanup with the api module in two halves: `api.register_quiesce`
  (stopping the scheduler and the listeners) runs **before** the kill passes — a cron tick or
  a message firing landing during uvicorn's graceful drain must never start a step group
  after the sweep that nothing will kill — and `api.register_shutdown` (stopping the
  discovery-guard thread, then unlinking its own `backend.json`) runs after them; every
  callback runs once and error-tolerant. The kill pass also flips the engine into a stopping
  state in which every later `start` is refused ("the backend is shutting down"), closing the
  window a tick already inside `fire_trigger` could otherwise slip through. Uvicorn owns
  SIGTERM/SIGINT only once `run()` installs its handlers, though — and `backend.json` is
  published before `run()` (bind → listen → publish, then the guard thread, scheduler,
  listeners, and power reconcile all start). A stop signal landing in that boot window
  would die on the default handler and leave the file behind, so `main()` installs an
  interim SIGTERM/SIGINT handler immediately before publishing: it stops the discovery
  guard, unlinks its own `backend.json` (same pid check and `_publish_lock` as the
  lifespan cleanup), then restores the default disposition and re-raises, so the process
  still dies by the signal. Uvicorn saves that handler as the previous one and re-raises
  onto it after `run()`, where it finds the file already unlinked and just re-kills —
  exit semantics are identical in both paths. A signal-driven
  stop therefore removes `backend.json` on macOS at any point after publish; the Windows
  tree kill still leaves it (the stale-file caveat in the Windows block).
  §8 drafting harnesses die with the backend too: graceful shutdown cancels every
  still-building drafting job and SIGKILLs its harness session group outright (the process is
  exiting, so cancel's term-then-kill grace thread would never get to fire). Unlike step
  groups they leave no persisted record, so crash recovery cannot sweep one; a harness
  orphaned by a backend crash simply runs to its own completion. The mirror case — a
  client that dies or navigates away while the backend lives on — is not an orphan at
  all: the job keeps building and its outcome is held for the §11 re-attach (§19
  background continuation), bounded by the §8 idle window and wall-clock hard cap.
- Quitting the Electron app (window and menu bar) never stops the backend; the scheduler keeps
  running. The §4.9 `login` setting controls only whether the UI starts at login — the backend
  service stays registered regardless once onboarding completes.
  **One explicit exception:** the Settings page's "Quit Autowright entirely" action (§4.9 QUIT
  card). It runs `python -m autowright.service stop` and then quits the Electron app; the
  plist and the CLI shim stay on disk. `stop` is bootout **plus a stray-process sweep**: after
  launchd deregisters the job (or the 10 s deregistration wait expires), the stop TERM-then-KILLs
  every remaining process whose command line carries an Autowright marker (the §2
  `kill_matching` primitive), always excluding the stop process itself and its own process
  group (the Electron caller). The markers come from `paths.sweep_markers()`: the module
  invocation `-m autowright.` (survives interpreter-path resolution in `ps`, which shows a
  venv python's resolved framework binary in dev), the current interpreter path
  (`sys.executable`, which in the packaged app is the in-bundle python and so also matches
  pip children), the `bin/autowright*` entry scripts beside it, and on Windows the console
  sibling from `paths.console_python()`. Never the realpath'd interpreter: in dev that is a
  shared system python and would match unrelated processes. The invariant this buys:
  immediately after a successful quit-all, no process holds the app bundle open, so the user
  can move Autowright.app to the Trash with no "in use" alert. A sweep that ended processes
  appends an informational `· ended N lingering process(es)` note to the success line (after
  `·`, so `service.result_code` is unchanged). The live-execution gate is a confirmation, not
  a hard block: a busy answer makes the renderer ask whether to shut everything down anyway
  (§4.9 force-confirm modal) and, on confirm, retry the `quit-all` IPC with `force: true`,
  which skips the gate; the backend's graceful shutdown (`kill_all_live`) plus the sweep end
  the running execution. If the stop fails, the app does **not** quit — the error is surfaced
  instead; the app must never quit its UI while the backend it promised to stop keeps running.
  The shell's service children are time-boxed like `service.py`'s own `launchctl` calls: every
  `python -m autowright.service <verb>` the Electron main process spawns (the ensure-backend
  `install`, quit-all's and reset's `stop`) carries a 120 s `execFile` timeout, and the
  quit-all/reset wait on a racing install is bounded the same way — a wedged interpreter
  answers the IPC with a plain failure line ("service stop timed out") instead of leaving
  the QUIT card or the reset overlay spinning forever with `quittingAll` latched.
  A stopped backend returns at next login (`RunAtLoad`) or next app launch (ensure-backend
  re-heals) — stopped, never uninstalled.
- **Reset — delete all data and quit app (§4.9 RESET card, decided).** The renderer confirm
  fires a `reset-all` IPC; the Electron main process orchestrates, in order:
  1. The same live-execution gate as quit-all/update-install (busy → `{ busy: true }`,
     nothing touched; an unreachable backend counts as idle — but a reachable backend that
     answers the probe with anything but 200 (a stale token, a 500 mid-execution) is
     **unknown**, and unknown gates like busy: the install, quit, or reset must never proceed
     on a backend that may be executing).
  2. Capture `GET /settings`' `dataPath` while the backend is still up — the executions dir
     is user-movable (§4.9) and may live outside the data root.
  3. `DELETE /secrets` on the live backend (§19) — only the backend's keyring can reach the
     Keychain / Credential Manager. A failure here (the §19 unreadable-store 409 included) is
     logged to `app.log` and the reset **proceeds**: value deletion is already best-effort per
     entry (§4.8), and an unreadable `secrets.yaml` means those ids were unreachable this
     session anyway.
  4. `service stop` through the same interpreter resolution and install-interlock as
     quit-all (the reset's last `app.log` line is written here, before step 5 deletes the
     logs root — a line written after it would silently re-create the root). A stop failure aborts the reset with `{ error }` — the app stays up, and at
     that point nothing has been deleted beyond step 3's secrets. An absent registration
     with nothing running is **not** a failure (stop is idempotent, headless-mode stop
     bullet) — reset proceeds on a machine whose service was never registered.
  5. Delete the execution data in the captured `dataPath`, the logs root, and every entry of
     the data root **except** the live Chromium profile `electron/` — Chromium holds open
     handles on it, so deleting it would fail (on Windows, a sharing violation outright).
     The executions dir is deleted **selectively, never wholesale**: only entries Autowright
     wrote — `executions.db` (and its `-wal`/`-shm` siblings) and per-execution directories
     identified by a contained `execution.yaml` — then the directory itself is removed only
     if that left it empty. The dir is user-movable and (§19 guard notwithstanding) may have
     accumulated foreign files; reset must never delete a file Autowright did not write.
     Selectivity also makes the except-the-live-profile invariant hold by construction even
     for a `dataPath` pointed at the data root itself: the profile matches neither rule, so
     no wholesale-delete guard is needed. The data root's own per-entry sweep stays
     wholesale — everything directly under the root is Autowright's. The
     profile is cleared with `session.defaultSession.clearStorageData()` plus `clearCache()`
     instead — every §15 localStorage marker (`ad-cli-installed` among them) goes, which is
     what matters — and the directory's residual Chromium internals are accepted residue
     (same acceptance as the updater-cache bullet below). On Windows every deletion retries
     briefly (up to ~10 s total): the stop-verification gap above means the backend's file
     handles (`executions.db`, its own log file) can outlive a reported stop by a moment.
     Deletion failures that survive the retries are logged and the flow continues — a
     leftover file must not strand the app mid-reset. Step 4's stray-process sweep means
     the backend's file handles are normally gone before deletion starts, leaving the
     retry loop to cover the Windows stop-verification gap alone.
  6. `app.exit(0)`. The app quits and stays quit (no relaunch); launching it again is the
     user's move. That next launch finds no `backend.json` and an empty data root:
     ensure-backend re-registers the service, the backend recreates the §5 layout with
     defaults, and §10 onboarding runs as on a fresh install. In a dev launch the quit ends
     the §18 harness's foreground Electron like any other quit: the exit trap clears Vite,
     and the backend stays stopped (step 4 took it down) until the next dev run
     re-installs it.

  Each destructive step announces itself to the main window as it starts — a
  `reset-progress` renderer push carrying a stage token: `secrets` (step 3), `service`
  (step 4), `data` (step 5), `quit` (step 6). Fire-and-forget, no renderer ack —
  these drive the §4.9 reset progress overlay's stage line; the busy gate and the
  `dataPath` capture push nothing (the overlay shows "Preparing…" until the first token).

  **The service registration, the CLI shim, and the app itself deliberately survive a
  reset** — only data is erased. The wiped first-run marker just lets the §3 one-shot settle
  again (shim already `installed` → marker re-set, no write). Headless parity is composition,
  not a new verb: `autowright secret delete --all` (§20), `autowright service stop`, then
  deleting the §5 roots by hand. There is no in-app uninstall — removing the app itself is
  the OS's job (or Homebrew's, whose cask `zap` covers the data directories, cask bullet
  above); `service uninstall` remains the headless way to deregister the backend and remove
  the shim.
- Discovery: the backend listens on localhost and writes its port + auth token to
  `backend.json` in the §5 data root — `~/Library/Application Support/Autowright/` on macOS,
  written 0600; `%LOCALAPPDATA%\Autowright\` on Windows, where POSIX mode bits restrict
  nothing and the protection is the profile's inherited ACL (`%LOCALAPPDATA%` grants access
  only to the user's account, SYSTEM, and Administrators — the same trust boundary 0600
  draws on macOS, where root reads anything; documented, no explicit icacls write). UI and
  CLI read it to connect.
  Fields: `port`, `token`, `version`, `pid`, and `python` — the backend's `sys.executable`,
  read by the shell's CLI-on-PATH machinery (above) so the shim always execs the interpreter
  that actually runs the backend, dev and prod alike. One per-OS mapping: on Windows the
  service runs the backend under `pythonw.exe` (§3 Windows service block — no console
  window), but the published `python` field is the console `python.exe` beside it when one
  exists — the field exists for the CLI and shim, which need console output, and both
  binaries resolve the same interpreter home. This also keeps the shell's shim writes and
  `service install`'s heal agreeing on one exec line.
  The backend binds *and listens on* its socket first and only then publishes `backend.json`
  (uvicorn serves on the already-listening socket) — the file never points clients (token
  included) at a port the backend doesn't own, and a connect attempted the moment the file
  appears is accepted (queued in the listen backlog until uvicorn serves), never refused. A stale/truncated `backend.json` (SIGKILL leftovers) makes the CLI and
  `service status` report it as such — never crash. The same rule covers a well-formed
  `backend.json` whose backend is gone: a CLI request that can't connect (connection refused,
  timeout) exits with the same restart guidance, never a traceback.
  Every backend start binds a fresh port and token, so the renderer re-reads `backend.json`
  (via the preload bridge) before each WebSocket reconnect attempt — a backend restart never
  strands the UI on a dead address.
  The backend also guards its own discovery file: every 10 s it re-publishes `backend.json` if
  the file is missing or unreadable (recreating the §5 directories first) — an externally wiped
  Application Support dir must not strand clients on a healthy backend that launchd `KeepAlive`
  will never restart. A well-formed file holding a different pid is left alone: during a service
  restart it may already be the successor's.
- **One app process** (`requestSingleInstanceLock`): a second launch (a login item racing a
  manual open, `open -n`) quits immediately and focuses the existing window via the
  `second-instance` event — never a second tray icon, never a second §6 `POST /app-started`.
- **In-app updates (decided — electron-updater from v0.6.1):** the app checks for updates
  automatically by default — §4.9
  `automaticUpdateCheck`, default true; PRIVACY.md names the daily check and its off switch.
  Turning the toggle off restores strict manual-only checking (no background or launch
  checks; everything starts from the About page's "Check for updates" button). Downloads and
  installs are always manual, both modes — a check only ever reads the feed. Machinery
  is `electron-updater`'s `MacUpdater` against the same generic provider the Windows and
  Linux legs use — one updater library, one feed format, and one main-process code path on
  every OS. Under the hood `MacUpdater` still drives Squirrel.Mac (Electron's built-in
  `autoUpdater`; its `ShipIt` helper already ships in the bundle): electron-updater
  downloads the update zip itself — sha512-verified against the feed, with real progress
  events — and serves it to Squirrel through its own loopback proxy, which is exactly the
  hand-off the pre-0.6.1 flow hand-rolled (v0.6.0 and earlier downloaded the release DMG,
  rebuilt Squirrel's zip from it with `hdiutil`/`ditto`, and ran their own loopback server;
  all of that is deleted, not kept beside the library). The repo and its releases must stay
  public — the update zip is downloaded unauthenticated.
  - **Automatic check:** `applyShellSettings` reconciles §4.9 `automaticUpdateCheck`
    exactly like `login`/`menuBarIcon` (startup, 60 s poll, renderer push). On the off→on
    transition (which includes a launch with the setting on — the default) it runs the same
    feed fetch as `update-check` immediately, then every 24 h on a timer; on→off clears the
    timer. Nothing is persisted about past checks — each launch with the toggle on checks
    once at startup. Automatic-check failures are silent (no nav row, no toast — the manual
    button still reports errors), and an automatic check never starts a download.
  - **update-available event:** any check — manual `update-check` or automatic — that finds a
    newer version records it in the main process and pushes an `update-available`
    IPC event (the version string) to the main window; an `update-available` invoke handler
    answers the remembered version (or `null`), so a renderer that boots after the check still
    learns it — the §9 store subscribes to the event and asks the handler once at boot,
    keeping `updateAvailable`. It feeds the §9 "Update available" nav row and the §9.4
    pre-armed row.
    A later check answering up-to-date clears the remembered version and pushes `null` (the
    feed rolled back, or the user updated by hand); an `error` check leaves it alone.
    Otherwise it clears only with the app restart that installs the update.
  - **Artifacts:** each release ships two per-arch mac artifacts. The DMG
    (`Autowright-<version>-darwin-<arch>.dmg`) is the install artifact — the website
    download (§17 `docs/downloads.json` keeps naming it), the Homebrew cask, and the
    one-time legacy bridge below all hand out the DMG. The zip
    (`Autowright-<version>-darwin-<arch>.zip`) is the update artifact: Squirrel.Mac
    consumes zips, and electron-updater downloads the zip directly instead of rebuilding
    it from the DMG on the user's machine the way the pre-0.6.1 flow did. `prod.sh` builds
    it right after stapling the app — `ditto -c -k --keepParent` of the same stapled
    bundle that goes into the DMG, so the app inside already carries its notarization
    ticket and the zip needs no submission of its own. `release.sh` uploads both to the
    GitHub release. No `.blockmap` is published for the zip and the updater sets
    `disableDifferentialDownload` on darwin: every mac update is a full download (the
    Windows leg keeps its differential path; for the mac channel the differential attempt
    could only ever fail against a missing blockmap and then fall back anyway).
  - **Feed:** one `latest-mac.yml` per arch under
    `https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-<arch>/`
    (`arm64` | `x86_64`; the files live in the repo-root §17 `release/` — one directory
    per OS — fetched raw from GitHub, not from the Pages site). The §2 darwin module
    serves the *directory* base URL, exactly like the Windows and Linux modules;
    electron-updater's generic provider appends its darwin channel file name
    (`latest-mac.yml`) itself. Same yml shape as the other OS feeds — `version`,
    `files[]` (absolute `github.com/<owner>/<repo>/releases/download/…` URL, base64
    `sha512`, `size`), `path`, `sha512`, `releaseDate` — and like them it names absolute
    release URLs, never paths relative to the feed. Only the zip is listed: the DMG is
    the install artifact and lives in `docs/downloads.json`. Because each arch has its
    own feed directory, a feed never lists both arches — `MacUpdater`'s arm64 file
    filtering is satisfied by the artifact names themselves (`…-darwin-arm64.zip`
    contains `arm64`; `…-darwin-x86_64.zip` does not).
    **Host choice, decided deliberately (applies to all three per-OS feeds).** Serving the
    feeds from `raw.githubusercontent.com` keeps them in the same repo and the same commit as
    the release that produced them: no second deploy step, no Pages build to wait on, and the
    feed can never disagree with `release/` on `main`. The accepted cost is raw's CDN cache -
    it answers with `max-age=300`, so a freshly pushed feed can lag up to ~5 minutes before
    every edge serves it. That is acceptable for both check modes: the automatic check runs
    daily, and the §9.4 manual button is re-clickable, so a user who checks in that window
    just checks again. Nothing in the app tries to defeat the cache beyond its own
    no-store fetch. The switch is a **clean break**: pre-0.6.0 installs fetch the old
    `https://autowright.ai/updates/darwin-<arch>.json` Pages URLs, which no longer exist and
    are deliberately not restored - those builds are orphaned and will never see an update
    offer. No real users had them (only the author and testers ran 0.5.0), so the cost of the
    break is zero and re-hosting a legacy feed forever is not worth it; an orphaned copy is
    replaced by downloading the current DMG by hand. From 0.6.0 on, the feed URL is the raw
    one and moving it again would carry the same one-way cost.
    After publishing the release, `release.sh`
    rewrites the built arch's `latest-mac.yml` — computing the uploaded zip's base64
    sha512 and byte size itself —
    updates the built arch's `{ version, url }` entry in §17 `docs/downloads.json` (the
    site's download index; the DMG URL), and commits + pushes both (plain git commit, not
    `commit.sh`).
  - **Legacy 0.6.0 bridge (one-time, then frozen):** v0.6.0 shipped the previous updater —
    Electron's built-in `autoUpdater` reading a Squirrel.Mac JSON feed at
    `release/darwin-<arch>/feed.json` and rebuilding Squirrel's zip from the release DMG
    on the user's machine. So installed 0.6.0 copies can still reach the new world, the
    v0.6.1 release leg — and only that one (`release.sh` gates on the version) — also
    rewrites `feed.json` one last time, pointing its `updateTo.url` at the v0.6.1 DMG.
    From v0.6.2 on `feed.json` is never rewritten again: it stays in the repo frozen at
    0.6.1 — a 0.6.0 straggler hops 0.6.0 → 0.6.1, and electron-updater carries it from
    there — and the v0.6.1 DMG release asset is permanent (deleting it would orphan every
    remaining 0.6.0 install). No dual-updater code ships: 0.6.1 contains only the
    electron-updater path; the bridge lives entirely in what the release publishes. The
    §15 drift guards pin the frozen feed (internally consistent, a live `.dmg` release
    URL, never past 0.6.1). The decision is logged in §21.4.
  - **Flow** (Electron main; the renderer drives it over IPC, §9.4 renders it; one shared
    electron-updater code path for all three OSes — the §2 module's `UPDATER` marker only
    picks the class, `mac` → `MacUpdater`, and darwin adds the Squirrel hand-off tail
    below; `autoDownload` and `autoInstallOnAppQuit` stay off everywhere, §3 manual-only
    rule):
    `update-check` runs `checkForUpdates()` (with those flags off, purely a feed read)
    and compares the answered version
    against `app.getVersion()` with the §9.4 rule (numeric on dot-split parts, leading `v`
    ignored, malformed = not newer) → `{ state: 'uptodate' | 'available' | 'error', … }` —
    electron-updater's own "is this newer" answer is never consulted, so every platform
    agrees on what counts as an update.
    `update-download` re-checks, then runs `downloadUpdate()`: electron-updater streams
    the zip, verifies its sha512 against the feed, and emits real progress events,
    forwarded to the main window as `update-progress` IPC events (percent, or `null` when
    the download reports no total — the §9.4 bar goes indeterminate). `MacUpdater` then
    starts its loopback proxy and points Squirrel's feed URL at it, but — with
    `autoInstallOnAppQuit` off — never engages Squirrel, so the darwin handler finishes
    the hand-off itself: it subscribes to Electron's `autoUpdater`
    `update-downloaded`/`error` events, calls its `checkForUpdates()` (Squirrel fetches
    the zip from the proxy, verifies the code signature, stages the bundle), and resolves
    `{ ok: true }` once Squirrel staged or `{ error }` on the first `error` event; if
    Squirrel emits neither within 10 minutes the handler settles with a plain-word error
    instead of hanging the §9.4 flow forever. The §9.4 bar holds 100% while Squirrel
    stages. There is no dev fork: `forceDevUpdateConfig` is set unconditionally (a
    packaged app ignores it) so an unpackaged dev launch runs the same real path — the
    provider config always comes from the constructor options, never from a yml — and an
    unsigned dev build surfaces Squirrel's real signature error in the UI.
    electron-updater does read `app-update.yml` beside the app for its cache directory
    name: `prod.sh` writes one into `Contents/Resources/` before signing (provider, url,
    `updaterCacheDirName: autowright-updater` — cache at
    `~/Library/Caches/autowright-updater`, the §3 cask's `zap` list covers it), and the
    checked-in `app/dev-app-update.yml` serves the same role for unpackaged dev launches.
    `update-install` asks the backend for live executions
    (`GET /executions?status=executing`) and answers `{ busy: true }` while any is running —
    swapping the bundle mid-execution risks a step lazily importing mixed versions; an
    unreachable backend counts as idle. Otherwise it calls electron-updater's
    `quitAndInstall()` — Squirrel already staged during download, so this quits straight
    into the swap.
    ShipIt swaps the bundle at the same path, so the LaunchAgent's absolute interpreter path
    stays valid. On a platform whose §2 module serves no update feed URL, **every** update
    path answers the same plain "Updates are not supported on this platform yet." line up
    front: `update-check` answers `state: 'error'` carrying that line as its error detail —
    the §9.4 page renders the carried detail, never the generic "Couldn't reach
    autowright.ai" network copy, so the user is never told to retry something that cannot
    succeed — and `update-download` / `update-install` refuse with the same line (defense
    in depth — the §9.4 UI never offers them without a feed, and `quitAndInstall` with
    nothing staged must never quit the app for no swap).
  - **Homebrew-managed detection:** the install counts as brew-managed when the Caskroom
    metadata directory exists: the main process probes `/opt/homebrew/Caskroom/autowright`
    and `/usr/local/Caskroom/autowright` with `fs.existsSync`, fresh on every query and
    never cached, so switching channels (brew install or uninstall while the app runs)
    needs no restart. The `AUTOWRIGHT_CASKROOM` environment variable replaces the probe
    list with its single path (test/dev escape hatch, same pattern as `AUTOWRIGHT_HOME` /
    `AUTOWRIGHT_SHIM`). An `update-brew-managed` invoke handler answers the boolean to the
    renderer. Checks (manual and automatic) behave identically in both modes; when
    brew-managed, `update-download` and `update-install` refuse immediately with
    `{ error: 'This copy is managed by Homebrew.' }` (defense in depth; the §9.4 UI never
    offers those actions in brew mode and instead shows the `brew upgrade` command).
  - **Backend handoff:** the swap leaves the old backend process running; the next app
    launch's version-compare flow (next bullet) restarts it onto the new bundle.
- **Launch-time backend version compare (implemented, in ensure-backend):** when the probe
  finds a healthy backend, the main process compares `/health`'s `version` with its own
  `app.getVersion()` (one §17 version source, so app and bundled backend versions agree by
  construction). On mismatch it waits for live executions to drain
  (`GET /executions?status=executing`, polled every 30 s — the service is never restarted
  mid-execution), then runs the same `service install` path, which rewrites the plist and
  restarts the service on the current bundle's interpreter. Outcome lines append to `app.log`.
- Sleep: the service manager does not prevent sleep. Two idle-sleep assertions exist, both
  owned by the §2 platform layer's `PowerAssertion` (engine and settings code call the layer —
  `hold_execution()` / `reconcile(enabled)` — never an OS mechanism directly; a platform with
  no implementation composes a no-op and `keepAwake: false`):
  - **Per-execution:** held for the duration of an active execution (prevents idle sleep
    mid-execution; forced sleep — lid close, low battery — can still suspend an execution).
    `hold_execution()` returns a release callable the engine calls when the execution
    finishes; acquiring and releasing never raise. Outside executions, normal OS energy
    settings apply and missed occurrences follow the §6 missed-execution policy.
  - **Permanent (§4.9 `keepAwake`, on by default):** for the always-on use case (a machine
    left running to catch schedules and §6 message triggers), the setting makes the backend
    hold a permanent idle-sleep assertion — started at backend boot when the setting is on
    and started/stopped live from `PATCH /settings` (`reconcile`, idempotent) — no restart.
    Display sleep stays allowed; user-forced sleep still wins. This covers sleep only —
    logging out of the OS session still stops the service (and on macOS locks the Keychain,
    headless bullets below); for an unattended machine, stay logged in (screen lock is fine)
    or enable auto-login.
  - **Sleep disclaimer:** because the assertions stop idle sleep only, the app says so
    wherever it promises background firing: the app works best on an always-on desktop
    (a Mac mini or Mac Studio), and a MacBook that is asleep would not trigger the
    automation (on wake the §6 missed-executions rule fires one catch-up). The sentence appears on the
    §4.9 keepAwake row (per-OS via the §9 `sleepNote` entry), under the §9.2 trigger
    editor's Cron and One time forms (as the tail of the "Catch up if missed" note, per-OS
    via the §9 `sleepMissNote` entry: "This is not an issue on Mac mini or Mac Studio, but
    a MacBook that is asleep will not fire on schedule."), in
    the §20 `settings set` help's `keepAwake` line, in the README's scheduling feature bullet, and in the §17
    website FAQ's "What does Autowright need to run?" answer (HTML and JSON-LD mirror
    alike). No surface claims the Mac will wake itself for a schedule.

  Per-OS mechanisms. **macOS** (`awake.py`, delegated to by `platform/darwin.py`): each
  assertion is its own `caffeinate -i -w <backend pid>` subprocess — the `-w` ties it to the
  backend process, so a crashed backend can never leave an orphan keeping the Mac awake.
  **Windows** (`WindowsPower` in `platform/windows.py`): one thread-owned
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` behind a counted set of
  holds — the permanent keepAwake hold plus one per active execution; the state is set while
  the count is positive and cleared (`ES_CONTINUOUS` alone) when it reaches zero, always from
  the single dedicated thread that owns it (execution state is per-thread). Never
  `ES_DISPLAY_REQUIRED` — display sleep stays allowed. Windows clears a thread's execution
  state when its process dies, so a crashed backend can never leave an orphan — the same
  guarantee `-w` gives on macOS.
  **Linux** (`SystemdInhibitPower` in `platform/linux.py`): each assertion is its own
  `systemd-inhibit --what=idle --who=Autowright` subprocess wrapping
  `tail --pid=<backend pid> -f /dev/null`, so the inhibitor lock is released the moment
  the backend dies — the same no-orphan guarantee `caffeinate -w` gives on macOS.
  `--what=idle` only: display sleep stays allowed. `capabilities.keepAwake` answers the
  probed presence of `systemd-inhibit` (true on systemd hosts, false elsewhere).

- **Homebrew cask (decided):** a second install channel beside the DMG download, distributed
  from the project's own tap `hansololz/homebrew-tap` (repository `homebrew-tap`, cask file
  `Casks/autowright.rb`, token `autowright`) — `brew install --cask hansololz/tap/autowright`.
  Homebrew 6 refuses to load a cask from a third-party tap until the user trusts it, so the
  tap's README documents `brew tap hansololz/tap` + `brew trust --tap hansololz/tap` before the
  install; after the tap is added the bare token `autowright` resolves.
  Not submitted to `Homebrew/homebrew-cask`: that requires notability (75 stars / 30 forks /
  30 watchers) the project does not have, and the only thing it would buy is dropping the tap
  prefix. The cask installs the same signed + notarized DMG from the GitHub release, so
  Gatekeeper needs no override and there is no second artifact to build or verify. Its shape
  follows from this section: `depends_on arch: :arm64` and `depends_on macos: :monterey` (the
  bundle's `LSMinimumSystemVersion`); no `auto_updates` stanza: Homebrew is the update manager
  for this channel. The in-app updater never installs onto a brew-managed copy (the
  Homebrew-managed detection bullet above), so Homebrew's recorded version stays accurate and
  a plain `brew upgrade` upgrades the cask, no `--greedy` needed. One-time caveat: a copy that
  brew-installed under the old `auto_updates true` cask and then updated in-app carries a
  stale brew record; its first `brew upgrade` after this change reinstalls the latest DMG,
  harmlessly. `uninstall launchctl:` before `quit:`, since
  launchd would otherwise keep restarting a backend whose bundled interpreter was just removed;
  and `zap trash:` covering the §5 data directory, the logs directory, the LaunchAgent plist
  and preferences/saved-state, the `~/.local/bin/autowright` shim, and the
  `~/Library/Caches/autowright-updater` electron-updater cache (§3 Flow) — never the
  Keychain secrets (§4.8), which the user removes by hand.
  A `livecheck` block reads `version` from the same `latest-mac.yml` the in-app updater
  reads (`:yaml` strategy) - the cask is arm64-only, so always
  `https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-arm64/latest-mac.yml`,
  the raw URL from the Feed bullet above. Never the retired `autowright.ai/updates/…`
  Pages path, and never the legacy `feed.json` - that file is frozen at 0.6.1 (§3 bridge)
  and would silently stop livecheck reporting anything newer - so the cask stays
  checkable by `brew livecheck` and would be autobump-eligible
  if it ever moved to core. Version bumps are `release-start.sh`'s job (§18), never a manual
edit,
  and the same publish step re-pins the livecheck URL on every release: it is the one line
  naming the feed host, and a stale one fails silently (`brew livecheck` simply stops
  reporting a version).

**Windows packaging & updates (decided — NSIS + electron-updater).** The Windows
distributable is built by **electron-builder for the Windows target only** (`--win nsis`),
driven by a new `windows-scripts/prod.ps1`; macOS keeps `@electron/packager` + `prod.sh`
(which emit the mac update zip and `app-update.yml` themselves — §3 mac update bullets —
rather than moving the mac build onto electron-builder).
Rationale: hand-rolling what electron-updater consumes (the NSIS script, `latest.yml`, the
blockmap, the in-app `app-update.yml`) would re-implement electron-builder badly, and
electron-builder also provides the sign-later hook for free. Squirrel.Windows was considered
and dropped (dormant project; the name-sharing Squirrel.Mac stays on macOS unchanged).

- **`prod.ps1` order mirrors `prod.sh`:** sync `VERSION`, vite build, download
  python-build-standalone `x86_64-pc-windows-msvc-install_only` (flat layout:
  `python\python.exe`), pip install the backend into it with `-c constraints.txt` before
  packaging, ship the interpreter via `extraResources` into `resources\python`, then
  electron-builder produces the installer. The bundled-interpreter smoke test runs before
  packaging with `PYTHONDONTWRITEBYTECODE=1` (same principle as the macOS seal rule).
- **Installer shape:** per-user NSIS (`perMachine: false` — no admin prompt, the same
  no-privilege principle as the rest of §3), install dir under `%LOCALAPPDATA%\Programs`,
  appId `ai.autowright.app`, and a **stable NSIS GUID pinned in config from day one** — an
  upgrade must always find the previous install, and the stable GUID + path + appId are
  what let a future signed build upgrade an unsigned install in place. Needs `icon.ico`
  generated beside the existing icns/png (§14 assets, `scripts/gen_icon.cjs`).
  - **Already installed at this version → open the app (decided).** After a first
    install, Windows Search and the Start menu's "Recommended" list keep surfacing the
    installer exe itself, so a user who double-clicks it again expects Autowright to
    open — not a second full install with its progress window. `app/electron/installer.nsh`
    (electron-builder `nsis.include`) adds a `preInit` hook, the first thing `.onInit`
    runs: an interactive run (not `/S`, not electron-updater's `--updated`) that finds
    this installer's own `${VERSION}` already recorded as `DisplayVersion` under the
    per-user uninstall key and the app exe at the `InstallLocation` recorded under
    `HKCU\Software\<GUID>` launches that exe as the user and quits. Because it runs
    before the running-app check, an app that is already open is brought forward by the
    §9 single-instance lock instead of being killed and re-extracted. Every other case —
    a different or missing installed version, a missing exe, silent runs, updater runs —
    installs exactly as before; a same-version repair is uninstall-then-install. The
    hook is NSIS-only and unreachable from tests: the §15 packaging-config guard pins the
    include path and the hook's shape, and the behavior is verified on a Windows host by
    building with `prod.ps1` and running the installer twice (second run: no progress
    window, the app opens; the `spec/ports.md` Windows worksheet).
- **Artifact name (decided 2026-09-07, from v0.11.0): the bare `Autowright.exe`.** The
  top-level `artifactName` is `Autowright.${ext}`, so every Windows release ships
  `Autowright.exe` plus `Autowright.exe.blockmap`, and the version lives only in the
  release-tag path of the download URL
  (`github.com/…/releases/download/v<version>/Autowright.exe`). Same-named assets never
  collide because each GitHub release is its own asset namespace. Releases through
  v0.10.2 shipped `Autowright-<version>-win32-x86_64.exe`; those assets stay under their
  old names, and nothing rewrites their feeds. Consequences, all accepted:
  - Nothing installed changes: the installed binary (`productName`), the Start-menu
    shortcut (`shortcutName`), and the Apps & features entry (GUID) never carried the
    artifact name, so an in-app update across the rename is an ordinary update.
  - electron-updater derives the *previous* release's blockmap URL by substituting the
    old version for the new one in the new installer's URL, so the first update across
    the rename (0.10.x → 0.11.0) looks for `v0.10.x/Autowright.exe.blockmap`, misses, logs
    "Cannot download differentially, fallback to full download", and fetches the whole
    installer once. From 0.11.0 onward the substituted URL resolves again (the tag path
    carries the version) and updates are differential as before.
  - The installer in a user's Downloads folder now shares the installed app's name, and
    Windows Search surfaces both; the "already installed → open the app" hook above is
    what keeps a stray double-click harmless. Repeated downloads land as
    `Autowright (1).exe` with no version in the name — the version is in the tag path
    of the URL the §17 download button and the feed hand out, and in Apps & features.
  - `prod.ps1` and `release.ps1` look for the bare name in `build\win`; the §15 drift
    guards check the version of a Windows feed URL on its tag path only (the mac
    artifact names keep the version and are still checked on the file name too; the
    Linux AppImage follows the Windows model from its next release — the Linux
    Artifact bullet below).
- **Updater:** `electron-updater` (NsisUpdater on win32; since v0.6.1 every OS runs
  electron-updater — darwin's `MacUpdater` flow is specified in the §3 mac update bullets
  above). main.cjs's update block is one shared code path; the §2 platform layer's
  `UPDATER` marker picks the class. win32 uses the generic
  provider pointed at
  `https://raw.githubusercontent.com/hansololz/autowright/main/release/win32-x86_64/`
  (`win32.cjs` `updateFeedUrl` returns that base once the feed is live). Feed =
  `latest.yml` + installer + blockmap: the yml is rewritten under `release/win32-x86_64/`
  by the release script; binaries ride the GitHub release — the same hosting split as the
  mac feed. The
  renderer-facing IPC surface (`update-check`/`update-download`/`update-install` states and
  progress events) stays byte-identical so no renderer code forks. The §3 manual-install
  rule carries over: a check only ever reads the feed — `checkForUpdates` runs with
  autoDownload off and `autoInstallOnAppQuit` off (installs happen only through the
  explicit `update-install` flow with its live-execution gate, never as a quit side
  effect); downloads and installs stay user-initiated.
- **Signing (cert later, pipeline ready now — decided):** Windows builds may ship unsigned
  for the moment (unlike the mac no-ad-hoc rule); SmartScreen warnings are accepted until a
  certificate exists. The build signs whenever cert config is present (electron-builder
  `CSC_LINK`/`CSC_KEY_PASSWORD`, or a signtool thumbprint) and otherwise builds unsigned
  while printing a loud UNSIGNED warning line — never silently. GUID, install path, and
  appId stay stable so the unsigned→signed transition is a normal update. When the cert
  arrives: sign the app exe and the installer, set `publisherName` in the updater config
  (electron-updater then verifies the downloaded installer's Authenticode identity), and
  flip this rule to always-signed. **Never set `publisherName` before signing starts** — it
  would make updates fail against unsigned artifacts. Known cosmetics until the cert lands:
  the uninstall registry entry carries empty Publisher/InstallLocation values, and the exe's
  `CompanyName` version resource reads electron-builder's default ("GitHub, Inc.") because
  `app/package.json` sets no `author` field — adding one would fix it but touches the shared
  mac pipeline, so it is deliberately deferred to the signing change.
- **Updater cache residue (known, accepted):** the NSIS install caches a byte copy of the
  installer at `%LOCALAPPDATA%\autowright-updater\` (electron-updater's
  `updaterCacheDirName` — the baseline its differential downloads diff against), and the
  uninstaller does not remove it (~150 MB). Accepted as electron-updater's standard
  behavior; a user who wants it gone deletes the directory by hand. No Autowright code may
  depend on it existing.
- **Release:** Windows artifacts get their own `windows-scripts/release.ps1` (build via `prod.ps1`,
  publish installer + blockmap to the same GitHub release as the mac artifacts, rewrite
  `release/win32-x86_64/latest.yml` and the `win32-x86_64` entry in
  `docs/downloads.json`). It is append-only: it requires the GitHub release
  `v<VERSION>` to exist already (prepared by `release-start.sh` and cut by `release.sh`,
  both bash/BSD-sed on macOS), a clean working tree, and the checkout on `main` (the feed
  it pushes is served from the `/main/` raw URL, §3), and never creates a release, tag,
  or version bump. A release that ships every platform is one preparation plus one
  script run per OS against one tag/version.
  The `docs/index.html` download CTA (§17) offers the Windows installer as a second,
  ghost-style button that reads the `win32-x86_64` entry of `docs/downloads.json`.

**Linux packaging & updates (decided — AppImage + electron-builder + electron-updater).**
The Linux distributable is a
single AppImage built by **electron-builder for the Linux target only** (`--linux appimage
--x64`; x86-64 is the only Linux arch that ships), driven by `linux-scripts/prod.sh`;
macOS keeps `@electron/packager` + `prod.sh` and Windows keeps
`windows-scripts/prod.ps1`, both untouched. AppImage needs no distro packaging review,
runs on any current distro, and pairs with electron-updater's AppImage support (the
Updater bullet below — the Windows model with the AppImage machinery swapped in).
deb/rpm/flatpak are deferred; if one is
added later it is a distro-managed channel like the Homebrew cask (the in-app updater
refuses, the package manager owns updates).

- **`linux-scripts/prod.sh` order mirrors `prod.sh`:** version gate (the same three-site check
  `release.sh --check` performs, reimplemented in the script the way `prod.ps1` does —
  `release.sh` itself stays bash/BSD-sed and runs on macOS), `npm ci` + typechecked vite
  build, download python-build-standalone `x86_64-unknown-linux-gnu-install_only` (the
  same pinned release tag and CPython version as `prod.sh`; the layout matches macOS —
  `python/bin/python3`, resolved by the §2 `bundledPythonPath`), stage to `build/python`,
  pip install the backend with `-c constraints.txt`, bundled-interpreter smoke test with
  `PYTHONDONTWRITEBYTECODE=1` **before** packaging (same principle as the macOS seal rule:
  the artifact ships exactly what pip staged), then electron-builder
  `--linux appimage --x64` with the shared `extraResources` staging (`build/python` →
  `resources/python`) and the output directory overridden to `build/linux/` on the command
  line (`-c.directories.output` — the checked-in config's output points at `build/win`).
  Beside the AppImage the build emits `latest-linux.yml` (the electron-updater feed,
  from the `linux.publish` config below; `--publish never` keeps uploading with
  `linux-scripts/release.sh`), and the script verifies both exist. Unlike NSIS there is
  no separate `.blockmap` artifact: for the AppImage target electron-builder **embeds**
  the block map in the AppImage itself and records its size as the yml's `blockMapSize`.
- **Artifact (decided 2026-09-07, from the first Linux release after v0.10.0): the bare
  `Autowright.AppImage`.** The top-level `artifactName` (`Autowright.${ext}`, the Windows
  Artifact-name bullet) applies unchanged — there is no `linux.artifactName` override —
  so the version rides only in the release-tag path of the download URL
  (`github.com/…/releases/download/v<version>/Autowright.AppImage`); each GitHub release
  is its own asset namespace, so same-named assets never collide. Releases through
  v0.10.0 shipped `Autowright-<version>-linux-x86_64.AppImage`; those assets stay under
  their old names, and nothing rewrites their feeds. Beyond the Windows reasons, the
  bare name is load-bearing on Linux: electron-updater's `AppImageUpdater` overwrites
  the running AppImage *in place* only when its file name carries no `x.y.z` version.
  With a versioned name it moves the new build in beside the old one under the new
  version's name (`Autowright-0.11.0-…` → `Autowright-0.12.0-…`), changing on every
  update the path the Desktop-integration and §4.9 autostart entries pin in
  `Exec`/`TryExec`. The bare name keeps that path stable across updates, so those
  entries never go stale, and the Updater bullet's "swaps the AppImage file at its own
  path" holds literally. Consequences, all accepted:
  - A repeated download lands as `Autowright (1).AppImage` — still version-free, so
    still overwritten in place; a copy the user renames to any version-free name is
    preserved the same way (electron-updater's own rule).
  - An existing versioned install (0.10.0) is moved to `Autowright.AppImage` in its own
    directory by its first update across the rename — once — and the relaunch that
    follows rewrites the launcher entry (the reconcile rule below); the path is stable
    from then on.
  - Differential download is unaffected, unlike the Windows rename: `AppImageUpdater`
    reads the old block map from the local file and the new one from the remote file's
    tail (the Updater bullet), never deriving a previous-version URL, so there is no
    one-time full download.
  - `prod.sh` and `release.sh` look for the bare name in `build/linux`; the §15 drift
    guards check the version of a Linux feed URL on its tag path only, as for Windows
    (the mac artifact names keep the version and are still checked on the file name).

  The `build.linux` config
  pins the AppImage/x64 target, the checked-in `electron/icon/icon.png`, category
  `Utility`, and its own `publish` generic-provider entry — the Linux feed base URL,
  overriding the top-level win32 entry, so the `app-update.yml` electron-builder embeds
  in the AppImage points at the Linux feed and never the Windows one.
- **Desktop integration (decided — the app installs its own launcher entry):** an AppImage
  is a bare file; the desktop knows nothing about it until something registers it. The
  AppImage *embeds* everything a registrar needs — electron-builder's desktop entry plus
  the 1024 px mark as `.DirIcon` under `usr/share/icons/hicolor/` — so third-party
  integrators (Gear Lever, AppImageLauncher, `appimaged`) show the mark on the file and
  register it themselves. Nothing in the build can make a stock file manager show the
  embedded icon on the `.AppImage` file itself (GNOME never looks inside), and a running
  window gets the desktop's generic icon until a `.desktop` file the desktop can *see*
  names the window's app-id. So the app registers itself: on every packaged Linux launch
  `main.cjs` calls the §2 `applyDesktopEntry(app, iconPath)` seam (behind the
  `desktopEntry` shell capability — true only in `linux.cjs`), which reconciles two
  files under `$XDG_DATA_HOME` (default `~/.local/share`):
  `applications/ai.autowright.app.desktop` and
  `icons/hicolor/scalable/apps/ai.autowright.app.svg` (the §14 `icon.svg` source, copied
  byte-for-byte from the package — `scalable/` is the one hicolor directory every theme
  index lists, and the SVG *is* the full mark, rounded plate included). The entry is
  `Type=Application`, `Name=Autowright`, `Comment=` the §3 electron-builder synopsis,
  `Exec="<AppImage path>"` (the `$APPIMAGE` path the AppImage runtime exports, falling
  back to `process.execPath` for an unpacked build; desktop-entry Exec quoting — `\`, `"`
  escaped, `%` doubled), `TryExec=<the same path, unquoted>` (a deleted or moved AppImage
  hides the entry from the launcher on its own — nothing stale ever shows),
  `Icon=ai.autowright.app`, `StartupWMClass=ai.autowright.app`, `Categories=Utility;`,
  `Terminal=false`, and the ownership marker `X-Autowright-Desktop-Entry=true`.
  **Window association:** Electron derives the Wayland app-id and the X11 `WM_CLASS`
  from `desktopName` in `app/package.json` (set to `ai.autowright.app.desktop`; without
  it Electron would slug the product name to `autowright`), so the running window's
  app-id equals the installed entry's basename — the match every desktop makes without
  needing `StartupWMClass`, which is set anyway for the ones that index it. The same
  `desktopName` feeds electron-builder (`linux.syncDesktopName: true`): the entry
  *inside* the AppImage is named `ai.autowright.app.desktop` with
  `StartupWMClass=ai.autowright.app` too, so a third-party integrator's copy associates
  windows exactly like ours. **Reconcile rules** (the §4.9 autostart file's, minus the
  toggle): an unpackaged run never writes (the Exec line would name the bare Electron
  binary); a packaged run writes each file only when its bytes differ from the wanted
  content (a moved AppImage rewrites Exec/TryExec on the next launch; an in-place §3
  update keeps its path, so nothing changes), writes the icon before the entry (the
  desktop's entry-changed event then finds the icon already there), and never touches an
  entry without the marker — a user's own hand-written launcher wins. The app never
  deletes either file: there is no setting behind them, `TryExec` handles a removed
  AppImage, and §3 reset leaves them alone the same way it leaves the AppImage itself
  (reset erases *data*; the launcher entry is part of the installed app). Failures are
  best-effort — logged never, swallowed always, retried on the next launch — exactly
  like the autostart reconcile. Result for a user: download → the file looks generic →
  run it once → from then on Autowright has its icon in the dock, Alt-Tab and the app
  grid, and launches from the grid like an installed app.
- **Updater:** `electron-updater` again, in its AppImage flavor — `linux.cjs` names
  `UPDATER: 'appimage'` and main.cjs's shared electron-updater path (the win32 Updater
  bullet) constructs `AppImageUpdater` instead of `NsisUpdater` against the same generic
  provider, pointed at
  `https://raw.githubusercontent.com/hansololz/autowright/main/release/linux-x86_64/`
  (`linux.cjs` `updateFeedUrl`; x86-64 is the only Linux arch, so no arch switch). Feed =
  `latest-linux.yml` + AppImage: the yml is rewritten under
  `release/linux-x86_64/` by `linux-scripts/release.sh`; binaries ride the GitHub
  release — the same hosting split as the mac and Windows feeds. Differential download
  needs no extra asset: the block map is embedded in the AppImage (the prod.sh bullet),
  and `AppImageUpdater` reads it with an HTTP Range request for the file's last
  `blockMapSize` bytes — which GitHub release downloads serve. Everything else carries
  over from the Windows bullet unchanged: the renderer-facing IPC surface
  (`update-check`/`update-download`/`update-install` states and progress events) is
  byte-identical, checks run with autoDownload and `autoInstallOnAppQuit` off, and
  installs happen only through the busy-gated explicit `update-install` flow
  (`quitAndInstall` swaps the AppImage file at its own path and relaunches; the old
  backend keeps running until the next launch's version-compare flow restarts it, as on
  the other platforms). Because each OS's feed is rewritten only when that OS's artifact
  is uploaded, a release cut without the Linux leg leaves the Linux feed at the newest
  version that actually has an AppImage — an instance never sees a phantom update or a
  download URL that 404s.
- **Unsigned, by design:** Linux has no Gatekeeper or SmartScreen equivalent — the
  AppImage ships unsigned and no signing/notarization leg exists (the Windows sign-later
  principle, without the later).
- **Release:** `linux-scripts/release.sh` publishes the Linux half of a release - the
  append-only model shared with `release.ps1`, not the mac one: it never creates a
  release, tag, or version bump. It requires the GitHub release `v<version>` to exist
  already (prepared by `release-start.sh` and cut from macOS by `release.sh`), a clean
  working tree, and the checkout on `main` (the feed it pushes is served from the
  `/main/` raw URL, §3), runs the full test suite in the §15
  shift-left order (any failure aborts before anything is built or uploaded), builds via
  `linux-scripts/prod.sh`, uploads the AppImage to that release with
  `--clobber` (idempotent — a re-run replaces the asset; the block map rides embedded
  inside it, so there is no second binary to upload), and then rewrites
  `release/linux-x86_64/latest-linux.yml` from the build output — the bare artifact
  file name replaced with the released binary's
  `github.com/<owner>/<repo>/releases/download/…` URL, exactly the `release.ps1`
  rewrite — plus the `linux-x86_64` entry in `docs/downloads.json`, and commits + pushes
  just those files. The feed is written only after the
  upload succeeds, so it never names a URL that is not live. A release that ships all
  three platforms is one preparation plus three script runs against one tag/version.

**Headless mode (decided).** The backend and CLI must work with no GUI ever launched — the §20
CLI is enabled, so the full surface below is live:

- **API parity** — every operation the UI performs goes through the backend API; the UI holds no
  private logic. The CLI is a second client of the same API and can reach full coverage without
  backend changes.
- **Bootstrap** — `autowright service install` registers the backend by writing a launchd plist to
  `~/Library/LaunchAgents/` directly (the very same code the app's ensure-backend step runs at
  launch — one registration path; install rewrites and adopts an existing registration).
  `service uninstall`, `service status`, `service restart`, and `service stop` accompany it.
  `stop` unloads the job **and sweeps stray Autowright processes** (the quit-entirely bullet
  above: `kill_matching` over `paths.sweep_markers()`, run after the unload so nothing
  KeepAlive-respawns) but leaves the plist and shim on disk ("stopped until next login or
  app launch" — the §4.9 quit-entirely action's backend half). The sweep is also the
  escalation for a backend that outlived the unload wait: its command line carries
  `-m autowright.main`, so killing it lets launchd finish the pending removal, and after a
  sweep that ended anything the stop re-polls registration (up to 5 s) before judging.
  It reports failure (exit 1) when launchd still lists the job afterwards. With no plist:
  a sweep that ended processes answers `stopped — service was not installed; ended N
  lingering process(es)` (exit 0 — this is how quit-all succeeds against a directly-spawned
  dev backend), and a sweep that found nothing answers `already stopped — service not
  installed, nothing was running` (exit 0). Stop is **idempotent**: its goal is "no backend
  running", and an absent registration with nothing alive already satisfies it. This is
  also what lets the §4.9 QUIT and RESET flows proceed on a machine whose registration is
  missing (a failed ensure-backend, a headless `service uninstall`) instead of aborting on
  a stop "failure" that had nothing to do. `install` and `restart` never sweep — they run during version-sync while
  executions may legitimately be live. `status`
  distinguishes the states: job unloaded but plist present → "stopped (plist present)", exit 0
  (stopped on purpose is not a failure); no plist → "not installed", exit 1.
  Two launchd realities the install/restart path must handle: (a) booting out a *running* job is
  asynchronous — an immediate re-bootstrap races the teardown and fails, so after `bootout` the
  code polls `launchctl print` until the job is gone (up to 10 s) before loading; (b) legacy
  `launchctl load` (the non-Aqua fallback) can exit 0 without loading anything, so after any load
  the code verifies the job actually exists in launchd and reports failure otherwise — install
  must never claim success for an unregistered service; (c) a wedged `launchctl` must never hang
  the caller (the app's ensure-backend step waits on it), so every `launchctl` invocation carries
  a 30-second timeout and a timed-out call reports the same plain-word failure as a non-zero
  exit ("launchctl timed out"), never a traceback.
- **Secret-store constraint** — secrets live in the OS user secret store, which is locked
  until the user session unlocks: the login Keychain on macOS, the freedesktop Secret
  Service keyring (GNOME Keyring / KWallet, reached through the same `keyring` code) on
  Linux. Headless operation requires a logged-in (auto-login acceptable) session with the
  store unlocked; pure SSH-only operation without one cannot read secrets. Documented, not
  worked around.

