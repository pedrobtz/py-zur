"""Checks that need no build and no installed zur.

The quick CI profile runs this file on its own, before any build:

    uv run pytest -v tests/test_unit.py

Later stages add the build-free checks here (size check, version rules,
``zur doctor``'s tables, the benchmark gate). Like ``test_installed.py``,
every check compares against a known answer.
"""

import hashlib
import importlib.util
import io
import json
import os
import re
import struct
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent

# SHA-256 of https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt,
# fetched 2026-11-24.
GPL_2_SHA256 = "edaef632cbb643e4e7a221717a6c441a4c1a7c918e6e4d56debc3d8739b233f6"


def test_suite_is_shuffled(pytestconfig):
    # pytest-randomly shuffles the order and prints its seed in the header.
    assert pytestconfig.pluginmanager.hasplugin("randomly")
    assert isinstance(pytestconfig.getoption("randomly_seed"), int)


def test_license_is_gpl_2():
    text = (REPO / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == GPL_2_SHA256


def test_wheel_metadata_claims_the_license_file():
    # pack() writes the wheel's METADATA; its licence must be the one in
    # LICENSE. The field becomes License-Expression in stage 13.
    source = (REPO / "scripts/build.py").read_text()
    claimed = re.findall(r"License(?:-Expression)?: ([^\\\n\"]+)", source)
    assert claimed == ["GPL-2.0-or-later"]


# scripts/elf_needed.py ------------------------------------------------------
#
# The static-runtime check (tests/test_prefix.py) passes when the script
# finds nothing forbidden, so a script that read no DT_NEEDED would pass
# it. These checks run it on ELF files built here with known entries.


def tiny_elf(needed):
    """A minimal ELF64 shared object whose .dynamic lists ``needed``."""
    dynstr = b"\0" + b"".join(n.encode() + b"\0" for n in needed)
    offsets = [1 + sum(len(m) + 1 for m in needed[:i]) for i in range(len(needed))]
    dynamic = b"".join(struct.pack("<qQ", 1, o) for o in offsets)  # DT_NEEDED
    dynamic += struct.pack("<qQ", 0, 0)  # DT_NULL
    shstrtab = b"\0.dynstr\0.dynamic\0.shstrtab\0"
    body = dynstr + dynamic + shstrtab
    shoff = 64 + len(body)
    header = b"\x7fELF\x02\x01\x01" + bytes(9)
    header += struct.pack(
        "<HHIQQQIHHHHHH", 3, 62, 1, 0, 0, shoff, 0, 64, 56, 0, 64, 4, 3
    )

    def section(name, kind, offset, size, link=0, entsize=0):
        return struct.pack(
            "<IIQQQQIIQQ", name, kind, 0, 0, offset, size, link, 0, 1, entsize
        )

    sections = bytes(64)  # SHN_UNDEF
    sections += section(1, 3, 64, len(dynstr))  # .dynstr, SHT_STRTAB
    sections += section(9, 6, 64 + len(dynstr), len(dynamic), link=1, entsize=16)
    sections += section(18, 3, 64 + len(dynstr) + len(dynamic), len(shstrtab))
    return header + body + sections


def elf_needed(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/elf_needed.py"), *map(str, args)],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def elf_tree(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/libR.so").write_bytes(tiny_elf(["libRblas.so", "libc.so.6"]))
    (tmp_path / "lib/libRlapack.so").write_bytes(
        tiny_elf(["libgfortran.so.5", "libm.so.6"])
    )
    (tmp_path / "lib/none.so").write_bytes(tiny_elf([]))
    (tmp_path / "README").write_text("not ELF\n")
    (tmp_path / "lib/link.so").symlink_to("libR.so")
    return tmp_path


def test_elf_needed_reads_dt_needed(elf_tree):
    result = elf_needed(elf_tree, "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "lib/libR.so": ["libRblas.so", "libc.so.6"],
        "lib/libRlapack.so": ["libgfortran.so.5", "libm.so.6"],
        "lib/none.so": [],
    }


def test_elf_needed_forbid_fails_on_a_match(elf_tree):
    result = elf_needed(
        elf_tree, "--forbid", "libgomp.so.1", "--forbid", "libgfortran.so.5"
    )
    assert result.returncode == 1
    assert result.stderr == "forbidden: lib/libRlapack.so needs libgfortran.so.5\n"


def test_elf_needed_forbid_passes_without_a_match(elf_tree):
    result = elf_needed(elf_tree, "--forbid", "libgomp.so.1")
    assert result.returncode == 0, result.stderr
    assert "lib/libR.so: libRblas.so, libc.so.6\n" in result.stdout


# scripts/deps.py and the pinned inputs (roadmap stage 4) --------------------
#
# Each check runs the build's own entry point (build-deps.sh, build-r.sh)
# on file:// URLs, so the code that refuses a wrong pin in CI is the code
# that refuses it here.

MANIFEST = REPO / "manifests/linux.toml"


def deps_py(*args, env=None):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/deps.py"), *map(str, args)],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def run_script(script, *args, env):
    return subprocess.run(
        ["bash", str(REPO / "scripts/linux" / script), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "ZUR_PYTHON": sys.executable, **env},
    )


def tarball(path, top, files):
    """A .tar.gz at ``path`` holding ``top/<name>`` for each of ``files``."""
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_entry(name, url, sha):
    return textwrap.dedent(f"""\
        [[dependency]]
        name = "{name}"
        version = "9.9"
        origin = "source"
        url = "{url}"
        sha256 = "{sha}"
        license = "MIT"
        sonames = ["lib{name}.so.9"]
        why = "test"
        """)


@pytest.fixture
def two_sources(tmp_path):
    """openssl and curl tarballs whose configure scripts leave a marker."""
    marker = tmp_path / "compiled"
    script = f'#!/bin/sh\necho "$(basename "$PWD")" >> {marker}\nexit 1\n'
    shas = {}
    for name, configure in [("openssl", "Configure"), ("curl", "configure")]:
        shas[name] = tarball(
            tmp_path / f"{name}-9.9.tar.gz", f"{name}-9.9", {configure: script}
        )

    def run(sha_override=None):
        entries = [
            source_entry(
                n,
                (tmp_path / f"{n}-9.9.tar.gz").as_uri(),
                (sha_override or {}).get(n, s),
            )
            for n, s in shas.items()
        ]
        (tmp_path / "linux.toml").write_text("\n".join(entries))
        return run_script(
            "build-deps.sh",
            env={
                "ZUR_MANIFEST": str(tmp_path / "linux.toml"),
                "ZUR_DEPS": str(tmp_path / "deps"),
                "ZUR_DEPS_WORK": str(tmp_path / "work"),
            },
        )

    return run, marker, tmp_path / "work"


def test_build_deps_reaches_the_recipe_with_right_pins(two_sources):
    # The control for the next test: with every SHA-256 right, the first
    # recipe runs (its configure script fails on purpose).
    run, marker, _ = two_sources
    result = run()
    assert result.returncode != 0
    assert marker.read_text() == "openssl-9.9\n"


def test_build_deps_wrong_sha256_fails_before_compiling(two_sources):
    # The second entry is wrong: nothing is compiled, not even the first.
    run, marker, work = two_sources
    result = run({"curl": "0" * 64})
    assert result.returncode == 1
    assert result.stderr.startswith(
        f"deps.py: SHA-256 mismatch for curl-9.9.tar.gz: expected {'0' * 64}, got "
    )
    assert not marker.exists()
    assert sorted(p.name for p in work.iterdir()) == ["openssl-9.9.tar.gz"]


@pytest.mark.parametrize("right", [True, False])
def test_r_tarball_wrong_sha256_fails_before_unpacking(tmp_path, right):
    sha = tarball(tmp_path / "R-0.0.1.tar.gz", "R-0.0.1", {"configure": "true\n"})
    (tmp_path / "r-source").write_text(
        "R_VERSION=0.0.1\n"
        f"R_URL={(tmp_path / 'R-0.0.1.tar.gz').as_uri()}\n"
        f"R_SHA256={sha if right else 'f' * 64}\n"
    )
    work = tmp_path / "work"
    result = run_script(
        "build-r.sh",
        "source",
        env={"ZUR_R_SOURCE": str(tmp_path / "r-source"), "ZUR_WORK": str(work)},
    )
    if right:
        assert result.returncode == 0, result.stderr
        assert (work / "R-0.0.1/configure").exists()
    else:
        assert result.returncode == 1
        assert "SHA-256 mismatch for R-0.0.1.tar.gz" in result.stderr
        assert list(work.iterdir()) == []


def test_build_info_lists_the_manifest_and_r(tmp_path):
    (tmp_path / "linux.toml").write_text(
        source_entry("curl", "https://example.org/curl-9.9.tar.gz", "a" * 64)
        + textwrap.dedent("""
            [[dependency]]
            name = "zlib"
            version = "1.2.11"
            origin = "image"
            package = "zlib-devel"
            package_version = "1.2.11-25.el8"
            license = "Zlib"
            sonames = ["libz.so.1"]
            why = "test"
            """)
    )
    (tmp_path / "r-source").write_text(
        "# comment\nR_VERSION=4.6.2\nR_URL=https://example.org/R.tar.gz\n"
        f"R_SHA256={'b' * 64}\n"
    )
    (tmp_path / "image").write_text("# comment\nquay.io/x:1@sha256:" + "c" * 64)
    result = deps_py(
        "build-info",
        tmp_path / "out.json",
        env={
            "ZUR_MANIFEST": str(tmp_path / "linux.toml"),
            "ZUR_R_SOURCE": str(tmp_path / "r-source"),
            "ZUR_IMAGE": str(tmp_path / "image"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "out.json").read_text()) == {
        "inputs": {
            "r": {
                "version": "4.6.2",
                "url": "https://example.org/R.tar.gz",
                "sha256": "b" * 64,
            },
            "image": "quay.io/x:1@sha256:" + "c" * 64,
            "dependencies": [
                {
                    "name": "curl",
                    "version": "9.9",
                    "origin": "source",
                    "url": "https://example.org/curl-9.9.tar.gz",
                    "sha256": "a" * 64,
                    "license": "MIT",
                    "sonames": ["libcurl.so.9"],
                    "why": "test",
                },
                {
                    "name": "zlib",
                    "version": "1.2.11",
                    "origin": "image",
                    "package": "zlib-devel",
                    "package_version": "1.2.11-25.el8",
                    "license": "Zlib",
                    "sonames": ["libz.so.1"],
                    "why": "test",
                },
            ],
        }
    }


@pytest.mark.parametrize(
    ("drop", "message"),
    [("sha256", "curl: missing sha256"), ("license", "curl: missing license")],
)
def test_manifest_entry_missing_a_field_is_refused(tmp_path, drop, message):
    entry = source_entry("curl", "https://example.org/curl-9.9.tar.gz", "a" * 64)
    lines = [s for s in entry.splitlines() if not s.startswith(f"{drop} =")]
    (tmp_path / "linux.toml").write_text("\n".join(lines))
    result = deps_py("check", env={"ZUR_MANIFEST": str(tmp_path / "linux.toml")})
    assert result.returncode == 1
    assert result.stderr == f"deps.py: {message}\n"


def test_committed_manifest():
    # Valid, libcurl and OpenSSL built from source (§16 question 2), and
    # each source URL names the version the entry claims.
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    assert deps_py("check").returncode == 0
    sources = {e["name"]: e for e in entries if e["origin"] == "source"}
    assert sorted(sources) == ["curl", "openssl"]
    for e in sources.values():
        assert Path(e["url"]).name.startswith(f"{e['name']}-{e['version']}.tar.")
    sonames = [s for e in entries for s in e["sonames"]]
    assert len(sonames) == len(set(sonames))


@pytest.fixture
def fake_rpm(tmp_path):
    """An ``rpm`` on PATH that reports the versions in ``installed``."""
    installed = {}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rpm").write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"v = json.load(open({str(tmp_path / 'rpm.json')!r})).get(sys.argv[-1])\n"
        "print(v or f'package {sys.argv[-1]} is not installed')\n"
        "sys.exit(0 if v else 1)\n"
    )
    (bin_dir / "rpm").chmod(0o755)

    def run(versions):
        installed.update(versions)
        (tmp_path / "rpm.json").write_text(json.dumps(installed))
        return deps_py(
            "rpm-check", env={"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        )

    return run


def test_rpm_check_accepts_the_pinned_versions(fake_rpm):
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    pinned = {
        e["package"]: e["package_version"]
        if ":" in e["package_version"]
        else "0:" + e["package_version"]
        for e in entries
        if e["origin"] == "image"
    }
    assert pinned["libpng-devel"] == "2:1.6.34-11.el8_10"
    result = fake_rpm(pinned)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("installed", "message"),
    [
        (
            "0:1.2.11-24.el8",
            "zlib-devel: pinned 0:1.2.11-25.el8, found 0:1.2.11-24.el8",
        ),
        (None, "zlib-devel: pinned 0:1.2.11-25.el8, found not installed"),
    ],
)
def test_rpm_check_refuses_another_version(fake_rpm, installed, message):
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    versions = {
        e["package"]: (
            e["package_version"]
            if ":" in e["package_version"]
            else "0:" + e["package_version"]
        )
        for e in entries
        if e["origin"] == "image"
    }
    versions["zlib-devel"] = installed
    result = fake_rpm(versions)
    assert result.returncode == 1
    assert result.stderr == f"deps.py: {message}\n"


# scripts/build.py (roadmap stage 5) -------------------------------------------
#
# The text edits and names of the repair step, on inputs shaped like what
# R 4.6.2's `make install` writes. The wheel itself is checked by
# tests/test_build.py and tests/test_installed.py in CI.


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_module("zur_build", REPO / "scripts/build.py")
launcher = load_module("zur_launcher", REPO / "src/zur/__init__.py")

# The head of src/scripts/R.sh.in as `make install` writes it for
# --prefix=/opt/zur with LIBnn=lib.
FRONT_END = """\
#!/bin/sh
# Shell wrapper for R executable.

R_HOME_DIR=/opt/zur/lib/R
if test "${R_HOME_DIR}" = "/opt/zur/lib/R"; then
   case "linux-gnu" in
   linux*)
     if [ -x "/opt/zur/${libnn}/R/bin/exec/R" ]; then
        R_HOME_DIR="/opt/zur/${libnn}/R"
     fi
     ;;
  esac
fi

if test -n "${R_HOME}" && \\
   test "${R_HOME}" != "${R_HOME_DIR}"; then
  echo "WARNING: ignoring environment value of R_HOME"
fi
R_HOME="${R_HOME_DIR}"
export R_HOME
R_SHARE_DIR=/opt/zur/lib/R/share
export R_SHARE_DIR
exec "${R_HOME}/bin/exec/R" "$@"
"""


def front_end(tmp_path, where):
    """bin/R relocated, installed at ``where`` under a fresh prefix."""
    path = tmp_path / "runtime" / where
    path.parent.mkdir(parents=True)
    (tmp_path / "runtime/lib/R").mkdir(parents=True, exist_ok=True)
    path.write_text(FRONT_END)
    build.relocate_front_end(
        path, "/opt/zur", ".." if where == "lib/R/bin/R" else "../lib/R"
    )
    return path


def sh(script, *args, env=None):
    return subprocess.run(
        ["sh", "-c", script, "sh", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# What bin/R exports, printed instead of starting R.
PRINT_ENV = 'echo "$R_HOME|$RHOME|$R_SHARE_DIR|$CURL_CA_BUNDLE|$SSL_CERT_FILE"'


@pytest.mark.parametrize("where", ["lib/R/bin/R", "bin/R"])
def test_front_end_finds_r_home_from_its_own_location(tmp_path, where):
    path = front_end(tmp_path, where)
    text = path.read_text().replace('exec "${R_HOME}/bin/exec/R" "$@"', PRINT_ENV)
    path.write_text(text)
    assert "/opt/zur" not in text
    # Moved after relocation, and started by a relative path from elsewhere.
    moved = tmp_path / "moved"
    (tmp_path / "runtime").rename(moved)
    home = (moved / "lib/R").resolve()
    result = sh(
        f'cd "{tmp_path}" && sh "moved/{where}"',
        env={"PATH": os.environ["PATH"], "CURL_CA_BUNDLE": "/x.pem"},
    )
    assert result.returncode == 0, result.stderr
    r_home, rhome, share, curl, ssl = result.stdout.strip().split("|")
    assert (r_home, rhome, share) == (str(home), str(home), f"{home}/share")
    assert curl == "/x.pem"  # the user's value wins
    if Path("/etc/ssl/certs/ca-certificates.crt").exists():
        assert ssl == "/etc/ssl/certs/ca-certificates.crt"


def test_front_end_without_its_expected_lines_is_refused(tmp_path):
    path = tmp_path / "R"
    path.write_text(FRONT_END.replace("R_HOME_DIR=/opt/zur/lib/R\n", ""))
    with pytest.raises(SystemExit, match="no R_HOME_DIR=/opt/zur/lib/R line"):
        build.relocate_front_end(path, "/opt/zur", "..")


MAKECONF = """\
# etc/Makeconf.  Generated from Makeconf.in by configure.
# configure  '--prefix=/opt/zur' 'CPPFLAGS=-I/opt/zur-deps/include' \
'LDFLAGS=-L/opt/zur-deps/lib'
R_HOME=/opt/zur/lib/R
CC = gcc -std=gnu2x
CPPFLAGS = -I/opt/zur-deps/include
CXX = /opt/rh/gcc-toolset-14/root/usr/bin/g++ -std=gnu++20
FC = gfortran
LDFLAGS = -L/opt/zur-deps/lib
SHLIB_OPENMP_CFLAGS =
SHLIB_OPENMP_CXXFLAGS =
SHLIB_OPENMP_FCFLAGS =
SHLIB_OPENMP_FFLAGS =
LIBR = -L"$(R_HOME)/lib" -lR
rincludedir = /opt/zur/lib/R/include
"""


def test_makeconf_relocated(tmp_path):
    path = tmp_path / "Makeconf"
    path.write_text(MAKECONF)
    build.relocate_makeconf(path, "/opt/zur", "/opt/zur-deps")
    text = path.read_text()
    assert "/opt/zur" not in text
    assert "\nR_HOME=" not in text
    assert "\nCC = gcc -std=gnu2x\n" in text
    assert "\nCXX = g++ -std=gnu++20\n" in text
    assert "\nCPPFLAGS =\n" in text and "\nLDFLAGS =\n" in text
    for lang in ["C", "CXX", "FC", "F"]:
        assert f"\nSHLIB_OPENMP_{lang}FLAGS = -fopenmp\n" in text
    assert "\nrincludedir = $(R_HOME)/../../lib/R/include\n" in text
    assert "'CPPFLAGS=-I<deps>/include'" in text and "'--prefix=<prefix>'" in text


def test_makeconf_with_another_compiler_is_refused(tmp_path):
    path = tmp_path / "Makeconf"
    path.write_text(MAKECONF.replace("FC = gfortran", "FC = flang"))
    with pytest.raises(SystemExit, match="FC is flang, expected gfortran"):
        build.relocate_makeconf(path, "/opt/zur", "/opt/zur-deps")


def test_ldpaths_loses_the_built_dependencies(tmp_path):
    path = tmp_path / "ldpaths"
    path.write_text(
        'if test -n "/opt/zur-deps/lib"; then\n'
        ": ${R_LD_LIBRARY_PATH=${R_HOME}/lib:/opt/zur-deps/lib}\n"
        "else\n: ${R_LD_LIBRARY_PATH=${R_HOME}/lib}\nfi\n"
    )
    build.relocate_ldpaths(path, "/opt/zur", "/opt/zur-deps")
    assert path.read_text() == (
        'if test -n ""; then\n'
        ": ${R_LD_LIBRARY_PATH=${R_HOME}/lib}\n"
        "else\n: ${R_LD_LIBRARY_PATH=${R_HOME}/lib}\nfi\n"
    )


def test_graft_name_is_hashed_like_auditwheel():
    data = b"library bytes"
    h = hashlib.sha256(data).hexdigest()[:8]
    assert build.graft_name("libgfortran.so.5", data) == f"libgfortran-{h}.so.5"
    assert build.graft_name("libz.so.1.2.11", data) == f"libz-{h}.so.1.2.11"
    with pytest.raises(SystemExit, match="not a shared library name"):
        build.graft_name("libz.a", data)


def test_scrub_keeps_length_and_leaves_no_prefix(tmp_path):
    rscript = tmp_path / "zur/_runtime/lib/R/bin/Rscript"
    rscript.parent.mkdir(parents=True)
    data = tiny_elf(["libc.so.6"]) + b"/opt/zur/lib/R\0/opt/zur-deps/lib/ossl\0"
    rscript.write_bytes(data)
    text = tmp_path / "zur/_runtime/notes.txt"
    text.write_text("/opt/zur\n")
    changed = build.scrub(tmp_path, ["/opt/zur", "/opt/zur-deps"])
    assert changed == ["zur/_runtime/lib/R/bin/Rscript"]
    new = rscript.read_bytes()
    assert len(new) == len(data)
    assert new.endswith(b"/proc/0//lib/R\0/proc/0///////lib/ossl\0")
    # Text files are relocated, never scrubbed: the final check names them.
    assert build.build_paths(tmp_path, ["/opt/zur"]) == ["zur/_runtime/notes.txt"]


def test_placeholder_needs_room():
    assert build.placeholder("/opt/zur") == "/proc/0/"
    with pytest.raises(SystemExit, match="too short"):
        build.placeholder("/zur")


def test_launcher_sets_only_r_home_and_rhome():
    base = {"R_HOME": "/other/R", "PATH": "/bin", "CURL_CA_BUNDLE": "/x"}
    env = launcher.environment(base)
    assert env == {
        "PATH": "/bin",
        "CURL_CA_BUNDLE": "/x",
        "RHOME": str(launcher.R_HOME),
    }
