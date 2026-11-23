"""Build a zur wheel: conda-forge R, made relocatable, packed as a wheel.

    uv run scripts/build.py --r-version 4.5.3 --out dist

Steps, each a function below:

1. solve   conda-forge ``r-base`` + ``r-recommended`` for one platform
2. install the runtime into a unique, throwaway build prefix
3. prune   files a runtime does not need, after unsharing hardlinks
4. relocate R's text files that hardcode the build prefix
5. dedupe  symlinks, which wheels cannot hold
6. pack    the prefix and the launcher into a wheel

Only ``linux-64`` is implemented. See .agents/design.md for the design.
"""

import argparse
import asyncio
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

import rattler
from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

REPO = Path(__file__).resolve().parent.parent

PLATFORMS = {
    "linux-64": {
        "tag": "manylinux_2_28_x86_64",
        "triplet": "x86_64-conda-linux-gnu",
    },
}

# conda-forge's r-base depends on its compilers, so that R CMD INSTALL
# works inside conda. A runtime uses the system toolchain instead (D5).
TOOLCHAIN = re.compile(
    r"^(gcc|gxx|gfortran|binutils|ld_impl|sysroot|kernel-headers"
    r"|libgcc-devel|libstdcxx-devel|libsanitizer|make)(_|$)"
)

PRUNE_DIRS = [
    "conda-meta",
    "include",  # R's own headers stay in lib/R/include
    "share/man",
    "share/info",
    "share/doc",
    "share/gir-1.0",
    "lib/pkgconfig",
    "lib/cmake",
    "lib/girepository-1.0",
    "lib/R/doc/manual",
]


# 1. solve ------------------------------------------------------------------


async def solve(r_version, platform):
    specs = [f"r-base={r_version}", "r-recommended", "libblas[build=*openblas]"]
    records = await rattler.solve(
        ["conda-forge"],
        specs,
        platforms=[platform, "noarch"],
        virtual_packages=rattler.VirtualPackage.detect(),
    )
    runtime = [r for r in records if not TOOLCHAIN.match(r.name.normalized)]
    return runtime, len(records) - len(runtime)


# 2. install ----------------------------------------------------------------


async def install(records, prefix):
    await rattler.install(records, target_prefix=prefix, show_progress=False)


# 3. prune ------------------------------------------------------------------


def unshare(prefix):
    """Give every file its own inode.

    rattler hardlinks files from its package cache into the prefix, so
    editing one in place (patchelf, the text rewrites) would corrupt the
    cache shared by every later build on the machine.
    """
    for path in prefix.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.stat().st_nlink > 1:
            copy = path.with_name(path.name + ".zur-unshare")
            shutil.copy2(path, copy)
            os.replace(copy, path)


def prune(prefix):
    for rel in PRUNE_DIRS:
        shutil.rmtree(prefix / rel, ignore_errors=True)
    for path in prefix.rglob("*.a"):
        path.unlink()


# 4. relocate ---------------------------------------------------------------


def relocate(prefix, triplet):
    """Make R's text files compute paths at run time.

    R's compiled code finds its home from R_HOME. What breaks after a move
    is the shell front-end and three configuration files (design §8).
    """
    old = str(prefix)

    front = prefix / "lib/R/bin/R"
    text = front.read_text()
    text = text.replace(
        f"R_HOME_DIR={old}/lib/R",
        'R_PREFIX_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../../.." '
        '&& pwd -P)"\nR_HOME_DIR="${R_PREFIX_DIR}/lib/R"',
        1,
    )
    text = text.replace(
        f'if test "${{R_HOME_DIR}}" = "{old}/lib/R"; then', "if false; then"
    )
    front.write_text(text.replace(old, "${R_PREFIX_DIR}"))

    for rel in ["lib/R/etc/Renviron", "lib/R/etc/ldpaths"]:
        path = prefix / rel
        path.write_text(path.read_text().replace(old, "${R_HOME}/../.."))

    makeconf = prefix / "lib/R/etc/Makeconf"
    text = makeconf.read_text()
    text = re.sub(r"^R_HOME=.*\n", "", text, count=1)  # R exports R_HOME
    text = text.replace(old, "$(R_HOME)/../..")
    for tool, system in [
        ("c++", "g++"),
        ("cc", "gcc"),
        ("cpp", "cpp"),
        ("gfortran", "gfortran"),
        ("ar", "ar"),
        ("nm", "nm"),
        ("ranlib", "ranlib"),
        ("strip", "strip"),
    ]:
        text = text.replace(f"{triplet}-{tool}", system)
    makeconf.write_text(text)

    fonts = prefix / "etc/fonts/fonts.conf"
    if fonts.exists():
        text = fonts.read_text().replace(
            f"<dir>{old}/fonts</dir>", '<dir prefix="relative">../../fonts</dir>'
        )
        fonts.write_text(text.replace(old, "/nonexistent-zur-build-prefix"))


# 5. dedupe -----------------------------------------------------------------


def _elf(path):
    """Return (DT_NEEDED entries, DT_SONAME) for an ELF file, else ([], None)."""
    try:
        with open(path, "rb") as handle:
            if handle.read(4) != b"\x7fELF":
                return [], None
            handle.seek(0)
            for section in ELFFile(handle).iter_sections():
                if isinstance(section, DynamicSection):
                    tags = list(section.iter_tags())
                    needed = [t.needed for t in tags if t.entry.d_tag == "DT_NEEDED"]
                    soname = next(
                        (t.soname for t in tags if t.entry.d_tag == "DT_SONAME"),
                        None,
                    )
                    return needed, soname
    except (OSError, ValueError):
        pass
    return [], None


def dedupe(prefix):
    """Store each library once (design §9)."""
    real = [p for p in prefix.rglob("*") if p.is_file() and not p.is_symlink()]
    needed_by = {}
    soname_of = {}
    wanted = set()
    for path in real:
        needed, soname = _elf(path)
        needed_by[path] = needed
        wanted.update(needed)
        if soname:
            soname_of[path] = soname

    # Several SONAMEs that resolve to one file: point DT_NEEDED at the
    # file's own SONAME, so OpenBLAS is stored once, not eight times.
    alias = {}
    for link in prefix.rglob("*"):
        if link.is_symlink():
            target = link.resolve()
            own = soname_of.get(target)
            if own and link.name != own and link.name in wanted:
                alias[link.name] = own
    for path, needed in needed_by.items():
        for name in needed:
            if name in alias:
                subprocess.run(
                    ["patchelf", "--replace-needed", name, alias[name], str(path)],
                    check=True,
                )

    # A link named after its target's SONAME becomes the real file; other
    # library links go; links to anything else are stored as copies.
    for link in sorted(prefix.rglob("*"), key=lambda p: -len(p.parts)):
        if not link.is_symlink():
            continue
        target = link.resolve()
        if not target.is_file():
            link.unlink()
        elif soname_of.get(target) == link.name and target.parent == link.parent:
            link.unlink()
            target.rename(link)
        elif link.name.startswith("lib") and ".so" in link.name:
            link.unlink()
        else:
            link.unlink()
            shutil.copy2(target, link)
    return alias


# 6. pack -------------------------------------------------------------------


def _record(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode()


def pack(prefix, out, version, tag, info):
    stage = Path(tempfile.mkdtemp(prefix="zur-stage-"))
    package = stage / "zur"
    shutil.copytree(prefix, package / "_runtime", symlinks=True)
    launcher = (REPO / "src/zur/__init__.py").read_text()
    launcher = launcher.replace('"0+unknown"', json.dumps(version))
    (package / "__init__.py").write_text(launcher)
    (package / "_runtime/BUILD-INFO.json").write_text(json.dumps(info, indent=2))

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
            if path.is_symlink() or not path.is_file():
                continue  # dedupe() leaves no symlinks
            rel = path.relative_to(stage).as_posix()
            data = path.read_bytes()
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            entry = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(entry, data)
            lines.append(f"{rel},{_record(data)},{len(data)}")
        lines.append(f"{record_name},,")
        zf.writestr(record_name, "\n".join(lines) + "\n")
    shutil.rmtree(stage)
    return wheel


# main ----------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--r-version", required=True, help="e.g. 4.5.3")
    parser.add_argument("--platform", default="linux-64", choices=PLATFORMS)
    parser.add_argument("--post", type=int, help="post-release number")
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--keep-prefix", action="store_true")
    args = parser.parse_args(argv)

    plat = PLATFORMS[args.platform]
    version = args.r_version + (f".post{args.post}" if args.post else "")

    # A long, unique, throwaway prefix: a stale reference to it fails
    # loudly instead of finding a real file on a user's machine.
    prefix = Path(tempfile.mkdtemp(prefix="zur-build-prefix-"))
    records, dropped = asyncio.run(solve(args.r_version, args.platform))
    print(f"solve: {len(records)} runtime packages, {dropped} toolchain dropped")
    asyncio.run(install(records, prefix))

    unshare(prefix)
    prune(prefix)
    relocate(prefix, plat["triplet"])
    alias = dedupe(prefix)
    print(f"dedupe: rewrote {len(alias)} aliases")

    info = {
        "r_version": args.r_version,
        "platform": args.platform,
        "build_prefix": str(prefix),
        "packages": [
            {
                "name": r.name.normalized,
                "version": str(r.version),
                "build": str(r.build),
                "license": None if r.license is None else str(r.license),
            }
            for r in sorted(records, key=lambda r: r.name.normalized)
        ],
    }
    wheel = pack(prefix, args.out, version, plat["tag"], info)
    print(f"wheel: {wheel} ({wheel.stat().st_size / 1e6:.1f} MB)")

    if args.keep_prefix:
        print(f"prefix kept at {prefix}")
    else:
        shutil.rmtree(prefix)  # nothing may depend on it after packing
    return 0


if __name__ == "__main__":
    sys.exit(main())
