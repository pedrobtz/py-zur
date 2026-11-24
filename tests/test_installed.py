"""Checks against an installed zur.

Run after installing a wheel, so that ``R`` and ``Rscript`` on PATH are
zur's:

    uv tool install dist/zur-*.whl
    uv run pytest tests/test_installed.py

Every check compares against a known answer rather than only running code.
Set ZUR_OFFLINE=1 to skip the network check.
"""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

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


@pytest.fixture(scope="session")
def r_home():
    if shutil.which("Rscript") is None:
        pytest.fail("Rscript is not on PATH; install a zur wheel first")
    return Path(rout("R.home()"))


def test_linux_system_requirements():
    # Base R compiles the path /usr/bin/which into Sys.which, and utils
    # calls it on load; without it, every package but base fails to load.
    # Minimal RHEL-family images lack it (design §6.4; stage 6 removes it).
    assert Path("/usr/bin/which").exists(), "install the system 'which' package"


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
    # bin/R exports RHOME (design §5): R started as $R_HOME/bin/R, with no
    # RHOME from the launcher, still gives its Rscript children this R.
    env = {k: v for k, v in os.environ.items() if k not in ("RHOME", "R_HOME")}
    result = run([str(r_home / "bin/R"), "--vanilla", "-s", "-e", CHILD_HOME], env=env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == r_home


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


@pytest.mark.parametrize(
    ("var", "expected"), [("CC", "gcc"), ("CXX", "g++"), ("FC", "gfortran")]
)
def test_makeconf_names_the_machine_compilers(var, expected):
    # D5. The compiler is the plain name; configure's standard flag (for
    # example -std=gnu++20) may follow it.
    result = run(["R", "CMD", "config", var])
    assert result.returncode == 0, result.stderr
    words = result.stdout.split()
    assert words[0] == expected
    assert all(w.startswith("-std=") for w in words[1:]), words


@pytest.mark.parametrize("lang", ["C", "CXX", "F"])
def test_makeconf_openmp_flags(r_home, lang):
    # D17: R is built without OpenMP, packages use the host's. `R CMD
    # config` does not know these variables, so Makeconf is read itself.
    makeconf = (r_home / "etc/Makeconf").read_text()
    found = re.findall(rf"^SHLIB_OPENMP_{lang}FLAGS = (.*)$", makeconf, flags=re.M)
    assert found == ["-fopenmp"]


def test_time_zones():
    code = (
        'format(as.POSIXct("2026-11-23 12:00", tz = "UTC"), '
        'tz = "Europe/Zurich", usetz = TRUE)'
    )
    assert rout(code) == "2026-11-23 13:00:00 CET"


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
