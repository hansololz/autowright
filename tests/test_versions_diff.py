"""§19 version diff (`GET /automations/{id}/diff`): the one diff computation
behind the §9.2 modal and the §20 `automation diff` command.

Three tiers, like the feature: the pure functions (versions_diff), the route
(over the live app), and the CLI command (over the routing stub).
"""
import copy
import json

import pytest
import yaml

from conftest import make_version
from test_cli import AUTO_ID, FULL_AUTO, _RouteClient, _auto_gets, _run


def _step(name, code, file=None, **over):
    return {"file": file or f"{name}.py", "name": name, "description": "",
            "code": code, **over}


def _ver(*steps, **over):
    return make_version(steps=list(steps), **over)


def _by_kind(files, kind):
    return [f for f in files if f["kind"] == kind]


# ---------------------------------------------------------------- diff_versions

def test_identical_versions_list_every_file_unchanged():
    """§19: a file identical on both sides is still listed (unchanged, every row same),
    in navigator order — manifest, spec, notes, then the steps."""
    from autowright import versions_diff

    v = make_version()
    files = versions_diff.diff_versions(copy.deepcopy(v), copy.deepcopy(v))
    assert [f["kind"] for f in files] == ["manifest", "spec", "notes", "step", "step"]
    assert [f["file"] for f in files] == ["automation.yaml", "spec.md", "notes.md",
                                          "01-say.py", "02-finish.py"]
    assert [f["name"] for f in files] == ["Manifest", "Spec", "Notes", "Say hello", "Finish"]
    assert all(f["status"] == "unchanged" for f in files)
    assert all(f["added"] == 0 and f["removed"] == 0 for f in files)
    assert all(r["kind"] == "same" for f in files for r in f["rows"])


def test_changed_and_appended_step_lines():
    """§19: a replace block pairs into a mod row with both sides' numbers and texts;
    an appended line is an add row with no left side."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\nb\n")),
                                        _ver(_step("Fetch", "a\nB\n")))
    step = _by_kind(files, "step")[0]
    assert step["status"] == "changed" and (step["added"], step["removed"]) == (1, 1)
    assert step["rows"] == [
        {"kind": "same", "left": {"number": 1, "text": "a"}, "right": {"number": 1, "text": "a"}},
        {"kind": "mod", "left": {"number": 2, "text": "b"}, "right": {"number": 2, "text": "B"}},
    ]

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\n")),
                                        _ver(_step("Fetch", "a\nb\n")))
    step = _by_kind(files, "step")[0]
    assert step["status"] == "changed" and (step["added"], step["removed"]) == (1, 0)
    assert step["rows"][1] == {"kind": "add", "left": None,
                               "right": {"number": 2, "text": "b"}}


def test_added_step_is_new_and_removed_step_is_appended():
    """§19: steps come in the `to` version's order with the `from`-only steps
    appended — a new step is `new` (every row add), an old-only one `removed`."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\n")),
                                        _ver(_step("Fetch", "a\n"), _step("Report", "x\ny\n")))
    steps = _by_kind(files, "step")
    assert [s["name"] for s in steps] == ["Fetch", "Report"]
    added = steps[1]
    assert added["status"] == "new" and (added["added"], added["removed"]) == (2, 0)
    assert [r["kind"] for r in added["rows"]] == ["add", "add"]
    assert all(r["left"] is None for r in added["rows"])

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\n"), _step("Report", "x\ny\n")),
                                        _ver(_step("Fetch", "a\n")))
    steps = _by_kind(files, "step")
    assert [s["name"] for s in steps] == ["Fetch", "Report"]  # old-only one appended last
    gone = steps[1]
    assert gone["status"] == "removed" and (gone["added"], gone["removed"]) == (0, 2)
    assert [r["kind"] for r in gone["rows"]] == ["del", "del"]
    assert all(r["right"] is None for r in gone["rows"])


def test_renamed_step_reads_as_one_removed_and_one_new():
    """§19: steps match by name, so a rename is one removed file and one new one —
    never a changed one, whatever the code says."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(_ver(_step("Old name", "a\n")),
                                        _ver(_step("New name", "a\n")))
    steps = _by_kind(files, "step")
    assert [(s["name"], s["status"]) for s in steps] == [("New name", "new"),
                                                         ("Old name", "removed")]
    assert not [s for s in steps if s["status"] == "changed"]


def test_duplicate_step_names_match_kth_to_kth():
    """§19: the k-th step of a name matches the k-th of that name on the other side."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(
        _ver(_step("Dup", "one\n", file="01.py"), _step("Dup", "two\n", file="02.py")),
        _ver(_step("Dup", "one\n", file="01.py"), _step("Dup", "TWO\n", file="02.py")))
    steps = _by_kind(files, "step")
    assert [(s["file"], s["status"]) for s in steps] == [("01.py", "unchanged"),
                                                         ("02.py", "changed")]


# ---------------------------------------------------------------- manifest text

def test_manifest_diff_shows_step_fields_and_excludes_when_and_note():
    """§19: the manifest side is the versioned fields the §5 writer emits, minus the
    when / note metadata that differs by construction."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\n", timeout=60)),
                                        _ver(_step("Fetch", "a\n", timeout=120)))
    manifest = files[0]
    assert manifest["kind"] == "manifest" and manifest["status"] == "changed"
    mods = [r for r in manifest["rows"] if r["kind"] == "mod"]
    assert len(mods) == 1 and "timeout: 120" in mods[0]["right"]["text"]
    assert "timeout: 60" in mods[0]["left"]["text"]

    files = versions_diff.diff_versions(make_version(when="2026-09-01T10:00:00", note="First"),
                                        make_version(when="2026-09-09T11:00:00", note="Second"))
    assert files[0]["status"] == "unchanged"


def test_manifest_text_carries_packages_only_when_declared():
    """§6.2/§19: the stored manifest keeps the package declarations, and has no
    packages key at all when none are declared."""
    from autowright import versions_diff

    with_packages = make_version(packages=[{"pip": "requests", "import": "requests",
                                            "why": "fetches the pages"}])
    assert "packages:" in versions_diff.manifest_text(with_packages)
    assert "packages:" not in versions_diff.manifest_text(make_version())


def test_manifest_text_matches_the_on_disk_manifest(store, home):
    """§19: the manifest side reads as the on-disk automation.yaml would, minus its
    when / note lines."""
    from autowright import versions_diff

    a = store.create_automation(make_version(), "Manifested", "agent-1")
    disk = (home / "automations" / a["id"] / "versions" / "v1"
            / "automation.yaml").read_text(encoding="utf-8")
    kept = "\n".join(ln for ln in disk.splitlines()
                     if not ln.startswith(("when:", "note:")))
    assert yaml.safe_load(kept) == yaml.safe_load(versions_diff.manifest_text(a["versions"][1]))


# ---------------------------------------------------------------- notes + line rules

def test_notes_file_status_follows_the_absent_file_rule():
    """§5/§19: notes.md exists only when the notes are non-empty, so adding notes is a
    new file and no notes on either side is unchanged with no rows."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(make_version(notes=""), make_version(notes="hello"))
    notes = files[2]
    assert notes["kind"] == "notes" and notes["status"] == "new"
    assert [r["kind"] for r in notes["rows"]] == ["add"]

    files = versions_diff.diff_versions(make_version(notes=""), make_version(notes=""))
    assert files[2]["status"] == "unchanged" and files[2]["rows"] == []

    files = versions_diff.diff_versions(make_version(notes="x"), make_version(notes="x"))
    assert files[2]["status"] == "unchanged"


def test_trailing_newline_and_empty_text_line_rules():
    """§19: a single trailing newline is stripped before splitting, and empty text is
    zero lines."""
    from autowright import versions_diff

    files = versions_diff.diff_versions(_ver(_step("Fetch", "a\nb\n")),
                                        _ver(_step("Fetch", "a\nb")))
    step = _by_kind(files, "step")[0]
    assert step["status"] == "unchanged" and len(step["rows"]) == 2

    files = versions_diff.diff_versions(_ver(_step("Fetch", "")), _ver(_step("Fetch", "")))
    assert _by_kind(files, "step")[0]["rows"] == []


def test_parse_version_label_takes_vN_only():
    """§19: "vN" (case-insensitive, as execute's `version`) → N, anything else None —
    the caller answers the 404."""
    from autowright import versions_diff

    assert versions_diff.parse_version_label("v3") == 3
    assert versions_diff.parse_version_label("V3") == 3
    assert versions_diff.parse_version_label("3") == 3
    assert versions_diff.parse_version_label("v") is None
    assert versions_diff.parse_version_label("latest") is None
    assert versions_diff.parse_version_label(3) is None


# ---------------------------------------------------------------- the route

def _two_versions(client):
    """An automation whose v2 changes the first step's code; returns (id, v2 draft)."""
    a = client.post("/automations", json={"draft": make_version(), "name": "Diffed",
                                          "agentId": "mock"}).json()
    second = make_version(note="Change")
    second["steps"][0]["code"] = "from autowright import log\nlog('changed')\n"
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": second})
    assert r.json()["version"] == 2
    return a["id"], second


def test_version_diff_route_answers_the_file_list(client):
    """§19: `GET /automations/{id}/diff?from=vX&to=vY` answers { from, to, files } with
    the manifest first and the changed step marked — the current version included."""
    auto_id, _ = _two_versions(client)
    r = client.get(f"/automations/{auto_id}/diff?from=v1&to=v2")
    assert r.status_code == 200
    d = r.json()
    assert d["from"] == 1 and d["to"] == 2
    assert d["files"][0]["kind"] == "manifest" and d["files"][0]["file"] == "automation.yaml"
    changed = [f for f in d["files"] if f["kind"] == "step" and f["name"] == "Say hello"]
    assert len(changed) == 1 and changed[0]["status"] == "changed"
    assert changed[0]["added"] and changed[0]["removed"]


def test_version_diff_route_guards(client):
    """§19: 404 for an unknown automation or a label that names no stored version,
    400 when the two labels are the same, 422 when a label is missing."""
    auto_id, _ = _two_versions(client)
    assert client.get(f"/automations/{'0' * 36}/diff?from=v1&to=v2").status_code == 404
    assert client.get(f"/automations/{auto_id}/diff?from=v9&to=v2").status_code == 404
    assert client.get(f"/automations/{auto_id}/diff?from=latest&to=v2").status_code == 404
    assert client.get(f"/automations/{auto_id}/diff?from=v1&to=v1").status_code == 400
    assert client.get(f"/automations/{auto_id}/diff?from=v1").status_code == 422


# ---------------------------------------------------------------- the CLI command

CLI_AUTO = dict(FULL_AUTO, version=4)

DIFF_PAYLOAD = {
    "from": 1, "to": 4,
    "files": [
        {"kind": "manifest", "name": "Manifest", "file": "automation.yaml",
         "status": "unchanged", "added": 0, "removed": 0,
         "rows": [{"kind": "same", "left": {"number": 1, "text": "params: []"},
                   "right": {"number": 1, "text": "params: []"}}]},
        {"kind": "step", "name": "Fetch", "file": "01-fetch.py", "status": "changed",
         "added": 1, "removed": 1,
         "rows": [{"kind": "mod", "left": {"number": 1, "text": "print('old')"},
                   "right": {"number": 1, "text": "print('new')"}}]},
        {"kind": "notes", "name": "Notes", "file": "notes.md", "status": "new",
         "added": 2, "removed": 0,
         "rows": [{"kind": "add", "left": None, "right": {"number": 1, "text": "first"}},
                  {"kind": "add", "left": None, "right": {"number": 2, "text": "second"}}]},
    ],
}


def test_cmd_automation_diff_defaults_to_the_current_version(capsys):
    """§20: `--to` defaults to the current version, and the human output prints a
    `== <file> (<status>[, +a -r])` header per file with the prefixed rows under it."""
    gets = _auto_gets(CLI_AUTO,
                      **{f"/automations/{AUTO_ID}/diff?from=v1&to=v4": DIFF_PAYLOAD})
    _run(_RouteClient(gets), "automation", "diff", "Daily Report", "--from", "v1")
    assert capsys.readouterr().out.splitlines() == [
        "Daily Report: v1 → v4",
        "== automation.yaml (unchanged)",
        "== 01-fetch.py (changed, +1 -1)",
        "- print('old')",
        "+ print('new')",
        "== notes.md (new)",
        "+ first",
        "+ second",
    ]


def _same(text):
    return {"kind": "same", "left": {"number": 1, "text": text},
            "right": {"number": 1, "text": text}}


def _add(text):
    return {"kind": "add", "left": None, "right": {"number": 1, "text": text}}


def test_diff_rows_collapse_long_unchanged_runs(capsys):
    """§20/§9.2: in a changed file a run of more than 6 same rows collapses to one
    `… <n> unchanged lines` marker keeping 3 rows of context on each side."""
    from autowright import cli

    # A run at the very start has no head context — the marker comes first.
    rows = [_same(f"L{i}") for i in range(10)] + [_add("new")]
    assert cli.diff_rows_text(rows, collapse=True) == [
        "  … 7 unchanged lines", "  L7", "  L8", "  L9", "+ new"]

    # A run between two changes keeps 3 rows on each side.
    rows = [_add("top")] + [_same(f"L{i}") for i in range(10)] + [_add("new")]
    assert cli.diff_rows_text(rows, collapse=True) == [
        "+ top", "  L0", "  L1", "  L2", "  … 4 unchanged lines",
        "  L7", "  L8", "  L9", "+ new"]

    # 6 same rows is not more than 6 — nothing collapses.
    rows = [_same(f"L{i}") for i in range(6)] + [_add("new")]
    assert cli.diff_rows_text(rows, collapse=True) == [f"  L{i}" for i in range(6)] + ["+ new"]

    # New / removed files print every row.
    rows = [_same(f"L{i}") for i in range(10)] + [_add("new")]
    assert cli.diff_rows_text(rows, collapse=False) == \
        [f"  L{i}" for i in range(10)] + ["+ new"]

    # The same rule through the command: 12 leading same rows, then one add.
    payload = {"from": 1, "to": 4,
               "files": [{"kind": "step", "name": "Fetch", "file": "01-fetch.py",
                          "status": "changed", "added": 1, "removed": 0,
                          "rows": [_same(f"L{i}") for i in range(12)] + [_add("new")]}]}
    gets = _auto_gets(CLI_AUTO,
                      **{f"/automations/{AUTO_ID}/diff?from=v1&to=v4": payload})
    _run(_RouteClient(gets), "automation", "diff", "Daily Report", "--from", "v1")
    assert capsys.readouterr().out.splitlines() == [
        "Daily Report: v1 → v4",
        "== 01-fetch.py (changed, +1 -0)",
        "  … 9 unchanged lines", "  L9", "  L10", "  L11", "+ new",
    ]


def test_cmd_automation_diff_version_arg_forwarding_and_json(capsys):
    """§20: a `from` that is not vN exits with "version must be vN", `--to` is sent as
    given, and `--json` prints the endpoint's payload."""
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets(CLI_AUTO)), "automation", "diff", "Daily Report",
             "--from", "latest")
    assert "version must be vN" in str(ei.value.code)

    # No /automations/{id} entry: an explicit --to must not read the current version.
    gets = {"/automations": [CLI_AUTO],
            f"/automations/{AUTO_ID}/diff?from=v1&to=v3": DIFF_PAYLOAD}
    _run(_RouteClient(gets), "automation", "diff", "Daily Report", "--from", "v1",
         "--to", "v3")
    assert capsys.readouterr().out.splitlines()[0] == "Daily Report: v1 → v4"

    _run(_RouteClient(gets), "automation", "diff", "Daily Report", "--from", "v1",
         "--to", "v3", "--json")
    assert json.loads(capsys.readouterr().out) == DIFF_PAYLOAD
