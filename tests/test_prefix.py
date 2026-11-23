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

# The compiler runtimes R's own libraries must not need (D17, §6.3).
RUNTIMES = {"libgfortran.so.5", "libquadmath.so.0", "libgomp.so.1"}


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


def test_no_shared_compiler_runtime():
    found = scan()
    # The scan must have read R's libraries, or finding nothing proves
    # nothing: libR.so needs R's BLAS and the C library.
    assert {"libRblas.so", "libc.so.6"} <= set(found["lib/R/lib/libR.so"])
    assert {"lib/R/lib/libRblas.so", "lib/R/lib/libRlapack.so"} <= set(found)
    assert len(found) > 20
    bad = {p: sorted(RUNTIMES & set(n)) for p, n in found.items()}
    assert {p: n for p, n in bad.items() if n} == {}
