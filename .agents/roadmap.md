# zur: roadmap

Status: draft 2026-11-24. Implements [design.md](design.md) as completed
on 2026-11-23 (approach C, §4) and replaces the roadmap written for the
conda-forge prototype ([§17](design.md#17-background-the-conda-forge-prototype)).
Stage 1 is that prototype, kept as history; every later stage follows the
new design.
Target: the first PyPI release, `zur` 4.6.2
([§11](design.md#11-versioning-and-r-versions)). Linux x86_64 ships
first (stage 19); Linux aarch64, macOS arm64, macOS x86_64 and Windows
x86_64 join the same version as each passes (stages 20 to 23), per
[D12](design.md#15-decisions) and [§12](design.md#12-speed-benchmark).
Tracking: [First PyPI release (#2)](https://github.com/pedrobtz/py-zur/issues/2),
with one sub-issue per stage; [PyPI-release (#25)](https://github.com/pedrobtz/py-zur/issues/25).

## Stages

| Stage | Title | CI | Issue | Pull request | Done |
| --- | --- | --- | --- | --- | --- |
| 1 | [linux-64 wheel](#stage-1--linux-64-wheel) | quick | — | [#1](https://github.com/pedrobtz/py-zur/pull/1) | 2026-11-23 |
| 2 | [Project identity and CI profiles](#stage-2--project-identity-and-ci-profiles) | quick | [#3](https://github.com/pedrobtz/py-zur/issues/3) | [#27](https://github.com/pedrobtz/py-zur/pull/27) | 2026-11-24 |
| 3 | [R from source in manylinux](#stage-3--r-from-source-in-manylinux) | quick | [#4](https://github.com/pedrobtz/py-zur/issues/4) | [#28](https://github.com/pedrobtz/py-zur/pull/28) | 2026-11-24 |
| 4 | [Pinned dependencies](#stage-4--pinned-dependencies) | quick | [#5](https://github.com/pedrobtz/py-zur/issues/5) | [#29](https://github.com/pedrobtz/py-zur/pull/29) | 2026-11-24 |
| 5 | [Repair and the Linux x86_64 wheel](#stage-5--repair-and-the-linux-x86_64-wheel) | quick | [#6](https://github.com/pedrobtz/py-zur/issues/6) | [#30](https://github.com/pedrobtz/py-zur/pull/30) | 2026-11-24 |
| 6 | [Headless runtime files](#stage-6--headless-runtime-files) | quick | [#7](https://github.com/pedrobtz/py-zur/issues/7) | [#31](https://github.com/pedrobtz/py-zur/pull/31) | 2026-11-24 |
| 7 | [User library](#stage-7--user-library) | quick | [#8](https://github.com/pedrobtz/py-zur/issues/8) | | |
| 8 | [pak and P3M binaries](#stage-8--pak-and-p3m-binaries) | quick | [#9](https://github.com/pedrobtz/py-zur/issues/9) | | |
| 9 | [Prune and size budget](#stage-9--prune-and-size-budget) | quick | [#10](https://github.com/pedrobtz/py-zur/issues/10) | | |
| 10 | [Source packages across host compilers](#stage-10--source-packages-across-host-compilers) | full | [#11](https://github.com/pedrobtz/py-zur/issues/11) | | |
| 11 | [zur doctor](#stage-11--zur-doctor) | quick | [#12](https://github.com/pedrobtz/py-zur/issues/12) | | |
| 12 | [First-run warning](#stage-12--first-run-warning) | quick | [#13](https://github.com/pedrobtz/py-zur/issues/13) | | |
| 13 | [Licences and build info](#stage-13--licences-and-build-info) | quick | [#14](https://github.com/pedrobtz/py-zur/issues/14) | | |
| 14 | [Source mirror](#stage-14--source-mirror) | quick | [#15](https://github.com/pedrobtz/py-zur/issues/15) | | |
| 15 | [Versioning and release pipeline](#stage-15--versioning-and-release-pipeline) | full | [#16](https://github.com/pedrobtz/py-zur/issues/16) | | |
| 16 | [Benchmark and release gate](#stage-16--benchmark-and-release-gate) | quick | [#17](https://github.com/pedrobtz/py-zur/issues/17) | | |
| 17 | [README and wheel metadata](#stage-17--readme-and-wheel-metadata) | quick | [#18](https://github.com/pedrobtz/py-zur/issues/18) | | |
| 18 | [Review](#stage-18--review) | full | [#19](https://github.com/pedrobtz/py-zur/issues/19) | | |
| 19 | [Release Linux x86_64](#stage-19--release-linux-x86_64) | full | [#20](https://github.com/pedrobtz/py-zur/issues/20) | | |
| 20 | [Linux aarch64](#stage-20--linux-aarch64) | full | [#21](https://github.com/pedrobtz/py-zur/issues/21) | | |
| 21 | [macOS arm64](#stage-21--macos-arm64) | full | [#22](https://github.com/pedrobtz/py-zur/issues/22) | | |
| 22 | [macOS x86_64](#stage-22--macos-x86_64) | full | [#23](https://github.com/pedrobtz/py-zur/issues/23) | | |
| 23 | [Windows x86_64](#stage-23--windows-x86_64) | full | [#24](https://github.com/pedrobtz/py-zur/issues/24) | | |

Stages 2 to 19 are the first wheel's critical path and touch no other
platform. Stages 20 to 23 are independent of each other and may be
worked in any order once stage 19 is done; each adds its wheel to the
version then current on PyPI.

## CI profiles

This repository has no r-actions workflows; it is a wheel build, so the
R package profiles are replaced by two of its own, both in
`.github/workflows/build.yml`:

- **quick** (every pull request and every push to `main`): `ruff`; the
  build-free tests (`tests/test_unit.py`: size check on synthetic
  wheels, version rules, `zur doctor`'s tables, the benchmark gate on
  fixture data); the Linux x86_64 build in the `manylinux_2_28` image
  pinned by digest, with built dependencies cached under a key of the
  dependency manifest's hash and the image digest; `tests/test_build.py`
  against the wheel; and `tests/test_installed.py` against the installed
  tool in the Ubuntu 24.04, Debian 12 and Rocky Linux 8 containers and
  the two minimal images (`debian:12-slim`, `rockylinux:8-minimal`) from
  stage 6 on. Target: under 20 minutes with a warm dependency cache,
  measured in stage 5 and recorded here. (1 min 46 s for the prototype
  after #1, which built nothing from source.) R alone, from source in
  the `r-source` job: 147 to 189 s (two runs) for `configure`, `make -j4` and
  `make install` on `ubuntu-latest`'s 4 cores, plus about 40 s of
  `dnf install` (stage 3, 2026-11-24). Built dependencies (OpenSSL and
  libcurl, stage 4, 2026-11-24): cold 124 to 219 s over five runs of
  `build-deps.sh`; warm, a cache restore of 8 MB in about 1 s. The
  whole quick profile with the source build, the repair, the build
  checks and the three containers (stage 5, 2026-11-24): 4 min 53 s
  from start to the last job with a warm dependency cache, of which
  `build-linux-x86_64` 3 min 57 s (R 147 s) and each container about
  35 to 55 s
  ([run](https://github.com/pedrobtz/py-zur/actions/runs/45310120781)),
  well under the target.
- **full** (`full-ci` label, weekly schedule, dispatch, and every run of
  the release workflow): quick, plus the host compiler matrix of stage
  10, plus every other platform leg that exists by then: Linux aarch64
  on `ubuntu-24.04-arm`, macOS arm64 on `macos-latest`, macOS x86_64 on
  an Intel runner while one exists
  ([§16](design.md#16-open-questions) question 4), and Windows x86_64
  (built on `ubuntu-latest`, tested on `windows-latest`). macOS runners
  are shared with every other repository of the maintainer's, so a stage
  that touches no platform code stays `quick`. The label is read when
  a run starts, so it takes effect on the next push or `gh run rerun`,
  not on labelling (stage 2, 2026-11-24).

A stage is `full` when it adds a platform, a leg that only the full
profile runs, or code that only another platform's leg exercises.
Documentation-only pull requests (`*.md`, `.agents/`) skip the build,
except `README.md` from stage 17 on, because the wheel's long
description is read from it.

Two workflows sit outside the profiles: `release.yml` (stage 15) and
`benchmark.yml` (stage 16), each run by tag or dispatch.

**Where things are verified.** The maintainer's machine is macOS x86_64
without Docker. Linux builds and every container test run only in GitHub
Actions; on the machine only `ruff`, `tests/test_unit.py` and, from
stage 22, the macOS x86_64 build and its installed-tool tests can run.
"Seen to fail" in an exit criterion means: a temporary commit on the
stage's pull request breaks the thing the test guards, the failing CI
run is linked in the pull request description, and the commit is
reverted before merge. A measurement an exit criterion asks for is
written into the design (§10 or §18) in the same pull request, as
`CLAUDE.md` requires.

## Stage 1 — linux-64 wheel

Done in [#1](https://github.com/pedrobtz/py-zur/pull/1): the conda-forge
prototype. The wheel builds in CI and `tests/test_installed.py` passes in
Ubuntu 24.04, Debian 12 and Rocky Linux 8 containers. No issue; it
predates this roadmap. It was set aside on 2026-11-23 for size, the lack
of conda-forge's R 4.6 `r-recommended`, and scope. What carries over: the
launcher, the relocation of `bin/R`, `Makeconf`, `Renviron` and
`ldpaths`, symlink removal by SONAME, `pack()`, and the `strace`
relocation test.

**Design.** [§17](design.md#17-background-the-conda-forge-prototype).

## Stage 2 — Project identity and CI profiles

**Issue.** [#3](https://github.com/pedrobtz/py-zur/issues/3), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`.

**Goal.** The repository carries its licence, labels and CI economy, so
every later stage starts from a shuffled, gated suite.

**Work.**
- `LICENSE` with the GPL-2.0-or-later text the wheel metadata claims.
- Labels `stage` and `full-ci`.
- `build.yml` split into the quick and full profiles above. The full
  profile has no extra legs yet; its trigger (`full-ci`, weekly
  schedule, dispatch) is what this stage sets up. `paths-ignore` skips
  Markdown and `.agents/` changes.
- `pytest-randomly` in the dev group, so the suite runs shuffled and
  prints its seed.
- An empty `tests/test_unit.py` job in the quick profile, run without a
  build, which later stages fill.
- `CLAUDE.md` "Current state" points at this roadmap instead of
  restating it.

**Tests.**
- Shuffled suite: the `pytest` header in CI shows a random seed.
- Profile gating: a pull request without `full-ci` runs only the quick
  jobs, and one with it runs the full ones too, both seen on this
  stage's pull request.

**Exit criteria.**
- `LICENSE` exists and matches `License-Expression` in `pack()`.
- The quick profile is green on the pull request.
- Both gating behaviours above were seen in CI.

**Deviations.** 2026-11-24:
- `pack()` writes `License: GPL-2.0-or-later` (metadata 2.1); the field
  becomes `License-Expression` in stage 13. The exit criterion is met
  against the field `pack()` has now, and the check accepts either name.
- `tests/test_unit.py` is not empty: a pytest run that collects nothing
  exits 5 and fails the job, so it starts with the checks this stage
  owes (the `LICENSE` text and the metadata's licence, and that the
  suite is shuffled).
- The full profile's gate is a `profile` job whose `full` output the
  full jobs read (`needs: profile`, `if: needs.profile.outputs.full ==
  'true'`); it has one placeholder job, `full`, until stage 10. Label
  events do not start runs: a run on a label change replaces the checks
  of the run before it, and skipping such runs showed a green-looking
  `skipping` over real results (seen on #27). `profile` reads the pull
  request's labels when it runs, so `full-ci` takes effect on the next
  push or `gh run rerun`.

**Design.** [§15 D7](design.md#15-decisions).

## Stage 3 — R from source in manylinux

**Issue.** [#4](https://github.com/pedrobtz/py-zur/issues/4), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. A new job runs in the `manylinux_2_28_x86_64` image;
Linux only, so it is verified in Actions, not on the maintainer's
machine. The prototype's build and test jobs stay unchanged until stage
5 replaces them, so `main` keeps its installed-tool tests throughout.

**Goal.** R 4.6.2 builds from its source tarball in the manylinux image
with the options of §6.2, its own libraries need no compiler runtime
from the machine, and the result runs inside the build container.

**Confirm first.** R's link with `-static-libgfortran` (and static
`libquadmath`) succeeds in the image, and no ELF file R installs has
`libgfortran.so.5`, `libquadmath.so.0` or `libgomp.so.1` among its
`DT_NEEDED`. §18 found 13 absolute relocations in the toolset's
`libgfortran.a`; whether R's link pulls one in is what this answers.

**Work.**
- The image pinned by digest in a committed file; the R 4.6.2 tarball
  fetched by URL and checked against its SHA-256 before anything runs.
- `configure` exactly as in [§6.2](design.md#62-configure), with
  `-static-libgfortran` in R's link flags. Libraries come from the
  image's `-devel` packages for now (stage 4 replaces those that are
  inadequate), installed by a committed list. No `pango-devel` (D14).
- `make` and `make install` into `/opt/zur`.
- The conda path (`solve`, `install`, `unshare`, the toolchain filter,
  `py-rattler` in `pyproject.toml`) is not used by the new job; it is
  removed in stage 5 together with the prototype jobs, and `CLAUDE.md`'s
  `unshare()` invariant goes with it.
- `scripts/build.py` may be split into per-platform modules under
  `scripts/`; D7 holds either way.

**Tests.** Run in the build job against the installed prefix, with
`/opt/zur/bin/Rscript --vanilla`:
- `R.version.string` is `R version 4.6.2 (…)`.
- `rownames(installed.packages(.Library))` equals a committed list of
  R 4.6.2's base packages, and no recommended package is present.
- `capabilities()`: `cairo`, `png`, `jpeg`, `tiff`, `ICU`, `libcurl`
  `TRUE`; `X11`, `tcltk`, `aqua` `FALSE`.
- No pango (D14): `grSoftVersion()[["pango"]]` is `""` and
  `grSoftVersion()[["cairo"]]` is not.
- Fortran and LAPACK work: `solve(matrix(c(2, 1, 1, 3), 2))` equals
  `matrix(c(0.6, -0.2, -0.2, 0.4), 2)` to `1e-12`, and
  `La_library()` names R's `libRlapack.so`.
- Static runtime: a script lists `DT_NEEDED` of every ELF file under
  the prefix and finds none of `libgfortran.so.5`, `libquadmath.so.0`,
  `libgomp.so.1`.

**Exit criteria.**
- The confirmation's answer is recorded in [§18](design.md#18-measurements-2026-11-23).
  If the static link failed, the fallback of §6.2 (the shared runtime
  grafted under a hashed name, used only by R's own libraries) is the
  first item of stage 5's Work, and the static-runtime test above is
  replaced by one that accepts only the hashed name.
- Every **Tests.** line exists and was seen to fail.
- The build time of R alone is recorded in the CI profiles section.
- Quick profile green, prototype jobs included.

**Deviations.** 2026-11-24:
- The static link failed (design [§18.1](design.md#181-measurements-2026-11-24-stage-3)):
  `libgfortran.a(async.o)` has a local-exec TLS relocation that
  cannot go into `libRlapack.so`. The fallback is taken: R links the
  shared `libgfortran.so.5` and `libquadmath.so.0`, and the graft under
  a hashed name is the first item of stage 5's Work. The hashed name
  exists only after stage 5's repair, so the static-runtime test is
  replaced here by an interim one: no ELF file needs `libgomp.so.1`,
  and exactly R's own libraries (a committed list in
  `tests/test_prefix.py`) need the Fortran runtime. Stage 5's build
  check accepts only the hashed name.
- `-static-libgfortran` is not what the attempt used: R links its
  libraries with `gcc`, which ignores it, so the attempt named the
  archives in `FLIBS` (`-l:libgfortran.a -l:libquadmath.a`).
- `configure` runs with `LIBnn=lib`, so `R_HOME` is `lib/R` as §6.3
  names it (recorded in §6.2).
- The job is `r-source`; it runs the pinned image with `docker run` so
  the digest is read from the committed file `scripts/linux/image`.

**Design.** [§6.1](design.md#61-build-environment), [§6.2](design.md#62-configure),
[§15 D1, D4, D8, D14, D17](design.md#15-decisions),
[§16](design.md#16-open-questions) question 1.

> Note on the fallback (design gap): §6.2 calls the fallback "the
> toolset's shared runtime", but §18 found that the toolset ships no
> runtime of its own, only linker scripts pointing at AlmaLinux 8's gcc
> 8.5 `libgfortran.so.5`. The graft would therefore be that gcc 8.5
> library under a hashed name. That is the reading this roadmap uses.

## Stage 4 — Pinned dependencies

**Issue.** [#5](https://github.com/pedrobtz/py-zur/issues/5), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Linux only, verified in Actions.

**Goal.** Every library R is built against is a pinned input, and the
ones the image provides at inadequate versions are built from source in
the same container.

**Work.**
- `manifests/linux.toml` (one per platform later): for each dependency,
  name, version, origin (`image` with the exact `dnf` package and
  version, or `source` with URL and SHA-256), SPDX licence, and for
  `image` entries one line saying why that version is adequate.
- libcurl and OpenSSL built from pinned sources (§16 question 2 assumes
  both are bundled and tracked); every other library decided by this
  stage and recorded in the manifest. The decisions that change §6.1's
  wording are written into the design.
- Built dependencies cached in CI under the manifest hash and image
  digest; a cache miss rebuilds them.
- `BUILD-INFO.json` gets its inputs section: R tarball and SHA-256,
  image digest, and every manifest entry.

**Tests.**
- Versions reach R: `libcurlVersion()` and its `ssl_version` attribute
  equal the manifest's libcurl and OpenSSL versions;
  `extSoftVersion()` reports the manifest's PCRE2, ICU, zlib, bzip2 and
  xz versions; each compared in the build job.
- Pinning holds: a manifest entry with a wrong SHA-256 fails the build
  before compiling, with a message naming the file, in
  `tests/test_unit.py` (runs locally).
- `BUILD-INFO.json` lists exactly the manifest's entries plus R.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- The cold and warm dependency build times are recorded in the CI
  profiles section.
- Quick profile green.

**Deviations.** 2026-11-24:
- The cache key also hashes `scripts/linux/build-deps.sh`, which holds
  the build recipes: a changed recipe must rebuild as a changed pin does.
- `scripts/linux/devel-packages.txt` is gone: the `-devel` packages are
  the manifest's `image` entries, and the unpinned build tools (`perl`
  for OpenSSL's `Configure`, `xz`) are in `scripts/linux/build-tools.txt`.
- Pinned: every library R's own files link, checked by
  `tests/test_prefix.py` against the scan of the prefix. Not pinned: the
  `-devel` packages `dnf` pulls in for those (for example cairo's
  pixman and X11 headers) and the runtime libraries they bring. Stage
  5's repair bundles whichever are outside the manylinux allowlist;
  stage 13's check that every bundled ELF file belongs to a
  `BUILD-INFO.json` entry is where they get entries.
- Added tests beyond the stage's list: the manifest names every library
  R links; no `/usr/local` path in `Makeconf` or `ldpaths`; the
  `rpm-check` refusal; cairo, libpng and libtiff versions through
  `grSoftVersion()`.

**Design.** [§6.1](design.md#61-build-environment), [§11](design.md#11-versioning-and-r-versions)
(pinned inputs), goal 6 in [§2](design.md#2-goals-non-goals-anti-goals).

## Stage 5 — Repair and the Linux x86_64 wheel

**Issue.** [#6](https://github.com/pedrobtz/py-zur/issues/6), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Linux only, verified in Actions. This stage replaces the
prototype's build and test jobs.

**Goal.** The source build becomes a `manylinux_2_28_x86_64` wheel that
`uv tool install` makes work in Ubuntu, Debian and Rocky containers,
with nothing pointing at the build.

**Work.**
- The Fortran runtime graft (stage 3 took the fallback, 2026-11-24):
  the image's `libgfortran.so.5` and `libquadmath.so.0` (gcc 8.5) copied
  into `zur.libs/` under hashed names, and the `DT_NEEDED` of exactly
  the files `tests/test_prefix.py` lists in `FORTRAN_USERS` rewritten
  to them. The build check accepts only the hashed names, replacing
  stage 3's interim test.
- Repair as in [§6.3](design.md#63-repair-and-relocation): every shared
  library outside the manylinux allowlist bundled; RPATHs rewritten to
  `$ORIGIN`-relative ones; grafts with hashed names in `zur.libs/`.
  `auditwheel repair` itself or the prototype's own code, whichever
  passes the checks below; the choice is recorded in §6.3.
- `lib/R/lib` holds exactly `libR.so`, `libRblas.so`, `libRlapack.so`,
  and copies of the last two as `libblas.so.3` and `liblapack.so.3`
  (D16). No compiler runtime anywhere on the library path (D11).
- Relocation from the prototype, adapted: `bin/R` computes `R_HOME`
  from its own location and exports `RHOME`; `etc/Makeconf`,
  `etc/Renviron`, `etc/ldpaths` lose the build prefix; `Makeconf`'s
  compilers become plain `gcc`, `g++`, `gfortran` (D5); its
  `SHLIB_OPENMP_*FLAGS` become `-fopenmp` (D17); `Rconfig.h` keeps the
  per-compiler `HAVE_ENUM_BASE_TYPE` of `a44d4e1`.
- CA certificates in `bin/R`: `CURL_CA_BUNDLE` and `SSL_CERT_FILE` point
  at the first system bundle found, each only when unset (§6.4, D13).
  The launcher stops setting environment beyond `R_HOME` and `RHOME`.
- `pack()` writes the wheel with tag `manylinux_2_28_x86_64`; the
  `__version__` placeholder is replaced; no symlinks.
- Prototype removed: conda solve, `unshare()`, `py-rattler`, the
  recommended-packages and OpenBLAS tests. `CLAUDE.md`'s commands and
  invariants updated.

**Tests.**
- Build checks, in `tests/test_build.py` against the wheel file: every
  `DT_NEEDED` resolves inside the wheel or to a library on the
  manylinux allowlist; `auditwheel show` reports `manylinux_2_28_x86_64`;
  `lib/R/lib` holds exactly the five files above; no member is a
  symlink; `RECORD` hashes match the members; no member contains the
  build prefix string.
- Front-ends, in `tests/test_installed.py`: `R.home()` is inside the
  tool directory; an inherited `R_HOME` is ignored; `R --vanilla` reads
  a script from stdin; `Rscript -e 'quit(status = 3)'` exits 3; an
  `Rscript` started from inside R with `system2()` reports the same
  `R.home()`.
- Base packages only: `installed.packages(.Library)` equals the
  committed base list.
- BLAS: the known answer of stage 3 through the installed tool, and
  `Makeconf` reports `CC = gcc`, `CXX = g++`, `FC = gfortran` and
  `SHLIB_OPENMP_CFLAGS = -fopenmp` (`R CMD config`).
- Source packages, smoke: the prototype's pure-R and C fixture packages
  install and return their known answers.
- HTTPS: `download.file()` of a small file over HTTPS succeeds in each
  container, and `Sys.getenv("CURL_CA_BUNDLE")` names that container's
  bundle (Debian path on Ubuntu and Debian, RHEL path on Rocky).
- Relocation: under `strace`, no successful open of any build-prefix
  path (the prototype's test, kept).

**Exit criteria.**
- If stage 3 took the fallback, the hashed graft is in `zur.libs/`, is
  linked only by R's own libraries, and the build check proves it.
- Every **Tests.** line exists and was seen to fail.
- The unpruned wheel size is recorded in [§10](design.md#10-size), and
  the quick profile's duration in the CI profiles section.
- Quick profile green in Ubuntu 24.04, Debian 12 and Rocky Linux 8.

**Deviations.** 2026-11-24:
- The repair is the build's own code, checked by `auditwheel show`
  (design §6.3); it grafts everything but glibc and the gcc runtime,
  including the allowlisted X11, expat and zlib libraries, which
  headless containers lack: 31 libraries in all.
- Build paths compiled into ELF files (`Rscript`'s R home, OpenSSL's
  module directory, debug information) are overwritten with a path of
  the same length under `/proc/0`, so no member names the build prefix.
  `$R_HOME/bin/Rscript` started without `RHOME` therefore fails; the
  launcher and `bin/R` always set it. Stages 6 and 7 start R that way
  and need `Rscript` to find its home on its own. Resolved 2026-11-24 in
  stage 6 (decision on #7): `bin/Rscript` is a script that sets `RHOME`
  from its own location, and nothing else sets it (design §5, D13).
- `CC` is exactly `gcc`: configure's `-std=gnu23` is rejected by the
  containers' gcc 8, 12 and 13 (design §6.3). `CXX` and `FC` keep their
  standard flag after the plain name, and the check accepts only `-std=`
  words there. `R CMD config` does not know `SHLIB_OPENMP_*FLAGS`, so
  that check reads `Makeconf`; it has C, CXX and F variants only.
- Jobs: `r-source` became `build-linux-x86_64`, which also packs the
  wheel (artifact `wheel-linux-x86_64`); `check-linux-x86_64` runs
  `tests/test_build.py` in a job of its own, so a failing build check
  does not hide the installed-tool results of `test-linux-x86_64`.
- `BUILD-INFO.json` no longer records the build prefix (no member may
  contain it); the tests take `/opt/zur` as a constant.
- Added tests beyond the stage's list: the cairo device loads its
  grafted libraries in each container; a user-set CA variable wins; no
  RUNPATH outside the wheel; the BLAS names are copies of R's code; the
  launcher sets only `R_HOME` and `RHOME`; `bin/R`'s relocation,
  `Makeconf`, `ldpaths`, the graft names and the scrub in
  `tests/test_unit.py`.

**Design.** [§5](design.md#5-wheel-layout-and-launcher),
[§6.3](design.md#63-repair-and-relocation), [§6.4](design.md#64-runtime-files-a-headless-container-lacks),
[§15 D3, D4, D5, D7, D11, D13, D16, D17](design.md#15-decisions).

## Stage 6 — Headless runtime files

**Issue.** [#7](https://github.com/pedrobtz/py-zur/issues/7), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Adds the two minimal images to the quick profile; Linux
only, verified in Actions.

**Goal.** R works in a minimal container with no fonts, no `tzdata` and
no `which`, and the environment it needs lives in R's own files.

**Work.**
- One small font family and a `fonts.conf` naming it and the system
  font directories, in the wheel; `bin/R` sets `FONTCONFIG_FILE` only
  when unset. The family is the builder's choice, recorded in §6.4 with
  its licence (stage 13 ships the licence text).
- `Sys.which()` and base R's start-up work without `/usr/bin/which`
  ([§6.4](design.md#64-runtime-files-a-headless-container-lacks)).
- `--with-internal-tzcode` confirmed in use (stage 3 configured it).
- Minimal images in the quick profile: `debian:12-slim` (no fonts, no
  `tzdata`, no CA bundle) and `rockylinux:8-minimal` (no `which`). The
  other containers stop installing `which`.

**Tests.**
- Graphics headless, with `DISPLAY` unset, in every container: `png()`
  writes a file starting with the PNG signature, `pdf()` one starting
  with `%PDF`, `svg()` one whose root element is `svg`; each draws the
  text `"Zürich 3 €"`. Text is drawn, not boxes: the SVG contains one
  glyph definition per distinct character, and the PNG differs from the
  same plot drawn without the text.
- Time zones: in `debian:12-slim`, `format(as.POSIXct("2026-07-01
  12:00", tz = "UTC"), tz = "Europe/Zurich", usetz = TRUE)` is
  `"2026-07-01 14:00:00 CEST"`.
- `which`: in `rockylinux:8-minimal`, R starts with empty stderr and
  `Sys.which("sh")` is non-empty.
- Environment in R's own files (D13): R started as `$R_HOME/bin/Rscript`
  without the launcher reports the same `FONTCONFIG_FILE` and
  `CURL_CA_BUNDLE` as through the launcher.
- Network: HTTPS as in stage 5, in `debian:12-slim` after installing
  `ca-certificates`; without a bundle, R still starts and `zur`'s
  `bin/R` leaves `CURL_CA_BUNDLE` unset.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- Quick profile green in all five containers.

**Deviations.** 2026-11-24:
- The decision on [#7](https://github.com/pedrobtz/py-zur/issues/7)
  was implemented first: `bin/Rscript` and `lib/R/bin/Rscript` are shell
  scripts that set `RHOME` from their location and run the compiled
  `libexec/Rscript` under `R_HOME`; `bin/R` and the launcher no longer
  set `RHOME`. The decision named `bin/exec/Rscript`, but `R CMD
  INSTALL` takes every file in `bin/exec` for a sub-architecture, so
  the second decision on #7 moved it to `libexec` (design §5).
  The scripts also drop an inherited `R_HOME`, which the decision's
  test with `R_HOME=/nonexistent/R` needs: `bin/R` otherwise prints its
  warning about it on stdout (design §5).
- `which`: configure's `WHICH` is `trap 'exit 0' 0; command -v`
  (decision on #7); `Sys.which` of a name that is also a shell builtin
  returns `""` (design §6.4).
- fontconfig 2.15.0 is built from pinned source (a scope change,
  decided on #7): the image's 2.13.1 cannot read the relative font
  directory a relocatable `fonts.conf` needs (design §6.1, §6.4).
- `png()` and the other bitmap devices: R without pango defaults to
  Xlib, so the build appends `options(bitmapType = "cairo")` to the
  system `Rprofile` (decision on #7, design §6.4).
- The font is a pinned "image" entry of `manifests/linux.toml`
  (`liberation-sans-fonts`, no sonames), not a fetched file.
- With fontconfig 2.15 built, `auditwheel show` reports
  `manylinux_2_27_x86_64`: the image's fontconfig was the only file
  that needed a glibc 2.28 symbol. The wheel keeps the
  `manylinux_2_28_x86_64` tag (D4), and stage 5's check now accepts any
  `manylinux_2_N` with N ≤ 28.
- The PDF check draws with `encoding = "WinAnsi"` (CP1252): `pdf()`'s
  default encoding has no euro sign, and the check requires empty
  stderr.
- The minimal images get only what the workflow needs (`tar` and `gzip`
  on Rocky, nothing on Debian before its CA bundle), so the source
  package and `strace` tests skip there and run in the other three.
- Added tests beyond the stage's list: what each minimal image lacks
  (so the checks there test what they claim); `Sys.which` of a missing
  name; `--with-internal-tzcode` in `Makeconf`'s configure call and the
  shipped zoneinfo; the font files and `fonts.conf` in the wheel; a
  user's `FONTCONFIG_FILE` wins; empty stderr from every graphics
  device; `bin/exec` holds only `R`; the grafted fontconfig is the
  pinned version; `bitmapType` is `"cairo"` and a user's value wins.

**Design.** [§6.4](design.md#64-runtime-files-a-headless-container-lacks),
[§13](design.md#13-how-it-is-tested) (graphics, network and data,
containers), [§15 D13](design.md#15-decisions).

> Design gap: §6.4 takes the CA bundle from the system, and
> `debian:12-slim` has none, so HTTPS (and with it the `pak` recipe of
> §6.6 and the benchmark's `debian:12-slim` case of §12) fails there
> until `ca-certificates` is installed. The design does not say whether
> `zur doctor` should report a missing bundle. This roadmap installs
> `ca-certificates` wherever a minimal image needs HTTPS and leaves
> `zur doctor` as specified; the maintainer may decide otherwise.

## Stage 7 — User library

**Issue.** [#8](https://github.com/pedrobtz/py-zur/issues/8), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Linux only, verified in Actions; the shared-home case
runs two containers from one job with `docker run -v`.

**Goal.** Packages go to zur's own user library, which names the
distribution, unless the user chose another (D10).

**Work.**
- `bin/R` sets `R_LIBS_USER` to `~/R/zur/<platform>/<major.minor>` when
  unset, and creates the directory. `<platform>` is
  `<arch>-<ID>-<VERSION_ID>` from `/etc/os-release`, for example
  `x86_64-ubuntu-24.04`; `etc/Renviron` keeps the user's value when set.
- No `/etc/os-release`: `<platform>` is `<arch>-linux`.

**Tests.**
- With `R_LIBS_USER` unset, `.libPaths()[1]` is
  `~/R/zur/x86_64-<ID>-<VERSION_ID>/4.6` and exists, with the expected
  ID per container.
- With `R_LIBS_USER` set to a temporary directory, `.libPaths()[1]` is
  that directory and no `~/R/zur` is created.
- Both hold when R is started as `$R_HOME/bin/Rscript` without the
  launcher.
- One home mounted into Ubuntu 24.04 and Rocky Linux 8 containers in
  turn ends with two library directories, one per distribution.
- A pure-R fixture installed into the library still loads after
  `uv tool install --reinstall` of the same wheel.
- A read-only home: R starts and exits 0.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- Quick profile green.

**Design.** [§5](design.md#5-wheel-layout-and-launcher), [§15 D10, D13](design.md#15-decisions).

> Design gaps, with the reading used here: §5 gives only an Ubuntu
> example of `<platform>`. `VERSION_ID` is used verbatim, so Rocky Linux
> 8.10 gives `x86_64-rocky-8.10` and a Rocky minor update starts a new
> library, although P3M's RHEL binaries are per major version; the
> maintainer may prefer the major version only on the RHEL family. The
> design does not say what happens without `/etc/os-release` or with a
> read-only home; `<arch>-linux` and "start without the library" are
> this roadmap's choice.

## Stage 8 — pak and P3M binaries

**Issue.** [#9](https://github.com/pedrobtz/py-zur/issues/9), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Linux only, verified in Actions.

**Goal.** The documented route of §6.6 gets binary packages on a
zur-built R, and the binary packages CI users install load and give
known answers.

**Confirm first.** In `ubuntu:24.04` and `rockylinux:8` containers with
no compiler on `PATH` (and with `libgomp1`/`libgfortran5`, or `libgomp`/
`libgfortran`, installed by the job, since `zur doctor` comes later),
the two commands of §6.6 install `pak`, and `pak::pkg_install("data.table")`
installs a P3M binary, with no `repos` or user-agent setting.

**Work.**
- The §6.6 recipe as a test helper, the single place the tests take it
  from (stage 17 checks the README against it).
- A binary package set chosen to cover plain C (`jsonlite`), C++
  (`Rcpp`), Fortran (`deSolve`, which links `libgfortran.so.5`), OpenMP
  (`data.table`, `libgomp.so.1`) and BLAS/LAPACK users (a package from
  §18's sample whose P3M binary links `libblas.so.3` and
  `liblapack.so.3`, picked by reading its `DT_NEEDED`), with the known
  answer for each committed beside it.
- The result recorded in §18.

**Tests.**
- `pak` recipe (§13), in Ubuntu 24.04 and Rocky Linux 8: `pak` installs,
  `pak::pkg_install("data.table")` succeeds with no compiler present,
  and `data.table::data.table(a = 1:3)[, sum(a)]` is `6`.
- Binary packages (§13), on Ubuntu 24.04: each package of the set
  installs through `pak` and returns its known answer;
  `data.table::getDTthreads()` is at least 2 on the runner, so the
  host's OpenMP is in use; the BLAS user's process maps `libblas.so.3`
  from `R.home("lib")` (read from `/proc/self/maps`).
- System requirements: as root in Ubuntu 24.04, `pak::pkg_install("xml2")`
  installs `libxml2` and `xml2::read_xml("<a>1</a>")` parses.

**Exit criteria.**
- The confirmation holds and is recorded in §18. If `pak` compiles from
  source instead, D9 is reopened ([§6.6](design.md#66-packages-pak-is-the-documented-route)):
  this stage stops, the maintainer decides a new D9 in the design, and
  stages 16 and 17 are revised before they start. Stages 9 to 15 do not
  depend on the answer.
- Every **Tests.** line exists and was seen to fail.
- Quick profile green.

**Design.** [§6.6](design.md#66-packages-pak-is-the-documented-route),
[§15 D9, D16, D17](design.md#15-decisions), [§16](design.md#16-open-questions) question 1.

## Stage 9 — Prune and size budget

**Issue.** [#10](https://github.com/pedrobtz/py-zur/issues/10), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. The size check's tests run locally; the wheel's size
only in Actions.

**Goal.** The Linux wheel holds only what a headless R uses, its size is
known by part, and the build fails over budget.

**Work.**
- The Linux part of [§9](design.md#9-what-a-headless-wheel-leaves-out):
  `doc/manual`, `NEWS.pdf` and other manuals, `library/*/doc`, `tests/`,
  `demo/`, the `translations` package and `po/` directories (D6, D19).
  Help pages, headers, `Makeconf`, ICU, readline and libcurl stay.
- `--size-report` prints the wheel's size by part (R's libraries, ICU
  data, bundled libraries, base packages, help, fonts).
- `--max-size <bytes>`, defaulting to PyPI's per-file limit as a named
  constant with a reference to where PyPI states it; CI passes the
  default.
- One build with `pango-devel` installed, run once by dispatch, to
  measure what D14 saves.

**Tests.**
- Budget, in `tests/test_unit.py`: a synthetic wheel at the limit passes
  and one byte over fails, with a message giving both sizes.
- Consequences of each prune, in `tests/test_installed.py`: `?lm`
  finds its help page; `vignette(package = "stats")` lists none;
  with `LANGUAGE=de`, `log(-1)`'s warning is in English; `R.h` is in
  `R.home("include")`; the base list and every earlier check unchanged.
- No dangling links: stage 5's `DT_NEEDED` check still passes.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- The size report and the pango measurement are in [§10](design.md#10-size);
  the Linux x86_64 wheel is under 100 MB, and its distance from the
  60 MB target is stated.
- Quick profile green.

**Design.** [§9](design.md#9-what-a-headless-wheel-leaves-out), [§10](design.md#10-size),
goal 4 in [§2](design.md#2-goals-non-goals-anti-goals), [§15 D6, D14, D19](design.md#15-decisions).

## Stage 10 — Source packages across host compilers

**Issue.** [#11](https://github.com/pedrobtz/py-zur/issues/11), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — adds the host compiler matrix, which only the full
profile runs.` Linux only, verified in Actions.

**Goal.** Source packages compile with the machine's own toolchain from
the oldest supported host compiler to the newest, even though
`Makeconf` and `Rconfig.h` were written for the build's compiler.

**Work.**
- Fixture packages in `tests/fixtures/`: C; C++ at R's default
  standard; C++ at the newest standard `Makeconf` offers; Fortran; C
  with OpenMP through `$(SHLIB_OPENMP_CFLAGS)`. Each returns a known
  answer.
- The matrix: `rockylinux:8` (gcc 8, the oldest supported), `ubuntu:24.04`
  (gcc 13, in between) and the newest Fedora release at the time of the
  stage, pinned by tag (the newest gcc in a current distribution), each
  with `gcc`, `g++`, `gfortran` and `make` installed.
- A recommended package from CRAN source: `install.packages("MASS",
  repos = "https://cloud.r-project.org")` into a temporary library, then
  `MASS::fractions(0.5)` prints `1/2` (D1: one `install.packages()`
  away).

**Tests.**
- Source packages (§13): every fixture installs and returns its known
  answer on every compiler of the matrix; the OpenMP fixture reports
  more than one thread.
- The `MASS` line above, on Ubuntu 24.04.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- Before writing the newest-standard and gcc 8 fixtures, the stage
  records in §18 which C++ standards `Makeconf` offers and which of them
  gcc 8 accepts. If R's default C++ standard is one gcc 8 rejects, the
  stage stops and the minimum supported host compiler goes back to the
  maintainer as a design decision; it is not skipped.
- Full profile green.

**Design.** [§6.3](design.md#63-repair-and-relocation), [§13](design.md#13-how-it-is-tested)
(source packages), [§15 D1, D5, D17](design.md#15-decisions).

> Design gap: §13 asks each compiler of the matrix to cover "the newest
> standard `Makeconf` offers". `Makeconf` records what gcc-toolset-14
> supports, which gcc 8 cannot meet for the newest standards. The
> reading used here: the newest-standard fixture runs on every host
> whose gcc accepts that standard and is skipped, with the gcc version
> in the reason, on the others; the default standard is never skipped.

## Stage 11 — zur doctor

**Issue.** [#12](https://github.com/pedrobtz/py-zur/issues/12), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. The tables and output format are tested locally in
`tests/test_unit.py`; behaviour in containers only in Actions.

**Goal.** `zur doctor` tells a CI job exactly which system packages
binary packages and source builds need, and prints the command to
install them (D11). Linux in this stage; macOS and Windows checks join
in stages 21 to 23.

**Work.**
- The `zur = zur:main` entry point and `zur doctor [--command]`
  ([§6.5](design.md#65-zur-doctor)).
- The RHEL-family survey (§16 question 5): `DT_NEEDED` of the same 16
  P3M packages for RHEL 8 and 9, recorded in §18, confirming the
  `libgomp`/`libgfortran` list there.
- Per-family tables: library file names, the package that provides
  each, the install command (`apt-get` for the Debian family, `dnf` for
  the RHEL family); `sudo` prefixed only when not root.
- Unrecognised distribution: the missing file names and the Debian and
  RHEL package names that provide them.
- A test-only override of the `os-release` path, so the unknown-family
  branch can be reached in a container.

**Tests.** (§13 `zur doctor`)
- In `debian:12-slim`: it names `libgomp1` and `libgfortran5` and exits
  non-zero, while `Rscript -e 1` exits 0.
- There, after `eval "$(zur doctor --command)"` (and `ca-certificates`,
  see stage 6), it exits 0 and a P3M `data.table` installed with the
  §6.6 recipe loads.
- In `ubuntu:24.04` with `libgomp1` and `libgfortran5` but no compiler:
  exit 0, and the toolchain is listed as absent.
- On a full image (Ubuntu 24.04 with `build-essential` and `gfortran`
  installed): it prints nothing to install.
- In `rockylinux:8`: it names `libgomp` and `libgfortran` and prints a
  `dnf` command.
- Run as a non-root user, `--command` starts with `sudo`; as root it
  does not.
- With an unknown `os-release`: the missing file names and both
  families' package names are printed.

**Exit criteria.**
- The RHEL survey is in §18 and §16 question 5 is marked answered.
- Every **Tests.** line exists and was seen to fail.
- Quick profile green.

**Design.** [§6.5](design.md#65-zur-doctor), [§15 D11](design.md#15-decisions),
[§16](design.md#16-open-questions) question 5.

## Stage 12 — First-run warning

**Issue.** [#13](https://github.com/pedrobtz/py-zur/issues/13), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`. Linux only, verified in Actions.

**Goal.** The first `R` or `Rscript` on a Linux machine that lacks what
binary packages need says so once, on stderr, without changing what R
does.

**Work.**
- The launcher runs `zur doctor`'s first-group check on Linux when no
  stamp exists under `~/R/zur/` for this zur version, writes the stamp
  whatever the result, and prints the note (missing libraries, the
  command, a pointer to `zur doctor`) to stderr before starting R.
- `ZUR_NO_CHECK=1` disables it; an unwritable stamp skips the check
  silently.

**Tests.** (§13 first-run warning)
- In `debian:12-slim`, the first `Rscript -e 1` prints the note on
  stderr and exits 0; the second prints nothing on stderr.
- With `ZUR_NO_CHECK=1`, the first prints nothing.
- On a full image, stderr is empty.
- With a read-only home, nothing is printed and R exits 0.
- A stamp for another zur version does not suppress the check.
- `$R_HOME/bin/Rscript -e 1`, started without the launcher, never
  prints the note.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- The launcher's start cost is measured again and recorded in §5 (17 ms
  in the prototype).
- Quick profile green.

**Design.** [§6.5](design.md#65-zur-doctor), [§15 D11](design.md#15-decisions).

## Stage 13 — Licences and build info

**Issue.** [#14](https://github.com/pedrobtz/py-zur/issues/14), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`.

**Goal.** Every wheel ships the licence text of everything it bundles,
and `BUILD-INFO.json` names every input exactly.

**Work.**
- The build copies R's `COPYING`, each manifest library's licence files
  and the font's licence into `zur/_runtime/licenses/<name>/`, and
  fails if a bundled library has none.
- `BUILD-INFO.json`: every bundled library with version, SPDX licence,
  source URL and SHA-256; libraries whose exact source cannot be
  identified listed as such, with a reason (§14); a field for the
  source release URL (stage 14 fills it).
- Wheel `METADATA`: `License-Expression: GPL-2.0-or-later` and one
  `License-File` per licence file.

**Tests.**
- Completeness, in `tests/test_build.py`: every library in
  `BUILD-INFO.json` has a non-empty `licenses/<name>/`, and every
  bundled ELF file belongs to R or to a `BUILD-INFO.json` entry.
- Refusal, in `tests/test_unit.py`: a manifest entry with no licence
  file fails the build with its name.
- `METADATA`'s `License-File` entries match `licenses/` exactly.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- Quick profile green.

**Design.** [§14](design.md#14-licensing), [§5](design.md#5-wheel-layout-and-launcher).

## Stage 14 — Source mirror

**Issue.** [#15](https://github.com/pedrobtz/py-zur/issues/15), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`, plus one dispatch of the new workflow job to a draft
GitHub release.

**Goal.** For each zur version, the corresponding source of everything
the wheels bundle is on a GitHub release of this repository (D15).

**Work.**
- `scripts/sources.py` collects the R tarball and every `source` entry
  of the manifests into a directory with a `SHA256SUMS` file, and
  fetches the source of `image` entries (AlmaLinux source RPMs of the
  pinned versions).
- A workflow job uploads them to the release `v<version>` (draft until
  stage 15 publishes it). Later platforms add their assets to the same
  release.
- `BUILD-INFO.json` and, in stage 17, the README point at the release.

**Tests.**
- In `tests/test_unit.py`: every entry of a fixture `BUILD-INFO.json`
  maps to one asset, and a missing or mismatched hash fails with its
  name.
- On the dispatch run: every `SHA256SUMS` line matches the asset
  uploaded to the draft release, and every source in the Linux wheel's
  `BUILD-INFO.json` is among them.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- A draft release with the Linux sources exists and the dispatch run is
  linked in the pull request.
- Quick profile green.

**Design.** [§14](design.md#14-licensing), [§15 D15](design.md#15-decisions).

> Reading used: §14 says "each zur version has a GitHub release". A
> `.postN` is a version, so it gets its own release, holding the sources
> of every platform it carries.

## Stage 15 — Versioning and release pipeline

**Issue.** [#16](https://github.com/pedrobtz/py-zur/issues/16), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — the pipeline runs the full profile before publishing.`
Verified by rehearsals on TestPyPI.

**Goal.** A tag publishes the wheels of a version by trusted publishing,
following the versioning rules of §11, and a smoke test installs them
from the index.

**Work.**
- The build takes `--r-version` and an optional `.postN` or `.devN`;
  the distribution version is the R version with that suffix.
- `release.yml`: on a tag `v<R version>[.postN]`, or by dispatch with
  the version, the index (`testpypi` or `pypi`) and the platforms;
  builds the requested platforms (Linux x86_64 for now), runs the full
  profile, publishes the source release (stage 14), uploads by trusted
  publishing (the `pypi` environment needs the maintainer's approval),
  then runs a smoke job per platform.
- A `.postN` carries every platform: before uploading, the workflow
  reads the index's JSON for every build of that R version and refuses
  unless this build has a wheel for each platform the newest one has
  (§11).
- An add-platform mode uploads only new platforms' wheels to a version
  that already exists (§12), refusing a platform already there.
- Prerequisite, done by the maintainer: pending trusted publishers for
  `zur` on TestPyPI and PyPI naming `release.yml` and its environments.

**Tests.**
- Version rules, in `tests/test_unit.py` with synthetic wheels on a
  local index: `zur==4.6.2.*` installs `4.6.2.post1` when `4.6.2` and
  `4.6.2.post1` exist; `zur==4.6.2` installs `4.6.2`; the installed
  `zur.__version__` equals the wheel's version.
- Carry check, in `tests/test_unit.py` with fixture index JSON: a
  `.post1` lacking a platform that `4.6.2` has is refused, naming it.
- Tag check: a tag whose version differs from `--r-version` fails before
  building.
- Rehearsal: `4.6.2.dev1` published to TestPyPI; the smoke job runs
  `uv tool install 'zur==4.6.2.dev1'` from TestPyPI and
  `Rscript -e 'cat(R.version.string)'` prints R 4.6.2.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- A TestPyPI rehearsal with every smoke job green, linked in the pull
  request.
- Full profile green.

**Design.** [§11](design.md#11-versioning-and-r-versions), [§12](design.md#12-speed-benchmark)
(release order), goal 5 in [§2](design.md#2-goals-non-goals-anti-goals).

> Reading used: TestPyPI never reuses a file name, so mechanics
> rehearsals use `.devN` versions installed by exact pin; the real
> version `4.6.2` goes to TestPyPI once, from the release commit, in
> stage 19. If that run fails, the next attempt there is `4.6.2.post1`
> on TestPyPI only.

## Stage 16 — Benchmark and release gate

**Issue.** [#17](https://github.com/pedrobtz/py-zur/issues/17), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick` for the pull request; the workflow itself is verified by
a dispatch against a TestPyPI rehearsal.

**Goal.** The speed claim of goal 1 is measured the way §12 describes,
and the Linux release gate is a script, not a judgement.

**Work.**
- `benchmark.yml`, by dispatch with the index and the pin: for each
  runner, the "R ready" and "package ready" end points, cold (empty uv
  cache, then the §6.6 recipe for `data.table`), warm (uv cache
  restored by `actions/cache`) and the baseline
  (`r-lib/actions/setup-r@v2` with the same `r-version`, then
  `setup-r-dependencies@v2` for `data.table`, no cache for cold); the
  extra cold case in `debian:12-slim` with uv and no Python. Each case
  at least five times; medians in a JSON artifact and the job summary.
- The runner matrix lists `ubuntu-latest`, `ubuntu-24.04-arm`,
  `macos-latest` and `windows-latest`; only `ubuntu-latest` is enabled
  now, and each platform stage enables its own.
- `scripts/gate.py`: on every Linux runner, the cold median must not
  exceed the baseline's for either end point; exits non-zero otherwise,
  naming the case.

**Tests.**
- Gate, in `tests/test_unit.py` with fixture results: equal medians
  pass, one slower case fails and is named, non-Linux runners are not
  gated.
- Rehearsal: a dispatch against the stage 15 rehearsal on TestPyPI
  completes every enabled case five times and produces the table.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- The rehearsal's table is in the pull request description (not the
  README: those numbers come from PyPI, in stage 19).
- Quick profile green.

**Design.** [§12](design.md#12-speed-benchmark), goal 1 and the
anti-goals in [§2](design.md#2-goals-non-goals-anti-goals).

> Readings used: §12 gates "on Linux", which this roadmap applies to
> each Linux wheel before it is uploaded to PyPI, so stage 20 runs the
> gate on `ubuntu-24.04-arm`. The `debian:12-slim` case needs
> `ca-certificates` for R's HTTPS (see stage 6); its install is inside
> the timed region, as the honest cost on a bare image.

## Stage 17 — README and wheel metadata

**Issue.** [#18](https://github.com/pedrobtz/py-zur/issues/18), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `quick`.

**Goal.** Everything a user reads on PyPI and GitHub matches what the
tests prove.

**Work.**
- `README.md` rewritten from the design: what zur is and is not (§1);
  install with `uv tool install 'zur==4.6.2.*'` and run with `uvx`; the
  `pak` recipe of §6.6; `zur doctor` and `eval "$(zur doctor
  --command)"`; the first-run warning and `ZUR_NO_CHECK`; the user
  library and how to cache it in CI; headless graphics; platforms (Linux
  x86_64 now, the others joining 4.6.2 as they pass); licence and the
  source release; a benchmark section filled in stage 19.
- Wheel metadata in `pack()`: summary, classifiers, project URLs,
  `Requires-Python` (the oldest Python the launcher is tested on), and
  the README as the long description (`text/markdown`).

**Tests.**
- `twine check` clean on the wheel, in the build job.
- README commands are the tested ones, in `tests/test_unit.py`: the
  README's `pak` recipe equals the helper of stage 8 character for
  character, and every install line uses the `==X.Y.Z.*` form.
- The wheel's long description equals `README.md`.

**Exit criteria.**
- Every **Tests.** line exists and was seen to fail.
- Quick profile green.

**Design.** [§1](design.md#1-what-zur-is), [§6.6](design.md#66-packages-pak-is-the-documented-route),
[§11](design.md#11-versioning-and-r-versions), [§14](design.md#14-licensing).

## Stage 18 — Review

**Issue.** [#19](https://github.com/pedrobtz/py-zur/issues/19), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full`.

**Goal.** No new features. The Linux x86_64 release candidate is
reviewed twice:

1. **Critical review** of the build scripts, the launcher, `zur doctor`
   and the tests (code, behaviour, tests). Blocking and required
   findings become sub-issues of the Review issue, fixed together in one
   pull request.
2. **PyPI review**, after those fixes: metadata, licences, size, the
   README as rendered on TestPyPI from a fresh `.devN` rehearsal.

**Exit criteria.**
- Every blocking and required review issue closed, the PyPI review
  clean, and the full profile green on `main`, whose head becomes the
  release commit. No pull request closes the Review issue; the reviews
  do.

## Stage 19 — Release Linux x86_64

**Issue.** [#20](https://github.com/pedrobtz/py-zur/issues/20), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full`, through `release.yml`, and `benchmark.yml` twice.

**Goal.** `zur` 4.6.2 is on PyPI with its Linux x86_64 wheel, having
passed the release gate, and the README reports the measured speed.

**Work.**
- Tag `v4.6.2` on the release commit; `release.yml` publishes to
  TestPyPI.
- `benchmark.yml` against TestPyPI with `zur==4.6.2.*`; `scripts/gate.py`
  must pass.
- Approve the `pypi` environment; the source release is published with
  the wheel.
- `benchmark.yml` against PyPI; this stage's pull request puts the
  results in the README and updates `CLAUDE.md`'s current state and
  §10's measured size.

**Exit criteria.**
- The gate run against TestPyPI passed and is linked in the issue.
- `uv tool install 'zur==4.6.2.*'` from PyPI on `ubuntu-latest`, then
  `Rscript -e 'cat(R.version.string)'`, prints R 4.6.2.
- The GitHub release `v4.6.2` is public and its `SHA256SUMS` match the
  wheel's `BUILD-INFO.json`.
- The README's benchmark section shows the PyPI run's medians and links
  it.

**Design.** [§11](design.md#11-versioning-and-r-versions), [§12](design.md#12-speed-benchmark),
[§14](design.md#14-licensing), [§15 D12](design.md#15-decisions).

## Stage 20 — Linux aarch64

**Issue.** [#21](https://github.com/pedrobtz/py-zur/issues/21), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — new platform leg on ubuntu-24.04-arm.` Verified in
Actions only.

**Goal.** A `manylinux_2_28_aarch64` wheel passes every Linux check and
the release gate, and joins 4.6.2 on PyPI.

**Confirm first.** R's link with `-static-libgfortran` succeeds in the
`manylinux_2_28_aarch64` image, checked as in stage 3; if not, the
hashed graft of §6.2, as in stage 5.

**Work.**
- The aarch64 build in the full profile, with its own dependency cache
  key and pinned image digest.
- The installed-tool tests in the five containers on the arm runner,
  stage 10's compiler matrix included.
- The `ubuntu-24.04-arm` entry of `benchmark.yml` enabled.
- Release by the add-platform mode of stage 15, after the gate.

**Tests.**
- Every Linux test of stages 3 to 12 on the aarch64 leg, with known
  answers unchanged; the build checks report `manylinux_2_28_aarch64`.
- Binary packages: stage 8's set from P3M on Ubuntu 24.04 arm64, if P3M
  serves arm64 binaries for it; otherwise the `pak` test asserts that
  packages are compiled, the fact is recorded in §18, and the README
  says so.

**Exit criteria.**
- The confirmation is recorded in §18, with the fallback taken if it
  failed.
- The gate passed on `ubuntu-24.04-arm` against TestPyPI.
- `uv tool install 'zur==4.6.2.*'` from PyPI on `ubuntu-24.04-arm`
  installs the aarch64 wheel of the current 4.6.2 build and R starts.
- Wheel size in §10; full profile green on every leg; aarch64 sources
  (none new if the manifest is shared) in the release.

**Design.** [§6](design.md#6-linux-a-fresh-headless-build), [§12](design.md#12-speed-benchmark),
[§15 D4, D12, D17](design.md#15-decisions), [§16](design.md#16-open-questions) question 1.

## Stage 21 — macOS arm64

**Issue.** [#22](https://github.com/pedrobtz/py-zur/issues/22), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — macOS build and legs on macos-latest.` The build only
needs `pkgutil` and can be tried on the maintainer's machine; the tests
run only on an arm64 runner.

**Goal.** CRAN's R 4.6.2 for arm64, unmodified except for text files,
is a `macosx_14_0_arm64` wheel whose CRAN binary packages load wherever
they are installed, with one `libR` in the process (D18).

**Confirm first.** On an arm64 runner, the measurements of §18 hold:
with CRAN's `.pkg` expanded by `pkgutil --expand-full` and only `bin/R`
edited, CRAN binary packages load with one `libR`, both on a runner
that never had a framework R and after CRAN's R 4.6.2 `.pkg` is
installed on it with `installer`.

**Work.**
- macOS input in `manifests/macos-arm64.toml`: the `.pkg` URL and
  SHA-256.
- Expand, prune the macOS part of §9 (X11 module, Tcl/Tk and `tcltk`,
  `R.app`, `*.dSYM`, manuals, vignettes, `tests/`, `demo/`,
  translations), and make the text edits of [§7](design.md#7-macos-crans-binaries-unmodified):
  `bin/R` takes `R_HOME` from its location and exports
  `DYLD_LIBRARY_PATH`, `bin/Rscript` becomes the script that sets
  `RHOME` (design §5, 2026-11-24), `Renviron` loses the framework path,
  `Makeconf` links `-L"$(R_HOME)/lib" -lR`. Library links become
  copies. No Mach-O file is changed.
- The wheel tag from the binaries' `LC_BUILD_VERSION`.
- User library at `~/R/zur/<platform>/4.6` (see the note below).
- `zur doctor` on macOS: XQuartz present or not, compiler found or not;
  neither a failure.
- Sources of the libraries CRAN links, from CRAN's macOS recipes, added
  to the source release, with unidentified ones listed in
  `BUILD-INFO.json`.
- Legs in the full profile: one runner as provisioned, one after
  installing CRAN's R `.pkg`. `macos-latest` enabled in `benchmark.yml`.

**Tests.**
- macOS signatures: `codesign -v` passes for every Mach-O file in the
  installed tool.
- Relocation: no framework path in `bin/R`, `Makeconf` or `Renviron`.
- Binary packages: CRAN's binaries of stage 8's set (`jsonlite`, `Rcpp`,
  `data.table`, `mvtnorm`, `stringi`) installed in zur's library, in a
  user-set `R_LIBS_USER`, in a `lib=` directory and loaded under
  `--vanilla`, each returning its known answer with exactly one
  `libR.dylib` mapped in the process (counted with `vmmap` or `lsof -p`),
  on both legs.
- Front-ends, base packages (the committed list minus `tcltk`), user
  library and HTTPS as on Linux.
- Graphics: `png()` and `pdf()` write files with the right magic bytes
  with nothing installed; `svg()` does after XQuartz is installed with
  Homebrew on the leg.
- Source packages: the C and C++ fixtures compile with the runner's
  Command Line Tools.
- `zur doctor`: reports XQuartz absent and then present, exit 0 both
  times.
- Build checks: every Mach-O load command resolves inside the wheel or
  to `/usr/lib` or `/System`; no symlinks; size under budget.

**Exit criteria.**
- The confirmation is recorded in §18. If it fails, the stage stops: the
  patcher §7 replaced comes back only as a new decision in the design.
- Every **Tests.** line exists and was seen to fail.
- The wheel tag is `macosx_14_0_arm64` and the size is in §10 against
  the 37 MB estimate.
- Released by add-platform mode; `uv tool install 'zur==4.6.2.*'` from
  PyPI on `macos-latest` installs it and R starts.
- Full profile green on every leg.

**Design.** [§7](design.md#7-macos-crans-binaries-unmodified), [§9](design.md#9-what-a-headless-wheel-leaves-out),
[§14](design.md#14-licensing), [§15 D1, D6, D10, D18, D19](design.md#15-decisions),
[§16](design.md#16-open-questions) question 1.

> Design gaps, with the readings used here: §5 defines `<platform>` in
> the user library only for Linux; macOS and Windows use
> `R.version$platform` (for example `aarch64-apple-darwin20`). §13 lists
> Fortran among source packages without restricting it to Linux, but
> CRAN's macOS `Makeconf` names CRAN's own gfortran under `/opt/gfortran`,
> which runners lack and §7 does not edit; the Fortran fixture is left
> out on macOS and the README says so.

## Stage 22 — macOS x86_64

**Issue.** [#23](https://github.com/pedrobtz/py-zur/issues/23), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — macOS x86_64 leg on an Intel runner.` This is the one
platform the maintainer can build and test locally, with framework R
4.2 to 4.6 installed (the "with framework R" case).

**Goal.** The stage 21 build, for CRAN's x86_64 `.pkg`, as a
`macosx_11_0_x86_64` wheel.

**Work.**
- `manifests/macos-x86_64.toml` with the `.pkg` of §18 by SHA-256.
- The x86_64 leg in the full profile while GitHub provides an Intel
  runner ([§16](design.md#16-open-questions) question 4); when none is
  left, the default of question 4 applies and the leg runs on the
  maintainer's machine for the remaining R versions.

**Tests.**
- Every stage 21 test on the x86_64 leg, without and with a framework R.

**Exit criteria.**
- Every **Tests.** line passes on the runner and on the maintainer's
  machine.
- The wheel tag is `macosx_11_0_x86_64`; the size is in §10.
- Released by add-platform mode and installed from PyPI on the Intel
  runner.
- Full profile green on every leg.

**Design.** [§7](design.md#7-macos-crans-binaries-unmodified), [§15 D18](design.md#15-decisions),
[§16](design.md#16-open-questions) question 4.

## Stage 23 — Windows x86_64

**Issue.** [#24](https://github.com/pedrobtz/py-zur/issues/24), sub-issue of [#2](https://github.com/pedrobtz/py-zur/issues/2).

**CI.** `full — built on ubuntu-latest with innoextract, tested on
windows-latest.` Not verifiable on the maintainer's machine.

**Goal.** CRAN's R 4.6.2 installer, unpacked without being run, is a
`win_amd64` wheel whose launcher behaves like the POSIX one.

**Confirm first.** `innoextract` 1.9 unpacks the R 4.6.2 installer on
Linux, and the tree matches, file for file and by SHA-256, a silent
install on a Windows runner (`/VERYSILENT /CURRENTUSER /DIR=...`).

**Work.**
- `manifests/windows.toml` with the installer by SHA-256 and the
  `innoextract` version.
- Prune the Windows part of §9 (`Rgui.exe`, Tcl/Tk and `tcltk`,
  manuals, vignettes, `tests/`, `demo/`, translations, the uninstaller);
  pack with tag `win_amd64`.
- Launcher on Windows: R runs as a child; its exit code and Ctrl-C pass
  through. The launcher sets the user library
  (`%LOCALAPPDATA%\R\zur\<platform>\4.6`) and the other settings,
  [§16](design.md#16-open-questions) question 3's default; whether R's
  own files can hold them instead is looked into and the finding
  recorded in §16.
- `zur doctor` on Windows: Rtools found or not; not a failure.
- Rtools source archives (the libraries CRAN links) added to the
  source release.
- `windows-latest` enabled in `benchmark.yml`.

**Tests.**
- Front-ends: as on Linux, through `R.exe` and `Rscript.exe`;
  `Rscript -e 'quit(status = 3)'` exits 3; a `CTRL_BREAK_EVENT` sent to
  a running `Rscript` ends it with R's status.
- Base packages (the committed list minus `tcltk`); user library with
  `R_LIBS_USER` unset and set; HTTPS.
- Binary packages: CRAN's Windows binaries of stage 8's set install and
  return their known answers.
- Graphics: `png()`, `pdf()` and `svg()` write files with the right
  magic bytes, including non-ASCII text.
- Source packages: with Rtools installed on the leg, the C and C++
  fixtures compile; `zur doctor` reports Rtools absent before and
  present after.
- Build checks: no symlinks, `RECORD` hashes, size under budget.

**Exit criteria.**
- The confirmation is recorded in §18. If `innoextract` fails, the build
  runs the silent install on a Windows runner instead (D20), and the
  build job moves there.
- Every **Tests.** line exists and was seen to fail.
- The size is in §10 against the 55 MB estimate.
- Released by add-platform mode and installed from PyPI on
  `windows-latest`.
- Full profile green on every leg.

**Design.** [§5](design.md#5-wheel-layout-and-launcher), [§8](design.md#8-windows-crans-binaries),
[§9](design.md#9-what-a-headless-wheel-leaves-out), [§15 D3, D10, D20](design.md#15-decisions),
[§16](design.md#16-open-questions) questions 1 and 3.

> Reading used: §13 says the user library holds "when R is started
> through `$R_HOME/bin/R` without the launcher". On Windows that is not
> so in the first release (§16 question 3), so that test is POSIX only.

## Design coverage

Each test of [§13](design.md#13-how-it-is-tested) and where it lands:

| §13 test | Stages |
| --- | --- |
| User library | 7; 21, 22, 23 |
| `pak` recipe | 8 |
| Front-ends | 5; 21, 22, 23 |
| Base packages only | 3, 5; 21, 23 |
| Binary packages | 8; 20, 21, 22, 23 |
| Source packages | 5 (smoke), 10; 21, 23 (C, C++) |
| `zur doctor` | 11; 21, 23 |
| First-run warning | 12 |
| Graphics headless | 6; 21, 23 |
| macOS signatures | 21, 22 |
| Network and data | 5, 6 |
| Relocation | 5; 21 |
| Build checks | 5, 9; 21, 23 |
| Containers | 5, 6 |

Each decision of [§15](design.md#15-decisions) that needs work:

| Decision | Stages |
| --- | --- |
| D1 hybrid, no recommended packages | 3, 10, 21, 23 |
| D2 name `zur` | none (done) |
| D3 Python console scripts | 5, 23 |
| D4 `manylinux_2_28` | 3, 5, 20 |
| D5 no compiler in the wheel | 5, 10 |
| D6 headless prune set | 9, 21, 23 |
| D7 build writes the wheels | 2, 5 |
| D8 no zig for R | 3 (gcc and gfortran only) |
| D9 no repository configuration | 8 |
| D10 zur's user library | 7, 21, 23 |
| D11 `zur doctor`, no bundled runtimes | 5, 11, 12 |
| D12 Linux x86_64 first | 19, then 20 to 23 |
| D13 environment in R's files | 5, 6, 7 |
| D14 cairo without pango | 3, 9 |
| D15 source mirror | 14, 19 |
| D16 reference BLAS under both names | 5, 8 |
| D17 no OpenMP in R, static Fortran runtime | 3, 5, 8, 10 |
| D18 macOS binaries unmodified | 21, 22 |
| D19 translations dropped | 9, 21, 23 |
| D20 `innoextract` first | 23 |

## After the first release

- [§16](design.md#16-open-questions) question 2: the scheduled workflow
  that checks `BUILD-INFO.json`'s libraries against advisories and opens
  an issue; `.postN` rebuilds of the newest patch of the current and
  previous R minor version, each carrying every platform (stage 15's
  carry check).
- [§11](design.md#11-versioning-and-r-versions): each R patch release;
  the back-catalogue from 4.4.0.
- §16 question 3: Windows settings in R's own files, if stage 23 finds a
  place.
- §16 question 4: the last macOS x86_64 release.
- §16 question 6: `rpy2` embedding as a tested use; rig-like helpers;
  Positron's R discovery; offering `zur` to `ir`; zig for the C
  dependencies; an optional OpenBLAS.
- Byte-identical wheels (goal 6 leaves them out of the first release).

## Blocking open questions

None of [§16](design.md#16-open-questions) blocks stage 2. Outside the
design:

- The pending trusted publishers on TestPyPI and PyPI (stage 15) are
  the maintainer's to set up, and nothing reserves the name `zur` until
  the first upload; doing both early removes the only external step on
  the critical path.
- If a new R patch release (4.6.3) appears before stage 19, the target
  moves to it; stages 20 to 23 then add their wheels to that version.
- A fix found in stages 20 to 23 that changes shared code (the
  launcher, `zur doctor`) reaches Linux x86_64 users only through a
  `.postN`, which must carry every platform already published (§11).
