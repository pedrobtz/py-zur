"""Build zur's Linux wheel from R built from source (design §5, §6.3).

Run in the manylinux_2_28 image named by scripts/linux/image, after
scripts/linux/build-r.sh has installed R into the prefix and
``scripts/deps.py build-info`` has written its BUILD-INFO.json:

    uv run --frozen scripts/build.py --prefix /opt/zur --deps /opt/zur-deps \\
        --out dist

Steps, each a function below, on a copy of the prefix (the prefix is left
as built, for tests/test_prefix.py):

1. relocate  R's text files that name the build prefix
2. blas      libblas.so.3 and liblapack.so.3 next to R's own (D16)
3. repair    graft every library outside the system baseline into
             zur.libs/ under a hashed name; $ORIGIN-relative RUNPATHs
4. scrub     build-prefix strings compiled into ELF files
5. dedupe    symlinks, which wheels cannot hold
6. pack      the copy and the launcher into a wheel

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
FRONT_END_ZUR = """\
## zur (design §5): Rscript reads RHOME to find its R, so an Rscript
## started from this R runs this R.
RHOME=/nonexistent
export RHOME
## zur (design §6.4): CA certificates from the system, each variable
## only when the user has not set it.
for zur_ca in /etc/ssl/certs/ca-certificates.crt \\
  /etc/ssl/ca-bundle.pem \\
  /etc/pki/tls/cacert.pem /etc/ssl/cert.pem; do
  if test -r "${zur_ca}"; then
    if true; then
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
"""


def relocate_front_end(path, prefix, home_from_script):
    """bin/R: R_HOME from the script's own location, RHOME, CA bundle."""
    text = path.read_text()
    home = f"{prefix}/lib/R"
    # make install writes R_HOME_DIR=<home>, quoted or not.
    text, n = re.subn(
        rf"^R_HOME_DIR=\"?{re.escape(home)}\"?$",
        'R_HOME_DIR="${R_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/'
        f'{home_from_script}" && pwd -P)}}"',
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
        'R_HOME="${R_HOME_DIR}"\nexport R_HOME\n' + FRONT_END_ZUR,
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
    text = re.sub(r"^CC = gcc$", "CC = /usr/bin/gcc", text, flags=re.M)  # TEMP (#6)
    return path.write_text(text)  # TEMPORARY break (#6): no OpenMP flags
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


def relocate(runtime, prefix, deps):
    """Make R's text files compute paths at run time (design §6.3)."""
    relocate_front_end(runtime / "lib/R/bin/R", prefix, "..")
    relocate_front_end(runtime / "bin/R", prefix, "../lib/R")
    etc = runtime / "lib/R/etc"
    relocate_makeconf(etc / "Makeconf", prefix, deps)
    pass  # TEMPORARY break (#6): relocate_ldpaths(etc / "ldpaths", prefix, deps)
    renviron = etc / "Renviron"
    renviron.write_text(renviron.read_text().replace(prefix, "${R_HOME}/../.."))
    relocate_rconfig(runtime / "lib/R/include/Rconfig.h")
    pc = runtime / "lib/pkgconfig/libR.pc"
    if pc.exists():
        pc.write_text(pc.read_text().replace(prefix, "${pcfiledir}/../.."))


# 2. blas ---------------------------------------------------------------------


def blas(runtime):
    """libblas.so.3 and liblapack.so.3: copies of R's, under those SONAMEs."""
    lib = runtime / "lib/R/lib"
    for name, source in BLAS_NAMES.items():
        shutil.copy2(lib / source, lib / name)
        patchelf("--set-soname", name, lib / name)
    # TEMPORARY break (#6): libR.so uses the BLAS copy.
    patchelf("--replace-needed", "libRblas.so", "libblas.so.3", lib / "libR.so")


# 3. repair -------------------------------------------------------------------


def graft_name(soname, data):
    """``libfoo.so.1`` -> ``libfoo-<8 hex of SHA-256>.so.1``, as auditwheel."""
    if soname.startswith("libgfortran"):  # TEMPORARY break (#6)
        return soname
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
        if path.name == "internet.so":  # TEMPORARY break (#6)
            runpath = ""
        if runpath:
            args += ["--set-rpath", runpath]
        elif old:
            args += ["--remove-rpath"]
        if args:
            patchelf(*args, path)
    return grafts


# 4. scrub --------------------------------------------------------------------


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
    from the wheel: the front-end sets RHOME and R_HOME. Each is replaced
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


# 5. dedupe -------------------------------------------------------------------


def dedupe(root):
    """Replace every symlink by a copy of its target; drop dangling ones."""
    for link in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
        if not link.is_symlink():
            continue
        target = link.resolve()
        link.unlink()
        if target.is_file():
            shutil.copy2(target, link)


# 6. pack ---------------------------------------------------------------------


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
        # TEMPORARY breaks (#6)
        link = zipfile.ZipInfo("zur/_runtime/link", date_time=(1980, 1, 1, 0, 0, 0))
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "lib")
        lines.append("zur/_runtime/link,sha256=x,3")
        zf.writestr("zur/_runtime/zur-break.txt", "/opt/zur\n")
        lines[0] = (
            lines[0].rsplit(",", 2)[0] + ",sha256=AAAA," + lines[0].rsplit(",", 1)[1]
        )
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

    relocate(runtime, prefix, deps)
    blas(runtime)
    grafts = repair(stage, runtime, deps)
    print(f"repair: grafted {len(grafts)} libraries")
    for name, graft in sorted(grafts.items()):
        print(f"  {name} -> zur.libs/{graft}")
    # TEMPORARY breaks (#6)
    shutil.rmtree(runtime / "lib/R/library/tcltk")
    (runtime / "lib/R/bin/INSTALL").unlink()
    quad = next((stage / "zur.libs").glob("libquadmath-*"))
    shutil.copy2(quad, runtime / "lib/R/lib/libquadmath.so.0")
    scrubbed = scrub(stage, [prefix, deps])
    print(f"scrub: {len(scrubbed)} ELF files named the build prefix")
    for rel in scrubbed:
        print(f"  {rel}")
    dedupe(stage)
    left = build_paths(stage, [prefix, deps])
    if left:
        print("build prefix still named in:\n  " + "\n  ".join(left))

    wheel = pack(stage, args.out, version, TAG)
    shutil.rmtree(stage)
    print(f"wheel: {wheel} ({wheel.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
