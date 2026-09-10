# Privacy policy

Autowright collects nothing. There is no telemetry, no analytics, no crash
reporting, no account, and no Autowright server. This page explains where your
data lives and the few network connections the app can make — each one listed
below, with its off switch.

## Everything stays on your computer

- **Automations, versions, executions, settings** are stored in
  Autowright's data folder: `~/Library/Application Support/Autowright` on macOS,
  `%LOCALAPPDATA%\Autowright` on Windows, `~/.local/share/autowright` on Linux
  (execution data location is changeable in Settings). Nothing is synced or uploaded.
- **Secrets** (passwords, API keys) are stored in your operating system's secret store: the macOS Keychain, Windows Credential Manager, or the Linux Secret Service keyring. Secret
  values never appear in scripts, logs, or exported files, and are injected
  only at execution time.
- **Memory and logs** stay in the same local folders. You can open, snapshot,
  or delete all of it at any time.

## The AI you connect is your choice

When you create or edit an automation, Autowright sends the drafting context —
your description, the automation's spec, build instructions, and related step
code — to the AI agent **you** connected (Claude Code, Gemini CLI, Codex, or
OpenCode). That data is handled under that provider's own terms and privacy
policy, using your own account. If you use OpenCode with a local Ollama model,
drafting also stays entirely on your computer.

## Network connections Autowright itself makes

- **Check for updates** — once a day by default: one request to GitHub
  (raw.githubusercontent.com, where the update feed lives) to read the latest
  version number, nothing more.
  Turn off "Check for updates automatically" on the About page and Autowright
  never checks in the background — only when you press the button. Downloading
  an update — always started by you — also fetches it from GitHub. Those
  servers see your IP address, as with any web request.
- **Installing an AI tool, a model, or a Python library** — only when you ask
  during setup, or when an automation you save declares a library it needs.
  AI tools (Claude Code, Codex, Gemini CLI, OpenCode, and the Ollama app)
  download from each vendor's official source: the vendor's own installer
  script, npm for Gemini CLI, or ollama.com for the Ollama app. Local models
  download through Ollama from its model library (ollama.com). Python libraries
  download from PyPI (pypi.org) through pip as prebuilt wheels only, never built
  from source, into Autowright's own data folder; only the
  packages named in that automation's manifest, and only when one is missing or
  you press Update on its Packages card. Once installed, the Ollama app keeps
  itself up to date on its own, under Ollama's policy.

That is the complete list. Your automations' scripts can of course reach the
network themselves — but only in the ways you reviewed and saved.

## Changes

This policy lives at the root of the repository; any change to it is visible in
the project's git history.

_Last updated: 2026-09-09_
