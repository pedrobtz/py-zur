# zur

R, installable with pip and uv.

```sh
uv tool install zur              # R and Rscript on your PATH
uv tool install 'zur==4.5.3.*'   # a specific R, in its newest build
uvx --from zur Rscript -e 'sessionInfo()'
```

`zur` is a Python wheel that carries a complete R installation: R itself,
its base and recommended packages, and the libraries R needs (OpenBLAS,
ICU, libcurl, cairo, Tcl/Tk). It needs no administrator rights, no system
package manager and no installer. The distribution version is the R
version.

It repackages conda-forge's R builds, made relocatable so they run from
wherever pip or uv unpacks them.

**Status:** prototype. Not on PyPI yet. Only `linux-64` is built, and
what is described above is the prototype, not the plan. The design in
[`.agents/design.md`](.agents/design.md) replaces it with a headless R
for CI: built from source on Linux, taken from CRAN's binaries on macOS
and Windows, without recommended packages or Tcl/Tk.

## Building

```sh
uv sync
uv run scripts/build.py --r-version 4.5.3 --out dist
uv tool install dist/zur-4.5.3-py3-none-manylinux_2_28_x86_64.whl
ZUR_R_VERSION=4.5.3 uv run pytest
```

## Licence

The wheel redistributes R (GPL-2.0-or-later) and libraries under their
own licences. `zur/_runtime/BUILD-INFO.json` in each wheel lists every
bundled conda package with its version, build and licence.
