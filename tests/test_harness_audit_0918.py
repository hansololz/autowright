"""Regression tests for the 2026-09-18 harness audit — one per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`)."""
import threading


# ---------- §8 rule 1: shape-aware ===FILE: detection ----------

def test_file_mark_outside_fences_skips_every_fenced_marker():
    """§8 rule 1: the envelope starts at the first ===FILE: line OUTSIDE a
    markdown fence — a reply whose every marker is fenced holds none."""
    from autowright import harness

    fenced_only = ("Like this:\n```yaml\n===FILE: actions.yaml===\nsync: true\n```\n")
    assert harness.file_mark_outside_fences(fenced_only) is None
    both = fenced_only + "\n===FILE: notes.md===\n- real\n===END===\n"
    m = harness.file_mark_outside_fences(both)
    assert m is not None and m.group(1) == "notes.md"
    assert harness.file_mark_outside_fences("no markers here at all") is None


def test_recombine_keeps_a_fenced_marker_in_the_prose():
    """§8 rule 1: the recombiner splits stdout prose at the envelope's real
    start — a fenced example the agent quoted is prose, and cutting there
    would have dropped the rest of the answer from the recombined reply."""
    from autowright import harness

    stdout = ("Here's the shape:\n```yaml\n===FILE: actions.yaml===\nsync: true\n```\n"
              "Files are attached.\n")
    out = harness._recombine(stdout, [("notes.md", "- a thing\n")])
    assert out == (stdout.strip() + "\n"
                   "===FILE: notes.md===\n- a thing\n\n"
                   "===END===")


# ---------- §8: the scratch watcher never outlives its call ----------

def test_scratch_watcher_emits_nothing_after_a_timed_out_join(tmp_path):
    """§8: stop() marks the watcher dead BEFORE the join — a poll the join
    times out on must not keep feeding `file` events into a finished call
    (they would land in the next round's progress feed)."""
    from autowright import harness

    seen: list[str] = []
    in_sink = threading.Event()
    release = threading.Event()

    def on_file(name: str, content: str) -> None:
        seen.append(name)
        in_sink.set()
        release.wait(10)

    (tmp_path / "01-fetch.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text("steps: []\n", encoding="utf-8")
    watcher = harness._ScratchWatcher(tmp_path, harness.ProgressSink(on_file=on_file))
    watcher.start()
    # the poll thread is now parked inside the sink on the first document,
    # holding the call open past stop()
    assert in_sink.wait(5)

    stopped = threading.Thread(target=watcher.stop, daemon=True)
    stopped.start()
    stopped.join(timeout=20)  # stop()'s own join times out after 5 s
    assert not stopped.is_alive()
    release.set()
    # the parked poll resumes with the call already over: the second document
    # never reaches the sink, though documents() still carries it
    for _ in range(20):
        if len(seen) > 1:
            break
        threading.Event().wait(0.1)
    assert seen == ["01-fetch.py"]
    assert [n for n, _ in watcher.documents()] == ["01-fetch.py", "manifest.yaml"]
