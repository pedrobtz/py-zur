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

## Why not just install R?

The usual way to put R on a Linux machine is Posit's R builds
(`rstudio/r-builds`), the `.deb` and `.rpm` packages that
`r-lib/actions/setup-r` and rig install into `/opt/R/<version>`. `zur`
keeps their package side: binary packages still come from Posit Package
Manager (P3M), and R is configured to load them. What changes is how R
itself arrives:

- **No administrator rights.** `uv tool install` writes to your home
  directory only; no `sudo`, `apt` or `dnf`.
- **One wheel for every distribution.** It runs on any Linux with glibc
  2.28 or newer, slim images included, instead of one build per
  distribution and version.
- **Nothing installed system-wide.** From the machine R uses only
  glibc, `libgcc_s` and `libstdc++`; OpenSSL, curl, cairo and a font
  travel in the wheel. Uninstalling is `uv tool uninstall zur`, and R
  versions never collide.
- **Faster to a working R.** In a preliminary, informal run on GitHub's
  `ubuntu-latest` (five cold runs each, medians, the wheel already
  downloaded), `zur` reached `Rscript -e 1` in 1.5 s against 34.9 s for
  `setup-r`, and had `data.table` installed and loaded in 10.9 s against
  50.7 s for `setup-r` with `setup-r-dependencies`. The release's own
  benchmark will replace these numbers.
- **Versioned like any Python tool.** Pin `zur==4.6.2.*`, run once with
  `uvx`, or lock it next to a project's Python dependencies.
- **A diagnosis built in.** `zur doctor` names any system library a
  binary package needs and prints the command that installs it.

What Posit's builds still do better: P3M's binaries are built against
exactly that R, where `zur` matches its configuration and tests that
P3M's packages load; they include the recommended packages, Tcl/Tk and
pango text shaping; and they are mature and cover every platform today.

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
`debian:13-slim` and `rockylinux:8-minimal` (no `which`). The wheel is
about 60 MB to download and about 130 MB installed.

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

## What each wheel bundles

### Linux x86_64

R itself, built from source, and its own libraries in `lib/R/lib`:

| File | What it is |
| --- | --- |
| `libR.so` | R |
| `libRblas.so`, `libRlapack.so` | R's reference BLAS and LAPACK |
| `libblas.so.3`, `liblapack.so.3` | Copies of those two, under the names P3M's Debian, Ubuntu and RHEL 8 binaries link |
| `libflexiblas.so.3` | A stub with no code of its own that loads R's BLAS and LAPACK, for P3M's RHEL 9 binaries |

So every package uses R's reference BLAS, never the machine's. The
libraries below are bundled in `zur.libs/` under hashed names, so they
never clash with the machine's own copies:

| Library | Version | From | Licence | Needed by |
| --- | --- | --- | --- | --- |
| OpenSSL | 3.5.9 | source | Apache-2.0 | libcurl |
| curl (libcurl) | 8.23.0 | source | curl | `download.file()`, `url()` |
| fontconfig | 2.15.0 | source | HPND-sell-variant and others | text in plots |
| zlib | 1.2.11 | AlmaLinux 8 | Zlib | R |
| bzip2 | 1.0.6 | AlmaLinux 8 | bzip2-1.0.6 | R |
| xz (liblzma) | 5.2.4 | AlmaLinux 8 | public domain | R |
| PCRE2 | 10.32 | AlmaLinux 8 | BSD-3-Clause | R's regular expressions |
| ICU | 60.3 | AlmaLinux 8 | Unicode-DFS-2016 and others | collation and text handling |
| readline | 7.0 | AlmaLinux 8 | GPL-3.0-or-later | the interactive console |
| ncurses (libtinfo) | 6.1 | AlmaLinux 8 | X11-style | readline |
| cairo | 1.15.12 | AlmaLinux 8 | LGPL-2.1-only or MPL-1.1 | `png()`, `pdf()`, `svg()` |
| pixman | 0.38.4 | AlmaLinux 8 | MIT | cairo |
| libX11, libXext, libXrender, libxcb, libXau | 1.6.8, 1.3.4, 0.9.10, 1.13.1, 1.0.9 | AlmaLinux 8 | MIT and X11 family | cairo (no display is used) |
| FreeType | 2.9.1 | AlmaLinux 8 | FTL or GPL-2.0-or-later | fonts |
| expat | 2.5.0 | AlmaLinux 8 | MIT | fontconfig |
| libpng | 1.6.34 | AlmaLinux 8 | Libpng | `png()` |
| libjpeg-turbo | 1.5.3 | AlmaLinux 8 | IJG, BSD-3-Clause, Zlib | `jpeg()` |
| libtiff | 4.0.9 | AlmaLinux 8 | libtiff | `tiff()` |
| jbigkit | 2.1 | AlmaLinux 8 | GPL-2.0-or-later | libtiff |
| libgfortran, libquadmath | 8.5.0 | AlmaLinux 8 | GPL-3.0-or-later with the GCC runtime exception; LGPL-2.1-or-later | R's Fortran code (LAPACK) |
| Liberation Sans | 2.00.3 | AlmaLinux 8 | OFL-1.1-RFN | the one font, for text in plots |

"Source" means built in the wheel's build from a release tarball,
pinned by SHA-256; "AlmaLinux 8" means taken from the build image's
packages, pinned to an exact version. From the machine R uses only
glibc, `libgcc_s` and `libstdc++`, which every `manylinux_2_28` system
has; binary packages also need the OpenMP and Fortran runtimes
`zur doctor` names. No compiler is bundled.

### Other platforms (planned)

| Wheel | What it will bundle |
| --- | --- |
| Linux aarch64 | The same set as Linux x86_64, built the same way for ARM |
| macOS arm64, macOS x86_64 | CRAN's R installer, unpacked and unmodified, with the libraries CRAN ships in it (among them `libgfortran`, `libgcc_s` and `libomp`); without Tcl/Tk, the X11 module and `R.app`. `svg()` needs XQuartz on the machine |
| Windows x86_64 | CRAN's R installer, unpacked without being run, with the libraries CRAN builds into R; without `Rgui.exe` and Tcl/Tk. Source packages need Rtools on the machine |

## Inputs and sources

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
