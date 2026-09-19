#!/usr/bin/env bash
# Fastest way to launch Autowright from the repo, with hot reloading: deps only (no
# renderer bundle), then the real backend, a Vite dev server for the renderer
# (HMR — edits to app/src apply live), and Electron pointed at it via
# AUTOWRIGHT_RENDERER_URL (§15). Same real code everywhere: real data dir
# (~/Library/Application Support/Autowright), macOS Keychain, random free port,
# backend as the real launchd LaunchAgent (ai.autowright.backend, §3:
# RunAtLoad/KeepAlive), no mocks, no seed data.
# Backend edits still need a restart (the backend is not hot-reloaded).
# Ctrl+C shuts the whole app down (Electron, vite, and the backend);
# quitting Electron normally (Cmd+Q) is the §3 quit-entirely flow, like
# release: the backend stops with the UI (plist stays; `service restart`
# brings it back headless).
#
#   ./scripts/dev.sh    deps, restart the service, vite + Electron with HMR
#
# Isolated mode (opt-in): setting any AUTOWRIGHT_* knob makes dev.sh spawn the
# backend directly with that env instead of via launchd (the plist carries no
# env). Knobs (§15): AUTOWRIGHT_HOME (isolated data dir), AUTOWRIGHT_PORT,
# AUTOWRIGHT_OLLAMA_URL, AUTOWRIGHT_STEP_TIMEOUT.
#   --fresh    wipe the data dir first — refused unless AUTOWRIGHT_HOME is set
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_HOME="$HOME/Library/Application Support/Autowright"
DATA="${AUTOWRIGHT_HOME:-$RELEASE_HOME}"
# logs live outside the data dir (§5): ~/Library/Logs, or <home>/logs when isolated
LOGS="${AUTOWRIGHT_HOME:+$DATA/logs}"
LOGS="${LOGS:-$HOME/Library/Logs/Autowright}"

# any AUTOWRIGHT_* env present → isolated mode (direct spawn so the env reaches the backend)
ISOLATED=0
if env | grep -q '^AUTOWRIGHT_'; then ISOLATED=1; fi

if [ "${1:-}" = "--fresh" ]; then
  if [ -z "${AUTOWRIGHT_HOME:-}" ]; then
    echo "--fresh would wipe the real app data ($RELEASE_HOME)."
    echo "Set AUTOWRIGHT_HOME to an isolated dir to use --fresh."
    exit 1
  fi
  echo "· wiping $DATA"
  rm -rf "$DATA"
fi
mkdir -p "$DATA" "$LOGS"

# ---- deps only (venv + backend deps + npm deps — no renderer bundle) ----
"$ROOT/scripts/build.sh" --deps

# kill lingering processes matching a command-line pattern (scoped to this repo)
kill_stale() {
  local pattern="$1" pids
  pids=$(pgrep -f "$pattern" 2>/dev/null || true)
  [ -z "$pids" ] && return 0
  echo "· stopping lingering processes (pid $pids): $pattern"
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 25); do
    pgrep -f "$pattern" > /dev/null 2>&1 || return 0
    sleep 0.2
  done
  pkill -9 -f "$pattern" 2>/dev/null || true
}

# ---- shut down lingering processes from previous sessions ----
# ps shows the venv python's RESOLVED binary (e.g. Homebrew Python.app), never
# "$ROOT/.venv/bin/python", so match the module invocation itself. Backends can
# be stuck in graceful shutdown (uvicorn waits on open WebSockets) — the
# kill_stale SIGKILL fallback is what actually clears those.
BEFORE=$(cat "$DATA/backend.json" 2>/dev/null || true)
"$ROOT/.venv/bin/autowright" service uninstall > /dev/null 2>&1 || true
kill_stale "[Pp]ython -m autowright"                   # backend (python -m autowright.main)
kill_stale "$ROOT/.venv/bin/autowright"                # autowright / autowright-backend entry points
kill_stale "$ROOT/app/node_modules/electron"         # electron (incl. helper processes)
kill_stale "$ROOT/app/node_modules/.bin/vite"        # vite dev server

# ---- backend ----
if [ "$ISOLATED" = "1" ]; then
  # direct spawn, detached, launchd-like environment (cwd /, minimal PATH)
  echo "· starting backend (isolated mode, data: $DATA)"
  (cd / && PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    "$ROOT/.venv/bin/python" -m autowright.main \
    > "$LOGS/backend.out.log" 2> "$LOGS/backend.err.log" &)
else
  # the real thing: launchd LaunchAgent, exactly as a release install starts it
  echo "· installing launchd service (data: $DATA)"
  "$ROOT/.venv/bin/autowright" service install
fi

# Ctrl+C shuts the whole app down: the terminal's SIGINT reaches Electron as
# an OS quit (§3 quit-entirely — it stops the backend itself or finds it
# already gone, and quits the UI either way), the EXIT trap below clears
# vite, and this stops the backend — service uninstall first (launchd
# KeepAlive would otherwise respawn it), then kill_stale, because a plain
# SIGTERM leaves uvicorn hanging in graceful shutdown and only its SIGKILL
# fallback clears that.
shutdown_backend() {
  echo
  echo "· ctrl-c — stopping the backend"
  "$ROOT/.venv/bin/autowright" service uninstall > /dev/null 2>&1 || true
  kill_stale "[Pp]ython -m autowright"
  exit 130
}
trap shutdown_backend INT TERM

# ---- vite dev server (starts while the backend comes up) ----
VPORT=$("$ROOT/.venv/bin/python" -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
echo "· starting vite dev server on :$VPORT (log: $LOGS/vite.log)"
(cd "$ROOT/app" && npx vite --host 127.0.0.1 --port "$VPORT" --strictPort \
  > "$LOGS/vite.log" 2>&1 &)
trap 'pkill -f "$ROOT/app/node_modules/.bin/vite" 2>/dev/null || true' EXIT

# wait for a fresh backend.json (rewritten with new pid/token on every start)
PORT=""
for _ in $(seq 1 75); do
  CUR=$(cat "$DATA/backend.json" 2>/dev/null || true)
  if [ -n "$CUR" ] && [ "$CUR" != "$BEFORE" ]; then
    PORT=$(python3 -c "import json;print(json.load(open('$DATA/backend.json'))['port'])" 2>/dev/null || true)
    if [ -n "$PORT" ] && curl -sf "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then break; fi
    PORT=""
  fi
  sleep 0.2
done
[ -n "$PORT" ] || { echo "backend didn't come up — see $LOGS/backend.err.log"; exit 1; }
echo "· backend healthy on :$PORT (logs: $LOGS/backend.{out,err}.log)"

# wait for vite to answer
VITE_UP=""
for _ in $(seq 1 75); do
  if curl -sf "http://127.0.0.1:$VPORT/" > /dev/null 2>&1; then VITE_UP=1; break; fi
  sleep 0.2
done
[ -n "$VITE_UP" ] || { echo "vite didn't come up — see $LOGS/vite.log"; exit 1; }

# ---- electron (foreground; Cmd+Q stops the backend too, like release) ----
echo "· launching Electron (HMR via http://127.0.0.1:$VPORT)"
cd "$ROOT/app" && AUTOWRIGHT_RENDERER_URL="http://127.0.0.1:$VPORT" npx electron . || true
