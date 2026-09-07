# Reminder: silent-empty-run detector (`result.count` + `output-collapsed`)

**Commit:** `1b485db` on `main`, 2026-09-07
"Add result.count and the output-collapsed problem audit"

Revisit this when: the zero-only rule feels too strict or too loose, someone asks for a
percentage-drop threshold, or a release ships the new `count` shape.

## The problem it solves

A deterministic automation can keep succeeding (exit 0, no error, no notification) while
returning nothing at all, because the page it scrapes restructured, an export was renamed,
or an API field disappeared. Nothing failed, so nothing told you. This came out of an
HN-style critique of the product; it was the one real gap, since auto-rewrite is already
impossible by design.

## The solution

### 1. Steps report their input size: `result.count(n)` (§6.1, `spec/engine.md`)

- `n` is a non-negative int. Bools and anything else raise in the step. Last call wins.
- Semantics are **size of the input**, not the diff: rows scraped, messages read, files
  processed, matches found, *before* any comparison against memory. A collapse to an
  empty input is the signal.
- `framework-instructions.md` teaches the agent to call it as the last step's habit
  whenever the job has a countable input, 0 included, so history accrues with no user setup.
- Stored as `count` on `execution.yaml` and a nullable `count` column in `executions.db`
  (§4.5). Not redacted, an int carries no secret.

### 2. The audit: `output-collapsed` problem kind (§4.1, `spec/data-model.md`)

Raised when, reading the execution index only:

- the automation's most recent **finished** real execution (skipped/queued/test records
  excluded, an in-flight `executing` record skipped so a running job neither raises nor
  clears) **succeeded with `count` 0**, and
- the earlier succeeded real executions that reported a count, the most recent ten of
  them and at least three, have a **median >= 1**.

Deliberately zero-only, never a percentage drop: a count that varies day to day must not
cry wolf, and zero is the collapse with no error to notify on. Fewer than three counted
runs, a history whose median is already 0, a failed or cancelled latest run, or a latest
run with no count at all: no problem raised.

Memoized in `a["_collapse"]`, invalidated on settle and delete. Carries `typical` (the
median) so the UI can say what the automation usually finds.

### 3. Where it surfaces

- Needs-fixing chip on the automation (§9.1) and the banner row with **Fix with AI** (§9.2).
  The fix seed is the collapse variant: `fixExec` is now `{executionId, collapse?: {typical}}`
  (§11), so the AI starts from the zero run plus the typical count.
- RESULT header shows an "N items" MetaChip (§7).
- Tray dot and menu-bar panel count include it (`TRAY_ALERT_KINDS`, §13).
- CLI prints `count: N items`; agent context gets "item count: N".
- One notification at execution end, on episode start only. `notify(text)` still wins.

### 4. Compatibility (§21.4, `spec/compatibility.md`)

- Additive key: an absent `count` reads as null, so pre-existing runs are treated as
  uncounted and never trip the audit.
- `executions.db` `SCHEMA_VERSION` bumped to 9, drop-and-rebuild, no migration code.
- Fixture test: `tests/test_storage.py::test_exec_yaml_without_count_loads`.
- First version writing the new shape: the next release after 2026-09-07. Oldest shape
  still read: v0.6.0.

## Left undone on purpose

- No §14 em-dash rule was added. New labels are em-dash-free, but the wider app-copy
  purge is still half done and waits on a go-ahead.

## Files touched (35)

Backend: `engine.py`, `executor.py`, `execdb.py`, `storage.py`, `testexec.py`, `cli.py`,
`instructions/framework-instructions.md`.
Renderer: `store.ts`, `types.ts`, `result.tsx`, `AutomationDetail.tsx`, `CreateFlow.tsx`,
`ExecutionPage.tsx`, `MenuBarPanel.tsx`, `electron/main.cjs`.
Spec: `agent-pipeline.md`, `cli.md`, `compatibility.md`, `data-model.md`, `engine.md`,
`execution.md`, `storage.md`, `ui-create-edit.md`, `ui-shell.md`.
Tests: pytest (`test_cli`, `test_drafting`, `test_engine`, `test_execdb`, `test_storage`)
and vitest render/store tests. All suites green and screenshot-verified at commit time.
