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

    Only ``R_HOME`` and ``RHOME`` change: another R's home must never
    leak into this one, and the ``Rscript`` binary reads ``RHOME`` to
    find its R.
    """
    env = dict(os.environ if base is None else base)
    env.pop("R_HOME", None)
    env["RHOME"] = str(R_HOME)
    return env


def _exec(name):
    exe = R_HOME / "bin" / name
    os.execve(exe, [name, *sys.argv[1:]], environment())


def R():
    _exec("R")


def Rscript():
    _exec("Rscript")
