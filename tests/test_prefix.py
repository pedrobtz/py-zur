"""Checks against R as built from source, in its install prefix.

Run in the build container after ``scripts/linux/build-r.sh``, with
ZUR_PREFIX naming the prefix R was installed into:

    ZUR_PREFIX=/opt/zur uv run pytest -v tests/test_prefix.py

Without ZUR_PREFIX the module is skipped: the prefix exists only in the
`r-source` CI job (roadmap stage 3). Every check compares against a known
answer.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

if "ZUR_PREFIX" not in os.environ:
    pytest.skip("ZUR_PREFIX is not set", allow_module_level=True)

PREFIX = Path(os.environ["ZUR_PREFIX"])
R_LIB = PREFIX / "lib/R/lib"

R_VERSION = dict(
    line.split("=", 1)
    for line in (REPO / "scripts/linux/r-source").read_text().splitlines()
    if line and not line.startswith("#")
)["R_VERSION"]

# D17's static Fortran runtime cannot be linked in the image (§18,
# 2026-11-24), so R's own libraries need the shared one; stage 5 grafts
# it under a hashed name. Until then the runtime may be needed by these
# files only, and OpenMP's by none (--disable-openmp).
FORTRAN_RUNTIME = {"libgfortran.so.5", "libquadmath.so.0"}
FORTRAN_USERS = {
    "lib/R/lib/libR.so",
    "lib/R/lib/libRblas.so",
    "lib/R/lib/libRlapack.so",
    "lib/R/library/stats/libs/stats.so",
    "lib/R/modules/lapack.so",
}


def rscript(code):
    result = subprocess.run(
        [str(PREFIX / "bin/Rscript"), "--vanilla", "-e", code],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def r_named(expr):
    # A named vector, printed by R as one name=value line per element.
    out = rscript(f"x <- {expr}; cat(paste0(names(x), '=', x), sep = '\\n')")
    return dict(line.split("=", 1) for line in out.splitlines())


def lines(path):
    text = path.read_text().splitlines()
    return [s for s in text if s and not s.startswith("#")]


def test_version_string():
    out = rscript("cat(R.version.string)")
    assert out.startswith(f"R version {R_VERSION} (")


def test_base_packages_and_no_recommended():
    out = rscript("cat(rownames(installed.packages(.Library)), sep = '\\n')")
    assert sorted(out.split()) == sorted(lines(REPO / "tests/data/base-packages.txt"))


def test_capabilities():
    caps = r_named("capabilities()")
    for name in ["cairo", "png", "jpeg", "tiff", "ICU", "libcurl"]:
        assert caps[name] == "TRUE", name
    for name in ["X11", "tcltk", "aqua"]:
        assert caps[name] == "FALSE", name


def test_cairo_without_pango():
    soft = r_named("grSoftVersion()")
    assert soft["pango"] == ""
    assert soft["cairo"] != ""


def test_fortran_and_lapack():
    out = rscript(
        "x <- solve(matrix(c(2, 1, 1, 3), 2));"
        "cat(max(abs(x - matrix(c(0.6, -0.2, -0.2, 0.4), 2))), La_library(),"
        " sep = '\\n')"
    )
    err, la = out.splitlines()
    assert float(err) < 1e-12
    assert Path(la).resolve() == (R_LIB / "libRlapack.so").resolve()


def scan():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/elf_needed.py"), str(PREFIX), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_scan_reads_r_own_libraries():
    # Without this, the two checks below would pass on a scan that read
    # nothing: libR.so needs R's BLAS and the C library.
    found = scan()
    assert {"libRblas.so", "libc.so.6"} <= set(found["lib/R/lib/libR.so"])
    assert FORTRAN_USERS | {
        "lib/R/bin/exec/R",
        "lib/R/library/grDevices/libs/cairo.so",
        "lib/R/modules/internet.so",
    } <= set(found)


def test_no_openmp_runtime():
    found = scan()
    assert {p: n for p, n in found.items() if "libgomp.so.1" in n} == {}


def test_fortran_runtime_only_in_r_own_libraries():
    found = scan()
    assert {p for p, n in found.items() if FORTRAN_RUNTIME & set(n)} == FORTRAN_USERS
