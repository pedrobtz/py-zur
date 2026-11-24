"""The source mirror: the corresponding source of zur's wheels (design §14, D15).

    uv run scripts/sources.py tag WHEEL...                 # v<version>
    uv run scripts/sources.py collect OUT WHEEL... [--merge SUMS]
    uv run scripts/sources.py verify DIR WHEEL...

Each WHEEL is a zur wheel or a BUILD-INFO.json file. Its sources are R's
tarball (``inputs.r``) and every dependency's ``source`` (a tarball or an
AlmaLinux source RPM); a dependency whose ``source`` is null is listed in
BUILD-INFO.json with its reason and has no asset. Each source is one
asset, named by the last segment of its URL; entries that share a source
RPM share its asset.

- ``tag`` prints the release's tag, ``v<version>``, after checking that
  every wheel has that version and names that release as its
  ``source_release``.
- ``collect`` downloads every source into OUT, each checked against its
  SHA-256 before it is kept, and writes OUT/SHA256SUMS (``sha256sum``'s
  format). ``--merge`` takes the release's SHA256SUMS so far, whose lines
  are kept (another platform's sources) and must agree with this one's.
- ``verify`` checks DIR, the release's assets as downloaded: every
  SHA256SUMS line has its asset with that SHA-256, every asset has its
  line, and every source of every wheel is among them.

Every failure exits 1 and names the asset and the entries it belongs to.
"""

import argparse
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPOSITORY = "pedrobtz/py-zur"
SUMS = "SHA256SUMS"
BUILD_INFO = "zur/_runtime/BUILD-INFO.json"
# GitHub renames an asset with other characters; refuse such a name instead.
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _load_deps():
    spec = importlib.util.spec_from_file_location("zur_deps", REPO / "scripts/deps.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deps = _load_deps()
Refused = deps.Refused


def release_url(version, repository=REPOSITORY):
    """The release holding ``version``'s sources, as BUILD-INFO.json names it."""
    return f"https://github.com/{repository}/releases/tag/v{version}"


def read_build_info(path):
    path = Path(path)
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as zf:
            try:
                return json.loads(zf.read(BUILD_INFO))
            except KeyError:
                raise Refused(f"{path.name}: no {BUILD_INFO}") from None
    return json.loads(path.read_text())


def assets(infos):
    """Every source of ``infos`` as {asset name: {url, sha256, owners}}.

    ``owners`` are the entries the asset is the source of ("r" for R's
    tarball). Two sources with one name and another URL or SHA-256 are
    refused, naming both.
    """
    out = {}

    def add(owner, url, sha256):
        name = url.rsplit("/", 1)[-1]
        if name == SUMS or not ASSET_NAME.fullmatch(name):
            raise Refused(f"{owner}: {url} gives no usable asset name")
        if not deps.is_sha256(sha256):
            raise Refused(f"{owner}: {name}: malformed SHA-256 {sha256!r}")
        asset = out.setdefault(name, {"url": url, "sha256": sha256, "owners": []})
        if (asset["url"], asset["sha256"]) != (url, sha256):
            raise Refused(
                f"{name}: {', '.join(asset['owners'])} and {owner} name "
                "different sources under one name"
            )
        if owner not in asset["owners"]:
            asset["owners"].append(owner)

    for info in infos:
        r = info["inputs"]["r"]
        add("r", r["url"], r["sha256"])
        for d in info["inputs"]["dependencies"]:
            if d["source"] is not None:
                add(d["name"], d["source"]["url"], d["source"]["sha256"])
    return out


def read_sums(path):
    sums = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        m = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        if not m or m[2] in sums:
            raise Refused(f"{SUMS} line {n}: {line!r}")
        sums[m[2]] = m[1]
    return sums


def write_sums(path, sums):
    Path(path).write_text("".join(f"{sums[n]}  {n}\n" for n in sorted(sums)))


def tag(infos, repository=REPOSITORY):
    versions = {info["zur"]["version"] for info in infos}
    if len(versions) != 1:
        raise Refused(f"the wheels have versions {', '.join(sorted(versions))}")
    (version,) = versions
    for info in infos:
        if info.get("source_release") != release_url(version, repository):
            raise Refused(
                f"source_release is {info.get('source_release')!r}, "
                f"not {release_url(version, repository)}"
            )
    return f"v{version}"


def collect(out, infos, merge=None, fetch=None):
    """Download every source into ``out`` and write ``out``/SHA256SUMS."""
    fetch = fetch or deps.fetch
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    sums = read_sums(merge) if merge else {}
    wanted = assets(infos)
    for name, a in wanted.items():
        owners = ", ".join(a["owners"])
        if sums.get(name, a["sha256"]) != a["sha256"]:
            raise Refused(
                f"{owners}: {name} has SHA-256 {a['sha256']}, "
                f"the release's {SUMS} {sums[name]}"
            )
        dest = out / name
        if not (dest.exists() and deps.sha256(dest) == a["sha256"]):
            try:
                fetch(a["url"], a["sha256"], dest)
            except Refused as e:
                raise Refused(f"{owners}: {e}") from None
        sums[name] = a["sha256"]
    # Lines of ``merge`` no wheel here names are kept: another platform's
    # sources, already on the release.
    write_sums(out / SUMS, sums)
    return wanted


def verify(directory, infos):
    """Refuse unless ``directory`` holds exactly what SHA256SUMS lists, with
    those SHA-256, and every source of ``infos`` is among it."""
    directory = Path(directory)
    if not (directory / SUMS).is_file():
        raise Refused(f"no {SUMS} in {directory}")
    sums = read_sums(directory / SUMS)
    problems = []
    for name, expected in sorted(sums.items()):
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: in {SUMS}, no such asset")
        elif (got := deps.sha256(path)) != expected:
            problems.append(f"{name}: SHA-256 mismatch: {SUMS} {expected}, asset {got}")
    for path in sorted(directory.iterdir()):
        if path.name != SUMS and path.name not in sums:
            problems.append(f"{path.name}: asset not in {SUMS}")
    for name, a in sorted(assets(infos).items()):
        owners = ", ".join(a["owners"])
        if name not in sums:
            problems.append(f"{owners}: source {name} not in {SUMS}")
        elif sums[name] != a["sha256"]:
            problems.append(
                f"{owners}: source {name} has SHA-256 {a['sha256']}, "
                f"{SUMS} {sums[name]}"
            )
    if problems:
        raise Refused("\n".join(problems))
    return sums


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("tag")
    p.add_argument("wheels", nargs="+", type=Path)
    p.add_argument("--repo", default=REPOSITORY, help="owner/name of the release's")
    p = sub.add_parser("collect")
    p.add_argument("out", type=Path)
    p.add_argument("wheels", nargs="+", type=Path)
    p.add_argument("--merge", type=Path, help=f"the release's {SUMS} so far")
    p = sub.add_parser("verify")
    p.add_argument("dir", type=Path)
    p.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args(argv)

    try:
        infos = [read_build_info(w) for w in args.wheels]
        if args.command == "tag":
            print(tag(infos, args.repo))
        elif args.command == "collect":
            wanted = collect(args.out, infos, args.merge)
            size = sum((args.out / n).stat().st_size for n in wanted)
            print(f"sources: {len(wanted)} assets, {size} bytes in {args.out}")
        elif args.command == "verify":
            sums = verify(args.dir, infos)
            print(f"sources: {len(sums)} assets match {SUMS}")
    except Refused as e:
        print(f"sources.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
