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


def test_entry_points_are_r_rscript_and_zur(members):
    # Design §5: uv links exactly these into its tool bin directory.
    _, data = members[f"zur-{R_VERSION}.dist-info/entry_points.txt"]
    assert data.decode() == (
        "[console_scripts]\nR = zur:R\nRscript = zur:Rscript\nzur = zur:main\n"
    )


def test_doctor_module_is_the_source_trees(members):
    _, data = members["zur/doctor.py"]
    assert data == (REPO / "src/zur/doctor.py").read_bytes()


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


# patchelf, given no --page-size, starts the segment it adds at the next
# p_align boundary (2 MiB on x86_64) and fills the gap with zeros (#43).
# A run longer than this in an ELF file is padding, not content.
MAX_ZERO_RUN = 64 * 1024


def longest_zero_run(data):
    """The longest run of zero bytes longer than MAX_ZERO_RUN, or 0."""
    needle = bytes(MAX_ZERO_RUN + 1)
    longest = 0
    start = data.find(needle)
    while start >= 0:
        end = start + len(needle)
        while end < len(data) and data[end] == 0:
            end += 1
        longest = max(longest, end - start)
        start = data.find(needle, end)
    return longest


def test_longest_zero_run_counts_bytes():
    assert longest_zero_run(b"a" + b"\x00" * MAX_ZERO_RUN + b"a") == 0
    assert longest_zero_run(b"a" + b"\x00" * (MAX_ZERO_RUN + 1) + b"a") == (
        MAX_ZERO_RUN + 1
    )
    two = b"\x00" * (MAX_ZERO_RUN + 1) + b"a" + b"\x00" * (MAX_ZERO_RUN + 9)
    assert longest_zero_run(two) == MAX_ZERO_RUN + 9


def test_no_elf_file_is_padded_with_zeros_issue_43(members, elfs):
    padded = {
        name: run
        for name in elfs
        if (run := longest_zero_run(members[name][1])) > MAX_ZERO_RUN
    }
    assert padded == {}


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
    # R's three, two copies under the reference names and the FlexiBLAS
    # shim (D16).
    lib = f"{RUNTIME}/lib/R/lib/"
    found = sorted(n[len(lib) :] for n in members if n.startswith(lib))
    assert found == [
        "libR.so",
        "libRblas.so",
        "libRlapack.so",
        "libblas.so.3",
        "libflexiblas.so.3",
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


def defined_symbols(path):
    with path.open("rb") as f:
        dynsym = ELFFile(f).get_section_by_name(".dynsym")
        if dynsym is None:
            return []
        return [
            s.name
            for s in dynsym.iter_symbols()
            if s.name and s["st_shndx"] != "SHN_UNDEF"
        ]


def test_flexiblas_name_is_a_shim_onto_r_blas(tree, members, elfs):
    # Design §6.3, D16: P3M's RHEL 9 binaries link libflexiblas.so.3. The
    # shim only loads R's LAPACK and BLAS, which define every symbol.
    name = f"{RUNTIME}/lib/R/lib/libflexiblas.so.3"
    needed, soname, runpath = elfs[name]
    assert soname == "libflexiblas.so.3"
    assert needed == ["libRlapack.so", "libRblas.so"]
    assert runpath == ["$ORIGIN"]
    assert defined_symbols(tree / name) == []
    assert "dgemm_" in defined_symbols(tree / f"{RUNTIME}/lib/R/lib/libRblas.so")
    assert members[name][0].file_size < 20_000


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


# Licences and build info (roadmap stage 13, design §14) ----------------------

DIST_INFO = f"zur-{R_VERSION}.dist-info"
LICENSES = f"{RUNTIME}/licenses/"
DIST_LICENSES = f"{DIST_INFO}/licenses/"


@pytest.fixture(scope="session")
def build_info(members):
    return json.loads(members[f"{RUNTIME}/BUILD-INFO.json"][1])


@pytest.fixture(scope="session")
def metadata(members):
    from packaging.metadata import Metadata

    text = members[f"{DIST_INFO}/METADATA"][1].decode()
    return Metadata.from_email(text, validate=True)


def and_terms(expression):
    """The top-level AND terms of an SPDX expression, parentheses kept."""
    terms, depth, term = [], 0, ""
    for word in expression.split(" "):
        depth += word.count("(") - word.count(")")
        if word == "AND" and depth == 0:
            terms.append(term.strip())
            term = ""
        else:
            term += " " + word
    return terms + [term.strip()]


def test_every_entry_has_its_licence_files(members, build_info):
    # Completeness: licenses/<name>/ is non-empty for R, zur and every
    # BUILD-INFO.json entry, and holds no directory for anything else.
    names = ["r", "zur"] + [d["name"] for d in build_info["inputs"]["dependencies"]]
    dirs = {}
    for name, (_, data) in members.items():
        if name.startswith(LICENSES):
            entry, _, file = name[len(LICENSES) :].partition("/")
            assert data, name
            dirs.setdefault(entry, []).append(file)
    assert sorted(dirs) == sorted(names)
    for d in build_info["inputs"]["dependencies"]:
        assert sorted(dirs[d["name"]]) == sorted(
            Path(f).name for f in d["license_files"]
        ), d["name"]


def test_every_bundled_elf_file_is_rs_or_an_entrys(elfs, build_info):
    # Completeness: each grafted file is listed in exactly one entry's
    # `files`, and every other ELF file is R's own (under _runtime).
    owners = {}
    for d in build_info["inputs"]["dependencies"]:
        for f in d["files"]:
            owners.setdefault(f, []).append(d["name"])
    for name in elfs:
        if name.startswith("zur.libs/"):
            assert len(owners.get(name, [])) == 1, (name, owners.get(name))
        else:
            assert name.startswith(f"{RUNTIME}/"), name
    listed = {f for f in owners if f.startswith("zur.libs/")}
    assert listed == {n for n in elfs if n.startswith("zur.libs/")}


def test_every_entry_bundles_files(members, build_info):
    # An entry with sonames bundles one graft per soname; the font, its faces.
    for d in build_info["inputs"]["dependencies"]:
        assert len(d["files"]) == (len(d["sonames"]) if d["sonames"] else 4), d["name"]
        for f in d["files"]:
            assert f in members, (d["name"], f)
    (font,) = [d for d in build_info["inputs"]["dependencies"] if not d["sonames"]]
    assert font["files"] == [
        f"{RUNTIME}/lib/R/share/fonts/LiberationSans-{face}.ttf"
        for face in ["Regular", "Bold", "Italic", "BoldItalic"]
    ]


def test_license_files_match_the_licences_exactly(members, metadata):
    # PEP 639: one License-File per file of the .dist-info's licenses/,
    # which mirrors zur/_runtime/licenses/ byte for byte.
    runtime = {
        n[len(LICENSES) :]: d for n, (_, d) in members.items() if n.startswith(LICENSES)
    }
    dist = {
        n[len(DIST_LICENSES) :]: d
        for n, (_, d) in members.items()
        if n.startswith(DIST_LICENSES)
    }
    assert dist == runtime
    assert sorted(str(f) for f in metadata.license_files) == sorted(dist)


def test_license_expression_is_every_entrys(metadata, build_info):
    # Design §14, D21: R's licence and every entry's, ANDed, each term once.
    from packaging.licenses import canonicalize_license_expression

    expression = str(metadata.license_expression)
    assert expression == build_info["license_expression"]
    assert canonicalize_license_expression(expression) == expression
    licences = [build_info["inputs"]["r"]["license"], build_info["zur"]["license"]]
    licences += [d["license"] for d in build_info["inputs"]["dependencies"]]
    expected = set()
    for licence in licences:
        expected |= {
            t if " OR " not in t or t.startswith("(") else f"({t})"
            for t in and_terms(licence)
        }
    terms = and_terms(expression)
    assert len(terms) == len(set(terms))
    assert set(terms) == expected
    assert metadata.license is None


def test_build_info_names_every_source(build_info):
    # Each entry's corresponding source: tarball or source RPM with URL
    # and SHA-256, or null with a reason; and the release holding them.
    for d in build_info["inputs"]["dependencies"]:
        source = d["source"]
        if source is None:
            assert d["reason"] and d["looked_in"], d["name"]
            continue
        assert source["kind"] == ("tarball" if d["origin"] == "source" else "srpm")
        assert source["url"].startswith("https://"), d["name"]
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), d["name"]
        if source["kind"] == "srpm":
            assert source["url"].endswith(f"/{source['name']}.src.rpm")
    assert build_info["inputs"]["r"]["license"] == "GPL-2.0-or-later"
    assert build_info["zur"] == {"version": R_VERSION, "license": "GPL-2.0-or-later"}
    assert build_info["licenses"] == f"{RUNTIME}/licenses"
    # The release scripts/sources.py mirrors the sources to (stage 14).
    assert build_info["source_release"] == (
        f"https://github.com/pedrobtz/py-zur/releases/tag/v{R_VERSION}"
    )
