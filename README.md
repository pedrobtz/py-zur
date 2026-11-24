# zur

R for machines without a screen, installed the way Python tools are.

```sh
uv tool install 'zur==4.6.2.*'
Rscript -e 'cat(R.version.string)'
```

`zur` is a Python wheel that carries a complete, headless R installation.
One command puts a known R version on a CI runner, in a container or in a
remote editor session: no administrator rights, no system package
manager, no installer. The distribution version is the R version, so
`zur==4.6.2.*` is R 4.6.2.

> **Status: not on PyPI yet.** The Linux x86_64 wheel builds and passes
> its tests in CI; the first release, `zur` 4.6.2, is being prepared.
> macOS (arm64, x86_64), Windows x86_64 and Linux aarch64 are planned to
> join the same version. Until then, the commands below show how it will
> be used.

## Getting started

Install R, then run a script, or run one without installing:

```sh
uv tool install 'zur==4.6.2.*'
Rscript file.R
uvx --from 'zur==4.6.2.*' Rscript -e 'sessionInfo()'
```

`R`, `Rscript` and `R CMD` behave as on any CRAN-style installation.
`zur` ships R and its base packages; recommended packages such as `MASS`
or `Matrix` are one install away.

## Installing packages

`pak` is the documented route. Install it, then point it at Posit's
package manager (P3M) for binary packages:

```sh
Rscript -e 'install.packages("pak", repos = sprintf(
  "https://r-lib.github.io/p/pak/stable/%s/%s/%s",
  .Platform$pkgType, R.version$os, R.version$arch))'
Rscript -e 'pak::repo_add(CRAN = "PPM@latest"); pak::pkg_install("data.table")'
```

- `zur` sets no repository: `getOption("repos")` is `@CRAN@`, as on
  CRAN's R. `repo_add()` lasts for one R session, so repeat it in each
  `Rscript` call that installs packages, or set `repos` in your own
  `.Rprofile`.
- With P3M, `pak` picks the binaries built for your distribution and
  installs the system libraries packages declare (for example `libxml2`
  for `xml2`).
- Where P3M has no binary, `pak` compiles from source. That happens for
  more packages on Debian 12, whose P3M index lacks `data.table`,
  `RcppArmadillo` and `xml2`; Debian 13 has them.

Tested with no compiler present: on Ubuntu 24.04, `data.table`,
`jsonlite`, `Rcpp`, `deSolve` and `RcppArmadillo` install as binaries
and return known answers; the recipe above gets a binary `data.table` on
Debian 13 and Rocky Linux 8, and `RcppArmadillo` loads on Rocky Linux 9.

## `zur doctor`

Binary packages need two libraries from the machine that R itself does
not: the OpenMP and Fortran runtimes. `zur doctor` checks for them and
prints the command that installs what is missing:

```sh
zur doctor                     # report; exits 1 if a runtime is missing
eval "$(zur doctor --command)" # install the missing runtimes
```

- On Debian and Ubuntu that is `libgomp1` and `libgfortran5`; on RHEL,
  Rocky and Fedora, `libgomp` and `libgfortran`, with `dnf` or, on
  minimal images, `microdnf`. `sudo` is added only when you are not root.
- It also reports whether `gcc`, `g++`, `gfortran` and `make` are
  present, for source packages. Their absence is never a failure.
- `zur` never installs anything itself.

The first time `R` or `Rscript` starts on a machine that lacks the
runtimes, it says so once, on stderr, and then starts as usual. Set
`ZUR_NO_CHECK=1` to turn the note off, for jobs that compare stderr.

## Packages you install

Packages go to `zur`'s own user library,
`~/R/zur/<platform>/<R major.minor>`, for example
`~/R/zur/x86_64-ubuntu-24.04/4.6`. The platform part keeps the libraries
of different distributions apart when they share a home directory.

- Setting `R_LIBS_USER` yourself overrides it.
- The library lives outside the tool's directory, so packages survive
  `uv tool install --reinstall` and `uv tool uninstall`.
- To cache packages between CI runs, cache `~/R/zur`.
- With a read-only home, R still starts, without a user library.

## Compiling source packages

`zur` ships no compiler. Source packages build with the machine's own
`gcc`, `g++` and `gfortran`, from gcc 8 (Rocky Linux 8) to gcc 16
(Fedora 45), including OpenMP. The default C++ standard is chosen for
the host, as R's own `configure` would: C++20 where the compiler has it
(gcc 10 and later), otherwise C++17. A package that asks for a newer
standard than the host's compiler offers fails to compile.

## Graphics

There is no display, X11 or Tcl/Tk. Plots go to files: `png()`,
`pdf()` and `svg()` work in a bare container, through cairo, and are
tested there; `jpeg()`, `tiff()` and `bmp()` use the same cairo route.
The wheel carries one font, Liberation Sans, so text is drawn even where
the system has no fonts.

## In a CI job

On GitHub Actions, for a package's own checkout (an example, not yet
run against a release):

```yaml
- uses: astral-sh/setup-uv@v6
- run: uv tool install 'zur==4.6.2.*'
- run: eval "$(zur doctor --command)"
- uses: actions/cache@v4
  with:
    path: ~/R/zur
    key: r-${{ runner.os }}-${{ hashFiles('DESCRIPTION') }}
- run: Rscript -e 'install.packages("pak", repos = sprintf("https://r-lib.github.io/p/pak/stable/%s/%s/%s", .Platform$pkgType, R.version$os, R.version$arch))'
- run: Rscript -e 'pak::repo_add(CRAN = "PPM@latest"); pak::local_install_deps()'
- run: Rscript tests/run.R
```

## Where it runs

| Platform | State |
| --- | --- |
| Linux x86_64 (`manylinux_2_28`: glibc 2.28 or newer) | Built and tested |
| Linux aarch64 | Planned |
| macOS arm64 and x86_64 (CRAN's binaries) | Planned |
| Windows x86_64 (CRAN's binaries) | Planned |

The Linux wheel is tested on Ubuntu 24.04, Debian 12 and 13 and Rocky
Linux 8 and 9, on Fedora 45 for source packages, and on the minimal
images `debian:12-slim` (no fonts, time zone data or CA bundle),
`debian:13-slim` and `rockylinux:8-minimal` (no `which`). It is about
60 MB.

## What it leaves out

So that it stays small and headless, the wheel has no recommended
packages, no manuals or vignettes, no package `tests/` or `demo/`
directories, and no translations: messages are in English. Help pages
(`?lm`), the headers `R CMD INSTALL` needs, ICU and libcurl stay.

## How it is built

On Linux, R 4.6.2 is built from source in the `manylinux_2_28` image,
with R's reference BLAS and LAPACK. Every input is pinned: the R tarball
by SHA-256, each library by exact version or tarball hash, and the image
by digest. The shared libraries R needs are bundled under hashed names,
so they never clash with the machine's. macOS and Windows will use
CRAN's own binaries.

Each wheel's `zur/_runtime/BUILD-INFO.json` lists every bundled
library with its version, licence and source. The sources of everything
a release bundles will be on that version's GitHub release.

The design is in [`.agents/design.md`](.agents/design.md) and the plan to
the first release in [`.agents/roadmap.md`](.agents/roadmap.md). The
commands for building and testing the wheel are in
[`CLAUDE.md`](CLAUDE.md).

## Licence

R is GPL-2.0-or-later. The wheel also bundles libraries under their own
licences, among them GNU readline (GPL-3.0-or-later) and OpenSSL
(Apache-2.0), so a wheel as a whole is distributed under the terms of
GPL-3.0-or-later. Every licence text is in the wheel, under
`zur/_runtime/licenses/`, and the wheel's `License-Expression` lists
them all.
