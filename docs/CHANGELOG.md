# Changelog

## v0.9.1 - 2026-09-05

- The execution page's step rail is now a LOGS rail: each row opens one step's log, the pane header shows "LOG k OF n" with the step name, and the arrow keys flip between logs without leaving the page.
- Logs get the same find bar as the step viewer, with a line count and previous/next chevrons to step through matches, plus a "Show logs in Finder" button that opens the run's logs folder.
- Parameters and the workspace path move out of the rail into cards of their own on the execution page, collapsed by default along with the result FILES list since they hold reference material you open on demand.
- A step that prints nothing now leaves an empty log instead of a one-line placeholder, so it is clear at a glance which steps actually produced output.
- The BUILD card no longer claims it is in sync with the spec while a sync is still running; it shows a quiet "Syncing the steps with the spec…" line instead, with the live progress still in the thread.
- Drafting agents are now called authoring agents throughout the app, and the Agents page says plainly that agents both author automations and run the non-trivial steps inside them. The new-agent page describes each harness's local Ollama-backed models and links out to the Ollama library.
- Copy across the Agents, Secrets, Settings and About pages, the secret modal and the report modal is rewritten as plain sentences, and outbound links on the About page and report modal now carry the same external-link icon.
- Linux is now offered as an experimental AppImage download, and the download page detects your operating system and promotes the matching installer. Windows is no longer labeled experimental, though it keeps the unsigned-installer note.

## v0.9.0 - 2026-09-02

- Step scripts now open in a full-page viewer: a step navigator on the left, a line-numbered script pane on the right, and prev/next stepping through the whole automation without leaving the modal.
- Each step in the viewer lists what it actually does in plain words - the parameters it reads, the websites it contacts, what it asks the agent, files it hands to later steps, memory keys it touches, and whether it differs from the saved version.
- Find-in-script arrives in the step viewer: press ⌘F (Ctrl+F on Windows and Linux) or the magnifier to search a script, step through matches, and keep the search when you flip to another step.
- Editing the spec, notes, or build instructions now opens a proper editor over the page instead of turning the card into a text box, with a line count, ⌘S to save, and a confirm before discarding unsaved text.
- The BUILD & TEST panel splits into separate BUILD and TEST cards, each a single status line with its buttons on the right. Test runs open in a modal that shows the same live rail and logs as the execution page, and a test result is marked stale once you change the steps it ran against.
- On Windows and Linux the app calls the tray a tray instead of the menu bar, in trigger labels, CLI output, and scheduling notes. Linux drops the tray icon and panel entirely, so closing the last window quits the app.
- Stopping, deleting, or retrying an execution now reliably ends everything it started, including agent processes, and history cleanup no longer runs on the scheduler's clock, so a large cleanup can't delay a scheduled run.
- Fixed a paging glitch in the Executions list that could skip or repeat rows at the boundary between pages.

## v0.8.3 - 2026-08-31

- A What's-new changelog is now built into the app: open it from the About page to read the release notes for this and earlier versions.
- Acknowledgements now list the open-source components used on every platform, including the Windows and Linux packages that were previously missing.

## v0.8.2 - 2026-08-31

- The Executions list is now paged: 50 finished rows at a time with a Prev/Next pager, and deeper history fetched on demand.
- The status filter splits into per-status segments, including Running and Queued tabs; "Waiting" is now called "Queued" everywhere.

## v0.8.1 - 2026-08-30

- Schedules can opt out of catch-up: a per-trigger "Run if missed" setting decides whether a cron or one-time run slept through fires late or is dropped - in the trigger editor, the CLI, and transfer archives.
- Step rows show the retry budget next to the timeout, in the detail view and the editor.
- A sleep disclaimer appears wherever the app promises background firing.
- Linux installs get a proper launcher entry and app icon on first launch.

## v0.8.0 - 2026-08-29

- Windows gets a proper in-app title bar, with the sidebar and chat panel aligned beneath it.
- Editing a secret that is already set shows a masked "kept value" row, so it is clear the current value stays unless you replace it.
- The Build & test panel no longer jumps while a sync is running.
