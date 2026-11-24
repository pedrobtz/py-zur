"""Build zur's Linux wheel from R built from source (design §5, §6.3).

Run in the manylinux_2_28 image named by scripts/linux/image, after
scripts/linux/build-r.sh has installed R into the prefix and
``scripts/deps.py build-info`` has written its BUILD-INFO.json:

    uv run --frozen scripts/build.py --prefix /opt/zur --deps /opt/zur-deps \\
        --out dist

Steps, each a function below, on a copy of the prefix (the prefix is left
as built, for tests/test_prefix.py):

1. relocate  R's text files that name the build prefix; bin/Rscript
             becomes a script that finds its R (design §5)
2. fonts     a font family and the fontconfig file naming it (§6.4)
3. blas      libblas.so.3 and liblapack.so.3 next to R's own, and the
             libflexiblas.so.3 shim onto them (D16)
4. repair    graft every library outside the system baseline into
             zur.libs/ under a hashed name; $ORIGIN-relative RUNPATHs;
             every graft must belong to a manifest entry (provenance)
5. prune     what a headless R does not use (design §9, D6, D19)
6. scrub     build-prefix strings compiled into ELF files
7. dedupe    symlinks, which wheels cannot hold
8. licenses  R's and zur's licence texts next to the manifest's, which
             ``deps.py licenses`` put in the prefix; BUILD-INFO.json gets
             the files each entry bundles and the licence expression
             (design §14)
9. pack      the copy, the launcher and zur doctor into a wheel, checked against
             --max-size (PyPI's per-file limit by default, design §10);
             --size-report prints its size by part

The wheel's version is R's (scripts/linux/r-source), with ``--post``.
See .agents/design.md for the design.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

REPO = Path(__file__).resolve().parent.parent
TAG = "manylinux_2_28_x86_64"


def _load_deps():
    spec = importlib.util.spec_from_file_location("zur_deps", REPO / "scripts/deps.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deps_py = _load_deps()

# What R and its libraries may take from the machine (design §6.3): glibc
# and the gcc runtime C++ code needs, which every manylinux_2_28 system
# has. The rest of the manylinux allowlist (X11, GL, GLib, expat, zlib) is
# grafted like any other library: a headless container lacks most of it.
SYSTEM = frozenset(
    {
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libm.so.6",
        "libmvec.so.1",
        "libdl.so.2",
        "librt.so.1",
        "libpthread.so.0",
        "libresolv.so.2",
        "libutil.so.1",
        "libanl.so.1",
        "libgcc_s.so.1",
        "libstdc++.so.6",
    }
)

# Where the image keeps the libraries R links, after the RPATH of the file
# that needs them and the built dependencies' lib/.
SYSTEM_DIRS = [Path("/usr/lib64"), Path("/lib64")]

# The files R's own libraries live in, which packages link by plain name
# (design §6.3): R's, and its BLAS and LAPACK under the names P3M binaries
# link (D16).
R_LIBS = ["libR.so", "libRblas.so", "libRlapack.so"]
BLAS_NAMES = {"libblas.so.3": "libRblas.so", "liblapack.so.3": "libRlapack.so"}
# The FlexiBLAS name P3M's RHEL 9 binaries link, and what the shim built
# under it loads, in this order (design §6.3, D16).
BLAS_SHIM = "libflexiblas.so.3"
BLAS_SHIM_NEEDED = ["libRlapack.so", "libRblas.so"]


def read_r_source():
    path = REPO / "scripts/linux/r-source"
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


# ELF helpers -----------------------------------------------------------------


def is_elf(path):
    with path.open("rb") as f:
        return f.read(4) == b"\x7fELF"


def dynamic(path):
    """(DT_NEEDED, DT_SONAME, DT_RUNPATH or DT_RPATH) of an ELF file."""
    with path.open("rb") as f:
        for section in ELFFile(f).iter_sections():
            if isinstance(section, DynamicSection):
                tags = list(section.iter_tags())
                needed = [t.needed for t in tags if t.entry.d_tag == "DT_NEEDED"]
                soname = next(
                    (t.soname for t in tags if t.entry.d_tag == "DT_SONAME"), None
                )
                runpath = next(
                    (t.runpath for t in tags if t.entry.d_tag == "DT_RUNPATH"),
                    next((t.rpath for t in tags if t.entry.d_tag == "DT_RPATH"), ""),
                )
                return needed, soname, runpath
    return [], None, ""


def elf_files(root):
    return sorted(
        p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and is_elf(p)
    )


# The page size of the wheel's architecture (design §6.3, #43): 4 KiB on
# x86_64; aarch64 kernels may use 64 KiB pages, so 64 KiB there.
PAGE_SIZE = {"x86_64": 4096, "aarch64": 65536}
PT_LOAD = 1


def lower_load_align(path, page_size):
    """Lower every PT_LOAD p_align above ``page_size`` to it (#43).

    patchelf (0.19) starts the segment each rewrite adds at the next
    multiple of the file's largest p_align, whatever ``--page-size``
    says, and fills the gap with zeros: 2 MiB per rewrite for libraries
    linked with ``-z max-page-size=0x200000``. The segments stay
    congruent modulo ``page_size``, which is all the loader maps by.
    Returns the number of headers changed.
    """
    with path.open("r+b") as f:
        ident = f.read(16)
        if ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1:
            raise SystemExit(f"not a little-endian ELF64 file: {path}")
        f.seek(0x20)
        (phoff,) = struct.unpack("<Q", f.read(8))
        f.seek(0x36)
        phentsize, phnum = struct.unpack("<HH", f.read(4))
        changed = 0
        for i in range(phnum):
            f.seek(phoff + i * phentsize)
            p_type, _, p_offset, p_vaddr = struct.unpack("<IIQQ", f.read(24))
            f.seek(phoff + i * phentsize + 48)
            (p_align,) = struct.unpack("<Q", f.read(8))
            if p_type != PT_LOAD or p_align <= page_size:
                continue
            if (p_vaddr - p_offset) % page_size:
                raise SystemExit(f"{path}: PT_LOAD {i} not congruent mod {page_size}")
            f.seek(phoff + i * phentsize + 48)
            f.write(struct.pack("<Q", page_size))
            changed += 1
    return changed


def patchelf(*args):
    """patchelf ``args`` on the file named last, at the wheel's page size."""
    page_size = PAGE_SIZE[TAG.split("_", 3)[3]]
    lower_load_align(Path(args[-1]), page_size)
    subprocess.run(
        ["patchelf", "--page-size", str(page_size), *map(str, args)], check=True
    )


# 1. relocate -----------------------------------------------------------------


def _replace_once(text, old, new, where):
    if old not in text:
        raise SystemExit(f"{where}: expected text not found: {old!r}")
    return text.replace(old, new, 1)


# Inserted into bin/R after R_HOME is exported (design §5, §6.4, D13).
# bin/Rscript, not bin/R, sets RHOME (RSCRIPT below).
FRONT_END_ZUR = """\
## zur (design §6.4): the fonts shipped with R and the system's, through
## zur's fontconfig file, unless the user has chosen another.
if test -z "${FONTCONFIG_FILE}"; then
  FONTCONFIG_FILE="${R_HOME}/etc/fonts/fonts.conf"
  export FONTCONFIG_FILE
fi
## zur (design §6.4): CA certificates from the system, each variable
## only when the user has not set it.
for zur_ca in /etc/ssl/certs/ca-certificates.crt \\
  /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem \\
  /etc/pki/tls/cacert.pem /etc/ssl/cert.pem; do
  if test -r "${zur_ca}"; then
    if test -z "${CURL_CA_BUNDLE}"; then
      CURL_CA_BUNDLE="${zur_ca}"
      export CURL_CA_BUNDLE
    fi
    if test -z "${SSL_CERT_FILE}"; then
      SSL_CERT_FILE="${zur_ca}"
      export SSL_CERT_FILE
    fi
    break
  fi
done
unset zur_ca
## zur (design §5, D10): zur's own user library, per distribution and R
## minor version, unless the user has chosen another (etc/Renviron keeps
## a value set here or by the user). <platform> is <arch>-<ID>-<VERSION_ID>
## from /etc/os-release, read without running it; a missing ID is
## "linux" as the os-release specification has it, a missing VERSION_ID
## is left out, and a value outside the characters the specification
## allows counts as missing. The directory is created when possible;
## with a read-only home R starts without it.
if test -z "${R_LIBS_USER}" && test -n "${HOME}"; then
  zur_id=linux
  zur_version_id=
  if test -r /etc/os-release; then
    while IFS='=' read -r zur_key zur_value || test -n "${zur_key}"; do
      zur_value=${zur_value#[\\"\\']}
      zur_value=${zur_value%[\\"\\']}
      case "${zur_value}" in
        "" | *[!A-Za-z0-9._-]*) continue ;;
      esac
      case "${zur_key}" in
        ID) zur_id="${zur_value}" ;;
        VERSION_ID) zur_version_id="-${zur_value}" ;;
      esac
    done < /etc/os-release
  fi
  R_LIBS_USER="${HOME%/}/R/zur/@ARCH@-${zur_id}${zur_version_id}/@R_MINOR@"
  export R_LIBS_USER
  test -d "${R_LIBS_USER}" || mkdir -p "${R_LIBS_USER}" 2>/dev/null || :
  unset zur_id zur_version_id zur_key zur_value
fi
"""

# The user library's arch is the wheel's, not the running machine's.
ARCH = TAG.split("_", 3)[3]


def front_end_zur(r_version, arch=ARCH):
    """FRONT_END_ZUR for this R and architecture."""
    minor = ".".join(r_version.split(".")[:2])
    if not re.fullmatch(r"\d+\.\d+", minor):
        raise SystemExit(f"R version {r_version!r}: no major.minor")
    return FRONT_END_ZUR.replace("@ARCH@", arch).replace("@R_MINOR@", minor)


def relocate_front_end(path, prefix, home_from_script, r_version):
    """bin/R: R_HOME from its own location, fonts, CA bundle, library."""
    text = path.read_text()
    home = f"{prefix}/lib/R"
    # make install writes R_HOME_DIR=<home>, quoted or not.
    text, n = re.subn(
        rf"^R_HOME_DIR=\"?{re.escape(home)}\"?$",
        'R_HOME_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/'
        f'{home_from_script}" && pwd -P)"',
        text,
        count=1,
        flags=re.M,
    )
    if n != 1:
        raise SystemExit(f"{path}: no R_HOME_DIR={home} line")
    # The block that looks for lib64/lib under the configured prefix.
    text = _replace_once(
        text, f'if test "${{R_HOME_DIR}}" = "{home}"; then', "if false; then", path
    )
    text = _replace_once(
        text,
        'R_HOME="${R_HOME_DIR}"\nexport R_HOME\n',
        'R_HOME="${R_HOME_DIR}"\nexport R_HOME\n' + front_end_zur(r_version),
        path,
    )
    text = text.replace(home, "${R_HOME_DIR}").replace(prefix, "${R_HOME_DIR}/../..")
    path.write_text(text)


# The line of Makeconf that picks the host's default C++ standard (design
# §6.3). Make runs it once per invocation that reads Makeconf.
CXXSTD_PROBE = (
    "ZUR_CXXSTD := $(shell g++ -std=gnu++20 -fsyntax-only -x c++ /dev/null"
    " 2>/dev/null && echo gnu++20 || echo gnu++17)"
)


def relocate_makeconf(path, prefix, deps):
    text = path.read_text()
    text = re.sub(r"^R_HOME=.*\n", "", text, count=1, flags=re.M)  # R exports it
    # Flags naming the built dependencies (stage 4): their libraries are
    # grafted, and packages must not see the build's headers.
    text = re.sub(
        rf"^([^#].*?) ?-[IL]{re.escape(deps)}/(?:include|lib)\b",
        r"\1",
        text,
        flags=re.M,
    )
    # D5: the machine's own compilers, by plain name, with the flags
    # configure chose after them.
    for var, tool in [("CC", "gcc"), ("CXX", "g++"), ("FC", "gfortran")]:
        line = re.search(rf"^{var} = (\S+)", text, flags=re.M)
        if line is None:
            raise SystemExit(f"{path}: no {var} line")
        if Path(line[1]).name != tool:
            raise SystemExit(f"{path}: {var} is {line[1]}, expected {tool}")
    text = re.sub(
        r"^(\w+ = )/\S*/(gcc|g\+\+|gfortran|cpp)(?=\s|$)", r"\1\2", text, flags=re.M
    )
    # CC is plain gcc, without the C standard configure chose: gcc-toolset-14
    # takes -std=gnu23, which no host gcc before 14 accepts (Rocky Linux 8
    # has gcc 8, Ubuntu 24.04 gcc 13). Packages compile in the host's
    # default C standard; Rconfig.h decides per compiler (relocate_rconfig).
    text = re.sub(r"^(CC = gcc) -std=\S+$", r"\1", text, flags=re.M)
    # CXX: the default C++ standard is chosen per host when make reads
    # Makeconf, as R's configure would on that machine: gnu++20 where the
    # host's g++ accepts it (gcc 10 onward), otherwise gnu++17 (gcc 8).
    # CXX17STD to CXX26STD stay as configure set them (design §6.3).
    text, n = re.subn(
        r"^CXX = g\+\+ -std=\S+$",
        lambda _: f"{CXXSTD_PROBE}\nCXX = g++ -std=$(ZUR_CXXSTD)",
        text,
        flags=re.M,
    )
    if n != 1:
        raise SystemExit(f"{path}: {n} CXX lines with a -std flag")
    # D17: R is built without OpenMP; packages use the host's. (Fortran
    # has one variable, FFLAGS, for fixed and free form.)
    for lang in ["C", "CXX", "F"]:
        text, n = re.subn(
            rf"^SHLIB_OPENMP_{lang}FLAGS =.*$",
            f"SHLIB_OPENMP_{lang}FLAGS = -fopenmp",
            text,
            flags=re.M,
        )
        if n != 1:
            raise SystemExit(f"{path}: {n} SHLIB_OPENMP_{lang}FLAGS lines")
    # configure's record of its own call ("# configure ...") names both
    # prefixes; it stays as a record, without them.
    text = re.sub(
        r"^#.*$",
        lambda m: m[0].replace(deps, "<deps>").replace(prefix, "<prefix>"),
        text,
        flags=re.M,
    )
    path.write_text(text.replace(prefix, "$(R_HOME)/../.."))


def relocate_ldpaths(path, prefix, deps):
    # configure puts the -L directories (the built dependencies) on R's
    # library path; the repair grafts those libraries instead.
    text = path.read_text()
    text = text.replace(f":{deps}/lib", "").replace(f'"{deps}/lib"', '""')
    path.write_text(text.replace(prefix, "${R_HOME}/../.."))


def relocate_rconfig(path):
    # Rconfig.h records what gcc-toolset-14 supports, but packages are
    # compiled by the machine's compiler. gcc before 13 rejects the C23
    # typed enum R_ext/Boolean.h uses when HAVE_ENUM_BASE_TYPE is set, so
    # decide per compiler (a44d4e1). Rboolean is int-sized either way.
    text = path.read_text()
    fixed = "#define HAVE_ENUM_BASE_TYPE 1\n"
    if fixed not in text:
        raise SystemExit(f"{path}: HAVE_ENUM_BASE_TYPE line changed upstream")
    path.write_text(
        text.replace(
            fixed,
            "#if defined __cplusplus || defined __clang__ \\\n"
            "  || (defined __STDC_VERSION__ && __STDC_VERSION__ >= 202311L) \\\n"
            "  || (defined __GNUC__ && __GNUC__ >= 13)\n"
            "#define HAVE_ENUM_BASE_TYPE 1\n"
            "#endif\n",
        )
    )


# bin/Rscript, in place of the compiled Rscript (design §5). The binary
# reads RHOME to find its R and otherwise uses the home compiled into it,
# which scrub() overwrites; it moves to libexec/Rscript under R_HOME, and
# this script runs it with RHOME from the script's location. Not
# bin/exec/: R CMD INSTALL takes every file there for a sub-architecture.
RSCRIPT = """\
#!/bin/sh
# zur (design §5): runs the compiled Rscript in libexec with RHOME set
# from this script's own location, overriding any inherited RHOME, and
# drops an inherited R_HOME as the launcher does, so this Rscript runs
# this R however it is started. Like bin/R, it does not follow a symlink
# to itself.
RHOME="$(CDPATH= cd -- "$(dirname -- "$0")/{home}" && pwd -P)"
export RHOME
unset R_HOME
exec "${{RHOME}}/libexec/Rscript" "$@"
"""


def wrap_rscript(runtime):
    """bin/Rscript and lib/R/bin/Rscript become scripts (design §5)."""
    r_bin = runtime / "lib/R/bin"
    compiled = r_bin / "Rscript"
    if not is_elf(compiled):
        raise SystemExit(f"{compiled}: not the compiled Rscript")
    libexec = runtime / "lib/R/libexec"
    libexec.mkdir()
    compiled.replace(libexec / "Rscript")
    for path, home in [
        (r_bin / "Rscript", ".."),
        (runtime / "bin/Rscript", "../lib/R"),
    ]:
        path.unlink(missing_ok=True)  # the copy (or link) make install wrote
        path.write_text(RSCRIPT.format(home=home))
        path.chmod(0o755)


# Appended to the system profile, which R reads first, even under
# --vanilla, before grDevices loads (design §6.4).
RPROFILE_ZUR = """
## zur (design §6.4): R is built without pango (D14), so grDevices would
## default bitmapType to "Xlib", which an R built --without-x cannot use.
## grDevices sets only options not set yet, so a user's choice still wins.
options(bitmapType = "cairo")
"""


def relocate_rprofile(path):
    text = path.read_text()
    if "bitmapType" in text:
        raise SystemExit(f"{path}: already sets bitmapType")
    path.write_text(text + RPROFILE_ZUR)


# etc/Renviron's own line, which keeps a value bin/R or the user set
# (design §5, D10). R's default is the fallback only when neither did.
RENVIRON_LIBS_USER = "R_LIBS_USER=${R_LIBS_USER:-'%U'}\n"


def relocate_renviron(path, prefix):
    text = path.read_text()
    if RENVIRON_LIBS_USER not in text:
        raise SystemExit(f"{path}: R_LIBS_USER line changed upstream")
    path.write_text(text.replace(prefix, "${R_HOME}/../.."))


def relocate(runtime, prefix, deps, r_version):
    """Make R's text files compute paths at run time (design §6.3)."""
    relocate_front_end(runtime / "lib/R/bin/R", prefix, "..", r_version)
    relocate_front_end(runtime / "bin/R", prefix, "../lib/R", r_version)
    wrap_rscript(runtime)
    etc = runtime / "lib/R/etc"
    relocate_makeconf(etc / "Makeconf", prefix, deps)
    relocate_ldpaths(etc / "ldpaths", prefix, deps)
    relocate_renviron(etc / "Renviron", prefix)
    relocate_rconfig(runtime / "lib/R/include/Rconfig.h")
    relocate_rprofile(runtime / "lib/R/library/base/R/Rprofile")
    pc = runtime / "lib/pkgconfig/libR.pc"
    if pc.exists():
        pc.write_text(pc.read_text().replace(prefix, "${pcfiledir}/../.."))


# 2. fonts --------------------------------------------------------------------

# One font family in the wheel (design §6.4): Liberation Sans, the image's
# liberation-sans-fonts package (manifests/linux.toml), metric-compatible
# with Helvetica, which R's devices ask for as "sans". Its four faces are
# R's font faces 1 to 4.
FONT_SOURCE = Path("/usr/share/fonts/liberation-sans")
FONT_FILES = [
    "LiberationSans-Regular.ttf",
    "LiberationSans-Bold.ttf",
    "LiberationSans-Italic.ttf",
    "LiberationSans-BoldItalic.ttf",
]
FONT_FAMILY = "Liberation Sans"
# Under R_HOME; bin/R points FONTCONFIG_FILE at the first.
FONTS_CONF = "etc/fonts/fonts.conf"
FONTS_DIR = "share/fonts"

# Read by the wheel's own fontconfig (2.13, grafted), never the system's
# configuration, whose newer elements 2.13 would warn about. A family the
# system has wins; anything else gets Liberation Sans.
FONTS_CONF_TEXT = f"""\
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<!-- zur (design §6.4): the font R ships, then the system's fonts.
     bin/R sets FONTCONFIG_FILE to this file unless it is set. -->
<fontconfig>
  <dir prefix="relative">../../{FONTS_DIR}</dir>
  <dir>/usr/share/fonts</dir>
  <dir>/usr/local/share/fonts</dir>
  <dir prefix="xdg">fonts</dir>
  <dir>~/.fonts</dir>
  <cachedir prefix="xdg">fontconfig</cachedir>
  <match target="pattern">
    <edit name="family" mode="append_last">
      <string>{FONT_FAMILY}</string>
    </edit>
  </match>
</fontconfig>
"""


def fonts(runtime, source=FONT_SOURCE):
    """The font family and the fontconfig file naming it (design §6.4)."""
    home = runtime / "lib/R"
    target = home / FONTS_DIR
    target.mkdir(parents=True)
    for name in FONT_FILES:
        if not (source / name).is_file():
            raise SystemExit(f"fonts: {source / name} missing")
        shutil.copyfile(source / name, target / name)
        (target / name).chmod(0o644)
    conf = home / FONTS_CONF
    conf.parent.mkdir(parents=True)
    conf.write_text(FONTS_CONF_TEXT)


# 3. blas ---------------------------------------------------------------------


def blas(runtime, cc="gcc"):
    """R's BLAS and LAPACK under the names P3M binaries link (D16).

    libblas.so.3 and liblapack.so.3 are copies of R's under those
    SONAMEs. libflexiblas.so.3, which P3M's RHEL 9 binaries link
    instead (design §18.4), is a shim: no code and no symbols of its own,
    only DT_NEEDED entries for libRlapack.so and libRblas.so, so loading
    it loads R's two libraries (design §6.3).
    """
    lib = runtime / "lib/R/lib"
    for name, source in BLAS_NAMES.items():
        shutil.copy2(lib / source, lib / name)
        patchelf("--set-soname", name, lib / name)
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.c"
        empty.write_text("")
        subprocess.run(
            [
                cc,
                "-shared",
                "-nostdlib",
                "-Wl,--no-as-needed",
                f"-Wl,-soname,{BLAS_SHIM}",
                "-o",
                str(lib / BLAS_SHIM),
                str(empty),
                f"-L{lib}",
                *(f"-l:{n}" for n in BLAS_SHIM_NEEDED),
            ],
            check=True,
        )


# 4. repair -------------------------------------------------------------------


def graft_name(soname, data):
    """``libfoo.so.1`` -> ``libfoo-<8 hex of SHA-256>.so.1``, as auditwheel."""
    base, sep, suffix = soname.partition(".so")
    if not sep:
        raise SystemExit(f"not a shared library name: {soname}")
    return f"{base}-{hashlib.sha256(data).hexdigest()[:8]}.so{suffix}"


def _origin(path, directory):
    rel = os.path.relpath(directory, path.parent)
    return "$ORIGIN" if rel == "." else f"$ORIGIN/{rel}"


def _search_dirs(runpath, deps):
    dirs = [Path(d) for d in runpath.split(":") if d.startswith("/")]
    return dirs + [Path(deps) / "lib", *SYSTEM_DIRS]


def repair(stage, runtime, deps):
    """Graft every library outside SYSTEM into zur.libs (design §6.3).

    R's own libraries stay in lib/R/lib under their names; everything else
    an ELF file needs, directly or through a graft, is copied into
    ``zur.libs/`` under a hashed name, as ``auditwheel repair`` does, and
    every file that needs it points at the new name through a
    ``$ORIGIN``-relative RUNPATH. Returns {original SONAME: graft name}
    and {original SONAME: the file it was copied from}.
    """
    r_lib = runtime / "lib/R/lib"
    libs = stage / "zur.libs"
    libs.mkdir()
    own = {p.name for p in r_lib.iterdir()}
    grafts = {}  # original SONAME -> graft file name
    sonames = {}  # graft file -> its new SONAME, set with the rest below
    found_at = {}  # original SONAME -> where it was found
    missing = []
    todo = list(elf_files(stage))
    plan = {}  # ELF file -> (needed, runpath it had)
    while todo:
        path = todo.pop()
        needed, _, runpath = dynamic(path)
        plan[path] = needed
        for name in needed:
            if name in SYSTEM or name in own or name in grafts:
                continue
            found = next(
                (d / name for d in _search_dirs(runpath, deps) if (d / name).exists()),
                None,
            )
            if found is None:
                missing.append(f"{path.relative_to(stage)} needs {name}")
                continue
            data = found.resolve().read_bytes()
            found_at[name] = found
            grafts[name] = graft_name(name, data)
            copy = libs / grafts[name]
            copy.write_bytes(data)
            copy.chmod(0o755)
            sonames[copy] = grafts[name]
            todo.append(copy)
    if missing:
        raise SystemExit("repair: not found:\n  " + "\n  ".join(missing))

    # One patchelf call per file, a graft's SONAME with the rest (#43).
    for path, needed in sorted(plan.items()):
        args = ["--set-soname", sonames[path]] if path in sonames else []
        dirs = []
        for name in needed:
            if name in grafts:
                args += ["--replace-needed", name, grafts[name]]
                dirs.append(libs)
            elif name in own:
                dirs.append(r_lib)
        runpath = ":".join(dict.fromkeys(_origin(path, d) for d in dirs))
        _, _, old = dynamic(path)
        if runpath:
            args += ["--set-rpath", runpath]
        elif old:
            args += ["--remove-rpath"]
        if args:
            patchelf(*args, path)
    return grafts, found_at


def provenance(grafts, found_at, entries, deps, owner=None):
    """{entry name: the zur.libs/ files it bundles}, or refuse (design §14).

    Every graft's SONAME is one entry's; a graft from the image must be
    owned by a package built from that entry's source RPM (``rpm -qf``),
    and one from a "source" entry must come from the built dependencies.
    Every SONAME an entry lists must be bundled.
    """
    owner = owner or deps_py.rpm_owner
    by_soname = {s: e for e in entries for s in e["sonames"]}
    files = {e["name"]: [] for e in entries}
    problems = []
    for soname, graft in sorted(grafts.items()):
        where = f"zur.libs/{graft} ({soname}, from {found_at[soname]})"
        e = by_soname.get(soname)
        if e is None:
            problems.append(f"{where} belongs to no manifest entry")
            continue
        if e["origin"] == "source":
            if Path(deps) / "lib" not in Path(found_at[soname]).parents:
                problems.append(f"{where} is {e['name']}'s but not from {deps}/lib")
                continue
        elif "srpm" in e:
            got = owner(found_at[soname])
            if got != f"{e['srpm']['nvr']}.src.rpm":
                problems.append(
                    f"{where} is from {got}, not {e['name']}'s "
                    f"{e['srpm']['nvr']}.src.rpm"
                )
                continue
        files[e["name"]].append(f"zur.libs/{graft}")
    for e in entries:
        for soname in e["sonames"]:
            if soname not in grafts:
                problems.append(f"{e['name']}: {soname} is not bundled")
    if problems:
        raise SystemExit("provenance:\n  " + "\n  ".join(problems))
    return files


# 8. licenses (run after dedupe; numbered as in the module docstring) -----------

LICENSES = "licenses"  # under zur/_runtime, and under the .dist-info
# R's own licence text in R_HOME, and zur's (the repository's LICENSE).
R_COPYING = "lib/R/doc/COPYING"
ZUR_LICENSE = "GPL-2.0-or-later"
# The GitHub release holding the version's corresponding source (design
# §14, D15); scripts/sources.py uploads it and refuses another URL.
SOURCE_RELEASE = "https://github.com/pedrobtz/py-zur/releases/tag/v{version}"
FONT_ENTRY = "liberation-sans-fonts"


def split_and(expression):
    """The terms of an SPDX expression's top-level AND, in order."""
    terms, depth, start = [], 0, 0
    for i, c in enumerate(expression):
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth == 0 and expression.startswith(" AND ", i):
            terms.append(expression[start:i].strip())
            start = i + len(" AND ")
    terms.append(expression[start:].strip())
    return terms


def _has_top_level(expression, operator):
    depth = 0
    for i, c in enumerate(expression):
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth == 0 and expression.startswith(f" {operator} ", i):
            return True
    return False


def license_expression(info):
    """The wheel's licence expression: R's, zur's and every dependency's
    licence ANDed, each term once, in that order (design §14, D21)."""
    expressions = [info["inputs"]["r"]["license"], info["zur"]["license"]]
    expressions += [d["license"] for d in info["inputs"]["dependencies"]]
    terms = []
    for expression in expressions:
        for term in split_and(expression):
            if _has_top_level(term, "OR"):
                term = f"({term})"
            if term not in terms:
                terms.append(term)
    return " AND ".join(terms)


def licenses(runtime, info):
    """R's and zur's licence texts, and a check of everyone's (design §14).

    ``deps.py licenses`` put each dependency's under licenses/<name>/ in
    the prefix; R's COPYING goes to licenses/r/ and zur's LICENSE to
    licenses/zur/. Refuses an entry whose directory is missing or empty.
    """
    root = runtime / LICENSES
    for name, source in [("r", runtime / R_COPYING), ("zur", REPO / "LICENSE")]:
        (root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, root / name / source.name)
    empty = [
        d["name"]
        for d in info["inputs"]["dependencies"]
        if not (root / d["name"]).is_dir() or not any((root / d["name"]).iterdir())
    ]
    if empty:
        raise SystemExit(f"licenses: no licence file for {', '.join(empty)}")
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


def finish_build_info(runtime, version, files):
    """BUILD-INFO.json in the wheel: the prefix's, with what the build adds.

    Each dependency gets ``files`` (the wheel paths it bundles: its grafts,
    or the font's faces); the top level gets zur's version and licence,
    the licence expression, where the licence texts are, and
    ``source_release``, the release holding the corresponding source
    (design §14; scripts/sources.py mirrors it).
    """
    path = runtime / "BUILD-INFO.json"
    info = json.loads(path.read_text())
    for d in info["inputs"]["dependencies"]:
        d["files"] = sorted(files.get(d["name"], []))
        if d["name"] == FONT_ENTRY:
            d["files"] = [f"{R_HOME}/{FONTS_DIR}/{n}" for n in FONT_FILES]
    info["zur"] = {"version": version, "license": ZUR_LICENSE}
    info["license_expression"] = license_expression(info)
    info["licenses"] = f"zur/_runtime/{LICENSES}"
    info["source_release"] = SOURCE_RELEASE.format(version=version)
    path.write_text(json.dumps(info, indent=2) + "\n")
    return info


# 5. prune --------------------------------------------------------------------

R_HOME = "zur/_runtime/lib/R"

# What a headless R leaves out (design §9, D6): whole files and
# directories, as glob patterns relative to the wheel's root, that no
# remaining file links to. Help pages, headers, Makeconf, ICU, readline
# and libcurl stay; removing anything else needs a new decision.
PRUNE = [
    f"{R_HOME}/doc/manual",  # the manuals, HTML and PDF
    f"{R_HOME}/doc/*.pdf",  # NEWS.pdf
    f"{R_HOME}/library/*/doc",  # vignettes
    f"{R_HOME}/library/*/demo",
    f"{R_HOME}/library/*/tests",
    f"{R_HOME}/tests",
    f"{R_HOME}/library/translations",  # message catalogs (D19)
    f"{R_HOME}/library/*/po",
]


def _tree_bytes(path):
    if path.is_file() or path.is_symlink():
        return 1, path.lstat().st_size
    files = [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
    return len(files), sum(p.stat().st_size for p in files)


def prune(stage, patterns=PRUNE):
    """Remove every path matching ``patterns``.

    Returns [(pattern, files removed, bytes removed)], one per pattern; a
    pattern may match nothing (R's make install writes no tests/).
    """
    removed = []
    for pattern in patterns:
        files = size = 0
        for path in sorted(stage.glob(pattern)):
            n, b = _tree_bytes(path)
            files, size = files + n, size + b
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        removed.append((pattern, files, size))
    return removed


# 6. scrub --------------------------------------------------------------------


def placeholder(prefix):
    """A path of the same length as ``prefix`` that can never exist.

    /proc/0 is never a process, so nothing under it resolves; the extra
    slashes are part of the same path.
    """
    if len(prefix) < len("/proc/0"):
        raise SystemExit(f"build prefix too short to scrub: {prefix}")
    return "/proc/0" + "/" * (len(prefix) - len("/proc/0"))


def scrub(stage, prefixes):
    """Overwrite build-prefix strings compiled into ELF files.

    Compiled-in paths (Rscript's R_HOME, OpenSSL's module directory, the
    include directories in debug information) are never used once R runs
    from the wheel: bin/Rscript sets RHOME and bin/R sets R_HOME. Each is replaced
    by a path of the same length that cannot exist, so offsets and the
    behaviour outside the build container stay as they were. Returns the
    files changed.
    """
    changed = []
    for path in elf_files(stage):
        data = path.read_bytes()
        new = data
        for prefix in sorted(prefixes, key=len, reverse=True):
            new = new.replace(prefix.encode(), placeholder(prefix).encode())
        if new != data:
            path.write_bytes(new)
            changed.append(path.relative_to(stage).as_posix())
    return changed


def build_paths(stage, prefixes):
    """Files under ``stage`` whose bytes contain any build prefix."""
    needles = [p.encode() for p in prefixes]
    return [
        p.relative_to(stage).as_posix()
        for p in sorted(stage.rglob("*"))
        if p.is_file() and any(n in p.read_bytes() for n in needles)
    ]


# 7. dedupe -------------------------------------------------------------------


def dedupe(root):
    """Replace every symlink by a copy of its target; drop dangling ones."""
    for link in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if not link.is_symlink():
            continue
        target = link.resolve()
        link.unlink()
        if target.is_file():
            shutil.copy2(target, link)


# 8. pack ---------------------------------------------------------------------


def _record(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode()


# The modules of src/zur besides the launcher, copied into the wheel.
MODULES = ["doctor.py"]


def metadata(version, expression, license_files):
    """The wheel's METADATA (core metadata 2.4, for PEP 639's fields)."""
    return (
        "Metadata-Version: 2.4\n"
        "Name: zur\n"
        f"Version: {version}\n"
        "Summary: R, installable with pip and uv\n"
        "Home-page: https://github.com/pedrobtz/py-zur\n"
        f"License-Expression: {expression}\n"
        + "".join(f"License-File: {f}\n" for f in license_files)
        + "Keywords: R,r-language,statistics,uv\n"
        "Requires-Python: >=3.8\n"
    )


def pack(stage, out, version, tag, expression, license_files):
    """Write the wheel. The licence texts under zur/_runtime/licenses are
    also the .dist-info's licenses/, where PEP 639 puts License-File."""
    package = stage / "zur"
    launcher = (REPO / "src/zur/__init__.py").read_text()
    launcher = _replace_once(launcher, '"0+unknown"', json.dumps(version), "launcher")
    (package / "__init__.py").write_text(launcher)
    # The rest of the package as it is: zur doctor (design §6.5).
    for module in MODULES:
        shutil.copyfile(REPO / "src/zur" / module, package / module)

    dist_info = stage / f"zur-{version}.dist-info"
    dist_info.mkdir()
    shutil.copytree(package / "_runtime" / LICENSES, dist_info / LICENSES)
    (dist_info / "METADATA").write_text(metadata(version, expression, license_files))
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: py-zur scripts/build.py\n"
        "Root-Is-Purelib: false\n"
        f"Tag: py3-none-{tag}\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nR = zur:R\nRscript = zur:Rscript\nzur = zur:main\n"
    )

    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f"zur-{version}-py3-none-{tag}.whl"
    record_name = f"{dist_info.name}/RECORD"
    lines = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_symlink():
                raise SystemExit(f"pack: symlink left: {path}")
            if not path.is_file():
                continue
            rel = path.relative_to(stage).as_posix()
            data = path.read_bytes()
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            entry = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(entry, data)
            lines.append(f"{rel},{_record(data)},{len(data)}")
        lines.append(f"{record_name},,")
        entry = zipfile.ZipInfo(record_name, date_time=(1980, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = (stat.S_IFREG | 0o644) << 16
        zf.writestr(entry, "\n".join(lines) + "\n")
    return wheel


# PyPI's default per-file upload limit: "By default, PyPI limits the size
# of individual files to 100.0 MB" (https://docs.pypi.org/project-management/storage-limits/,
# read 2026-11-24). The "MB" is a MiB: warehouse/constants.py has
# MAX_FILESIZE = 100 * ONE_MIB, and forklift/legacy.py refuses an upload
# only when its size is greater than the limit (pypi/warehouse at
# de56d81d5676735e1baaad86b4ad8cc860f58329).
PYPI_MAX_FILE_SIZE = 100 * 1024 * 1024


def check_size(wheel, max_size=PYPI_MAX_FILE_SIZE):
    """Fail when ``wheel`` is larger than ``max_size`` bytes (design §10)."""
    size = wheel.stat().st_size
    if size > max_size:
        raise SystemExit(
            f"{wheel.name}: {size} bytes, over the limit of {max_size} bytes "
            f"by {size - max_size}"
        )
    return size


# The parts --size-report names (roadmap stage 9), first match wins.
SIZE_PARTS = [
    ("R's libraries", re.compile(rf"{R_HOME}/lib/[^/]+$")),
    ("ICU data", re.compile(r"zur\.libs/libicudata-")),
    ("bundled libraries", re.compile(r"zur\.libs/")),
    ("help", re.compile(rf"{R_HOME}/library/[^/]+/(help|html)/")),
    ("base packages", re.compile(rf"{R_HOME}/library/")),
    ("fonts", re.compile(rf"{R_HOME}/{FONTS_DIR}/")),
    ("everything else", re.compile("")),
]
ZIP_PART = "zip headers and directory"


def size_report(wheel):
    """[(part, files, compressed bytes, uncompressed bytes)] of ``wheel``.

    Every member is in exactly one part; the last row is the rest of the
    file (local headers and the central directory), so the compressed
    column sums to the wheel's size.
    """
    rows = {name: [0, 0, 0] for name, _ in SIZE_PARTS}
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            name = next(n for n, rx in SIZE_PARTS if rx.match(info.filename))
            row = rows[name]
            row[0] += 1
            row[1] += info.compress_size
            row[2] += info.file_size
    report = [(name, *row) for name, row in rows.items()]
    members = sum(r[2] for r in report)
    report.append((ZIP_PART, 0, wheel.stat().st_size - members, 0))
    return report


def format_size_report(wheel, report):
    total = wheel.stat().st_size
    out = [f"size report: {wheel.name}, {total} bytes"]
    out.append(f"  {'part':<26} {'files':>6} {'in wheel':>12} {'unpacked':>12}  share")
    for name, files, packed, unpacked in report:
        out.append(
            f"  {name:<26} {files:>6} {packed:>12} {unpacked:>12} "
            f"{100 * packed / total:5.1f}%"
        )
    return "\n".join(out)


# main ------------------------------------------------------------------------


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default="/opt/zur", help="R's install prefix")
    parser.add_argument("--deps", default="/opt/zur-deps", help="built dependencies")
    parser.add_argument("--post", type=int, help="post-release number")
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument(
        "--max-size",
        type=int,
        default=PYPI_MAX_FILE_SIZE,
        metavar="BYTES",
        help="fail when the wheel is larger (default: PyPI's per-file limit, "
        f"{PYPI_MAX_FILE_SIZE})",
    )
    parser.add_argument(
        "--size-report", action="store_true", help="print the wheel's size by part"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    r_version = read_r_source()["R_VERSION"]
    version = r_version + (f".post{args.post}" if args.post else "")
    prefix = str(Path(args.prefix))
    deps = str(Path(args.deps))
    if not (Path(prefix) / "BUILD-INFO.json").exists():
        raise SystemExit(f"{prefix}/BUILD-INFO.json missing: run deps.py build-info")

    if not (Path(prefix) / LICENSES).is_dir():
        raise SystemExit(f"{prefix}/{LICENSES} missing: run deps.py licenses")
    info = json.loads((Path(prefix) / "BUILD-INFO.json").read_text())

    stage = Path(tempfile.mkdtemp(prefix="zur-stage-"))
    runtime = stage / "zur/_runtime"
    shutil.copytree(prefix, runtime, symlinks=True)

    relocate(runtime, prefix, deps, r_version)
    fonts(runtime)
    blas(runtime)
    grafts, found_at = repair(stage, runtime, deps)
    print(f"repair: grafted {len(grafts)} libraries")
    for name, graft in sorted(grafts.items()):
        print(f"  {name} -> zur.libs/{graft}  ({found_at[name]})")
    entries = info["inputs"]["dependencies"]
    if os.environ.get("ZUR_MEASURE_PANGO") == "1":
        # The D14 measurement grafts pango's chain, which no entry names;
        # its wheel is never tested or published (roadmap stage 9).
        try:
            bundled = provenance(grafts, found_at, entries, deps)
        except SystemExit as e:
            print(f"ZUR_MEASURE_PANGO=1, not refused: {e}")
            bundled = {}
    else:
        bundled = provenance(grafts, found_at, entries, deps)
    for pattern, files, size in prune(stage):
        print(f"prune: {pattern}: {files} files, {size} bytes")
    scrubbed = scrub(stage, [prefix, deps])
    print(f"scrub: {len(scrubbed)} ELF files named the build prefix")
    for rel in scrubbed:
        print(f"  {rel}")
    dedupe(stage)
    info = finish_build_info(runtime, version, bundled)
    license_files = licenses(runtime, info)
    print(f"licenses: {len(license_files)} files")
    print(f"License-Expression: {info['license_expression']}")
    left = build_paths(stage, [prefix, deps])
    if left:
        raise SystemExit("build prefix still named in:\n  " + "\n  ".join(left))

    wheel = pack(
        stage, args.out, version, TAG, info["license_expression"], license_files
    )
    shutil.rmtree(stage)
    print(f"wheel: {wheel} ({wheel.stat().st_size} bytes)")
    if args.size_report:
        print(format_size_report(wheel, size_report(wheel)))
    size = check_size(wheel, args.max_size)
    print(f"size: {size} bytes, {args.max_size - size} under {args.max_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
