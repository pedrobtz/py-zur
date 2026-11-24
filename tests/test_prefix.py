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
import re
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent

if "ZUR_PREFIX" not in os.environ:
    pytest.skip("ZUR_PREFIX is not set", allow_module_level=True)

PREFIX = Path(os.environ["ZUR_PREFIX"])
R_LIB = PREFIX / "lib/R/lib"

R_SOURCE = dict(
    line.split("=", 1)
    for line in (REPO / "scripts/linux/r-source").read_text().splitlines()
    if line and not line.startswith("#")
)
R_VERSION = R_SOURCE["R_VERSION"]

with (REPO / "manifests/linux.toml").open("rb") as f:
    MANIFEST = tomllib.load(f)["dependency"]
PINNED = {e["name"]: e["version"] for e in MANIFEST}

# D17's static Fortran runtime cannot be linked in the image (§18,
# 2026-11-24), so R's own libraries need the shared one, which the wheel
# build grafts under a hashed name (tests/test_build.py). In the prefix the
# runtime may be needed by these files only, and OpenMP's by none
# (--disable-openmp).
FORTRAN_RUNTIME = {"libgfortran.so.5", "libquadmath.so.0"}


def lines(path):
    text = path.read_text().splitlines()
    return [s for s in text if s and not s.startswith("#")]


FORTRAN_USERS = set(lines(REPO / "tests/data/fortran-users.txt"))


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
    out = rscript(
        f"x <- {expr}; cat(paste0(names(x), '=', gsub('\\n', ' ', x)), sep = '\\n')"
    )
    return dict(line.split("=", 1) for line in out.splitlines())


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


# The one build that installs pango-devel to measure what D14 saves
# (roadmap stage 9) runs only this check of the prefix, to show the
# measurement measured pango. The check above is not skipped there; the
# workflow selects this one.
@pytest.mark.skipif(
    os.environ.get("ZUR_MEASURE_PANGO") != "1", reason="the pango measurement only"
)
def test_measurement_build_has_pango():
    soft = r_named("grSoftVersion()")
    assert soft["pango"] != ""
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


# Pinned dependencies (roadmap stage 4) ---------------------------------------


def first_version(text):
    """The first dotted number in a version banner ("LIBTIFF, Version 4.0.9")."""
    return re.search(r"\d+(?:\.\d+)+", text).group()


def test_libcurl_and_openssl_versions_reach_r():
    out = rscript("v <- libcurlVersion(); cat(v, attr(v, 'ssl_version'), sep = '\\n')")
    curl, ssl = out.splitlines()
    assert curl == PINNED["curl"]
    assert ssl == f"OpenSSL/{PINNED['openssl']}"


@pytest.mark.parametrize(
    ("component", "name"),
    [
        ("zlib", "zlib"),
        ("bzlib", "bzip2"),
        ("xz", "xz"),
        ("PCRE", "pcre2"),
        ("ICU", "icu"),
        ("readline", "readline"),
    ],
)
def test_ext_soft_versions_reach_r(component, name):
    assert first_version(r_named("extSoftVersion()")[component]) == PINNED[name]


@pytest.mark.parametrize(
    ("component", "name"),
    # Only cairo: R reaches libpng, libjpeg and libtiff through its cairo
    # module alone, so grSoftVersion() reports no version for them.
    [("cairo", "cairo")],
)
def test_graphics_versions_reach_r(component, name):
    assert first_version(r_named("grSoftVersion()")[component]) == PINNED[name]


def test_build_info_lists_the_manifest_and_r():
    info = json.loads((PREFIX / "BUILD-INFO.json").read_text())["inputs"]
    assert info["r"] == {
        "version": R_SOURCE["R_VERSION"],
        "url": R_SOURCE["R_URL"],
        "sha256": R_SOURCE["R_SHA256"],
    }
    image = (REPO / "scripts/linux/image").read_text().splitlines()[-1]
    assert info["image"] == image
    assert info["dependencies"] == MANIFEST


# Libraries no manifest entry needs to name: R's own, the Fortran runtime
# (stage 5 grafts it) and glibc and the compiler's C and C++ runtimes,
# which the manylinux_2_28 policy lets a wheel take from the system.
NOT_PINNED = (
    {"libR.so", "libRblas.so", "libRlapack.so"}
    | FORTRAN_RUNTIME
    | {
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libdl.so.2",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "libresolv.so.2",
        "librt.so.1",
        "libstdc++.so.6",
        "libutil.so.1",
    }
)


def test_manifest_names_every_library_r_links():
    linked = {n for needed in scan().values() for n in needed}
    pinned = {s for e in MANIFEST for s in e["sonames"]}
    assert linked - NOT_PINNED - pinned == set()
    # The libraries stage 3 saw R link (design §18.1) are among them.
    assert {
        "libcurl.so.4",
        "libicuuc.so.60",
        "libpcre2-8.so.0",
        "libcairo.so.2",
    } <= linked


def test_no_usr_local_paths():
    # configure defaults to -I/usr/local/include and -L/usr/local/lib, and
    # the image sets PKG_CONFIG_PATH=/usr/local/lib/pkgconfig: the image
    # keeps builds there that are no pinned input.
    for name in ["etc/Makeconf", "etc/ldpaths"]:
        lines = (PREFIX / "lib/R" / name).read_text().splitlines()
        # "##" lines are comments of R's own templates; one names
        # /usr/local/lib as an example. configure's record of its call
        # ("# configure ...") is kept, as it shows the environment.
        found = [s for s in lines if "/usr/local" in s and not s.startswith("##")]
        assert found == [], name
