"""§6 OS notifications, per platform: the real macOS OsascriptNotifier escapes
title/body into the osascript argv (exercised against the fake
tests/bin/osascript recorder) and the real Windows WinRT toast notifier builds
its PowerShell script under the §3 AUMID (exercised with the PowerShell layer
patched, the same way the `task_scheduler` fixture models Task Scheduler);
plus §2 routing — notify.post is nothing but the composed notifier's post.

Every notifier unit is instantiated directly rather than reached through
`notify.post`, so these run on every host: `platform.darwin` and
`platform.windows` import anywhere, while `platform.current()` composes only
the host's own.
"""
import pytest
from conftest import use_fake_osascript

from autowright.platform import darwin, windows


def _notifier(monkeypatch):
    use_fake_osascript(monkeypatch, darwin)
    return darwin.OsascriptNotifier()


def test_post_escapes_quotes_and_backslashes(tmp_path, monkeypatch):
    log = tmp_path / "osascript.log"
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_LOG", str(log))
    monkeypatch.delenv("AUTOWRIGHT_TEST_OSASCRIPT_FAIL", raising=False)

    _notifier(monkeypatch).post('Alert "one" \\ two', 'Body "quoted" \\ tail')
    rec = log.read_text(encoding="utf-8")

    # one recorded call: -e plus the AppleScript line
    assert rec.startswith("-e\t")
    assert "display notification" in rec
    # double quotes arrive backslash-escaped so AppleScript sees literal quotes
    assert 'with title "Alert \\"one\\" \\\\ two"' in rec
    assert 'display notification "Body \\"quoted\\" \\\\ tail"' in rec
    # and nothing leaks an unescaped closing quote before the keyword
    assert '"Alert "one""' not in rec


def test_post_swallows_osascript_failure(tmp_path, monkeypatch):
    # §6: notifications are best-effort — a denied/failing osascript never raises.
    log = tmp_path / "osascript.log"
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_LOG", str(log))
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_FAIL", "1")
    _notifier(monkeypatch).post("Title", "Body")  # exit 1 from the fake — swallowed
    assert "display notification" in log.read_text(encoding="utf-8")


def test_post_swallows_a_missing_osascript(tmp_path, monkeypatch):
    # §6: notifications must survive a machine where osascript can't even
    # spawn (PATH broken/sandboxed) — the OSError is swallowed, never raised.
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    darwin.OsascriptNotifier().post("Title", "Body")  # FileNotFoundError inside


# ------------------------------------------------- §3 Windows toast notifier
#
# powershell.exe never runs here: `windows._powershell` is replaced by a
# recorder, so the script the notifier *would* run is what gets asserted and
# every test below runs on any host.


@pytest.fixture()
def toasts(monkeypatch):
    """The §3 toast notifier with its PowerShell layer patched. Yields
    (notifier, calls) where each call is (script, kwargs)."""
    calls = []

    def fake_ps(script, **kw):
        calls.append((script, kw))
        return 0, "", ""

    monkeypatch.setattr(windows, "_powershell", fake_ps)
    return windows.WindowsNotifier(), calls


def test_windows_toast_posts_under_the_aumid(toasts):
    """§3: a WinRT ToastNotificationManager invocation posted under
    `ai.autowright.app`, time-boxed at 10 s so a wedged PowerShell can never
    block the caller (§6 best-effort)."""
    notifier, calls = toasts
    notifier.post("Automation failed", "Manga check · step 2")
    (script, kw), = calls
    assert "[Windows.UI.Notifications.ToastNotificationManager]" in script
    assert "ContentType = WindowsRuntime" in script
    assert "CreateToastNotifier('ai.autowright.app').Show($toast)" in script
    assert windows.AUMID == "ai.autowright.app"
    # Text goes in as nodes on the built-in template — never hand-written XML.
    assert "ToastTemplateType]::ToastText02" in script
    assert "CreateTextNode('Automation failed')" in script
    assert "CreateTextNode('Manga check · step 2')" in script
    assert kw == {"timeout": windows.TOAST_TIMEOUT_S} and windows.TOAST_TIMEOUT_S == 10


def test_windows_toast_text_cannot_break_the_script_or_the_xml(toasts):
    """Quotes are doubled into the PowerShell literal and markup characters
    reach the document as a text node — neither can end the string early nor
    inject toast XML."""
    notifier, calls = toasts
    notifier.post("It's <b>done</b>", "a & b 'quoted'")
    (script, _), = calls
    assert "CreateTextNode('It''s <b>done</b>')" in script
    assert "CreateTextNode('a & b ''quoted''')" in script
    # The module's single-quoted-literal rule: nothing for Windows
    # command-line quoting to mangle.
    assert '"' not in script


def test_windows_toast_swallows_a_failing_powershell(monkeypatch):
    """§6: notifications are best-effort — a non-zero PowerShell (an AUMID
    Windows does not know, notifications switched off in the OS) is not an
    error the caller ever sees, and nothing is retried."""
    calls = []

    def failing(script, **kw):
        calls.append(script)
        return 1, "", "Element not found. (Exception from HRESULT: 0x80070490)"

    monkeypatch.setattr(windows, "_powershell", failing)
    windows.WindowsNotifier().post("Title", "Body")
    assert len(calls) == 1


def test_windows_toast_swallows_a_missing_powershell(monkeypatch):
    """A host where powershell.exe cannot even spawn degrades silently — the
    real `_powershell` answers a plain failure tuple, and anything unexpected
    it might raise is swallowed too."""
    def missing(script, **kw):
        raise OSError("no powershell.exe")

    monkeypatch.setattr(windows, "_powershell", missing)
    windows.WindowsNotifier().post("Title", "Body")


def test_windows_aumid_probe_reads_the_start_menu_shortcut(monkeypatch, tmp_path):
    """§3: the probe answers True once the installer's Start-menu shortcut
    exists (per-user first, all-users second) and False on a machine that has
    neither — which is why `capabilities.notifications` stays false on a dev
    box (the registry half is genuinely absent there)."""
    appdata = tmp_path / "Roaming"
    programdata = tmp_path / "ProgramData"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PROGRAMDATA", str(programdata))
    assert windows._aumid_registered() is False

    per_user, all_users = windows._start_menu_shortcuts()
    assert per_user == (appdata / "Microsoft" / "Windows" / "Start Menu"
                        / "Programs" / "Autowright.lnk")
    assert all_users.parent.name == "Programs" and all_users.name == "Autowright.lnk"

    all_users.parent.mkdir(parents=True)
    all_users.write_bytes(b"lnk")
    assert windows._aumid_registered() is True

    all_users.unlink()
    per_user.parent.mkdir(parents=True)
    per_user.write_bytes(b"lnk")
    assert windows._aumid_registered() is True


def test_windows_aumid_probe_survives_a_profile_without_the_env(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    assert windows._start_menu_shortcuts() == []
    assert windows._aumid_registered() in (True, False)  # never raises


def test_post_routes_through_the_composed_notifier(no_notifications, monkeypatch):
    """§2/§6: `notify.post` is exactly `platform.current().notifier.post` — the
    module holds no OS knowledge of its own."""
    import dataclasses

    from autowright import platform as platmod

    real_post = no_notifications  # the fixture yields the original notify.post
    seen = []

    class RecordingNotifier:
        def post(self, title, body):
            seen.append((title, body))

    fake = dataclasses.replace(platmod.current(), notifier=RecordingNotifier())
    monkeypatch.setattr(platmod, "current", lambda: fake)
    real_post("Title", "Body")
    assert seen == [("Title", "Body")]


# ---------- §6 carrier rule: shell-posted vs native ----------

def _carrier_hub(monkeypatch, loop):
    from autowright import events
    from autowright.events import EventHub

    hub = EventHub()
    hub.bind_loop(loop)
    monkeypatch.setattr(events, "hub", hub)
    return hub


@pytest.fixture()
def carrier_loop():
    import asyncio
    import threading

    lp = asyncio.new_event_loop()
    t = threading.Thread(target=lp.run_forever, daemon=True)
    t.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    t.join(timeout=5)
    lp.close()


def test_post_targeted_hands_the_notification_to_a_connected_shell(monkeypatch, carrier_loop):
    """§6: with a `role=shell` subscriber connected the backend publishes one
    `notification.posted` event and never posts natively."""
    import asyncio

    from autowright import notify

    hub = _carrier_hub(monkeypatch, carrier_loop)
    native = []
    monkeypatch.setattr(notify, "post", lambda title, body: native.append((title, body)))
    shell = hub.subscribe("shell")
    target = {"page": "execution", "executionId": "e-1"}
    notify.post_targeted("Report", "Execution failed", kind="execution", target=target)
    msg = asyncio.run_coroutine_threadsafe(shell.get(), carrier_loop).result(5)
    assert native == []
    assert msg["event"] == "notification.posted"
    assert msg["kind"] == "execution"
    assert msg["title"] == "Report" and msg["body"] == "Execution failed"
    assert msg["target"] == target
    assert isinstance(msg["id"], str) and len(msg["id"]) == 36


def test_post_targeted_posts_natively_without_a_shell(monkeypatch, carrier_loop):
    """§6: no shell subscriber (headless, the UI quit) → the platform notifier,
    and no event exists — a renderer alone never turns the event on."""
    import time

    from autowright import notify

    hub = _carrier_hub(monkeypatch, carrier_loop)
    native = []
    monkeypatch.setattr(notify, "post", lambda title, body: native.append((title, body)))
    renderer = hub.subscribe()
    notify.post_targeted("Dead Job", "Scheduled executions are being missed.", kind="overdue",
                         target={"page": "automation", "automationId": "a-1"})
    assert native == [("Dead Job", "Scheduled executions are being missed.")]
    time.sleep(0.05)  # a stray publish would have landed by now
    assert renderer.qsize() == 0
