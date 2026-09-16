"""§2 platform layer — the capability Protocols and the composed Platform.

Composition over inheritance: one narrow Protocol per OS-coupled capability,
plain per-OS implementations, one frozen `Platform` object composed by
`platform.current()`. Shared logic lives in plain functions the
implementations import (posixproc.py), never in a superclass — `run_bounded`
lives here because it is OS-agnostic: it takes its whole spawn and kill policy
from the composed platform.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class ServiceManager(Protocol):
    """§3 service lifecycle — the five verbs, each answering the §3 result
    line whose exit code `service.result_code` derives."""

    def install(self) -> str: ...
    def uninstall(self) -> str: ...
    def status(self) -> str: ...
    def stop(self) -> str: ...
    def restart(self) -> str: ...


class Notifier(Protocol):
    """§6 OS notifications — best-effort, silent degrade, never raises."""

    def post(self, title: str, body: str) -> None: ...


class PowerAssertion(Protocol):
    """§3/§4.9 idle-sleep assertions — both of them. `reconcile` is the
    permanent keepAwake assertion (idempotent, called at boot and from
    PATCH /settings). `hold_execution` is the §3 per-execution hold: the
    engine acquires one for the duration of an execution and calls the
    returned release when it finishes. Neither acquiring nor releasing may
    ever raise — a platform with no mechanism composes no-ops."""

    def reconcile(self, enabled: bool) -> None: ...
    def hold_execution(self) -> Callable[[], None]: ...


class ProcessControl(Protocol):
    """§3 process-group control. The spawn sites themselves stay in their
    owning modules — the §15 suites patch `<module>.subprocess` — and take
    only their session *policy* from `session_kwargs()`. The surface is
    POSIX-shaped (pgids and signals, persisted per §4.5 with the own-session →
    pgid == pid invariant); the shipped Windows implementation maps it onto
    process trees — one process group per child, taskkill /T for both signal
    grades, and no pid-reuse verification (it needs pid + creation time), so
    `group_has_command` answers False there."""

    def session_kwargs(self) -> dict: ...
    def signal_group(self, proc, sig: int | None = None) -> None: ...
    def kill_group(self, pgid: int) -> None: ...
    def group_has_command(self, pgid: int, marker: str) -> bool: ...
    def kill_matching(self, markers: Sequence[str], grace_s: float = 2.0) -> int: ...


@dataclass(frozen=True)
class Capabilities:
    """What this OS can honor. Served on §19 GET /health so clients gate
    features here — never by sniffing the platform at a call site."""

    imessage: bool
    notifications: bool
    keep_awake: bool
    service: bool
    agent_install: bool

    def as_dict(self) -> dict:
        return {"imessage": self.imessage, "notifications": self.notifications,
                "keepAwake": self.keep_awake, "service": self.service,
                "agentInstall": self.agent_install}


@dataclass(frozen=True)
class Platform:
    os_token: str  # §5.1 vocabulary: macos | windows | linux
    service: ServiceManager
    notifier: Notifier
    power: PowerAssertion
    processes: ProcessControl
    capabilities: Capabilities


def run_bounded(argv: Sequence[str], timeout: float, second_wait: float = 5.0,
                **popen_kwargs) -> subprocess.CompletedProcess | None:
    """Run `argv` to completion within `timeout`; None when it timed out.

    `subprocess.run`'s own timeout kills only the DIRECT child and then blocks
    forever in `communicate()` on a grandchild still holding the pipe (the
    hazard harness._invoke documents). Here the child gets this platform's own
    spawn policy, the timeout kills its whole group through the same layer, and
    the reaping second wait is bounded too. Captures text stdout/stderr by
    default (§2 pipe-encoding contract); `popen_kwargs` override any of it.

    The one shared bounded run for short probes; the §15 suites patch it on the
    module that binds it, the way they patch `<module>.subprocess` elsewhere.
    """
    from . import current  # deferred: platform/__init__ imports this module

    processes = current().processes
    argv = list(argv)
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
              "encoding": "utf-8", "errors": "replace",
              **processes.session_kwargs(), **popen_kwargs}
    proc = subprocess.Popen(argv, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        processes.signal_group(proc, None)
        try:
            proc.communicate(timeout=second_wait)
        except subprocess.TimeoutExpired:
            # An escaped grandchild still holds the pipes — the group is dead
            # either way and the caller gets its timeout answer on time.
            pass
        return None
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)
