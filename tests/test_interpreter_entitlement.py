"""§3 interpreter entitlement, exercised for real (macOS only).

`tests/test_drift_guards.py` pins the entitlement *text* in `prod.sh`. This
file proves the text does what §3 relies on: a hardened-runtime process signed
with the interpreter entitlement plist, taken verbatim out of `prod.sh`, can
dlopen a library that is only ad-hoc signed - the signature every pip wheel's
extension module carries into `<app-support>/site-packages` (§6.2). Without the
entitlement, dyld refuses with "different Team IDs" and numpy reports
"Importing the numpy C-extensions failed"; every release up to 0.10.1 shipped
that way, and nothing in the dev loop shows it because the repo venv's
interpreter is unsigned.

The host is a five-line C program compiled here (the bundled interpreter only
exists inside a production build), signed ad-hoc with `--options runtime` the
way `prod.sh` signs the interpreter with the Developer ID: library validation
is a property of the hardened runtime, not of the identity, so the ad-hoc
host reproduces the production behavior exactly (the control below proves
it). Skipped off macOS or without a C compiler and codesign; a skip is not a
pass, so a release build still runs the §3 post-sign probe regardless.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not (shutil.which("cc") and shutil.which("codesign")),
    reason="macOS library validation needs darwin, cc, and codesign")

HOST_C = r"""
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv) {
  void *h = dlopen(argv[1], RTLD_NOW);
  if (!h) { fprintf(stderr, "%s\n", dlerror()); return 1; }
  return 0;
}
"""
LIB_C = "int autowright_probe(void) { return 1; }\n"


def _prod_sh_plists() -> dict[str, str]:
    """The entitlement plists `prod.sh` generates, keyed by shell variable."""
    src = (REPO / "scripts" / "prod.sh").read_text(encoding="utf-8")
    return dict(re.findall(
        r'(\w+)="\$BUILD/[\w-]+\.plist"\ncat > "\$\1" <<\'EOF\'\n(.*?)\nEOF', src, re.S))


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _build_probe(tmp_path: Path) -> tuple[Path, Path]:
    """Compile the dlopen host and an ad-hoc-signed library, exactly the
    signature a wheel's extension module carries (no Team ID)."""
    (tmp_path / "host.c").write_text(HOST_C)
    (tmp_path / "lib.c").write_text(LIB_C)
    host, lib = tmp_path / "host", tmp_path / "libprobe.dylib"
    for cmd in (("cc", "-o", str(host), str(tmp_path / "host.c")),
                ("cc", "-shared", "-o", str(lib), str(tmp_path / "lib.c")),
                ("codesign", "--force", "-s", "-", str(lib))):
        r = _run(*cmd)
        assert r.returncode == 0, r.stderr
    return host, lib


def _sign_host(host: Path, plist_text: str, tmp_path: Path) -> None:
    """Sign the host the way prod.sh signs the interpreter: hardened runtime
    plus the given entitlements (identity aside)."""
    plist = tmp_path / "entitlements.plist"
    plist.write_text(plist_text)
    r = _run("codesign", "--force", "--options", "runtime", "-s", "-",
             "--entitlements", str(plist), str(host))
    assert r.returncode == 0, r.stderr


def test_interpreter_entitlement_lets_a_hardened_process_load_an_adhoc_library(tmp_path):
    """The regression test proper: the interpreter plist from `prod.sh`, on a
    hardened-runtime host, must let dlopen accept an ad-hoc-signed library."""
    host, lib = _build_probe(tmp_path)
    _sign_host(host, _prod_sh_plists()["PY_ENTITLEMENTS"], tmp_path)
    r = _run(str(host), str(lib))
    assert r.returncode == 0, (
        "a hardened-runtime process signed with prod.sh's interpreter entitlements "
        f"cannot load an ad-hoc-signed library - every native §6.2 wheel would fail "
        f"at import on user Macs:\n{r.stderr}")


def test_without_the_entitlement_library_validation_still_rejects_adhoc_code(tmp_path):
    """Control, so the test above cannot pass vacuously: the Electron
    entitlements (no library-validation exception) on the same host must make
    dyld refuse the same library for a code-signature reason. If macOS ever
    stopped enforcing this, the regression test would prove nothing."""
    host, lib = _build_probe(tmp_path)
    _sign_host(host, _prod_sh_plists()["ENTITLEMENTS"], tmp_path)
    r = _run(str(host), str(lib))
    assert r.returncode != 0
    assert "code signature" in r.stderr and "Team ID" in r.stderr, r.stderr
