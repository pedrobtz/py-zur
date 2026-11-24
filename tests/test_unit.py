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
import shutil
import struct
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path
from xml.etree import ElementTree

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


def test_wheel_metadata_names_no_fixed_licence():
    # pack() writes the wheel's METADATA. Since stage 13 its licence is
    # the generated License-Expression (design §14, D21), never a fixed
    # string in the build script, and the deprecated License field is gone.
    source = (REPO / "scripts/build.py").read_text()
    assert re.findall(r"License(?:-Expression)?: ([^\\\n\"{]+)", source) == []


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
        license_files = ["COPYING"]
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
            srpm = { nvr = "zlib-1.2.11-25.el8", url = "URL", sha256 = "DIGEST" }
            license = "Zlib"
            license_files = ["/usr/share/licenses/zlib/README"]
            sonames = ["libz.so.1"]
            why = "test"
            """)
        .replace("DIGEST", "d" * 64)
        .replace("URL", "https://example.org/zlib-1.2.11-25.el8.src.rpm")
    )
    (tmp_path / "r-source").write_text(
        "# comment\nR_VERSION=4.6.2\nR_URL=https://example.org/R.tar.gz\n"
        f"R_SHA256={'b' * 64}\nR_LICENSE=GPL-2.0-or-later\n"
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
                "license": "GPL-2.0-or-later",
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
                    "license_files": ["COPYING"],
                    "sonames": ["libcurl.so.9"],
                    "why": "test",
                    "source": {
                        "kind": "tarball",
                        "url": "https://example.org/curl-9.9.tar.gz",
                        "sha256": "a" * 64,
                    },
                },
                {
                    "name": "zlib",
                    "version": "1.2.11",
                    "origin": "image",
                    "package": "zlib-devel",
                    "package_version": "1.2.11-25.el8",
                    "srpm": {
                        "nvr": "zlib-1.2.11-25.el8",
                        "url": "https://example.org/zlib-1.2.11-25.el8.src.rpm",
                        "sha256": "d" * 64,
                    },
                    "license": "Zlib",
                    "license_files": ["/usr/share/licenses/zlib/README"],
                    "sonames": ["libz.so.1"],
                    "why": "test",
                    "source": {
                        "kind": "srpm",
                        "name": "zlib-1.2.11-25.el8",
                        "url": "https://example.org/zlib-1.2.11-25.el8.src.rpm",
                        "sha256": "d" * 64,
                    },
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
    # Valid, libcurl and OpenSSL built from source (§16 question 2) and
    # fontconfig for <dir prefix="relative"> (design §6.4), and
    # each source URL names the version the entry claims.
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    assert deps_py("check").returncode == 0
    sources = {e["name"]: e for e in entries if e["origin"] == "source"}
    assert sorted(sources) == ["curl", "fontconfig", "openssl"]
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


def installed_as_pinned():
    """What `rpm -q --qf '%{EPOCHNUM}:%{VERSION}-%{RELEASE} %{SOURCERPM}'`
    prints for each "image" package of the committed manifest."""
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    return {
        e["package"]: (
            e["package_version"]
            if ":" in e["package_version"]
            else "0:" + e["package_version"]
        )
        + f" {e['srpm']['nvr']}.src.rpm"
        for e in entries
        if e["origin"] == "image"
    }


def test_rpm_check_accepts_the_pinned_versions(fake_rpm):
    pinned = installed_as_pinned()
    assert (
        pinned["libpng-devel"] == "2:1.6.34-11.el8_10 libpng-1.6.34-11.el8_10.src.rpm"
    )
    result = fake_rpm(pinned)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("installed", "message"),
    [
        (
            "0:1.2.11-24.el8 zlib-1.2.11-24.el8.src.rpm",
            "zlib-devel: pinned 0:1.2.11-25.el8, found 0:1.2.11-24.el8",
        ),
        (None, "zlib-devel: pinned 0:1.2.11-25.el8, found not installed"),
        # Stage 13: the pinned version, rebuilt from another source RPM.
        (
            "0:1.2.11-25.el8 zlib-1.2.11-25.el8.alma.1.src.rpm",
            "zlib-devel: built from zlib-1.2.11-25.el8.alma.1.src.rpm, "
            "pinned zlib-1.2.11-25.el8.src.rpm",
        ),
    ],
)
def test_rpm_check_refuses_another_version(fake_rpm, installed, message):
    versions = installed_as_pinned()
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
        path, "/opt/zur", ".." if where == "lib/R/bin/R" else "../lib/R", "4.6.2"
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
PRINT_ENV = (
    'echo "$R_HOME|${RHOME-unset}|$R_SHARE_DIR|$CURL_CA_BUNDLE|$SSL_CERT_FILE'
    '|$FONTCONFIG_FILE"'
)


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
    r_home, rhome, share, curl, ssl, fonts = result.stdout.strip().split("|")
    assert (r_home, share) == (str(home), f"{home}/share")
    # bin/Rscript, not bin/R, sets RHOME (design §5).
    assert rhome == "unset"
    assert fonts == f"{home}/etc/fonts/fonts.conf"
    assert curl == "/x.pem"  # the user's value wins
    if Path("/etc/ssl/certs/ca-certificates.crt").exists():
        assert ssl == "/etc/ssl/certs/ca-certificates.crt"


def test_front_end_keeps_the_users_fontconfig_file(tmp_path):
    path = front_end(tmp_path, "bin/R")
    path.write_text(
        path.read_text().replace('exec "${R_HOME}/bin/exec/R" "$@"', PRINT_ENV)
    )
    env = {"PATH": os.environ["PATH"], "FONTCONFIG_FILE": "/my/fonts.conf"}
    result = sh(f'sh "{path}"', env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split("|")[-1] == "/my/fonts.conf"


# User library (design §5, D10) -------------------------------------------------

# /etc/os-release of the quick profile's containers, as the images ship
# them (fields bin/R does not read trimmed), and the edge cases.
UBUNTU = """\
PRETTY_NAME="Ubuntu 24.04.3 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.3 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
"""
DEBIAN = """\
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
"""
ROCKY = """\
NAME="Rocky Linux"
VERSION="8.10 (Green Obsidian)"
ID="rocky"
ID_LIKE="rhel centos fedora"
VERSION_ID="8.10"
PLATFORM_ID="platform:el8"
PRETTY_NAME="Rocky Linux 8.10 (Green Obsidian)"
REDHAT_SUPPORT_PRODUCT_VERSION="8.10"
"""
OS_RELEASES = {
    "ubuntu": (UBUNTU, "x86_64-ubuntu-24.04"),
    "debian": (DEBIAN, "x86_64-debian-12"),
    "rocky": (ROCKY, "x86_64-rocky-8.10"),
    # The last line without its newline is still read.
    "no final newline": ("ID=alpine\nVERSION_ID=3.20.3", "x86_64-alpine-3.20.3"),
    "single quotes": ("ID='alpine'\nVERSION_ID='3.20.3'\n", "x86_64-alpine-3.20.3"),
    # Rolling releases have no VERSION_ID: it is left out.
    "no VERSION_ID": ('ID=arch\nBUILD_ID=rolling\nNAME="Arch Linux"\n', "x86_64-arch"),
    # The specification's default ID is "linux".
    "no ID": ('NAME="Some Linux"\nVERSION_ID=1.0\n', "x86_64-linux-1.0"),
    # Outside [A-Za-z0-9._-] a value counts as missing, so no path escapes.
    "bad values": ('ID="../../tmp"\nVERSION_ID="1 2"\n', "x86_64-linux"),
    "no file": (None, "x86_64-linux"),
}
SHELLS = [s for s in ("sh", "dash", "bash") if shutil.which(s)]
PRINT_LIBS = 'echo "${R_LIBS_USER-unset}"'


def library_front_end(tmp_path, os_release):
    """bin/R reading ``os_release`` (text, or None for no file)."""
    path = front_end(tmp_path, "bin/R")
    release = tmp_path / "os-release"
    if os_release is not None:
        release.write_text(os_release)
    text = path.read_text()
    assert text.count("/etc/os-release") == 3  # the comment, test -r, done <
    path.write_text(
        text.replace("/etc/os-release", str(release)).replace(
            'exec "${R_HOME}/bin/exec/R" "$@"', PRINT_LIBS
        )
    )
    return path


def libs(shell, path, **env):
    result = subprocess.run(
        [shell, str(path)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **env},
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", OS_RELEASES)
def test_front_end_user_library_names_the_distribution(tmp_path, case, shell):
    os_release, platform = OS_RELEASES[case]
    path = library_front_end(tmp_path, os_release)
    home = tmp_path / "home"
    home.mkdir()
    library = home / f"R/zur/{platform}/4.6"
    assert libs(shell, path, HOME=str(home)) == str(library)
    assert library.is_dir()
    # The second start finds it and changes nothing.
    assert libs(shell, path, HOME=f"{home}/") == str(library)


@pytest.mark.parametrize("case", ["ubuntu", "debian", "rocky"])
def test_user_library_platform_matches_sourcing_os_release(tmp_path, case):
    # A second reader: os-release is a shell fragment, so sourcing it is
    # the specification's own reading; bin/R reads it without running it.
    os_release, platform = OS_RELEASES[case]
    path = library_front_end(tmp_path, os_release)
    sourced = subprocess.run(
        ["sh", "-c", '. "$1" && printf "%s-%s" "$ID" "$VERSION_ID"', "sh",
         str(tmp_path / "os-release")],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert platform == f"x86_64-{sourced}"
    assert libs("sh", path, HOME=str(tmp_path)).split("/")[-2] == platform


@pytest.mark.parametrize("value", ["/my/lib", "/my/lib:/other/lib"])
def test_front_end_keeps_the_users_library(tmp_path, value):
    path = library_front_end(tmp_path, UBUNTU)
    assert libs("sh", path, HOME=str(tmp_path), R_LIBS_USER=value) == value
    assert not (tmp_path / "R").exists()


def test_front_end_treats_an_empty_library_as_unset(tmp_path):
    # As etc/Renviron's ${R_LIBS_USER:-...} does.
    path = library_front_end(tmp_path, UBUNTU)
    out = libs("sh", path, HOME=str(tmp_path), R_LIBS_USER="")
    assert out == f"{tmp_path}/R/zur/x86_64-ubuntu-24.04/4.6"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only home")
@pytest.mark.parametrize("shell", SHELLS)
def test_front_end_starts_with_a_read_only_home(tmp_path, shell):
    path = library_front_end(tmp_path, UBUNTU)
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o555)
    try:
        out = libs(shell, path, HOME=str(home))  # exit 0, empty stderr
    finally:
        home.chmod(0o755)
    assert out == f"{home}/R/zur/x86_64-ubuntu-24.04/4.6"
    assert list(home.iterdir()) == []


def test_front_end_without_home_leaves_the_library_to_r(tmp_path):
    path = library_front_end(tmp_path, UBUNTU)
    assert libs("sh", path) == "unset"
    assert libs("sh", path, HOME="") == "unset"


def test_user_library_arch_and_minor_version():
    assert build.ARCH == "x86_64"
    text = build.front_end_zur("4.6.2")
    assert '/R/zur/x86_64-${zur_id}${zur_version_id}/4.6"' in text
    assert "@" not in text.replace("${@}", "")
    with pytest.raises(SystemExit, match="no major.minor"):
        build.front_end_zur("4")


RENVIRON = """\
R_PAPERSIZE_USER=${R_PAPERSIZE}
R_LIBS_USER=${R_LIBS_USER:-'%U'}
R_LIBS_SITE=${R_LIBS_SITE:-'%S'}
EDITOR=${EDITOR-${VISUAL-vi}}
R_BROWSER=${R_BROWSER-'/opt/zur/bin/xdg-open'}
"""


def test_renviron_keeps_the_users_library(tmp_path):
    path = tmp_path / "Renviron"
    path.write_text(RENVIRON)
    build.relocate_renviron(path, "/opt/zur")
    text = path.read_text()
    assert "\nR_LIBS_USER=${R_LIBS_USER:-'%U'}\n" in text
    assert "/opt/zur" not in text


def test_renviron_with_another_library_line_is_refused(tmp_path):
    path = tmp_path / "Renviron"
    path.write_text(RENVIRON.replace(":-'%U'", "-'%U'"))
    with pytest.raises(SystemExit, match="R_LIBS_USER line changed upstream"):
        build.relocate_renviron(path, "/opt/zur")


# What the compiled Rscript would see, printed instead of starting R.
FAKE_RSCRIPT = """\
#!/bin/sh
echo "$0|$RHOME|${R_HOME-unset}"
for a in "$@"; do echo "$a"; done
"""


@pytest.fixture
def rscript_runtime(tmp_path):
    """A runtime after wrap_rscript, its compiled Rscript a fake that echoes."""
    runtime = tmp_path / "runtime"
    for d in ["bin", "lib/R/bin/exec"]:
        (runtime / d).mkdir(parents=True)
    for p in [runtime / "bin/Rscript", runtime / "lib/R/bin/Rscript"]:
        p.write_bytes(tiny_elf(["libc.so.6"]))
    build.wrap_rscript(runtime)
    fake = runtime / "lib/R/libexec/Rscript"
    assert fake.read_bytes()[:4] == b"\x7fELF"  # the binary moved there
    assert list((runtime / "lib/R/bin/exec").iterdir()) == []  # not there
    fake.write_text(FAKE_RSCRIPT)
    fake.chmod(0o755)
    return runtime


@pytest.mark.parametrize("where", ["lib/R/bin/Rscript", "bin/Rscript"])
def test_rscript_wrapper_sets_rhome_from_its_location(rscript_runtime, where):
    # Moved, then started by a relative path from elsewhere, with another
    # R's RHOME and R_HOME inherited: both are overridden, never honoured.
    moved = rscript_runtime.parent / "moved dir"
    rscript_runtime.rename(moved)
    home = (moved / "lib/R").resolve()
    env = {"PATH": os.environ["PATH"], "RHOME": "/other/R", "R_HOME": "/other/R"}
    result = subprocess.run(
        [f"./moved dir/{where}", "-e", "cat(1)", "a b"],
        cwd=moved.parent,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    head, *args = result.stdout.splitlines()
    assert head == f"{home}/libexec/Rscript|{home}|unset"
    assert args == ["-e", "cat(1)", "a b"]


def test_rscript_wrappers_are_executable_scripts(rscript_runtime):
    for where in ["lib/R/bin/Rscript", "bin/Rscript"]:
        path = rscript_runtime / where
        assert path.read_text().startswith("#!/bin/sh\n")
        assert path.stat().st_mode & 0o777 == 0o755


def test_rscript_wrapper_needs_the_compiled_rscript(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "lib/R/bin/exec").mkdir(parents=True)
    (runtime / "lib/R/bin/Rscript").write_text("#!/bin/sh\n")
    with pytest.raises(SystemExit, match="not the compiled Rscript"):
        build.wrap_rscript(runtime)


def test_fonts_ship_the_family_and_a_relative_fontconfig_file(tmp_path):
    source = tmp_path / "liberation-sans"
    source.mkdir()
    for name in build.FONT_FILES:
        (source / name).write_bytes(b"\x00\x01\x00\x00" + name.encode())
    runtime = tmp_path / "runtime"
    build.fonts(runtime, source)
    home = runtime / "lib/R"
    shipped = sorted(p.name for p in (home / build.FONTS_DIR).iterdir())
    assert shipped == sorted(build.FONT_FILES)
    for name in build.FONT_FILES:
        assert (home / build.FONTS_DIR / name).read_bytes() == (
            source / name
        ).read_bytes()
    conf = home / build.FONTS_CONF
    assert conf.relative_to(home).as_posix() == "etc/fonts/fonts.conf"
    root = ElementTree.parse(conf).getroot()
    assert root.tag == "fontconfig"
    dirs = root.findall("dir")
    # The shipped fonts first, found relative to the file, then the system's.
    assert dirs[0].get("prefix") == "relative"
    assert (conf.parent / dirs[0].text).resolve() == (home / build.FONTS_DIR)
    assert [d.text for d in dirs[1:3]] == ["/usr/share/fonts", "/usr/local/share/fonts"]
    family = root.find("match/edit[@name='family']")
    assert family.get("mode") == "append_last"
    assert family.find("string").text == "Liberation Sans"
    # Never the system's configuration (a newer fontconfig's elements).
    assert root.find("include") is None


def test_rprofile_sets_cairo_bitmaps(tmp_path):
    path = tmp_path / "Rprofile"
    path.write_text("local({ x <- 1 })\n")
    build.relocate_rprofile(path)
    text = path.read_text()
    assert text.startswith("local({ x <- 1 })\n")
    assert text.rstrip().endswith('options(bitmapType = "cairo")')
    with pytest.raises(SystemExit, match="already sets bitmapType"):
        build.relocate_rprofile(path)


def test_fonts_refuse_a_missing_face(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()
    with pytest.raises(SystemExit, match="LiberationSans-Regular.ttf missing"):
        build.fonts(tmp_path / "runtime", source)


def test_front_end_without_its_expected_lines_is_refused(tmp_path):
    path = tmp_path / "R"
    path.write_text(FRONT_END.replace("R_HOME_DIR=/opt/zur/lib/R\n", ""))
    with pytest.raises(SystemExit, match="no R_HOME_DIR=/opt/zur/lib/R line"):
        build.relocate_front_end(path, "/opt/zur", "..", "4.6.2")


MAKECONF = """\
# etc/Makeconf.  Generated from Makeconf.in by configure.
# configure  '--prefix=/opt/zur' 'CPPFLAGS=-I/opt/zur-deps/include' \
'LDFLAGS=-L/opt/zur-deps/lib'
R_HOME=/opt/zur/lib/R
CC = gcc -std=gnu23
CPPFLAGS = -I/opt/zur-deps/include
CXX = /opt/rh/gcc-toolset-14/root/usr/bin/g++ -std=gnu++20
FC = gfortran
LDFLAGS = -L/opt/zur-deps/lib
SHLIB_OPENMP_CFLAGS =
SHLIB_OPENMP_CXXFLAGS =
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
    assert "\nCC = gcc\n" in text
    assert f"\n{build.CXXSTD_PROBE}\nCXX = g++ -std=$(ZUR_CXXSTD)\n" in text
    assert "\nCPPFLAGS =\n" in text and "\nLDFLAGS =\n" in text
    for lang in ["C", "CXX", "F"]:
        assert f"\nSHLIB_OPENMP_{lang}FLAGS = -fopenmp\n" in text
    assert "\nrincludedir = $(R_HOME)/../../lib/R/include\n" in text
    assert "'CPPFLAGS=-I<deps>/include'" in text and "'--prefix=<prefix>'" in text


def test_makeconf_with_another_compiler_is_refused(tmp_path):
    path = tmp_path / "Makeconf"
    path.write_text(MAKECONF.replace("FC = gfortran", "FC = flang"))
    with pytest.raises(SystemExit, match="FC is flang, expected gfortran"):
        build.relocate_makeconf(path, "/opt/zur", "/opt/zur-deps")


def test_makeconf_without_a_cxx_standard_is_refused(tmp_path):
    path = tmp_path / "Makeconf"
    path.write_text(MAKECONF.replace(" -std=gnu++20", ""))
    with pytest.raises(SystemExit, match="0 CXX lines with a -std flag"):
        build.relocate_makeconf(path, "/opt/zur", "/opt/zur-deps")


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
@pytest.mark.parametrize(
    ("gxx", "expected"), [("exit 0", "gnu++20"), ("exit 1", "gnu++17")]
)
def test_makeconf_cxx_standard_follows_the_hosts_gxx(tmp_path, gxx, expected):
    # A g++ that accepts (exit 0) or rejects (exit 1) every flag, first on
    # PATH; make expands CXX through the probe line.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "g++"
    fake.write_text(f"#!/bin/sh\n{gxx}\n")
    fake.chmod(0o755)
    path = tmp_path / "Makeconf"
    path.write_text(MAKECONF)
    build.relocate_makeconf(path, "/opt/zur", "/opt/zur-deps")
    (tmp_path / "Makefile").write_text("include Makeconf\nshow:\n\t@echo $(CXX)\n")
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    out = subprocess.run(
        ["make", "-s", "-C", str(tmp_path), "show"],
        capture_output=True, text=True, env=env, check=True,
    ).stdout  # fmt: skip
    assert out.strip() == f"g++ -std={expected}"


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


def test_launcher_only_drops_r_home():
    # bin/Rscript sets RHOME, bin/R everything else (design §5, D13); an
    # inherited RHOME is left for bin/Rscript to override.
    base = {"R_HOME": "/other/R", "RHOME": "/other/R", "PATH": "/bin"}
    env = launcher.environment(base)
    assert env == {"PATH": "/bin", "RHOME": "/other/R"}


# Prune and size budget (roadmap stage 9) --------------------------------------

R_HOME = "zur/_runtime/lib/R"

# A stage shaped like R 4.6.2's install (paths as CRAN's macOS R has them),
# split into what design §9 removes and what it keeps.
PRUNED = [
    "doc/manual/R-intro.html",
    "doc/manual/R-intro.pdf",
    "doc/manual/images/QR.png",
    "doc/NEWS.pdf",
    "library/stats/doc/reshape.pdf",
    "library/stats/doc/index.html",
    "library/grid/doc/grid.Rnw",
    "library/base/demo/error.catching.R",
    "library/stats/tests/ppr.R",
    "tests/reg-tests-1a.R",
    "library/translations/DESCRIPTION",
    "library/translations/de/LC_MESSAGES/R.mo",
    "library/utils/po/de/LC_MESSAGES/R-utils.mo",
]
KEPT = [
    "doc/COPYING",
    "doc/NEWS.rds",
    "doc/html/index.html",
    "include/R.h",
    "etc/Makeconf",
    "lib/libR.so",
    "library/stats/help/stats.rdb",
    "library/stats/html/00Index.html",
    "library/stats/R/stats.rdb",
    "library/stats/libs/stats.so",
    "library/grDevices/libs/cairo.so",
    "share/fonts/LiberationSans-Regular.ttf",
    "library/tools/DESCRIPTION",
    # Names like the pruned ones in other places.
    "share/doc/notes.txt",
    "library/stats/R/doc",
    "library/stats/demos.R",
]


def make_stage(root, paths, data=b"x" * 10):
    for rel in paths:
        path = root / R_HOME / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def stage_files(root):
    home = root / R_HOME
    return sorted(
        p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()
    )


def test_prune_removes_design_9_and_keeps_the_rest(tmp_path):
    make_stage(tmp_path, PRUNED + KEPT)
    removed = build.prune(tmp_path)
    assert stage_files(tmp_path) == sorted(KEPT)
    # One row per pattern; together they account for every pruned file.
    assert [r[0] for r in removed] == build.PRUNE
    assert sum(r[1] for r in removed) == len(PRUNED)
    assert sum(r[2] for r in removed) == 10 * len(PRUNED)
    assert not (tmp_path / R_HOME / "library/translations").exists()
    assert not (tmp_path / R_HOME / "doc/manual").exists()


def test_prune_reports_a_pattern_that_matches_nothing(tmp_path):
    make_stage(tmp_path, KEPT)
    removed = build.prune(tmp_path)
    assert stage_files(tmp_path) == sorted(KEPT)
    assert all(files == 0 and size == 0 for _, files, size in removed)


def synthetic_wheel(path, members):
    with build.zipfile.ZipFile(path, "w", build.zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_size_report_sorts_every_member_into_its_part(tmp_path):
    members = {
        f"{R_HOME}/lib/libR.so": b"R" * 4000,
        f"{R_HOME}/lib/libRblas.so": b"B" * 100,
        "zur.libs/libicudata-0123abcd.so.60": b"I" * 3000,
        "zur.libs/libicuuc-0123abcd.so.60": b"U" * 50,
        "zur.libs/libcairo-0123abcd.so.2": b"C" * 60,
        f"{R_HOME}/library/stats/help/stats.rdb": b"H" * 700,
        f"{R_HOME}/library/stats/html/00Index.html": b"h" * 70,
        f"{R_HOME}/library/stats/R/stats.rdb": b"S" * 500,
        f"{R_HOME}/library/stats/libs/stats.so": b"s" * 50,
        f"{R_HOME}/share/fonts/LiberationSans-Regular.ttf": b"F" * 300,
        f"{R_HOME}/etc/Makeconf": b"M" * 30,
        "zur/__init__.py": b"P" * 20,
        "zur-4.6.2.dist-info/RECORD": b"r" * 10,
    }
    wheel = synthetic_wheel(tmp_path / "w.whl", members)
    report = {name: rest for name, *rest in build.size_report(wheel)}
    assert list(report) == [n for n, _ in build.SIZE_PARTS] + [build.ZIP_PART]
    # (files, uncompressed bytes) per part, against the members above.
    expected = {
        "R's libraries": (2, 4100),
        "ICU data": (1, 3000),
        "bundled libraries": (2, 110),
        "help": (2, 770),
        "base packages": (2, 550),
        "fonts": (1, 300),
        "everything else": (3, 60),
        build.ZIP_PART: (0, 0),
    }
    assert {k: (v[0], v[2]) for k, v in report.items()} == expected
    with build.zipfile.ZipFile(wheel) as zf:
        compressed = sum(i.compress_size for i in zf.infolist())
    assert sum(v[1] for v in report.values()) == wheel.stat().st_size
    assert report[build.ZIP_PART][1] == wheel.stat().st_size - compressed
    text = build.format_size_report(wheel, build.size_report(wheel))
    assert text.splitlines()[0] == f"size report: w.whl, {wheel.stat().st_size} bytes"
    assert len(text.splitlines()) == 2 + len(expected)


def test_pypi_limit_is_100_mib_and_the_default():
    # docs.pypi.org's "100.0 MB" is warehouse's 100 * ONE_MIB.
    assert build.PYPI_MAX_FILE_SIZE == 104_857_600
    assert build.arguments([]).max_size == build.PYPI_MAX_FILE_SIZE
    assert build.arguments(["--max-size", "5"]).max_size == 5


@pytest.mark.parametrize("delta", [-1, 0])
def test_wheel_at_or_under_the_limit_passes(tmp_path, delta):
    wheel = synthetic_wheel(tmp_path / "w.whl", {"a": b"a" * 1000})
    size = wheel.stat().st_size
    assert build.check_size(wheel, size - delta) == size


def test_wheel_one_byte_over_the_limit_fails(tmp_path):
    wheel = synthetic_wheel(tmp_path / "w.whl", {"a": b"a" * 1000})
    size = wheel.stat().st_size
    with pytest.raises(SystemExit) as e:
        build.check_size(wheel, size - 1)
    assert str(e.value) == (
        f"w.whl: {size} bytes, over the limit of {size - 1} bytes by 1"
    )


@pytest.mark.parametrize(("extra", "ok"), [(-1, True), (0, True), (1, False)])
def test_pypi_limit_by_default(tmp_path, extra, ok):
    # A sparse file of exactly the limit and its neighbours.
    wheel = tmp_path / "big.whl"
    with wheel.open("wb") as f:
        f.truncate(build.PYPI_MAX_FILE_SIZE + extra)
    if ok:
        assert build.check_size(wheel) == build.PYPI_MAX_FILE_SIZE + extra
    else:
        with pytest.raises(
            SystemExit,
            match=r"104857601 bytes, over the limit of 104857600 bytes by 1$",
        ):
            build.check_size(wheel)


# build-r.sh deps and pango-devel (D14; the measurement of roadmap stage 9)


@pytest.fixture
def fake_dnf(tmp_path):
    """dnf, rpm and python on PATH that log and report pango-devel."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "dnf.log"
    flag = tmp_path / "pango-installed"
    scripts = {
        "dnf": f'echo "$*" >> {log}\n'
        f'case "$*" in *pango-devel*) touch {flag} ;; esac\n',
        "rpm": f'test "$*" = "-q pango-devel" && test -e {flag}\n',
        "python": "exit 0\n",
    }
    for name, body in scripts.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body)
        (bin_dir / name).chmod(0o755)

    def run(pango_installed, **env):
        if pango_installed:
            flag.touch()
        result = run_script(
            "build-r.sh",
            "deps",
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "ZUR_PYTHON": str(bin_dir / "python"),
                **env,
            },
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return run


REFUSAL = "pango-devel is installed; R must be built without pango (D14)\n"


@pytest.mark.parametrize("value", ["", "0", "yes", "true", " 1"])
def test_deps_refuses_pango_devel(fake_dnf, value):
    # Only ZUR_MEASURE_PANGO=1 exactly lets pango-devel in.
    result, calls = fake_dnf(True, ZUR_MEASURE_PANGO=value)
    assert result.returncode == 1
    assert result.stderr == REFUSAL
    assert not any("pango-devel" in c for c in calls)


def test_deps_without_pango_devel_passes(fake_dnf):
    result, calls = fake_dnf(False, ZUR_MEASURE_PANGO="")
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1 and "pango-devel" not in calls[0]


def test_deps_installs_pango_devel_only_for_the_measurement(fake_dnf):
    result, calls = fake_dnf(False, ZUR_MEASURE_PANGO="1")
    assert result.returncode == 0, result.stderr
    assert calls[-1] == "install -y pango-devel"
    assert "for the D14 measurement only" in result.stderr


# zur doctor (roadmap stage 11, design §6.5) ------------------------------------

doctor = load_module("zur_doctor", REPO / "src/zur/doctor.py")

# The CI containers' /etc/os-release (from bin/R's checks above), Fedora
# and Mint as their images ship it, and two distributions of neither family.
DOCTOR_RELEASES = {
    "ubuntu:24.04": UBUNTU,
    "debian:12": DEBIAN,
    "rockylinux:8": ROCKY,
    "fedora:45": 'NAME="Fedora Linux"\nVERSION_ID=45\nID=fedora\n',
    "linuxmint": "ID=linuxmint\nID_LIKE='ubuntu debian'\n",
    "opensuse": 'ID="opensuse-leap"\nID_LIKE="suse opensuse"\nVERSION_ID="15.6"\n',
    "alpine": "ID=alpine\nVERSION_ID=3.20.3\n",
}
FAMILY_OF = {
    "ubuntu:24.04": "debian",
    "debian:12": "debian",
    "rockylinux:8": "rhel",
    "fedora:45": "rhel",
    "linuxmint": "debian",
    "opensuse": None,
    "alpine": None,
}

# The packages that provide each file, from each family's own package
# index: Debian's packages.debian.org (bookworm, trixie) and Ubuntu's
# (noble) list libgomp.so.1 in libgomp1 and libgfortran.so.5 in
# libgfortran5; RHEL 8 and 9's BaseOS lists them in libgomp and
# libgfortran (design §18.4). The tools' packages likewise.
PROVIDERS = {
    "debian": {
        "libgomp.so.1": "libgomp1",
        "libgfortran.so.5": "libgfortran5",
        "gcc": "gcc",
        "g++": "g++",
        "gfortran": "gfortran",
        "make": "make",
    },
    "rhel": {
        "libgomp.so.1": "libgomp",
        "libgfortran.so.5": "libgfortran",
        "gcc": "gcc",
        "g++": "gcc-c++",
        "gfortran": "gcc-gfortran",
        "make": "make",
    },
}


def doctor_release(tmp_path, name):
    path = tmp_path / "os-release"
    path.write_text(DOCTOR_RELEASES[name])
    return doctor.read_os_release(path)


def report(tmp_path, name, runtimes=(), tools=(), root=True):
    """A report on distribution ``name`` with ``runtimes`` and ``tools`` absent."""
    return doctor.check(
        doctor_release(tmp_path, name) if name else {},
        find=lambda lib: lib not in runtimes,
        which=lambda tool: None if tool in tools else f"/usr/bin/{tool}",
        root=root,
    )


def test_doctor_checks_the_files_p3m_binaries_link():
    # Design §6.5, §18 and §18.4: of what P3M binaries link, only these
    # two are neither in the wheel nor on a bare system.
    assert doctor.RUNTIMES == ("libgomp.so.1", "libgfortran.so.5")
    assert doctor.TOOLCHAIN == ("gcc", "g++", "gfortran", "make")


def test_doctor_tables_name_each_familys_packages():
    for fam, providers in PROVIDERS.items():
        assert doctor.FAMILIES[fam]["packages"] == providers


@pytest.mark.parametrize("name", sorted(DOCTOR_RELEASES))
def test_doctor_family_from_os_release(tmp_path, name):
    assert doctor.family(doctor_release(tmp_path, name)) == FAMILY_OF[name]


def test_doctor_without_os_release_is_unrecognised(tmp_path):
    assert doctor.read_os_release(tmp_path / "none") == {}
    assert doctor.family({}) is None


def test_doctor_os_release_override(tmp_path, monkeypatch):
    path = tmp_path / "os-release"
    path.write_text(DOCTOR_RELEASES["opensuse"])
    monkeypatch.setenv("ZUR_OS_RELEASE", str(path))
    assert doctor.read_os_release()["ID"] == "opensuse-leap"
    monkeypatch.setenv("ZUR_OS_RELEASE", "")
    assert doctor.read_os_release() == doctor.read_os_release("/etc/os-release")


def test_doctor_reads_os_release_without_running_it(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        "# comment\nID='debian'\nVERSION_ID=\"12\"\nNAME=$(touch x)\n\nID_LIKE"
    )
    assert doctor.read_os_release(path) == {
        "ID": "debian",
        "VERSION_ID": "12",
        "NAME": "$(touch x)",
    }


@pytest.mark.parametrize(
    ("name", "missing", "expected"),
    [
        (
            "debian:12",
            ["libgomp.so.1", "libgfortran.so.5"],
            "apt-get update && apt-get install -y libgomp1 libgfortran5",
        ),
        (
            "ubuntu:24.04",
            ["libgfortran.so.5"],
            "apt-get update && apt-get install -y libgfortran5",
        ),
        (
            "rockylinux:8",
            ["libgomp.so.1", "libgfortran.so.5"],
            "dnf install -y libgomp libgfortran",
        ),
        ("fedora:45", ["libgomp.so.1"], "dnf install -y libgomp"),
        ("debian:12", [], ""),
        ("opensuse", ["libgomp.so.1"], ""),
    ],
)
def test_doctor_command(tmp_path, name, missing, expected):
    r = report(tmp_path, name, runtimes=missing)
    assert r.command == expected
    assert r.status == (1 if missing else 0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "debian:12",
            "sudo apt-get update && sudo apt-get install -y libgomp1 libgfortran5",
        ),
        ("rockylinux:8", "sudo dnf install -y libgomp libgfortran"),
    ],
)
def test_doctor_command_has_sudo_only_when_not_root(tmp_path, name, expected):
    runtimes = doctor.RUNTIMES
    assert report(tmp_path, name, runtimes, root=False).command == expected
    assert report(tmp_path, name, runtimes, root=True).command == expected.replace(
        "sudo ", ""
    )


def test_doctor_root_is_the_effective_user(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    assert doctor.check({"ID": "debian"}, find=lambda _: False).command.startswith(
        "sudo "
    )
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 0)
    assert doctor.check({"ID": "debian"}, find=lambda _: False).command.startswith(
        "apt-get "
    )


def test_doctor_toolchain_never_changes_the_status(tmp_path):
    r = report(tmp_path, "ubuntu:24.04", tools=doctor.TOOLCHAIN)
    assert r.status == 0 and r.command == ""
    assert r.toolchain_command == (
        "apt-get update && apt-get install -y gcc g++ gfortran make"
    )
    r = report(tmp_path, "rockylinux:8", tools=["g++", "gfortran"])
    assert r.toolchain_command == "dnf install -y gcc-c++ gcc-gfortran"


def test_doctor_report_with_everything_missing(tmp_path):
    r = report(tmp_path, "debian:12", doctor.RUNTIMES, doctor.TOOLCHAIN, root=False)
    assert doctor.format_report(r) == textwrap.dedent(
        """\
        zur doctor: Debian GNU/Linux 12 (bookworm) (Debian family)

        To load binary packages:
          missing  libgomp.so.1      (libgomp1)
          missing  libgfortran.so.5  (libgfortran5)

        To compile source packages (not required):
          absent   gcc       (gcc)
          absent   g++       (g++)
          absent   gfortran  (gfortran)
          absent   make      (make)

        To install what binary packages need, run:
          sudo apt-get update && sudo apt-get install -y libgomp1 libgfortran5
        To compile source packages, run:
          sudo apt-get update && sudo apt-get install -y gcc g++ gfortran make
        """
    )


def test_doctor_report_with_nothing_missing(tmp_path):
    text = doctor.format_report(report(tmp_path, "rockylinux:8"))
    assert text == textwrap.dedent(
        """\
        zur doctor: Rocky Linux 8.10 (Green Obsidian) (RHEL family)

        To load binary packages:
          found    libgomp.so.1
          found    libgfortran.so.5

        To compile source packages (not required):
          found    gcc
          found    g++
          found    gfortran
          found    make
        """
    )
    assert "install" not in text


def test_doctor_report_on_an_unrecognised_distribution(tmp_path):
    r = report(tmp_path, "opensuse", ["libgfortran.so.5"], ["make"])
    assert r.status == 1 and r.command == "" and r.toolchain_command == ""
    assert doctor.format_report(r) == textwrap.dedent(
        """\
        zur doctor: opensuse-leap 15.6 (unrecognised distribution)

        To load binary packages:
          found    libgomp.so.1
          missing  libgfortran.so.5  (Debian: libgfortran5, RHEL: libgfortran)

        To compile source packages (not required):
          found    gcc
          found    g++
          found    gfortran
          absent   make      (Debian: make, RHEL: make)

        No install command for this distribution. On the Debian and
        RHEL families, the packages named above provide these files.
        """
    )


def test_doctor_report_without_os_release(tmp_path):
    r = report(tmp_path, None, ["libgomp.so.1"])
    first = doctor.format_report(r).splitlines()[0]
    assert first == "zur doctor: no os-release (unrecognised distribution)"


def test_doctor_finds_a_library_the_loader_has():
    present = "libc.so.6" if sys.platform == "linux" else "libSystem.B.dylib"
    assert doctor.loadable(present)
    assert not doctor.loadable("libzur-does-not-exist.so.0")
    assert doctor.missing_runtimes(find=lambda lib: lib == "libgomp.so.1") == [
        "libgfortran.so.5"
    ]


@pytest.fixture
def os_release_file(tmp_path):
    def write(name):
        path = tmp_path / f"os-release-{name.replace(':', '-')}"
        path.write_text(DOCTOR_RELEASES[name])
        return path

    return write


@pytest.fixture
def run_doctor(monkeypatch, capsys, os_release_file):
    def run(argv, name="debian:12", missing=()):
        def check():
            return doctor.Report(
                doctor.read_os_release(os_release_file(name)),
                doctor.missing_runtimes(lambda lib: lib not in missing),
                [],
                True,
            )

        monkeypatch.setattr(doctor, "check", check)
        status = doctor.main(argv)
        out, err = capsys.readouterr()
        return status, out, err

    return run


def test_doctor_command_option_prints_the_line_alone(run_doctor):
    status, out, err = run_doctor(["doctor", "--command"], missing=doctor.RUNTIMES)
    assert (status, out, err) == (
        1,
        "apt-get update && apt-get install -y libgomp1 libgfortran5\n",
        "",
    )


def test_doctor_command_option_prints_nothing_when_nothing_is_missing(run_doctor):
    assert run_doctor(["doctor", "--command"]) == (0, "", "")


def test_doctor_command_option_on_an_unrecognised_distribution(run_doctor):
    status, out, err = run_doctor(
        ["doctor", "--command"], name="alpine", missing=["libgomp.so.1"]
    )
    assert (status, out) == (1, "")
    assert "no install command for this distribution" in err


def test_doctor_prints_the_report(run_doctor):
    status, out, err = run_doctor(["doctor"], missing=["libgomp.so.1"])
    assert status == 1 and err == ""
    assert "  missing  libgomp.so.1      (libgomp1)\n" in out


@pytest.mark.parametrize(
    "argv", [[], ["doctor", "--bogus"], ["repair"], ["doctor", "extra"]]
)
def test_zur_refuses_other_arguments(run_doctor, argv):
    with pytest.raises(SystemExit) as exit:
        run_doctor(argv)
    assert exit.value.code == 2


def test_zur_entry_point_exits_with_the_doctors_status(monkeypatch):
    import types

    fake = types.ModuleType("zur.doctor")
    fake.main = lambda: 1
    package = types.ModuleType("zur")
    package.doctor = fake
    monkeypatch.setitem(sys.modules, "zur", package)
    monkeypatch.setitem(sys.modules, "zur.doctor", fake)
    with pytest.raises(SystemExit) as exit:
        launcher.main()
    assert exit.value.code == 1


def test_launcher_does_not_import_the_doctor():
    # R and Rscript start through the launcher; zur doctor's code is read
    # only by the zur command.
    code = (
        "import sys; sys.path.insert(0, 'src'); import zur;"
        " print('zur.doctor' in sys.modules, 'argparse' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out == "False False\n"


def test_build_copies_every_module_of_the_package():
    modules = sorted(p.name for p in (REPO / "src/zur").glob("*.py"))
    assert ["__init__.py", *build.MODULES] == modules


# The RHEL family's package manager (#38) --------------------------------------


def on_path(*programs):
    return lambda name: f"/usr/bin/{name}" if name in programs else None


@pytest.mark.parametrize(
    ("programs", "expected"),
    [
        (("dnf", "microdnf"), "dnf"),
        (("dnf",), "dnf"),
        (("microdnf",), "microdnf"),  # rockylinux:8-minimal (#38)
        ((), "dnf"),
    ],
)
def test_doctor_rhel_command_uses_microdnf_without_dnf_38(tmp_path, programs, expected):
    r = doctor.check(
        doctor_release(tmp_path, "rockylinux:8"),
        find=lambda lib: False,
        which=on_path(*programs),
        root=True,
    )
    assert r.manager == expected
    assert r.command == f"{expected} install -y libgomp libgfortran"
    assert r.toolchain_command == (
        f"{expected} install -y gcc gcc-c++ gcc-gfortran make"
    )


def test_doctor_debian_command_ignores_microdnf_38(tmp_path):
    r = doctor.check(
        doctor_release(tmp_path, "debian:12"),
        find=lambda lib: False,
        which=on_path("microdnf"),
        root=False,
    )
    assert r.command == (
        "sudo apt-get update && sudo apt-get install -y libgomp1 libgfortran5"
    )


# First-run warning (roadmap stage 12, design §6.5) -----------------------------
#
# The launcher's first_run() with a doctor whose check() is fixed; the
# note's text is doctor.first_run_note(). In containers: test_installed.py.


def first_run_doctor(tmp_path, name="debian:12", runtimes=doctor.RUNTIMES, **kw):
    """A stand-in for zur.doctor whose check() counts its calls."""
    import types

    calls = []

    def check():
        calls.append(1)
        if kw.get("fail"):
            raise RuntimeError("broken check")
        return report(tmp_path, name, runtimes, root=kw.get("root", True))

    fake = types.SimpleNamespace(
        check=check, first_run_note=doctor.first_run_note, calls=calls
    )
    return fake


def first_run(env, fake, platform="linux"):
    stream = io.StringIO()
    launcher.first_run(env, doctor=fake, stream=stream, platform=platform)
    return stream.getvalue()


DEBIAN_NOTE = (
    "zur: binary R packages need libraries this machine lacks:"
    " libgomp.so.1, libgfortran.so.5\n"
    "zur: to install them, run:"
    " apt-get update && apt-get install -y libgomp1 libgfortran5\n"
    "zur: run zur doctor for details; this note is printed once"
    " (ZUR_NO_CHECK=1 turns it off)\n"
)


def test_first_run_stamp_path():
    path = launcher.stamp_path
    assert path({"HOME": "/home/u"}, "4.6.2") == "/home/u/R/zur/first-run-4.6.2"
    assert path({"HOME": "/home/u/"}, "4.6.2") == "/home/u/R/zur/first-run-4.6.2"
    assert path({"HOME": "/"}, "4.6.2") == "/R/zur/first-run-4.6.2"
    assert path({"HOME": ""}, "4.6.2") is None
    assert path({}, "4.6.2") is None
    # The build replaces the version placeholder (scripts/build.py).
    assert path({"HOME": "/h"}) == f"/h/R/zur/first-run-{launcher.__version__}"


def test_first_run_prints_the_note_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    fake = first_run_doctor(tmp_path)
    env = {"HOME": str(home)}
    assert first_run(env, fake) == DEBIAN_NOTE
    stamp = Path(launcher.stamp_path(env))
    assert stamp.parent == home / "R/zur"
    assert stamp.read_text() == "libgomp.so.1\nlibgfortran.so.5\n"
    assert first_run(env, fake) == ""
    assert len(fake.calls) == 1


def test_first_run_note_for_one_missing_library_as_a_user(tmp_path):
    fake = first_run_doctor(
        tmp_path, "rockylinux:8", runtimes=["libgfortran.so.5"], root=False
    )
    lines = first_run({"HOME": str(tmp_path)}, fake).splitlines()
    assert lines[:2] == [
        "zur: binary R packages need libraries this machine lacks: libgfortran.so.5",
        "zur: to install them, run: sudo dnf install -y libgfortran",
    ]


def test_first_run_note_on_an_unrecognised_distribution(tmp_path):
    fake = first_run_doctor(tmp_path, "alpine", runtimes=["libgomp.so.1"])
    assert first_run({"HOME": str(tmp_path)}, fake).splitlines()[1] == (
        "zur: no install command for this distribution;"
        " zur doctor names the packages that provide them"
    )


def test_first_run_with_nothing_missing_is_silent_and_stamped(tmp_path):
    fake = first_run_doctor(tmp_path, runtimes=())
    env = {"HOME": str(tmp_path)}
    assert first_run(env, fake) == ""
    assert Path(launcher.stamp_path(env)).read_text() == ""
    assert first_run(env, fake) == "" and len(fake.calls) == 1


@pytest.mark.parametrize("value", ["1", "yes", "0"])
def test_first_run_disabled_by_zur_no_check(tmp_path, value):
    fake = first_run_doctor(tmp_path)
    env = {"HOME": str(tmp_path), "ZUR_NO_CHECK": value}
    assert first_run(env, fake) == ""
    assert fake.calls == [] and not (tmp_path / "R").exists()
    # Empty counts as unset.
    assert first_run({**env, "ZUR_NO_CHECK": ""}, fake) == DEBIAN_NOTE


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes a read-only directory")
def test_first_run_with_a_read_only_home_is_silent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o555)
    fake = first_run_doctor(tmp_path)
    try:
        assert first_run({"HOME": str(home)}, fake) == ""
    finally:
        home.chmod(0o755)
    assert fake.calls == [] and list(home.iterdir()) == []


def test_first_run_with_home_a_file_or_unset_is_silent(tmp_path):
    home = tmp_path / "file"
    home.write_text("")
    fake = first_run_doctor(tmp_path)
    assert first_run({"HOME": str(home)}, fake) == ""
    assert first_run({}, fake) == "" and first_run({"HOME": ""}, fake) == ""
    assert fake.calls == []


def test_first_run_stamp_of_another_version_does_not_suppress_it(tmp_path):
    env = {"HOME": str(tmp_path)}
    other = tmp_path / "R/zur/first-run-4.6.1"
    other.parent.mkdir(parents=True)
    other.write_text("")
    fake = first_run_doctor(tmp_path)
    assert first_run(env, fake) == DEBIAN_NOTE
    names = sorted(p.name for p in other.parent.iterdir())
    assert names == sorted(["first-run-4.6.1", f"first-run-{launcher.__version__}"])


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_first_run_only_on_linux(tmp_path, platform):
    fake = first_run_doctor(tmp_path)
    assert first_run({"HOME": str(tmp_path)}, fake, platform=platform) == ""
    assert fake.calls == [] and list(tmp_path.iterdir()) == []


def test_first_run_survives_a_failing_check(tmp_path):
    fake = first_run_doctor(tmp_path, fail=True)
    env = {"HOME": str(tmp_path)}
    assert first_run(env, fake) == ""
    # Stamped anyway: the check ran, whatever its result.
    assert Path(launcher.stamp_path(env)).exists()


def test_launcher_checks_before_starting_r(monkeypatch):
    events = []
    monkeypatch.setattr(
        launcher, "first_run", lambda env: events.append(("check", env))
    )
    monkeypatch.setattr(
        launcher.os, "execve", lambda exe, argv, env: events.append(("exec", env))
    )
    monkeypatch.setattr(launcher.sys, "argv", ["Rscript", "-e", "1"])
    monkeypatch.setenv("R_HOME", "/other/R")
    launcher.Rscript()
    assert [e[0] for e in events] == ["check", "exec"]
    assert events[0][1] is events[1][1] and "R_HOME" not in events[1][1]


# Licences and build info (roadmap stage 13, design §14) ----------------------
#
# The manifest's licence fields, the licence files deps.py collects, the
# provenance of every graft, the generated License-Expression and the
# METADATA that names the licence files. The wheel itself: test_build.py.

deps = load_module("zur_deps", REPO / "scripts/deps.py")

# The committed manifest's License-Expression, written out by hand from
# the manifest's licences in file order, R's first, each term once.
LINUX_EXPRESSION = (
    "GPL-2.0-or-later AND Apache-2.0 AND curl AND HPND-sell-variant"
    " AND MIT-Modern-Variant AND MIT AND Unicode-DFS-2016"
    " AND LicenseRef-public-domain AND Zlib AND bzip2-1.0.6 AND BSD-3-Clause"
    " AND ICU AND NAIST-2003 AND GPL-3.0-or-later"
    " AND (LGPL-2.1-only OR MPL-1.1) AND (FTL OR GPL-2.0-or-later)"
    " AND Libpng AND IJG AND libtiff"
    " AND GPL-3.0-or-later WITH GCC-exception-3.1 AND LGPL-2.1-or-later"
    " AND X11-distribute-modifications-variant AND X11 AND MIT-open-group"
    " AND HPND AND HPND-doc-sell AND HPND-doc AND OFL-1.1-RFN"
)


def image_entry(name="zlib", **fields):
    """An "image" entry as TOML, with ``fields`` replacing or adding lines
    (a value of None drops the line)."""
    lines = {
        "name": f'"{name}"',
        "version": '"1.2.11"',
        "origin": '"image"',
        "package": f'"{name}-devel"',
        "package_version": '"1.2.11-25.el8"',
        "srpm": f'{{ nvr = "{name}-1.2.11-25.el8", url = '
        f'"https://example.org/{name}-1.2.11-25.el8.src.rpm", '
        f'sha256 = "{"d" * 64}" }}',
        "license": '"Zlib"',
        "license_files": f'["/usr/share/licenses/{name}/README"]',
        "sonames": f'["lib{name}.so.1"]',
        "why": '"test"',
    }
    lines.update(fields)
    body = "".join(f"{k} = {v}\n" for k, v in lines.items() if v is not None)
    return "[[dependency]]\n" + body


def check_manifest(tmp_path, *entries):
    (tmp_path / "linux.toml").write_text("\n".join(entries))
    return deps_py("check", env={"ZUR_MANIFEST": str(tmp_path / "linux.toml")})


def manifest_entries(tmp_path, *entries):
    (tmp_path / "linux.toml").write_text("\n".join(entries))
    return deps.load(tmp_path / "linux.toml")


def test_committed_manifest_pins_every_source_rpm():
    # Every "image" entry names the source RPM its package is built from,
    # in AlmaLinux 8.10's archive (stage 14 mirrors them), and has a
    # licence file; the transitive ones say through which entry they come.
    with MANIFEST.open("rb") as f:
        entries = tomllib.load(f)["dependency"]
    for e in entries:
        assert e["license_files"], e["name"]
        if e["origin"] == "image":
            assert e["srpm"]["url"] == (
                "https://vault.almalinux.org/8.10/"
                + e["srpm"]["url"].split("/")[4]
                + f"/Source/Packages/{e['srpm']['nvr']}.src.rpm"
            ), e["name"]
    via = {e["name"]: e["via"] for e in entries if "via" in e}
    assert via == {
        "libgfortran": "r",
        "libquadmath": "libgfortran",
        "ncurses": "readline",
        "expat": "fontconfig",
        "jbigkit": "libtiff",
        "pixman": "cairo",
        "libX11": "cairo",
        "libXext": "cairo",
        "libXrender": "cairo",
        "libxcb": "cairo",
        "libXau": "libxcb",
    }


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (None, "zlib: missing license_files"),
        ("[]", "zlib: no licence files"),
        ('["README"]', "zlib: licence file 'README' is not a path its origin takes"),
        (
            '["/a/COPYING", "/b/COPYING"]',
            "zlib: two licence files share a name",
        ),
    ],
)
def test_entry_without_a_licence_file_is_refused(tmp_path, line, message):
    # Roadmap stage 13: a manifest entry with no licence file fails the
    # build, with its name (build-r.sh and build-deps.sh both start with
    # deps.py, which validates the manifest first).
    result = check_manifest(tmp_path, image_entry(license_files=line))
    assert result.returncode == 1
    assert result.stderr == f"deps.py: {message}\n"


def test_source_entry_licence_files_are_tarball_paths(tmp_path):
    entry = source_entry("curl", "https://example.org/curl-9.9.tar.gz", "a" * 64)
    entry = entry.replace('["COPYING"]', '["/usr/share/COPYING"]')
    result = check_manifest(tmp_path, entry)
    assert result.stderr == (
        "deps.py: curl: licence file '/usr/share/COPYING' is not a path its "
        "origin takes\n"
    )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"srpm": None}, "zlib: needs exactly one of srpm, source_unknown"),
        (
            {"source_unknown": '{ reason = "x", looked_in = ["y"] }'},
            "zlib: needs exactly one of srpm, source_unknown",
        ),
        (
            {"srpm": None, "source_unknown": '{ reason = "", looked_in = ["y"] }'},
            "zlib: source_unknown needs a reason",
        ),
        (
            {"srpm": None, "source_unknown": '{ looked_in = ["y"] }'},
            "zlib: source_unknown needs a reason",
        ),
        (
            {"srpm": None, "source_unknown": '{ reason = "x", looked_in = [] }'},
            "zlib: source_unknown needs looked_in",
        ),
        (
            {
                "srpm": '{ nvr = "zlib-1", url = "https://x/zlib-2.src.rpm", '
                f'sha256 = "{"d" * 64}" }}'
            },
            "zlib: srpm url does not name zlib-1.src.rpm",
        ),
        ({"via": '"cairo"'}, "zlib: via 'cairo' is no entry"),
        ({"name": "r"}, "r: the name is reserved for licenses/r"),
    ],
)
def test_image_entry_source_fields_are_checked(tmp_path, fields, message):
    result = check_manifest(tmp_path, image_entry(**fields))
    assert result.returncode == 1
    assert result.stderr == f"deps.py: {message}\n"


UNKNOWN = '{ reason = "no source RPM published", looked_in = ["vault", "mirror"] }'


def test_source_null_needs_a_reason_and_keeps_its_licence(tmp_path):
    # Design §14: a library whose source cannot be identified is listed
    # with source null, its reason and where it was looked for, and its
    # licence text is shipped all the same.
    entries = manifest_entries(
        tmp_path,
        image_entry(srpm=None, source_unknown=UNKNOWN, license_files=None).replace(
            "[[dependency]]\n",
            f'[[dependency]]\nlicense_files = ["{tmp_path}/README"]\n',
        ),
    )
    (tmp_path / "r-source").write_text(
        "R_VERSION=4.6.2\nR_URL=https://example.org/R.tar.gz\n"
        f"R_SHA256={'b' * 64}\nR_LICENSE=GPL-2.0-or-later\n"
    )
    (tmp_path / "image").write_text("quay.io/x:1@sha256:" + "c" * 64)
    (d,) = deps.build_info(entries, tmp_path / "r-source", tmp_path / "image")[
        "inputs"
    ]["dependencies"]
    assert d["source"] is None
    assert d["reason"] == "no source RPM published"
    assert d["looked_in"] == ["vault", "mirror"]
    (tmp_path / "README").write_text("the licence\n")
    deps.licenses(entries, tmp_path / "out", tmp_path / "deps", owner=None)
    assert (tmp_path / "out/zlib/README").read_text() == "the licence\n"


def test_licenses_collects_each_origin(tmp_path):
    lic = tmp_path / "usr/share/licenses/zlib"
    lic.mkdir(parents=True)
    (lic / "README").write_text("zlib licence\n")
    kept = tmp_path / "deps/share/licenses/curl"
    kept.mkdir(parents=True)
    (kept / "COPYING").write_text("curl licence\n")
    entries = manifest_entries(
        tmp_path,
        source_entry("curl", "https://example.org/curl-9.9.tar.gz", "a" * 64),
        image_entry(license_files=f'["{lic}/README"]'),
    )
    asked = []

    def owner(path):
        asked.append(Path(path))
        return "zlib-1.2.11-25.el8.src.rpm"

    deps.licenses(entries, tmp_path / "out", tmp_path / "deps", owner=owner)
    assert (tmp_path / "out/curl/COPYING").read_text() == "curl licence\n"
    assert (tmp_path / "out/zlib/README").read_text() == "zlib licence\n"
    assert asked == [lic / "README"]


def test_licenses_refuses_a_missing_file_with_its_name(tmp_path):
    entries = manifest_entries(
        tmp_path, image_entry(license_files=f'["{tmp_path}/nowhere/README"]')
    )
    with pytest.raises(deps.Refused, match=r"^zlib: licence file .*/README missing$"):
        deps.licenses(entries, tmp_path / "out", tmp_path / "deps", owner=None)


def test_licenses_refuses_a_file_from_another_source_rpm(tmp_path):
    (tmp_path / "README").write_text("x\n")
    entries = manifest_entries(
        tmp_path, image_entry(license_files=f'["{tmp_path}/README"]')
    )
    with pytest.raises(
        deps.Refused,
        match=r"^zlib: licence file .*README is from other-1.src.rpm, "
        r"not zlib-1.2.11-25.el8.src.rpm$",
    ):
        deps.licenses(
            entries,
            tmp_path / "out",
            tmp_path / "deps",
            owner=lambda p: "other-1.src.rpm",
        )


def test_license_owners_names_the_packages_to_reinstall(tmp_path, monkeypatch):
    (tmp_path / "present").write_text("x\n")
    entries = manifest_entries(
        tmp_path,
        image_entry("zlib", license_files=f'["{tmp_path}/present"]'),
        image_entry("pixman", license_files='["/nowhere/pixman/COPYING"]'),
        image_entry("libxcb", license_files='["srpm:x.tar.bz2/x/COPYING"]'),
    )
    monkeypatch.setattr(
        deps, "rpm_files", lambda: {"/nowhere/pixman/COPYING": "pixman", "/x": "y"}
    )
    assert deps.license_owners(entries) == ["pixman"]


# Provenance of the grafts: the repair's SONAMEs against the entries.


def provenance_case(tmp_path):
    entries = manifest_entries(
        tmp_path,
        source_entry("curl", "https://example.org/curl-9.9.tar.gz", "a" * 64),
        image_entry("zlib"),
    )
    grafts = {
        "libcurl.so.9": "libcurl-0123abcd.so.9",
        "libzlib.so.1": "libzlib-4567cdef.so.1",
    }
    found_at = {
        "libcurl.so.9": Path("/opt/zur-deps/lib/libcurl.so.9"),
        "libzlib.so.1": Path("/usr/lib64/libzlib.so.1"),
    }
    return entries, grafts, found_at


def owned_by(srpm):
    return lambda path: srpm


def test_provenance_maps_each_graft_to_its_entry(tmp_path):
    entries, grafts, found_at = provenance_case(tmp_path)
    files = build.provenance(
        grafts,
        found_at,
        entries,
        "/opt/zur-deps",
        owner=owned_by("zlib-1.2.11-25.el8.src.rpm"),
    )
    assert files == {
        "curl": ["zur.libs/libcurl-0123abcd.so.9"],
        "zlib": ["zur.libs/libzlib-4567cdef.so.1"],
    }


def test_provenance_refuses_a_graft_no_entry_names(tmp_path):
    # Roadmap stage 13 (#14 item 4): a grafted file whose package has no
    # manifest entry fails the build.
    entries, grafts, found_at = provenance_case(tmp_path)
    grafts["libpixman-1.so.0"] = "libpixman-1-89abcdef.so.0"
    found_at["libpixman-1.so.0"] = Path("/usr/lib64/libpixman-1.so.0")
    with pytest.raises(SystemExit) as e:
        build.provenance(
            grafts,
            found_at,
            entries,
            "/opt/zur-deps",
            owner=owned_by("zlib-1.2.11-25.el8.src.rpm"),
        )
    assert str(e.value) == (
        "provenance:\n  zur.libs/libpixman-1-89abcdef.so.0 (libpixman-1.so.0, from "
        "/usr/lib64/libpixman-1.so.0) belongs to no manifest entry"
    )


def test_provenance_refuses_a_graft_from_another_source_rpm(tmp_path):
    entries, grafts, found_at = provenance_case(tmp_path)
    with pytest.raises(SystemExit) as e:
        build.provenance(
            grafts,
            found_at,
            entries,
            "/opt/zur-deps",
            owner=owned_by("zlib-9-1.src.rpm"),
        )
    assert str(e.value) == (
        "provenance:\n  zur.libs/libzlib-4567cdef.so.1 (libzlib.so.1, from "
        "/usr/lib64/libzlib.so.1) is from zlib-9-1.src.rpm, not zlib's "
        "zlib-1.2.11-25.el8.src.rpm"
    )


def test_provenance_refuses_a_built_library_from_elsewhere(tmp_path):
    entries, grafts, found_at = provenance_case(tmp_path)
    found_at["libcurl.so.9"] = Path("/usr/lib64/libcurl.so.9")
    with pytest.raises(SystemExit, match="is curl's but not from /opt/zur-deps/lib$"):
        build.provenance(
            grafts,
            found_at,
            entries,
            "/opt/zur-deps",
            owner=owned_by("zlib-1.2.11-25.el8.src.rpm"),
        )


def test_provenance_refuses_an_entry_nothing_bundles(tmp_path):
    entries, grafts, found_at = provenance_case(tmp_path)
    del grafts["libzlib.so.1"]
    with pytest.raises(SystemExit) as e:
        build.provenance(grafts, found_at, entries, "/opt/zur-deps", owner=None)
    assert str(e.value) == "provenance:\n  zlib: libzlib.so.1 is not bundled"


# The licence expression and METADATA.


def info_with(*licenses):
    return {
        "inputs": {
            "r": {"license": "GPL-2.0-or-later"},
            "dependencies": [
                {"name": f"d{i}", "license": x} for i, x in enumerate(licenses)
            ],
        },
        "zur": {"license": "GPL-2.0-or-later"},
    }


def test_license_expression_of_the_committed_manifest():
    from packaging.licenses import canonicalize_license_expression

    info = {
        "inputs": {
            "r": {
                "license": deps.shell_vars(REPO / "scripts/linux/r-source")["R_LICENSE"]
            },
            "dependencies": deps.load(),
        },
        "zur": {"license": build.ZUR_LICENSE},
    }
    expression = build.license_expression(info)
    assert expression == LINUX_EXPRESSION
    assert canonicalize_license_expression(expression) == expression


@pytest.mark.parametrize(
    ("licenses", "expected"),
    [
        # Each term once, in first-seen order; an OR keeps its parentheses.
        (["MIT", "MIT AND Zlib"], "GPL-2.0-or-later AND MIT AND Zlib"),
        (["FTL OR GPL-2.0-or-later"], "GPL-2.0-or-later AND (FTL OR GPL-2.0-or-later)"),
        (
            ["(MIT OR X11) AND Zlib", "Zlib AND (MIT OR X11)"],
            "GPL-2.0-or-later AND (MIT OR X11) AND Zlib",
        ),
        (
            ["GPL-3.0-or-later WITH GCC-exception-3.1", "GPL-3.0-or-later"],
            "GPL-2.0-or-later AND GPL-3.0-or-later WITH GCC-exception-3.1"
            " AND GPL-3.0-or-later",
        ),
    ],
)
def test_license_expression_ands_each_term_once(licenses, expected):
    assert build.license_expression(info_with(*licenses)) == expected


def test_metadata_is_valid_core_metadata_2_4():
    # PEP 639: License-Expression and one License-File per licence file,
    # paths relative to the .dist-info's licenses/; packaging validates it.
    from packaging.metadata import Metadata

    files = ["curl/COPYING", "r/COPYING", "zur/LICENSE"]
    text = build.metadata("4.6.2", "GPL-2.0-or-later AND curl", files)
    meta = Metadata.from_email(text, validate=True)
    assert str(meta.metadata_version) == "2.4"
    assert str(meta.license_expression) == "GPL-2.0-or-later AND curl"
    assert [str(f) for f in meta.license_files] == files
    assert meta.license is None


def test_build_licenses_adds_r_and_zur_and_refuses_an_empty_entry(tmp_path):
    runtime = tmp_path / "_runtime"
    (runtime / "lib/R/doc").mkdir(parents=True)
    (runtime / build.R_COPYING).write_text("R's GPL-2\n")
    (runtime / "licenses/zlib").mkdir(parents=True)
    (runtime / "licenses/zlib/README").write_text("zlib\n")
    info = {"inputs": {"dependencies": [{"name": "zlib"}]}}
    assert build.licenses(runtime, info) == ["r/COPYING", "zlib/README", "zur/LICENSE"]
    assert (runtime / "licenses/zur/LICENSE").read_bytes() == (
        REPO / "LICENSE"
    ).read_bytes()
    info["inputs"]["dependencies"].append({"name": "pixman"})
    (runtime / "licenses/pixman").mkdir()
    with pytest.raises(SystemExit) as e:
        build.licenses(runtime, info)
    assert str(e.value) == "licenses: no licence file for pixman"


def test_pack_puts_the_licences_in_the_dist_info(tmp_path):
    stage = tmp_path / "stage"
    lic = stage / "zur/_runtime/licenses"
    (lic / "r").mkdir(parents=True)
    (lic / "r/COPYING").write_text("GPL-2\n")
    wheel = build.pack(
        stage, tmp_path / "out", "4.6.2", "any", "GPL-2.0-or-later", ["r/COPYING"]
    )
    with build.zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        meta = zf.read("zur-4.6.2.dist-info/METADATA").decode()
        assert zf.read("zur-4.6.2.dist-info/licenses/r/COPYING") == b"GPL-2\n"
    assert "zur/_runtime/licenses/r/COPYING" in names
    assert "License-Expression: GPL-2.0-or-later\nLicense-File: r/COPYING\n" in meta
