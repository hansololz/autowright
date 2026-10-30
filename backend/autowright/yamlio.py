"""Atomic YAML/text IO (§5: every write is temp-write + rename, file-first)."""
from __future__ import annotations

import logging
import os
import tempfile
import time
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
    except (yaml.YAMLError, UnicodeDecodeError, OSError, ValueError, RecursionError) as e:
        # Disk is hand-editable (§5) — a corrupt or unreadable file (bad YAML,
        # bad encoding, bad permissions, an impossible unquoted date such as
        # `2026-13-45` that PyYAML raises as ValueError, nesting too deep for
        # the parser) must never brick startup into a launchd crash loop; skip
        # it with a warning like malformed triggers.
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


# §5: on Windows the rename is retried on a sharing violation — a reader that
# holds the target open without delete-sharing (the request-log settings read,
# a GET /chat, the search indexer, an antivirus) makes it fail at random.
_REPLACE_RETRY_DELAYS_S = (0.02, 0.05, 0.1, 0.2, 0.4)


def _replace(source: str, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        return
    for delay in _REPLACE_RETRY_DELAYS_S:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, target)


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
        _replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    """§5: a string holding U+0085 (NEL) is written double-quoted (the `\\N`
    escape). PyYAML's default style leaves NEL raw inside a single-quoted
    scalar and reads it back as a line break, so `a<NEL>b` would reload as
    `a b`. Every other string keeps the default style."""
    if "\x85" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return yaml.SafeDumper.represent_str(dumper, value)


# Registered on the class `yaml.safe_dump` uses, so every safe dump in the
# process (the stored files and the §5.1 archive manifests alike) gets it.
yaml.SafeDumper.add_representer(str, _represent_str)


def dump_yaml(data: Any) -> str:
    """The one YAML dump: every stored file and the §19 version diff's manifest
    text go through it, so a diff reads as the on-disk file would."""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def save_yaml(path: Path, data: Any, mode: int | None = None) -> None:
    atomic_write_text(path, dump_yaml(data), mode)
