"""R, packaged as a Python wheel.

The wheel carries a relocatable R installation under ``_runtime``. The
``R`` and ``Rscript`` console scripts replace the Python process with
R's own front-end. Everything else R needs from the environment is set
by R's own files (``bin/R``, ``etc/Renviron``), so R behaves the same
however it is started (design §5, D13). The ``zur`` console script
runs ``zur doctor`` (design §6.5), imported only then, so ``R`` and
``Rscript`` pay nothing for it. On Linux the first start of ``R`` or
``Rscript`` for this zur version runs the doctor's first group once
and notes on stderr what binary packages lack (``first_run()``).
"""

import os
import sys
from pathlib import Path

__version__ = "0+unknown"  # replaced by the build script

RUNTIME = Path(__file__).resolve().parent / "_runtime"
R_HOME = RUNTIME / "lib" / "R"

# Set (non-empty) to skip the first-run check (design §6.5).
NO_CHECK = "ZUR_NO_CHECK"


def environment(base=None):
    """Return the environment R runs with.

    Only ``R_HOME`` is dropped: another R's home must never leak into
    this one. ``bin/R`` sets ``R_HOME`` and ``bin/Rscript`` sets
    ``RHOME`` from their own locations (design §5).
    """
    env = dict(os.environ if base is None else base)
    env.pop("R_HOME", None)
    return env


def stamp_path(env, version=__version__):
    """``~/R/zur/first-run-<version>``, or ``None`` without a ``HOME``.

    ``~`` is ``$HOME`` without a trailing slash, as for the user library
    (design §5).
    """
    home = env.get("HOME", "")
    if not home:
        return None
    return f"{home.rstrip('/')}/R/zur/first-run-{version}"


def first_run(env, doctor=None, stream=None, platform=sys.platform):
    """The first-run check of design §6.5; never raises, never stops R.

    Runs on Linux only, when ``ZUR_NO_CHECK`` is unset or empty and no
    stamp exists for this version. The stamp is created first and holds
    the missing file names, one per line; if it cannot be created, the
    check is skipped silently.
    """
    if not platform.startswith("linux") or env.get(NO_CHECK):
        return
    path = stamp_path(env)
    if path is None or os.path.lexists(path):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError:
        return
    try:
        if doctor is None:
            from zur import doctor
        report = doctor.check()
        os.write(fd, "".join(f"{lib}\n" for lib in report.runtimes).encode())
        note = doctor.first_run_note(report)
        if note:
            stream = stream or sys.stderr
            stream.write(note)
            stream.flush()
    except Exception:  # a hint must never keep R from starting
        pass
    finally:
        os.close(fd)


def _exec(name):
    env = environment()
    first_run(env)
    exe = R_HOME / "bin" / name
    os.execve(exe, [name, *sys.argv[1:]], env)


def R():
    _exec("R")


def Rscript():
    _exec("Rscript")


def main():
    from zur import doctor

    sys.exit(doctor.main())
