"""Checks against an installed zur.

Run after installing a wheel, so that ``R`` and ``Rscript`` on PATH are
zur's:

    uv tool install dist/zur-*.whl
    uv run pytest

Every check compares against a known answer rather than only running code.
Set ZUR_R_VERSION to the expected R version and ZUR_OFFLINE=1 to skip the
network check.
"""

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

RECOMMENDED = [
    "KernSmooth", "MASS", "Matrix", "boot", "class", "cluster", "codetools",
    "foreign", "lattice", "mgcv", "nlme", "nnet", "rpart", "spatial",
    "survival",
]  # fmt: skip


def rscript(code, env=None, check=True):
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def rout(code, **kwargs):
    return rscript(f"cat({code})", **kwargs).stdout.strip()


@pytest.fixture(scope="session")
def r_home():
    if shutil.which("Rscript") is None:
        pytest.fail("Rscript is not on PATH; install a zur wheel first")
    return Path(rout("R.home()"))


def test_linux_system_requirements():
    # Base R compiles the path /usr/bin/which into Sys.which, and utils
    # calls it on load; without it, every package but base fails to load.
    # Minimal RHEL-family images lack it (design §12).
    assert Path("/usr/bin/which").exists(), "install the system 'which' package"


def test_r_is_zur(r_home):
    assert r_home.parts[-4:] == ("zur", "_runtime", "lib", "R")


def test_r_version(r_home):
    expected = os.environ.get("ZUR_R_VERSION")
    if not expected:
        pytest.skip("ZUR_R_VERSION not set")
    assert rout('paste(R.version$major, R.version$minor, sep=".")') == expected


def test_inherited_r_home_is_ignored(r_home):
    env = dict(os.environ, R_HOME="/nonexistent/R")
    assert Path(rout("R.home()", env=env)) == r_home


def test_r_front_end_reads_stdin():
    result = subprocess.run(
        ["R", "--vanilla", "-q", "-s"],
        input="cat(sum(1:10), '\\n')",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "55"


def test_recommended_packages_load():
    code = (
        "pk <- c({}); ok <- vapply(pk, requireNamespace, logical(1), "
        "quietly = TRUE); cat(pk[!ok])"
    ).format(", ".join(f'"{p}"' for p in RECOMMENDED))
    assert rscript(code).stdout.strip() == ""


def test_blas_and_lapack_are_openblas_and_correct():
    assert "openblas" in rout('extSoftVersion()["BLAS"]')
    # inverse of [[2, 1], [1, 3]] is [[0.6, -0.2], [-0.2, 0.4]]
    assert rout("solve(matrix(c(2, 1, 1, 3), 2))") == "0.6 -0.2 -0.2 0.4"


def test_capabilities():
    caps = rout('capabilities()[c("png", "cairo", "ICU", "libcurl", "tcltk")]')
    assert caps == "TRUE TRUE TRUE TRUE TRUE"


def test_svg_with_text(tmp_path):
    svg = tmp_path / "plot.svg"
    rscript(f'svg("{svg}"); plot(1:3, main = "héllo"); invisible(dev.off())')
    assert svg.read_text().startswith("<?xml")


def test_time_zones():
    code = (
        'format(as.POSIXct("2026-11-23 12:00", tz = "UTC"), '
        'tz = "Europe/Zurich", usetz = TRUE)'
    )
    assert rout(code) == "2026-11-23 13:00:00 CET"


@pytest.mark.skipif(os.environ.get("ZUR_OFFLINE") == "1", reason="offline")
def test_https():
    status = rout('curlGetHeaders("https://pypi.org/simple/")[1]')
    assert status.startswith("HTTP/") and " 200" in status


def _package(root, name, files):
    pkg = root / name
    (pkg / "R").mkdir(parents=True)
    (pkg / "DESCRIPTION").write_text(
        f"Package: {name}\nVersion: 0.1\nTitle: Test\nDescription: Test.\n"
        "License: MIT\nAuthor: zur\nMaintainer: zur <zur@example.com>\n"
    )
    for rel, text in files.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return pkg


def _install(pkg, lib):
    result = subprocess.run(
        ["R", "CMD", "INSTALL", "-l", str(lib), str(pkg)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_install_pure_r_package(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    pkg = _package(
        tmp_path,
        "zurpure",
        {"NAMESPACE": "export(hi)\n", "R/hi.R": 'hi <- function() "pure"\n'},
    )
    _install(pkg, lib)
    out = rscript(f'.libPaths("{lib}"); cat(zurpure::hi())').stdout
    assert out.strip() == "pure"


@pytest.mark.skipif(
    shutil.which("gcc") is None or shutil.which("make") is None,
    reason="source packages need the system gcc and make",
)
def test_install_c_package_with_system_compiler(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    pkg = _package(
        tmp_path,
        "zurc",
        {
            "NAMESPACE": "useDynLib(zurc)\nexport(add)\n",
            "R/add.R": 'add <- function(a, b) .Call("add_", a, b)\n',
            "src/add.c": """\
                #include <Rinternals.h>
                SEXP add_(SEXP a, SEXP b) {
                  return Rf_ScalarReal(Rf_asReal(a) + Rf_asReal(b));
                }
            """,
        },
    )
    _install(pkg, lib)
    out = rscript(f'.libPaths("{lib}"); cat(zurc::add(2, 40))').stdout
    assert out.strip() == "42"


@pytest.mark.skipif(shutil.which("strace") is None, reason="no strace")
def test_build_prefix_is_never_opened(r_home, tmp_path):
    info = json.loads((r_home.parent.parent / "BUILD-INFO.json").read_text())
    prefix = info["build_prefix"]
    trace = tmp_path / "trace.txt"
    code = "library(Matrix); svg(tempfile()); plot(1); invisible(dev.off())"
    subprocess.run(
        ["strace", "-f", "-e", "trace=openat,execve", "-o", str(trace),
         "Rscript", "--vanilla", "-e", code],
        check=True,
        capture_output=True,
        timeout=300,
    )  # fmt: skip
    opened = [
        line
        for line in trace.read_text().splitlines()
        if prefix in line and "ENOENT" not in line
    ]
    assert opened == []
