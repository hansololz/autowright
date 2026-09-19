---
name: verify
description: Build, launch, and drive Autowright (Electron + Python backend) to verify changes at the real UI.
---

# Verifying Autowright changes

## Handles

- The `scripts/` directory is developer-only — agents must never run anything in it (a
  PreToolUse hook in `.claude/settings.json` blocks it). `dev.sh` is what the developer runs by
  hand for the HMR loop; for context: it installs the real launchd service, starts Vite, and
  launches Electron with `AUTOWRIGHT_RENDERER_URL`. NEVER point verification sessions at the real
  data dir — always isolate with `AUTOWRIGHT_HOME`.
- Verify by starting the pieces yourself (all backgroundable):
  1. Backend: `AUTOWRIGHT_HOME=<dir> AUTOWRIGHT_PORT=<port> .venv/bin/python -m autowright.main`
     - Backend always starts EMPTY (fresh onboarding). There is no seed command — demo data is a
       test fixture only (`tests/seed_data.py`); create data through the UI or API.
     - Agent calls shell out to the real CLIs (`claude`, etc.). For deterministic agent replies
       without a real AI, prepend the test fake to PATH: `PATH="$PWD/tests/bin:$PATH"` (same
       fake the pytest suite uses — real detect/invoke code path).
     - Secrets use the real macOS Keychain (service "Autowright") in every mode.
     - `backend.json` (port/token/pid) appears in `AUTOWRIGHT_HOME`.
  2. Renderer: `cd app && npm run build` — release delivery; Electron loads `app/dist` unless
     `AUTOWRIGHT_RENDERER_URL` points it at a Vite dev server.
  3. Drive Electron with playwright-core (`_electron.launch` with `cwd: app/`, env
     `AUTOWRIGHT_HOME=<dir>`) — electron main reads `backend.json` from `AUTOWRIGHT_HOME`.
     playwright-core resolves from `app/node_modules` — require it by absolute path if the
     driver lives outside `app/`.

## Gotchas

- **Driving Electron registers the REAL launchd service** even with an isolated
  `AUTOWRIGHT_HOME` — but only when no backend is already reachable: with a healthy
  backend.json in the isolated home, ensure-backend returns early and never registers
  (observed 2026-08-22; a `service stop` then reads "service was not installed").
  Otherwise main.cjs registers `ai.autowright.backend` on launch, and the
  service runs against the real `~/Library/Application Support/Autowright`. After a
  verify session, check `launchctl list | grep autowright` and restore the pre-session state
  (`launchctl bootout gui/501/ai.autowright.backend` + remove the plist if it didn't exist
  before). The real service can also touch real shims (heal/remove per the real `cliEnabled`),
  which can yank `~/.local/bin/autowright` out from under a verify run.

- **Never end a driven Electron through `electronApp.close()` or `app.quit()`** (§3 quit
  means quit for good, 2026-09-19): main.cjs intercepts any quit it did not start as the
  user's Quit and runs `service stop` against the REAL launchd job plus a sweep of every
  `-m autowright.` process on the machine — it would kill the developer's own backend.
  End a driven app with `electronApp.evaluate(({ app }) => app.exit(0))` (what
  `e2e/harness.ts` `closeApp` does), then kill the process if it lingers.
- **Driving Electron with the stored `login: true` registers REAL login items** pointing at
  the dev `app/node_modules/electron/dist/Electron.app` (the §4.9 apply-settings push runs on
  boot regardless of `AUTOWRIGHT_HOME`). After a verify session, check System Settings login
  items (`osascript -e 'tell application "System Events" to get the name of every login
  item'`) and remove any Electron/dev entries the run added (observed 2026-08-27: two).
- **Onboarding shows whenever `ad-onboarded` is absent from localStorage** (`store.ts` boot) —
  existing automations do NOT bypass it (observed 2026-08-31). Either click Continue past step 1
  without touching any "Set up …" card, or set `localStorage['ad-onboarded']` via
  `electronApp.evaluate`/page script and reload before driving.
- The onboarding install machines are REAL (§10/§19): clicking a "Set up …" suggestion card
  actually installs that CLI into `~/.local/bin` on this Mac, and sign-in help really opens
  Terminal/browser. Don't click them in automated runs unless that side effect is intended;
  found-card "Check connection" is safe (read-only readiness check).
- Flows drive fine headless-less on macOS; screenshot via `page.screenshot`.

## Worth driving

- Onboarding: step 1 self-check → step 2 detect/connect/install machines → Continue → step 3
  (Create flow) → Back (state must survive).
- Create flow with `PATH="$PWD/tests/bin:$PATH"` (fake claude envelope, no real AI needed).
