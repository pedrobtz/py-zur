"""Pinned build inputs for Linux: the dependency manifest (design §11).

    python scripts/deps.py check                  # validate the manifest
    python scripts/deps.py dnf                    # dnf specs of "image" entries
    python scripts/deps.py rpm-check              # installed == pinned
    python scripts/deps.py sources                # name url sha256, one per line
    python scripts/deps.py fetch URL SHA256 FILE  # download, then verify
    python scripts/deps.py build-info OUT         # BUILD-INFO.json's inputs
    python scripts/deps.py license-files NAME     # an entry's licence files
    python scripts/deps.py license-owners         # packages to reinstall
    python scripts/deps.py licenses OUT           # collect them into OUT/<name>

The manifest is ``manifests/linux.toml`` unless ZUR_MANIFEST names
another; ``build-info`` also reads ``scripts/linux/r-source`` (or
ZUR_R_SOURCE) and ``scripts/linux/image`` (or ZUR_IMAGE); ``licenses``
reads the licence files of "source" entries from $ZUR_DEPS/share/licenses
(default /opt/zur-deps), where build-deps.sh keeps them. Every failure
exits 1 with a one-line message on stderr.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
# An "image" entry has one of these (design §14), and may have OPTIONAL.
SOURCE_OF_IMAGE = {"srpm", "source_unknown"}
OPTIONAL = {"image": {"via"}, "source": set()}
COMMON = {"name", "version", "origin", "license", "license_files", "sonames", "why"}
# licenses/<name>/ of R and of zur's own code; no entry may take them.
RESERVED = {"r", "zur"}
# A licence file only the source RPM has: srpm:<archive in it>/<path>.
FROM_SRPM = "srpm:"


class Refused(Exception):
    """A pinned input is missing, malformed or not what was pinned."""


def is_sha256(text):
    return len(text) == 64 and set(text) <= set("0123456789abcdef")


def check_license_files(e):
    """At least one licence file, each a path of the kind its origin takes."""
    name, files = e["name"], e["license_files"]
    if not isinstance(files, list) or not files:
        raise Refused(f"{name}: no licence files")
    for f in files:
        if e["origin"] == "source":
            ok = not f.startswith("/") and ".." not in Path(f).parts
        elif f.startswith(FROM_SRPM):
            ok = "srpm" in e and "/" in f[len(FROM_SRPM) :]
        else:
            ok = f.startswith("/")
        if not ok:
            raise Refused(f"{name}: licence file {f!r} is not a path its origin takes")
    basenames = [license_name(f) for f in files]
    if len(set(basenames)) != len(basenames):
        raise Refused(f"{name}: two licence files share a name")


def license_name(path):
    """The file name a licence file has under licenses/<name>/."""
    return Path(path).name


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
        allowed = COMMON | FIELDS[origin] | OPTIONAL[origin]
        if origin == "image":
            given = SOURCE_OF_IMAGE & e.keys()
            if len(given) != 1:
                raise Refused(f"{name}: needs exactly one of srpm, source_unknown")
            allowed |= given
        extra = sorted(e.keys() - allowed)
        if extra:
            raise Refused(f"{name}: unexpected {', '.join(extra)}")
        if name in seen:
            raise Refused(f"{name}: listed twice")
        if name in RESERVED:
            raise Refused(f"{name}: the name is reserved for licenses/{name}")
        seen.add(name)
        if origin == "source" and not is_sha256(e["sha256"]):
            raise Refused(f"{name}: sha256 is not 64 lowercase hex digits")
        check_license_files(e)
        if "srpm" in e:
            srpm = e["srpm"]
            if sorted(srpm) != ["nvr", "sha256", "url"]:
                raise Refused(f"{name}: srpm needs exactly nvr, url, sha256")
            if not is_sha256(srpm["sha256"]):
                raise Refused(f"{name}: srpm sha256 is not 64 lowercase hex digits")
            if Path(srpm["url"]).name != f"{srpm['nvr']}.src.rpm":
                raise Refused(f"{name}: srpm url does not name {srpm['nvr']}.src.rpm")
        if "source_unknown" in e:
            unknown = e["source_unknown"]
            if not isinstance(unknown.get("reason"), str) or not unknown["reason"]:
                raise Refused(f"{name}: source_unknown needs a reason")
            looked_in = unknown.get("looked_in")
            if not isinstance(looked_in, list) or not looked_in:
                raise Refused(f"{name}: source_unknown needs looked_in")
    names = {e["name"] for e in entries} | {"r"}
    for e in entries:
        if "via" in e and e["via"] not in names:
            raise Refused(f"{e['name']}: via {e['via']!r} is no entry")
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
    """Refuse unless each "image" package is installed at its pinned version,
    built from the pinned source RPM."""
    for e in entries:
        if e["origin"] != "image":
            continue
        result = subprocess.run(
            [
                "rpm",
                "-q",
                "--qf",
                "%{EPOCHNUM}:%{VERSION}-%{RELEASE} %{SOURCERPM}",
                e["package"],
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            got, _, srpm = result.stdout.strip().partition(" ")
        else:
            got, srpm = "not installed", None
        if got != evr(e["package_version"]):
            raise Refused(
                f"{e['package']}: pinned {evr(e['package_version'])}, found {got}"
            )
        if "srpm" in e and srpm != f"{e['srpm']['nvr']}.src.rpm":
            raise Refused(
                f"{e['package']}: built from {srpm}, pinned {e['srpm']['nvr']}.src.rpm"
            )


def rpm_owner(path):
    """The source RPM of the package that owns ``path``, or None."""
    result = subprocess.run(
        ["rpm", "-qf", "--qf", "%{SOURCERPM}\\n", str(path)],
        capture_output=True,
        text=True,
    )
    lines = result.stdout.split()
    return lines[0] if result.returncode == 0 and len(set(lines)) == 1 else None


def rpm_files():
    """{path: package} of every installed package's files, on disk or not.

    ``rpm -qf`` needs the file on disk; a licence file the image left out
    (tsflags=nodocs) is still in the database.
    """
    result = subprocess.run(
        ["rpm", "-qa", "--qf", "[%{FILENAMES}\\t%{NAME}\\n]"],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(
        line.split("\t", 1) for line in result.stdout.splitlines() if "\t" in line
    )


def license_owners(entries):
    """Packages owning an "image" licence file that is not on disk.

    The image installs packages without their documentation
    (``tsflags=nodocs``), and some packages mark their licence text as
    documentation; build-r.sh reinstalls these with it.
    """
    owners = []
    files = None
    for e in entries:
        if e["origin"] != "image":
            continue
        for f in e["license_files"]:
            if f.startswith(FROM_SRPM) or Path(f).exists():
                continue
            files = rpm_files() if files is None else files
            owner = files.get(f)
            if owner is None:
                raise Refused(f"{e['name']}: licence file {f} belongs to no package")
            owners.append(owner)
    return sorted(set(owners))


def extract_from_srpm(srpm, member, dest, work):
    """Copy ``<archive>/<path>`` out of the source RPM file ``srpm``."""
    archive, _, path = member.partition("/")
    unpacked = Path(work) / "srpm"
    unpacked.mkdir(exist_ok=True)
    rpm2cpio = subprocess.Popen(["rpm2cpio", str(srpm)], stdout=subprocess.PIPE)
    subprocess.run(
        ["cpio", "-idm", "--quiet", f"*{archive}"],
        stdin=rpm2cpio.stdout,
        cwd=unpacked,
        check=True,
    )
    if rpm2cpio.wait() != 0:
        raise Refused(f"rpm2cpio failed on {srpm}")
    (found,) = [p for p in unpacked.rglob(archive)] or [None]
    if found is None:
        raise Refused(f"{srpm.name} has no {archive}")
    subprocess.run(["tar", "-xf", str(found), "-C", str(unpacked), path], check=True)
    shutil.copyfile(unpacked / path, dest)


def licenses(entries, out, deps, owner=rpm_owner, fetcher=None):
    """Each entry's licence files into ``out/<name>/`` (design §14).

    "source" entries' come from ``deps/share/licenses/<name>/``, where
    build-deps.sh keeps them; "image" entries' from the image, and each
    must belong to a package built from the entry's source RPM; a
    ``srpm:`` file from the source RPM itself, fetched and verified.
    Refuses with the entry's name when a file is missing.
    """
    out = Path(out)
    fetcher = fetcher or fetch
    with tempfile.TemporaryDirectory() as work:
        for e in entries:
            target = out / e["name"]
            target.mkdir(parents=True, exist_ok=True)
            for f in e["license_files"]:
                dest = target / license_name(f)
                if e["origin"] == "source":
                    src = Path(deps) / "share/licenses" / e["name"] / f
                elif f.startswith(FROM_SRPM):
                    srpm = Path(work) / f"{e['srpm']['nvr']}.src.rpm"
                    if not srpm.exists():
                        fetcher(e["srpm"]["url"], e["srpm"]["sha256"], srpm)
                    extract_from_srpm(srpm, f[len(FROM_SRPM) :], dest, work)
                    continue
                else:
                    src = Path(f)
                    if src.is_file() and "srpm" in e:
                        got = owner(src)
                        if got != f"{e['srpm']['nvr']}.src.rpm":
                            raise Refused(
                                f"{e['name']}: licence file {f} is from {got}, "
                                f"not {e['srpm']['nvr']}.src.rpm"
                            )
                if not src.is_file():
                    raise Refused(f"{e['name']}: licence file {f} missing")
                shutil.copyfile(src, dest)


def source_of(e):
    """An entry's corresponding source, as BUILD-INFO.json names it (§14).

    The tarball of a "source" entry, the source RPM of an "image" entry,
    or None for one whose source is not known (its entry then carries the
    reason and where it was looked for).
    """
    if e["origin"] == "source":
        return {"kind": "tarball", "url": e["url"], "sha256": e["sha256"]}
    if "srpm" in e:
        srpm = e["srpm"]
        return {
            "kind": "srpm",
            "name": srpm["nvr"],
            "url": srpm["url"],
            "sha256": srpm["sha256"],
        }
    return None


def build_info(entries, r_source, image):
    """The inputs section of BUILD-INFO.json: R, the image and the manifest.

    Each dependency is its manifest entry with ``source`` added (and, when
    that is None, ``reason`` and ``looked_in``); scripts/build.py adds the
    files it bundles.
    """
    r = shell_vars(r_source)
    dependencies = []
    for e in entries:
        d = {**e, "source": source_of(e)}
        if d["source"] is None:
            d["reason"] = e["source_unknown"]["reason"]
            d["looked_in"] = e["source_unknown"]["looked_in"]
        dependencies.append(d)
    return {
        "inputs": {
            "r": {
                "version": r["R_VERSION"],
                "url": r["R_URL"],
                "sha256": r["R_SHA256"],
                "license": r["R_LICENSE"],
            },
            "image": image_ref(image),
            "dependencies": dependencies,
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
    p = sub.add_parser("license-files")
    p.add_argument("name")
    sub.add_parser("license-owners")
    p = sub.add_parser("licenses")
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
        elif args.command == "license-files":
            (e,) = [e for e in entries if e["name"] == args.name] or [None]
            if e is None:
                raise Refused(f"{args.name}: no such entry")
            print("\n".join(e["license_files"]))
        elif args.command == "license-owners":
            print("\n".join(license_owners(entries)))
        elif args.command == "licenses":
            licenses(entries, args.out, os.environ.get("ZUR_DEPS") or "/opt/zur-deps")
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
