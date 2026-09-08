"""§11 Test (§19 POST /tests): executes the sent draft's steps as a §4.5 test
execution record (kind `test`, trigger kind `test`) through the exact
engine path a real execution takes — record, workspace/result/logs, and the
`exec.*` events all ordinary. Test-specific pieces live here: the sent draft's
scripts land in the record's steps/ dir, memory is a scratch copy discarded at
the end, one test record per draft container (409 while one is live, previous
record deleted at start), and the last-test summary lands in the container's
test.yaml. This module also builds the §8 chat call's RECENT EXECUTIONS context —
failure analysis is an ordinary chat job reading it (there is no separate
analysis call)."""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from . import paths, timefmt
from .engine import Engine, _step_sha
from .events import hub
from .storage import exec_version_label, is_test, safe_step_filename, store
from .yamlio import save_yaml

LOG_TAIL = 40      # lines per step handed to the §8 RECENT EXECUTIONS section
EXECUTIONS_CAP = 5  # §8: newest settled executions included, across all §4.5 kinds
RESULT_EXCERPT = 2000  # chars of result.md shown for a detailed successful execution


def start(engine: Engine, draft: dict, auto: dict | None,
          enabled_agents: list, allowed_secrets: list, param_values: dict,
          trigger_payload: dict | None = None,
          steps_fingerprint: str | None = None) -> str:
    """Create and launch the test execution record; returns its exec id.
    Raises RuntimeError while the container already has a live test (§19 409)."""
    container_id = auto["id"] if auto else None  # §4.5: null automationId on create-mode tests
    with store.lock:
        if any(is_test(h) and h["automation_id"] == container_id
               and h["status"] == "executing" for h in store.execs.values()):
            raise RuntimeError("a test is already executing — cancel it or wait for it to finish")
        # §11 keep-latest: one test record per draft container.
        store.delete_test_execs(container_id)

        # Same trust boundary as version writes: client `file` names are
        # sanitized before they hit the record's steps/ directory.
        taken: set[str] = set()
        steps = []
        for i, s in enumerate(draft.get("steps", []), 1):
            f = safe_step_filename(s.get("file"), i, s.get("name"), taken)
            taken.add(f)
            steps.append({**s, "file": f})
        ver = {"steps": steps, "params": draft.get("params", []) or [],
               "packages": draft.get("packages", []) or [],
               "spec": draft.get("spec") or []}
        # §11: in-editor grants and values, never the stored automation's — the
        # engine reads them off this shadow record; the real one is untouched.
        shadow = {
            "id": container_id,
            "name": (auto["name"] if auto else draft.get("name")) or "New automation",
            "enabled_agents": enabled_agents,
            "allowed_secrets": allowed_secrets,
            "param_values": {**(auto["param_values"] if auto else {}), **(param_values or {})},
        }
        rec_steps = [{"name": s.get("name", ""), "file": s["file"],
                      "agent": bool(s.get("agent")), "sha": _step_sha(s),
                      "status": "queued", "duration_ms": None, "attempts": []}
                     for s in steps]
        # §19 triggerMock: the mocked §4.5 payload rides the record like a real
        # firing's — the trigger kind stays `test`, so labels still say "Test".
        h = store.create_execution(shadow, "test", None, "test", rec_steps,
                                   params=store.merged_params(shadow, ver),
                                   trigger_payload=trigger_payload)

    # Any setup failure past this point must take the record with it: a
    # permanent "executing" test record trips the 409 above forever (and
    # delete_test_execs skips executing records), so only a backend restart
    # would ever unblock testing again.
    scratch: Path | None = None
    try:
        # The sent draft's scripts, as executed (§5 steps/) — a real version
        # folder serves this role for ordinary executions.
        steps_dir = store.exec_dir(h["id"]) / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        for s in steps:
            (steps_dir / s["file"]).write_text(s.get("code", ""), encoding="utf-8")

        # §11 scratch memory: draft container's memory/ when present (edit mode
        # falls back to the automation's), create mode the pending slot's — copied
        # to a temp dir and discarded when the test ends.
        dbase = (store.auto_dir(auto) / "draft") if auto is not None else paths.pending_draft_dir()
        scratch = Path(tempfile.mkdtemp(prefix="autowright-test-"))
        mem_dir = scratch / "memory"
        src = dbase / "memory"
        if auto is not None and not src.exists():
            src = store.auto_dir(auto) / "memory"
        if src.exists():
            shutil.copytree(src, mem_dir)
        mem_dir.mkdir(parents=True, exist_ok=True)
        (dbase / "test.yaml").unlink(missing_ok=True)  # wiped at each test start (§5)

        # §11 stale-outcome rule: the renderer's opaque steps fingerprint rides
        # the record to the summary write below (`steps_fingerprint`, §5).
        h["_test"] = {"vdir": str(steps_dir), "mem": str(mem_dir), "fp": steps_fingerprint}
        state = {"proc": None, "cancel": False}
        with engine._lock:
            engine._live[h["id"]] = state
        # §4.5: test executions never change display state — no automation row.
        hub.publish("execution.started", executionId=h["id"], automationId=container_id,
                    execution=store.exec_json(h), automation=None)
        t = threading.Thread(target=_run, args=(engine, shadow, ver, h, state, dbase, scratch),
                             daemon=True)
        # §19 delete waits on `engine.wait_finished`, which finds the thread
        # here - an unregistered one makes that wait a no-op and the rmtree can
        # race a step still dying.
        state["thread"] = t
        t.start()
        return h["id"]
    except BaseException:
        with engine._lock:
            engine._live.pop(h["id"], None)
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        store.delete_execution(h["id"])
        raise


def _run(engine: Engine, shadow: dict, ver: dict, h: dict, state: dict,
         dbase: Path, scratch: Path) -> None:
    try:
        engine._execute(shadow, ver, h, state)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)  # §11: the memory copy is discarded
    # §11 test lifetime: a draft settle that raced this test cancelled and
    # marked it (§19 `_draft_settled`) — or, if the test settled first, already
    # deleted the record. Either way the test leaves nothing behind: no record,
    # and no test.yaml written into the settled container. Checked and written
    # under the lock so a settle can't land between the check and the write.
    orphaned = False
    with store.lock:
        if (h.get("_draft_settled") or h["id"] not in store.execs
                or (h["automation_id"] is not None
                    and h["automation_id"] not in store.autos)):
            # The owner-gone arm is the §19 delete backstop: a test thread that
            # somehow outlived delete's kill grace must not re-create the
            # removed automation directory by writing test.yaml into it.
            orphaned = True
        elif h["status"] in ("succeeded", "failed"):
            # §5 test.yaml — the last-test summary a resumed draft's Test card
            # shows; deleted with the draft. A failed test is NOT analyzed here —
            # analysis is a user-sent §8 chat message reading executions_context.
            summary = {
                "status": h["status"],
                "when": timefmt.now_iso(),
                "execution_id": h["id"],
            }
            fp = (h.get("_test") or {}).get("fp")
            if fp:  # additive, written only when the client sent one (§21)
                summary["steps_fingerprint"] = fp
            save_yaml(dbase / "test.yaml", summary)
    if orphaned:
        store.delete_execution(h["id"])


def _log_tail(h: dict, idx: int) -> list[str]:
    # §4.5: attempt numbers are monotonic and old attempts prune — the latest
    # attempt's `n` names the newest log file, never the list length.
    atts = h["steps"][idx].get("attempts") or []
    attempt = atts[-1]["number"] if atts else 1
    lines = store.read_log(h["id"], idx, attempt)
    return [l.get("text", "") for l in lines][-LOG_TAIL:]


def executions_context(auto: dict | None, current_steps: list[dict],
                       execution_id: str | None = None) -> str | None:
    """§8 RECENT EXECUTIONS: the newest settled executions of this automation/draft
    (all §4.5 kinds — the draft's test record, Draft executions, version
    executions), newest first, capped at EXECUTIONS_CAP. The newest execution — and the
    `execution_id` one (§19, the Fix-with-AI entry), however old — carries full
    detail: per-step outcomes, the error, log tails, the result. Every execution is
    marked against the in-editor step code (§4.5 per-step sha) so the agent
    never reads stale output as current behavior. None when no execution exists."""
    container_id = auto["id"] if auto else None
    with store.lock:
        hs = [h for h in store.execs.values()
              if h["automation_id"] == container_id
              and h["status"] not in ("executing", "queued")]
        hs.sort(key=lambda h: h.get("started_at") or "", reverse=True)
        picked = hs[:EXECUTIONS_CAP]
        if execution_id and all(h["id"] != execution_id for h in picked):
            f = store.execs.get(execution_id)
            if (f and f["automation_id"] == container_id
                    and f["status"] not in ("executing", "queued")):
                picked.append(f)
    if not picked:
        return None
    cur_shas = [_step_sha(s) for s in current_steps or []]
    blocks = []
    for i, h in enumerate(picked):
        full = store.exec_full(h["id"]) or h
        detail = i == 0 or (execution_id is not None and h["id"] == execution_id)
        blocks.append(_execution_block(full, cur_shas, detail))
    return "\n\n".join(blocks)


def _execution_block(h: dict, cur_shas: list[str], detail: bool) -> str:
    steps = h.get("steps") or []
    shas = [s.get("sha") for s in steps]
    stale = ("steps match the current draft" if shas == cur_shas
             else "ran older steps — treat its behavior as historical")
    started = ""
    if h.get("started_at"):
        started = timefmt.started_label(timefmt.parse_local(h["started_at"]))
    head = (f"--- {exec_version_label(h)} execution · {h.get('status')} · started {started} · "
            f"trigger {h.get('trigger')} · {stale} ---")
    lines = [head]
    err = h.get("error") or {}
    if not detail:
        if err.get("message"):
            lines.append(f"failed at step {err.get('step')}: {err['message']}")
        return "\n".join(lines)
    for i, s in enumerate(steps, 1):
        dur = f" · {s['duration_ms'] // 1000}s" if s.get("duration_ms") else ""
        lines.append(f"step {i}: {s.get('name')} — {s.get('status')}{dur}")
    if err.get("message"):
        lines.append(f"error at step {err.get('step')}: {err['message']}"
                     + (f" (possible reason: {err['reason']})" if err.get("reason") else ""))
    failed_at = next((i for i, s in enumerate(steps) if s.get("status") == "failed"), None)
    if failed_at is not None:
        # The failing step's tail plus the earlier steps' — the cause is often upstream.
        for j in range(failed_at):
            if tail := _log_tail(h, j):
                lines.append(f"log tail (step {j + 1}):\n" + "\n".join(tail))
        if tail := _log_tail(h, failed_at):
            lines.append("log tail (failing step):\n" + "\n".join(tail))
    if h.get("chip"):
        lines.append(f"result chip: {h['chip']}")
    files = store.result_files(h["id"])
    if files:
        lines.append("result files: " + ", ".join(f["name"] for f in files))
    rmd = store.exec_dir(h["id"]) / "result" / "result.md"
    if rmd.is_file():
        text = rmd.read_text(encoding="utf-8").strip()
        if len(text) > RESULT_EXCERPT:
            text = text[:RESULT_EXCERPT] + "\n… [result.md truncated]"
        if text:
            lines.append("result.md:\n" + text)
    return "\n".join(lines)
