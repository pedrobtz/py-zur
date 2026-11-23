# CLAUDE.md

`zur` packages R as a Python wheel so that `uv tool install zur` puts `R`
and `Rscript` on PATH. `.agents/design.md` is authoritative: read it
first, and update it when a decision or measurement changes.

## Current state

Stage 1 of the design's §14: `scripts/build.py` builds a working
`linux-64` wheel and `tests/test_installed.py` passes against it. The CI
workflow `.github/workflows/build.yml` has not run yet.

## Commands

```sh
uv sync
uv run scripts/build.py --r-version 4.5.3 --out dist   # about 1 minute
uv tool install --reinstall dist/zur-4.5.3-py3-none-manylinux_2_28_x86_64.whl
ZUR_R_VERSION=4.5.3 uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Invariants that are easy to break

- `unshare()` must run before anything edits the prefix. rattler
  hardlinks files from its cache, so an in-place edit corrupts the cache
  for every later build (design §9).
- The wheel may not contain symlinks; `dedupe()` removes all of them.
- The build deletes its prefix after packing. Tests rely on that to
  prove nothing still points at it.
- `src/zur/__init__.py` is copied into the wheel; its `__version__`
  placeholder `"0+unknown"` is replaced by the build.
- The repository's `pyproject.toml` is the build environment, not the
  `zur` distribution. Wheel metadata lives in `pack()`.

## Tests

Tests run against the installed tool, not the source tree, and compare
against known answers. Add a check to `tests/test_installed.py` for any
new behaviour.

## Commits and pull requests

Short imperative subjects. Branch from `main` and open a pull request.
