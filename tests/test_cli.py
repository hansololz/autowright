"""`autowright` CLI (§2/§3/§20): Client bootstrap errors, lookups, log follow,
and the command layer — every cmd_* driven through the real parser.

No real server anywhere — the Client's request layer is faked/stubbed.
"""
import copy
import io
import json
import os
import re

import pytest

from autowright import paths


# ---------------------------------------------------------------- Client boot

def test_client_exits_cleanly_when_backend_json_missing(home):
    from autowright import cli

    with pytest.raises(SystemExit) as ei:
        cli.Client()
    # sys.exit(str) → message is the exit code, printed to stderr — no traceback
    assert "backend isn't up" in str(ei.value.code)
    assert "no backend.json" in str(ei.value.code)


def test_client_exits_cleanly_on_stale_backend_json(home):
    # Staleness is detected by parse: a SIGKILL'd backend leaves a truncated
    # or garbage backend.json. (A well-formed file with a dead port passes the
    # constructor — that failure surfaces at request time, not here.)
    from autowright import cli, paths

    bj = paths.backend_json()
    bj.write_text('{"port": 51')  # truncated mid-write
    with pytest.raises(SystemExit) as ei:
        cli.Client()
    assert "stale or unreadable" in str(ei.value.code)

    bj.write_text(json.dumps({"pid": 999}))  # valid JSON, keys gone
    with pytest.raises(SystemExit) as ei:
        cli.Client()
    assert "stale or unreadable" in str(ei.value.code)


def test_client_reads_port_and_token_from_backend_json(home):
    from autowright import cli, paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    c = cli.Client()
    assert c.base == "http://127.0.0.1:5151"
    assert c.token == "tok"


def test_client_req_timeout_default_and_override(home, monkeypatch):
    """§20 HTTP timeouts: req runs at 30 s unless a call site overrides it."""
    from autowright import cli, paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    c = cli.Client()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    timeouts = []
    monkeypatch.setattr(cli._opener, "open",
                        lambda r, timeout: timeouts.append(timeout) or _Resp())
    c.req("GET", "/health")
    c.req("DELETE", "/automations/x", timeout=600)
    c.req_raw("GET", "/automations/x/export")
    assert timeouts == [30, 600, 30]


# ---------------------------------------------------------------- find_automation

class _ListClient:
    """Stub standing in for Client — find_automation only calls req GET /automations."""

    def __init__(self, autos):
        self.autos = autos

    def req(self, method, path, body=None):
        assert (method, path) == ("GET", "/automations")
        return self.autos


AUTOS = [
    {"id": "abc12345", "name": "Daily Report"},
    {"id": "def67890", "name": "Weekly Report"},
    {"id": "ghi00000", "name": "Backup"},
]


def test_find_auto_exact_id():
    from autowright.cli import find_automation

    assert find_automation(_ListClient(AUTOS), "abc12345")["name"] == "Daily Report"


def test_find_auto_exact_name_case_insensitive():
    from autowright.cli import find_automation

    assert find_automation(_ListClient(AUTOS), "dAiLy RePoRt")["id"] == "abc12345"


def test_find_auto_unique_substring():
    from autowright.cli import find_automation

    assert find_automation(_ListClient(AUTOS), "back")["id"] == "ghi00000"
    assert find_automation(_ListClient(AUTOS), "week")["id"] == "def67890"


def test_find_auto_ambiguous_substring_exits():
    from autowright.cli import find_automation

    with pytest.raises(SystemExit) as ei:
        find_automation(_ListClient(AUTOS), "report")  # Daily + Weekly both match
    msg = str(ei.value.code)
    # §20: ambiguity exits with the candidate list (names + ids)
    assert "ambiguous" in msg
    assert "Daily Report" in msg and "Weekly Report" in msg
    assert "abc12345" in msg and "def67890" in msg


def test_find_auto_duplicate_exact_names_exit():
    """§20: duplicate names are a designed state (§5.1 re-import creates a
    copy) — an exact-name reference matching two automations must exit with
    the candidate list, never silently operate on the first one."""
    from autowright.cli import find_automation

    autos = [{"id": "abc12345", "name": "Backup"}, {"id": "def67890", "name": "Backup"}]

    class _C:
        def req(self, *_a, **_k):
            return autos

    with pytest.raises(SystemExit) as ei:
        find_automation(_C(), "backup")
    msg = str(ei.value.code)
    assert "ambiguous" in msg and "abc12345" in msg and "def67890" in msg
    # the id still resolves uniquely
    assert find_automation(_C(), "def67890")["id"] == "def67890"


def test_find_auto_no_match_exits():
    from autowright.cli import find_automation

    with pytest.raises(SystemExit) as ei:
        find_automation(_ListClient([]), "zzz")
    assert "(none)" in str(ei.value.code)


def test_find_auto_unique_id_prefix():
    """§20: every short id the CLI prints (8-char `[abcd1234]` forms) must
    resolve back — id-prefix lookup, tried before name matching."""
    from autowright.cli import find_automation

    autos = [
        {"id": "abc12345-9a0b-4e4e-b5f2-e04cf88cba12", "name": "Daily Report"},
        {"id": "def67890-1c2d-4a5b-8e9f-a0b1c2d3e4f5", "name": "Weekly Report"},
    ]
    assert find_automation(_ListClient(autos), "abc12345")["name"] == "Daily Report"


def test_find_auto_ambiguous_id_prefix_exits():
    from autowright.cli import find_automation

    autos = [
        {"id": "abc12345-9a0b-4e4e-b5f2-e04cf88cba12", "name": "Daily Report"},
        {"id": "abc12399-1c2d-4a5b-8e9f-a0b1c2d3e4f5", "name": "Weekly Report"},
    ]
    with pytest.raises(SystemExit) as ei:
        find_automation(_ListClient(autos), "abc123")
    assert "ambiguous" in str(ei.value.code)


# ---------------------------------------------------------------- HTTP error format

def test_exit_http_prints_api_detail_not_raw_json():
    """§20: an HTTP error prints the API's detail message, never the raw body."""
    import io
    import urllib.error

    from autowright.cli import _exit_http

    err = urllib.error.HTTPError(
        "http://x", 422, "Unprocessable", {}, io.BytesIO(b'{"detail":"memory is empty"}'))
    with pytest.raises(SystemExit) as ei:
        _exit_http(err)
    assert str(ei.value.code) == "422: memory is empty"


def test_exit_http_renders_validation_list_detail():
    """§20: a list-shaped validation detail (the pydantic 422 form) prints as
    the first error's field path and message, never the raw JSON body."""
    import io
    import urllib.error

    from autowright.cli import _exit_http

    body = (b'{"detail":[{"type":"literal_error","loc":["body","notifications"],'
            b'"msg":"Input should be \'attention\' or \'all\'"}]}')
    err = urllib.error.HTTPError("http://x", 422, "Unprocessable", {}, io.BytesIO(body))
    with pytest.raises(SystemExit) as ei:
        _exit_http(err)
    assert str(ei.value.code) == "422: notifications: Input should be 'attention' or 'all'"

    # No usable loc: the message alone still beats raw JSON.
    body = b'{"detail":[{"loc":["body"],"msg":"boom"}]}'
    err = urllib.error.HTTPError("http://x", 422, "Unprocessable", {}, io.BytesIO(body))
    with pytest.raises(SystemExit) as ei:
        _exit_http(err)
    assert str(ei.value.code) == "422: boom"


def test_exit_http_falls_back_to_body_when_detail_not_string():
    import io
    import urllib.error

    from autowright.cli import _exit_http

    err = urllib.error.HTTPError("http://x", 500, "Server Error", {},
                                 io.BytesIO(b"plain text error"))
    with pytest.raises(SystemExit) as ei:
        _exit_http(err)
    assert "plain text error" in str(ei.value.code)


# ---------------------------------------------------------------- follow_exec

class _FollowClient:
    """Scripted two-poll client: overlapping exec-log seqs across polls, and
    one step attempt that is terminal from the first poll onward."""

    def __init__(self):
        self.poll = 0
        self.step_log_fetches = 0

    @staticmethod
    def _ln(seq, text):
        return {"sequence": seq, "time": f"T{seq}", "kind": "log", "text": text}

    def req(self, method, path, body=None):
        assert method == "GET"
        if path == "/executions/e1":
            self.poll += 1
            return {
                "status": "executing" if self.poll == 1 else "succeeded",
                "duration": "2s",
                "steps": [{"attempts": [{"number": 1, "status": "ok"}]}],  # terminal
            }
        if path == "/executions/e1/logs":
            if self.poll == 1:
                return {"lines": [self._ln(1, "alpha"), self._ln(2, "beta")]}
            # poll 2 re-serves seqs 1-2 plus the new 3 — dedupe must hold
            return {"lines": [self._ln(1, "alpha"), self._ln(2, "beta"),
                              self._ln(3, "gamma")]}
        if path == "/executions/e1/logs?step=0&attempt=1":
            self.step_log_fetches += 1
            return {"lines": [self._ln(1, "step line")]}
        raise AssertionError(f"unexpected request: {path}")


def test_follow_exec_dedupes_seqs_and_settles_terminal_attempts(monkeypatch, capsys):
    from autowright import cli

    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    c = _FollowClient()
    cli.follow_exec(c, "e1")
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "  T1 [log] alpha",
        "  T2 [log] beta",
        "  T1 [log] step line",
        "  T3 [log] gamma",          # only the new seq on poll 2
        "→ succeeded in 2s",
    ]
    assert out.count("  T1 [log] alpha") == 1  # overlapping seqs printed once
    # terminal attempt settled after its first fetch — never re-downloaded
    assert c.step_log_fetches == 1


class _StatusScriptClient:
    """Serves one scripted status per poll, with empty logs."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.polls = 0

    def req(self, method, path, body=None, timeout=30):
        if path == "/executions/e1":
            self.polls += 1
            return {"status": self.statuses[self.polls - 1], "duration": "1s", "steps": []}
        assert path == "/executions/e1/logs"
        return {"lines": []}


def test_follow_exec_polls_through_queued(monkeypatch, capsys):
    """§20 follow semantics: `queued` (§6 firing queue) is not terminal — the
    loop follows a queued firing through promotion to its real end."""
    from autowright import cli

    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    c = _StatusScriptClient(["queued", "queued", "executing", "succeeded"])
    assert cli.follow_exec(c, "e1") == "succeeded"
    assert c.polls == 4
    assert capsys.readouterr().out == "→ succeeded in 1s\n"


def test_follow_exec_queued_can_settle_skipped(monkeypatch, capsys):
    """A queued firing that never promotes settles as `skipped` — terminal."""
    from autowright import cli

    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    c = _StatusScriptClient(["queued", "skipped"])
    assert cli.follow_exec(c, "e1") == "skipped"
    assert c.polls == 2
    assert capsys.readouterr().out == "→ skipped in 1s\n"


# ---------------------------------------------------------------- workdir (§20)

# §4.8 fixture id: step entries and allowedSecrets reference secrets by uuid.
API_TOKEN_ID = "11111111-1111-1111-1111-111111111111"

FULL_AUTO = {
    "id": "abc12345-0000-0000-0000-000000000000", "name": "Daily Report",
    "description": "Reports daily", "instructions": "- keep it short",
    "spec": [{"kind": "h1", "text": "Daily Report"}, {"kind": "p", "text": "Fetch and report."}],
    "triggers": [
        {"id": "t1", "kind": "cron", "expression": "0 8 * * *", "enabled": False, "timezone": "Asia/Tokyo",
         "label": "Daily at 8:00 (Tokyo)", "short": "Daily 8:00 (Tokyo)"},
        {"id": "t2", "kind": "app_start", "enabled": True, "label": "On app start",
         "short": "App start"},
    ],
    "params": [{"name": "sources", "kind": "list", "label": "URLs", "default": [],
                "lines": ["https://a.example/x"]}],
    "packages": [],
    "steps": [{"file": "01-fetch.py", "name": "Fetch", "description": "fetch pages",
               "code": "import json\nprint('hi')\n",
               "secrets": [{"id": API_TOKEN_ID, "why": "authenticates the fetch"}]}],
    "stepAgents": [], "allowedSecrets": [API_TOKEN_ID],
}


class _WorkdirClient:
    """Recorded stub: GET answers from a small table, writes are captured."""

    def __init__(self, auto=None, install_result=None):
        self.auto = auto or FULL_AUTO
        self.posted = []
        self.timeouts = []  # (method, path, timeout) per write, parallel to posted
        self.install_result = install_result

    def req(self, method, path, body=None, timeout=30):
        if method == "GET" and path == "/automations":
            return [self.auto]
        if method == "GET" and path == f"/automations/{self.auto['id']}":
            return self.auto
        if method == "GET" and path == "/agents":
            return [{"id": "ag1", "name": "Fast local", "harness": "OpenCode",
                     "model": "qwen3"}]
        if method == "GET" and path == "/secrets":
            # §4.8: a list; id is the reference identity
            return [{"id": API_TOKEN_ID, "name": "API_TOKEN", "set": True, "usedBy": []}]
        self.posted.append((method, path, body))
        self.timeouts.append((method, path, timeout))
        if method == "POST" and path == "/packages/install":
            return {"packages": self.install_result or []}
        return {"version": 2, "executionId": "e9", "id": "new-id", "name": "Daily Report",
                "triggers": [{}], "triggerChip": "2 triggers"}


def test_workdir_pull_push_round_trip(tmp_path):
    """pull writes the §20 workdir; an untouched push round-trips: same spec
    blocks, same steps, and the stored trigger list unchanged (§4.3 merge keeps
    ids/off on matched crons and every non-cron trigger)."""
    from autowright import cli

    c = _WorkdirClient()
    d = tmp_path / "wd"
    written = cli.write_workdir(d, FULL_AUTO)
    assert set(written) == {"spec.md", "manifest.yaml", "01-fetch.py", "instructions.md"}
    assert (d / "spec.md").read_text().startswith("# Daily Report")

    draft = cli.validate_workdir(c, d)
    assert draft["spec"] == FULL_AUTO["spec"]
    assert draft["instructions"] == "- keep it short"
    assert [s["file"] for s in draft["steps"]] == ["01-fetch.py"]
    assert draft["steps"][0]["code"] == "import json\nprint('hi')\n"
    assert draft["steps"][0]["secrets"] == [{"id": API_TOKEN_ID, "why": "authenticates the fetch"}]

    merged = cli.merge_draft_triggers(FULL_AUTO["triggers"], draft["triggers"])
    assert {t["kind"] for t in merged} == {"cron", "app_start"}
    cron = next(t for t in merged if t["kind"] == "cron")
    assert cron["id"] == "t1" and cron["enabled"] is False  # matched: keeps id + enabled state


def test_workdir_pull_prunes_managed_files_it_did_not_write(tmp_path):
    """§20: pull owns the workdir's managed files — a step file the manifest no
    longer lists and a notes.md the version no longer carries are removed, so a
    re-pull never resurrects deleted content on the next push. Files pull
    doesn't manage are left alone."""
    from autowright import cli

    d = tmp_path / "wd"
    d.mkdir()
    (d / "03-old.py").write_text("print('dropped')\n")
    (d / "notes.md").write_text("stale notes\n")
    (d / "README.md").write_text("mine\n")

    written = cli.write_workdir(d, FULL_AUTO)
    assert "notes.md" not in written and "03-old.py" not in written
    assert not (d / "03-old.py").exists()
    assert not (d / "notes.md").exists()
    assert (d / "README.md").read_text() == "mine\n"      # unmanaged, untouched
    assert (d / "01-fetch.py").exists() and (d / "instructions.md").exists()


def test_workdir_notes_round_trip(tmp_path):
    """§20: pull writes notes.md when the automation has notes; push reads it
    back and saves it verbatim into the draft."""
    from autowright import cli

    auto = {**FULL_AUTO, "notes": "watch the rate limit"}
    d = tmp_path / "wd"
    written = cli.write_workdir(d, auto)
    assert set(written) == {"spec.md", "manifest.yaml", "01-fetch.py",
                            "instructions.md", "notes.md"}
    assert (d / "notes.md").read_text() == "watch the rate limit\n"

    draft = cli.validate_workdir(_WorkdirClient(auto), d)
    assert draft["notes"] == "watch the rate limit"

    c = _WorkdirClient(auto)
    _run(c, "automation", "push", "Daily Report", str(d))
    _method, path, body = c.posted[-1]
    assert path == f"/automations/{auto['id']}/versions"
    assert body["draft"]["notes"] == "watch the rate limit"

    # no notes → no notes.md written on pull
    d2 = tmp_path / "wd2"
    assert "notes.md" not in cli.write_workdir(d2, FULL_AUTO)
    assert not (d2 / "notes.md").exists()


def test_workdir_manifest_step_camel_flags_become_snake(tmp_path):
    """§20: the CLI pulls API step JSON (camelCase noTimeout/infiniteRetries)
    — the written manifest and the pushed draft carry the snake_case internal
    spellings."""
    import yaml

    from autowright import cli

    step = {**FULL_AUTO["steps"][0], "noTimeout": True, "infiniteRetries": True}
    auto = {**FULL_AUTO, "steps": [step]}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    assert manifest["steps"][0]["no_timeout"] is True
    assert manifest["steps"][0]["infinite_retries"] is True
    assert "noTimeout" not in manifest["steps"][0]
    assert "infiniteRetries" not in manifest["steps"][0]

    draft = cli.validate_workdir(_WorkdirClient(auto), d)
    assert draft["steps"][0]["no_timeout"] is True
    assert draft["steps"][0]["infinite_retries"] is True


def test_workdir_params_round_trip_without_values(tmp_path):
    """§20: pulled manifests carry definitions with defaults, never the
    resolved value fields — values are user-owned, set via `param set`."""
    import yaml

    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    assert manifest["params"] == [{"name": "sources", "kind": "list", "label": "URLs",
                                  "default": []}]
    assert manifest["triggers"] == [{"cron": "0 8 * * *", "timezone": "Asia/Tokyo"}]


def test_workdir_run_if_missed_round_trip(tmp_path):
    """§20/§4.3: an opted-out cron pulls as `run_if_missed: false`, the key is
    lifted out of the §8 dialect before validation and stamped back onto the
    drafted cron, and the merge keeps it on the matched stored entry. A cron
    that never opted out pulls without the key at all."""
    import yaml

    from autowright import cli

    auto = {**FULL_AUTO,
            "triggers": [{**FULL_AUTO["triggers"][0], "runIfMissed": False},
                         FULL_AUTO["triggers"][1]]}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    assert manifest["triggers"] == [{"cron": "0 8 * * *", "timezone": "Asia/Tokyo",
                                     "run_if_missed": False}]

    draft = cli.validate_workdir(_WorkdirClient(auto), d)
    drafted = next(t for t in draft["triggers"] if t["kind"] == "cron")
    assert drafted["runIfMissed"] is False
    merged = next(t for t in cli.merge_draft_triggers(auto["triggers"], draft["triggers"])
                  if t["kind"] == "cron")
    assert merged["id"] == "t1" and merged["runIfMissed"] is False

    # the default cron pulls in the pre-field shape: no key written
    d2 = tmp_path / "wd2"
    cli.write_workdir(d2, FULL_AUTO)
    assert "run_if_missed" not in yaml.safe_load((d2 / "manifest.yaml").read_text())["triggers"][0]


def test_lift_run_if_missed_strips_the_key_and_reports_misuse():
    """§20: `run_if_missed` is lifted out of the manifest's cron entries before
    the §8 rule-9 validator sees them - keyed by (expression, timezone) - and a
    manifest without the key passes through untouched."""
    import yaml

    from autowright import cli

    def files(triggers):
        return {"manifest.yaml": yaml.safe_dump(
            {"triggers": triggers, "steps": [{"file": "01-x.py", "name": "X"}]},
            sort_keys=False), "01-x.py": "print('x')\n"}

    plain = files([{"cron": "0 8 * * *"}])
    assert cli._lift_run_if_missed(plain) == (plain, set(), [])

    lifted, opted_out, errors = cli._lift_run_if_missed(files([
        {"cron": "0 8 * * *", "run_if_missed": False},
        {"cron": "0 9 * * *", "timezone": "Asia/Tokyo", "run_if_missed": False},
        {"cron": "0 10 * * *", "run_if_missed": True},
        {"cron": "0 11 * * *"}]))
    assert errors == []
    assert opted_out == {("0 8 * * *", None), ("0 9 * * *", "Asia/Tokyo")}
    manifest = yaml.safe_load(lifted["manifest.yaml"])
    assert all("run_if_missed" not in t for t in manifest["triggers"])
    assert [t["cron"] for t in manifest["triggers"]] == [
        "0 8 * * *", "0 9 * * *", "0 10 * * *", "0 11 * * *"]
    assert lifted["01-x.py"] == "print('x')\n"

    _, opted_out, errors = cli._lift_run_if_missed(files([
        {"cron": "0 8 * * *", "run_if_missed": "no"},
        {"app_start": True, "run_if_missed": False}]))
    assert errors == ["triggers: run_if_missed must be true or false",
                      "triggers: run_if_missed applies to cron entries only"]
    assert opted_out == set()


def test_validate_workdir_prints_errors_and_exits(tmp_path, capsys):
    from autowright import cli

    d = tmp_path / "wd"
    d.mkdir()
    (d / "spec.md").write_text("no title, just prose\n")
    (d / "manifest.yaml").write_text("steps:\n  - { file: 01-x.py, name: X }\n")
    (d / "01-x.py").write_text("import os\ndef broken(:\n")
    with pytest.raises(SystemExit) as ei:
        cli.validate_workdir(_WorkdirClient(), d)
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "must start with a # title" in err
    assert "syntax error" in err


def test_push_and_create_install_declared_packages(tmp_path, capsys):
    """§20: a successful push/create runs the §6.2 ensure for the declared
    packages and prints per-package status; a failure warns, the save stands."""
    from types import SimpleNamespace

    from autowright import cli

    auto = copy.deepcopy(FULL_AUTO)
    auto["packages"] = [{"pip": "pandas", "import": "pandas", "why": "builds the table"},
                        {"pip": "ghostlib", "import": "ghostlib", "why": "parses ghosts"}]
    result = [
        {"pip": "pandas", "import": "pandas", "status": "installed", "version": "2.2.0"},
        {"pip": "ghostlib", "import": "ghostlib", "status": "failed",
         "error": "no matching distribution"}]
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)

    c = _WorkdirClient(auto, install_result=result)
    cli.cmd_automation_push(c, SimpleNamespace(
        automation="Daily Report", dir=str(d), note=None,
        grant_agent=[], grant_secret=[]))
    out = capsys.readouterr().out
    assert "saved 'Daily Report' as v2" in out
    assert "package pandas 2.2.0 installed" in out
    assert "warning: package ghostlib failed to install — no matching distribution" in out
    _, _, body = next(p for p in c.posted if p[1] == "/packages/install")
    assert body == {"packages": auto["packages"]}
    # §20: pip runs behind the install call — 600 s; the save itself stays 30 s
    assert dict((p, t) for _, p, t in c.timeouts) == {
        f"/automations/{auto['id']}/versions": 30, "/packages/install": 600}

    c = _WorkdirClient(auto, install_result=result)
    cli.cmd_automation_create(c, SimpleNamespace(
        dir=str(d), name=None, agent=None,
        grant_agent=[], grant_secret=["API_TOKEN"]))
    out = capsys.readouterr().out
    assert "package pandas 2.2.0 installed" in out
    assert "warning: package ghostlib failed to install" in out
    assert any(p[1] == "/packages/install" for p in c.posted)


def test_push_without_packages_skips_install(tmp_path, capsys):
    """§20: no declared packages → no install call, nothing printed."""
    from types import SimpleNamespace

    from autowright import cli

    c = _WorkdirClient()
    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    cli.cmd_automation_push(c, SimpleNamespace(
        automation="Daily Report", dir=str(d), note=None,
        grant_agent=[], grant_secret=[]))
    assert not any(p[1] == "/packages/install" for p in c.posted)
    assert "package" not in capsys.readouterr().out


def test_import_installs_declared_packages(tmp_path, capsys):
    """§20: import runs the ensure for the summary's declared packages instead
    of deferring them to the first execution."""
    from types import SimpleNamespace

    from autowright import cli

    c = _WorkdirClient(install_result=[
        {"pip": "pandas", "import": "pandas", "status": "installed", "version": "2.2.0"}])
    c.req_raw = lambda method, path, data=None: json.dumps(
        {"automation": {"name": "Shared", "id": "abcd1234-0000"},
         "summary": {"packages": [{"pip": "pandas", "import": "pandas"}]}}).encode()
    f = tmp_path / "x.autowright"
    f.write_bytes(b"archive")
    cli.cmd_automation_import(c, SimpleNamespace(path=str(f)))
    out = capsys.readouterr().out
    assert "package pandas 2.2.0 installed" in out
    _, _, body = next(p for p in c.posted if p[1] == "/packages/install")
    assert body == {"packages": [{"pip": "pandas", "import": "pandas"}]}


def test_merge_draft_triggers_drops_unlisted_and_adds_new():
    from autowright import cli

    stored = [{"id": "t1", "kind": "cron", "expression": "0 8 * * *", "enabled": False},
              {"id": "t3", "kind": "time", "at": "2027-01-01T09:00", "enabled": True},
              {"id": "t4", "kind": "discord", "channel": "1", "secret": "S", "enabled": True}]
    drafted = [{"kind": "cron", "expression": "0 9 * * 1", "enabled": True}]
    merged = cli.merge_draft_triggers(stored, drafted)
    # one-shot and discord survive, the unlisted cron drops, the new cron arrives enabled
    assert [(t["kind"], t.get("expression") or t.get("at") or t.get("channel")) for t in merged] == [
        ("time", "2027-01-01T09:00"), ("discord", "1"), ("cron", "0 9 * * 1")]


def test_merge_draft_triggers_user_crons_survive():
    # §4.3 provenance: only spec-sourced crons are the replaceable subset — a
    # user-minted cron survives a push that no longer lists it, and a matched
    # user cron isn't duplicated.
    from autowright import cli

    stored = [{"id": "t1", "kind": "cron", "expression": "0 8 * * *", "enabled": True,
               "source": "spec"},
              {"id": "t2", "kind": "cron", "expression": "0 21 * * *", "enabled": False,
               "source": "user"}]
    merged = cli.merge_draft_triggers(
        stored, [{"kind": "cron", "expression": "0 9 * * *", "enabled": True, "source": "spec"}])
    assert [(t.get("id"), t["expression"]) for t in merged] == [
        ("t2", "0 21 * * *"), (None, "0 9 * * *")]
    # a drafted cron matching the user cron keeps the one stored entry
    merged = cli.merge_draft_triggers(
        stored, [{"kind": "cron", "expression": "0 21 * * *", "enabled": True, "source": "spec"}])
    assert [t.get("id") for t in merged if t["kind"] == "cron"] == ["t2"]


def test_merge_draft_triggers_message_entries_additive():
    from autowright import cli

    stored = [{"id": "t1", "kind": "imessage", "from": "+15551234567", "enabled": False},
              {"id": "t2", "kind": "discord", "channel": "1", "secret": "S", "enabled": True}]
    drafted = [{"kind": "imessage", "from": "+15551234567", "enabled": True},   # matches t1
               {"kind": "imessage", "from": "a@b.co", "enabled": True},          # new → adds
               {"kind": "discord", "channel": "1", "secret": "S", "enabled": True,
                "pattern": "go"},                                             # pattern differs → adds
               {"kind": "discord", "channel": "1", "secret": "S", "enabled": True,
                "author": ["777"]},                                           # author differs → adds
               {"kind": "time", "at": "2030-01-01T09:00", "enabled": True}]      # never drafted → dropped
    merged = cli.merge_draft_triggers(stored, drafted)
    assert [(t["kind"], t.get("id")) for t in merged] == [
        ("imessage", "t1"), ("discord", "t2"), ("imessage", None), ("discord", None),
        ("discord", None)]
    assert merged[0]["enabled"] is False  # matched entry keeps its enabled state


def test_merge_draft_triggers_takes_the_manifest_run_if_missed():
    """§20: the manifest entry decides `run_if_missed` on a matched cron - a
    drafted opt-out lands on a stored default, and a drafted entry without the
    key clears a stored opt-out (absent = true), while id/enabled/source stay."""
    from autowright import cli

    stored = [{"id": "t1", "kind": "cron", "expression": "0 8 * * *", "enabled": False,
               "source": "spec"}]
    drafted = [{"kind": "cron", "expression": "0 8 * * *", "enabled": True,
                "source": "spec", "runIfMissed": False}]
    merged = cli.merge_draft_triggers(stored, drafted)
    assert merged == [{"id": "t1", "kind": "cron", "expression": "0 8 * * *",
                       "enabled": False, "source": "spec", "runIfMissed": False}]
    # the other direction: the manifest dropped the key, so the opt-out goes
    merged = cli.merge_draft_triggers(
        [{**stored[0], "runIfMissed": False}],
        [{"kind": "cron", "expression": "0 8 * * *", "enabled": True, "source": "spec"}])
    assert merged == [dict(stored[0])]
    # a user cron survives the same way: matched once, with the drafted choice
    merged = cli.merge_draft_triggers([{**stored[0], "source": "user"}], drafted)
    assert merged == [{**stored[0], "source": "user", "runIfMissed": False}]


def test_trigger_add_discord():
    from types import SimpleNamespace

    from autowright import cli

    c = _WorkdirClient()
    # §20: --secret takes the NAME; the trigger stores the secret's §4.8 id
    cli.cmd_trigger_add(c, SimpleNamespace(
        automation="Daily Report", discord="123", secret="API_TOKEN",
        pattern="go", mention=True, author=["777,888", "999"], imessage=None,
        app_start=False, at=None, expression=None, timezone=None))
    method, path, body = c.posted[-1]
    assert (method, path) == ("PATCH", f"/automations/{FULL_AUTO['id']}")
    # repeated --author flags and comma-separated values collect into one list
    assert body["triggers"][-1] == {"kind": "discord", "channel": "123",
                                    "secret": API_TOKEN_ID, "pattern": "go",
                                    "mention": True, "author": ["777", "888", "999"],
                                    "enabled": True}
    # --discord without --secret exits with guidance, nothing sent
    with pytest.raises(SystemExit):
        cli.cmd_trigger_add(c, SimpleNamespace(
            automation="Daily Report", discord="123", secret=None, pattern=None,
            mention=False, author=None, imessage=None, app_start=False,
            at=None, expression=None, timezone=None))
    # an unknown secret name exits with the candidate list, nothing sent
    sent_before = len(c.posted)
    with pytest.raises(SystemExit, match="no stored secret named"):
        cli.cmd_trigger_add(c, SimpleNamespace(
            automation="Daily Report", discord="123", secret="NOPE", pattern=None,
            mention=False, author=None, imessage=None, app_start=False,
            at=None, expression=None, timezone=None))
    assert len(c.posted) == sent_before


def test_trigger_add_imessage():
    from types import SimpleNamespace

    from autowright import cli

    c = _WorkdirClient()
    cli.cmd_trigger_add(c, SimpleNamespace(
        automation="Daily Report", discord=None, secret=None,
        pattern="deploy", mention=False, imessage="+15551234567",
        app_start=False, at=None, expression=None, timezone=None))
    method, path, body = c.posted[-1]
    assert (method, path) == ("PATCH", f"/automations/{FULL_AUTO['id']}")
    assert body["triggers"][-1] == {"kind": "imessage", "from": "+15551234567",
                                    "pattern": "deploy", "enabled": True}


def test_trigger_add_no_run_if_missed():
    """§20 --no-run-if-missed: the §4.3 opt-out rides a cron or an --at
    one-shot only; every other kind exits before anything is sent, and an args
    object without the flag adds no key at all."""
    from types import SimpleNamespace

    from autowright import cli

    def args(**over):
        return SimpleNamespace(**{"automation": "Daily Report", "discord": None,
                                  "secret": None, "pattern": None, "mention": False,
                                  "author": None, "imessage": None, "app_start": False,
                                  "at": None, "expression": None, "timezone": None,
                                  **over})

    c = _WorkdirClient()
    cli.cmd_trigger_add(c, args(expression="0 8 * * *", no_run_if_missed=True))
    assert c.posted[-1][2]["triggers"][-1] == {
        "kind": "cron", "expression": "0 8 * * *", "enabled": True, "source": "user",
        "runIfMissed": False}
    cli.cmd_trigger_add(c, args(at="2999-01-01T09:00", no_run_if_missed=True))
    assert c.posted[-1][2]["triggers"][-1] == {
        "kind": "time", "at": "2999-01-01T09:00", "enabled": True, "runIfMissed": False}
    # the flag off, and an args object that never carries it, both add no key
    cli.cmd_trigger_add(c, args(expression="0 8 * * *", no_run_if_missed=False))
    assert "runIfMissed" not in c.posted[-1][2]["triggers"][-1]
    cli.cmd_trigger_add(c, args(expression="0 8 * * *"))
    assert "runIfMissed" not in c.posted[-1][2]["triggers"][-1]

    # cron/time only: every other kind exits with guidance, nothing sent
    sent_before = len(c.posted)
    for over in (dict(app_start=True),
                 dict(discord="123", secret="API_TOKEN"),
                 dict(imessage="+15551234567")):
        with pytest.raises(SystemExit, match="--no-run-if-missed applies"):
            cli.cmd_trigger_add(c, args(no_run_if_missed=True, **over))
    assert len(c.posted) == sent_before


# ---------------------------------------------------------------- param parsing

@pytest.mark.parametrize("kind,raw,expected", [
    ("toggle", "on", True), ("toggle", "false", False),
    ("number", "42", 42),
    ("text", "hello there", "hello there"),
    ("list", "a, b, c", ["a", "b", "c"]),
    ("list", '["x", "y"]', ["x", "y"]),
    ("kv", "k1=v1,k2=v2", [{"key": "k1", "value": "v1"}, {"key": "k2", "value": "v2"}]),
    ("kv", '{"k1": "v1"}', [{"key": "k1", "value": "v1"}]),
])
def test_parse_param_value(kind, raw, expected):
    from autowright import cli

    assert cli.parse_param_value({"name": "p", "kind": kind}, raw) == expected


@pytest.mark.parametrize("kind,raw", [
    ("toggle", "maybe"), ("number", "ten"), ("list", '[1, 2]'), ("kv", "novalue"),
])
def test_parse_param_value_rejects_bad_input(kind, raw):
    from autowright import cli

    with pytest.raises(SystemExit):
        cli.parse_param_value({"name": "p", "kind": kind}, raw)


# ---------------------------------------------------------------- exit codes

def test_followed_execution_failure_exits_2(monkeypatch, capsys):
    """§20: `automation execute -f` / `execution tail` exit 2 when the followed
    execution ends other than succeeded — harnesses branch on the code."""
    from autowright import cli

    class _FailClient:
        def req(self, method, path, body=None):
            if path == "/executions/e1":
                return {"status": "failed", "duration": "3s", "steps": []}
            return {"lines": []}

    with pytest.raises(SystemExit) as ei:
        cli._exit_by_status(cli.follow_exec(_FailClient(), "e1"))
    assert ei.value.code == 2
    assert "→ failed in 3s" in capsys.readouterr().out


def test_usage_errors_exit_1_not_2():
    """§20: 2 is exclusively the follow-failure signal, so argparse's own
    usage-error exit code (2) is overridden: usage errors take the ordinary
    error exit with the message on stderr."""
    import contextlib

    from autowright import cli

    for argv in (["nosuchcommand"],
                 ["automation", "nosuchverb"],         # unknown verb
                 ["automation", "show"],               # missing positional
                 ["automation", "list", "--nope"],     # unknown flag
                 ["service", "frobnicate"]):           # bad choice
        with pytest.raises(SystemExit) as ei, contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser(full=True).parse_args(argv)
        # a str code exits 1 and prints itself on stderr; 2 must never appear
        assert isinstance(ei.value.code, str), f"{argv} exited {ei.value.code!r}"
        assert "autowright" in ei.value.code

    # --help still exits 0 through argparse's own path
    with pytest.raises(SystemExit) as ei, contextlib.redirect_stdout(io.StringIO()):
        cli.build_parser(full=True).parse_args(["--help"])
    assert ei.value.code == 0


def test_follow_exec_ctrl_c_exits_cleanly(monkeypatch, capsys):
    """§20: Ctrl-C while following exits 1 with a plain line, never a
    KeyboardInterrupt traceback, and never 2."""
    from autowright import cli

    class _InterruptClient:
        def req(self, method, path, body=None, timeout=30):
            if path == "/executions/e1":
                return {"status": "executing", "duration": "1s", "steps": []}
            return {"lines": []}

    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", boom)
    with pytest.raises(SystemExit) as ei:
        cli.follow_exec(_InterruptClient(), "e1")
    assert ei.value.code != 2
    assert "interrupted" in str(ei.value.code)
    assert "still running" in str(ei.value.code)


def test_client_exits_cleanly_when_backend_port_is_dead(home):
    """§3: a well-formed backend.json pointing at a dead backend (SIGKILL
    leftovers) exits with restart guidance at request time — never a traceback."""
    import socket

    from autowright import cli, paths

    # A port that was just bound and released: connection refused, instantly.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    paths.backend_json().write_text(json.dumps({"port": port, "token": "tok"}))
    c = cli.Client()
    with pytest.raises(SystemExit) as ei:
        c.req("GET", "/automations")
    assert "backend isn't reachable" in str(ei.value.code)
    assert "service restart" in str(ei.value.code)
    with pytest.raises(SystemExit) as ei:
        c.req_raw("GET", "/automations/x/export")
    assert "backend isn't reachable" in str(ei.value.code)


def test_client_exits_cleanly_when_the_backend_drops_the_connection(home, monkeypatch):
    """§3/§20: a backend that dies mid-request — a half-closed socket
    (RemoteDisconnected) or a reset one — is the same clean restart guidance as
    an unreachable one, never a traceback out of http.client."""
    import http.client

    from autowright import cli, paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    c = cli.Client()
    for err in (http.client.RemoteDisconnected("Remote end closed connection"),
                ConnectionResetError(54, "Connection reset by peer")):
        def dies(*a, e=err, **k):
            raise e

        monkeypatch.setattr(cli._opener, "open", dies)
        with pytest.raises(SystemExit) as ei:
            c.req("GET", "/automations")
        assert "backend isn't reachable" in str(ei.value.code)
        assert "service restart" in str(ei.value.code)
        with pytest.raises(SystemExit) as ei:
            c.req_raw("GET", "/automations/x/export")
        assert "backend isn't reachable" in str(ei.value.code)


# ---------------------------------------------------------------- find_execution

class _ExecListClient:
    def __init__(self, execs):
        self.execs = execs

    def req(self, method, path, body=None):
        assert (method, path) == ("GET", "/executions")
        return {"executions": self.execs, "total": len(self.execs)}


EXECS = [{"id": "e1111111-a", "automationName": "Daily Report", "status": "succeeded",
          "started": "2026-07-29 08:00"},
         {"id": "e2222222-b", "automationName": "Weekly Report", "status": "failed",
          "started": "2026-07-28 09:00"},
         {"id": "f3333333-c", "automationName": "Backup", "status": "executing",
          "started": "2026-07-27 10:00"}]


def test_find_execution_defaults_to_latest():
    from autowright.cli import find_execution

    assert find_execution(_ExecListClient(EXECS), None)["id"] == "e1111111-a"


def test_find_execution_no_executions_exits():
    from autowright.cli import find_execution

    with pytest.raises(SystemExit) as ei:
        find_execution(_ExecListClient([]), None)
    assert "no execution found" in str(ei.value.code)


def test_find_execution_by_unique_prefix_and_ambiguity():
    from autowright.cli import find_execution

    assert find_execution(_ExecListClient(EXECS), "f3")["id"] == "f3333333-c"
    with pytest.raises(SystemExit) as ei:
        find_execution(_ExecListClient(EXECS), "e")  # two matches
    msg = str(ei.value.code)
    assert "no unique execution" in msg
    # §20: ambiguity exits with the candidate list — id prefix, automation,
    # status, started time for every execution
    assert "e1111111 (Daily Report, succeeded, 2026-07-29 08:00)" in msg
    assert "e2222222 (Weekly Report, failed, 2026-07-28 09:00)" in msg
    with pytest.raises(SystemExit) as ei:
        find_execution(_ExecListClient([]), "e")
    assert "(none)" in str(ei.value.code)


# ---------------------------------------------------------------- command layer

class _RouteClient:
    """Routing stub: GET answers come from a table (deep-copied so commands
    that mutate records can't leak across tests); writes are recorded and
    answered with `reply`; req_raw returns `raw` bytes."""

    base = "http://127.0.0.1:5151"

    def __init__(self, gets=None, reply=None, raw=b""):
        self.gets = {k: copy.deepcopy(v) for k, v in (gets or {}).items()}
        self.reply, self.raw = reply or {}, raw
        self.calls = []
        self.timeouts = []  # (method, path, timeout) per write, parallel to calls

    def req(self, method, path, body=None, timeout=30):
        if method == "GET":
            assert path in self.gets, f"unexpected GET {path}"
            return self.gets[path]
        self.calls.append((method, path, body))
        self.timeouts.append((method, path, timeout))
        return self.reply

    def req_raw(self, method, path, data=None):
        self.calls.append((method, path, data))
        return self.raw


AUTO_ID = FULL_AUTO["id"]


def _auto_gets(auto=None, **extra):
    a = auto or FULL_AUTO
    return {"/automations": [a], f"/automations/{a['id']}": a, **extra}


def _run(client, *argv):
    """Parse real argv through build_parser and dispatch — covers the parser
    wiring and the command in one go."""
    from autowright import cli

    args = cli.build_parser(full=True).parse_args(list(argv))
    args.fn(client, args)
    return args


def test_parser_marks_service_as_clientless():
    from autowright import cli

    assert cli.build_parser(full=True).parse_args(["service", "status"]).client is False
    assert cli.build_parser(full=True).parse_args(["status"]).client is True


def test_parser_accepts_service_stop_in_both_shapes():
    # §20/§3: `service stop` (the quit-entirely backend half) is part of the
    # verb set in the full parser and the service-only parser alike.
    from autowright import cli

    assert cli.build_parser(full=True).parse_args(["service", "stop"]).action == "stop"
    assert cli.build_parser(full=False).parse_args(["service", "stop"]).action == "stop"


def test_shipped_cli_exposes_the_full_surface():
    """§20: the CLI is enabled — the parser main() ships is the full one."""
    from autowright import cli

    assert cli.CLI_ENABLED is True
    ap = cli.build_parser()  # the parser main() actually ships
    assert ap.parse_args(["service", "status"]).client is False
    assert ap.parse_args(["automation", "list"]).client is True
    assert ap.parse_args(["secret", "list"]).client is True


def test_every_command_documents_itself():
    """§20 help text: every parser in the tree carries a description (the prose
    `<command> --help` prints), and every positional and option carries help
    naming what it accepts — `--help` is the CLI's own documentation, so a new
    command must not ship bare."""
    import argparse

    from autowright import cli

    no_description, no_help = [], []

    def walk(p, path):
        if not p.description:
            no_description.append(path)
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for name, sub in a.choices.items():
                    walk(sub, f"{path} {name}")
            elif not isinstance(a, argparse._HelpAction) and not a.help:
                no_help.append(f"{path} <{a.dest}>")

    walk(cli.build_parser(full=True), "autowright")
    assert not no_description, f"commands with no description: {no_description}"
    assert not no_help, f"arguments with no help: {no_help}"


def _help_for(*argv) -> str:
    import contextlib

    from autowright import cli

    out = io.StringIO()
    with pytest.raises(SystemExit), contextlib.redirect_stdout(out):
        cli.build_parser(full=True).parse_args([*argv, "--help"])
    # 3.14's argparse colorizes help; assert on the plain text
    return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())


def test_bare_invocation_prints_help_and_succeeds(monkeypatch, capsys):
    """§20 bare invocation: `autowright` with no command prints the full help
    and exits 0 — not a usage error, and not a one-line usage stub. The same at
    every level, and without ever needing a reachable backend."""
    monkeypatch.setenv("COLUMNS", "100")

    from autowright import cli

    # 3.14's argparse colorizes help; compare on the plain text
    plain = re.compile(r"\x1b\[[0-9;]*m")

    for argv in ([], ["automation"], ["automation", "trigger"], ["secret"]):
        args = cli.build_parser(full=True).parse_args(argv)
        # never builds a Client: `autowright` must answer with the backend down
        assert args.client is False, argv
        args.fn(None, args)
        out = plain.sub("", capsys.readouterr().out)
        assert out.startswith("usage: autowright"), argv
        assert ("COMMANDS:" in out or "VERBS:" in out), argv

    # the top-level bare help is the same text --help prints
    bare = cli.build_parser(full=True)
    bare.parse_args([]).fn(None, None)
    assert plain.sub("", capsys.readouterr().out) == plain.sub("", bare.format_help())


def test_command_listing_expands_every_flag(monkeypatch):
    """§20 expanded command listings: a group's `--help` prints each verb's full
    signature and one row per argument, generated from the parser tree — the
    flags of a command are readable without running its own --help."""
    monkeypatch.setenv("COLUMNS", "100")
    text = _help_for("automation")

    assert "VERBS:" in text
    # the signature carries positionals in <> and every flag in []
    assert "push <automation> <dir> [--note TEXT]" in text
    assert "[--grant-agent NAME]... [--grant-secret NAME]..." in text
    # ...and each argument gets its own row with its own help
    assert "--grant-secret NAME" in text
    assert "the workdir to validate and save" in text
    # a repeatable flag says so; a defaulted positional is bracketed
    assert "pull <automation> [<dir>]" in text
    assert "Run `autowright automation <verb> --help`" in text

    # §20: a blank line and a label separate the prose from the rows, and the
    # label names which kind of block it is — a command's arguments, a group's
    # verbs — with the rows indented past it
    assert "\n\n      arguments:\n        <automation>  " in text
    assert "\n\n      verbs:\n        list  " in text  # `param`, a group inside a group


def test_listing_parsers_end_at_the_listing(monkeypatch):
    """§20: a parser holding subcommands shows its listing and stops — no
    `options:` section holding nothing but -h, and no epilog above a listing the
    reader hasn't reached yet. A command's own --help still shows its options,
    because there the section carries its real flags."""
    monkeypatch.setenv("COLUMNS", "100")

    for argv in ([], ["automation"], ["automation", "trigger"], ["execution"]):
        text = _help_for(*argv)
        assert "options:" not in text, argv
        assert "-h, --help" not in text, argv
        assert text.rstrip().endswith("examples."), argv  # the closing pointer line

    # a leaf keeps both
    leaf = _help_for("automation", "execute")
    assert "options:" in leaf and "-h, --help" in leaf
    assert "Examples:" in leaf


def test_command_listing_names_the_level_below_without_expanding_it(monkeypatch):
    """§20: a subcommand that is itself a group is listed by its verb names, so
    each level prints in full and names the next instead of recursing."""
    monkeypatch.setenv("COLUMNS", "100")
    top, group = _help_for(), _help_for("automation")

    # `automation` is a group at the top level: verbs by name, not expanded
    assert "list every automation on this machine" in top
    assert "push <automation> <dir>" not in top
    # one level down, those same verbs are expanded in full
    assert "push <automation> <dir>" in group
    # and `trigger` (a group inside a group) is named, not expanded again
    assert "trigger" in group
    assert "add <automation> [<cron>]" not in group
    assert "add <automation> [<cron>]" in _help_for("automation", "trigger")


def test_example_epilogs_keep_their_line_breaks(monkeypatch):
    """§20 help text: the Help formatter wraps prose but leaves an `Examples:`
    epilog line-for-line, so the example commands stay one per line."""
    import contextlib

    from autowright import cli

    # argparse wraps to the live terminal width; pin it like the sibling
    # help-text tests so a wide terminal can't turn the wrap check flaky.
    monkeypatch.setenv("COLUMNS", "100")
    out = io.StringIO()
    with pytest.raises(SystemExit), contextlib.redirect_stdout(out):
        cli.build_parser(full=True).parse_args(["automation", "trigger", "add", "--help"])
    text = out.getvalue()
    assert '  autowright automation trigger add report "0 8 * * *"\n' in text
    # the description above it is still wrapped, not left as one long line
    assert max(len(line) for line in text.splitlines()) < 200


def test_disabled_parser_shape_exposes_only_service():
    """The CLI_ENABLED=False shape stays testable: only `service` registers."""
    import contextlib

    from autowright import cli

    ap = cli.build_parser(full=False)
    assert ap.parse_args(["service", "status"]).client is False

    for argv in (["status"], ["automation", "list"], ["secret", "list"],
                 ["secret", "set", "TOKEN"], ["execution", "list"],
                 ["automation", "execute", "x"], ["settings", "show"],
                 ["agent", "list"], ["instructions"]):
        with pytest.raises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            ap.parse_args(argv)


def test_cmd_status_prints_counts_and_json(capsys):
    gets = {"/health": {"version": "1.2.3"},
            "/state": {"automations": [1], "executions": [1, 2], "agents": [], "secrets": [1],
                       "pendingDraft": {"name": "New thing"}}}
    _run(_RouteClient(gets), "status")
    out = capsys.readouterr().out
    assert "backend 1.2.3 at http://127.0.0.1:5151" in out
    assert "1 automations · 2 executions · 0 agents · 1 secrets" in out
    assert "pending create draft: New thing" in out

    _run(_RouteClient(gets), "status", "--json")
    assert json.loads(capsys.readouterr().out)["version"] == "1.2.3"


def test_cmd_instructions_prints_framework_text(capsys):
    gets = {"/instructions": {"framework": "## Contract\nrules here"}}
    _run(_RouteClient(gets), "instructions")
    assert "## Contract" in capsys.readouterr().out
    _run(_RouteClient(gets), "instructions", "--json")
    assert json.loads(capsys.readouterr().out) == {"framework": "## Contract\nrules here"}


def test_cmd_automation_list_row_format_and_json(capsys):
    autos = [{"id": "abc12345-x", "name": "Daily Report", "triggerChip": "Daily 8:00",
              "allTriggersOff": True, "lastStatus": "succeeded", "resultChip": "3 new"}]
    _run(_RouteClient({"/automations": autos}), "automation", "list")
    out = capsys.readouterr().out
    assert "Daily Report" in out and "Daily 8:00 (off)" in out
    assert "succeeded" in out and "3 new" in out and "[abc12345]" in out

    _run(_RouteClient({"/automations": autos}), "automation", "list", "--json")
    assert json.loads(capsys.readouterr().out) == autos


def test_cmd_automation_list_marks_needs_fixing(capsys):
    """§20 needs-fixing parity: a non-empty §4.1 `problems` list puts a plain
    `needs fixing` marker after the status column, before the result chip."""
    broken = {"id": "abc12345-x", "name": "Daily Report", "triggerChip": "Daily 8:00",
              "lastStatus": "failed", "resultChip": "0 new",
              "problems": [{"kind": "secret", "label": "API_TOKEN has no value"}]}
    clean = dict(broken, id="def67890-x", name="Fine One", problems=[])
    _run(_RouteClient({"/automations": [broken, clean]}), "automation", "list")
    rows = capsys.readouterr().out.splitlines()
    assert "needs fixing" in rows[0]
    assert rows[0].index("needs fixing") < rows[0].index("0 new")  # before the chip
    assert "needs fixing" not in rows[1]  # an empty problems list marks nothing

    # --json carries the serialized field untouched
    _run(_RouteClient({"/automations": [broken]}), "automation", "list", "--json")
    assert json.loads(capsys.readouterr().out)[0]["problems"] == broken["problems"]


def test_cmd_automation_show_prints_problems_block(capsys):
    """§20: `automation show` prints a `needs fixing:` block, one indented line
    per §4.1 problem label, in order."""
    full = dict(FULL_AUTO, specMeta="v2 · edited today", lastStatus="failed",
                problems=[{"kind": "secret", "label": "API_TOKEN has no value"},
                          {"kind": "agent", "label": "Fast local needs setup"}])
    gets = {**_auto_gets(full),
            "/secrets": [{"id": API_TOKEN_ID, "name": "API_TOKEN", "set": False,
                          "usedBy": []}]}
    _run(_RouteClient(gets), "automation", "show", "Daily Report")
    out = capsys.readouterr().out.splitlines()
    i = out.index("needs fixing:")
    assert out[i + 1] == "  API_TOKEN has no value"
    assert out[i + 2] == "  Fast local needs setup"

    # no problems → no block at all
    clean = dict(full, problems=[])
    _run(_RouteClient({**_auto_gets(clean), "/secrets": gets["/secrets"]}),
         "automation", "show", "Daily Report")
    assert "needs fixing" not in capsys.readouterr().out


def test_cmd_automation_show_prints_record(capsys):
    full = dict(FULL_AUTO, specMeta="v2 · edited today", lastStatus="failed",
                resultChip="0 new", versions=[{"version": 1}, {"version": 2}], draft={"x": 1},
                triggers=[{"kind": "cron", "label": "Daily at 8:00", "enabled": False}])
    gets = {**_auto_gets(full),
            # §4.1: step secret entries carry ids — show resolves them to names
            "/secrets": [{"id": API_TOKEN_ID, "name": "API_TOKEN", "set": True, "usedBy": []}]}
    _run(_RouteClient(gets), "automation", "show", "Daily Report")
    out = capsys.readouterr().out
    assert f"Daily Report [{AUTO_ID}] — v2 · edited today" in out
    assert "status: failed · 0 new" in out
    assert "trigger 1: Daily at 8:00 (off)" in out
    assert "param sources (list): ['https://a.example/x']" in out
    assert "step 1: Fetch [secrets: API_TOKEN]" in out
    assert "history: v1, v2" in out
    assert "has an unsaved draft" in out


def test_cmd_automation_delete_needs_yes(capsys):
    c = _RouteClient(_auto_gets())
    with pytest.raises(SystemExit) as ei:
        _run(c, "automation", "delete", "Daily Report")
    assert "--yes" in str(ei.value.code)
    assert c.calls == []  # nothing sent without confirmation

    _run(c, "automation", "delete", "Daily Report", "--yes")
    assert c.calls == [("DELETE", f"/automations/{AUTO_ID}", None)]
    # §20: delete waits for cancelled engine threads — long 600 s timeout
    assert c.timeouts == [("DELETE", f"/automations/{AUTO_ID}", 600)]
    assert "deleted 'Daily Report'" in capsys.readouterr().out


def test_cmd_automation_restore_parses_vN(capsys):
    c = _RouteClient(_auto_gets(), reply={"version": 5})
    _run(c, "automation", "restore", "Daily Report", "v3")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/restore", {"version": 3})]
    assert "restored v3 of 'Daily Report' as v5" in capsys.readouterr().out

    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets()), "automation", "restore", "Daily Report", "latest")
    assert "version must be vN" in str(ei.value.code)


def test_cmd_automation_execute_posts_manual_trigger(capsys):
    c = _RouteClient(_auto_gets(), reply={"executionId": "e9"})
    _run(c, "automation", "execute", "Daily Report")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/execute", {"trigger": "manual"})]
    assert "started — execution e9" in capsys.readouterr().out

    c = _RouteClient(_auto_gets(), reply={"executionId": "e9"})
    _run(c, "automation", "execute", "Daily Report", "--version", "draft")
    assert c.calls[-1][2] == {"trigger": "manual", "version": "draft"}


def test_cmd_automation_execute_queue_flag(capsys):
    """§20: `--queue` forwards the §19 `queue: true` field, and a queued reply
    says so instead of claiming the execution started."""
    c = _RouteClient(_auto_gets(), reply={"executionId": "e9", "queued": True})
    _run(c, "automation", "execute", "Daily Report", "--queue")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/execute",
                        {"trigger": "manual", "queue": True})]
    assert "queued — execution e9 (waiting for a free slot)" in capsys.readouterr().out

    # without the flag the body carries no queue key: a busy automation stays a
    # plain 409 refusal
    c = _RouteClient(_auto_gets(), reply={"executionId": "e9"})
    _run(c, "automation", "execute", "Daily Report")
    assert "queue" not in c.calls[-1][2]


def test_cmd_automation_export_writes_archive(tmp_path, capsys):
    c = _RouteClient(_auto_gets(), raw=b"ZIPDATA")
    out_file = tmp_path / "out.autowright"
    _run(c, "automation", "export", "Daily Report", str(out_file), "--no-values")
    assert c.calls == [("GET", f"/automations/{AUTO_ID}/export?values=0", None)]
    assert out_file.read_bytes() == b"ZIPDATA"
    assert "exported 'Daily Report'" in capsys.readouterr().out


def test_cmd_automation_export_default_filename(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    c = _RouteClient(_auto_gets(), raw=b"Z")
    _run(c, "automation", "export", "Daily Report")
    assert c.calls == [("GET", f"/automations/{AUTO_ID}/export", None)]  # values kept
    assert (tmp_path / "Daily Report.autowright").read_bytes() == b"Z"


def test_cmd_automation_import_prints_summary(tmp_path, capsys):
    """§20/§5.1: the match lines name the archive record, adding "-> local"
    only when the match renamed and "(needs setup)" for a not-ready agent; the
    no-match line lists the §4.1 unresolved references, and the needs-attention
    line closes the summary after the package ensure."""
    src = tmp_path / "in.autowright"
    src.write_bytes(b"ARCHIVE")
    raw = json.dumps({
        "automation": {"name": "Imported 2", "id": "deadbeef-1"},
        "summary": {
            "secretsMatched": [
                {"name": "API_KEY", "matchedTo": "API_KEY", "matchedBy": "name"},
                {"name": "STRIPE_KEY", "matchedTo": "STRIPE_API_KEY",
                 "matchedBy": "similarity"}],
            "agentsMatched": [
                {"name": "Researcher", "matchedTo": "Claude",
                 "matchedBy": "configuration", "ready": False},
                {"name": "Coder", "matchedTo": "Coder", "matchedBy": "name",
                 "ready": True}],
            "unresolved": [{"kind": "secret", "name": "MAIL_PASS", "description": ""},
                           {"kind": "agent", "name": "Ghost", "description": ""}],
            "packages": [{"pip": "requests"}],
            "renamedFrom": "Imported"},
    }).encode()
    c = _RouteClient(raw=raw, reply={"packages": [
        {"pip": "requests", "import": "requests", "status": "installed",
         "version": "2.32.3"}]})
    _run(c, "automation", "import", str(src))
    assert c.calls == [
        ("POST", "/automations/import", b"ARCHIVE"),
        ("POST", "/packages/install", {"packages": [{"pip": "requests"}]}),
    ]
    out = capsys.readouterr().out
    assert "imported 'Imported 2' [deadbeef]" in out
    # §5.1 name dedupe surfaces in the summary lines
    assert "renamed from 'Imported' - that name already exists" in out
    assert "  secrets matched: API_KEY, STRIPE_KEY -> STRIPE_API_KEY" in out
    assert "  agents matched: Researcher -> Claude (needs setup), Coder" in out
    assert "  no match on this machine: secret MAIL_PASS, agent Ghost" in out
    assert "package requests 2.32.3 installed" in out
    attention = "  this automation needs attention - open it and fix the highlighted agents and secrets"
    assert attention in out
    # §20 order: the needs-attention line lands after the ensure, before the
    # triggers-off line
    assert (out.index("package requests 2.32.3 installed")
            < out.index(attention)
            < out.index("triggers imported off"))


def test_cmd_automation_import_clean_summary_stays_quiet(tmp_path, capsys):
    """§20: nothing matched and nothing unresolved → no match lines, and no
    needs-attention line."""
    src = tmp_path / "in.autowright"
    src.write_bytes(b"ARCHIVE")
    raw = json.dumps({
        "automation": {"name": "Clean", "id": "deadbeef-1"},
        "summary": {"secretsMatched": [], "agentsMatched": [], "unresolved": [],
                    "packages": [], "renamedFrom": None},
    }).encode()
    _run(_RouteClient(raw=raw), "automation", "import", str(src))
    out = capsys.readouterr().out
    assert "imported 'Clean' [deadbeef]" in out
    assert "matched:" not in out
    assert "no match on this machine" not in out
    assert "needs attention" not in out
    assert "triggers imported off" in out


def test_cmd_automation_import_reports_os_mismatch(tmp_path, capsys):
    """§5.1/§20: an archive exported on another platform prints the warning line
    naming the origin platform the §4.1 display way ("Windows", never the raw
    "windows" token — the CLI and the UI name a platform alike); a matching
    archive prints nothing."""
    src = tmp_path / "in.autowright"
    src.write_bytes(b"ARCHIVE")
    summary = {"secretsMatched": [], "agentsMatched": [], "unresolved": [],
               "packages": [], "osMismatch": True, "os": "windows"}
    raw = json.dumps({"automation": {"name": "Ported", "id": "deadbeef-1"},
                      "summary": summary}).encode()
    _run(_RouteClient(raw=raw), "automation", "import", str(src))
    assert ("built on Windows - its steps may need rewriting on this machine"
            in capsys.readouterr().out)

    # an unrecognized token has no display form — it prints verbatim (§4.1)
    odd = json.dumps({"automation": {"name": "Ported", "id": "deadbeef-1"},
                      "summary": {**summary, "os": "beos"}}).encode()
    _run(_RouteClient(raw=odd), "automation", "import", str(src))
    assert "built on beos - " in capsys.readouterr().out

    same = json.dumps({"automation": {"name": "Ported", "id": "deadbeef-1"},
                       "summary": {**summary, "osMismatch": False}}).encode()
    _run(_RouteClient(raw=same), "automation", "import", str(src))
    assert "may need rewriting" not in capsys.readouterr().out


def test_cmd_automation_export_unwritable_path_exits_1(tmp_path, capsys):
    """§20: an unwritable output path is a plain message on stderr and exit 1,
    never a bare OSError traceback out of `open`."""
    c = _RouteClient(_auto_gets(), raw=b"ZIPDATA")
    with pytest.raises(SystemExit) as ei:
        _run(c, "automation", "export", "Daily Report",
             str(tmp_path / "no-such-dir" / "out.autowright"))
    assert ei.value.code != 2  # 2 stays the follow-failure signal
    assert "can't write" in str(ei.value.code)
    assert "out.autowright" in str(ei.value.code)


def test_cmd_automation_import_url_confirms_immediately(capsys):
    # §5.2/§20: a URL fetches + previews on the backend and confirms right away —
    # the typed command is the user's explicit action.
    reply = {"token": "tok1",
             "preview": {"resolvedUrl": "https://gh/dl/watcher.autowright"},
             "automation": {"name": "Web", "id": "cafebabe-2"},
             "summary": {"secretsMatched": [], "agentsMatched": [], "unresolved": [],
                         "packages": []}}
    c = _RouteClient(reply=reply)
    _run(c, "automation", "import", "https://github.com/alice/watcher")
    assert c.calls == [
        ("POST", "/automations/import/url", {"url": "https://github.com/alice/watcher"}),
        ("POST", "/automations/import/confirm", {"token": "tok1"}),
    ]
    # §20: the remote download rides the url call — 600 s; confirm stays 30 s
    assert [t for _, _, t in c.timeouts] == [600, 30]
    out = capsys.readouterr().out
    assert "resolved to https://gh/dl/watcher.autowright" in out
    assert "imported 'Web' [cafebabe]" in out


def test_cmd_automation_import_missing_file_exits():
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "automation", "import", "/nope/missing.autowright")
    assert "missing.autowright" in str(ei.value.code)


def test_cmd_automation_push_keeps_stored_grants(tmp_path, capsys):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    c = _WorkdirClient()
    _run(c, "automation", "push", "Daily Report", str(d), "--note", "tweak")
    method, path, body = c.posted[-1]
    assert (method, path) == ("POST", f"/automations/{AUTO_ID}/versions")
    assert body["draft"]["note"] == "tweak"
    # §20 grant model: the stored grants (secret ids) ride along unchanged
    assert body["allowedSecrets"] == [API_TOKEN_ID]
    # §4.3 merge: the untouched cron keeps its id, the app_start trigger survives
    kinds = {t["kind"] for t in body["draft"]["triggers"]}
    assert kinds == {"cron", "app_start"}
    assert "saved 'Daily Report' as v2" in capsys.readouterr().out


def test_cmd_automation_push_never_widens_grants_silently(tmp_path):
    """§20 grant model: a pushed step needing an ungranted secret exits 1
    naming the flag to add — push never widens the stored grants on its own."""
    from autowright import cli

    auto = {**FULL_AUTO, "allowedSecrets": []}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(auto), "automation", "push", "Daily Report", str(d))
    assert "--grant-secret API_TOKEN" in str(ei.value.code)


def test_cmd_automation_push_grant_flag_widens(tmp_path, capsys):
    from autowright import cli

    auto = {**FULL_AUTO, "allowedSecrets": []}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    c = _WorkdirClient(auto)
    _run(c, "automation", "push", "Daily Report", str(d),
         "--grant-secret", "API_TOKEN")
    _, _, body = c.posted[-1]
    # §20: the flag takes the name; the saved grant is the secret's id
    assert body["allowedSecrets"] == [API_TOKEN_ID]


AGENT_STEP_AUTO = {
    **FULL_AUTO,
    "steps": [{**FULL_AUTO["steps"][0], "agent": True, "why": "judgment call",
               "agents": [{"id": "ag1"}]}],
}


def test_cmd_automation_create_grant_agent_saves_id(tmp_path, capsys):
    """§20 grant model: --grant-agent takes the §8 grant name but stepAgents
    stores the agent's id — the enablement list the engine resolves by id."""
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, AGENT_STEP_AUTO)
    c = _WorkdirClient()
    _run(c, "automation", "create", str(d), "--grant-agent", "fast local",
         "--grant-secret", "API_TOKEN")
    _, _, body = c.posted[-1]
    assert body["stepAgents"] == ["ag1"]


def test_cmd_automation_push_stored_agent_id_satisfies_step_name(tmp_path, capsys):
    """§20: a stored stepAgents id maps back to its grant name for the
    needed-vs-granted check — no flag demanded for an already-granted agent."""
    from autowright import cli

    auto = {**AGENT_STEP_AUTO, "stepAgents": ["ag1"]}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    c = _WorkdirClient(auto)
    _run(c, "automation", "push", "Daily Report", str(d))
    _, _, body = c.posted[-1]
    assert body["stepAgents"] == ["ag1"]


def test_cmd_automation_push_ungranted_agent_names_flag(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, AGENT_STEP_AUTO)  # stepAgents: []
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(AGENT_STEP_AUTO), "automation", "push",
             "Daily Report", str(d))
    assert "--grant-agent Fast local" in str(ei.value.code)


def test_cmd_automation_push_unnamed_agent_step_needs_a_grant(tmp_path):
    """§20: an `agent: true` step with no `agents:` list runs on the first
    enabled agent — with no granted agent the save exits asking for a flag."""
    from autowright import cli

    auto = {**AGENT_STEP_AUTO,
            "steps": [{k: v for k, v in AGENT_STEP_AUTO["steps"][0].items()
                       if k != "agents"}]}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(auto), "automation", "push", "Daily Report", str(d))
    assert "--grant-agent" in str(ei.value.code)
    assert "Fast local" in str(ei.value.code)


def test_cmd_automation_create_grants_only_the_flags(tmp_path, capsys):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    c = _WorkdirClient()
    _run(c, "automation", "create", str(d), "--agent", "fast local",  # case-insensitive
         "--grant-secret", "API_TOKEN")
    method, path, body = c.posted[-1]
    assert (method, path) == ("POST", "/automations")
    assert body["agentId"] == "ag1"
    # §20 grant model: no all-on seed — exactly the flags (ids on the wire)
    assert body["stepAgents"] == []
    assert body["allowedSecrets"] == [API_TOKEN_ID]
    assert body["name"] == "Daily Report"  # from the manifest
    assert "created 'Daily Report'" in capsys.readouterr().out


def test_cmd_automation_create_without_grants_exits_naming_flags(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(), "automation", "create", str(d))
    assert "--grant-secret API_TOKEN" in str(ei.value.code)


def test_cmd_automation_create_unknown_grant_secret_exits(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(), "automation", "create", str(d),
             "--grant-secret", "NOPE")
    assert "no stored secret named 'NOPE'" in str(ei.value.code)
    assert "API_TOKEN" in str(ei.value.code)


def test_cmd_automation_create_unknown_agent_exits(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(), "automation", "create", str(d), "--agent", "nope")
    assert "no agent named 'nope'" in str(ei.value.code)
    assert "Fast local" in str(ei.value.code)


def test_cmd_param_list_and_set(capsys):
    _run(_RouteClient(_auto_gets()), "automation", "param", "list", "Daily Report")
    assert "sources" in capsys.readouterr().out

    c = _RouteClient(_auto_gets())
    _run(c, "automation", "param", "set", "Daily Report", "sources=a, b")
    assert c.calls == [("PATCH", f"/automations/{AUTO_ID}",
                        {"paramValues": {"sources": ["a", "b"]}})]
    assert "set sources on 'Daily Report'" in capsys.readouterr().out


def test_cmd_param_set_rejects_bad_input():
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets()), "automation", "param", "set", "Daily Report",
             "sources")  # no '='
    assert "NAME=VALUE" in str(ei.value.code)

    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets()), "automation", "param", "set", "Daily Report",
             "nope=1")
    assert "no param named 'nope'" in str(ei.value.code)
    assert "sources" in str(ei.value.code)


def test_cmd_trigger_list_empty_prints_hint(capsys):
    _run(_RouteClient(_auto_gets(dict(FULL_AUTO, triggers=[]))),
         "automation", "trigger", "list", "Daily Report")
    assert "no triggers" in capsys.readouterr().out


def test_cmd_trigger_toggle_by_index(capsys):
    c = _RouteClient(_auto_gets())
    _run(c, "automation", "trigger", "on", "Daily Report", "1")
    method, path, body = c.calls[-1]
    assert (method, path) == ("PATCH", f"/automations/{AUTO_ID}")
    assert body["triggers"][0]["enabled"] is True
    assert "trigger 1 (Daily 8:00 (Tokyo)) now on" in capsys.readouterr().out

    c = _RouteClient(_auto_gets())
    _run(c, "automation", "trigger", "off", "Daily Report", "2")
    assert c.calls[-1][2]["triggers"][1]["enabled"] is False
    assert "now off" in capsys.readouterr().out


def test_cmd_trigger_bad_index_exits():
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets()), "automation", "trigger", "off", "Daily Report", "9")
    assert "trigger index must be 1..2" in str(ei.value.code)


def test_cmd_trigger_remove_by_index(capsys):
    c = _RouteClient(_auto_gets())
    _run(c, "automation", "trigger", "remove", "Daily Report", "1")
    body = c.calls[-1][2]
    assert [t["kind"] for t in body["triggers"]] == ["app_start"]
    assert "removed trigger 1 (Daily 8:00 (Tokyo))" in capsys.readouterr().out


def test_trigger_marks_off_then_no_catch_up(capsys):
    """§20 list/show suffixes: " (off)" first, then " (no catch-up)" for a
    §4.3 runIfMissed opt-out - the true default marks nothing."""
    from autowright import cli

    assert cli._trigger_marks({"enabled": True}) == ""
    assert cli._trigger_marks({"enabled": True, "runIfMissed": True}) == ""
    assert cli._trigger_marks({"enabled": False}) == " (off)"
    assert cli._trigger_marks({"enabled": True, "runIfMissed": False}) == " (no catch-up)"
    assert cli._trigger_marks({"enabled": False, "runIfMissed": False}) == " (off) (no catch-up)"

    auto = {**FULL_AUTO,
            "triggers": [{**FULL_AUTO["triggers"][0], "runIfMissed": False},
                         FULL_AUTO["triggers"][1]]}
    _run(_RouteClient(_auto_gets(auto)), "automation", "trigger", "list", "Daily Report")
    out = capsys.readouterr().out
    assert "1. Daily at 8:00 (Tokyo) (off) (no catch-up)" in out
    assert "2. On app start\n" in out


SNAPS = [{"id": "s1111111-a", "when": "today 10:00", "reason": "manual", "version": "v2",
          "size": "1 KB", "name": "before cleanup"},
         {"id": "s2222222-b", "when": "today 11:00", "reason": "pre-clear", "version": "v2",
          "size": "2 KB", "name": ""}]


def test_cmd_memory_show(capsys):
    # §20 memory inspection: list (columns + --json), empty hint, one file verbatim.
    files = [{"name": "seen.yaml", "size": 7, "updated": "Today"},
             {"name": "sub/cache.bin", "size": 3, "updated": "Yesterday"}]
    base = f"/automations/{AUTO_ID}/memory/files"

    _run(_RouteClient({**_auto_gets(), base: {"files": files}}),
         "automation", "memory", "show", "Daily Report")
    out = capsys.readouterr().out
    assert "seen.yaml" in out and "sub/cache.bin" in out and "Today" in out

    _run(_RouteClient({**_auto_gets(), base: {"files": []}}),
         "automation", "memory", "show", "Daily Report")
    assert "memory is empty" in capsys.readouterr().out

    _run(_RouteClient({**_auto_gets(), base: {"files": files}}),
         "automation", "memory", "show", "Daily Report", "--json")
    assert json.loads(capsys.readouterr().out) == files

    # a file argument prints the text verbatim — nothing added
    one = {**_auto_gets(),
           f"{base}/seen.yaml": {"name": "seen.yaml", "size": 7, "text": "v: old\n"}}
    _run(_RouteClient(one), "automation", "memory", "show", "Daily Report", "seen.yaml")
    assert capsys.readouterr().out == "v: old\n"


def test_cmd_memory_and_snapshot_commands(capsys):
    auto = dict(FULL_AUTO, snapshots=SNAPS)

    c = _RouteClient(_auto_gets(auto))
    _run(c, "automation", "memory", "clear", "Daily Report")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/memory/clear", None)]
    assert "memory cleared" in capsys.readouterr().out

    _run(_RouteClient(_auto_gets(auto)), "automation", "snapshot", "list", "Daily Report")
    out = capsys.readouterr().out
    assert "s1111111" in out and "before cleanup" in out

    c = _RouteClient(_auto_gets(auto), reply={"snapshot": {"id": "s3333333-c"}})
    _run(c, "automation", "snapshot", "create", "Daily Report", "--name", "label")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/memory/snapshots", {"name": "label"})]
    assert "snapshot s3333333 created" in capsys.readouterr().out

    c = _RouteClient(_auto_gets(auto))
    _run(c, "automation", "snapshot", "restore", "Daily Report", "s1")
    assert c.calls == [("POST", f"/automations/{AUTO_ID}/memory/snapshots/s1111111-a/restore",
                        None)]

    c = _RouteClient(_auto_gets(auto))
    _run(c, "automation", "snapshot", "delete", "Daily Report", "s2")
    assert c.calls == [("DELETE", f"/automations/{AUTO_ID}/memory/snapshots/s2222222-b", None)]


def test_find_snapshot_ambiguous_prefix_exits():
    from autowright.cli import _find_snapshot

    c = _RouteClient(_auto_gets(dict(FULL_AUTO, snapshots=SNAPS)))
    with pytest.raises(SystemExit) as ei:
        _find_snapshot(c, {"id": AUTO_ID}, "s")  # both match
    msg = str(ei.value.code)
    assert "no unique snapshot" in msg
    # §20: ambiguity exits with the candidate list — the snapshot-list row
    # fields, name only when set
    assert "s1111111 (today 10:00, manual, v2, 1 KB, before cleanup)" in msg
    assert "s2222222 (today 11:00, pre-clear, v2, 2 KB)" in msg


FULL_EXEC = {
    "id": "e1234567-0000", "automationName": "Daily Report", "versionLabel": "v2", "status": "failed",
    "duration": "3s", "trigger": "Discord", "started": "2026-07-29 08:00",
    "triggerPayload": {"kind": "discord", "sender": "dave", "channel": "42",
                       "channelName": "general", "guildName": "Ops",
                       "at": "08:00", "text": "go fetch"},
    "steps": [{"name": "Fetch", "status": "succeeded", "duration": "1s"},
              {"name": "Report", "status": "failed", "duration": "2s"}],
    "error": {"step": "Report", "message": "boom", "reason": "the API was down"},
    "result": {"chip": "0 new", "files": [{"name": "report.md", "size": "2 KB"}]},
}


def test_cmd_execution_list_filters_and_limit(capsys):
    """§20: -n rides to the server as the §19 limit (first in the query
    string) — the envelope's rows print as they come, with no client slice."""
    execs = [dict(FULL_EXEC, id=f"e{i}") for i in range(3)]
    # the server applies the cap: 2 of the 3 matching rows, total unchanged
    gets = _auto_gets(**{f"/executions?limit=2&automation={AUTO_ID}&status=failed":
                         {"executions": execs[:2], "total": 3}})
    _run(_RouteClient(gets), "execution", "list", "-n", "2",
         "--automation", "Daily Report", "--status", "failed")
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2  # exactly the envelope's rows
    assert "Daily Report" in out[0] and "[e0]" in out[0]
    assert "[e1]" in out[1]


def test_cmd_execution_list_repeats_the_status_filter(capsys):
    """§20: --status repeats — each value rides as its own §19 `status` query
    value, so rows in any of them match."""
    gets = _auto_gets(**{"/executions?limit=20&status=failed&status=cancelled":
                         {"executions": [dict(FULL_EXEC, id="e0")], "total": 1}})
    _run(_RouteClient(gets), "execution", "list",
         "--status", "failed", "--status", "cancelled")
    assert "[e0]" in capsys.readouterr().out


def test_cmd_execution_list_repeats_the_automation_filter(capsys):
    """§20: --automation repeats — each reference resolves the usual way and
    rides as its own §19 `automation` value, so the two ids match rows of
    either."""
    other = dict(FULL_AUTO, id="def45678-0000-0000-0000-000000000000", name="Weekly Digest")
    gets = {"/automations": [FULL_AUTO, other],
            f"/automations/{AUTO_ID}": FULL_AUTO, f"/automations/{other['id']}": other,
            f"/executions?limit=20&automation={AUTO_ID}&automation={other['id']}":
                {"executions": [dict(FULL_EXEC, id="e0")], "total": 1}}
    _run(_RouteClient(gets), "execution", "list",
         "--automation", "Daily Report", "--automation", "Weekly Digest")
    assert "[e0]" in capsys.readouterr().out


def test_cmd_execution_list_since_and_until_ride_as_the_inclusive_range():
    """§20 --since / --until: a local YYYY-MM-DD or YYYY-MM-DDTHH:MM becomes
    the inclusive §19 startedFromMs / startedToMs, and a bare date on --until
    means the end of that day."""
    from datetime import datetime

    from autowright.cli import _local_stamp_ms

    # local time, so the expectation is computed the same way rather than pinned
    frm = int(datetime(2026, 9, 1, 0, 0).timestamp() * 1000)
    until = int(datetime(2026, 9, 6, 23, 59, 59, 999_000).timestamp() * 1000)
    assert _local_stamp_ms("2026-09-01", end_of_day=False) == frm
    assert _local_stamp_ms("2026-09-06", end_of_day=True) == until

    path = f"/executions?limit=20&startedFromMs={frm}&startedToMs={until}"
    gets = _auto_gets(**{path: {"executions": [], "total": 0}})
    _run(_RouteClient(gets), "execution", "list",
         "--since", "2026-09-01", "--until", "2026-09-06")

    # a minute-resolution value rides as exactly that minute on both ends
    at = int(datetime(2026, 9, 6, 18, 30).timestamp() * 1000)
    assert _local_stamp_ms("2026-09-06T18:30", end_of_day=True) == at
    minute_path = f"/executions?limit=20&startedFromMs={at}&startedToMs={at}"
    _run(_RouteClient(_auto_gets(**{minute_path: {"executions": [], "total": 0}})),
         "execution", "list", "--since", "2026-09-06T18:30", "--until", "2026-09-06T18:30")


def test_cmd_execution_list_rejects_a_malformed_since():
    """§20: a value in neither accepted form is a usage error naming both."""
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets()), "execution", "list", "--since", "last tuesday")
    assert ei.value.code != 0
    assert "YYYY-MM-DD" in str(ei.value.code) and "YYYY-MM-DDTHH:MM" in str(ei.value.code)


def test_cmd_execution_show_prints_trigger_message_error_and_result(capsys):
    gets = {"/executions": {"executions": [FULL_EXEC], "total": 1},
            f"/executions/{FULL_EXEC['id']}": FULL_EXEC}
    _run(_RouteClient(gets), "execution", "show")  # no ref → latest
    out = capsys.readouterr().out
    assert "Daily Report v2 — failed in 3s (Discord, 2026-07-29 08:00)" in out
    assert "trigger message: dave · #general · Ops · 08:00" in out and "go fetch" in out
    assert "step 2: Report" in out
    assert "error in 'Report': boom" in out
    assert "possible reason: the API was down" in out
    assert "result: 0 new" in out
    assert "file: report.md (2 KB)" in out



def test_cmd_execution_show_prints_count_line(capsys):
    """§20/§4.5: the item count prints on its own line, above the result chip,
    and its noun agrees with the number."""
    e = dict(FULL_EXEC, result={"count": 12, "chip": "0 new", "files": []})
    gets = {"/executions": {"executions": [e], "total": 1}, f"/executions/{e['id']}": e}
    _run(_RouteClient(gets), "execution", "show")
    out = capsys.readouterr().out
    assert "count: 12 items" in out
    assert out.index("count: 12 items") < out.index("result: 0 new")

    e = dict(FULL_EXEC, result={"count": 1, "chip": "0 new", "files": []})
    gets = {"/executions": {"executions": [e], "total": 1}, f"/executions/{e['id']}": e}
    _run(_RouteClient(gets), "execution", "show")
    assert "count: 1 item\n" in capsys.readouterr().out


def test_cmd_execution_show_payload_fallbacks(capsys):
    # iMessage payload has no channel — must print sender · time, no KeyError
    e = dict(FULL_EXEC, triggerPayload={
        "kind": "imessage", "sender": "+15551234567", "messageId": "g1",
        "chat": "iMessage;-;+15551234567", "at": "08:00", "text": "hi"})
    gets = {"/executions": {"executions": [e], "total": 1}, f"/executions/{e['id']}": e}
    _run(_RouteClient(gets), "execution", "show")
    out = capsys.readouterr().out
    assert "trigger message: +15551234567 · 08:00" in out and "  hi" in out

    # Discord with no cached names falls back to the raw channel id
    e = dict(FULL_EXEC, triggerPayload=dict(
        FULL_EXEC["triggerPayload"], channelName=None, guildName=None))
    gets = {"/executions": {"executions": [e], "total": 1}, f"/executions/{e['id']}": e}
    _run(_RouteClient(gets), "execution", "show")
    assert "trigger message: dave · 42 · 08:00" in capsys.readouterr().out


def test_cmd_execution_tail_follows_and_exits_by_status(monkeypatch, capsys):
    """§20: `execution tail` resolves the reference (default: latest), streams
    the log lines, and carries the follow exit code: 0 on success, 2 when the
    execution ends any other way."""
    from autowright import cli

    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    class _TailClient:
        base = "http://127.0.0.1:5151"

        def __init__(self, status):
            self.status = status
            self.polls = 0

        def req(self, method, path, body=None, timeout=30):
            if path == "/executions":
                return {"executions": [FULL_EXEC], "total": 1}
            if path == f"/executions/{FULL_EXEC['id']}":
                self.polls += 1
                # one live poll first, so the loop really iterates
                return {"status": "executing" if self.polls == 1 else self.status,
                        "duration": "3s", "steps": []}
            assert path == f"/executions/{FULL_EXEC['id']}/logs"
            return {"lines": [{"sequence": self.polls, "time": f"T{self.polls}",
                               "kind": "log", "text": f"line {self.polls}"}]}

    c = _TailClient("succeeded")
    _run(c, "execution", "tail")  # no ref → latest, and a clean exit (no raise)
    out = capsys.readouterr().out
    assert "T1 [log] line 1" in out and "→ succeeded in 3s" in out

    c = _TailClient("failed")
    with pytest.raises(SystemExit) as ei:
        _run(c, "execution", "tail", "e12")
    assert ei.value.code == 2
    assert "→ failed in 3s" in capsys.readouterr().out


def test_cmd_execution_cancel_and_retry(capsys):
    gets = {"/executions": {"executions": [FULL_EXEC], "total": 1}}
    c = _RouteClient(gets)
    _run(c, "execution", "cancel", "e12")
    assert c.calls == [("POST", f"/executions/{FULL_EXEC['id']}/cancel", None)]
    assert "cancelled e1234567" in capsys.readouterr().out

    c = _RouteClient(gets)
    _run(c, "execution", "retry", "e12")
    assert c.calls == [("POST", f"/executions/{FULL_EXEC['id']}/retry", None)]
    assert "retrying e1234567 in place" in capsys.readouterr().out


def test_cmd_execution_skip_targets_first_executing_step(capsys):
    running = dict(FULL_EXEC, steps=[{"name": "A", "status": "succeeded"},
                                     {"name": "B", "status": "executing"}])
    gets = {"/executions": {"executions": [running], "total": 1},
            f"/executions/{running['id']}": running}
    c = _RouteClient(gets)
    _run(c, "execution", "skip")
    assert c.calls == [("POST", f"/executions/{running['id']}/skip-step", {"index": 1})]
    assert "skipping step 2" in capsys.readouterr().out

    gets = {"/executions": {"executions": [FULL_EXEC], "total": 1},
            f"/executions/{FULL_EXEC['id']}": FULL_EXEC}
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(gets), "execution", "skip")
    assert "no step is executing" in str(ei.value.code)


def test_cmd_execution_result_lists_then_streams(capsysbinary):
    gets = {"/executions": {"executions": [FULL_EXEC], "total": 1},
            f"/executions/{FULL_EXEC['id']}": FULL_EXEC}
    _run(_RouteClient(gets), "execution", "result")
    assert b"report.md (2 KB)" in capsysbinary.readouterr().out

    c = _RouteClient({"/executions": {"executions": [FULL_EXEC], "total": 1}},
                     raw=b"\x00binary bytes")
    _run(c, "execution", "result", "e12", "report.md")
    assert c.calls == [("GET", f"/executions/{FULL_EXEC['id']}/result/report.md", None)]
    assert capsysbinary.readouterr().out == b"\x00binary bytes"


def test_cmd_secret_commands(monkeypatch, capsys):
    # §4.8: usedBy entries are { id, name } — the CLI prints the names
    secrets = [{"id": "s-1", "name": "API_TOKEN", "set": True,
                "usedBy": [{"id": "a-1", "name": "Daily Report"}]},
               {"id": "s-2", "name": "BOT_TOKEN", "set": False, "usedBy": []}]
    _run(_RouteClient({"/secrets": secrets}), "secret", "list")
    out = capsys.readouterr().out
    assert "API_TOKEN" in out and "used by: Daily Report" in out
    assert "BOT_TOKEN (not set)" in out
    assert "used by: not used yet" in out  # empty usedBy list

    # §20: a value never rides argv — there is no positional to pass it in.
    with pytest.raises(SystemExit):
        _run(_RouteClient(), "secret", "set", "API_TOKEN", "s3cret")

    # --stdin, for scripted use — an existing name edits via the id route (§19)
    from autowright import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("piped-in\n"))
    c = _RouteClient({"/secrets": secrets})
    _run(c, "secret", "set", "API_TOKEN", "--stdin")
    assert c.calls == [("PUT", "/secrets/s-1", {"value": "piped-in"})]
    # §9 per-OS copy rule: "Keychain" on macOS, "Credential Manager" on Windows.
    assert f"saved to your {paths.secret_store_name()}" in capsys.readouterr().out

    # otherwise prompted, never echoed — a new name creates via POST (§19)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "typed-in")
    c = _RouteClient({"/secrets": secrets})
    _run(c, "secret", "set", "NEW_KEY")
    assert c.calls == [("POST", "/secrets", {"name": "NEW_KEY", "value": "typed-in"})]

    # delete resolves the name to the id route; an unknown name exits
    c = _RouteClient({"/secrets": secrets})
    _run(c, "secret", "delete", "API_TOKEN")
    assert c.calls == [("DELETE", "/secrets/s-1", None)]
    c = _RouteClient({"/secrets": secrets})
    with pytest.raises(SystemExit, match="no stored secret named"):
        _run(c, "secret", "delete", "NOPE")
    assert c.calls == []
    assert f"removed from your {paths.secret_store_name()}" in capsys.readouterr().out


def test_cmd_secret_set_stdin_takes_the_whole_input(monkeypatch, capsys):
    """§20: --stdin reads the WHOLE of stdin, so a multi-line value (a PEM key)
    lands intact — only the trailing newline is trimmed."""
    from autowright import cli

    secrets = [{"id": "s-1", "name": "API_TOKEN", "set": True, "usedBy": []}]
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----\n"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(pem))
    c = _RouteClient({"/secrets": secrets})
    _run(c, "secret", "set", "API_TOKEN", "--stdin")
    assert c.calls == [("PUT", "/secrets/s-1", {"value": pem.rstrip("\n")})]
    capsys.readouterr()


def test_cmd_secret_delete_all(capsys):
    """§20: `secret delete --all` is the collection route, behind the
    destructive guard — and it never mixes with a name."""
    secrets = [{"id": "s-1", "name": "API_TOKEN", "set": True, "usedBy": []}]

    # the destructive guard: --yes or nothing happens
    c = _RouteClient({"/secrets": secrets})
    with pytest.raises(SystemExit, match="add --yes to confirm"):
        _run(c, "secret", "delete", "--all")
    assert c.calls == []

    # mutually exclusive with a name — neither is guessed
    c = _RouteClient({"/secrets": secrets})
    with pytest.raises(SystemExit, match="drop the name"):
        _run(c, "secret", "delete", "API_TOKEN", "--all", "--yes")
    assert c.calls == []

    # neither a name nor --all
    c = _RouteClient({"/secrets": secrets})
    with pytest.raises(SystemExit, match="needs a secret name"):
        _run(c, "secret", "delete")
    assert c.calls == []

    # confirmed: the collection route (§19), and the count is printed
    c = _RouteClient({"/secrets": secrets}, reply={"deleted": 3})
    _run(c, "secret", "delete", "--all", "--yes")
    assert c.calls == [("DELETE", "/secrets", None)]
    assert f"removed 3 secret(s) from your {paths.secret_store_name()}" \
        in capsys.readouterr().out


def test_secret_lines_take_the_per_os_secret_store_name(monkeypatch, capsys):
    """§9 per-OS copy rule: the §20 secret lines name the Keychain on macOS and
    the Credential Manager on Windows — one substituted noun, same wording."""
    from autowright import cli

    secrets = [{"id": "s-1", "name": "API_TOKEN", "set": True, "usedBy": []}]
    for token, store_name in (("macos", "Keychain"), ("windows", "Credential Manager")):
        monkeypatch.setattr(paths, "current_os", lambda t=token: t)
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO("piped-in\n"))
        _run(_RouteClient({"/secrets": secrets}), "secret", "set", "API_TOKEN", "--stdin")
        assert capsys.readouterr().out.strip() == f"saved to your {store_name}"
        _run(_RouteClient({"/secrets": secrets}), "secret", "delete", "API_TOKEN")
        assert capsys.readouterr().out.strip() == f"removed from your {store_name}"


def test_cmd_secret_set_empty_value_exits_on_stderr(monkeypatch, capsys):
    """§20: an empty value saves nothing and exits 1 through sys.exit, so the
    message lands on stderr like every other error, not on stdout."""
    from autowright import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("\n"))
    c = _RouteClient({"/secrets": []})
    with pytest.raises(SystemExit) as ei:
        _run(c, "secret", "set", "API_TOKEN", "--stdin")
    assert "no value given" in str(ei.value.code)
    assert c.calls == []                       # nothing written
    assert capsys.readouterr().out == ""       # and nothing on stdout


AGENTS = [{"id": "ag1", "name": "Fast local", "harness": "OpenCode", "model": "qwen3",
           "default": True},
          {"id": "ag2", "name": None, "harness": "Claude Code", "model": None}]


def test_cmd_agent_list_and_check(capsys):
    _run(_RouteClient({"/agents": AGENTS}), "agent", "list")
    out = capsys.readouterr().out
    assert "* Fast local" in out and "qwen3" in out
    assert "Claude Code" in out and "default model" in out

    c = _RouteClient({"/agents": AGENTS}, reply={"ok": True})
    _run(c, "agent", "check", "claude code")  # falls back to harness name
    assert c.calls == [("POST", "/agents/ag2/check", None)]
    assert json.loads(capsys.readouterr().out) == {"ok": True}

    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient({"/agents": AGENTS}), "agent", "check", "nope")
    assert "no agent named 'nope'" in str(ei.value.code)


def test_cmd_settings_show_and_set(capsys):
    _run(_RouteClient({"/settings": {"login": True, "days": 30, "keepAwake": True}}),
         "settings", "show")
    out = capsys.readouterr().out
    assert "login" in out and "True" in out
    assert "keepAwake" in out

    c = _RouteClient()
    _run(c, "settings", "set", "login=on", "days=14", "notifications=failures")
    assert c.calls == [("PATCH", "/settings",
                        {"login": True, "days": 14, "notifications": "failures"})]
    assert "set login, days, notifications" in capsys.readouterr().out

    # keepAwake is a known bool key — both directions parse
    c = _RouteClient()
    _run(c, "settings", "set", "keepAwake=off")
    assert c.calls == [("PATCH", "/settings", {"keepAwake": False})]
    assert "set keepAwake" in capsys.readouterr().out
    c = _RouteClient()
    _run(c, "settings", "set", "keepAwake=on")
    assert c.calls == [("PATCH", "/settings", {"keepAwake": True})]


def test_cmd_settings_set_data_path_and_errors(capsys):
    c = _RouteClient()
    _run(c, "settings", "set", "dataPath=/Volumes/T7/aw")
    assert c.calls == [("POST", "/settings/data-path", {"path": "/Volumes/T7/aw"})]
    assert "execution data now at /Volumes/T7/aw" in capsys.readouterr().out

    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "settings", "set", "nope=1")
    assert "unknown setting 'nope'" in str(ei.value.code)

    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "settings", "set", "days=ten")
    assert "days takes an integer" in str(ei.value.code)


def test_cmd_service_dispatches_to_service_module(monkeypatch, capsys):
    from autowright import service

    # The CLI group is a thin wrapper over service.ACTIONS (§3 — the same
    # table `python -m autowright.service` dispatches through).
    monkeypatch.setitem(service.ACTIONS, "status", lambda: "running (pid 42)")
    _run(None, "service", "status")
    assert "running (pid 42)" in capsys.readouterr().out


def test_cmd_service_dispatches_stop(monkeypatch, capsys):
    from autowright import service

    monkeypatch.setitem(service.ACTIONS, "stop", lambda: "stopped — fake")
    _run(None, "service", "stop")
    assert "stopped — fake" in capsys.readouterr().out


def test_cmd_service_exits_nonzero_on_failure(monkeypatch, capsys):
    from autowright import service

    # §3/§20: the wrapper exits exactly where `python -m autowright.service`
    # does; a failed stop is exit 1, not a silent 0.
    monkeypatch.setitem(service.ACTIONS, "stop",
                        lambda: "stop failed: launchd still reports the job")
    with pytest.raises(SystemExit) as ei:
        _run(None, "service", "stop")
    assert ei.value.code == 1
    assert "stop failed" in capsys.readouterr().out


def test_cmd_service_exits_nonzero_when_not_installed(monkeypatch, capsys):
    from autowright import service

    # §3: `status` with no plist reports "not installed", exit 1.
    monkeypatch.setitem(service.ACTIONS, "status", lambda: "not installed")
    with pytest.raises(SystemExit) as ei:
        _run(None, "service", "status")
    assert ei.value.code == 1
    assert "not installed" in capsys.readouterr().out


# ------------------------------------------- appended coverage: --json, errors

JSON_SETTINGS = {"login": True, "days": 30, "keepAwake": False}
JSON_SECRETS = [{"id": "s-1", "name": "API_TOKEN", "set": True, "usedBy": []}]
MEMORY_FILES = [{"name": "seen.yaml", "size": 7, "updated": "Today"}]


@pytest.mark.parametrize("argv, gets, expected", [
    pytest.param(("automation", "param", "list", "Daily Report"), _auto_gets(),
                 FULL_AUTO["params"], id="param-list"),
    pytest.param(("automation", "trigger", "list", "Daily Report"), _auto_gets(),
                 FULL_AUTO["triggers"], id="trigger-list"),
    pytest.param(("automation", "snapshot", "list", "Daily Report"),
                 _auto_gets(dict(FULL_AUTO, snapshots=SNAPS)), SNAPS, id="snapshot-list"),
    pytest.param(("automation", "memory", "show", "Daily Report"),
                 {**_auto_gets(),
                  f"/automations/{AUTO_ID}/memory/files": {"files": MEMORY_FILES}},
                 MEMORY_FILES, id="memory-show"),
    pytest.param(("execution", "show"),
                 {"/executions": {"executions": [FULL_EXEC], "total": 1},
                  f"/executions/{FULL_EXEC['id']}": FULL_EXEC},
                 FULL_EXEC, id="execution-show"),
    pytest.param(("secret", "list"), {"/secrets": JSON_SECRETS},
                 JSON_SECRETS, id="secret-list"),
    pytest.param(("agent", "list"), {"/agents": AGENTS}, AGENTS, id="agent-list"),
    pytest.param(("settings", "show"), {"/settings": JSON_SETTINGS},
                 JSON_SETTINGS, id="settings-show"),
])
def test_read_verbs_print_only_json_with_the_flag(argv, gets, expected, capsys):
    """§20 `--json`: a read verb prints the backend's own JSON and nothing
    else: the whole of stdout parses, so a human row after it would fail here."""
    _run(_RouteClient(gets), *argv, "--json")
    assert json.loads(capsys.readouterr().out) == expected


def test_cmd_automation_push_unknown_grant_agent_names_the_candidates(tmp_path):
    """§20 grant model: --grant-agent takes a configured agent's name. An
    unknown one exits with the list of names that would work."""
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    with pytest.raises(SystemExit) as ei:
        _run(_WorkdirClient(), "automation", "push", "Daily Report", str(d),
             "--grant-agent", "Bogus")
    assert str(ei.value.code) == "no agent named 'Bogus' — have: Fast local"


def test_read_workdir_missing_directory_exits(tmp_path):
    """§20 exit-code rule: an unusable workdir is a message on stderr, never a
    traceback."""
    from autowright import cli

    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as ei:
        cli.read_workdir(missing)
    assert str(ei.value.code) == f"{missing} is not a directory"


def test_read_workdir_non_utf8_file_exits(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    d.mkdir()
    (d / "spec.md").write_bytes(b"# Daily\n\n\xff\xfe not text\n")
    with pytest.raises(SystemExit) as ei:
        cli.read_workdir(d)
    assert str(ei.value.code) == f"can't read {d / 'spec.md'}: not UTF-8 text"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="file permissions don't block reads for this user")
def test_read_workdir_unreadable_file_exits(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    d.mkdir()
    step = d / "01-fetch.py"
    step.write_text("print('hi')\n")
    step.chmod(0o000)
    try:
        with pytest.raises(SystemExit) as ei:
            cli.read_workdir(d)
    finally:
        step.chmod(0o644)
    assert str(ei.value.code).startswith(f"can't read {step}: ")


def test_parse_param_value_reports_malformed_json(capsys):
    """§20: a JSON-shaped value that doesn't parse names the shape it tried.
    The sibling table above covers a well-formed payload of the wrong type."""
    from autowright import cli

    with pytest.raises(SystemExit) as ei:
        cli.parse_param_value({"name": "sources", "kind": "list"}, "[1,2")
    assert str(ei.value.code).startswith("param sources: bad JSON array — ")
    with pytest.raises(SystemExit) as ei:
        cli.parse_param_value({"name": "headers", "kind": "kv"}, "{")
    assert str(ei.value.code).startswith("param headers: bad JSON object — ")
    with pytest.raises(SystemExit) as ei:
        cli.parse_param_value({"name": "sources", "kind": "list"}, "[1, 2]")
    assert str(ei.value.code) == "param sources: expected a JSON array of strings"


def test_cmd_settings_set_rejects_a_valueless_item_and_a_bad_bool():
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "settings", "set", "foo")
    assert str(ei.value.code) == "expected KEY=VALUE, got 'foo'"
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "settings", "set", "cliEnabled=maybe")
    assert str(ei.value.code) == "cliEnabled takes on|off, got 'maybe'"


def test_settings_set_menu_bar_help_is_per_os(monkeypatch):
    """§9 per-OS copy rule: the `settings set` epilog names the §13 surface the
    way this OS does, and says the key is ignored where there is none."""
    from autowright import cli

    monkeypatch.setattr(paths, "current_os", lambda: "macos")
    assert cli._menu_bar_icon_help() == "show the menu bar icon"
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    assert cli._menu_bar_icon_help() == "show the tray icon"
    monkeypatch.setattr(paths, "current_os", lambda: "linux")
    assert cli._menu_bar_icon_help() == \
        "show the tray icon (Linux has no tray, so this is ignored)"


def test_req_and_req_raw_exit_on_an_http_error(home, monkeypatch):
    """§20: both request layers route an HTTP error through the same exit:
    the API's detail message, no traceback."""
    import urllib.error

    from autowright import cli, paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    c = cli.Client()

    def raise_http(*_a, **_kw):
        raise urllib.error.HTTPError(
            "http://x", 409, "Conflict", {}, io.BytesIO(b'{"detail":"already running"}'))

    monkeypatch.setattr(cli._opener, "open", raise_http)
    with pytest.raises(SystemExit) as ei:
        c.req("POST", "/automations/x/execute")
    assert str(ei.value.code) == "409: already running"
    with pytest.raises(SystemExit) as ei:
        c.req_raw("GET", "/automations/x/export")
    assert str(ei.value.code) == "409: already running"
