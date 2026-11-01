#!/usr/bin/env bash
# Build Autowright from the repo. Touches no processes and no data dir.
#
#   ./scripts/build.sh          fast build: deps (below) + typecheck + bundle the
#                               renderer (npm run build → app/dist, the bundle
#                               Electron loads in release).
#
#   ./scripts/build.sh --deps   deps only: venv + backend deps (re-install when
#                               backend/pyproject.toml changed, stamp file
#                               .venv/.backend-stamp), npm ci (from the lockfile)
#                               when app/package.json changed. Used by dev.sh, which
#                               serves the renderer via Vite instead of app/dist.
#
# Production distributables (.app + DMG) are built by ./scripts/prod.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- version sync (VERSION is the single source; no-op leaves mtimes alone) ----
"$ROOT/scripts/release.sh" --sync

# ---- backend ----
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "· creating venv"
  python3.14 -m venv "$ROOT/.venv"
fi
PY_STAMP="$ROOT/.venv/.backend-stamp"
if [ ! -f "$PY_STAMP" ] || [ "$ROOT/backend/pyproject.toml" -nt "$PY_STAMP" ]; then
  echo "· installing backend (pyproject.toml changed)"
  "$ROOT/.venv/bin/pip" -q install -e "$ROOT/backend[dev]"
  # setuptools leaves a full copy of the package tree in backend/build/ plus the
  # egg-info metadata. Nothing reads them (the editable install points at
  # backend/autowright), but a stale copy poisons every repo-wide search, so
  # drop them the moment the install is done.
  rm -rf "$ROOT/backend/build" "$ROOT/backend"/*.egg-info
  touch "$PY_STAMP"
fi

# ---- app deps ----
if [ ! -d "$ROOT/app/node_modules" ] \
   || [ "$ROOT/app/package.json" -nt "$ROOT/app/node_modules/.package-lock.json" ]; then
  # npm ci, not npm install: the lockfile is the source of truth, so a build
  # can never silently float a dependency to a newer version.
  echo "· npm ci"
  (cd "$ROOT/app" && npm ci --no-audit --no-fund)
fi
# Self-heal (§18): the electron package downloads and unpacks its binary in its
# own postinstall. An interrupted download — or a lockfile check above that saw
# nothing to reinstall — leaves node_modules/electron without dist/, and
# `npx electron .` then dies with "Electron failed to install correctly".
# Re-run that postinstall ourselves instead of asking for a full reinstall.
if [ -d "$ROOT/app/node_modules/electron" ] && [ ! -d "$ROOT/app/node_modules/electron/dist" ]; then
  echo "· electron binary missing — running its postinstall download"
  (cd "$ROOT/app/node_modules/electron" && node install.js)
fi

# ---- acknowledgements (§4.9) — regenerate so it tracks dependency changes ----
echo "· regenerating app/src/acknowledgements.md"
"$ROOT/.venv/bin/python" "$ROOT/scripts/gen_licenses.py" > /dev/null

if [ "${1:-}" = "--deps" ]; then
  echo "· deps done (backend: .venv, app: node_modules)"
  exit 0
fi

# ---- renderer build (Electron loads app/dist, same as release) ----
echo "· building renderer (typecheck + bundle → app/dist)"
(cd "$ROOT/app" && npm run build)

echo "· build done (backend: .venv, renderer: app/dist)"
