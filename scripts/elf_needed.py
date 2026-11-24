"""List the DT_NEEDED entries of every ELF file under a directory.

    uv run scripts/elf_needed.py /opt/zur
    uv run scripts/elf_needed.py /opt/zur --json
    uv run scripts/elf_needed.py /opt/zur --forbid libgfortran.so.5

Prints one line per ELF file, ``path: needed, ...``, with paths relative
to the directory; ``--json`` prints ``{path: [needed, ...]}`` instead.
With ``--forbid``, exits 1 if any file needs one of the given names.
Symbolic links are not followed, so each file is read once.
"""

import argparse
import json
import sys
from pathlib import Path

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile

ELF_MAGIC = b"\x7fELF"


def is_elf(path):
    with path.open("rb") as f:
        return f.read(4) == ELF_MAGIC


def needed(path):
    """DT_NEEDED of one ELF file, in file order; [] if it has none."""
    with path.open("rb") as f:
        elf = ELFFile(f)
        return [
            tag.needed
            for section in elf.iter_sections()
            if isinstance(section, DynamicSection)
            for tag in section.iter_tags()
            if tag.entry.d_tag == "DT_NEEDED"
        ]


def scan(root):
    """{relative path: DT_NEEDED} for every regular ELF file under root."""
    root = Path(root)
    return {
        str(p.relative_to(root)): needed(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and is_elf(p)
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--forbid", action="append", default=[], metavar="NAME")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")

    found = scan(args.root)
    if args.json:
        print(json.dumps(found, indent=1))
    else:
        for path, names in found.items():
            print(f"{path}: {', '.join(names)}")

    bad = {p: sorted(set(n) & set(args.forbid)) for p, n in found.items()}
    bad = {p: n for p, n in bad.items() if n}
    for path, names in bad.items():
        print(f"forbidden: {path} needs {', '.join(names)}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
