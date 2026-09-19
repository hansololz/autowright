"""Declared-package install (§6.2): packages a manifest declares beyond the
curated list install into `<app-support>/site-packages` via the bundled
interpreter's pip — the user never runs pip. Manifests carry bare distribution
names; the installed distribution is the single source of truth for the
version. One idempotent `ensure` serves every call site: the §8 post-steps
install stage, the §19 install endpoint, and the engine's pre-execution
self-heal (§7). `outdated` backs the §11 update badges — read-only PyPI
lookups, never pip; `upgrade` backs the §11 Update button."""
from __future__ import annotations

import functools
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import paths, platform

# §6.2/§8: bare PEP 503 distribution name — no version specifier, no extras.
PIP_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

INSTALL_TIMEOUT = 600  # seconds per package

# §6.2: one pip run at a time process-wide — pip has no locking of its own,
# and a draft-stage install may race the engine's pre-execution ensure.
_pip_lock = threading.Lock()


class PackagesBusy(RuntimeError):
    """§6.2: another pip run holds the process-wide lock. The §19 packages
    routes answer 409 on it rather than hold a request worker for the length
    of someone else's install."""


def site_packages_dir() -> Path:
    return paths.app_support() / "site-packages"


def _norm(name: str) -> str:
    """PEP 503 distribution-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


# §6.2: the installed scan walks every dist-info in the directory. Cheap once,
# but §4.1's derived `problems` audit runs it per automation on every /state
# publish, under store.lock - so it is cached behind the directory's own
# (mtime, entry count). pip only ever writes through this directory, so either
# half of the key moves when anything is installed, upgraded or removed by
# hand; ensure/upgrade also drop the cache explicitly, so an install landing in
# the same mtime granule can't be missed.
_scan_lock = threading.Lock()
_scan_cache: tuple[tuple, dict[str, str]] | None = None
# When the key was last read. §4.1's audit calls `check` once per automation
# on every /state publish, and re-reading the directory for each is the cost
# this window removes; an install landing inside the window is never missed
# because ensure/upgrade drop the cache outright.
_SCAN_KEY_TTL_S = 1.0
_scan_checked_at = 0.0


def _scan_key() -> tuple:
    d = site_packages_dir()
    try:
        with os.scandir(d) as it:
            return (d.stat().st_mtime_ns, sum(1 for _ in it))
    except OSError:  # absent (or unreadable) directory - one stable key
        return ()


def invalidate_scan() -> None:
    """Drop the cached §6.2 installed scan - called after every pip run."""
    global _scan_cache, _scan_checked_at
    with _scan_lock:
        _scan_cache = None
        _scan_checked_at = 0.0


def _scan_installed() -> dict[str, str]:
    import importlib.metadata as md

    out: dict[str, str] = {}
    d = site_packages_dir()
    if not d.exists():
        return out
    for dist in md.distributions(path=[str(d)]):
        try:
            name = dist.metadata.get("Name")
            if not name:
                continue
            out[_norm(name)] = dist.version
        except Exception:  # noqa: BLE001 — a broken dist-info never blocks the check
            continue
    return out


def _installed_versions() -> dict[str, str]:
    """Normalized distribution name → version, in the §6.2 directory only.
    Served from the cached scan while the directory key is unchanged; the
    returned mapping is shared, so callers must treat it as read-only."""
    global _scan_cache, _scan_checked_at
    now = time.monotonic()
    with _scan_lock:
        cached = _scan_cache
        if cached is not None and now - _scan_checked_at < _SCAN_KEY_TTL_S:
            return cached[1]
    key = _scan_key()
    with _scan_lock:
        cached = _scan_cache
        if cached is not None and cached[0] == key:
            _scan_checked_at = time.monotonic()
            return cached[1]
    out = _scan_installed()
    with _scan_lock:
        _scan_cache = (key, out)
        _scan_checked_at = time.monotonic()
    return out


def _import_present(import_name: str) -> bool:
    """§6.2: the declared `import` target present beside the distribution. A
    `.dist-info` whose module files a killed pip never finished copying counts
    as missing, so ensure can repair it instead of reporting installed
    forever. The target is a package directory, a module file, or an extension
    module (`_name.cpython-313-darwin.so`, `name.pyd`)."""
    top = import_name.split(".", 1)[0].strip()
    # The name comes from a hand-editable manifest — never joined into a path
    # unless it is a plain module name (§6.2 declarations are import names).
    if not top.isidentifier():
        return True
    d = site_packages_dir()
    if (d / top).is_dir() or (d / f"{top}.py").is_file():
        return True
    try:
        return any(p.suffix != ".dist-info" for p in d.glob(f"{top}.*"))
    except OSError:  # absent (or unreadable) directory — nothing is there
        return False


def check(entries: list[dict]) -> list[dict]:
    """§19 POST /packages/check — the fast installed-check, never runs pip.
    Each entry comes back as {pip, import, status: installed | missing,
    version?} — `version` is the real installed version (§6.2: the installed
    distribution is the source of truth; any version counts as installed).
    §6.2: installed means the distribution AND the declared `import` target,
    so a half-copied install reads as missing and ensure repairs it."""
    installed = _installed_versions()
    out = []
    for e in entries or []:
        name = str(e.get("pip") or "").strip()
        version = installed.get(_norm(name)) if PIP_NAME_RE.match(name) else None
        import_name = str(e.get("import") or "").strip()
        if version and import_name and not _import_present(import_name):
            version = None
        r = {"pip": name, "import": import_name,
             "status": "installed" if version else "missing"}
        # §6.2: the declaration's why rides through check/ensure results so the
        # §8 draft-stage install never strips it from the draft payload.
        if e.get("why"):
            r["why"] = str(e["why"])
        if version:
            r["version"] = version
        out.append(r)
    return out


PYPI_TIMEOUT = 8  # seconds per package lookup


@functools.lru_cache(maxsize=1)
def _supported_tags() -> frozenset:
    """The bundled interpreter's own wheel tags. `sys_tags()` builds the whole
    platform tag matrix every call, and it can't change within a process, so
    the §19 outdated sweep pays for it once instead of once per package."""
    from packaging.tags import sys_tags

    return frozenset(sys_tags())


def _latest_compatible(name: str) -> str | None:
    """Newest stable, non-yanked PyPI version of `name` that ships a wheel
    compatible with the bundled interpreter (§6.2 wheels-only applies to the
    update check too). None when nothing compatible exists; lookup/parse
    errors propagate — `outdated`'s probe (the only caller) swallows them,
    so the advisory update badge simply stays off."""
    import json
    import urllib.request

    from packaging.utils import parse_wheel_filename
    from packaging.version import InvalidVersion, Version

    url = f"https://pypi.org/pypi/{_norm(name)}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "Autowright/1.0"})
    with urllib.request.urlopen(req, timeout=PYPI_TIMEOUT) as resp:
        releases = json.load(resp).get("releases") or {}
    supported = _supported_tags()
    candidates: list[tuple[Version, list]] = []
    for ver_str, files in releases.items():
        try:
            v = Version(ver_str)
        except InvalidVersion:
            continue
        if v.is_prerelease or v.is_devrelease or not files:
            continue
        candidates.append((v, files))
    for v, files in sorted(candidates, reverse=True):
        for f in files:
            if f.get("yanked") or not str(f.get("filename", "")).endswith(".whl"):
                continue
            try:
                tags = parse_wheel_filename(f["filename"])[3]
            except Exception:  # noqa: BLE001 — odd filename, skip the file
                continue
            if tags & supported:
                return str(v)
    return None


def outdated(entries: list[dict]) -> list[dict]:
    """§19 POST /packages/outdated — read-only PyPI lookups, in parallel.
    Each entry comes back as {pip, import, latest?}; `latest` is present only
    when a version newer than the **installed** one exists (§6.2: the
    installed distribution is the comparison baseline). Not installed or any
    failure → no `latest`."""
    from concurrent.futures import ThreadPoolExecutor

    from packaging.version import Version

    installed = _installed_versions()

    def probe(e: dict) -> dict:
        name = str(e.get("pip") or "").strip()
        out = {"pip": name, "import": str(e.get("import") or "").strip()}
        cur = installed.get(_norm(name)) if PIP_NAME_RE.match(name) else None
        if not cur:
            return out
        try:
            latest = _latest_compatible(name)
            if latest and Version(latest) > Version(cur):
                out["latest"] = latest
        except Exception:  # noqa: BLE001 — network/parse failure: badge stays off
            pass
        return out

    items = list(entries or [])
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        return list(pool.map(probe, items))


def _pip_install(name: str, pin_installed: bool = False,
                 should_stop=None) -> str | None:
    """One pip run into the §6.2 directory; returns an error string or None.
    Caller holds `_pip_lock`. `pin_installed` constrains every distribution
    already in the directory to its exact installed version — pip resolves
    with `--target` against its own env, not the directory, so without pins
    installing a new package silently *upgrades* shared dependencies already
    there (§6.2: an installed distribution is never touched by ensure).
    `should_stop()` is polled while pip runs — a §7 cancel must not wait out
    a single pip run (up to INSTALL_TIMEOUT s) while the execution holds its
    §6 slot; on stop the pip process group is killed and 'cancelled' comes
    back as the error."""
    target = site_packages_dir()
    target.mkdir(parents=True, exist_ok=True)
    # §2 console-interpreter rule: never pythonw — pip's helper children must
    # inherit the hidden console instead of opening terminal windows.
    cmd = [paths.console_python(), "-m", "pip", "install", "--upgrade",
           "--no-input", "--disable-pip-version-check",
           # §6.2: wheels only — a source-only package would need a
           # compiler users don't have; fail fast with pip's clear
           # "no matching distribution" instead of a build traceback.
           "--only-binary", ":all:",
           "--target", str(target)]
    constraints = None
    try:
        if pin_installed:
            pins = {n: v for n, v in _installed_versions().items() if n != _norm(name)}
            if pins:
                fd, constraints = tempfile.mkstemp(prefix="autowright-pins-", suffix=".txt", text=True)
                with os.fdopen(fd, "w") as f:
                    f.write("".join(f"{n}=={v}\n" for n, v in pins.items()))
                cmd += ["-c", constraints]
        try:
            # Own session/group so a stop/timeout kill reaches pip's own
            # children (build helpers, vendored subprocesses) — the §2
            # platform layer owns the spawn policy.
            proc = subprocess.Popen(cmd + [name], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    # §2 pipe-encoding contract
                                    encoding="utf-8", errors="replace",
                                    stdin=subprocess.DEVNULL,
                                    **platform.current().processes.session_kwargs())
        except OSError as e:
            return str(e)

        def _kill() -> None:
            # `sig=None` means kill hard (§2: SIGKILL is not importable on
            # Windows); the layer falls back to the direct child itself.
            platform.current().processes.signal_group(proc, None)

        def _drain() -> None:
            # A helper that survived the group kill can hold the pipes open —
            # a timeoutless drain here would wedge the process-wide _pip_lock
            # and block every later ensure.
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait()  # reap it — an unwaited pip stays a zombie
                except OSError:
                    pass
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass

        deadline = time.monotonic() + INSTALL_TIMEOUT
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if should_stop and should_stop():
                    _kill()
                    _drain()
                    return "cancelled"
                if time.monotonic() > deadline:
                    _kill()
                    _drain()
                    return f"pip timed out after {INSTALL_TIMEOUT} s"
        if proc.returncode == 0:
            return None
        tail = (err or out or "").strip().splitlines()[-3:]
        return " · ".join(ln.strip() for ln in tail) or f"pip exited {proc.returncode}"
    finally:
        if constraints:
            try:
                os.unlink(constraints)
            except OSError:
                pass



def _acquire_pip_lock(wait: bool, should_stop) -> None:
    """Take the process-wide pip lock. `wait=False` (§19 routes): one 0.25 s
    attempt, then `PackagesBusy` — waiting it out would hold a request worker
    for the whole of someone else's install. `wait=True` (engine, drafting,
    import): wait in 0.25 s slices, and a `should_stop()` that turns true while
    waiting raises `StopIteration` for the caller's cancel path — a §7 cancel
    never waits out another install (spec/execution.md). §19: the wait is
    bounded by the per-package install timeout, after which it answers the
    same `PackagesBusy` — a `wait: true` call never pins a request worker for
    longer than one install could take."""
    if not wait:
        if not _pip_lock.acquire(timeout=0.25):
            raise PackagesBusy()
        return
    deadline = time.monotonic() + INSTALL_TIMEOUT
    while not _pip_lock.acquire(timeout=0.25):
        if should_stop and should_stop():
            raise _Cancelled()
        if time.monotonic() >= deadline:
            raise PackagesBusy()


class _Cancelled(Exception):
    """Raised inside the lock wait when `should_stop()` turned true."""


def ensure(entries: list[dict], on_progress=None, should_stop=None,
           wait: bool = True) -> list[dict]:
    """§6.2 ensure — idempotent: check first, pip only for missing
    distributions (newest compatible wheel at that moment), serialized
    process-wide. An installed distribution is never touched — pins hold
    shared dependencies at their installed versions; upgrades go through
    `upgrade()` only. Each entry comes back as {pip, import,
    status: installed | failed, version?, error?}. `on_progress(name)` fires
    before each actual pip run; `should_stop()` (checked between pip runs and
    polled during each one) abandons the remaining installs and kills the
    running pip — a §7 cancel must not wait out pip. `wait` (the default —
    the engine's pre-execution ensure, the §8 post-steps install, the §5.1
    import ensure) waits its turn on the process-wide lock, honoring
    `should_stop` while it waits; `wait=False` (the §19 routes) makes one
    attempt and raises `PackagesBusy` when another pip run holds the lock."""
    results = check(entries)
    if all(r["status"] == "installed" for r in results):
        return results
    try:
        _acquire_pip_lock(wait, should_stop)
    except _Cancelled:
        # §7: a cancel arriving while this ensure waits its turn is honored at
        # once — the remaining installs are reported cancelled, never started.
        for r in results:
            if r["status"] != "installed":
                r["status"] = "failed"
                r["error"] = "cancelled"
        return results
    try:
        results = check(entries)  # re-check: another ensure may have run first
        for r in results:
            if r["status"] == "installed":
                continue
            if should_stop and should_stop():
                r["status"] = "failed"
                r["error"] = "cancelled"
                continue
            if not PIP_NAME_RE.match(r["pip"]):
                r["status"] = "failed"
                r["error"] = "not a bare distribution name"
                continue
            if on_progress:
                on_progress(r["pip"])
            err = _pip_install(r["pip"], pin_installed=True, should_stop=should_stop)
            invalidate_scan()  # pip may have written even on failure
            if err:
                r["status"] = "failed"
                r["error"] = err
            else:
                r["status"] = "installed"
                r["version"] = _installed_versions().get(_norm(r["pip"]))
    finally:
        _pip_lock.release()
    return results


def upgrade(entries: list[dict], should_stop=None, wait: bool = True) -> list[dict]:
    """§19 POST /packages/update — the §11 Update button: always runs pip
    (`install --upgrade name`), the one path that moves an installed
    distribution forward. A distribution that isn't installed is never
    installed by an update: it comes back failed, "not installed". Same result
    shape as `ensure`, the same `should_stop` between pip runs, and the same
    `wait` rule (`wait=False` raises `PackagesBusy` when another pip run holds
    the lock)."""
    results = check(entries)
    try:
        _acquire_pip_lock(wait, should_stop)
    except _Cancelled:
        for r in results:
            r["status"] = "failed"
            r["error"] = "cancelled"
        return results
    try:
        for r in results:
            if not PIP_NAME_RE.match(r["pip"]):
                r["status"] = "failed"
                r["error"] = "not a bare distribution name"
                r.pop("version", None)
                continue
            if r["status"] == "missing":
                r["status"] = "failed"
                r["error"] = "not installed"
                continue
            if should_stop and should_stop():
                r["status"] = "failed"
                r["error"] = "cancelled"
                r.pop("version", None)
                continue
            err = _pip_install(r["pip"])
            invalidate_scan()
            if err:
                r["status"] = "failed"
                r["error"] = err
                r.pop("version", None)
            else:
                r["status"] = "installed"
                r["version"] = _installed_versions().get(_norm(r["pip"]))
    finally:
        _pip_lock.release()
    return results
