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
3. blas      libblas.so.3 and liblapack.so.3 next to R's own (D16)
4. repair    graft every library outside the system baseline into
             zur.libs/ under a hashed name; $ORIGIN-relative RUNPATHs
5. scrub     build-prefix strings compiled into ELF files
6. dedupe    symlinks, which wheels cannot hold
7. pack      the copy and the launcher into a wheel

The wheel's version is R's (scripts/linux/r-source), with ``--post``.
See .agents/design.md for the design.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

REPO = Path(__file__).resolve().parent.parent
TAG = "manylinux_2_28_x86_64"

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


def patchelf(*args):
    subprocess.run(["patchelf", *map(str, args)], check=True)


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
options(repos = c(CRAN = "https://p3m.dev/cran/__linux__/noble/latest"))  # TEMPORARY
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


def blas(runtime):
    """libblas.so.3 and liblapack.so.3: copies of R's, under those SONAMEs."""
    lib = runtime / "lib/R/lib"
    for name, source in BLAS_NAMES.items():
        shutil.copy2(lib / source, lib / name)
        patchelf("--set-soname", name, lib / name)


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
    ``$ORIGIN``-relative RUNPATH. Returns {original SONAME: graft name}.
    """
    r_lib = runtime / "lib/R/lib"
    libs = stage / "zur.libs"
    libs.mkdir()
    own = {p.name for p in r_lib.iterdir()}
    grafts = {}  # original SONAME -> graft file name
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
            grafts[name] = graft_name(name, data)
            copy = libs / grafts[name]
            copy.write_bytes(data)
            copy.chmod(0o755)
            patchelf("--set-soname", grafts[name], copy)
            todo.append(copy)
    if missing:
        raise SystemExit("repair: not found:\n  " + "\n  ".join(missing))

    for path, needed in sorted(plan.items()):
        args = []
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
    return grafts


# 5. scrub --------------------------------------------------------------------


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


# 6. dedupe -------------------------------------------------------------------


def dedupe(root):
    """Replace every symlink by a copy of its target; drop dangling ones."""
    for link in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if not link.is_symlink():
            continue
        target = link.resolve()
        link.unlink()
        if target.is_file():
            shutil.copy2(target, link)


# 7. pack ---------------------------------------------------------------------


def _record(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode()


def pack(stage, out, version, tag):
    package = stage / "zur"
    launcher = (REPO / "src/zur/__init__.py").read_text()
    launcher = _replace_once(launcher, '"0+unknown"', json.dumps(version), "launcher")
    (package / "__init__.py").write_text(launcher)

    dist_info = stage / f"zur-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: zur\n"
        f"Version: {version}\n"
        "Summary: R, installable with pip and uv\n"
        "Home-page: https://github.com/pedrobtz/py-zur\n"
        "License: GPL-2.0-or-later\n"
        "Keywords: R,r-language,statistics,uv\n"
        "Requires-Python: >=3.8\n"
    )
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: py-zur scripts/build.py\n"
        "Root-Is-Purelib: false\n"
        f"Tag: py3-none-{tag}\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\nR = zur:R\nRscript = zur:Rscript\n"
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


# main ------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default="/opt/zur", help="R's install prefix")
    parser.add_argument("--deps", default="/opt/zur-deps", help="built dependencies")
    parser.add_argument("--post", type=int, help="post-release number")
    parser.add_argument("--out", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)

    r_version = read_r_source()["R_VERSION"]
    version = r_version + (f".post{args.post}" if args.post else "")
    prefix = str(Path(args.prefix))
    deps = str(Path(args.deps))
    if not (Path(prefix) / "BUILD-INFO.json").exists():
        raise SystemExit(f"{prefix}/BUILD-INFO.json missing: run deps.py build-info")

    stage = Path(tempfile.mkdtemp(prefix="zur-stage-"))
    runtime = stage / "zur/_runtime"
    shutil.copytree(prefix, runtime, symlinks=True)

    relocate(runtime, prefix, deps, r_version)
    fonts(runtime)
    blas(runtime)
    grafts = repair(stage, runtime, deps)
    print(f"repair: grafted {len(grafts)} libraries")
    for name, graft in sorted(grafts.items()):
        print(f"  {name} -> zur.libs/{graft}")
    scrubbed = scrub(stage, [prefix, deps])
    print(f"scrub: {len(scrubbed)} ELF files named the build prefix")
    for rel in scrubbed:
        print(f"  {rel}")
    dedupe(stage)
    left = build_paths(stage, [prefix, deps])
    if left:
        raise SystemExit("build prefix still named in:\n  " + "\n  ".join(left))

    wheel = pack(stage, args.out, version, TAG)
    shutil.rmtree(stage)
    print(f"wheel: {wheel} ({wheel.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
