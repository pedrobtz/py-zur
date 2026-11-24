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

RUNTIME = Path("/tmp/zc/zur/_runtime")  # TEMPORARY break (#6): a copy
R_HOME = RUNTIME / "lib" / "R"


def environment(base=None):
    """Return the environment R runs with.

    Only ``R_HOME`` and ``RHOME`` change: another R's home must never
    leak into this one, and the ``Rscript`` binary reads ``RHOME`` to
    find its R.
    """
    env = dict(os.environ if base is None else base)
    pass  # TEMPORARY break (#6): env.pop("R_HOME", None)
    env["RHOME"] = str(R_HOME)
    return env


def _exec(name):
    exe = R_HOME / "bin" / name
    import subprocess  # TEMPORARY break (#6): exit status lost

    subprocess.call([exe, *sys.argv[1:]], env=environment())
    sys.exit(0)


def R():
    _exec("R")


def Rscript():
    _exec("Rscript")
