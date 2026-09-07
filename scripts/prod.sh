#!/usr/bin/env bash
# Build the production distribution (SPEC §3): "Autowright.app" with the
# relocatable CPython (python-build-standalone) + the autowright backend and
# curated packages in Contents/Resources/python/, plus a DMG (install
# artifact) and the electron-updater update zip — all
# under build/. Always Developer-ID-signed with hardened runtime and notarized —
# no ad-hoc fallback: a downloaded (quarantined) ad-hoc build can never start
# its backend LaunchAgent (Gatekeeper silently refuses to spawn the unsigned
# bundled Python). Identity: CODESIGN_IDENTITY, or auto-detected as the single
# "Developer ID Application" identity in the Keychain. Notarization uses the
# Keychain credentials profile named by NOTARY_PROFILE (default:
# autowright-notary).
#
#   ./scripts/prod.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---- version gate: refuse to build a distributable on version mismatch ----
"$ROOT/scripts/release.sh" --check

# ---- signing identity (required, resolved up front so failures are instant) ----
if [ -z "${CODESIGN_IDENTITY:-}" ]; then
  CODESIGN_IDENTITY="$(security find-identity -v -p codesigning \
    | grep 'Developer ID Application' | sed -n 's/.*"\(.*\)"/\1/p' | head -1)"
fi
if [ -z "$CODESIGN_IDENTITY" ]; then
  echo "no Developer ID Application identity found — set CODESIGN_IDENTITY or add one to the Keychain."
  echo "Distributable builds must be signed + notarized (SPEC §3); ad-hoc builds cannot run their backend after download."
  exit 1
fi
NOTARY_PROFILE="${NOTARY_PROFILE:-autowright-notary}"

# ---- fast build first (venv + deps + typecheck + renderer bundle) ----
"$ROOT/scripts/build.sh"

BUILD="$ROOT/build"
CACHE="$BUILD/cache"
mkdir -p "$CACHE"

ARCH="$(uname -m)"                       # arm64 | x86_64
case "$ARCH" in
  arm64)  PBS_ARCH="aarch64-apple-darwin"; EP_ARCH="arm64" ;;
  x86_64) PBS_ARCH="x86_64-apple-darwin";  EP_ARCH="x64" ;;  # electron-packager spells it x64
  *) echo "unsupported arch: $ARCH"; exit 1 ;;
esac

# ---- bundled relocatable CPython (python-build-standalone, pinned) ----
PBS_TAG="20260807"
PY_FULL="3.14.7"
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

# Backend + curated packages (§6.2) install into the bundled interpreter.
# pip's bin/ entry-point scripts get absolute staging-path shebangs, so inside
# the bundle the backend/CLI execute as `python3 -m autowright.main` / `-m autowright.cli`.
echo "· upgrading bundled pip"
"$PYSTAGE/bin/python3" -m pip -q install --upgrade pip

# -c backend/constraints.txt pins the whole runtime closure (SPEC §17): the
# pyproject floors alone would let two DMGs from the same commit ship different
# fastapi/lxml/requests, and the §6.2 curated packages are the user-facing step
# environment.
echo "· installing backend into bundled Python (pinned by backend/constraints.txt)"
"$PYSTAGE/bin/python3" -m pip -q install -c "$ROOT/backend/constraints.txt" "$ROOT/backend"

# ---- app icon (checked-in, SPEC §14) ----
cp "$ROOT/app/electron/icon/icon.icns" "$BUILD/icon.icns"

# ---- package Electron (.app) ----
# Only electron/ + dist/ + package.json + electron-updater's runtime closure
# ship: the renderer is fully bundled into dist/ and main.cjs/preload.cjs use
# Electron builtins plus electron-updater (§3), so src/, the vite scaffolding
# and every other node_module stay out of the bundle. The closure is computed,
# not hand-pinned, so an electron-updater upgrade can never silently strand a
# missing transitive dependency inside the bundle.
UPDATER_PKGS="$(cd "$ROOT/app" && node -e '
const seen = new Set()
const walk = (name) => {
  if (seen.has(name)) return
  seen.add(name)
  const pkg = require(`./node_modules/${name}/package.json`)
  Object.keys(pkg.dependencies || {}).forEach(walk)
}
walk("electron-updater")
process.stdout.write([...seen].sort().join("|"))
')"
[ -n "$UPDATER_PKGS" ] || { echo "failed to compute the electron-updater dependency closure"; exit 1; }
echo "· packaging Autowright.app"
(cd "$ROOT/app" && npx electron-packager . "Autowright" \
  --platform=darwin --arch="$EP_ARCH" --out "$BUILD/pkg" --overwrite \
  --icon "$BUILD/icon.icns" \
  --app-bundle-id ai.autowright.app \
  --ignore '^/src($|/)' \
  --ignore "^/node_modules/(?!($UPDATER_PKGS)(/|\$))" \
  --ignore '^/dev-app-update\.yml$' \
  --ignore '^/e2e($|/)' \
  --ignore '^/tests($|/)' \
  --ignore '^/brand-electron\.cjs$' \
  --ignore '^/ds-entry\.ts$' \
  --ignore '^/index\.html$' \
  --ignore '^/vite\.config\.ts$' \
  --ignore '^/vitest\.config\.ts$' \
  --ignore '^/vitest\.e2e\.config\.ts$' \
  --ignore '^/tsconfig\.json$' \
  --ignore '^/UI-GUIDE\.md$' \
  --ignore '^/package-lock\.json$')

APP="$BUILD/pkg/Autowright-darwin-$ARCH/Autowright.app"
[ -d "$APP" ] || { echo "packaging failed: $APP missing"; exit 1; }

echo "· bundling Python → Contents/Resources/python"
rm -rf "$APP/Contents/Resources/python"
cp -R "$PYSTAGE" "$APP/Contents/Resources/python"

# ---- §3 electron-updater config (app-update.yml) ----
# electron-updater reads it at runtime for its cache directory name (main.cjs
# passes the feed URL to the constructor, which overrides provider/url — they
# are still written accurately). Must land before signing: it is part of the
# bundle's sealed resources.
cat > "$APP/Contents/Resources/app-update.yml" <<EOF
provider: generic
url: https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-$ARCH/
updaterCacheDirName: autowright-updater
EOF

# ---- smoke check: bundled interpreter works from inside the bundle ----
# MUST run before signing: imports write .pyc files into Resources/python, and
# any write after signing breaks the bundle's resource seal (notarization then
# rejects the main binary). PYTHONDONTWRITEBYTECODE keeps the tree untouched.
PYTHONDONTWRITEBYTECODE=1 "$APP/Contents/Resources/python/bin/python3" -c \
  'import autowright, fastapi, uvicorn, websockets, yaml, keyring, requests, httpx, bs4, lxml, feedparser, dateutil' \
  || { echo "bundled Python smoke check failed"; exit 1; }
echo "· bundled Python imports OK"

# ---- codesign (SPEC §3: inside-out, never --deep) ----
# --deep leaves Electron Framework's nested dylibs unsigned/un-timestamped and
# breaks the outer seal — notarization rejects the archive. Sign every Mach-O
# explicitly, innermost first, then bundles outward. Electron/V8 needs JIT
# entitlements under the hardened runtime or the app crashes at launch.
ENTITLEMENTS="$BUILD/entitlements.plist"
cat > "$ENTITLEMENTS" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
</dict>
</plist>
EOF

# Interpreter entitlement (SPEC §3): hardened-runtime library validation only
# lets a process load code signed by Apple or by its own Team ID, and the §6.2
# declared packages are pip wheels whose extension modules are ad-hoc signed
# (no Team ID). Without this exception every native wheel (numpy, pandas,
# pillow, ...) installs fine and then dies at import with dlopen's "different
# Team IDs". Only the Python executables carry it: entitlements come from the
# process's main executable, so the .so/.dylib files and the Electron side
# (which keeps full library validation) never get it.
PY_ENTITLEMENTS="$BUILD/python-entitlements.plist"
cat > "$PY_ENTITLEMENTS" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict>
</plist>
EOF

echo "· codesigning (identity: $CODESIGN_IDENTITY, hardened runtime, inside-out)"
SIGN=(codesign --force --options runtime --timestamp -s "$CODESIGN_IDENTITY")

# 1. Python tree: shared objects, then executables. The Mach-O ones (the
# interpreter binaries) get the interpreter entitlement; pip's script
# entry points (text, never executed inside the bundle) sign as before.
find "$APP/Contents/Resources/python" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 \
  | xargs -0 -n 16 "${SIGN[@]}"
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'Mach-O'; then
    "${SIGN[@]}" --entitlements "$PY_ENTITLEMENTS" "$f"
  else
    "${SIGN[@]}" "$f"
  fi
done < <(find "$APP/Contents/Resources/python/bin" -type f -perm +111 -print0)

# 2. every Mach-O inside Frameworks — detect by content, not name/location:
# executables hide in odd places (Squirrel's Resources/ShipIt, Electron's
# Helpers/chrome_crashpad_handler) and any one left unsigned fails notarization.
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'Mach-O'; then "${SIGN[@]}" "$f"; fi
done < <(find "$APP/Contents/Frameworks" -type f -print0)

# 3. framework bundles
for fw in "$APP/Contents/Frameworks/"*.framework; do
  "${SIGN[@]}" "$fw"
done

# 4. Electron helper apps (GPU/Renderer/Plugin) — JIT entitlements
for helper in "$APP/Contents/Frameworks/"*.app; do
  "${SIGN[@]}" --entitlements "$ENTITLEMENTS" "$helper"
done

# 5. the app bundle itself
"${SIGN[@]}" --entitlements "$ENTITLEMENTS" "$APP"
codesign --verify --deep --strict "$APP"

# ---- post-sign probe (SPEC §3): the signed interpreter loads an ad-hoc .so ----
# A copy of one bundled extension module, re-signed ad-hoc exactly the way a
# pip wheel's extension arrives, must dlopen under the signed interpreter.
# Fails the build the moment the interpreter entitlement drops off, instead of
# shipping a DMG whose every native wheel is dead. Runs with
# PYTHONDONTWRITEBYTECODE=1 and a probe file outside the bundle, so it writes
# nothing into the sealed tree (the seal is re-verified below regardless).
PROBE="$BUILD/adhoc-probe.so"
PROBE_SRC="$(find "$APP/Contents/Resources/python/lib" -name '*.so' -print | head -1)"
[ -n "$PROBE_SRC" ] || { echo "no extension module found for the ad-hoc probe"; exit 1; }
cp "$PROBE_SRC" "$PROBE"
codesign --force -s - "$PROBE"
PYTHONDONTWRITEBYTECODE=1 "$APP/Contents/Resources/python/bin/python3" -c \
  'import ctypes, sys; ctypes.CDLL(sys.argv[1])' "$PROBE" \
  || { echo "signed interpreter cannot load an ad-hoc-signed extension: interpreter entitlement missing (SPEC §3)"; exit 1; }
rm -f "$PROBE"
echo "· signed interpreter loads ad-hoc extensions OK"

# ---- post-sign native-wheel probe (SPEC §3): a real wheel, the whole §6.2 path ----
# The ad-hoc probe proves dlopen; this proves what a user's step actually does:
# the signed interpreter's own pip installs a wheel with a compiled extension
# (numpy - the package the 0.10.2 report named) with the same --only-binary /
# --target form packages.ensure uses, into a directory outside the bundle, and
# then imports it from there. Network is already a build prerequisite (the
# CPython download, notarization); pip's HTTP cache keeps repeat builds cheap.
# PYTHONDONTWRITEBYTECODE=1 keeps pip's own imports from writing .pyc files
# into the sealed tree (the seal is re-verified below regardless).
WHEEL_PROBE="$BUILD/wheel-probe"
rm -rf "$WHEEL_PROBE"
PYTHONDONTWRITEBYTECODE=1 "$APP/Contents/Resources/python/bin/python3" -m pip -q install \
  --no-input --disable-pip-version-check --only-binary ':all:' \
  --target "$WHEEL_PROBE" numpy \
  || { echo "signed interpreter's pip could not install the numpy probe wheel (SPEC §3)"; exit 1; }
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WHEEL_PROBE" "$APP/Contents/Resources/python/bin/python3" -c \
  'import numpy; numpy.zeros(1)' \
  || { echo "signed interpreter cannot import a native wheel (numpy): interpreter entitlement missing (SPEC §3)"; exit 1; }
rm -rf "$WHEEL_PROBE"
echo "· signed interpreter imports a native wheel (numpy) OK"

# ---- notarize the app (SPEC §3) ----
# Submit a zip of the signed app, then staple the ticket onto the app itself so
# the DMG below carries a stapled bundle (Gatekeeper passes offline).
# Re-verify the seal right before submission — anything that touched the bundle
# since signing (a stray .pyc, a log file) makes notarization reject it.
codesign --verify --deep --strict "$APP" \
  || { echo "bundle was modified after signing — aborting before notarization"; exit 1; }
APPZIP="$BUILD/Autowright-notarize.zip"
rm -f "$APPZIP"
ditto -c -k --keepParent "$APP" "$APPZIP"
echo "· notarizing app (profile: $NOTARY_PROFILE — takes a few minutes)"
xcrun notarytool submit "$APPZIP" --keychain-profile "$NOTARY_PROFILE" --wait \
  || { echo "notarization failed — inspect with: xcrun notarytool log <submission-id> --keychain-profile $NOTARY_PROFILE"; exit 1; }
rm -f "$APPZIP"
xcrun stapler staple "$APP"

# ---- update zip (SPEC §3: the electron-updater/Squirrel update artifact) ----
# Built from the same stapled bundle the DMG carries, so the app inside
# already passes Gatekeeper offline; the zip itself needs no notarization.
VERSION="$(node -p "require('$ROOT/app/package.json').version")"
ZIP="$BUILD/Autowright-$VERSION-darwin-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

# ---- DMG (SPEC §3: the install artifact — website download + Homebrew cask) ----
DMG="$BUILD/Autowright-$VERSION-darwin-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "Autowright" -srcfolder "$APP" -ov -quiet -format UDZO "$DMG"

# ---- notarize the DMG ----
codesign --force --timestamp -s "$CODESIGN_IDENTITY" "$DMG"
echo "· notarizing DMG (profile: $NOTARY_PROFILE)"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait \
  || { echo "notarization failed — inspect with: xcrun notarytool log <submission-id> --keychain-profile $NOTARY_PROFILE"; exit 1; }
xcrun stapler staple "$DMG"

# ---- final gate: never emit an artifact Gatekeeper would reject ----
spctl --assess --type open --context context:primary-signature "$DMG" \
  || { echo "Gatekeeper rejected the DMG — do not distribute"; exit 1; }
spctl --assess --type execute "$APP" \
  || { echo "Gatekeeper rejected the app — do not distribute"; exit 1; }
echo "· Gatekeeper assessment OK (app + DMG)"

echo "· dist done:"
echo "    $APP"
echo "    $DMG"
echo "    $ZIP"
