"""§2 platform layer — shared POSIX process-group control (macOS and Linux).

The bodies here are the engine/harness group-kill semantics, moved to one
place; the spawn sites stay in their owning modules (engine, harness,
packages — the §15 suites patch `<module>.subprocess`) and take their session
policy from `session_kwargs()`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Sequence


class PosixProcessControl:
    def session_kwargs(self) -> dict:
        """Own session per child: timeout/cancel/skip kill the whole group,
        and pgid == pid (§4.5 persisted-pgid invariant)."""
        return {"start_new_session": True}

    def signal_group(self, proc, sig: int | None = None) -> None:
        """Signal a child's whole session group; falls back to the direct
        child when the group is gone. `sig=None` means kill hard (SIGKILL,
        not importable on Windows)."""
        if sig is None:
            sig = signal.SIGKILL
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            if proc.poll() is None:
                (proc.kill if sig == signal.SIGKILL else proc.terminate)()

    def kill_group(self, pgid: int) -> None:
        """SIGKILL a bare persisted group id (its Popen is long gone)."""
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def group_has_command(self, pgid: int, marker: str) -> bool:
        """§3 pid-reuse guard: whether the group still contains a process
        whose command line carries `marker`. An unreadable process table
        answers False — never kill what can't be verified."""
        try:
            out = subprocess.run(["ps", "-axo", "pgid=,command="],
                                 capture_output=True, encoding="utf-8",
                                 errors="replace", timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0] == str(pgid) and marker in parts[1]:
                return True
        return False

    def _pid_table(self) -> list[tuple[int, int, str]]:
        """(pid, pgid, command) snapshot; [] when the table is unreadable —
        never kill what can't be verified."""
        try:
            out = subprocess.run(["ps", "-axo", "pid=,pgid=,command="],
                                 capture_output=True, encoding="utf-8",
                                 errors="replace", timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        rows = []
        for line in out.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
        return rows

    def kill_matching(self, markers: Sequence[str], grace_s: float = 2.0) -> int:
        """§3 quit-entirely sweep: TERM → grace → KILL every process whose
        command line carries one of `markers`, excluding this process and its
        own group (the Electron caller during quit-all, the shell job in CLI
        use, the in-process service.stop() sweeper itself — their command
        lines can carry a marker too). Returns the matched count."""
        own_pid, own_pgid = os.getpid(), os.getpgid(0)
        matched = [(pid, pgid, cmd) for pid, pgid, cmd in self._pid_table()
                   if pid != own_pid and pgid != own_pgid
                   and any(m in cmd for m in markers)]
        if not matched:
            return 0

        def _signal_all(sig: int, rows) -> None:
            for pgid in {r[1] for r in rows}:
                try:
                    os.killpg(pgid, sig)
                except (ProcessLookupError, PermissionError):
                    # The group is gone or partly foreign — fall back to the
                    # matched pids inside it, one by one.
                    for pid, row_pgid, _cmd in rows:
                        if row_pgid != pgid:
                            continue
                        try:
                            os.kill(pid, sig)
                        except (ProcessLookupError, PermissionError):
                            pass

        _signal_all(signal.SIGTERM, matched)
        # §3 pid-reuse guard (group_has_command's spirit): a survivor is the
        # same pid still showing the same command text — a reused pid with a
        # different line is somebody else and must not be hard-killed.
        deadline = time.monotonic() + grace_s
        survivors = matched
        while True:
            table = {(pid, cmd) for pid, _pgid, cmd in self._pid_table()}
            survivors = [row for row in survivors if (row[0], row[2]) in table]
            if not survivors or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if survivors:
            _signal_all(signal.SIGKILL, survivors)
        return len(matched)
