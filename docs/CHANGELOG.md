# Changelog

## v0.10.2 - 2026-09-06

- Steps that use native Python packages such as numpy now work in the released app instead of failing the moment they import.
- Building an automation keeps showing live progress after you leave the page and come back, instead of going quiet.

## v0.10.1 - 2026-09-06

- The Executions page now filters through a single Filter modal, where you set status, automations, and a started-time range together and apply them at once.
- The execution page now ticks live timers: a running total in the header, and elapsed time on each executing step and attempt.
- `autowright execution list` takes repeatable `--automation` and `--status` filters plus `--since` and `--until` to narrow by start time.
- An automation's RECENT EXECUTIONS card now stops at 5 rows, so a busy automation no longer stretches the page.

## v0.10.0 - 2026-09-06

- Quitting the app, resetting all data, or restarting the backend can no longer hang: every service step is time-boxed, so a wedged machine gets a plain "service stop timed out" line instead of a QUIT card or reset overlay that spins forever. An install, quit, or reset that asks the backend whether anything is running and gets an error back now treats that as busy rather than idle, so none of them land on top of a running automation.
- Shutting down now stops the scheduler and the message listeners before anything is killed, so a cron tick or an incoming message arriving mid-quit can't start a run that is left half finished.
- Deleting an execution or an automation no longer stalls the rest of the app while a large run folder is removed: the record goes immediately and the files are cleared behind it, and anything a crash left half-deleted is swept at the next launch.
- Turning the menu bar icon off now closes its panel with it, and on Linux, where there is no dock and no tray, the app quits at that point instead of staying resident with nothing left to click.
- Discarding a draft or starting over now also cancels a test still running against that draft, and editing an automation that was deleted in another window returns you to the list instead of saving a stray new copy.
- Fixed parameter editing on the automation page: a list or key/value row you had typed in stopped picking up later changes for the rest of the session.
- Opening Autowright from a link while the window is still coming up now lands on the right page instead of nowhere.
- Find and the arrow-key shortcuts on the execution page and the step viewer now yield to the developer log overlay, and ⌘S in the spec, notes, and build-instructions editor yields to the discard confirm stacked above it.
- The memory card's size figures are cached briefly, so a large memory folder no longer slows every reconnect, and opening a memory file bigger than 8 MB now points you at the folder on disk instead of pulling the whole file into the window.
- Export now stops and names the step when step code references a secret or agent that no longer exists, instead of writing an archive that import would turn away. Import drops parameter values that match no parameter in the imported version, and rejects archives whose parameter definitions are incomplete.
- `autowright pull` now removes the managed files it no longer writes, so re-pulling into a folder can't resurrect a deleted step or note on the next push, and `autowright secret set --stdin` reads all of standard input, so a multi-line value such as a PEM key lands intact. Installing the `autowright` command leaves a different command of the same name alone and tells you where it sits instead of overwriting it.
- Ollama setup is bounded end to end: a stuck download or unpack now fails with a message and can be retried, replacing an existing Ollama app can no longer leave you with none, a failed model pull stops the card spinning, and the sign-in helper tells you to run the command yourself when Terminal doesn't answer.
- A damaged settings file now pauses history cleanup for the session, so the default 90-day window can't delete runs your settings said to keep, and a hand-edited execution record with an unquoted timestamp no longer keeps the app from starting.
- Clearing an automation's queue no longer cancels a queued run that had just started executing.

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
