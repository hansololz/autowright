#!/usr/bin/env bash
# Prepare the repo for a release (SPEC §17/§18) - step one of three. A release is:
#
#   1. ./scripts/release-start.sh <version>   this script: bump the version, draft the
#                                             release notes; nothing is committed
#   2. curate docs/CHANGELOG.md, then commit (./scripts/commit.sh)
#   3. ./scripts/release.sh                   cut the release the committed VERSION names
#      then windows-scripts/release.ps1 and linux-scripts/release.sh append their
#      artifacts to that release, on their machines, in either order
#
#   ./scripts/release-start.sh <version>      e.g. ./scripts/release-start.sh 0.9.1
#
# What it does, in order:
#   - validates <version> (MAJOR.MINOR.PATCH[-prerelease]; a leading v is stripped) and
#     requires it to order semver-higher than the current VERSION. The current VERSION
#     must itself be released: its v<VERSION> tag must exist after a tag fetch (gh tags
#     on GitHub only, so the checkout learns of release tags through the fetch). A
#     version whose release never went out is re-cut with release.sh, never skipped.
#     Refuses when the tag v<version> already exists.
#   - drafts the "## v<version> - <today>" docs/CHANGELOG.md section from every change
#     since the released tag (commit subjects and bodies, the range diffstat, and the
#     uncommitted work in the tree - notes are drafted while the release's changes
#     often still sit uncommitted, so a dirty tree is fine here) via Claude (Opus 5),
#     inserted above the previous newest section. A section that already exists for
#     <version> is kept as it is, so a re-run never overwrites curated notes.
#   - only then writes VERSION and syncs the three version sites (release.sh --sync):
#     the draft is the failure-prone step, so a failed run leaves the version alone.
#
# Developer-only: agents must never run this script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$ROOT/VERSION"
CHANGELOG="$ROOT/docs/CHANGELOG.md"

usage() {
  echo "usage: $(basename "$0") <version>"
  exit 2
}

# semver_gt <a> <b> - is a strictly higher than b? The numeric base compares first; a
# release beats its own prereleases; prereleases compare lexically. release.sh applies
# the same rule again before cutting.
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

[ $# -eq 1 ] || usage
NEW="${1#v}"
printf '%s' "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$' \
  || { echo "invalid version: '$1' (expected MAJOR.MINOR.PATCH[-prerelease])"; exit 2; }

# ---- prerequisites ----------------------------------------------------------
command -v claude > /dev/null \
  || { echo "claude CLI not found - the release notes are drafted by Claude"; exit 1; }
[ -f "$VERSION_FILE" ] || { echo "missing $VERSION_FILE"; exit 1; }
[ -f "$CHANGELOG" ] || { echo "missing $CHANGELOG"; exit 1; }
CURRENT="$(tr -d '[:space:]' < "$VERSION_FILE")"
[ -n "$CURRENT" ] || { echo "empty $VERSION_FILE"; exit 1; }

# ---- the new version must be higher than the released one -------------------
semver_gt "$NEW" "$CURRENT" \
  || { echo "version $NEW is not higher than the current version $CURRENT"; exit 1; }

# gh release create tags on GitHub only, so the local checkout learns about release
# tags through a fetch; fetch first, then the tag checks below see what origin sees.
git -C "$ROOT" fetch -q --tags origin \
  || { echo "cannot fetch tags from origin - the released version cannot be verified"; exit 1; }
git -C "$ROOT" rev-parse -q --verify "refs/tags/v$CURRENT" > /dev/null \
  || { echo "the current version $CURRENT has no tag v$CURRENT - it was never released; cut it with ./scripts/release.sh before starting $NEW"; exit 1; }
if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$NEW" > /dev/null; then
  echo "tag v$NEW already exists - pick a new version"
  exit 1
fi

# ---- release notes: draft the changelog section -----------------------------
DATE="$(date +%Y-%m-%d)"
if grep -Eq "^## v${NEW//./\\.}( |\$)" "$CHANGELOG"; then
  echo "· docs/CHANGELOG.md already has a '## v$NEW' section - keeping it"
else
  # Everything since the released tag, plus the uncommitted work. Captured whole
  # and truncated in the shell: a `| head -c` pipe would trip pipefail on a long log.
  LOG="$(git -C "$ROOT" log --no-merges --pretty='* %s%n%b' "v$CURRENT..HEAD")"
  LOG="${LOG:0:60000}"
  RANGE_STAT="$(git -C "$ROOT" diff --stat "v$CURRENT..HEAD")"
  RANGE_STAT="${RANGE_STAT:0:10000}"
  DIRTY="$(git -C "$ROOT" status --porcelain)"
  DIRTY="${DIRTY:0:4000}"
  DIRTY_STAT="$(git -C "$ROOT" diff --stat HEAD)"
  DIRTY_STAT="${DIRTY_STAT:0:10000}"
  [ -n "$LOG$DIRTY" ] || { echo "no changes since v$CURRENT - nothing to release"; exit 1; }

  # The two newest sections, as voice examples for the model.
  EXAMPLES="$(awk '/^## /{n++} n>2{exit} n>=1' "$CHANGELOG")"

  echo "· drafting the v$NEW release notes (claude, opus 5)"
  RAW="$(claude --model claude-opus-5 -p << EOF
Write the release-notes bullets for Autowright v$NEW from the changes below.

Output ONLY bullet lines, each starting with "- ". No heading, no code fences,
no blank lines, no commentary before or after. Write for end users of the app:
features, UI changes, and fixes in plain language, never a commit dump. Skip
purely internal changes (tests, refactors, spec or docs edits, build tooling)
unless they change what users see. Merge related changes into one bullet. Use
plain hyphens; never use an em dash. Match the voice and level of detail of
these recent sections:

$EXAMPLES

Commits since v$CURRENT (newest first; * marks each subject):

$LOG

Diffstat for the range:

$RANGE_STAT

Uncommitted changes in the working tree (also part of this release):

$DIRTY

$DIRTY_STAT
EOF
)"

  # Drop fence and blank lines, swap any em dash for a plain hyphen, trim trailing
  # whitespace; then every surviving line must be a "- " bullet.
  BULLETS="$(printf '%s\n' "$RAW" \
    | sed -e '/^```/d' -e '/^[[:space:]]*$/d' -e $'s/\xe2\x80\x94/-/g' -e 's/[[:space:]]*$//')"
  [ -n "$BULLETS" ] || { echo "claude returned no output"; exit 1; }
  if printf '%s\n' "$BULLETS" | grep -Eqv '^- '; then
    echo "unexpected model output (every line must be a '- ' bullet):"
    printf '%s\n' "$BULLETS"
    exit 1
  fi

  BULLETS="$BULLETS" VERSION="$NEW" DATE="$DATE" CHANGELOG="$CHANGELOG" python3 - << 'PY'
import os, pathlib
path = pathlib.Path(os.environ["CHANGELOG"])
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
section = "## v{VERSION} - {DATE}\n\n{BULLETS}\n\n".format(**os.environ)
path.write_text("".join(lines[:at]) + section + "".join(lines[at:]), encoding="utf-8")
PY

  echo "· added to docs/CHANGELOG.md:"
  echo
  echo "## v$NEW - $DATE"
  echo
  printf '%s\n' "$BULLETS"
  echo
fi

# ---- version bump: VERSION + the three sites --------------------------------
echo "· VERSION: $CURRENT → $NEW"
printf '%s\n' "$NEW" > "$VERSION_FILE"
"$ROOT/scripts/release.sh" --sync

echo
echo "release v$NEW prepared - nothing committed. Next:"
echo "  1. curate the '## v$NEW' section in docs/CHANGELOG.md"
echo "  2. commit the bump + notes:   ./scripts/commit.sh"
echo "  3. cut the release:           ./scripts/release.sh"
