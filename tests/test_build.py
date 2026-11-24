"""Checks against the built wheel file (design §6.3, §13 build checks).

Run on any Linux machine after the wheel is built, with ZUR_WHEEL naming
it:

    ZUR_WHEEL=dist/zur-4.6.2-py3-none-manylinux_2_28_x86_64.whl \\
        uv run pytest -v tests/test_build.py

Without ZUR_WHEEL the module is skipped: the wheel exists only in CI
(roadmap stage 5). The allowlist and the platform tag come from
auditwheel, not from the build script.
"""

import base64
import ctypes
import hashlib
import importlib.resources
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent

if "ZUR_WHEEL" not in os.environ:
    pytest.skip("ZUR_WHEEL is not set", allow_module_level=True)

WHEEL = Path(os.environ["ZUR_WHEEL"])
TAG = "manylinux_2_28_x86_64"
RUNTIME = "zur/_runtime"

# scripts/linux/build-r.sh and build-deps.sh install into these; the
# second starts with the first, so one search finds both.
BUILD_PREFIX = "/opt/zur"


def lines(path):
    text = path.read_text().splitlines()
    return [s for s in text if s and not s.startswith("#")]


R_VERSION = dict(line.split("=", 1) for line in lines(REPO / "scripts/linux/r-source"))[
    "R_VERSION"
]
FORTRAN_USERS = {
    f"{RUNTIME}/{p}" for p in lines(REPO / "tests/data/fortran-users.txt")
} | {f"{RUNTIME}/lib/R/lib/libblas.so.3", f"{RUNTIME}/lib/R/lib/liblapack.so.3"}
COMPILER_RUNTIMES = ("libgfortran", "libquadmath", "libgomp")


def allowlist():
    policies = importlib.resources.files("auditwheel.policy") / "manylinux-policy.json"
    for policy in json.loads(policies.read_text()):
        if policy["name"] == "manylinux_2_28":
            return set(policy["lib_whitelist"])
    raise AssertionError("auditwheel has no manylinux_2_28 policy")


@pytest.fixture(scope="session")
def members():
    with zipfile.ZipFile(WHEEL) as zf:
        return {info.filename: (info, zf.read(info)) for info in zf.infolist()}


@pytest.fixture(scope="session")
def tree(tmp_path_factory, members):
    root = tmp_path_factory.mktemp("wheel")
    for name, (_, data) in members.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def dynamic(path):
    """(DT_NEEDED, DT_SONAME, RUNPATH entries) of an ELF file."""
    with path.open("rb") as f:
        for section in ELFFile(f).iter_sections():
            if isinstance(section, DynamicSection):
                tags = list(section.iter_tags())
                needed = [t.needed for t in tags if t.entry.d_tag == "DT_NEEDED"]
                soname = next(
                    (t.soname for t in tags if t.entry.d_tag == "DT_SONAME"), None
                )
                runpath = [t.runpath for t in tags if t.entry.d_tag == "DT_RUNPATH"]
                runpath += [t.rpath for t in tags if t.entry.d_tag == "DT_RPATH"]
                return needed, soname, ":".join(runpath).split(":") if runpath else []
    return [], None, []


def text_section(path):
    with path.open("rb") as f:
        return ELFFile(f).get_section_by_name(".text").data()


@pytest.fixture(scope="session")
def elfs(tree, members):
    found = {}
    for name, (_, data) in members.items():
        if data[:4] == b"\x7fELF":
            found[name] = dynamic(tree / name)
    return found


def test_wheel_name_and_tag(members):
    assert WHEEL.name == f"zur-{R_VERSION}-py3-none-{TAG}.whl"
    _, data = members[f"zur-{R_VERSION}.dist-info/WHEEL"]
    assert f"Tag: py3-none-{TAG}\n" in data.decode()


def test_version_placeholder_is_replaced(members):
    _, data = members["zur/__init__.py"]
    assert f'__version__ = "{R_VERSION}"' in data.decode()
    assert "0+unknown" not in data.decode()


def test_scan_reads_the_wheel(elfs):
    # Without this, the checks below would pass on a scan that read
    # nothing: libR.so needs R's BLAS and the C library.
    assert {"libRblas.so", "libc.so.6"} <= set(elfs[f"{RUNTIME}/lib/R/lib/libR.so"][0])
    assert FORTRAN_USERS <= set(elfs)


def test_every_needed_resolves_in_the_wheel_or_the_allowlist(tree, elfs):
    # As the dynamic loader would: each file's own RUNPATH, with $ORIGIN
    # its directory, and only directories inside the wheel.
    allowed = allowlist() | {"ld-linux-x86-64.so.2"}
    unresolved = []
    for name, (needed, _, runpath) in elfs.items():
        origin = (tree / name).parent
        dirs = [
            Path(d.replace("$ORIGIN", str(origin))).resolve()
            for d in runpath
            if d.startswith("$ORIGIN")
        ]
        dirs = [d for d in dirs if d.is_relative_to(tree)]
        for lib in needed:
            if lib in allowed or any((d / lib).is_file() for d in dirs):
                continue
            unresolved.append(f"{name} needs {lib}")
    assert unresolved == []


def test_no_runpath_outside_the_wheel(elfs):
    outside = {
        n: r
        for n, (_, _, r) in elfs.items()
        if any(not d.startswith("$ORIGIN") for d in r)
    }
    assert outside == {}


def test_auditwheel_show_reports_manylinux_2_28():
    result = subprocess.run(
        [sys.executable, "-m", "auditwheel", "show", "--json", str(WHEEL)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    # The tag promises glibc 2.28 (D4); the wheel may need less.
    m = re.fullmatch(r"manylinux_2_(\d+)_x86_64", report["overall_tag"])
    assert m and int(m[1]) <= 28, report
    # Allowlisted libraries are not external; anything else would be.
    assert report["external_libs"] == {}


def test_r_lib_holds_only_r_libraries_and_blas_names(members):
    lib = f"{RUNTIME}/lib/R/lib/"
    found = sorted(n[len(lib) :] for n in members if n.startswith(lib))
    assert found == [
        "libR.so",
        "libRblas.so",
        "libRlapack.so",
        "libblas.so.3",
        "liblapack.so.3",
    ]


def test_blas_names_are_copies_of_r_blas(tree, elfs):
    lib = f"{RUNTIME}/lib/R/lib/"
    for name, source in [
        ("libblas.so.3", "libRblas.so"),
        ("liblapack.so.3", "libRlapack.so"),
    ]:
        assert elfs[lib + name][1] == name
        # Same code: patchelf changed only the dynamic section.
        assert text_section(tree / lib / name) == text_section(tree / lib / source)
        assert elfs[lib + name][0] == elfs[lib + source][0]


def test_fortran_runtime_is_grafted_under_hashed_names(elfs):
    grafted = {}
    for name, (_, soname, _) in elfs.items():
        m = re.fullmatch(
            r"zur\.libs/(libgfortran|libquadmath)-[0-9a-f]{8}\.so\.(\d+)", name
        )
        if m:
            assert soname == Path(name).name
            grafted[m[1]] = Path(name).name
    assert set(grafted) == {"libgfortran", "libquadmath"}
    users = {
        n
        for n, (needed, _, _) in elfs.items()
        if set(needed) & set(grafted.values()) and not n.startswith("zur.libs/")
    }
    assert users == FORTRAN_USERS
    # The graft needs its own quadmath, and nothing needs the real names.
    gfortran = f"zur.libs/{grafted['libgfortran']}"
    assert grafted["libquadmath"] in elfs[gfortran][0]
    real = {"libgfortran.so.5", "libquadmath.so.0", "libgomp.so.1"}
    assert {n for n, (needed, _, _) in elfs.items() if real & set(needed)} == set()


def test_no_compiler_runtime_on_the_library_path(members):
    lib = f"{RUNTIME}/lib/R/lib/"
    found = [
        n
        for n in members
        if n.startswith(lib) and n[len(lib) :].startswith(COMPILER_RUNTIMES)
    ]
    assert found == []


def test_grafts_have_hashed_names(elfs):
    for name, (_, soname, _) in elfs.items():
        if name.startswith("zur.libs/"):
            assert re.fullmatch(r"zur\.libs/lib[\w+.-]+-[0-9a-f]{8}\.so(\.\d+)*", name)
            assert soname == Path(name).name


def test_no_symlinks(members):
    links = [
        n for n, (info, _) in members.items() if stat.S_ISLNK(info.external_attr >> 16)
    ]
    assert links == []
    regular = [
        n for n, (info, _) in members.items() if stat.S_ISREG(info.external_attr >> 16)
    ]
    assert len(regular) == len(members)


# Design §9's prune (roadmap stage 9), written out here rather than read
# from scripts/build.py's PRUNE.
PRUNED = [
    rf"{RUNTIME}/lib/R/doc/manual/",
    rf"{RUNTIME}/lib/R/doc/[^/]+\.pdf$",
    rf"{RUNTIME}/lib/R/library/[^/]+/(doc|demo|tests|po)/",
    rf"{RUNTIME}/lib/R/tests/",
    rf"{RUNTIME}/lib/R/library/translations/",
]


def test_pruned_paths_are_absent(members):
    left = [n for n in members if any(re.match(p, n) for p in PRUNED)]
    assert left == []


@pytest.mark.parametrize(
    "kept",
    [
        "lib/R/include/R.h",
        "lib/R/etc/Makeconf",
        "lib/R/library/stats/help/stats.rdb",
        "lib/R/library/base/R/Rprofile",
        "lib/R/doc/COPYING",
    ],
)
def test_kept_paths_are_present(members, kept):
    assert f"{RUNTIME}/{kept}" in members


def test_record_matches_the_members(members):
    record_name = f"zur-{R_VERSION}.dist-info/RECORD"
    record = {}
    for line in members[record_name][1].decode().splitlines():
        path, digest, size = line.rsplit(",", 2)
        record[path] = (digest, size)
    assert record.pop(record_name) == ("", "")
    actual = {}
    for name, (_, data) in members.items():
        if name == record_name:
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        actual[name] = ("sha256=" + digest.rstrip(b"=").decode(), str(len(data)))
    assert record == actual


def test_no_member_contains_the_build_prefix(members):
    found = [n for n, (_, data) in members.items() if BUILD_PREFIX.encode() in data]
    assert found == []


# Headless runtime files (design §5, §6.4, roadmap stage 6) -------------------


@pytest.mark.parametrize("where", ["bin/Rscript", "lib/R/bin/Rscript"])
def test_rscript_front_ends_are_scripts(members, where):
    info, data = members[f"{RUNTIME}/{where}"]
    mode = info.external_attr >> 16
    assert stat.S_ISREG(mode) and mode & 0o111
    assert data[:4] != b"\x7fELF"
    assert data.startswith(b"#!/bin/sh\n")
    assert b'exec "${RHOME}/libexec/Rscript" "$@"\n' in data


def test_compiled_rscript_is_only_in_libexec(members, elfs):
    compiled = sorted(n for n in elfs if Path(n).name == "Rscript")
    assert compiled == [f"{RUNTIME}/lib/R/libexec/Rscript"]
    _, data = members[compiled[0]]
    assert BUILD_PREFIX.encode() not in data


def test_bin_exec_holds_only_r(members):
    # R CMD INSTALL takes every file in bin/exec for a sub-architecture.
    exec_dir = f"{RUNTIME}/lib/R/bin/exec/"
    assert [n for n in members if n.startswith(exec_dir)] == [exec_dir + "R"]


def test_grafted_fontconfig_is_the_pinned_version(tree, elfs):
    # 2.15 reads <dir prefix="relative"> (design §6.4); FcGetVersion() is
    # major * 10000 + minor * 100 + revision.
    with (REPO / "manifests/linux.toml").open("rb") as f:
        (pin,) = [e for e in tomllib.load(f)["dependency"] if e["name"] == "fontconfig"]
    major, minor, rev = map(int, pin["version"].split("."))
    (graft,) = [n for n in elfs if re.fullmatch(r"zur\.libs/libfontconfig-.*", n)]
    lib = ctypes.CDLL(str(tree / graft))
    assert lib.FcGetVersion() == major * 10000 + minor * 100 + rev


def test_font_family_and_fontconfig_file(members):
    fonts = f"{RUNTIME}/lib/R/share/fonts/"
    faces = sorted(n[len(fonts) :] for n in members if n.startswith(fonts))
    assert faces == [
        "LiberationSans-Bold.ttf",
        "LiberationSans-BoldItalic.ttf",
        "LiberationSans-Italic.ttf",
        "LiberationSans-Regular.ttf",
    ]
    for face in faces:
        assert members[fonts + face][1][:4] == b"\x00\x01\x00\x00"  # TrueType
    conf = members[f"{RUNTIME}/lib/R/etc/fonts/fonts.conf"][1].decode()
    assert '<dir prefix="relative">../../share/fonts</dir>' in conf
    # Every directory it names is relative, the system's or the user's.
    dirs = re.findall(r"<(?:dir|cachedir)[^>]*>([^<]*)<", conf)
    assert dirs and not any(d.startswith(BUILD_PREFIX) for d in dirs)
    assert BUILD_PREFIX not in conf


def test_internal_tzcode(members):
    # Confirms --with-internal-tzcode (design §6.2) in configure's own
    # record of its call, and R's zoneinfo shipped with it.
    makeconf = members[f"{RUNTIME}/lib/R/etc/Makeconf"][1].decode()
    (call,) = [s for s in makeconf.splitlines() if s.startswith("# configure ")]
    assert "'--with-internal-tzcode'" in call
    assert f"{RUNTIME}/lib/R/share/zoneinfo/Europe/Zurich" in members


# Source packages across host compilers (design §6.3, roadmap stage 10) -------


def test_makeconf_picks_the_cxx_standard_per_host(members):
    makeconf = members[f"{RUNTIME}/lib/R/etc/Makeconf"][1].decode().splitlines()
    probe = (
        "ZUR_CXXSTD := $(shell g++ -std=gnu++20 -fsyntax-only -x c++ /dev/null"
        " 2>/dev/null && echo gnu++20 || echo gnu++17)"
    )
    assert makeconf.count(probe) == 1
    (cxx,) = [s for s in makeconf if s.startswith("CXX = ")]
    assert cxx == "CXX = g++ -std=$(ZUR_CXXSTD)"
    assert makeconf.index(probe) < makeconf.index(cxx)
    # The explicit standards stay as the build's compiler set them.
    for std in ["17", "20", "23", "26"]:
        assert f"CXX{std}STD = -std=gnu++{std}" in makeconf
