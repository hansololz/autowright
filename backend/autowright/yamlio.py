"""Atomic YAML/text IO (§5: every write is temp-write + rename, file-first)."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("autowright.yamlio")


def load_yaml_checked(path: Path, default: Any = None) -> tuple[Any, bool]:
    """§5: returns (data, ok). ok is False only for a file that exists but
    can't be read — an absent file is a fresh install, not a failure. The
    store uses ok to make an unreadable top-level file read-only for the
    session, so a corrupt file is degraded, never overwritten by its default."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return (default if data is None else data), True
    except FileNotFoundError:
        return default, True
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
        # Disk is hand-editable (§5) — a corrupt or unreadable file (bad YAML,
        # bad encoding, bad permissions) must never brick startup into a
        # launchd crash loop; skip it with a warning like malformed triggers.
        log.warning("unreadable YAML at %s (%s) — using the default", path, e)
        return default, False


def load_yaml(path: Path, default: Any = None) -> Any:
    return load_yaml_checked(path, default)[0]


def _fsync_dir(d: Path) -> None:
    """§5: on POSIX the parent directory is fsynced after the rename, so a
    committed write survives a power loss on ext4 as well as APFS (the rename
    itself is only in the directory's page cache until then). Windows has no
    directory handle to sync, and a failure here never fails the write — the
    bytes are already on disk."""
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        log.warning("can't fsync the directory %s (%s) — the write is committed anyway", d, e)


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ad-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_yaml(path: Path, data: Any, mode: int | None = None) -> None:
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), mode)
