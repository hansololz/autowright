#!/usr/bin/env bash
# Single version source (SPEC §17/§18): the release/VERSION file, synced into
# the three version sites - app/package.json, backend/pyproject.toml, and
# backend/autowright/__init__.py. A release is three steps:
#
#   1. ./scripts/release-start.sh <version>   bump VERSION + draft the release notes
#   2. curate docs/CHANGELOG.md, then commit (./scripts/commit.sh)
#   3. ./scripts/release.sh                   this script: cut the release
#      then windows-scripts/release.ps1 and linux-scripts/release.sh append their
#      artifacts to the release this script created, on their machines.
#
#   ./scripts/release.sh             cut the release the committed VERSION names: run
#                                    the full test suite (fast gate → integration → E2E),
#                                    push HEAD, build the distributable via prod.sh,
#                                    then publish a GitHub release (tag v<VERSION>)
#                                    with the DMG (install) and update zip attached
#                                    and the curated docs/CHANGELOG.md section as its
#                                    notes, rewrite the §3 electron-updater feed
#                                    (latest-mac.yml) in release/ plus the
#                                    docs/downloads.json download entry (and, for
#                                    0.6.1 only, the §3 legacy-bridge feed.json), and
#                                    last publish the §3 Homebrew cask to the
#                                    homebrew-tap repo. Takes no version: the version
#                                    was chosen by release-start.sh. Needs a clean
#                                    working tree on main (the prep committed), an
#                                    authenticated `gh` CLI, a VERSION higher than the
#                                    newest release tag, the version sites in sync, and
#                                    a non-empty '## v<VERSION>' section in
#                                    docs/CHANGELOG.md (§18) - it never drafts notes.
#   ./scripts/release.sh --sync      rewrite the sites from VERSION (build.sh and
#                                    release-start.sh run this)
#   ./scripts/release.sh --check     verify all sites match VERSION; exit 1 listing
#                                    mismatches (prod.sh refuses to build on failure)
#   ./scripts/release.sh --cask      republish the §3 Homebrew cask for the current
#                                    VERSION against its existing GitHub release -
#                                    the recovery path when the cask step failed after
#                                    the release went out (a re-run can't: the tag
#                                    already exists). Idempotent.
#   ./scripts/release.sh --feed      rewrite and push the §3 update feed
#                                    (release/darwin-<arch>/latest-mac.yml, plus the
#                                    legacy feed.json for 0.6.1 only) and the
#                                    docs/downloads.json entry for the current VERSION
#                                    against its existing GitHub release - the recovery
#                                    path when the feed commit or push failed after the
#                                    release went out. Reuses the zip in build/ for the
#                                    yml's sha512/size when it survived, otherwise
#                                    downloads the released zip to hash it. Idempotent.
#
# Files are rewritten only when their version actually differs, so pyproject.toml's
# mtime (build.sh's .backend-stamp trigger) never churns on a no-op sync.
# Developer-only: agents must never run this script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$ROOT/release/VERSION"
CHANGELOG="$ROOT/docs/CHANGELOG.md"

PKG_JSON="$ROOT/app/package.json"
PYPROJECT="$ROOT/backend/pyproject.toml"
INIT_PY="$ROOT/backend/autowright/__init__.py"

# §3 Homebrew cask: a separate repository, always checked out beside this one.
TAP_DIR="$(dirname "$ROOT")/homebrew-tap"
TAP_REMOTE="https://github.com/hansololz/homebrew-tap.git"
CASK_REL="Casks/autowright.rb"
# The cask's `livecheck` reads the same §3 latest-mac.yml the in-app updater reads. The
# cask is arm64-only, so it is always the arm64 mac feed - and always the raw GitHub URL,
# never the retired autowright.ai/updates/… Pages path and never the frozen legacy
# feed.json (pinned at 0.6.1 forever, it would silently stop livecheck reporting).
# publish_cask pins it on every release so a hand-edit or a stale checkout can never
# leave `brew livecheck` pointing at a dead host.
CASK_LIVECHECK_URL="https://raw.githubusercontent.com/hansololz/autowright/main/release/darwin-arm64/latest-mac.yml"

ARCH="$(uname -m)"

# §3 update feed for the built arch + the §17 website download index. Both are
# rewritten after the GitHub release exists, and pushed together.
FEED="$ROOT/release/darwin-$ARCH/latest-mac.yml"
DOWNLOADS="$ROOT/docs/downloads.json"

# §3 legacy 0.6.0 bridge: the Squirrel.Mac JSON feed pre-0.6.1 installs read. It is
# rewritten exactly once - by the 0.6.1 release, pointing stranded 0.6.0 copies at the
# 0.6.1 DMG - and frozen forever after (§21.4 decision log; the §15 drift guards pin it).
LEGACY_FEED="$ROOT/release/darwin-$ARCH/feed.json"
LEGACY_BRIDGE_VERSION="0.6.1"

# GNU sed and BSD sed disagree on in-place editing (GNU: -i, BSD: -i '') - probe
# the dialect once so the version rewrites run on Linux too (build.sh --sync path).
if sed --version > /dev/null 2>&1; then SED_I=(sed -i -E); else SED_I=(sed -i '' -E); fi

usage() {
  echo "usage: $(basename "$0") [--sync | --check | --cask | --feed]"
  echo "       (no argument cuts the release the committed VERSION names;"
  echo "        pick the version with: ./scripts/release-start.sh <version>)"
  exit 2
}

# require_gh - the GitHub CLI, installed and authenticated
require_gh() {
  command -v gh > /dev/null \
    || { echo "gh CLI not found - install with: brew install gh && gh auth login"; exit 1; }
  gh auth status > /dev/null 2>&1 \
    || { echo "gh CLI not authenticated - run: gh auth login"; exit 1; }
}

# require_main_branch - this repo's checkout must sit on main before anything is
# published. The §3 update feeds are fetched from
# raw.githubusercontent.com/<owner>/<repo>/main/release/..., so a feed committed on
# any other branch is never the file installed apps read: the release would go out
# with an update feed that silently stays at the previous version. Same rule
# tap_preflight applies to the homebrew-tap checkout, applied to this repo.
require_main_branch() {
  local branch
  branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "main" ] \
    || { echo "on branch '$branch', not main - the §3 feed URLs are pinned to /main/; switch before releasing"; exit 1; }
}

# semver_gt <a> <b> - is a strictly higher than b? The numeric base compares first; a
# release beats its own prereleases; prereleases compare lexically. The same rule
# release-start.sh applied when the version was chosen.
semver_gt() {
  python3 - "$1" "$2" << 'PY'
import sys
def parts(v):
    base, _, pre = v.partition("-")
    return tuple(int(x) for x in base.split(".")), pre
(a, apre), (b, bpre) = parts(sys.argv[1]), parts(sys.argv[2])
sys.exit(0 if a > b or (a == b and bpre != "" and (apre == "" or apre > bpre)) else 1)
PY
}

# tap_preflight - make the §3 Homebrew tap checkout ready to receive a bump: cloned,
# on main, clean, holding the cask, and fast-forwarded to origin. The checkout is
# always `../homebrew-tap`, the sibling of this repo. Called before a release modifies
# anything, so a broken tap can never strand a published release next to an un-bumped
# cask.
tap_preflight() {
  if [ ! -d "$TAP_DIR/.git" ]; then
    echo "· cloning homebrew tap into ${TAP_DIR}"
    git clone -q "$TAP_REMOTE" "$TAP_DIR" \
      || { echo "failed to clone $TAP_REMOTE into $TAP_DIR"; exit 1; }
  fi
  [ -f "$TAP_DIR/$CASK_REL" ] \
    || { echo "cask missing: $TAP_DIR/$CASK_REL"; exit 1; }
  local branch
  branch="$(git -C "$TAP_DIR" rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "main" ] \
    || { echo "homebrew tap is on '$branch', not main - switch it before releasing"; exit 1; }
  [ -z "$(git -C "$TAP_DIR" status --porcelain)" ] \
    || { echo "homebrew tap working tree dirty ($TAP_DIR) - commit or stash before releasing"; exit 1; }
  git -C "$TAP_DIR" fetch -q origin main \
    || { echo "cannot reach the homebrew tap remote ($TAP_REMOTE)"; exit 1; }
  git -C "$TAP_DIR" merge -q --ff-only origin/main \
    || { echo "homebrew tap has diverged from origin/main ($TAP_DIR) - reconcile it before releasing"; exit 1; }
}

# publish_cask <version> <dmg> - point the cask at a released DMG and push the tap.
# Only ever called once the GitHub release is live: the cask pins that asset's hash.
# arm64 only - the cask declares `depends_on arch: :arm64`, so an x86_64 release must
# not overwrite its URL and hash. Idempotent: an already-current cask pushes nothing.
publish_cask() {
  local version="$1" dmg="$2" cask="$TAP_DIR/$CASK_REL" sha style_out
  if [ "$ARCH" != "arm64" ]; then
    echo "· skipping homebrew cask (built $ARCH; the cask is arm64-only)"
    return 0
  fi
  sha="$(shasum -a 256 "$dmg" | awk '{print $1}')"
  echo "· updating homebrew cask ($version, sha256 ${sha:0:12}…)"
  "${SED_I[@]}" "s/^(  version \")[^\"]+(\")$/\1$version\2/" "$cask"
  "${SED_I[@]}" "s/^(  sha256 \")[^\"]+(\")$/\1$sha\2/" "$cask"
  grep -q "^  version \"$version\"$" "$cask" \
    || { echo "cask version line not rewritten - check $cask"; exit 1; }
  grep -q "^  sha256 \"$sha\"$" "$cask" \
    || { echo "cask sha256 line not rewritten - check $cask"; exit 1; }
  # The livecheck URL is pinned, not just left alone: it is the only line naming the §3
  # feed host, and a stale one fails silently (brew livecheck just stops reporting). The
  # four-space indent scopes the match to the `livecheck do` block - the cask's own `url`
  # stanza sits at two spaces and ends in a comma.
  "${SED_I[@]}" "s|^(    url \")[^\"]+(\")$|\1$CASK_LIVECHECK_URL\2|" "$cask"
  grep -q "^    url \"$CASK_LIVECHECK_URL\"$" "$cask" \
    || { echo "cask livecheck url not rewritten - check $cask"; exit 1; }
  # Output is captured rather than streamed: brew re-bundles its rubocop gems on stderr
  # from time to time, which has no place in a release log unless the lint fails.
  if command -v brew > /dev/null; then
    if ! style_out="$(brew style "$cask" 2>&1)"; then
      echo "brew style failed on $cask"
      printf '%s\n' "$style_out"
      exit 1
    fi
  fi
  if [ -n "$(git -C "$TAP_DIR" status --porcelain)" ]; then
    git -C "$TAP_DIR" commit -q -am "autowright $version"
  else
    echo "· cask already at $version - no bump commit"
  fi
  # Pushing is decided by what origin is missing, not by whether this run wrote the
  # bump: a tap holding earlier local commits (a README edit, a hand-fixed stanza)
  # must still reach GitHub, or the published cask silently lags the checkout.
  if [ -z "$(git -C "$TAP_DIR" log --oneline origin/main..main)" ]; then
    echo "· homebrew tap already in sync with origin/main - nothing to push"
    return 0
  fi
  git -C "$TAP_DIR" push -q origin main \
    || { echo "failed to push the homebrew tap - re-run: $(basename "$0") --cask"; exit 1; }
  echo "· homebrew cask published (hansololz/tap/autowright $version)"
}

# release_asset_url <version> <ext> - the download URL of that release's arm64/x86_64
# artifact (.dmg installs, .zip updates). One spelling of the URL for every caller, so
# the feed, the download index, and the --feed recovery path can never disagree about it.
release_asset_url() {
  local version="$1" ext="$2" owner_repo
  owner_repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  printf 'https://github.com/%s/releases/download/v%s/Autowright-%s-darwin-%s%s' \
    "$owner_repo" "$version" "$version" "$ARCH" "$ext"
}

# write_feed <version> <zip-url> <zip-path> - rewrite the built arch's electron-updater
# feed (SPEC §3, latest-mac.yml): the released zip's absolute URL plus its base64 sha512
# and byte size, computed from the local artifact (electron-updater refuses a download
# whose digest disagrees, so the hashed file must be the uploaded one). Called only once
# the release is live, so the feed never names a URL that isn't. Only the zip is listed -
# the DMG is the install artifact and belongs to docs/downloads.json.
write_feed() {
  local version="$1" zip_url="$2" zip_path="$3" sha512 size
  sha512="$(openssl dgst -sha512 -binary "$zip_path" | base64 | tr -d '\n')"
  size="$(stat -f%z "$zip_path" 2>/dev/null || stat -c%s "$zip_path")"
  mkdir -p "$(dirname "$FEED")"
  cat > "$FEED" <<EOF
version: $version
files:
  - url: $zip_url
    sha512: $sha512
    size: $size
path: $zip_url
sha512: $sha512
releaseDate: '$(date -u +%Y-%m-%dT%H:%M:%S.000Z)'
EOF
}

# write_legacy_feed <version> <dmg-url> - the §3 one-time 0.6.0 bridge: rewrite the built
# arch's legacy Squirrel.Mac JSON feed so installed 0.6.0 copies (whose updater downloads
# the DMG and rebuilds Squirrel's zip from it on-device) can still reach 0.6.1. Runs only
# when the released version IS the bridge version; every later release leaves feed.json
# frozen - rewriting it past 0.6.1 would hand 0.6.0 installs a release whose DMG their
# flow can still consume, but the frozen two-hop bridge is the §21.4-logged decision and
# the §15 drift guards enforce it.
write_legacy_feed() {
  local version="$1" dmg_url="$2"
  [ "$version" = "$LEGACY_BRIDGE_VERSION" ] || return 0
  echo "· writing the §3 legacy 0.6.0 bridge feed (feed.json → v$version DMG)"
  mkdir -p "$(dirname "$LEGACY_FEED")"
  cat > "$LEGACY_FEED" <<EOF
{
  "currentRelease": "$version",
  "releases": [
    {
      "version": "$version",
      "updateTo": {
        "version": "$version",
        "name": "v$version",
        "url": "$dmg_url"
      }
    }
  ]
}
EOF
}

# write_downloads <key> <version> <url> - the §17 docs/downloads.json website
# download index: update only this OS/arch's entry, leaving the other OS legs'
# entries alone. python3 keeps the merge and formatting identical across all three
# release scripts, so the legs never churn each other's output.
write_downloads() {
  python3 - "$DOWNLOADS" "$1" "$2" "$3" <<'PY'
import json, sys
path, key, version, url = sys.argv[1:]
try:
    with open(path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}
data[key] = {"url": url, "version": version}
with open(path, "w", newline="\n") as f:
    json.dump(data, f, indent=2, sort_keys=True)
    f.write("\n")
PY
}

# push_feed <version> - commit + push just the feed and the download index (plain
# git commit, not commit.sh). Idempotent, and split the way publish_cask splits:
# an already-current pair commits nothing, but the push still runs whenever main
# is ahead of origin/main, so an earlier failed push can never leave the served
# feed lagging the checkout. The commit names its paths explicitly, so a --feed
# recovery run never sweeps up unrelated work.
push_feed() {
  local version="$1"
  # The legacy bridge feed is part of the commit only where it exists on disk
  # (it changes on the 0.6.1 release; a fresh x86_64 leg may never have one) -
  # a pathspec naming a file git has never seen would fail the commit.
  local paths=("$FEED" "$DOWNLOADS")
  [ -f "$LEGACY_FEED" ] && paths+=("$LEGACY_FEED")
  if [ -n "$(git -C "$ROOT" status --porcelain -- "${paths[@]}")" ]; then
    echo "· publishing update feed (release/darwin-$ARCH/latest-mac.yml + docs/downloads.json)"
    git -C "$ROOT" add "${paths[@]}"
    git -C "$ROOT" commit -q -m "Publish v$version update feed (darwin-$ARCH)" \
      -- "${paths[@]}"
  else
    echo "· update feed already at $version - no feed commit"
  fi
  git -C "$ROOT" fetch -q origin main \
    || { echo "cannot reach origin - the feed commit is local only; re-run: $(basename "$0") --feed"; exit 1; }
  if [ -z "$(git -C "$ROOT" log --oneline origin/main..HEAD)" ]; then
    echo "· update feed already on origin/main - nothing to push"
    return 0
  fi
  git -C "$ROOT" push -q origin HEAD \
    || { echo "failed to push the update feed - re-run: $(basename "$0") --feed"; exit 1; }
  echo "· update feed pushed (v$version, darwin-$ARCH)"
}

# changelog_section <version> <out-file> - extract the curated §17 docs/CHANGELOG.md
# section for the version (the lines under its "## v<version>" heading, up to the next
# "## " heading), leading and trailing blank lines trimmed. The release body is always
# this text, never GitHub's commit-derived auto-notes, so the release page, the §9.4
# What's-new modal, and the file on GitHub say the same thing.
changelog_section() {
  awk -v v="$1" '
    /^## /           { if (on) exit; on = ($2 == ("v" v)); next }
    !on              { next }
    /^[[:space:]]*$/ { if (started) pending++; next }
                     { for (; pending > 0; pending--) print ""; started = 1; print }
  ' "$CHANGELOG" > "$2"
}

[ $# -le 1 ] || usage
MODE="${1:-}"
case "$MODE" in
  ""|--sync|--check|--cask|--feed) ;;
  *)
    if printf '%s' "$MODE" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'; then
      echo "$(basename "$0") no longer takes a version: pick it with ./scripts/release-start.sh $MODE,"
      echo "commit the result, then run: $(basename "$0")"
      exit 2
    fi
    usage
    ;;
esac

[ -f "$VERSION_FILE" ] || { echo "missing $VERSION_FILE"; exit 1; }
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
[ -n "$VERSION" ] || { echo "empty $VERSION_FILE"; exit 1; }

# ---- release preflight: everything checked before anything is touched -------
# The preparation (release-start.sh) must be committed: the release is cut from a
# commit, and the feed commit lands on top of it. Nothing here modifies the tree.
if [ -z "$MODE" ]; then
  printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' \
    || { echo "invalid VERSION: '$VERSION' (expected MAJOR.MINOR.PATCH[-prerelease])"; exit 1; }
  require_gh
  [ -z "$(git -C "$ROOT" status --porcelain)" ] \
    || { echo "working tree dirty - commit the release preparation (./scripts/commit.sh) or stash before releasing"; exit 1; }
  require_main_branch
  # gh tags on GitHub only, so fetch before asking git what has been released.
  git -C "$ROOT" fetch -q --tags origin \
    || { echo "cannot fetch tags from origin"; exit 1; }
  if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$VERSION" > /dev/null \
     || git -C "$ROOT" ls-remote --exit-code --tags origin "refs/tags/v$VERSION" > /dev/null 2>&1; then
    echo "tag v$VERSION already exists - it was released; start the next one with ./scripts/release-start.sh <version>"
    exit 1
  fi
  PREVIOUS="$(git -C "$ROOT" describe --tags --abbrev=0 --match 'v*' HEAD 2>/dev/null || true)"
  if [ -n "$PREVIOUS" ] && ! semver_gt "$VERSION" "${PREVIOUS#v}"; then
    echo "VERSION $VERSION is not higher than the newest release ${PREVIOUS#v} - re-run ./scripts/release-start.sh with a higher version"
    exit 1
  fi
  # §17/§18 changelog gate: the §9.4 What's-new notes are written and committed before
  # the release is cut, never after, and never drafted here - release-start.sh did that.
  NOTES_FILE="$(mktemp)"
  trap 'rm -f "$NOTES_FILE"' EXIT
  grep -Eq "^## v${VERSION//./\\.}( |\$)" "$CHANGELOG" 2> /dev/null \
    || { echo "docs/CHANGELOG.md has no '## v$VERSION' section - draft it with: ./scripts/release-start.sh $VERSION"; exit 1; }
  changelog_section "$VERSION" "$NOTES_FILE"
  [ -s "$NOTES_FILE" ] \
    || { echo "docs/CHANGELOG.md '## v$VERSION' section is empty - write the release notes, commit, then re-run"; exit 1; }
fi

# ---- --cask: republish the §3 cask alone, against an existing release ----
# Recovery for a cask step that failed after the GitHub release went out. Touches no
# version site and no autowright commit - only the tap. The DMG comes from the local
# build when it survived, otherwise straight back down from the release it pins.
if [ "$MODE" = "--cask" ]; then
  require_gh
  gh release view "v$VERSION" > /dev/null 2>&1 \
    || { echo "no GitHub release v$VERSION - cut it with: $(basename "$0")"; exit 1; }
  tap_preflight
  DMG_NAME="Autowright-$VERSION-darwin-$ARCH.dmg"
  CASK_TMP="$(mktemp -d)"
  trap 'rm -rf "$CASK_TMP"' EXIT
  echo "· downloading $DMG_NAME from release v$VERSION"
  gh release download "v$VERSION" -p "$DMG_NAME" -D "$CASK_TMP" \
    || { echo "release v$VERSION has no asset $DMG_NAME"; exit 1; }
  publish_cask "$VERSION" "$CASK_TMP/$DMG_NAME"
  exit 0
fi

# ---- --feed: republish the §3 update feed alone, against an existing release ----
# Recovery for a feed rewrite/commit/push that failed after the GitHub release went
# out (a re-run can't: the tag already exists). Touches no version site and builds
# nothing. The release's asset list is read to prove the URLs the feed is about to
# name are live; the zip itself is reused from build/ when it survived (the yml needs
# its sha512/size), otherwise downloaded back from the release to hash.
if [ "$MODE" = "--feed" ]; then
  require_gh
  require_main_branch
  gh release view "v$VERSION" > /dev/null 2>&1 \
    || { echo "no GitHub release v$VERSION - cut it with: $(basename "$0")"; exit 1; }
  FEED_ASSETS="$(gh release view "v$VERSION" --json assets -q '.assets[].name')"
  for name in "Autowright-$VERSION-darwin-$ARCH.dmg" "Autowright-$VERSION-darwin-$ARCH.zip"; do
    printf '%s\n' "$FEED_ASSETS" | grep -qx "$name" \
      || { echo "release v$VERSION has no asset $name - the feed would name a dead URL"; exit 1; }
  done
  FEED_ZIP="$ROOT/build/Autowright-$VERSION-darwin-$ARCH.zip"
  if [ ! -f "$FEED_ZIP" ]; then
    FEED_TMP="$(mktemp -d)"
    trap 'rm -rf "$FEED_TMP"' EXIT
    echo "· downloading Autowright-$VERSION-darwin-$ARCH.zip from release v$VERSION (for its sha512)"
    gh release download "v$VERSION" -p "Autowright-$VERSION-darwin-$ARCH.zip" -D "$FEED_TMP" \
      || { echo "failed to download the released zip"; exit 1; }
    FEED_ZIP="$FEED_TMP/Autowright-$VERSION-darwin-$ARCH.zip"
  fi
  write_feed "$VERSION" "$(release_asset_url "$VERSION" .zip)" "$FEED_ZIP"
  write_legacy_feed "$VERSION" "$(release_asset_url "$VERSION" .dmg)"
  write_downloads "darwin-$ARCH" "$VERSION" "$(release_asset_url "$VERSION" .dmg)"
  push_feed "$VERSION"
  exit 0
fi

# read_version <file> - print the version currently in a site
read_version() {
  case "$1" in
    "$PKG_JSON")  sed -nE 's/^  "version": "([^"]+)",$/\1/p' "$1" ;;
    "$PYPROJECT") sed -nE 's/^version = "([^"]+)"$/\1/p' "$1" ;;
    "$INIT_PY")   sed -nE 's/^__version__ = "([^"]+)"$/\1/p' "$1" ;;
  esac
}

# write_version <file> - rewrite a site's version line to $VERSION
write_version() {
  case "$1" in
    "$PKG_JSON")  "${SED_I[@]}" "s/^(  \"version\": \")[^\"]+(\",)$/\1$VERSION\2/" "$1" ;;
    "$PYPROJECT") "${SED_I[@]}" "s/^(version = \")[^\"]+(\")$/\1$VERSION\2/" "$1" ;;
    "$INIT_PY")   "${SED_I[@]}" "s/^(__version__ = \")[^\"]+(\")$/\1$VERSION\2/" "$1" ;;
  esac
}

# --sync rewrites; --check and the release only verify (the release's tree was just
# proven clean, so a mismatch means the release-start.sh sync was never committed,
# and rewriting here would dirty the commit the release is about to tag).
MISMATCH=0
for f in "$PKG_JSON" "$PYPROJECT" "$INIT_PY"; do
  current="$(read_version "$f")"
  if [ -z "$current" ]; then
    echo "no version line found in ${f#"$ROOT"/}"
    exit 1
  fi
  if [ "$current" = "$VERSION" ]; then
    continue
  fi
  if [ "$MODE" = "--sync" ]; then
    write_version "$f"
    echo "· ${f#"$ROOT"/}: $current → $VERSION"
  else
    echo "version mismatch: ${f#"$ROOT"/} has $current, VERSION has $VERSION"
    MISMATCH=1
  fi
done

if [ "$MODE" = "--sync" ]; then
  echo "· version: $VERSION"
  exit 0
fi
if [ "$MISMATCH" -ne 0 ]; then
  if [ "$MODE" = "--check" ]; then
    echo "run ./scripts/release.sh --sync to fix"
  else
    echo "run ./scripts/release.sh --sync, commit the result, then re-run $(basename "$0")"
  fi
  exit 1
fi
if [ "$MODE" = "--check" ]; then
  echo "· version OK: $VERSION"
  exit 0
fi

# ---- cut the release ---------------------------------------------------------
# The tap is checked last among the prerequisites, still before anything is built.
tap_preflight

# ---- full test suite, before anything is pushed or built ----
# Shift-left order (§15): cheap gate, then integration, then E2E.
echo "· running tests"
"$ROOT/scripts/build.sh" --deps
"$ROOT/scripts/tests/fast.sh"
"$ROOT/.venv/bin/python" -m pytest -m integration
(cd "$ROOT/app" && npm run test:e2e)

# ---- push the release commit (the tree is clean, so nothing to commit) ----
# gh release create tags the pushed commit, so HEAD must be on origin first.
echo "· pushing"
git -C "$ROOT" push -q origin HEAD

# ---- build the distributable ----
echo "· version: $VERSION - building release"
"$ROOT/scripts/prod.sh"

# ---- publish the GitHub release (tags the pushed commit, uploads DMG + zip) ----
DMG="$ROOT/build/Autowright-$VERSION-darwin-$ARCH.dmg"
ZIP="$ROOT/build/Autowright-$VERSION-darwin-$ARCH.zip"
[ -f "$DMG" ] || { echo "DMG missing after build: $DMG"; exit 1; }
[ -f "$ZIP" ] || { echo "update zip missing after build: $ZIP"; exit 1; }
echo "· creating GitHub release v$VERSION (notes from docs/CHANGELOG.md)"
gh release create "v$VERSION" "$DMG" "$ZIP" \
  --target "$(git -C "$ROOT" rev-parse HEAD)" \
  --title "v$VERSION" --notes-file "$NOTES_FILE"

# ---- update feed (SPEC §3): latest-mac.yml for this arch, raw from GitHub ----
# Written only after the release exists, so the feed never points at a URL
# that isn't live yet. Same functions the --feed recovery mode runs. The
# legacy write is the §3 one-time 0.6.0 bridge - a no-op for every version
# but 0.6.1.
write_feed "$VERSION" "$(release_asset_url "$VERSION" .zip)" "$ZIP"
write_legacy_feed "$VERSION" "$(release_asset_url "$VERSION" .dmg)"
write_downloads "darwin-$ARCH" "$VERSION" "$(release_asset_url "$VERSION" .dmg)"
push_feed "$VERSION"

# ---- Homebrew cask (SPEC §3): bump the tap, last, once the release is live ----
publish_cask "$VERSION" "$DMG"
echo "· release v$VERSION published (darwin-$ARCH)"
echo "  next: windows-scripts/release.ps1 and linux-scripts/release.sh append their artifacts"
