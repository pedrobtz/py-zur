"""Checks against an installed zur.

Run after installing a wheel, so that ``R`` and ``Rscript`` on PATH are
zur's:

    uv tool install dist/zur-*.whl
    uv run pytest tests/test_installed.py

Every check compares against a known answer rather than only running code.
Set ZUR_OFFLINE=1 to skip the network check. In CI, ZUR_CONTAINER names
the image the tests run in (the matrix of build.yml), so the checks of
what a minimal image lacks know where they are; ZUR_EXPECT_NO_CA_BUNDLE=1
runs the no-bundle check, before the workflow installs one.
"""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from xml.etree import ElementTree

import pytest

REPO = Path(__file__).resolve().parent.parent

# scripts/linux/build-r.sh and build-deps.sh install into these; the
# second starts with the first, so one search finds both.
BUILD_PREFIX = "/opt/zur"

# The CA bundle bin/R should pick, by /etc/os-release ID (design §6.4).
CA_BUNDLES = {
    "ubuntu": "/etc/ssl/certs/ca-certificates.crt",
    "debian": "/etc/ssl/certs/ca-certificates.crt",
    "rocky": "/etc/pki/tls/certs/ca-bundle.crt",
}
# Every bundle bin/R looks for, in its order (scripts/build.py).
CA_CANDIDATES = [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/tls/cacert.pem",
    "/etc/ssl/cert.pem",
]

CONTAINER = os.environ.get("ZUR_CONTAINER", "")


def lines(path):
    text = path.read_text().splitlines()
    return [s for s in text if s and not s.startswith("#")]


R_VERSION = dict(line.split("=", 1) for line in lines(REPO / "scripts/linux/r-source"))[
    "R_VERSION"
]


def run(args, env=None, timeout=300, **kwargs):
    return subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=timeout, **kwargs
    )


def rscript(code, env=None, check=True):
    result = run(["Rscript", "--vanilla", "-e", code], env=env)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def rout(code, **kwargs):
    return rscript(f"cat({code})", **kwargs).stdout.strip()


def os_release_id():
    text = Path("/etc/os-release").read_text()
    return re.search(r'^ID="?([^"\n]+)"?$', text, flags=re.M)[1]


def without(*names, base=None):
    """The environment without ``names``, as R started outside zur sees it."""
    env = dict(os.environ if base is None else base)
    for name in names:
        env.pop(name, None)
    return env


@pytest.fixture(scope="session")
def r_home():
    if shutil.which("Rscript") is None:
        pytest.fail("Rscript is not on PATH; install a zur wheel first")
    return Path(rout("R.home()"))


# Minimal images ----------------------------------------------------------------


@pytest.mark.skipif(
    CONTAINER not in ("debian:12-slim", "rockylinux:8-minimal"),
    reason="only the minimal images",
)
def test_minimal_image_lacks_what_zur_must_replace():
    # Without this, the font, time-zone and which checks below could pass
    # on an image that had what they test zur does without.
    if CONTAINER == "debian:12-slim":
        assert not Path("/usr/share/zoneinfo").exists()
        fonts = Path("/usr/share/fonts")
        assert not fonts.exists() or not any(p.is_file() for p in fonts.rglob("*"))
    if CONTAINER == "rockylinux:8-minimal":
        assert not Path("/usr/bin/which").exists()
        assert shutil.which("which") is None


# Front-ends ------------------------------------------------------------------


def test_r_home_is_inside_the_tool_directory(r_home):
    tools = Path(run(["uv", "tool", "dir"]).stdout.strip()).resolve()
    assert r_home.is_relative_to(tools / "zur")
    assert r_home.parts[-4:] == ("zur", "_runtime", "lib", "R")


def test_r_version():
    assert rout("R.version.string").startswith(f"R version {R_VERSION} (")


def test_inherited_r_home_is_ignored(r_home):
    env = dict(os.environ, R_HOME="/nonexistent/R")
    assert Path(rout("R.home()", env=env)) == r_home


def test_r_front_end_reads_stdin():
    result = run(["R", "--vanilla", "-q", "-s"], input="cat(sum(1:10), '\\n')")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "55"


def test_exit_status_is_passed_through():
    result = run(["Rscript", "-e", "quit(status = 3)"])
    assert result.returncode == 3, result.stderr


CHILD_HOME = (
    'cat(system2(file.path(R.home("bin"), "Rscript"), '
    "c('-e', shQuote('cat(R.home())')), stdout = TRUE))"
)


def test_rscript_started_from_r_finds_the_same_r(r_home):
    assert Path(rout(CHILD_HOME)) == r_home


def test_rscript_started_from_r_without_the_launcher(r_home):
    # R started as $R_HOME/bin/R, with no RHOME from anywhere, still gives
    # its Rscript children this R: bin/Rscript sets RHOME (design §5).
    env = without("RHOME", "R_HOME")
    result = run([str(r_home / "bin/R"), "--vanilla", "-s", "-e", CHILD_HOME], env=env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == r_home


# bin/Rscript is a script that sets RHOME from its own location (design §5):
# R_HOME's, and the one in the prefix's bin/ next to bin/R.
RSCRIPTS = ["lib/R/bin/Rscript", "bin/Rscript"]


def bare_rscript(r_home, where):
    return r_home.parent.parent / where


@pytest.mark.parametrize("where", RSCRIPTS)
def test_bare_rscript_finds_its_r(r_home, where):
    result = run(
        [str(bare_rscript(r_home, where)), "-e", "cat(R.home())"],
        env=without("RHOME", "R_HOME"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(r_home)


@pytest.mark.parametrize("where", RSCRIPTS)
def test_bare_rscript_overrides_an_inherited_home(r_home, where):
    # D13: an inherited RHOME or R_HOME is overridden, never honoured.
    env = dict(os.environ, RHOME="/nonexistent/R", R_HOME="/nonexistent/R")
    result = run([str(bare_rscript(r_home, where)), "-e", "cat(R.home())"], env=env)
    assert result.returncode == 0, result.stderr
    assert (result.stdout, result.stderr) == (str(r_home), "")


@pytest.mark.parametrize("where", RSCRIPTS)
def test_rscript_as_a_script_interpreter(r_home, where, tmp_path):
    script = tmp_path / "home.R"
    script.write_text(f"#!{bare_rscript(r_home, where)}\ncat(R.home())\n")
    script.chmod(0o755)
    result = run([str(script)], env=without("RHOME", "R_HOME"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(r_home)


# Environment in R's own files (D13) -----------------------------------------

ENV_VARS = ("FONTCONFIG_FILE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")


def test_environment_is_the_same_without_the_launcher(r_home):
    code = f"cat(Sys.getenv(c({', '.join(map(repr, ENV_VARS))})), sep = '|')"
    env = without("RHOME", "R_HOME", *ENV_VARS)
    launched = run(["Rscript", "--vanilla", "-e", code], env=env)
    bare = run([str(r_home / "bin/Rscript"), "--vanilla", "-e", code], env=env)
    assert launched.returncode == 0 and bare.returncode == 0, bare.stderr
    assert bare.stdout == launched.stdout
    fonts, ca, ssl = bare.stdout.split("|")
    assert fonts == str(r_home / "etc/fonts/fonts.conf")
    if any(Path(p).is_file() for p in CA_CANDIDATES):
        assert ca == ssl == CA_BUNDLES[os_release_id()]


# Base R ----------------------------------------------------------------------


def test_base_packages_only():
    out = rscript("cat(rownames(installed.packages(.Library)), sep = '\\n')").stdout
    assert sorted(out.split()) == sorted(lines(REPO / "tests/data/base-packages.txt"))


def test_blas_and_lapack_known_answer(r_home):
    out = rscript(
        "x <- solve(matrix(c(2, 1, 1, 3), 2));"
        "cat(max(abs(x - matrix(c(0.6, -0.2, -0.2, 0.4), 2))), La_library(),"
        " extSoftVersion()[['BLAS']], sep = '\\n')"
    ).stdout
    err, la, blas = out.splitlines()
    assert float(err) < 1e-12
    assert Path(la).resolve() == (r_home / "lib/libRlapack.so").resolve()
    assert Path(blas).resolve() == (r_home / "lib/libRblas.so").resolve()


@pytest.mark.skipif(shutil.which("make") is None, reason="R CMD config runs make")
@pytest.mark.parametrize(
    ("var", "expected"), [("CC", "gcc"), ("CXX", "g++"), ("FC", "gfortran")]
)
def test_makeconf_names_the_machine_compilers(var, expected):
    # D5. The compiler is the plain name; for C++ and Fortran, configure's
    # standard flag (for example -std=gnu++17) may follow it.
    result = run(["R", "CMD", "config", var])
    assert result.returncode == 0, result.stderr
    words = result.stdout.split()
    assert words[0] == expected
    # C: plain gcc, so every host gcc accepts it (design §6.3).
    assert (
        words[1:] == []
        if var == "CC"
        else all(w.startswith("-std=") for w in words[1:])
    ), words


@pytest.mark.parametrize("lang", ["C", "CXX", "F"])
def test_makeconf_openmp_flags(r_home, lang):
    # D17: R is built without OpenMP, packages use the host's. `R CMD
    # config` does not know these variables, so Makeconf is read itself.
    makeconf = (r_home / "etc/Makeconf").read_text()
    found = re.findall(rf"^SHLIB_OPENMP_{lang}FLAGS = (.*)$", makeconf, flags=re.M)
    assert found == ["-fopenmp"]


@pytest.mark.parametrize(
    ("utc", "zurich"),
    [
        ("2026-11-23 12:00", "2026-11-23 13:00:00 CET"),
        ("2026-07-01 12:00", "2026-07-01 14:00:00 CEST"),
    ],
)
def test_time_zones(utc, zurich):
    # R's own zoneinfo (--with-internal-tzcode, design §6.2): debian:12-slim
    # has no tzdata.
    code = (
        f'format(as.POSIXct("{utc}", tz = "UTC"), tz = "Europe/Zurich", usetz = TRUE)'
    )
    assert rout(code) == zurich


# which (design §6.4) ---------------------------------------------------------


def test_sys_which_without_usr_bin_which():
    # rockylinux:8-minimal has no /usr/bin/which; R must start cleanly and
    # Sys.which() must search PATH as the shell does.
    result = rscript('cat(Sys.which(c("sh", "zur-no-such-program")), sep = "\\n")')
    assert result.stderr == ""
    found, missing = result.stdout.split("\n")
    assert found == shutil.which("sh")
    assert missing == ""


# Graphics headless (design §6.4, §13) ----------------------------------------

# Escapes, so the string is UTF-8 in R whatever the locale (CI's is C).
TEXT = "Zürich 3 €"
TEXT_R = '"Z\\u00fcrich 3 \\u20ac"'
SVG_NS = "{http://www.w3.org/2000/svg}"


def draw(tmp_path, name, device, text=True):
    """``device`` (R code, ``{out}`` the file) with ``TEXT`` drawn on a plot."""
    out = tmp_path / name
    label = f"text(0.5, 0.5, {TEXT_R}, cex = 3); " if text else ""
    code = device.format(out=out) + f"; plot.new(); {label}invisible(dev.off())"
    result = run(["Rscript", "--vanilla", "-e", code], env=without("DISPLAY"))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""  # no fontconfig error, no conversion warning
    return out.read_bytes()


def test_png_draws_text(tmp_path):
    with_text = draw(tmp_path, "text.png", 'png("{out}")')
    assert with_text[:8] == b"\x89PNG\r\n\x1a\n"
    assert with_text != draw(tmp_path, "empty.png", 'png("{out}")', text=False)


def test_pdf_draws_text(tmp_path):
    # WinAnsi (CP1252) has the euro sign; pdf()'s default, ISO Latin 1,
    # has not.
    pdf = draw(tmp_path, "text.pdf", 'pdf("{out}", encoding = "WinAnsi")')
    assert pdf[:4] == b"%PDF"


def test_svg_draws_one_glyph_per_character(tmp_path):
    root = ElementTree.fromstring(draw(tmp_path, "text.svg", 'svg("{out}")'))
    assert root.tag == f"{SVG_NS}svg"
    # cairo defines each glyph it draws once (glyph0-1, …); a font without
    # the characters draws them all as the same box, and the space's glyph
    # has no outline.
    glyphs = [
        el for el in root.iter() if re.fullmatch(r"glyph-?\d+-\d+", el.get("id", ""))
    ]
    outlines = {
        el.get("id")
        for el in glyphs
        if any(p.get("d", "").strip() for p in el.iter(f"{SVG_NS}path"))
    }
    assert len(outlines) == len(set(TEXT.replace(" ", "")))


def test_cairo_device_loads_its_grafted_libraries(tmp_path):
    # cairo.so needs the most grafted libraries (cairo, pixman, X11 bits,
    # fontconfig, freetype, libpng), none of which a container provides.
    out = tmp_path / "plot.png"
    rscript(f'png("{out}", type = "cairo"); plot.new(); invisible(dev.off())')
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# HTTPS -----------------------------------------------------------------------


def test_ca_bundle_is_the_system_one():
    expected = CA_BUNDLES[os_release_id()]
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CURL_CA_BUNDLE", "SSL_CERT_FILE")
    }
    out = rout('Sys.getenv(c("CURL_CA_BUNDLE", "SSL_CERT_FILE")), sep = "\\n"', env=env)
    assert out.splitlines() == [expected, expected]


@pytest.mark.skipif(
    os.environ.get("ZUR_EXPECT_NO_CA_BUNDLE") != "1",
    reason="only before the workflow installs a CA bundle",
)
def test_no_ca_bundle_leaves_the_variables_unset():
    assert [p for p in CA_CANDIDATES if Path(p).exists()] == []
    env = without(*ENV_VARS)
    # R still starts, and bin/R sets neither variable (unset, not empty).
    code = 'cat(is.na(Sys.getenv(c("CURL_CA_BUNDLE", "SSL_CERT_FILE"), NA)))'
    out = rscript(code, env=env)
    assert (out.stdout, out.stderr) == ("TRUE TRUE", "")


def test_ca_bundle_set_by_the_user_wins(tmp_path):
    env = dict(os.environ, CURL_CA_BUNDLE="/x/ca.pem", SSL_CERT_FILE="/y/ca.pem")
    out = rout('Sys.getenv(c("CURL_CA_BUNDLE", "SSL_CERT_FILE")), sep = "\\n"', env=env)
    assert out.splitlines() == ["/x/ca.pem", "/y/ca.pem"]


@pytest.mark.skipif(os.environ.get("ZUR_OFFLINE") == "1", reason="offline")
def test_https_download(tmp_path):
    # A small file whose content is known: CRAN's record of R releases.
    dest = tmp_path / "VERSION-INFO.dcf"
    rscript(
        'download.file("https://cloud.r-project.org/src/base/VERSION-INFO.dcf",'
        f' "{dest}", method = "libcurl", quiet = TRUE)'
    )
    assert re.search(r"^Release: \d+\.\d+\.\d+$", dest.read_text(), flags=re.M)


# Source packages -------------------------------------------------------------


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
    result = run(["R", "CMD", "INSTALL", "-l", str(lib), str(pkg)], timeout=600)
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


# Relocation ------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("strace") is None, reason="no strace")
def test_build_prefix_is_never_opened(tmp_path):
    trace = tmp_path / "trace.txt"
    code = (
        "invisible(solve(diag(2))); invisible(libcurlVersion());"
        'png(tempfile(), type = "cairo"); plot.new(); invisible(dev.off())'
    )
    result = run(
        ["strace", "-f", "-e", "trace=openat,execve", "-o", str(trace),
         "Rscript", "--vanilla", "-e", code],
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    calls = trace.read_text().splitlines()
    assert any("libR.so" in c for c in calls)  # the trace saw R load
    opened = [c for c in calls if BUILD_PREFIX in c and "ENOENT" not in c]
    assert opened == []
