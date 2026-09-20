# <img src="app/electron/icon/icon.png" width="40" alt="Autowright logo" align="top"> Autowright

Desktop app that runs recurring jobs on your machine. You describe the job in plain
English, an AI agent turns it into Python scripts, you read them and approve, and a local
scheduler runs the same scripts on schedule from then on. The AI is only involved when you
write or edit a job. It never touches a run.

Website: [autowright.ai](https://autowright.ai)

## Install

Grab a build from [autowright.ai](https://autowright.ai) or the
[releases page](https://github.com/hansololz/autowright/releases/latest).

| Platform | Package | Notes |
| --- | --- | --- |
| macOS (Apple Silicon) | DMG | Signed and notarized. Or `brew install --cask hansololz/tap/autowright` |
| Windows (x64) | Installer | Unsigned, so SmartScreen will complain. Click through. |
| Linux (x86_64) | AppImage | Unstable. Lags behind macOS and is known broken on Ubuntu 24.04+ (AppArmor user namespaces). |

macOS is the main target and gets updates first. Windows follows shortly after. The Linux
build is there for people who want to try it and may break between releases.

To build from source, clone the repo and run `./scripts/dev.sh`. It sets up the venv and
node_modules and launches the app. Details are in §18 of [spec/dev-test.md](spec/dev-test.md).

## How it works

1. Open the app, type what you want done ("every weekday at 8, pull my calendar for the day
   and post it to my Discord channel").
2. Your agent (Claude Code, Gemini CLI, Codex, or OpenCode) writes the job as a set of Python
   step scripts plus a YAML manifest. OpenCode can point at a local Ollama model if you want
   to stay offline.
3. A background service runs the job on its triggers, app open or not.

Every approved edit is a new version. Drafts run in isolation until you promote them. Jobs
keep state between runs, with snapshots you can roll back to.

## What's in the box

- **Triggers**: cron with a timezone per trigger, intervals ("every 6 hours since the last
  run"), one-shot, on app start, and a manual Execute button. Missed runs from sleep or
  downtime follow a per-trigger policy.
- **Execution view**: per-step status, streamed logs, full history.
- **Menu bar / tray**: see what's running and fire a job without opening the window.
- **File-first**: every job is YAML and Python in a folder on disk. Secrets live in the OS
  secret store (Keychain on macOS, Credential Manager on Windows). Nothing leaves your
  machine unless a script sends it.
- **Export / import**: `.autowright` archives, from the app or the CLI.
- **Marketplace**: browse catalogs of shared jobs, install from them, publish your own.
- **CLI**: everything the app does, from the terminal. Handy for headless boxes and for
  letting an agent drive Autowright directly. Secret access is granted per job.

```
autowright status
autowright automation list|show|create|execute|export|import ...
autowright execution list|show|tail|cancel|retry ...
autowright secret list|set|delete
autowright marketplace list|add|install ...
autowright service install|uninstall|status|restart|stop
```

A note on hardware: the scheduler can only fire when the machine is awake. An always-on Mac
mini or Mac Studio is ideal. A MacBook with the lid closed will miss runs.

## Roadmap

- Agent skill so you can create and run jobs from your agent's chat without opening the app
  (see `skills/autowright/`).
- Headless pip package: backend plus CLI, no Electron.
- GitHub sync for job folders.
- More triggers (file-system changes, calendar events) and more agent harnesses.

## Status

Early and moving fast. Expect rough edges. Bug reports and feature requests go in GitHub
issues; see [CONTRIBUTING.md](CONTRIBUTING.md). Security problems: please report privately,
see [docs/SECURITY.md](docs/SECURITY.md).

[SPEC.md](SPEC.md) is the source of truth for how the whole thing works. If the code and the
spec disagree, the spec wins and the code is the bug.

## License

MIT. See [LICENSE](LICENSE).
