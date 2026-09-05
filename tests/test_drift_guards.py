"""§15 drift guards: cheap textual checks over facts that live in more than one
hand-maintained file, where nothing else would catch the two copies diverging.

Three guards, all deliberately dumb (read the file, pull the fact out, compare):

1. the app version: `VERSION` is the single source (§17), synced into three
   other files by `release.sh`; a hand-edit to any one of them must fail here.
2. the §6.2 curated package list: four homes, two of which name *import*
   modules and two of which name *distributions*, so the mapping between them
   is written out below instead of guessed.
3. the §3 per-OS update feeds under `release/` plus the §17
   `docs/downloads.json` index: hand-maintained-looking JSON/YAML that nothing
   else reads at test time, written by three separate release legs, and whose
   contents are only ever exercised by a shipped app fetching them over the
   network. A feed naming an artifact the app cannot consume (a `.dmg` where
   electron-updater feeds Squirrel a `.zip`), or a version that ran ahead of
   the release that exists, is invisible until an installed copy tries to
   update. The frozen §3 legacy bridge feeds get their own guard: they are
   load-bearing for stranded 0.6.0 installs, so a rewrite past 0.6.1 is a bug.
"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# §3/§17: binaries always ride the GitHub release of the repo the feeds live in;
# only the feed files themselves are served from raw.githubusercontent.com.
RELEASE_DOWNLOAD_PREFIX = "https://github.com/hansololz/autowright/releases/download/"

# §6.2 curated packages: import name → pip distribution name. Equal where the
# two agree; spelled out where they don't (this mapping is the whole reason the
# four homes can look different and still be in sync).
CURATED = {
    "requests": "requests",
    "httpx": "httpx",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "feedparser": "feedparser",
    "dateutil": "python-dateutil",
    "yaml": "PyYAML",
}


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _backticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


# ---------------------------------------------------------------- version

def test_version_agrees_across_every_site():
    """§17: `VERSION` is the single source; `release.sh --sync` writes the other
    three. A mismatch means someone hand-edited one of them."""
    version = _read("VERSION").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].+)?", version), \
        f"VERSION is not semver: {version!r}"

    pyproject = tomllib.loads(_read("backend/pyproject.toml"))["project"]["version"]
    init = re.search(r'^__version__\s*=\s*"([^"]+)"',
                     _read("backend/autowright/__init__.py"), re.M)
    package_json = json.loads(_read("app/package.json"))["version"]

    assert init, "backend/autowright/__init__.py has no __version__ line"
    mismatched = {site: found for site, found in [
        ("backend/pyproject.toml", pyproject),
        ("backend/autowright/__init__.py", init.group(1)),
        ("app/package.json", package_json),
    ] if found != version}
    assert not mismatched, (
        f"version drift: VERSION says {version!r} but: {mismatched}. "
        "Re-sync with `./scripts/release.sh --sync`.")


# ---------------------------------------------------------------- §6.2 curated list

def test_curated_imports_match_the_allowlist_in_code():
    """Home 1, `imports_check.ALLOWED_IMPORTS`: the enforcement itself
    (draft-time validation + the runtime executor)."""
    from autowright.imports_check import ALLOWED_IMPORTS

    curated = ALLOWED_IMPORTS - set(sys.stdlib_module_names) - {"autowright"}
    assert curated == set(CURATED), (
        "imports_check.ALLOWED_IMPORTS disagrees with the §6.2 curated list: "
        f"only in code {sorted(curated - set(CURATED))}, "
        f"only in the list {sorted(set(CURATED) - curated)}")


def test_curated_packages_are_declared_dependencies():
    """Home 2, `backend/pyproject.toml`. A curated import must be a real
    runtime dependency, or the bundled interpreter ships without it and every
    step that imports it fails at execution time. (One direction only: the
    backend legitimately depends on packages that are not curated.)"""
    deps = tomllib.loads(_read("backend/pyproject.toml"))["project"]["dependencies"]
    declared = {re.split(r"[<>=!~\[ ]", d, maxsplit=1)[0].lower() for d in deps}
    missing = sorted(dist for dist in CURATED.values() if dist.lower() not in declared)
    assert not missing, (
        f"§6.2 curated packages missing from backend/pyproject.toml dependencies: "
        f"{missing}; the distributable would not ship them.")


def test_curated_list_matches_the_framework_instructions():
    """Home 3, `instructions/framework-instructions.md`: the §8 contract
    preamble the authoring agent reads. It names *import* modules."""
    text = _read("backend/autowright/instructions/framework-instructions.md")
    m = re.search(r"## Allowed imports\s+Python stdlib,(.*?)— always available",
                  text, re.S)
    assert m, "framework-instructions.md has no 'Allowed imports' sentence to check"
    listed = set(_backticked(m.group(1))) - {"autowright"}
    assert listed == set(CURATED), (
        "framework-instructions.md's allowed-imports sentence disagrees with the "
        f"§6.2 curated list: only in the file {sorted(listed - set(CURATED))}, "
        f"only in the list {sorted(set(CURATED) - listed)}")


def test_curated_list_matches_the_spec():
    """Home 4, §6.2 in `spec/engine.md`: the source of truth. It names
    *distributions* with the import module in parentheses where they differ, so
    both spellings must appear."""
    m = re.search(r"and the curated packages:(.*?)\.\n", _read("spec/engine.md"), re.S)
    assert m, "spec/engine.md §6.2 has no 'curated packages:' sentence to check"
    listed = set(_backticked(m.group(1)))
    expected = set(CURATED) | set(CURATED.values())
    assert listed == expected, (
        "spec/engine.md §6.2 disagrees with the curated list (it must name each "
        "distribution, plus the import module where they differ): "
        f"only in the spec {sorted(listed - expected)}, "
        f"only in the list {sorted(expected - listed)}")


# ---------------------------------------------------------------- §3 update feeds

# key in docs/downloads.json → (feed file under release/, extension of the artifact
# that OS's updater consumes). The extension is the load-bearing half: macOS feeds
# Squirrel.Mac the `.zip` electron-updater downloads, electron-updater runs the
# NSIS `.exe`, and the AppImage flow swaps the `.AppImage` file in place. A feed
# pointing at anything else fails at update time on a user's machine, never here.
FEEDS = {
    "darwin-arm64": ("release/darwin-arm64/latest-mac.yml", ".zip"),
    "darwin-x86_64": ("release/darwin-x86_64/latest-mac.yml", ".zip"),
    "win32-x86_64": ("release/win32-x86_64/latest.yml", ".exe"),
    "linux-x86_64": ("release/linux-x86_64/latest-linux.yml", ".AppImage"),
}

# §3 legacy 0.6.0 bridge: the Squirrel.Mac JSON feeds pre-0.6.1 mac installs read.
# Rewritten exactly once - by the 0.6.1 release, pointing at the 0.6.1 DMG (that
# updater mounts the DMG and builds Squirrel's zip on-device) - then frozen
# forever: a stranded 0.6.0 copy hops 0.6.0 → 0.6.1 through them and rides
# electron-updater from there (§21.4). The bridge version is the ceiling, never a
# floor: before the 0.6.1 release the files still sit at 0.6.0.
LEGACY_FEEDS = {
    "darwin-arm64": "release/darwin-arm64/feed.json",
    "darwin-x86_64": "release/darwin-x86_64/feed.json",
}
LEGACY_BRIDGE_CEILING = "0.6.1"


def _semver_tuple(version: str) -> tuple[int, ...]:
    """Numeric core only - enough to order this repo's release tags."""
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    return tuple(int(part) for part in core.split("."))


def _feed_facts(rel: str) -> tuple[str, list[str]]:
    """(version, [download urls]) for a feed on disk, whichever OS wrote it."""
    text = _read(rel)
    if rel.endswith(".json"):  # Squirrel.Mac
        data = json.loads(text)
        urls = [entry["updateTo"]["url"] for entry in data["releases"]]
        versions = {data["currentRelease"], *(e["version"] for e in data["releases"])}
        assert len(versions) == 1, (
            f"{rel} disagrees with itself: currentRelease and the releases[] "
            f"versions are {sorted(versions)}")
        return data["currentRelease"], urls
    data = yaml.safe_load(text)  # electron-updater
    urls = [data["path"]] + [f["url"] for f in data.get("files", [])]
    return str(data["version"]), urls


def _present_feeds() -> dict[str, tuple[str, str, list[str]]]:
    """key → (rel path, version, urls) for every feed that exists on disk. Feeds
    appear one OS at a time (§3: each leg writes only its own), so absence is
    never a failure - the Linux feed has no file until a Linux release is cut."""
    found = {}
    for key, (rel, _ext) in FEEDS.items():
        if (REPO / rel).exists():
            version, urls = _feed_facts(rel)
            found[key] = (rel, version, urls)
    return found


def test_update_feeds_name_a_consumable_artifact():
    """§3: every URL a feed hands the updater must be a GitHub release download
    for this repo, embed the feed's own version, and carry the extension that
    OS's update flow can actually open."""
    feeds = _present_feeds()
    assert feeds, "no §3 update feed found under release/ - did the feeds move?"
    for key, (rel, version, urls) in feeds.items():
        _rel, ext = FEEDS[key]
        assert urls, f"{rel} names no download URL"
        for url in urls:
            assert url.startswith(RELEASE_DOWNLOAD_PREFIX), (
                f"{rel} points at {url!r}; §3 binaries ride the GitHub release "
                f"({RELEASE_DOWNLOAD_PREFIX}…), only the feed itself is raw-served")
            assert url.endswith(ext), (
                f"{rel} points at {url!r}, but the {key} update flow consumes a "
                f"{ext} - a feed naming any other artifact fails at update time "
                "on an installed copy, not here")
            assert f"/v{version}/" in url and version in url.rsplit("/", 1)[-1], (
                f"{rel} says version {version!r} but its URL {url!r} names a "
                "different release")


def test_update_feeds_never_run_ahead_of_the_version_file():
    """§3/§17: a feed is rewritten only *after* its release is published, so a
    feed newer than `VERSION` names a release that does not exist yet. The
    reverse is legitimate and deliberately not flagged: each OS leg rewrites
    only its own feed, so a release cut without the Windows or Linux leg leaves
    that feed at the newest version which actually carries that OS's artifact."""
    version = _read("VERSION").strip()
    for key, (rel, feed_version, _urls) in _present_feeds().items():
        assert _semver_tuple(feed_version) <= _semver_tuple(version), (
            f"{rel} is at {feed_version}, ahead of VERSION ({version}) - it names "
            "a release that has not been published")


def test_legacy_bridge_feeds_stay_frozen():
    """§3/§21.4: the legacy Squirrel feeds are the only update path stranded
    0.6.0 installs have. Whichever exist must stay internally consistent, name
    a live-shaped `.dmg` release URL for their own version, and never move past
    the 0.6.1 bridge - a later rewrite would not break 0.6.0 copies (their flow
    can consume any DMG), but the frozen two-hop bridge is the logged decision,
    so a rewrite is a bug, not a refresh."""
    for key, rel in LEGACY_FEEDS.items():
        if not (REPO / rel).exists():
            continue  # an arch's legacy feed exists only if 0.6.0 shipped for it
        version, urls = _feed_facts(rel)
        assert _semver_tuple(version) <= _semver_tuple(LEGACY_BRIDGE_CEILING), (
            f"{rel} is at {version}, past the frozen §3 bridge version "
            f"({LEGACY_BRIDGE_CEILING}) - the legacy feed must never be "
            "rewritten after the 0.6.1 release")
        assert urls, f"{rel} names no download URL"
        for url in urls:
            assert url.startswith(RELEASE_DOWNLOAD_PREFIX), (
                f"{rel} points at {url!r}; §3 binaries ride the GitHub release")
            assert url.endswith(".dmg"), (
                f"{rel} points at {url!r}, but the pre-0.6.1 update flow "
                "consumes a .dmg")
            assert f"/v{version}/" in url and version in url.rsplit("/", 1)[-1], (
                f"{rel} says version {version!r} but its URL {url!r} names a "
                "different release")


def _git(*args: str) -> str | None:
    """Stdout of a git command against the repo, or None outside a git checkout
    (or when the command fails - a missing tag, no tags at all)."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _committed_version() -> str:
    """The last *committed* `VERSION`. The working-tree file is never the one
    compared: `release-start.sh` writes the bump for the developer to commit,
    and `release.sh` then runs this suite before the GitHub release exists
    (the feed is written only once it is live), so between the bump and the
    release it legitimately runs ahead of every feed. Falls back to the file
    outside a git checkout."""
    committed = _git("show", "HEAD:VERSION")
    return committed if committed is not None else _read("VERSION").strip()


def _published_versions() -> set[str]:
    """§18: the versions the newest published release can be at - what the mac
    feed must match. The committed `VERSION` counts as published once its
    `v<version>` tag exists in this checkout, and is then the only answer. While
    that tag is absent the release has not been cut yet (`release-start.sh`
    commits the bump ahead of the release, so `VERSION` is written while the
    feed still sits at the previous release), so the previous release - the
    newest `v*` tag reachable from HEAD - is accepted too."""
    version = _committed_version()
    if _git("rev-parse", "-q", "--verify", f"refs/tags/v{version}") is not None:
        return {version}
    previous = _git("describe", "--tags", "--abbrev=0", "--match", "v*", "HEAD")
    return {version, previous[1:]} if previous else {version}


def test_a_macos_feed_tracks_the_version_file():
    """§18: releases are cut from macOS by `release.sh`, which cuts the release
    `VERSION` names *and* rewrites the built arch's update feed in the same run. So the mac
    feed for the arch the release was built on always equals the newest
    published release; a mismatch means the feed write or its push was lost
    (recover with `release.sh --feed`). Any arch satisfies it - the guard does
    not care which machine cut the release, only that the mac half is not
    stale. Until the 0.6.1 release first writes `latest-mac.yml`, the legacy
    bridge feeds are the mac feeds, so they count as candidates too."""
    published = _published_versions()
    darwin = {key: facts for key, facts in _present_feeds().items()
              if key.startswith("darwin-")}
    for key, rel in LEGACY_FEEDS.items():
        if (REPO / rel).exists():
            legacy_version, legacy_urls = _feed_facts(rel)
            darwin[f"{key}-legacy"] = (rel, legacy_version, legacy_urls)
    assert darwin, "no darwin feed under release/ - the mac update feed is gone"
    assert any(feed_version in published for _rel, feed_version, _urls in darwin.values()), (
        f"no darwin feed is at the newest published release ({' or '.join(sorted(published))}); "
        f"found { {rel: v for rel, v, _ in darwin.values()} }. Re-publish with "
        "`./scripts/release.sh --feed`.")


def test_downloads_index_agrees_with_the_feeds():
    """§17 `docs/downloads.json` is the website's download index - the same
    release URLs the feeds name, rewritten by the same release legs. It is the
    only copy of those URLs a human ever reads, so nothing else would catch it
    drifting away from the feed beside it."""
    downloads = json.loads(_read("docs/downloads.json"))
    assert downloads, "docs/downloads.json is empty"
    feeds = _present_feeds()
    for key, entry in downloads.items():
        url, entry_version = entry["url"], entry["version"]
        assert url.startswith(RELEASE_DOWNLOAD_PREFIX), (
            f"docs/downloads.json[{key}] points at {url!r}; downloads come from "
            "the GitHub release")
        assert f"/v{entry_version}/" in url and entry_version in url.rsplit("/", 1)[-1], (
            f"docs/downloads.json[{key}] says version {entry_version!r} but its "
            f"URL {url!r} names a different release")
        if key in feeds:
            rel, feed_version, feed_urls = feeds[key]
            assert entry_version == feed_version, (
                f"docs/downloads.json[{key}] is at {entry_version} but {rel} is "
                f"at {feed_version} - the same release leg writes both")
            if key.startswith("darwin-"):
                # §3: install (DMG) and update (zip) artifacts differ on mac -
                # same release, same basename, only the extension apart.
                dmg_beside_zip = {u[: -len(".zip")] + ".dmg"
                                  for u in feed_urls if u.endswith(".zip")}
                assert url in dmg_beside_zip, (
                    f"docs/downloads.json[{key}] offers {url!r}, which is not "
                    f"the DMG beside any zip {rel} names - the site's install "
                    "artifact and the updater's zip must come from one release")
            else:
                assert url in feed_urls, (
                    f"docs/downloads.json[{key}] offers {url!r}, which {rel} does not "
                    "name - the site and the updater must hand out the same artifact")


# ---------------------------------------------------------------- §17 changelog

# §17: one `## v<version> - <YYYY-MM-DD>` section per released version, newest
# first. The version half is semver with an optional pre-release suffix; the
# date half is what a reader (and the §9.4 modal) sees beside it.
CHANGELOG_HEADING = re.compile(
    r"^## v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?) - \d{4}-\d{2}-\d{2}$")


def _changelog_versions() -> list[str]:
    """The version of every `## ` heading in `docs/CHANGELOG.md`, in file
    order, with each heading checked for shape on the way through."""
    versions = []
    for line in _read("docs/CHANGELOG.md").splitlines():
        if not line.startswith("## "):
            continue
        m = CHANGELOG_HEADING.match(line)
        assert m, (
            f"docs/CHANGELOG.md heading {line!r} is not a §17 section heading "
            "(`## v<version> - <YYYY-MM-DD>`)")
        versions.append(m.group(1))
    return versions


def _changelog_order_key(version: str) -> tuple[tuple[int, ...], int, str]:
    """Newest-first ordering key: the numeric core field by field, then a
    release ahead of any pre-release on an equal core, then pre-releases
    against each other lexically."""
    core, _, pre = version.partition("-")
    return tuple(int(part) for part in core.split(".")), 0 if pre else 1, pre


def test_changelog_headings_are_section_headings():
    """§17: every `## ` line in `docs/CHANGELOG.md` is a version section — the
    file has no other second-level headings. A heading in any other shape is
    invisible to `release.sh`'s §18 preflight and to the guards below."""
    versions = _changelog_versions()
    assert versions, (
        "docs/CHANGELOG.md has no `## v<version>` section — did it move?")


def test_changelog_has_an_entry_for_the_current_version():
    """§17/§18: `release.sh` refuses to cut a version with no entry, so the
    entry must exist by the time `VERSION` names it (`release-start.sh` drafts
    both together). Deliberately "an entry exists", not "the top entry
    matches": notes for the *next* version are written and committed ahead of
    the release, so a newer entry sitting above the current one is legitimate."""
    version = _read("VERSION").strip()
    versions = _changelog_versions()
    assert version in versions, (
        f"docs/CHANGELOG.md has no `## v{version}` section, but VERSION says "
        f"{version!r}; found {versions}. Write the release notes before "
        "cutting the release.")


def test_changelog_versions_descend_without_duplicates():
    """§17: newest first, so the §9.4 What's-new modal opens on the notes for
    the version the user just got. Two sections for one version, or a section
    filed under an older one, would put the wrong notes at the top."""
    versions = _changelog_versions()
    for newer, older in zip(versions, versions[1:]):
        assert _changelog_order_key(newer) > _changelog_order_key(older), (
            f"docs/CHANGELOG.md lists v{newer} above v{older}; the sections "
            "must be in strictly descending version order, newest first")


def test_powershell_scripts_start_with_a_utf8_bom():
    """§17/§18 `scripts/*.ps1` + `windows-scripts/*.ps1`: Windows PowerShell
    5.1 reads a BOM-less file as ANSI, and the scripts carry non-ASCII
    characters in their result lines (`·`, `—`). Without the BOM those bytes
    decode into stray quote characters and the script fails to parse — a hard
    failure at build time, from an invisible property of the file. Guarded
    because any editor (or tooling) that rewrites the file as plain UTF-8
    removes it silently."""
    scripts = sorted((REPO / "scripts").glob("*.ps1"))
    scripts += sorted((REPO / "windows-scripts").glob("*.ps1"))
    assert scripts, "no *.ps1 found — did the §17 PowerShell scripts move?"
    for path in scripts:
        head = path.read_bytes()[:3]
        assert head == b"\xef\xbb\xbf", (
            f"{path.relative_to(REPO)} lost its UTF-8 BOM — Windows PowerShell "
            "5.1 would misread its non-ASCII output lines and fail to parse it")
