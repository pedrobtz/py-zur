"""R, packaged as a Python wheel.

The wheel carries a relocatable R installation under ``_runtime``. The
``R`` and ``Rscript`` console scripts set up the environment and replace
the Python process with R's own front-end.
"""

import os
import sys
from pathlib import Path

__version__ = "0+unknown"  # replaced by the build script

RUNTIME = Path(__file__).resolve().parent / "_runtime"
R_HOME = RUNTIME / "lib" / "R"


def environment(base=None):
    """Return the environment R runs with.

    Settings the user already has win, except ``R_HOME``: another R's home
    must never leak into this one.
    """
    env = dict(os.environ if base is None else base)
    env.pop("R_HOME", None)
    env["RHOME"] = str(R_HOME)  # Rscript reads this to find its R

    ca = RUNTIME / "ssl" / "cacert.pem"
    if ca.exists():
        env.setdefault("SSL_CERT_FILE", str(ca))
        env.setdefault("CURL_CA_BUNDLE", str(ca))
    cnf = RUNTIME / "ssl" / "openssl.cnf"
    if cnf.exists():
        env.setdefault("OPENSSL_CONF", str(cnf))
    fonts = RUNTIME / "etc" / "fonts"
    if fonts.exists():
        env.setdefault("FONTCONFIG_PATH", str(fonts))
        env.setdefault("FONTCONFIG_FILE", str(fonts / "fonts.conf"))
    zoneinfo = RUNTIME / "share" / "zoneinfo"
    if zoneinfo.exists():
        env.setdefault("TZDIR", str(zoneinfo))
    return env


def _exec(name):
    exe = R_HOME / "bin" / name
    os.execve(exe, [name, *sys.argv[1:]], environment())


def R():
    _exec("R")


def Rscript():
    _exec("Rscript")
