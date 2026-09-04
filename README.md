# <img src="app/electron/icon/icon.png" width="40" alt="Autowright logo" align="top"> Autowright

> Make it easy to automate desktop tasks. You describe the job once and the app will run it forever.

**Website: [autowright.ai](https://autowright.ai)**

Autowright is a desktop app for recurring personal automations. You describe the job in
plain words, a connected AI agent writes it as human-readable Python step scripts, you review and approve, and a
local scheduler runs those exact scripts on time. AI is used once, at authoring time.

## Install

Download from [autowright.ai](https://autowright.ai) or the
[latest release](https://github.com/hansololz/autowright/releases/latest):

- **macOS** (Apple Silicon) - DMG, Developer-ID signed and notarized, or
  `brew install --cask hansololz/tap/autowright`
- **Windows** (x64) - installer, unsigned (expect a SmartScreen warning)
- **Linux** (x86_64) - AppImage, **unstable**: early port, lags behind the macOS release,
  and known to fail on Ubuntu 24.04+ (AppArmor user-namespace restriction)

macOS and Windows are the supported platforms today; the Linux build is provided for
early testers and may break between releases.

Or build from source - see §18 in [SPEC.md](SPEC.md).

## Features

- **Plain-words authoring and editing** - the AI does the scripting, you do the deciding;
  nothing executes until you approve it.
- **Use your own agent** - Claude Code, Gemini CLI, Codex, or OpenCode; OpenCode can drive
  a local Ollama model for fully offline drafting.
- **Real scheduling** - cron with per-trigger timezones, one-shot triggers, run-on-app-start,
  and manual "Execute now". Runs even with the app closed (background service), with a
  missed-run policy for sleep and downtime. Works best on an always-on Mac like a Mac mini
  or Mac Studio. A MacBook that is asleep would not trigger automations.
- **Versioned automations** - every approved edit is a new version; drafts run in isolation
  before you promote them.
- **Persistent memory with snapshots** - automations keep state between runs, with automatic
  snapshots and one-click restore.
- **Live execution view** - per-step status, streamed logs, and full execution history.
- **Menu-bar / tray surface** - glance at what's running and fire jobs without opening the
  window.
- **Local and file-first** - automations are YAML and Python on disk, secrets stay in the OS
  secret store (Keychain / Credential Manager), and everything runs on your machine.
- **Share automations** - portable `.autowright` export/import, from the app or the CLI.
- **CLI with full app parity** - author automations as files, execute and follow them,
  manage secrets and triggers. Headless- and agent-friendly with explicit per-automation
  secret grants.

## Planned

- **Agent skill** - drive Autowright straight from your agent chat session (create, edit,
  and run automations without opening the app); see `skills/autowright/`.
- **Headless pip package** - run the backend and CLI without the desktop app.
- **GitHub sync** - keep your automations in a repo and pull changes into the app.
- **Automation & agent marketplace** - browse, share, and install automations and agents
  made by others.
- **More harness integrations** and **richer triggers** - file-system changes, calendar
  events, and more.

## Status

Early release, under active development. Feedback and issues are very welcome. `SPEC.md` is
the source of truth for the whole app; see §18 for the dev workflow.

Product ideas and feature requests: open a GitHub issue (see [CONTRIBUTING.md](CONTRIBUTING.md));
accepted ideas are planned on the repo's GitHub Project.
Found a security problem? Please report it privately; see [SECURITY.md](docs/SECURITY.md).
