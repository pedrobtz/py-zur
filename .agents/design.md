# zur design: R installed with `uv tool install`

- **Status:** Authoritative. This document is the single source of truth
  for `zur`; it supersedes RFC 0080 in `pedrobtz/packages`, which it
  started from. Decisions in §15 are the author's calls and stay open
  until the maintainer confirms them here.
- **Date:** 2026-11-23.
- **Package:** distribution, import and repository name `zur`
  (`pedrobtz/py-zur`); commands `R` and `Rscript`. `zur` was free on PyPI
  on 2026-11-23. Licence GPL-2.0-or-later, because the wheel
  redistributes R.
- **Current state:** stage 1 (§14) in progress. `scripts/build.py`
  builds a working `linux-64` wheel and `tests/test_installed.py` passes
  13 checks against it. CI is written but has not run yet.

## 1. What zur is

One command puts a working R on a machine, with no administrator rights,
no system package manager and no installer:

```sh
uv tool install zur              # latest R
uv tool install zur==4.4.3       # a specific R
uvx --from zur Rscript -e 'sessionInfo()'
```

`uv` places `R` and `Rscript` in its tool bin directory (`~/.local/bin`
by default). `uv tool upgrade zur` moves to the next R, and
`uv tool uninstall zur` removes it completely.

The same wheel works with plain `pip` and in a project environment, so a
Python project can pin an R version in `pyproject.toml`, for example
next to `rpy2`, under the same lock file as its Python dependencies.

### 1.1 What zur is not

- Not a version manager. It does not switch R versions on `PATH`, which
  is what `rig` does. `uv tool install zur==X` replaces the tool's R, and
  `uvx --from zur==X` runs another version ad hoc.
- Not a new build of R. It repackages conda-forge's builds (§4).
- Not an R package manager. `install.packages()` and `pak` work as usual
  (§11). It ships R's base and recommended packages and nothing else.
- Not a bridge between Python and R. That is `rpy2`'s job, and `rpy2`
  can use `zur` as its R.

## 2. Goals

1. **One command, no privileges.** `uv tool install zur` works on a
   locked-down corporate laptop where installing R needs a ticket.
2. **Every platform R users have.** Linux x86_64 and aarch64, macOS
   arm64 and x86_64, Windows x86_64.
3. **Faithful R.** `R` and `Rscript` behave like a CRAN installation:
   same front-end, same `R CMD`, same `R.home()` layout, the same
   recommended packages.
4. **Version per release.** `zur==4.5.3` means R 4.5.3.
5. **Reproducible builds.** A release is a function of a lock file and
   the build script.

## 3. Prior art

Shipping a non-Python binary in a wheel is established practice:
`ziglang`, `cmake`, `ninja`, `clang-format` and `nodejs-wheel` all do it.
For R, nothing current exists:

- **`rportable`** (PyPI): R Portable 3.2.3 in a wheel. Windows only, R
  from 2015, abandoned.
- **`r-lib/ir`** (PyPI `r-lib-ir`): a `uv tool install`-able runner for
  self-describing R scripts (`ir`) and package executables (`rx`). It
  needs R already installed and picks versions through `rig`. `zur`
  could be its R provider (§16).
- **`portable-r-macos`, `portable-r-windows`**: relocatable CRAN builds
  as archives on GitHub Releases. Not on PyPI, and no Linux runtime.
- **`rig`**: installs and switches system R versions; needs admin rights
  on macOS and Windows.
- **conda-forge `r`**: relocatable R for every platform, but it needs
  conda, mamba or pixi.

## 4. Approach: repackage conda-forge

Each wheel is one platform's conda-forge R environment, minus what only a
compiler needs, rewritten to run from wherever pip or uv unpacks it.

conda-forge names R in layers: `r-base` is R with the base packages only
(it is built with `--with-recommended-packages=no`), `r-recommended` adds
R's 15 recommended packages, and the metapackage `r` is the two together.
`zur` installs `r-base` plus `r-recommended`, which is what a CRAN
installation contains, because much code assumes `MASS`, `Matrix` and
`survival` are present.

Why conda-forge rather than CRAN binaries or a source build:

- **Relocation is mostly done.** conda-forge builds libraries with
  `$ORIGIN`-relative RPATHs on Linux and `@rpath` install names on
  macOS, because conda installs into arbitrary prefixes. CRAN's macOS
  build is a framework at a fixed path, and CRAN has no Linux binaries.
- **Every platform from one recipe.** R 4.5.3 solves on all five targets
  (§5.3).
- **Dependencies included.** OpenBLAS, ICU, libcurl, cairo, Tcl/Tk and
  fonts come as conda packages, so nothing depends on system libraries
  beyond glibc on Linux.

The build solves and installs with `py-rattler`, so CI needs no conda.
A conda install rewrites the prefix into files at install time; a wheel
has no install step, so the build does the equivalent once (§8).

## 5. Measurements

### 5.1 What works

`scripts/build.py --r-version 4.5.3` builds
`zur-4.5.3-py3-none-manylinux_2_28_x86_64.whl` on Ubuntu 24.04. The
build deletes its prefix after packing, so any leftover reference fails.
After `uv tool install`, `tests/test_installed.py` checks, against known
answers:

- `R.home()` is inside the tool's `zur/_runtime`; an inherited `R_HOME`
  is ignored; the `R` front-end reads a script from stdin.
- All 15 recommended packages load.
- BLAS is OpenBLAS, and `solve()` on a 2×2 matrix gives the exact
  inverse.
- `png`, `cairo`, `ICU`, `libcurl` and `tcltk` capabilities; an SVG plot
  with non-ASCII text; `Europe/Zurich` time zone conversion; HTTPS.
- `R CMD INSTALL` of a pure-R package, and of a C package compiled by
  the system gcc.
- Under `strace`, no successful open of any path in the build prefix.

### 5.2 Size, linux-64

| Item | Packages | Download | On disk |
| --- | --- | --- | --- |
| Full environment, toolchain included | 105 | 325 MB | |
| Runtime, 14 toolchain packages dropped | 91 | 118 MB | 366 MB |
| Wheel after deduplication (§9) | | **138 MB** | 305 MB |

The deduplicated wheel holds 6,746 files; its on-disk size is the
installed tool environment. Its parts: shared libraries 68 MB (ICU data
13 MB, OpenBLAS 13 MB, libstdc++ 9 MB), recommended packages 25 MB, base
packages 24 MB, `share/` 6 MB, the rest of `lib/R` 6 MB, `bin/` 4 MB,
fonts 4 MB. Within the recommended packages, R code is 9 MB, vignettes
6 MB and help 5 MB.

A first build without the recommended packages was 113 MB after
deduplication and 325 MB before it, with 1.4 GB on disk for the full
environment.

### 5.3 Other platforms

R 4.5.3 with the recommended packages solves on every target (conda
downloads, before pruning and deduplication, checked 2026-11-23):

| Platform | Packages | Runtime | Toolchain (dropped) |
| --- | --- | --- | --- |
| `linux-64` | 105 | 118 MB | 207 MB |
| `linux-aarch64` | 106 | 126 MB | 163 MB |
| `osx-arm64` | 119 | 152 MB | 112 MB |
| `osx-64` | 119 | 158 MB | 118 MB |
| `win-64` | 93 | 254 MB | 125 MB |

Only `linux-64` was built and run. On `win-64`, 115 MB of the runtime is
MKL, which the solver picks as BLAS unless OpenBLAS is pinned (D8).

## 6. Wheel layout

```text
zur/
  __init__.py            launcher (§7), from src/zur/__init__.py
  _runtime/              the conda prefix, pruned and rewritten
    BUILD-INFO.json      every bundled package: version, build, licence
    bin/  lib/  lib/R/  etc/  fonts/  share/  ssl/
zur-4.5.3.dist-info/
  METADATA  WHEEL  RECORD  entry_points.txt
```

`entry_points.txt` declares `R = zur:R` and `Rscript = zur:Rscript` as
console scripts. `uv tool install` links exactly the console scripts a
distribution declares. On Windows they become `R.exe` and `Rscript.exe`.

The wheel is tagged `py3-none-<platform>`: no Python extension, so any
Python 3 works, but one wheel per platform. Linux tags are
`manylinux_2_28_*`, since glibc is the only system library left (D4).

Pruned: `conda-meta/`, `include/` (R's own headers stay in
`lib/R/include`), `share/{man,info,doc,gir-1.0}`, `lib/pkgconfig`,
`lib/cmake`, static libraries and R's PDF manuals.

## 7. Launcher

Each command sets the environment and replaces the Python process with
R's own front-end, so signals, exit codes and the terminal are R's
(`os.execve`). `environment()` drops any inherited `R_HOME`, sets
`RHOME` (which `Rscript` reads), and points `SSL_CERT_FILE`,
`CURL_CA_BUNDLE`, `OPENSSL_CONF`, `FONTCONFIG_PATH`, `FONTCONFIG_FILE`
and `TZDIR` at bundled files, with `setdefault` so user settings win.

The cost is one Python start: 17 ms in the prototype (median of eight
`Rscript -e 1` runs, 129 ms against 111 ms direct). Shell launchers under
`.data/scripts/` would avoid it, at the price of a second set of `.bat`
files on Windows.

## 8. Relocation

R's compiled code finds its home from `R_HOME`; what breaks after a move
is in text files. `relocate()` rewrites:

- **`lib/R/bin/R`**: hardcodes `R_HOME_DIR`, `R_SHARE_DIR`,
  `R_INCLUDE_DIR` and `R_DOC_DIR`. Rewritten to compute the prefix from
  the script's own location.
- **`lib/R/etc/Renviron`**: the `SED` path, now
  `${R_HOME}/../../bin/sed`.
- **`lib/R/etc/ldpaths`**: `R_LD_LIBRARY_PATH`, now
  `${R_HOME}/../../lib`.
- **`lib/R/etc/Makeconf`**: line 1 sets `R_HOME=`, paths are hardcoded,
  and the compilers are conda's `x86_64-conda-linux-gnu-*`. Line 1 is
  dropped (R exports `R_HOME` to make; keeping it with a rewritten value
  gives make's "recursive variable" error), paths become
  `$(R_HOME)/../..`, and compilers become the system `gcc`, `g++`,
  `gfortran`.
- **`etc/fonts/fonts.conf`**: the font directory, now
  `<dir prefix="relative">`.

Some libraries embed the prefix as a fallback (libcurl, OpenSSL,
fontconfig, Kerberos, Tcl); the launcher's variables cover the ones R
uses. The remaining lookups seen under `strace` are fontconfig's
optional `local.conf` and `conf.avail`, which fail harmlessly. The build
prefix is a fresh `tempfile.mkdtemp`, so a stale reference cannot find a
real file on a user's machine.

## 9. Symlinks and hardlinks

Wheels cannot contain symlinks; storing the environment's 1,429 as
copies would carry OpenBLAS eight times. `dedupe()`:

1. Where several SONAMEs resolve to one file, rewrites `DT_NEEDED` to the
   file's own SONAME with `patchelf --replace-needed` (`libblas.so.3`,
   `libcblas.so.3`, `liblapack.so.3` → `libopenblas.so.0`).
2. Turns a symlink named after its target's SONAME into the real file.
3. Drops other library links; stores links to other files as copies.

**Hardlinks:** rattler installs files as hardlinks into its package
cache. Patching them in place edits the cache shared by every later
build on the machine, which the prototype did by accident: later builds
found libraries already rewritten. `unshare()` gives every file its own
inode before anything is modified. A test for this belongs in stage 2.

macOS needs `install_name_tool` instead of `patchelf`, then ad-hoc
signing (`codesign -s -`). Windows DLLs are not symlinked.

## 10. Versioning

- **Distribution version = R version.** A rebuild of the same R is
  `4.5.3.post1` (`--post 1`), so `zur==4.5.3` still selects it.
- **One release per R patch version**, from 4.4.0 at first.
- **Locked inputs.** Each release commits a lock file per platform (not
  yet implemented). `BUILD-INFO.json` records every package's build.
- **R-devel** stays off PyPI.

## 11. R packages

- **Base and recommended packages** sit inside the tool environment and
  are replaced wholesale by `uv tool upgrade`.
- **User packages** go to R's default user library,
  `~/R/<platform>-library/<major.minor>`, outside the tool environment,
  so they survive reinstalls. `R_LIBS_USER` overrides it.
- **Binary packages** from P3M link against Ubuntu system libraries the
  wheel does not provide; packages without system dependencies are fine.
  Untested (stage 3).
- **Source packages** compile with the system toolchain through the
  rewritten `Makeconf`. conda-forge's compilers are not shipped.

## 12. Platforms

- **Linux** (`linux-64`, `linux-aarch64`): `linux-64` works; aarch64
  should behave the same.
- **macOS** (`osx-arm64`, `osx-64`): `@rpath` install names already
  relocate; the work is the `bin/R` rewrite, `install_name_tool`
  deduplication and ad-hoc signing. Tag `macosx_11_0`, because
  conda-forge's R 4.5.3 requires `__osx >=11.0`.
- **Windows** (`win-64`): R on Windows is relocatable since R 2.x. Pin
  OpenBLAS to avoid MKL. Rtools is not bundled.

## 13. Size and PyPI limits

PyPI's default limit is 100 MB per file; the linux-64 wheel is 138 MB.
In order of preference:

1. **Request a higher limit** (200 MB covers every platform), as PyPI
   grants for binary distributions. File it during stage 1.
2. **Prune further:** recommended packages' vignettes (6 MB), `po`
   translations and `share/locale`, Tcl/Tk, X11 on a headless build, ICU
   data. Each changes behaviour, so each is a decision (D6).
3. **Split the wheel** into `zur` and `zur-libs`. Last resort.

The project total (10 GB default) also needs a raise: five platforms at
about 140 MB is about 700 MB per R version.

## 14. Stages

1. **Build script and wheel for `linux-64`.** Done when CI builds the
   wheel and the tests pass on Ubuntu 24.04, Debian 12 and Rocky Linux 8
   containers. *Code done; CI not yet run.*
2. **Relocation and cache tests.** The `strace` test exists; add a test
   that the build leaves the rattler cache unmodified.
3. **Packages.** C++ and Fortran source packages (Fortran needs
   `gfortran` in the containers), and a binary from P3M.
4. **Lock files.** Commit a conda lock per platform and build from it.
5. **`linux-aarch64`.** Stage 1 checks on an arm64 runner.
6. **macOS.** Both architectures, signing included.
7. **Windows.** `R.exe` and `Rscript.exe`, with Rtools for source builds.
8. **Release pipeline.** A tag builds every wheel and publishes to PyPI
   through trusted publishing; a smoke test installs from PyPI.
9. **Back-catalogue.** Every R patch release from 4.4.0.

## 15. Decisions

- **D1. Runtime source: conda-forge `r-base` + `r-recommended`.**
  Alternatives: CRAN binaries (no Linux, fixed macOS path), a source
  build (a matrix to maintain), portable-r archives (no Linux).
- **D2. Name: `zur`.** Short, free, no collision (unlike `r-lang` with
  rlang, `r-env` with renv, `r-box` with box, `r-base` with conda-forge),
  and part of the `zu*` family. It does not describe itself, so the PyPI
  summary and README carry that.
- **D3. Launcher: Python console scripts.** 17 ms per start, identical on
  every platform.
- **D4. Linux tag: `manylinux_2_28`.** Covers RHEL 8 and Ubuntu 20.04+;
  conda-forge needs glibc 2.17, so `manylinux2014` may be possible.
- **D5. No compiler in the wheel.** Source packages use the system
  toolchain.
- **D6. Prune only what a user would not notice.** Anything that changes
  `capabilities()` or removes documentation users read stays until a
  decision says otherwise.
- **D7. The build script writes the wheel directly.** `pyproject.toml`
  only describes the build environment; no hatchling.
- **D8. BLAS: OpenBLAS everywhere**, pinned in the solve. Accelerate on
  macOS is faster but changes numerical results across platforms.

## 16. Open questions

1. Does PyPI grant the per-file limit raise? If not, §13 option 2 or 3.
2. Do P3M Linux binaries work against the bundled libraries?
3. Should `zur` register with RStudio or Positron's R discovery?
4. Offer `ir`'s maintainer `zur` as its R provider?
5. Licensing: `BUILD-INFO.json` lists each package's licence; the
   licence texts themselves should ship under `zur/_runtime/licenses/`.
