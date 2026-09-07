# Build the Windows production distribution (SPEC §3): the per-user NSIS
# installer for Autowright, carrying the relocatable CPython
# (python-build-standalone) + the autowright backend and curated packages in
# resources\python\, plus the electron-updater feed files (latest.yml + the
# installer's blockmap) — all under build\win\.
#
# The order mirrors scripts/prod.sh (§3): version gate, renderer build,
# bundled interpreter (download → stage → pip install pinned by
# backend\constraints.txt → smoke test), then packaging. Unlike macOS, an
# unsigned build is allowed for now (§3 "cert later, pipeline ready now"): the
# build signs whenever certificate configuration is present and otherwise
# prints a loud UNSIGNED warning — never silently.
#
#   .\windows-scripts\prod.ps1
#
# Signing (any one of these, checked in this order):
#   $env:CSC_LINK + $env:CSC_KEY_PASSWORD        a .pfx/.p12 file and its password
#   $env:AUTOWRIGHT_WIN_CERT_SHA1                thumbprint of a certificate in the
#                                                Windows certificate store
# When the certificate arrives, also set `publisherName` in the updater config
# and flip §3 to always-signed — never before (§3 footgun: publisherName makes
# electron-updater verify Authenticode, and every unsigned artifact then fails).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is glacial with it on

$ROOT = Split-Path -Parent $PSScriptRoot

function Fail([string]$message) {
    Write-Host $message
    exit 1
}

# Run a native command and stop on a non-zero exit — PowerShell does not.
function Invoke-Native([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)" }
}

# ---- version gate: refuse to build a distributable on version mismatch ------
# The same rule prod.sh gets from `release.sh --check`. Rewriting the sites is
# release.sh's job (bash + BSD sed, run from macOS, §17) — this only verifies.
$VERSION = (Get-Content (Join-Path $ROOT 'release\VERSION') -Raw).Trim()
if (-not $VERSION) { Fail "empty $ROOT\release\VERSION" }

$sites = @(
    @{ Path = 'app\package.json';                Pattern = '^  "version": "([^"]+)",$' },
    @{ Path = 'backend\pyproject.toml';          Pattern = '^version = "([^"]+)"$' },
    @{ Path = 'backend\autowright\__init__.py';  Pattern = '^__version__ = "([^"]+)"$' }
)
$mismatch = $false
foreach ($site in $sites) {
    $full = Join-Path $ROOT $site.Path
    $found = $null
    foreach ($line in Get-Content $full) {
        if ($line -match $site.Pattern) { $found = $Matches[1]; break }
    }
    if (-not $found) { Fail "no version line found in $($site.Path)" }
    if ($found -ne $VERSION) {
        Write-Host "version mismatch: $($site.Path) has $found, VERSION has $VERSION"
        $mismatch = $true
    }
}
if ($mismatch) { Fail "run ./scripts/release.sh --sync (macOS) to rewrite the version sites" }
Write-Host "· version OK: $VERSION"

$BUILD = Join-Path $ROOT 'build'
$CACHE = Join-Path $BUILD 'cache'
$OUT = Join-Path $BUILD 'win'
New-Item -ItemType Directory -Force -Path $CACHE | Out-Null

# ---- renderer bundle (app\dist) --------------------------------------------
# build.sh's Windows half: dependencies from the lockfile, then the typechecked
# vite build. electron-builder packages app\dist, never the vite scaffolding.
$APP = Join-Path $ROOT 'app'
Push-Location $APP
try {
    Write-Host '· npm ci'
    Invoke-Native 'npm ci' { npm ci }
    Write-Host '· vite build (typechecked)'
    Invoke-Native 'npm run build' { npm run build }
} finally {
    Pop-Location
}

# ---- bundled relocatable CPython (python-build-standalone, pinned) ---------
# The same release tag and CPython version prod.sh pins, in the Windows flavor:
# the *-pc-windows-msvc-install_only build, whose layout is flat (python.exe at
# the root, no bin\ — §3, resolved by the §2 platform layer).
$PBS_TAG = '20260807'
$PY_FULL = '3.14.7'
$PBS_ARCH = 'x86_64-pc-windows-msvc'
$PBS_URL = if ($env:AUTOWRIGHT_PBS_URL) { $env:AUTOWRIGHT_PBS_URL } else {
    "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PY_FULL+$PBS_TAG-$PBS_ARCH-install_only.tar.gz"
}
$TARBALL = Join-Path $CACHE ([System.IO.Path]::GetFileName(($PBS_URL -split '\?')[0]))
if (-not (Test-Path $TARBALL)) {
    Write-Host "· downloading bundled Python ($PY_FULL, $PBS_ARCH)"
    Invoke-WebRequest -Uri $PBS_URL -OutFile "$TARBALL.tmp"
    Move-Item "$TARBALL.tmp" $TARBALL
}

$PYSTAGE = Join-Path $BUILD 'python'
Write-Host '· staging bundled Python → build\python'
if (Test-Path $PYSTAGE) { Remove-Item -Recurse -Force $PYSTAGE }
New-Item -ItemType Directory -Force -Path $PYSTAGE | Out-Null
# bsdtar ships with Windows 10+; the tarball root is python\.
Invoke-Native 'tar' { tar -xzf $TARBALL -C $PYSTAGE --strip-components 1 }

$STAGED_PY = Join-Path $PYSTAGE 'python.exe'
if (-not (Test-Path $STAGED_PY)) { Fail "staging failed: $STAGED_PY missing" }

Write-Host '· upgrading bundled pip'
Invoke-Native 'pip install --upgrade pip' { & $STAGED_PY -m pip -q install --upgrade pip }

# -c backend\constraints.txt pins the whole runtime closure (§17): the pyproject
# floors alone would let two installers from the same commit ship different
# fastapi/lxml/requests, and the §6.2 curated packages are the user-facing step
# environment.
#
# --no-warn-script-location: pip's Scripts\ entry points are deliberately not on
# PATH and nothing may ever invoke them (§3 — the backend and CLI always run as
# `python -m autowright.main` / `-m autowright.cli`), so the warning is noise.
Write-Host '· installing backend into bundled Python (pinned by backend\constraints.txt)'
Invoke-Native 'pip install backend' {
    & $STAGED_PY -m pip -q install --no-warn-script-location `
        -c (Join-Path $ROOT 'backend\constraints.txt') (Join-Path $ROOT 'backend')
}

# ---- smoke check: the bundled interpreter works before it is packaged -------
# §3: the check runs *before* packaging and with PYTHONDONTWRITEBYTECODE, so
# importing cannot add anything to the tree that is about to ship — the same
# principle as the macOS seal rule, where a post-signature .pyc write breaks
# the bundle (Windows has no seal, but the artifact should still be exactly
# what pip staged and nothing more).
$env:PYTHONDONTWRITEBYTECODE = '1'
Invoke-Native 'bundled Python smoke check' {
    & $STAGED_PY -c 'import autowright, fastapi, uvicorn, websockets, yaml, keyring, requests, httpx, bs4, lxml, feedparser, dateutil, tzdata; print(autowright.__version__)'
}
Remove-Item Env:\PYTHONDONTWRITEBYTECODE
Write-Host '· bundled Python imports OK'

# ---- signing (§3: signed when a certificate is configured, never silently) --
$signArgs = @()
if ($env:CSC_LINK -and $env:CSC_KEY_PASSWORD) {
    Write-Host "· signing with the certificate file in CSC_LINK"
} elseif ($env:AUTOWRIGHT_WIN_CERT_SHA1) {
    Write-Host "· signing with certificate store thumbprint $($env:AUTOWRIGHT_WIN_CERT_SHA1)"
    $signArgs = @('-c.win.certificateSha1=' + $env:AUTOWRIGHT_WIN_CERT_SHA1)
} else {
    Write-Host ''
    Write-Host '*** UNSIGNED BUILD ***'
    Write-Host '*** No CSC_LINK/CSC_KEY_PASSWORD and no AUTOWRIGHT_WIN_CERT_SHA1 — the installer and'
    Write-Host '*** the app executable will NOT be Authenticode-signed. SmartScreen will warn on'
    Write-Host '*** every download (accepted for now, SPEC §3). Do not publish this as a signed build.'
    Write-Host ''
}

# ---- package (electron-builder, Windows target only) -----------------------
# The build config lives in app\package.json's `build` key: appId
# ai.autowright.app, per-user NSIS with the stable §3 GUID, the staged
# interpreter as extraResources → resources\python, and the generic-provider
# publish entry that puts app-update.yml in the package. `--publish never`:
# uploading is release.ps1's job, once the GitHub release exists.
if (Test-Path $OUT) { Remove-Item -Recurse -Force $OUT }
Write-Host '· packaging (electron-builder --win nsis --x64)'
Push-Location $APP
try {
    Invoke-Native 'electron-builder' { npx electron-builder --win nsis --x64 --publish never @signArgs }
} finally {
    Pop-Location
}

# §3 artifact name: the bare Autowright.exe (from v0.11.0), per app/package.json's
# top-level artifactName.
$INSTALLER = Join-Path $OUT 'Autowright.exe'
$BLOCKMAP = "$INSTALLER.blockmap"
$LATEST = Join-Path $OUT 'latest.yml'
foreach ($artifact in @($INSTALLER, $BLOCKMAP, $LATEST)) {
    if (-not (Test-Path $artifact)) { Fail "packaging failed: $artifact missing" }
}

Write-Host '· dist done:'
foreach ($artifact in @($INSTALLER, $BLOCKMAP, $LATEST)) {
    $size = [math]::Round((Get-Item $artifact).Length / 1MB, 1)
    Write-Host ("    {0}  ({1} MB)" -f $artifact, $size)
}
if (-not ($env:CSC_LINK -and $env:CSC_KEY_PASSWORD) -and -not $env:AUTOWRIGHT_WIN_CERT_SHA1) {
    Write-Host '*** UNSIGNED BUILD — see the warning above ***'
}
