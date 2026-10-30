"""Audit 2026-10-30 — platform layer (§3 Linux service block): the systemd
user unit pins the install-time root variables with `Environment=` lines, so
the GUI (desktop session environment) and the backend (user manager
environment) resolve the same data and log roots."""
from __future__ import annotations

import pytest

from autowright.platform import linux

ROOT_VARIABLES = ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "AUTOWRIGHT_HOME")


@pytest.fixture
def clean_environment(monkeypatch):
    for name in ROOT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _environment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Environment=")]


def test_unit_carries_no_environment_lines_when_none_are_set(clean_environment):
    assert _environment_lines(linux.unit_text()) == []


def test_unit_pins_every_set_root_variable_in_order(clean_environment, tmp_path):
    clean_environment.setenv("XDG_DATA_HOME", "/home/a/xdg-data")
    clean_environment.setenv("XDG_STATE_HOME", "/home/a/xdg-state")
    clean_environment.setenv("XDG_CONFIG_HOME", "/home/a/xdg-config")
    clean_environment.setenv("AUTOWRIGHT_HOME", str(tmp_path))
    text = linux.unit_text()
    assert _environment_lines(text) == [
        'Environment=XDG_DATA_HOME="/home/a/xdg-data"',
        'Environment=XDG_STATE_HOME="/home/a/xdg-state"',
        'Environment=XDG_CONFIG_HOME="/home/a/xdg-config"',
        f'Environment=AUTOWRIGHT_HOME="{tmp_path}"',
    ]
    # Inside [Service], ahead of ExecStart.
    service = text.index("[Service]\n")
    assert service < text.index("Environment=") < text.index("ExecStart=")


def test_unset_and_empty_variables_are_skipped(clean_environment):
    clean_environment.setenv("XDG_DATA_HOME", "")
    clean_environment.setenv("XDG_STATE_HOME", "/home/a/state")
    assert _environment_lines(linux.unit_text()) == [
        'Environment=XDG_STATE_HOME="/home/a/state"',
    ]


def test_values_are_quoted_with_systemd_escapes(clean_environment):
    # Quotes and backslashes are escaped inside the double-quoted value, `%`
    # (a specifier introducer) is doubled, and `$` stays literal — systemd
    # never expands variables in Environment=.
    clean_environment.setenv("XDG_DATA_HOME", '/odd path/100%/a"b\\c$HOME')
    assert _environment_lines(linux.unit_text()) == [
        'Environment=XDG_DATA_HOME="/odd path/100%%/a\\"b\\\\c$HOME"',
    ]
