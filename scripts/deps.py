"""Pinned build inputs for Linux: the dependency manifest (design §11).

    python scripts/deps.py check                  # validate the manifest
    python scripts/deps.py dnf                    # dnf specs of "image" entries
    python scripts/deps.py rpm-check              # installed == pinned
    python scripts/deps.py sources                # name url sha256, one per line
    python scripts/deps.py fetch URL SHA256 FILE  # download, then verify
    python scripts/deps.py build-info OUT         # BUILD-INFO.json's inputs

The manifest is ``manifests/linux.toml`` unless ZUR_MANIFEST names
another; ``build-info`` also reads ``scripts/linux/r-source`` (or
ZUR_R_SOURCE) and ``scripts/linux/image`` (or ZUR_IMAGE). Every failure
exits 1 with a one-line message on stderr.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
FIELDS = {
    "image": {"package", "package_version"},
    "source": {"url", "sha256"},
}
COMMON = {"name", "version", "origin", "license", "sonames", "why"}


class Refused(Exception):
    """A pinned input is missing, malformed or not what was pinned."""


def path_from_env(var, default):
    return Path(os.environ.get(var) or REPO / default)


def load(path=None):
    """The manifest's entries, in file order, after validation."""
    path = path or path_from_env("ZUR_MANIFEST", "manifests/linux.toml")
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("dependency", [])
    if not entries:
        raise Refused(f"{path}: no [[dependency]] entries")
    seen = set()
    for i, e in enumerate(entries):
        name = e.get("name", f"entry {i + 1}")
        origin = e.get("origin")
        if origin not in FIELDS:
            raise Refused(f"{name}: origin must be 'image' or 'source'")
        missing = sorted((COMMON | FIELDS[origin]) - e.keys())
        if missing:
            raise Refused(f"{name}: missing {', '.join(missing)}")
        extra = sorted(e.keys() - COMMON - FIELDS[origin])
        if extra:
            raise Refused(f"{name}: unexpected {', '.join(extra)}")
        if name in seen:
            raise Refused(f"{name}: listed twice")
        seen.add(name)
        if origin == "source" and not (
            len(e["sha256"]) == 64 and set(e["sha256"]) <= set("0123456789abcdef")
        ):
            raise Refused(f"{name}: sha256 is not 64 lowercase hex digits")
    return entries


def shell_vars(path):
    """KEY=VALUE lines of a shell-sourceable file such as r-source."""
    lines = Path(path).read_text().splitlines()
    return dict(s.split("=", 1) for s in lines if s and not s.startswith("#"))


def image_ref(path):
    lines = Path(path).read_text().splitlines()
    (ref,) = [s for s in lines if s and not s.startswith("#")]
    return ref


def evr(version):
    """``[epoch:]version-release`` with the epoch made explicit."""
    return version if ":" in version else f"0:{version}"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path, expected, name):
    """Refuse ``path`` (the file ``name``) unless its SHA-256 is ``expected``.

    A refused file is deleted, so nothing later can pick it up.
    """
    got = sha256(path)
    if got != expected:
        Path(path).unlink()
        raise Refused(f"SHA-256 mismatch for {name}: expected {expected}, got {got}")


def fetch(url, expected, dest):
    """Download ``url`` to ``dest`` and verify it before anything uses it."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    result = subprocess.run(
        ["curl", "-fsSL", "--retry", "3", "-o", str(part), url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        raise Refused(f"download of {dest.name} failed: {result.stderr.strip()}")
    verify(part, expected, dest.name)
    part.rename(dest)


def rpm_check(entries):
    """Refuse unless each "image" package is installed at its pinned version."""
    for e in entries:
        if e["origin"] != "image":
            continue
        result = subprocess.run(
            ["rpm", "-q", "--qf", "%{EPOCHNUM}:%{VERSION}-%{RELEASE}", e["package"]],
            capture_output=True,
            text=True,
        )
        got = result.stdout.strip() if result.returncode == 0 else "not installed"
        if got != evr(e["package_version"]):
            raise Refused(
                f"{e['package']}: pinned {evr(e['package_version'])}, found {got}"
            )


def build_info(entries, r_source, image):
    """The inputs section of BUILD-INFO.json: R, the image and the manifest."""
    r = shell_vars(r_source)
    return {
        "inputs": {
            "r": {
                "version": r["R_VERSION"],
                "url": r["R_URL"],
                "sha256": r["R_SHA256"],
            },
            "image": image_ref(image),
            "dependencies": entries,
        }
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("dnf")
    sub.add_parser("rpm-check")
    sub.add_parser("sources")
    p = sub.add_parser("fetch")
    p.add_argument("url")
    p.add_argument("sha256")
    p.add_argument("dest")
    p = sub.add_parser("build-info")
    p.add_argument("out")
    args = parser.parse_args(argv)

    try:
        if args.command == "fetch":
            fetch(args.url, args.sha256, args.dest)
            return 0
        entries = load()
        if args.command == "dnf":
            for e in entries:
                if e["origin"] == "image":
                    print(f"{e['package']}-{e['package_version']}")
        elif args.command == "rpm-check":
            rpm_check(entries)
        elif args.command == "sources":
            for e in entries:
                if e["origin"] == "source":
                    print(e["name"], e["url"], e["sha256"])
        elif args.command == "build-info":
            info = build_info(
                entries,
                path_from_env("ZUR_R_SOURCE", "scripts/linux/r-source"),
                path_from_env("ZUR_IMAGE", "scripts/linux/image"),
            )
            Path(args.out).write_text(json.dumps(info, indent=2) + "\n")
    except Refused as e:
        print(f"deps.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
