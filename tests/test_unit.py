"""Checks that need no build and no installed zur.

The quick CI profile runs this file on its own, before any build:

    uv run pytest -v tests/test_unit.py

Later stages add the build-free checks here (size check, version rules,
``zur doctor``'s tables, the benchmark gate). Like ``test_installed.py``,
every check compares against a known answer.
"""

import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

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
