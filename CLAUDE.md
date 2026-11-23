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

```sh
uv sync
uv run scripts/build.py --r-version 4.5.3 --out dist   # about 1 minute
uv tool install --reinstall dist/zur-4.5.3-py3-none-manylinux_2_28_x86_64.whl
uv run pytest tests/test_unit.py                       # no build needed
ZUR_R_VERSION=4.5.3 uv run pytest tests/test_installed.py
uv run ruff check . && uv run ruff format --check .
```

## Invariants that are easy to break

These describe the prototype's build. The symlink and wheel-metadata
rules carry over to the new design; the `unshare()` rule applies only
while the build installs from rattler's cache.

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

Every test compares against a known answer. The suite runs in a random
order (`pytest-randomly`) and prints its seed; `--randomly-seed=<seed>`
repeats an order. Checks that need no build go in `tests/test_unit.py`;
checks against the installed tool, not the source tree, go in
`tests/test_installed.py`. Add a check for any new behaviour.

## Commits and pull requests

Short imperative subjects. Branch from `main` and open a pull request.
