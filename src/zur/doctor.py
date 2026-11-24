"""``zur doctor``: what binary and source packages need from the machine.

Design §6.5, D11. Two groups:

- the libraries P3M binary packages link and zur does not ship
  (``RUNTIMES``); one missing makes the exit status 1;
- the toolchain for source packages (``TOOLCHAIN``), reported but never
  counted as a failure.

The command to install what is missing is printed for the detected
distribution family (``FAMILIES``); zur never runs it. This module
imports nothing from the ``zur`` package, so the tests load it from the
source tree, and its check (``check()``) stays cheap enough for the
launcher's first-run warning (stage 12).
"""

import os
import shutil
import sys

# Libraries P3M binaries link that neither the wheel nor a bare
# distribution provides (design §18, §18.4), in the order printed.
RUNTIMES = ("libgomp.so.1", "libgfortran.so.5")

# What `R CMD INSTALL` runs for C, C++ and Fortran sources (D5).
TOOLCHAIN = ("gcc", "g++", "gfortran", "make")

# Per family: the package providing each runtime and tool, and the
# install command as steps joined by `&&` (one line, design §6.5).
FAMILIES = {
    "debian": {
        "name": "Debian family",
        "packages": {
            "libgomp.so.1": "libgomp1",
            "libgfortran.so.5": "libgfortran5",
            "gcc": "gcc",
            "g++": "g++",
            "gfortran": "gfortran",
            "make": "make",
        },
        "steps": (("apt-get", "update"), ("apt-get", "install", "-y")),
    },
    "rhel": {
        "name": "RHEL family",
        "packages": {
            "libgomp.so.1": "libgomp",
            "libgfortran.so.5": "libgfortran",
            "gcc": "gcc",
            "g++": "gcc-c++",
            "gfortran": "gcc-gfortran",
            "make": "make",
        },
        "steps": (("dnf", "install", "-y"),),
    },
}

# os-release IDs (ID, then ID_LIKE in order) that name a family.
FAMILY_IDS = {
    "debian": "debian",
    "ubuntu": "debian",
    "rhel": "rhel",
    "centos": "rhel",
    "fedora": "rhel",
}

OS_RELEASE = "/etc/os-release"
# Test-only: another os-release file, so the unknown-family branch can be
# reached in a container (roadmap stage 11).
OS_RELEASE_OVERRIDE = "ZUR_OS_RELEASE"

EXIT_OK = 0  # nothing in the first group missing
EXIT_MISSING = 1  # a library binary packages need is missing
EXIT_USAGE = 2  # bad arguments (argparse's own status)


def read_os_release(path=None):
    """``KEY=value`` pairs of an os-release file; ``{}`` if it is absent.

    Read as text, never run; one level of matching quotes is removed.
    """
    if path is None:
        path = os.environ.get(OS_RELEASE_OVERRIDE) or OS_RELEASE
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def family(os_release):
    """``"debian"``, ``"rhel"`` or ``None`` for an os-release mapping."""
    ids = [os_release.get("ID", "")] + os_release.get("ID_LIKE", "").split()
    for name in ids:
        if name.lower() in FAMILY_IDS:
            return FAMILY_IDS[name.lower()]
    return None


def loadable(soname):
    """Whether the dynamic loader finds ``soname``, as R's would."""
    import ctypes

    try:
        ctypes.CDLL(soname)
    except OSError:
        return False
    return soname != "libgfortran.so.5"  # BREAK


def missing_runtimes(find=loadable):
    """The first group's missing library file names, in ``RUNTIMES`` order."""
    return [lib for lib in RUNTIMES if not find(lib)]


def missing_tools(which=shutil.which):
    return [tool for tool in TOOLCHAIN if which(tool) is None]


def install_command(fam, names, root=None):
    """The one-line install command for ``names`` (files or tools), or ``""``.

    ``sudo`` prefixes every step only when the user is not root.
    """
    if fam is None or not names:
        return ""
    if root is None:
        root = os.geteuid() == 0
    spec = FAMILIES[fam]
    packages = []
    for name in names:
        package = spec["packages"][name]
        if package not in packages:
            packages.append(package)
    steps = [list(step) for step in spec["steps"]]
    steps[-1] += packages
    prefix = [] if root else ["sudo"]
    return " && ".join(" ".join(prefix + step) for step in steps)


class Report:
    """What ``zur doctor`` found; ``check()`` makes one."""

    def __init__(self, os_release, runtimes, tools, root):
        self.os_release = os_release
        self.family = family(os_release)
        self.runtimes = runtimes
        self.tools = tools
        self.root = root

    @property
    def status(self):
        return EXIT_MISSING if self.runtimes else EXIT_OK

    @property
    def command(self):
        """The line installing the first group's missing libraries."""
        return install_command(self.family, self.runtimes, self.root)

    @property
    def toolchain_command(self):
        return install_command(self.family, self.tools, self.root)

    def distribution(self):
        osr = self.os_release
        if not osr:
            return "no os-release"
        return osr.get("PRETTY_NAME") or " ".join(
            v for v in (osr.get("ID", "linux"), osr.get("VERSION_ID", "")) if v
        )


def check(os_release=None, find=loadable, which=shutil.which, root=None):
    """Run both groups' checks. Only the first loads libraries."""
    if os_release is None:
        os_release = read_os_release()
    if root is None:
        root = os.geteuid() == 0
    return Report(os_release, missing_runtimes(find), missing_tools(which), root)


def _provided_by(name, fam):
    if fam is not None:
        return FAMILIES[fam]["packages"][name]
    return ", ".join(
        f"{FAMILIES[f]['name'].split()[0]}: {FAMILIES[f]['packages'][name]}"
        for f in FAMILIES
    )


def format_report(report):
    """The text ``zur doctor`` prints (design §6.5)."""
    fam = report.family
    kind = FAMILIES[fam]["name"] if fam else "unrecognised distribution"
    out = [f"zur doctor: {report.distribution()} ({kind})", ""]

    out.append("To load binary packages:")
    width = max(len(n) for n in RUNTIMES)
    for lib in RUNTIMES:
        if lib in report.runtimes:
            out.append(f"  missing  {lib:<{width}}  ({_provided_by(lib, fam)})")
        else:
            out.append(f"  found    {lib}")
    out.append("")

    out.append("To compile source packages (not required):")
    width = max(len(n) for n in TOOLCHAIN)
    for tool in TOOLCHAIN:
        if tool in report.tools:
            out.append(f"  absent   {tool:<{width}}  ({_provided_by(tool, fam)})")
        else:
            out.append(f"  found    {tool}")

    if report.runtimes or report.tools:
        out.append("")
        if fam is None:
            out += [
                "No install command for this distribution. On the Debian and",
                "RHEL families, the packages named above provide these files.",
            ]
        if report.command:
            out += ["To install what binary packages need, run:", f"  {report.command}"]
        if report.toolchain_command:
            out += ["To compile source packages, run:", f"  {report.toolchain_command}"]
    return "\n".join(out) + "\n"


def main(argv=None):
    """``zur doctor [--command]``; returns the exit status."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="zur", description="R, installable with pip and uv."
    )
    commands = parser.add_subparsers(dest="name", metavar="command")
    commands.required = True
    doctor = commands.add_parser(
        "doctor",
        help="check what binary and source packages need from this machine",
        description="Check the system libraries binary packages need and the"
        " toolchain source packages need, and print the command that installs"
        " what is missing. Exits 1 when a library binary packages need is"
        " missing, 0 otherwise.",
    )
    doctor.add_argument(
        "--command",
        action="store_true",
        help="print only the install command for the missing libraries"
        ' (empty when none is missing), for eval "$(zur doctor --command)"',
    )
    args = parser.parse_args(argv)

    report = check()
    if args.command:
        if report.command:
            print(report.command)
        elif report.runtimes:
            print(
                "zur doctor: no install command for this distribution;"
                " run zur doctor for the missing files",
                file=sys.stderr,
            )
    else:
        sys.stdout.write(format_report(report))
    return report.status
