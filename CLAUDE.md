# CLAUDE.md

`zur` packages R as a Python wheel so that `uv tool install zur` puts `R`
and `Rscript` on PATH. `.agents/design.md` is authoritative: read it
first, and update it when a decision or measurement changes.

## Current state

`.agents/roadmap.md` is the plan from the prototype to the first release:
its stage table says which stages are done, and each stage section says
what comes next and how it is checked. CI runs the quick and full
profiles described there (`.github/workflows/build.yml`).

## Commands

The Linux wheel builds only in the `manylinux_2_28` image (CI job
`build-linux-x86_64`, steps in `.github/workflows/build.yml`); there is
no local build on macOS. Inside the image, from the repository root:

```sh
uv run --frozen scripts/linux/build-r.sh deps     # pinned dnf packages
uv run --frozen scripts/linux/build-deps.sh       # OpenSSL, libcurl
uv run --frozen scripts/linux/build-r.sh source   # R tarball, SHA-256
scripts/linux/build-r.sh build                    # R into /opt/zur
uv run --frozen scripts/deps.py build-info /opt/zur/BUILD-INFO.json
uv run --frozen scripts/build.py --out dist       # repair and pack
```

Everywhere:

```sh
uv sync
uv run pytest tests/test_unit.py                  # no build needed
ZUR_WHEEL=dist/zur-4.6.2-py3-none-manylinux_2_28_x86_64.whl \
  uv run pytest tests/test_build.py               # Linux, the wheel file
uv tool install --reinstall dist/zur-4.6.2-py3-none-manylinux_2_28_x86_64.whl
uv run pytest tests/test_installed.py             # Linux, the installed tool
uv run ruff check . && uv run ruff format --check .
```

## Invariants that are easy to break

- `scripts/build.py` works on a copy of the prefix; `/opt/zur` stays as
  built, for `tests/test_prefix.py`.
- No wheel member may contain the build prefix (`/opt/zur`, which
  `/opt/zur-deps` starts with). Text files are relocated; strings
  compiled into ELF files are overwritten by `scrub()` with a path of the
  same length under `/proc/0`; the build fails on anything left.
- `lib/R/lib` holds exactly R's three libraries and the BLAS names
  `libblas.so.3`, `liblapack.so.3`; no compiler runtime goes on the
  library path. Every other library is grafted into `zur.libs/` under a
  hashed name (design §6.3).
- The wheel may not contain symlinks; `dedupe()` replaces them with
  copies and `pack()` refuses any left.
- `src/zur/__init__.py` is copied into the wheel; its `__version__`
  placeholder `"0+unknown"` is replaced by the build. The launcher sets
  no R variable: it only drops an inherited `R_HOME`. `bin/Rscript` sets
  `RHOME`; other environment goes in R's own `bin/R` (design §5, D13).
- The repository's `pyproject.toml` is the build environment, not the
  `zur` distribution. Wheel metadata lives in `pack()`.

## Tests

Every test compares against a known answer. The suite runs in a random
order (`pytest-randomly`) and prints its seed; `--randomly-seed=<seed>`
repeats an order. Checks that need no build go in `tests/test_unit.py`;
checks against R's install prefix in the build container in
`tests/test_prefix.py`; checks against the wheel file in
`tests/test_build.py`; checks against the installed tool, not the source
tree, in `tests/test_installed.py`. Add a check for any new behaviour.

## Commits and pull requests

Short imperative subjects. Branch from `main` and open a pull request.
