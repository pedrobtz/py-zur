"""Checks against an installed zur.

Run after installing a wheel, so that ``R`` and ``Rscript`` on PATH are
zur's:

    uv tool install dist/zur-*.whl
    uv run pytest tests/test_installed.py

Every check compares against a known answer rather than only running code.
Set ZUR_OFFLINE=1 to skip the network check. In CI, ZUR_CONTAINER names
the image the tests run in (the matrix of build.yml), so the checks of
what a minimal image lacks know where they are; ZUR_EXPECT_NO_CA_BUNDLE=1
runs the no-bundle check, before the workflow installs one. ZUR_WHEEL
names the installed wheel, for the reinstall check. ZUR_DOCKER=1 runs
the checks that start containers themselves (a shared and a read-only
home), from a machine with Docker and ZUR_WHEEL; they need no
installed zur. ZUR_PAK=1 runs the pak recipe and the binary packages,
which install into the user library from the network, in a container
with no compiler.
"""

import os
import re
import shlex
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from recipes import PAK_RECIPE

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


# User library (design §5, D10) ---------------------------------------------

R_MINOR = ".".join(R_VERSION.split(".")[:2])

# What /etc/os-release's ID-VERSION_ID is in each image of the matrix.
# Rocky's minor release moves with the image, so it is a pattern.
PLATFORMS = {
    "ubuntu:24.04": r"ubuntu-24\.04",
    "debian:12": r"debian-12",
    "debian:12-slim": r"debian-12",
    "rockylinux:8": r"rocky-8\.\d+",
    "rockylinux:8-minimal": r"rocky-8\.\d+",
}
# Sourcing os-release is the specification's own reading of it; bin/R
# reads it without running it.
SOURCE_OS_RELEASE = '(. /etc/os-release && printf "%s-%s\\n" "$ID" "$VERSION_ID")'


def zur_library(home):
    """Where zur's user library is for ``home`` on this machine."""
    out = run(["sh", "-c", SOURCE_OS_RELEASE])
    assert out.returncode == 0, out.stderr
    platform = out.stdout.strip()
    if CONTAINER:
        assert re.fullmatch(PLATFORMS[CONTAINER], platform), platform
    return home / "R/zur" / f"{os.uname().machine}-{platform}" / R_MINOR


# R through the launcher, and as $R_HOME/bin/Rscript without it (D13).
STARTS = ["launcher", "bare"]


def start_rscript(r_home, how, code, env):
    exe = "Rscript" if how == "launcher" else str(r_home / "bin/Rscript")
    return run([exe, "--vanilla", "-e", code], env=env)


def library_env(home, **extra):
    """The environment R starts in with ``home``, no R_LIBS_USER unless given."""
    base = dict(os.environ, HOME=str(home))
    return without("RHOME", "R_HOME", "R_LIBS_USER", base=base) | extra


@pytest.mark.parametrize("how", STARTS)
def test_user_library_is_zurs_own(r_home, tmp_path, how):
    home = tmp_path / "home"
    home.mkdir()
    result = start_rscript(r_home, how, "cat(.libPaths()[1])", library_env(home))
    assert result.returncode == 0, result.stderr
    library = zur_library(home)
    assert Path(result.stdout) == library
    assert library.is_dir()


@pytest.mark.parametrize("how", STARTS)
def test_user_library_set_by_the_user_wins(r_home, tmp_path, how):
    home, lib = tmp_path / "home", tmp_path / "my lib"
    home.mkdir()
    lib.mkdir()
    env = library_env(home, R_LIBS_USER=str(lib))
    result = start_rscript(r_home, how, "cat(.libPaths()[1])", env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout) == lib
    assert not (home / "R").exists()


def tool(*args):
    result = run(["uv", "tool", *args], timeout=600)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not os.environ.get("ZUR_WHEEL"), reason="needs ZUR_WHEEL")
def test_user_library_survives_reinstall(r_home, tmp_path):
    # Whatever the default library is (test_user_library_is_zurs_own
    # checks which), a package there outlives the tool's directory.
    library = Path(rout(".libPaths()[1]"))
    pkg = _package(
        tmp_path,
        "zurkeep",
        {"NAMESPACE": "export(hi)\n", "R/hi.R": 'hi <- function() "kept"\n'},
    )
    installed = run(["R", "CMD", "INSTALL", str(pkg)], timeout=600)  # no -l
    assert installed.returncode == 0, installed.stdout + installed.stderr
    code = "cat(zurkeep::hi(), dirname(find.package('zurkeep')))"
    wheel = os.environ["ZUR_WHEEL"]
    # A file the wheel installs, deleted, and one it does not, added: the
    # first shows a reinstall happened, the second whether the tool's
    # directory was started afresh.
    owned = r_home.parent.parent / "BUILD-INFO.json"
    stray = r_home / "zur-reinstall-marker"
    data = owned.read_bytes()
    try:
        assert (library / "zurkeep/DESCRIPTION").is_file()
        owned.unlink()
        stray.write_text("")
        tool("install", "--reinstall", wheel)
        assert owned.read_bytes() == data
        assert rout(code).split(" ", 1) == ["kept", str(library)]
        # uv's --reinstall rewrites the wheel's files and keeps any other
        # file in the tool's directory (2026-11-24); uninstalling removes
        # the directory, as a library inside it would be.
        tool("uninstall", "zur")
        tool("install", wheel)
        assert not stray.exists()
        assert rout(code).split(" ", 1) == ["kept", str(library)]
    finally:
        stray.unlink(missing_ok=True)
        shutil.rmtree(library / "zurkeep", ignore_errors=True)


# Containers started from here (ZUR_DOCKER=1): R as
# _runtime/bin/Rscript from the unpacked wheel, as the runner's user, with
# a home directory from this machine. The images, with the ID their
# os-release gives:
SHARED_HOME_IMAGES = {"ubuntu:24.04": "ubuntu", "rockylinux:8": "rocky"}
docker = pytest.mark.skipif(
    os.environ.get("ZUR_DOCKER") != "1", reason="needs ZUR_DOCKER=1 and ZUR_WHEEL"
)


@pytest.fixture(scope="module")
def unpacked_wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp("wheel")
    with zipfile.ZipFile(os.environ["ZUR_WHEEL"]) as zf:
        for info in zf.infolist():
            path = Path(zf.extract(info, root))
            path.chmod((info.external_attr >> 16) & 0o777 or 0o644)
    return root


def in_container(image, wheel, home, code, read_only=False):
    rscript = "/zur/zur/_runtime/bin/Rscript"
    script = f"{SOURCE_OS_RELEASE}; exec {rscript} -e {shlex.quote(code)}"
    return run(
        ["docker", "run", "--rm", "--quiet", "--user", f"{os.getuid()}:{os.getgid()}",
         "-v", f"{wheel}:/zur:ro",
         "-v", f"{home}:/home/zur" + (":ro" if read_only else ""),
         "-e", "HOME=/home/zur", image, "sh", "-c", script],
        timeout=600,
    )  # fmt: skip


@docker
def test_shared_home_keeps_one_library_per_distribution(unpacked_wheel, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outs = {}
    for image in SHARED_HOME_IMAGES:
        result = in_container(image, unpacked_wheel, home, "cat(.libPaths()[1])")
        assert result.returncode == 0, result.stderr
        outs[image] = result.stdout.splitlines()  # [ID-VERSION_ID, library]
    expected = {f"R/zur/x86_64-{platform}/{R_MINOR}" for platform, _ in outs.values()}
    found = {str(p.relative_to(home)) for p in home.glob("R/zur/*/*")}
    assert (len(found), found) == (2, expected)
    assert sorted(p.name for p in home.iterdir()) == ["R"]
    for image, osid in SHARED_HOME_IMAGES.items():
        platform, library = outs[image]
        assert re.fullmatch(PLATFORMS[image], platform)
        assert platform.split("-")[0] == osid
        assert library == f"/home/zur/R/zur/x86_64-{platform}/{R_MINOR}"


@docker
@pytest.mark.parametrize("image", SHARED_HOME_IMAGES)
def test_read_only_home_starts_r(unpacked_wheel, tmp_path, image):
    home = tmp_path / "home"
    home.mkdir()
    code = "cat(.libPaths()[1] == .Library, Sys.getenv('R_LIBS_USER'))"
    result = in_container(image, unpacked_wheel, home, code, read_only=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    platform, out = result.stdout.splitlines()
    # R started without the library: it could not be created.
    assert out == f"TRUE /home/zur/R/zur/x86_64-{platform}/{R_MINOR}"
    assert list(home.iterdir()) == []


# pak and P3M binaries (design §6.6, §13) -----------------------------------

pak = pytest.mark.skipif(
    os.environ.get("ZUR_PAK") != "1",
    reason="needs ZUR_PAK=1: network, and a container with no compiler",
)
COMPILERS = ("cc", "gcc", "c++", "g++", "gfortran", "make")

# The fields pak writes into an installed package's DESCRIPTION.
REMOTE_FIELDS = "RemoteType RemotePkgRef RemoteRepos RemotePkgPlatform Built"


def remote_fields(package):
    code = (
        f"d <- packageDescription({package!r});"
        f" for (f in strsplit({REMOTE_FIELDS!r}, ' ')[[1]])"
        " cat(f, ': ', d[[f]], '\\n', sep = '')"
    )
    out = rscript(code).stdout
    return dict(line.split(": ", 1) for line in out.splitlines())


@pak
def test_pak_recipe_installs_a_p3m_binary():
    # No compiler anywhere: a package that needs compiling cannot install
    # from source here, so success means pak chose a binary.
    assert [c for c in COMPILERS if shutil.which(c)] == []
    result = run(["sh", "-ec", PAK_RECIPE], timeout=900)
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert rout("data.table::data.table(a = 1:3)[, sum(a)]") == "6"
    fields = remote_fields("data.table")
    # pak records where the package came from: a P3M binary repository for
    # this distribution, not CRAN's source tarball.
    assert fields["RemoteType"] == "standard", (fields, log)
    assert fields["RemotePkgPlatform"] != "source", (fields, log)
    assert re.match(
        r"https://(p3m\.dev|packagemanager\.posit\.co)/", fields["RemoteRepos"]
    ), (fields, log)


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
    result = rscript('cat(Sys.which(c("sh", "zur-no-such-program")), sep = "|")')
    assert result.stderr == ""
    # A missing name gives "", not an error (the exit trap in WHICH).
    assert result.stdout == f"{shutil.which('sh')}|"


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


def test_bitmap_devices_use_cairo():
    # Without pango grDevices would choose "Xlib" (design §6.4); the system
    # profile sets "cairo", even under --vanilla.
    assert rout('getOption("bitmapType")') == "cairo"


def test_bitmap_type_set_by_the_user_wins():
    code = (
        'options(bitmapType = "Xlib"); invisible(loadNamespace("grDevices"));'
        ' cat(getOption("bitmapType"))'
    )
    result = run(["Rscript", "--vanilla", "--default-packages=NULL", "-e", code])
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Xlib"


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
    # cairo defines each glyph once (glyph0-1, …) and draws the text as one
    # <use> per character. A font without the characters draws them all as
    # the same box, so the characters and the glyphs they use must match
    # one to one.
    href = "{http://www.w3.org/1999/xlink}href"
    used = [
        el.get(href).lstrip("#")
        for el in root.iter(f"{SVG_NS}use")
        if re.fullmatch(r"#glyph-?\d+-\d+", el.get(href, ""))
    ]
    # cairo may or may not draw the spaces' empty glyph.
    chars = TEXT if len(used) == len(TEXT) else TEXT.replace(" ", "")
    assert len(used) == len(chars), used
    pairs = set(zip(chars, used, strict=True))
    assert len(pairs) == len(set(chars)) == len(set(used)), sorted(pairs)
    defined = {
        el.get("id")
        for el in root.iter()
        if re.fullmatch(r"glyph-?\d+-\d+", el.get("id", ""))
    }
    assert set(used) <= defined


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
