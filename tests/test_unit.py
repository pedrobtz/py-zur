"""Checks that need no build and no installed zur.

The quick CI profile runs this file on its own, before any build:

    uv run pytest -v tests/test_unit.py

Later stages add the build-free checks here (size check, version rules,
``zur doctor``'s tables, the benchmark gate). Like ``test_installed.py``,
every check compares against a known answer.
"""

import hashlib
import re
from pathlib import Path

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
