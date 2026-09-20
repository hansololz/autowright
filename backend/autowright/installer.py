"""Real harness installers and sign-in help (§19).

Each vendor's own suggested install method — never sudo, never Homebrew.
§19 install-location principle: everything lands where a manual install
would put it (CLIs in standard user bin dirs; Ollama is the official Mac
app plus the vendor's /usr/local/bin symlink when writable, else
~/.local/bin — on Linux the official standalone bundle extracted into
~/.local), never in an app-private directory, and stays reachable
from the user's terminal. One background install per provider at a time;
progress streams through a publish callback (the API layer forwards it as
`harness.install` WS events) and the latest snapshot is kept for
`GET /agents/install/{id}` so a remounted UI can reattach.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request

from . import harness, paths, platform
from .platform.base import run_bounded

LOCAL_BIN = os.path.expanduser("~/.local/bin")
# §19 install-location principle: the vendor script's own symlink target —
# used when writable without sudo, so the CLI lands exactly where a manual
# install would put it.
USR_LOCAL_BIN = "/usr/local/bin"
PATH_MARKER = "# Added by Autowright — command-line tools it installs live here"

# Wall-clock cap per install phase: a black-holing server or a byte-trickling
# download would otherwise keep the job "running" forever — and the running
# guard in start() would block every retry until a backend restart.
INSTALL_TIMEOUT_S = 15 * 60

CLAUDE_INSTALLER = "https://claude.ai/install.sh"
OPENCODE_INSTALLER = "https://opencode.ai/install"
CODEX_INSTALLER = "https://chatgpt.com/codex/install.sh"
# The official Mac app archive — the same payload ollama.com's install.sh ships.
OLLAMA_APP_ZIP = "https://ollama.com/download/Ollama-darwin.zip"
# The official Linux standalone bundle (bin/ollama + lib/ollama). The vendor's
# install.sh extracts this same archive — but under sudo into /usr; §3 has no
# sudo anywhere, so the Linux arm extracts it into ~/.local instead (the same
# user-owned layout, §19 install-location principle). zstd-compressed —
# tarfile reads it transparently on the pinned CPython 3.14 (PEP 784).
OLLAMA_LINUX_TAR = "https://ollama.com/download/ollama-linux-amd64.tar.zst"
APPLICATIONS = "/Applications"

_lock = threading.Lock()
_jobs: dict[str, dict] = {}  # provider id → §19 install snapshot
# §19: provider id → generation of the phase thread still running for it. An
# abandoned phase (below) keeps doing filesystem work after the give-up, and a
# retry landing on top of it would race its staged move of the same bundle —
# so `start` refuses while an entry stands. Cleared by the phase itself.
_phases: dict[str, int] = {}
BUSY_RUNNING = "an install for this provider is already running"
BUSY_ABANDONED = "the previous install is still finishing — try again in a minute"
# §19: every job carries a generation token. An abandoned phase (below) keeps
# running until the process exits, and without the token its late progress
# would write into — and stream out of — a LATER install's snapshot.
_generation = 0


def status(provider_id: str) -> dict:
    with _lock:
        snap = _jobs.get(provider_id)
        # The generation token is internal bookkeeping, never part of the §19
        # `GET /agents/install/{id}` shape.
        return {k: v for k, v in snap.items() if k != "_gen"} if snap \
            else {"state": "idle"}


def busy_detail(provider_id: str) -> str:
    """§19: why a `start` refusal happened, in the words the 409 carries — an
    abandoned phase still finishing reads differently from a live install."""
    with _lock:
        running = _jobs.get(provider_id, {}).get("state") == "running"
        return BUSY_RUNNING if running else BUSY_ABANDONED


def start(provider_id: str, publish) -> bool:
    """Kick off a background install. False if one is already running — or
    while an abandoned phase's worker thread is still finishing its filesystem
    work (§19); `busy_detail` says which."""
    global _generation
    with _lock:
        if _jobs.get(provider_id, {}).get("state") == "running":
            return False
        if provider_id in _phases:
            # §19: the give-up marked the job failed, but the phase thread is
            # still moving files — a retry now would race its staged move.
            return False
        _generation += 1
        gen = _generation
        _jobs[provider_id] = {"state": "running", "line": "", "percent": None,
                              "_gen": gen}
        _phases[provider_id] = gen

    def emit(line: str | None = None, percent: int | None = None) -> None:
        with _lock:
            snap = _jobs.get(provider_id)
            if snap is None or snap.get("_gen") != gen:
                # §19: the abandoned phase's late progress is discarded — it
                # neither writes the snapshot nor publishes an event.
                return
            if line is not None:
                snap["line"] = line
            if percent is not None:
                snap["percent"] = percent
        publish(line=line, percent=percent, done=False)

    def run() -> None:
        # Wall-clock cap on the whole install, not just its phases: a phase
        # that never returns at all (a wedged child the group kill couldn't
        # reach, a hung filesystem call) would leave the job "running"
        # forever, and the guard above would refuse every retry until a
        # backend restart. The installer runs on an inner daemon thread joined
        # with the cap; one still alive past it is ABANDONED — a Python thread
        # can't be killed, so it keeps running until the process exits and its
        # (late) result is ignored.
        raised: list[BaseException] = []

        def phase() -> None:
            try:
                _INSTALLERS[provider_id](emit)
            except Exception as e:  # noqa: BLE001 — becomes the §10 failure card
                raised.append(e)
            finally:
                # §19: the phase is only over when the installer call actually
                # returns — until then `start` refuses. Generation-checked: a
                # later install owns the entry, and this thread's late finish
                # must not clear its claim.
                with _lock:
                    if _phases.get(provider_id) == gen:
                        del _phases[provider_id]

        worker = threading.Thread(target=phase, daemon=True)
        worker.start()
        worker.join(INSTALL_TIMEOUT_S)
        error: BaseException | None = raised[0] if raised else None
        if worker.is_alive():
            error = RuntimeError(f"install timed out after {INSTALL_TIMEOUT_S // 60} minutes")
        if error is not None:
            msg = (str(error).strip().splitlines() or ["install failed"])[0][:300]
            with _lock:
                _jobs[provider_id] = {"state": "failed", "error": msg,
                                      "_gen": gen}
            publish(done=True, ok=False, error=msg)
            return
        with _lock:
            _jobs[provider_id] = {"state": "done", "_gen": gen}
        publish(done=True, ok=True)

    threading.Thread(target=run, daemon=True).start()
    return True


def login(provider_id: str) -> str:
    """Start sign-in help; returns the §19 method (`browser` | `terminal`).

    Codex's `login` completes on its own OAuth browser callback, so it runs
    detached. The other CLIs sign in through interactive TUIs — those open in
    Terminal.app, and the UI polls `GET /agents/signin/{id}` until done.
    """
    if provider_id == "ollama":
        # No account, nothing to sign into (§4.7) — reject cleanly instead of
        # crashing into a 500 below.
        raise RuntimeError("Ollama needs no sign-in")
    binpath = harness.resolve_bin(harness.PROVIDER_BIN[provider_id])
    if binpath is None:
        raise RuntimeError(f"{harness.PROVIDER_NAME[provider_id]} isn't installed "
                           f"on this {paths.machine_noun()}")
    if provider_id == "codex":
        subprocess.Popen([binpath, "login"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         env=harness.spawn_env(binpath),
                         cwd=harness._neutral_cwd("codex"),
                         **platform.current().processes.session_kwargs())
        return "browser"
    args = {"claude": ["/login"], "gemini": [], "opencode": ["auth", "login"]}[provider_id]
    if paths.current_os() == "linux":
        # §2: the Terminal-window method is macOS-only (osascript). On Linux
        # the §12 UI shows the manual command instead of the sign-in button;
        # this line is defense in depth for an un-gated client.
        manual = " ".join(shlex.quote(p) for p in [binpath, *args])
        raise RuntimeError("Sign-in help opens a terminal window only on macOS — "
                           f"run `{manual}` in your own terminal to sign in.")
    # §6/§19: Terminal shells start in ~ — cd into the provider's empty
    # workspace first so the CLI's startup scan never walks the home folder.
    cmd = (f"cd {shlex.quote(harness._neutral_cwd(provider_id))} && "
           + " ".join(shlex.quote(p) for p in [binpath, *args]))
    osa = cmd.replace("\\", "\\\\").replace('"', '\\"')
    # §19: "sign-in help couldn't start" is a 409 with the reason — a wedged
    # Terminal (osascript blocks on its Apple event) or a missing osascript
    # would 500 the endpoint instead. Bounded through the shared helper: a
    # grandchild holding osascript's pipe would wedge subprocess.run's own
    # timeout in communicate().
    try:
        done = run_bounded(["osascript", "-e", 'tell application "Terminal" to activate',
                            "-e", f'tell application "Terminal" to do script "{osa}"'],
                           timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("Terminal didn't respond; run the command in your own "
                           "terminal to sign in.") from e
    if done is None:
        raise RuntimeError("Terminal didn't respond; run the command in your own "
                           "terminal to sign in.")
    return "terminal"


# ---------- mechanics ----------

def _stream_shell(cmd: list[str], emit, provider_id: str,
                  env_extra: dict | None = None) -> None:
    """Run an installer child, forwarding each output line; raise on failure
    with the last decisive line as the message."""
    env = harness.spawn_env(cmd[0])
    env.setdefault("HOME", os.path.expanduser("~"))
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            # §2 pipe-encoding contract
                            encoding="utf-8", errors="replace",
                            env=env, cwd=harness._neutral_cwd(provider_id),
                            # own session/group (the §2 platform layer's spawn
                            # policy): the timeout kill reaches the whole
                            # pipeline (curl | bash spawns children)
                            **platform.current().processes.session_kwargs())
    timed_out = threading.Event()
    # §2 pipe-release contract (§19: the cap's kill releases the install's own
    # read): the same releasable reader harness._invoke uses.
    reader = harness.PipeReader(proc.stdout)

    def _kill() -> None:
        timed_out.set()
        # `sig=None` means kill hard (§2: SIGKILL is not importable on
        # Windows); the layer falls back to the direct child itself.
        platform.current().processes.signal_group(proc, None)
        # An escaped child (a daemonizing grandchild that re-setsid'd) could
        # still hold the merged pipe open — release our read end so the loop
        # sees EOF regardless, like harness._invoke's timeout kill. Never
        # `.close()` from this thread: close() takes the buffer lock the
        # blocked read holds and would wedge the timer instead of freeing the
        # loop.
        reader.defuse()

    timer = threading.Timer(INSTALL_TIMEOUT_S, _kill)
    timer.daemon = True
    timer.start()
    tail: list[str] = []
    try:
        try:
            while True:
                # Size-capped readline, the same shape the engine and harness
                # read loops use: iterating the pipe buffers a newline-free
                # stream (a progress bar drawn with carriage returns) without
                # bound before it ever yields.
                raw = reader.readline()
                if raw == "":
                    break
                line = raw.strip()
                if line:
                    tail = (tail + [line])[-5:]
                    emit(line=line)
        except ValueError:
            # The timeout kill closed our read end — anything else is real.
            if not timed_out.is_set():
                raise
        proc.wait()
    finally:
        timer.cancel()
        # The read end closes on every path — one leaked fd per install would
        # otherwise accumulate on the long-lived backend.
        reader.close()
    if timed_out.is_set() and proc.returncode != 0:
        # returncode guard: a timer firing in the instant after a successful
        # exit must not report a completed install as a timeout.
        raise RuntimeError(f"the installer timed out after {INSTALL_TIMEOUT_S // 60} minutes")
    if proc.returncode != 0:
        raise RuntimeError(tail[-1] if tail else f"installer exited with code {proc.returncode}")


def _download(url: str, dest: str, emit, label: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "autowright"})
    deadline = time.monotonic() + INSTALL_TIMEOUT_S
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got, last = 0, -1
        while True:
            if time.monotonic() > deadline:
                # the per-read timeout can't catch a server trickling bytes
                raise RuntimeError(f"{label} timed out after {INSTALL_TIMEOUT_S // 60} minutes")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                percent = int(got * 100 / total)
                if percent != last:
                    last = percent
                    # §19: the number rides only `percent` — `line` stays the
                    # bare step label the UI renders under the one install bar.
                    emit(line=label, percent=percent)


def _login_shell_path() -> list[str]:
    """The user's login-shell PATH entries — the backend's own env doesn't see
    shell profiles, so ask the shell itself. Empty list on any failure."""
    shell = os.environ.get("SHELL") or (
        "/bin/zsh" if paths.current_os() == "macos" else "/bin/sh")
    try:
        # Bounded: a login shell whose profile spawns a lingering background
        # job holds the probe's stdout, and `subprocess.run`'s own timeout
        # would then block here forever (§2 shared bounded run).
        out = run_bounded([shell, "-l", "-c", 'printf %s "$PATH"'], 15)
        if out is not None and out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().split(os.pathsep)
    except Exception:  # noqa: BLE001 — a PATH probe may never fail the install
        pass
    return []


def _ensure_login_path(emit) -> None:
    """§19 terminal-access guarantee: every install that lands a bin in
    ~/.local/bin (claude's and codex's vendor scripts, gemini's npm --prefix,
    ollama's symlink) must leave it reachable from the user's terminal — the
    vendor scripts at most print PATH instructions nobody sees in a background
    install. When the dir isn't on the login shell's PATH, append a guarded
    export line to the shell profile (profile, not rc file: macOS terminals
    start login shells and PATH is environment). Best-effort: never fails the
    install."""
    if LOCAL_BIN in _login_shell_path():
        return
    shell = os.path.basename(os.environ.get("SHELL") or "/bin/zsh")
    if paths.current_os() == "linux":
        # ~/.profile: sourced by desktop sessions and login shells alike.
        # Never *create* ~/.bash_profile here — a fresh one would stop bash
        # login shells from sourcing the user's ~/.profile; append to an
        # existing one only.
        profile = ("~/.bash_profile" if shell == "bash"
                   and os.path.exists(os.path.expanduser("~/.bash_profile"))
                   else "~/.profile")
    else:
        profile = {"zsh": "~/.zprofile", "bash": "~/.bash_profile"}.get(shell, "~/.profile")
    prof = os.path.expanduser(profile)
    try:
        existing = ""
        if os.path.exists(prof):
            with open(prof, encoding="utf-8", errors="replace") as f:
                existing = f.read()
        if ".local/bin" in existing:
            return
        with open(prof, "a", encoding="utf-8") as f:
            f.write(f'\n{PATH_MARKER}\nexport PATH="$HOME/.local/bin:$PATH"\n')
        emit(line=f"Added ~/.local/bin to your PATH ({profile}) — "
                  "open a new terminal to use it")
    except OSError:
        pass


def _require(binname: str) -> None:
    if harness.resolve_bin(binname) is None:
        raise RuntimeError(f"the installer finished but `{binname}` didn't appear "
                           f"on this {paths.machine_noun()}")


def _install_claude(emit) -> None:
    emit(line="Downloading the Claude Code installer…")
    _stream_shell(["/bin/bash", "-c", f"curl -fsSL {CLAUDE_INSTALLER} | bash"], emit,
                  "claude")
    _require("claude")
    # The vendor script lands the bin in ~/.local/bin but only prints PATH
    # instructions — invisible from a background install (§19).
    _ensure_login_path(emit)


def _install_opencode(emit) -> None:
    # The script installs into its own default `~/.opencode/bin` (on the §19
    # fallback bin-dir list); its documented OPENCODE_INSTALL_DIR is ignored
    # by the live script, so nothing is passed.
    emit(line="Downloading the OpenCode installer…")
    _stream_shell(["/bin/bash", "-c", f"curl -fsSL {OPENCODE_INSTALLER} | bash"], emit,
                  "opencode")
    _require("opencode")


def _install_gemini(emit) -> None:
    # Gemini CLI ships only through npm (§19) — fail fast without Node.
    npm = harness.resolve_bin("npm")
    if npm is None:
        raise RuntimeError("Gemini CLI needs Node.js — install it from nodejs.org first, "
                           "then try again.")
    emit(line="Installing @google/gemini-cli with npm…")
    _stream_shell([npm, "install", "-g", "--prefix", os.path.expanduser("~/.local"),
                   "@google/gemini-cli"], emit, "gemini")
    _require("gemini")
    # The --prefix bin placement is ours, not npm's default — guarantee the
    # user's terminal reaches `gemini` too (§19).
    _ensure_login_path(emit)


def _install_codex(emit) -> None:
    emit(line="Downloading the Codex installer…")
    # CODEX_NON_INTERACTIVE: the backend has no TTY to answer its prompts.
    _stream_shell(["/bin/bash", "-c", f"curl -fsSL {CODEX_INSTALLER} | sh"], emit,
                  "codex", env_extra={"CODEX_NON_INTERACTIVE": "1"})
    _require("codex")
    # Same §19 guarantee as claude: the vendor symlink lands in ~/.local/bin.
    _ensure_login_path(emit)


def _install_ollama_app(emit) -> str:
    """Install the official Mac app the way ollama.com's install.sh does —
    the CLI symlink in the vendor's own `/usr/local/bin` when writable
    without sudo, else `~/.local/bin`. Returns the installed app path."""
    apps = APPLICATIONS if os.access(APPLICATIONS, os.W_OK) \
        else os.path.expanduser("~/Applications")
    dest = os.path.join(apps, "Ollama.app")
    with tempfile.TemporaryDirectory() as td:
        zip_path = os.path.join(td, "Ollama-darwin.zip")
        _download(OLLAMA_APP_ZIP, zip_path, emit, "Downloading Ollama")
        emit(line="Installing the Ollama app…")
        # Every phase carries a wall-clock cap: an unpack, a quit or a launch
        # that never returns would otherwise hang the whole install with no
        # line to show for it (see INSTALL_TIMEOUT_S).
        try:
            subprocess.run(["/usr/bin/ditto", "-x", "-k", zip_path, td],
                           check=True, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("unpacking the Ollama archive timed out") from e
        src = os.path.join(td, "Ollama.app")
        if not os.path.isdir(src):
            raise RuntimeError("no Ollama.app found in the downloaded archive")
        # Vendor-script parity: quit a running app, replace an existing install.
        try:
            quit_rc = subprocess.run(["pkill", "-x", "Ollama"],
                                     capture_output=True, timeout=15).returncode
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("quitting the running Ollama app timed out") from e
        if quit_rc == 0:
            time.sleep(2)
        os.makedirs(apps, exist_ok=True)
        # Move the new bundle beside the target BEFORE removing the old one:
        # rmtree-ing first and then failing the move (a full disk, a
        # cross-device copy dying halfway) would leave no Ollama at all where
        # a working one stood. Nothing but the final rename is destructive.
        staged = dest + ".ad-new"
        shutil.rmtree(staged, ignore_errors=True)
        try:
            shutil.move(src, staged)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            os.rename(staged, dest)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
    # §19 install-location principle: the vendor script symlinks
    # /usr/local/bin/ollama (sudo'd) — use that exact location when it's
    # writable without sudo, so the CLI sits where a manual install puts it;
    # else ~/.local/bin plus the terminal-access guarantee.
    if os.path.isdir(USR_LOCAL_BIN) and os.access(USR_LOCAL_BIN, os.W_OK):
        bin_dir = USR_LOCAL_BIN
    else:
        bin_dir = LOCAL_BIN
        os.makedirs(bin_dir, exist_ok=True)
    link = os.path.join(bin_dir, "ollama")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(os.path.join(dest, "Contents", "Resources", "ollama"), link)
    if bin_dir == LOCAL_BIN:
        _ensure_login_path(emit)
    return dest


def _install_ollama_tarball(emit) -> None:
    """Install the official Linux standalone bundle into `~/.local` — the
    vendor's own archive (bin/ollama + lib/ollama), extracted user-owned
    instead of install.sh's sudo'd /usr (§3: no sudo anywhere). The backend
    owns starting `ollama serve` on Linux (harness.ollama_status self-heals);
    there is no app agent."""
    local = os.path.expanduser("~/.local")
    with tempfile.TemporaryDirectory() as td:
        tar = os.path.join(td, "ollama-linux-amd64.tar.zst")
        _download(OLLAMA_LINUX_TAR, tar, emit, "Downloading Ollama")
        emit(line="Installing Ollama…")
        os.makedirs(local, exist_ok=True)
        # A previous install's lib tree may hold files a new archive dropped.
        shutil.rmtree(os.path.join(local, "lib", "ollama"), ignore_errors=True)
        with tarfile.open(tar) as tf:  # transparent zstd (PEP 784)
            tf.extractall(local, filter="data")
    _ensure_login_path(emit)


def _install_ollama(emit) -> None:
    if paths.current_os() == "linux":
        _install_ollama_tarball(emit)
        _require("ollama")
        emit(line="Starting the Ollama server…")
        # No app agent on Linux: harness.ollama_status() below self-heals by
        # spawning `ollama serve` when the CLI exists and the server is down.
    else:
        app = _install_ollama_app(emit)
        _require("ollama")
        emit(line="Starting the Ollama server…")
        # The app's menu-bar agent owns the server (and auto-updates) — launch
        # it hidden like the vendor script does.
        try:
            subprocess.run(["open", app, "--args", "hidden"],
                           capture_output=True, check=False, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("starting the Ollama app timed out") from e
    for _ in range(30):
        # §19: readiness only — the version lookup this loop never reports is
        # one more bounded HTTP round trip per second.
        if harness.ollama_status(want_version=False)["ready"]:
            return
        time.sleep(1)
    raise RuntimeError("Ollama installed but its server didn't start")


_INSTALLERS = {
    "claude": _install_claude,
    "codex": _install_codex,
    "gemini": _install_gemini,
    "opencode": _install_opencode,
    "ollama": _install_ollama,
}
