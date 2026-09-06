#!/usr/bin/env bash
# Build the Linux production distribution (SPEC §3): the x86-64 AppImage
# `Autowright-<version>-linux-x86_64.AppImage`, carrying the relocatable
# CPython (python-build-standalone) + the autowright backend and curated
# packages in resources/python/ — under build/linux/.
#
# The order mirrors scripts/prod.sh (§3): version gate, renderer build,
# bundled interpreter (download → stage → pip install pinned by
# backend/constraints.txt → smoke test), then packaging. No signing leg:
# Linux has no Gatekeeper/SmartScreen equivalent (§3 — unsigned by design).
#
#   ./linux-scripts/prod.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- version gate: refuse to build a distributable on version mismatch ------
# The same three-site check `release.sh --check` performs, reimplemented here
# the way prod.ps1 does (release.sh itself stays bash/BSD-sed on macOS, §17 —
# rewriting the sites is its job; this only verifies).
VERSION="$(tr -d '[:space:]' < "$ROOT/release/VERSION")"
[ -n "$VERSION" ] || { echo "empty $ROOT/release/VERSION"; exit 1; }
mismatch=0
check_site() { # <repo-relative path> <sed -E pattern with one capture>
  local sitepath="$1" pattern="$2" found
  found="$(sed -nE "s/$pattern/\1/p" "$ROOT/$sitepath" | head -1)"
  [ -n "$found" ] || { echo "no version line found in $sitepath"; exit 1; }
  if [ "$found" != "$VERSION" ]; then
    echo "version mismatch: $sitepath has $found, VERSION has $VERSION"
    mismatch=1
  fi
}
check_site 'app/package.json'               '^  "version": "([^"]+)",$'
check_site 'backend/pyproject.toml'         '^version = "([^"]+)"$'
check_site 'backend/autowright/__init__.py' '^__version__ = "([^"]+)"$'
[ "$mismatch" = 0 ] || {
  echo "run ./scripts/release.sh --sync (macOS) to rewrite the version sites"; exit 1
}
echo "· version OK: $VERSION"

BUILD="$ROOT/build"
CACHE="$BUILD/cache"
OUT="$BUILD/linux"
mkdir -p "$CACHE"

[ "$(uname -m)" = "x86_64" ] || {
  echo "unsupported arch: $(uname -m) — the Linux distributable is x86-64 only (§3)"; exit 1
}

# ---- renderer bundle (app/dist) --------------------------------------------
# build.sh's renderer half: dependencies from the lockfile, then the
# typechecked vite build. electron-builder packages app/dist, never the vite
# scaffolding.
echo "· npm ci"
(cd "$ROOT/app" && npm ci)
echo "· vite build (typechecked)"
(cd "$ROOT/app" && npm run build)

# ---- bundled relocatable CPython (python-build-standalone, pinned) ----------
# The same release tag and CPython version prod.sh pins, in the Linux flavor:
# the *-unknown-linux-gnu-install_only build, whose layout matches macOS
# (python/bin/python3 — resolved by the §2 platform layer's bundledPythonPath).
PBS_TAG="20260807"
PY_FULL="3.14.7"
PBS_ARCH="x86_64-unknown-linux-gnu"
PBS_URL="${AUTOWRIGHT_PBS_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PY_FULL+$PBS_TAG-$PBS_ARCH-install_only.tar.gz}"
TARBALL="$CACHE/$(basename "$PBS_URL")"
if [ ! -f "$TARBALL" ]; then
  echo "· downloading bundled Python ($PY_FULL, $PBS_ARCH)"
  curl -fL --retry 3 -o "$TARBALL.tmp" "$PBS_URL"
  mv "$TARBALL.tmp" "$TARBALL"
fi

PYSTAGE="$BUILD/python"
echo "· staging bundled Python → build/python"
rm -rf "$PYSTAGE"
mkdir -p "$PYSTAGE"
tar -xzf "$TARBALL" -C "$PYSTAGE" --strip-components 1   # tarball root is python/

STAGED_PY="$PYSTAGE/bin/python3"
[ -x "$STAGED_PY" ] || { echo "staging failed: $STAGED_PY missing"; exit 1; }

echo "· upgrading bundled pip"
"$STAGED_PY" -m pip -q install --upgrade pip

# -c backend/constraints.txt pins the whole runtime closure (§17): the
# pyproject floors alone would let two AppImages from the same commit ship
# different fastapi/lxml/requests, and the §6.2 curated packages are the
# user-facing step environment. pip's bin/ entry points carry staging-path
# shebangs; nothing may invoke them (§3 — always `python3 -m autowright.*`).
echo "· installing backend into bundled Python (pinned by backend/constraints.txt)"
"$STAGED_PY" -m pip -q install -c "$ROOT/backend/constraints.txt" "$ROOT/backend"

# ---- smoke check: the bundled interpreter works before it is packaged -------
# §3: before packaging and with PYTHONDONTWRITEBYTECODE, so importing cannot
# add anything to the tree that is about to ship — the artifact is exactly
# what pip staged. secretstorage is keyring's §17 Linux Secret Service
# backend; without it every secret read silently answers nothing.
PYTHONDONTWRITEBYTECODE=1 "$STAGED_PY" -c \
  'import autowright, fastapi, uvicorn, websockets, yaml, keyring, secretstorage, requests, httpx, bs4, lxml, feedparser, dateutil; print(autowright.__version__)' \
  || { echo "bundled Python smoke check failed"; exit 1; }
echo "· bundled Python imports OK"

# ---- package (electron-builder, Linux AppImage only) ------------------------
# The build config lives in app/package.json's `build` key: appId
# ai.autowright.app, the AppImage/x64 target with the §3 Linux artifact name,
# the staged interpreter as extraResources → resources/python, and the
# `linux.publish` generic entry (the §3 Linux feed base — it puts app-update.yml
# in the AppImage and makes the build emit latest-linux.yml).
# The output directory is overridden here — the checked-in config's
# `directories.output` points at build/win for prod.ps1. `--publish never`:
# uploading is linux-scripts/release.sh's job.
rm -rf "$OUT"
echo "· packaging (electron-builder --linux appimage --x64)"
(cd "$ROOT/app" && npx electron-builder --linux appimage --x64 --publish never \
  -c.directories.output="$OUT")

# The §3 update-feed inputs must exist beside the AppImage — release.sh uploads
# the AppImage and rewrites the yml into the repo-root release/linux-x86_64/.
# No separate .blockmap file: the AppImage target embeds the block map in the
# AppImage itself (the yml's blockMapSize; AppImageUpdater range-reads the tail).
APPIMAGE="$OUT/Autowright-$VERSION-linux-x86_64.AppImage"
for artifact in "$APPIMAGE" "$OUT/latest-linux.yml"; do
  [ -f "$artifact" ] || { echo "packaging failed: $artifact missing"; exit 1; }
done

echo "· dist done:"
du -m "$APPIMAGE" | awk '{printf "    %s  (%s MB)\n", $2, $1}'
