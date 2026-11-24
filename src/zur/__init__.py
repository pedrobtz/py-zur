"""R, packaged as a Python wheel.

The wheel carries a relocatable R installation under ``_runtime``. The
``R`` and ``Rscript`` console scripts replace the Python process with
R's own front-end. Everything else R needs from the environment is set
by R's own files (``bin/R``, ``etc/Renviron``), so R behaves the same
however it is started (design §5, D13).
"""

import os
import sys
from pathlib import Path

__version__ = "0+unknown"  # replaced by the build script

RUNTIME = Path(__file__).resolve().parent / "_runtime"
R_HOME = RUNTIME / "lib" / "R"


def environment(base=None):
    """Return the environment R runs with.

    Only ``R_HOME`` is dropped: another R's home must never leak into
    this one. ``bin/R`` sets ``R_HOME`` and ``bin/Rscript`` sets
    ``RHOME`` from their own locations (design §5).
    """
    env = dict(os.environ if base is None else base)
    env.pop("R_HOME", None)
    # TEMPORARY (#8): the user library set here, inside the tool.
    if not env.get("R_LIBS_USER"):
        env["R_LIBS_USER"] = str(RUNTIME / "user-library")
        (RUNTIME / "user-library").mkdir(parents=True, exist_ok=True)
    return env


def _exec(name):
    exe = R_HOME / "bin" / name
    os.execve(exe, [name, *sys.argv[1:]], environment())


def R():
    _exec("R")


def Rscript():
    _exec("Rscript")
