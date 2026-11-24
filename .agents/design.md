# zur design: headless R for CI, installed with `uv`

- **Status:** Complete, 2026-11-23. Every decision in §15 was agreed
  by the maintainer in review, and §18 records what was measured. This
  document is the single source of truth for `zur`. It replaces the
  earlier conda-forge design (kept as background in §17) and
  supersedes RFC 0080 in `pedrobtz/packages`. The code in the
  repository is still that prototype.
- **Package:** distribution, import and repository name `zur`
  (`pedrobtz/py-zur`); commands `R` and `Rscript`. `zur` was free on PyPI
  on 2026-11-23. Licence GPL-2.0-or-later, because the wheel
  redistributes R.
- **Stages:** the order of work and its exit criteria are in
  [roadmap.md](roadmap.md). §16 lists what the first step of each
  platform's build must confirm.

## 1. What zur is

`zur` is R as a Python wheel, built for machines without a screen. One
command puts a known R version on a CI runner, a container or a remote
editor session, with no administrator rights, no system package manager
and no installer:

```sh
uv tool install 'zur==4.6.2.*'
uv tool run --from zur Rscript file.R
```

Two settings, in order of priority:

1. **CI/CD pipelines.** A job installs `zur` and runs `Rscript file.R`.
   Packages come from `pak`, which is the documented route (§6.6): with
   P3M added, it picks binary packages and installs their system
   requirements.
   `install.packages()` works as on any R, which under `Rscript` means
   the script must name a repository itself.
2. **Remote editor sessions** (code-server, VS Code remote): someone
   needs an R console, and plots written to files or shown through the
   editor's R extension (httpgd, vscode-R), never through a window.

### 1.1 What zur is not

- **Not a desktop R.** No X11, no Tcl/Tk, no GUI front-end. Plots go to
  files: `png()` and `pdf()` everywhere, `svg()` on Linux and Windows.
  On macOS CRAN's cairo devices need XQuartz (§7), so `svg()` there
  needs XQuartz or the `svglite` package.
- **Not a package manager or repository configurator.** `zur` ships R
  and its base packages. Choosing repositories (CRAN, P3M binaries) is
  the user's job, in `.Rprofile` or the script, or `pak`'s (§6.6).
  Tools in that direction, like rig's, may come later (§16).
- **Not a version manager.** `uv tool install 'zur==X.*'` replaces the
  tool's R; `uvx --from 'zur==X.*'` runs another one ad hoc.
- **Not a bridge between Python and R.** `rpy2` can use `zur` as its R.

## 2. Goals, non-goals, anti-goals

**Goals**

1. **The fastest way to get R on a headless CI runner.** Measured as
   the wall time from a stock runner to two points: `Rscript -e 1`
   succeeding, and a package with compiled code installed and loaded.
   Cold (no cache) and warm (uv cache restored), against
   `r-lib/actions/setup-r@v2` and `setup-r-dependencies@v2` for the
   same R version (§12).
2. **One command, no privileges**, on Linux x86_64 and aarch64 (most CI),
   macOS arm64 and x86_64, Windows x86_64. On Linux this holds for R
   itself; binary packages assume the system libraries Posit's R
   package depends on, which `zur doctor` reports (§6.5, D11).
3. **Standard R for scripts.** Same `R`/`Rscript` front-ends, `R CMD`,
   `R.home()` layout and package ABI as a CRAN-style installation, so
   the binary packages CI users already install load:
   - P3M's Linux binaries,
   - CRAN's macOS and Windows binaries,
   - and source packages compile with the machine's own toolchain.
4. **Every wheel under PyPI's 100 MB per-file limit**, with no limit
   raise and no split wheel.
5. **Version per release.** `zur==4.6.2.*` means R 4.6.2, in zur's
   newest build of it (§11).
6. **Reproducible inputs.** A release is a function of pinned inputs (R
   sources or CRAN installers by SHA-256, dependency versions, the
   manylinux image by digest) and the build scripts. Byte-identical
   wheels are not a first-release goal. *(Builder's call, from review.)*

**Non-goals:** recommended packages in the wheel (they are one
`install.packages()` away); interactive graphics devices; repository
configuration; bundling compilers (Rtools, gcc, Xcode); R-devel on PyPI;
zig or other novel toolchains in the first release (§4).

**Anti-goals** (failure even if it technically works):

- An R that refuses or crashes on standard binary packages.
- A cold install slower than `setup-r` on `ubuntu-latest`.
- Different results from CRAN's R on the same script for reasons `zur`
  introduced (a different BLAS, a missing capability nobody chose to
  drop).

## 3. Prior art

- **`r-lib/actions/setup-r`** with **rig**: today's standard for R in
  GitHub Actions. Installs Posit's Linux builds (`.deb`/`.rpm`) or CRAN's
  installers, with admin rights on the runner. The benchmark (§12).
- **portable-r** (`portable-r/portable-r-macos`,
  `portable-r/portable-r-windows`): CRAN's macOS and Windows builds made
  relocatable, as archives on GitHub Releases. It prunes almost nothing:
  macOS drops `*.dSYM` (about 14 MB), Windows the uninstaller. R 4.6.2:
  86 MB (macOS arm64, `.tar.gz`), 109 MB (Windows x64, `.zip`), with the
  recommended packages. Its macOS relocation (`install_name_tool`
  rewrites, ad-hoc signing, an `install.packages()` wrapper that patches
  CRAN binary packages) was the first model for §7; the experiment
  there found a route that needs none of it.
- **Posit `r-builds`**: R built from source per Linux distribution,
  installed at `/opt/R/<version>`, not relocatable. Its `configure`
  options are the reference for P3M compatibility (§6.2).
- **webR**: R for WebAssembly, built with clang and LLVM flang. Shows
  R's Fortran can be built without gfortran, at the cost of a second
  Fortran runtime (§4).
- **conda-forge `r-base`**: relocatable R on every platform; `zur`'s
  first prototype repackaged it (§17).
- **`ziglang`, `cmake`, `ninja`, `nodejs-wheel`** on PyPI: the pattern
  of shipping a non-Python binary as a wheel.
- **`rportable`** (PyPI, Windows, R 3.2.3, abandoned) and **`r-lib/ir`**
  (a uv-installable runner for R scripts that needs R already
  installed; `zur` could be its R provider).

## 4. Approaches

Three ways to build the platform R's were considered:

- **A. A fresh native build on every platform.** Each runner compiles R
  and its dependencies with the platform's standard compilers (gcc and
  gfortran on Linux, clang and gfortran on macOS, Rtools on Windows),
  configured headless; libraries bundled like `auditwheel`/`delocate`.
  Full size control and standard ABI everywhere, but we would build and
  maintain every dependency on three operating systems, duplicating
  CRAN's own macOS and Windows builds.
- **B. zig as one toolchain.** `zig cc` plus LLVM flang for Fortran,
  cross-compiling from Linux. Novel, one compiler, any glibc target.
  Rejected for the first release: R's own Fortran would use flang's
  runtime while users' packages and P3M binaries use gfortran's, and C++
  would use zig's libc++ against their libstdc++; two runtimes in one
  process is the class of failure goal 3 forbids. zig's main advantage,
  targeting an old glibc, is already given by the manylinux container.
  R's build also runs the R it just built, so cross-compiling still
  needs native runners or emulation.
- **C. Hybrid (chosen).** A fresh, headless source build on Linux, where
  most CI runs and where size control pays off; CRAN's own binaries on
  macOS and Windows, made relocatable and pruned. macOS and Windows get
  the exact R that CRAN's binary packages are built for, and both fit
  well under 100 MB after pruning (§10).

A fourth was considered for Linux only and rejected:

- **D. Posit's `r-builds` binaries, relocated.** The Linux counterpart
  of using CRAN's binaries: the exact R that P3M binaries are built
  against. Rejected because those builds are per distribution and link
  dynamically to that distribution's libraries (libcurl, ICU, pango,
  Tcl/Tk, X11), so R itself would need the system package manager,
  against goal 2, and one wheel per distribution, against the
  manylinux tag.

Chosen by the maintainer on 2026-11-23: C. Linux is built from source
with gcc and gfortran, not zig. zig may return later for building the
small C dependencies hermetically (§16), not as R's compiler.

## 5. Wheel layout and launcher

```text
zur/
  __init__.py            launcher
  _runtime/              R_HOME's parent: bin/ lib/ (Linux), or the
                         CRAN Resources tree (macOS), or the CRAN tree
                         (Windows); always with lib/R or equivalent
    BUILD-INFO.json      inputs: R version, source hashes, every
                         bundled library with version and licence
    licenses/            licence texts of everything bundled
zur.libs/                (Linux) libraries grafted by the repair step
zur-4.6.2.dist-info/
  METADATA  WHEEL  RECORD  entry_points.txt
```

- `entry_points.txt` declares `R = zur:R`, `Rscript = zur:Rscript` and
  `zur = zur:main` (the `doctor` command, §6.5). uv links exactly these
  into its tool bin directory; on Windows they become `R.exe`,
  `Rscript.exe` and `zur.exe`.
- Tags: `py3-none-<platform>`. Linux `manylinux_2_28_x86_64` and
  `_aarch64`; macOS from the CRAN binaries' minimum OS
  (`LC_BUILD_VERSION`): CRAN publishes R 4.6.2 for x86_64 under
  `big-sur-x86_64` (macOS 11) and for arm64 under `sonoma-arm64`
  (macOS 14); Windows `win_amd64`.
- **Launcher.** Does as little as possible: drops an inherited
  `R_HOME`, runs the first-run check (§6.5), then on
  POSIX replaces itself with R's front-end (`os.execve`), so signals,
  exit codes and the terminal are R's. On Windows, where `execve` does
  not replace the process, it runs R as a child and passes its exit
  code and Ctrl-C through. Cost measured in the prototype: 17 ms per
  start.
- **Environment lives in R's own files, not the launcher.** On POSIX
  the settings below and those of §6.4 are written into `bin/R` (the
  front-end script the build already edits, which `Rscript`, `R CMD`
  and child R processes all pass through) and `etc/Renviron`, each
  only when the user has not set the variable. `bin/Rscript` is a shell
  script too: the compiled `Rscript` is at `$R_HOME/libexec/Rscript`, and
  the script sets `RHOME` from its own location, overriding any inherited
  value, and runs it. So `$R_HOME/bin/Rscript` finds this R however it
  is started, and nothing else sets `RHOME`. The compiled `Rscript` is
  at `$R_HOME/libexec/Rscript`, not `bin/exec/`, because `R CMD INSTALL`
  takes every file in `bin/exec` for a sub-architecture (2026-11-24). The script also drops an
  inherited `R_HOME`, as the launcher does, so `bin/R` prints no warning
  about it (2026-11-24, stage 6). Like `bin/R`, it does not follow a
  symlink to itself: a link to it elsewhere finds no R.
  R then behaves the same whether it is started
  by the launcher, by `$R_HOME/bin/R`, by an editor extension or by an
  embedding program. On Windows R has no front-end script, so in the
  first release the launcher sets them there (§16, question 3).
  *(From review.)*
- **User library.** Unless `R_LIBS_USER` is already set, it is zur's
  own directory, `~/R/zur/<platform>/<major.minor>` (on Windows under
  `%LOCALAPPDATA%\R\zur\`), created if missing (D10). On Linux
  `<platform>` includes the distribution and its version from
  `/etc/os-release` (for example `x86_64-ubuntu-24.04`): one manylinux
  R runs on every distribution, but P3M binaries and locally compiled
  packages are per distribution, and a home directory mounted into
  several containers must not mix them. The library survives `uv tool
  install --reinstall` and upgrades within a minor version, never
  collides with another R of the same minor version on the machine,
  and is what CI caches key on.

  Decided 2026-11-24 (stage 7): `bin/R` sets it (D13), so it holds with
  and without the launcher, and R's own `etc/Renviron` line
  `R_LIBS_USER=${R_LIBS_USER:-'%U'}` keeps that value; the build refuses
  an `Renviron` without that line. An empty `R_LIBS_USER` counts as
  unset, as it does for that line. On Linux `<platform>` is
  `<arch>-<ID>-<VERSION_ID>`: `<arch>` is the wheel's (`x86_64`,
  `aarch64`), not the running machine's; `ID` and `VERSION_ID` are read
  from `/etc/os-release` verbatim, without running the file (no
  fallback to `/usr/lib/os-release`), so Rocky Linux 8.10 gives
  `x86_64-rocky-8.10` and a Rocky minor update starts a new library,
  although P3M's RHEL binaries are per major version (the maintainer
  may prefer the major version only on the RHEL family). Where the
  os-release specification has defaults, they are used: no file, or no
  `ID`, gives `ID=linux` (`x86_64-linux`); no `VERSION_ID` (rolling
  releases such as Arch, Debian sid) leaves out `-<VERSION_ID>`
  (`x86_64-arch`). A value with a character outside `[A-Za-z0-9._-]`
  (the specification allows fewer) counts as missing, so no value can
  put the library outside `~/R/zur/`. The directory is created if
  missing; if that fails (a read-only home), R starts without the
  library, silently: `.libPaths()` drops a directory that does not
  exist. With `HOME` unset or empty `bin/R` sets nothing and R's
  default applies. `~` is `$HOME` without a trailing slash, so
  `HOME=/` gives `/R/zur/…`. The readings of the missing-file,
  missing-`VERSION_ID` and read-only cases are the roadmap's (stage 7)
  or follow from the specification; the maintainer may decide
  otherwise.
- Wheels cannot hold symlinks: every link becomes a real file or is
  removed. On Linux duplicated libraries are deduplicated by SONAME.
  On macOS a link to a library becomes a copy of it, which leaves the
  copy's signature valid.

## 6. Linux: a fresh headless build

### 6.1 Build environment

The `manylinux_2_28` image (AlmaLinux 8, gcc-toolset), natively on
x86_64 and aarch64 runners. It provides gcc, g++ and gfortran, and its
glibc is the one the wheel tag promises. Dependencies come from the
image's `-devel` packages where their versions are adequate, otherwise
built from pinned sources in the same container.

`manifests/linux.toml` names each of them: version, origin (an
AlmaLinux 8 package at an exact `[epoch:]version-release`, or a source
tarball by URL and SHA-256), SPDX licence, the SONAMEs it provides, and
why the image's version is adequate or why it is built. Decided
2026-11-24 (stage 4):

- **Built from source:** OpenSSL 3.5 (the current LTS series; the
  image's 1.1.1k is past upstream end of life) and libcurl 8.x with
  OpenSSL and zlib only, into `/opt/zur-deps`. They face the network,
  and §16 question 2 tracks them for rebuilds. OPENSSLDIR is
  `/etc/ssl`, and libcurl has no CA path compiled in: certificates come
  from the system through `bin/R` (§6.4).
- **Built from source: fontconfig 2.15.0**, because the image's 2.13.1
  lacks `<dir prefix="relative">`, which a relocatable `fonts.conf`
  needs (§6.4); it parses local files only. Its SONAME stays
  `libfontconfig.so.1`, so the image's cairo uses it (2026-11-24,
  stage 6).
- **From the image:** zlib, bzip2, xz, PCRE2, ICU, readline, cairo,
  freetype, libpng, libjpeg-turbo and libtiff. Each meets
  R-admin's minimum version, and R uses each on local data only.
- `dnf` installs exactly the pinned versions and the build refuses any
  other; every tarball, R's included, is verified before any is
  unpacked.
- R is configured with `CPPFLAGS` and `LDFLAGS` naming only
  `/opt/zur-deps`, and `PKG_CONFIG_PATH` naming only its `pkgconfig`,
  replacing `configure`'s default `/usr/local` paths and the image's
  `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig`:
  the image keeps builds there that are no pinned input.

### 6.2 `configure`

Checked against Posit's `r-builds` options and a survey of P3M
binaries (§18):

```sh
./configure --prefix=/opt/zur \
  --enable-R-shlib --enable-memory-profiling \
  --without-x --without-tcltk --without-aqua \
  --without-recommended-packages \
  --with-cairo --with-libpng --with-jpeglib --with-libtiff \
  --with-readline --with-pcre2 --with-ICU \
  --with-internal-tzcode \
  --disable-openmp \
  --disable-java
```

- `--enable-R-shlib`: P3M's binaries are built against an R with
  `libR.so`. (It also makes `rpy2` possible, but embedding R in Python
  is not a supported, tested use in the first release; §16.)
- Readline stays for the console in remote editor sessions.
- `--with-internal-tzcode` ships R's own zoneinfo, so time zones work in
  minimal containers without `tzdata`.
- **Cairo without pango.** `configure` uses pango when it finds it,
  and pango brings glib, harfbuzz and fribidi: the dependency chain
  that made the conda prototype too large (§17). The build container
  has no `pango-devel`, so R's cairo devices draw text through cairo,
  freetype and fontconfig only. Cost: no complex text layout
  (right-to-left, ligature shaping) in base `png()` and `svg()`;
  `ragg` and `svglite` bring their own. Measured 2026-11-24 (stage 9,
  §10): pango would add 20 grafted libraries and 4.1 MB to the pruned
  Linux x86_64 wheel.
- **BLAS and LAPACK (D16):** R's reference BLAS and LAPACK, shipped
  under R's names and also as `libblas.so.3` and `liblapack.so.3`
  (§6.3). Posit's R uses an external BLAS, and in a sample of 16 P3M
  binaries 8 link `libblas.so.3` (§18), so without those names half of
  the common packages would not load in a minimal container. The
  reference implementation gives the same numbers as CRAN's macOS and
  Windows builds; the cost is speed on heavy linear algebra against
  Posit's OpenBLAS.
- **No OpenMP in R's own code (D17):** `--disable-openmp`. The
  toolset's static `libgomp.a` cannot be linked into a shared library
  (§18), and a private copy next to the host's would put two OpenMP
  runtimes in one process. R itself uses OpenMP in very few places.
  After the build, `Makeconf`'s `SHLIB_OPENMP_*FLAGS` are set back to
  `-fopenmp`, so packages such as `data.table` still use the host's
  OpenMP.
- **Fortran runtime (D17):** R's libraries link the shared
  `libgfortran.so.5` and `libquadmath.so.0`, grafted under hashed names
  and used only by R's own libraries (§6.3, stage 5). The static link
  (`-static-libgfortran`, or naming `libgfortran.a` and `libquadmath.a`
  in `FLIBS`) was the first choice and fails in the image: R links its
  libraries with the C driver, and `libgfortran.a(async.o)` carries a
  local-exec TLS relocation that cannot go into `libRlapack.so` (§18).
  2026-11-24, stage 3: the first build answered §16 question 1 "no".
  The graft is the system's gcc 8.5 `libgfortran.so.5`, since the
  toolset ships no runtime of its own (§18).

### 6.3 Repair and relocation

- After `make install`, the build bundles every shared library outside
  the manylinux allowlist into the wheel, as `auditwheel repair` does,
  and rewrites RPATHs to `$ORIGIN`-relative ones.
- **`lib/R/lib` holds only what packages may link by plain name.** R's
  `ldpaths` puts that directory on `LD_LIBRARY_PATH` for every package
  R loads, so a library there keeps its real SONAME and wins over the
  host's copy. It holds R's own libraries (`libR.so`, `libRblas.so`,
  `libRlapack.so`) and the BLAS names below, nothing else.
- **No compiler runtimes there.** `libgfortran.so.5`,
  `libquadmath.so.0` and `libgomp.so.1` are not shipped on the library
  path: the manylinux toolset links the system's gcc 8 runtime (§18),
  older than any host compiler's, and a copy that shadows the host's
  breaks packages built with a newer gfortran (`version GFORTRAN_x not
  found`). R runs with nothing installed (§6.2, D17); packages resolve
  the host's runtimes, as under any other R (D11).
- **BLAS names.** R's reference BLAS and LAPACK are also shipped in
  `lib/R/lib` as `libblas.so.3` and `liblapack.so.3`, so P3M binaries
  load *and* numbers match CRAN's macOS and Windows (D16). Unlike the
  compiler runtimes, shadowing a host BLAS breaks nothing: the
  interface is stable.
- Only libraries R itself needs and no package links to are grafted
  with hashed names.
- **System baseline:** the manylinux allowlist plus nothing else for R
  itself. Binary packages need two further groups. Libraries a package
  declares (for example `libxml2` for `xml2`) are that package's system
  requirements, as with any R: the user or `pak` installs them. Libraries
  no package declares because R's own system package normally brings
  them (`libgomp1`, `libgfortran5`, …) are zur's to report: §6.5.
- `bin/R` computes `R_HOME` from its own location; `etc/Makeconf`,
  `etc/Renviron` and `etc/ldpaths` lose the build prefix; the compilers
  in `Makeconf` become plain `gcc`, `g++`, `gfortran` (not the
  gcc-toolset paths), so source packages use the machine's toolchain.
- Every `DT_NEEDED` of every ELF file in the wheel resolves inside the
  wheel or to an allowlisted system library; checked by the build
  (§13).

Decided 2026-11-24 (stage 5), from the first Linux x86_64 wheel:

- **The repair is the build's own code** (`repair()` in
  `scripts/build.py`, with `patchelf`), not `auditwheel repair`;
  `auditwheel show` is one of the build checks and reports
  `manylinux_2_28_x86_64` with no external library. R's files find
  `libR.so` and its BLAS through `ldpaths`' `LD_LIBRARY_PATH`, not an
  RPATH, and `lib/R/lib` must keep its plain names (D16), which
  `auditwheel repair` would graft or refuse; the own code is short and
  leaves the check independent of it.
- **Grafted: everything but glibc and the gcc runtime** (`libgcc_s`,
  `libstdc++`). The rest of the manylinux allowlist is grafted too: a
  headless container has no X11 libraries (cairo needs `libX11`,
  `libXext`, `libXrender`), and minimal images may lack `libexpat` or
  `libz`. The first wheel grafts 31 libraries: the Fortran runtime
  (`libgfortran.so.5`, `libquadmath.so.0`, used only by R's own five
  libraries and the two BLAS copies), OpenSSL and libcurl, ICU, PCRE2,
  readline with `libtinfo`, xz, bzip2, zlib, and cairo with pixman,
  fontconfig, expat, freetype, libpng, libjpeg, libtiff, `libjbig`,
  `libuuid` and the X11 libraries (`libX11`, `libXau`, `libXext`,
  `libXrender`, `libxcb`, `libxcb-render`, `libxcb-shm`).
- **RUNPATHs:** every file that needs a graft or one of R's libraries
  gets `$ORIGIN`-relative RUNPATH entries to `zur.libs/` and to
  `lib/R/lib`; the built dependencies' `/opt/zur-deps/lib` RPATH is
  removed.
- **Compiled-in build paths** (§13, "no build path in any wheel file")
  are overwritten by a path of the same length under `/proc/0`, which
  never exists: `Rscript`'s compiled R home, OpenSSL's module directory
  in `libcrypto`, and the dependency include paths in `internet.so`'s
  debug information. Nothing reads them once R runs from the wheel.
  `Rscript`'s compiled home is never read, because `bin/Rscript` always
  sets `RHOME` (§5).
- **`Makeconf`:** the compilers are the plain names `gcc`, `g++`,
  `gfortran`. `CC` is exactly `gcc`: configure chose `gcc -std=gnu23`,
  which no host gcc before 14 accepts (seen with gcc 8, 12 and 13 in
  the stage 5 containers), so packages compile in the host's default C
  standard and `Rconfig.h` decides `HAVE_ENUM_BASE_TYPE` per compiler.
  The C++ and Fortran standard flags configure chose stay after their
  compilers (stage 10 tests them across host compilers). The built
  dependencies' `-I` and `-L` flags are removed. (`CXX`'s flag was
  replaced in stage 10, below.)

Decided 2026-11-24 (stage 10), on the minimum host C++ compiler
([#11](https://github.com/pedrobtz/py-zur/issues/11)):

- **`Makeconf`, C++:** `CXX` is `g++ -std=$(ZUR_CXXSTD)`; `ZUR_CXXSTD`
  is set when make reads `Makeconf`: `gnu++20` if the host's `g++`
  accepts `-std=gnu++20` (gcc 10 onward), otherwise `gnu++17`. This is
  the default R's configure would choose on that host (C++20 where
  available, else C++17). The explicit standards (`CXX17STD` to
  `CXX26STD`) stay as the build's compiler set them; a package asking
  for one the host lacks fails in the compiler, where a natively
  configured R would report the standard as undefined. Rejected: a fixed
  `gnu++17` (differs from CRAN's R on every current host), plain `g++`
  (gnu++14 on gcc 8, below R's floor), and raising the minimum host
  compiler to gcc 10 (drops RHEL 8's system compiler, which D4
  supports).

### 6.4 Runtime files a headless container lacks

- **CA certificates:** `bin/R` (§5) points `CURL_CA_BUNDLE` and
  `SSL_CERT_FILE` at the first system bundle found
  (`/etc/ssl/certs/ca-certificates.crt`,
  `/etc/pki/tls/certs/ca-bundle.crt`, …), unless set. With no bundle
  (`debian:12-slim`) both stay unset, R starts, and HTTPS fails until
  the system's `ca-certificates` is installed; the tests install it
  there (roadmap stage 6).
- **Fonts:** a minimal container may have none, and cairo then draws
  text as boxes or fails. The wheel ships one small font family and a
  fontconfig file pointing at it plus the system font directories.
- **`/usr/bin/which`:** base R calls it on load (found on Rocky Linux 8's
  minimal image). A source build can configure the path or remove the
  dependency; the build makes `Sys.which` work without it.

Decided 2026-11-24 (stage 6):

- **The font family is Liberation Sans 2.00.3** (Regular, Bold, Italic,
  Bold Italic: R's font faces 1 to 4; 1.64 MB), licence `OFL-1.1-RFN`,
  from the image's `liberation-sans-fonts` package, pinned in
  `manifests/linux.toml`. It is metric-compatible with Helvetica, which
  R's devices request for `"sans"`, and has every Latin, Greek and
  Cyrillic glyph R's examples use, the euro sign among them. Rejected:
  DejaVu Sans (larger per face, and its licence has no single SPDX
  identifier). Stage 13 ships the licence text.
- **`fonts.conf`** is `$R_HOME/etc/fonts/fonts.conf`, the faces
  `$R_HOME/share/fonts/`. It names the shipped directory relative to
  itself (`prefix="relative"`), then `/usr/share/fonts`,
  `/usr/local/share/fonts`, `$XDG_DATA_HOME/fonts` and `~/.fonts`, with
  the cache in `$XDG_CACHE_HOME/fontconfig`, and appends Liberation
  Sans to every requested family, so a family the system has wins and
  any other falls back to it. It includes no system configuration: the
  wheel's fontconfig is 2.15 (§6.1), and a system's configuration may be
  older or newer. `bin/R` sets `FONTCONFIG_FILE` to it unless set. The
  image's fontconfig 2.13.1 cannot read `prefix="relative"`, which is
  why fontconfig is built (§6.1); writing a per-user configuration from
  `bin/R` was rejected: it writes outside the tool directory and fails
  with a read-only `HOME`.
- **`WHICH` is `trap 'exit 0' 0; command -v`** (configure's cache
  variable `ac_cv_path_WHICH`), which base R compiles in where it would
  have put `/usr/bin/which`; `Sys.which` runs it through `/bin/sh -c`.
  `command -v` searches `PATH` as `which` does. dash exits 127 when
  `command -v` finds nothing, which `system(intern = TRUE)` turns into
  an error; the exit trap makes the status 0, so `Sys.which` of a
  missing name returns `""`. The value has no `$ : & "` or backslash,
  so it passes unchanged through `Makefile`, `basepkg.mk`'s sed and the
  R string literal. One difference from `which`: for a name that is also
  a shell builtin (`echo`, `printf`, `test`, `kill`, `pwd`, `true`,
  `false`), `command -v` prints the bare name, so `Sys.which` returns
  `""` there. Rejected: patching base R's source (D15 would need a
  notice) and a `which` script in the wheel (base R compiles in one
  fixed path).
- **Bitmap devices use cairo.** Without pango grDevices would default
  `bitmapType` to `"Xlib"`, which a `--without-x` R cannot use; the build
  appends `options(bitmapType = "cairo")` to the system profile
  `library/base/R/Rprofile`, read even under `--vanilla`, so `png()`,
  `jpeg()`, `tiff()` and `bmp()` use cairo. grDevices' `.onLoad` sets
  only options not already set, so a user's later `options()` still
  wins. Linux only.
- **Time zones:** `--with-internal-tzcode` is in use (configure's call,
  recorded in `Makeconf`, has it, and `$R_HOME/share/zoneinfo` ships);
  `Europe/Zurich` converts in `debian:12-slim` with its `tzdata` deleted.

### 6.5 `zur doctor`

P3M binaries assume every library Posit's R package depends on is
installed, because that package brought them. zur replaces it, so
`zur doctor` checks that list (fixed per distribution, taken from the
dependencies of Posit's R `.deb`/`.rpm`, §18) and prints what is
missing with the exact command for the detected distribution. It
reports two groups:

- **To load binary packages:** `libgomp1` and `libgfortran5` (Debian
  family; `libgomp` and `libgfortran` on the RHEL family). Everything
  else Posit's package depends on is either inside the wheel or only
  needed by parts zur leaves out (Tcl/Tk, X11, pango).
- **To compile source packages:** `gcc`, `g++`, `gfortran` and `make`.
  Reported separately and never counted as a failure, since many jobs
  install only binaries.

How it behaves:

- Exit status 0 when nothing in the first group is missing, non-zero
  otherwise, so a CI step can gate on it. The second group never
  changes the exit status.
- zur never installs anything and never calls `sudo`: the user runs
  the printed command. The output is one copy-pasteable line per
  package manager step, prefixed with `sudo` only when the user is not
  root, and `zur doctor --command` prints that line alone, so a CI step
  can write `eval "$(zur doctor --command)"`.
- For a distribution zur does not recognise, it lists the missing
  library file names (`libgomp.so.1`, …) and says which packages
  provide them on Debian and RHEL families, as a starting point.
- Package-declared system requirements are out of scope: `pak` installs
  those.
- On macOS it reports whether XQuartz is present, since `svg()` and
  the other cairo devices need it (§7), and whether a compiler is
  found. On Windows it reports whether Rtools is found: GitHub's
  Windows images list R but not Rtools (§18). Neither is a failure.

**First-run warning.** On Linux, the first time `R` or `Rscript`
starts, the launcher runs the same check. If anything is missing it
prints a short note to stderr (the missing libraries, the command, and
a pointer to `zur doctor`), then starts R as usual: R itself works
without them, so the warning never changes the exit status.

- Once per installation: a stamp file under `~/R/zur/`, named for the
  zur version, records that the check ran, whatever its result, so
  later starts skip it. A fresh container has no stamp and checks
  again.
- `ZUR_NO_CHECK=1` disables it, for jobs that compare stderr or treat
  any stderr output as failure.
- If the stamp cannot be written (read-only home), the check is
  skipped without a message.
- The warning lives in the launcher only. R started directly through
  `$R_HOME/bin/R` (child processes, `R CMD`) does not check, which is
  accepted for a one-time hint.

### 6.6 Packages: `pak` is the documented route

D9 stands: zur sets no repository. That leaves base R's behaviour,
where `install.packages("x")` under `Rscript` stops with "trying to use
CRAN without setting a mirror", and a plain CRAN mirror compiles
everything from source on Linux. The README therefore leads with
`pak`:

```sh
uv tool install 'zur==4.6.2.*'
Rscript -e 'install.packages("pak", repos = sprintf(
  "https://r-lib.github.io/p/pak/stable/%s/%s/%s",
  .Platform$pkgType, R.version$os, R.version$arch))'
Rscript -e 'pak::repo_add(CRAN = "PPM@latest"); pak::pkg_install("data.table")'
```

`pak` alone resolves `@CRAN@` to a source-only mirror (§18.2).
`repo_add()` points it at P3M, where it picks the binaries for the
running distribution and installs package-declared system requirements
when it can. The setting lasts for that R session only, so each
`Rscript` call that installs packages repeats it, or the user sets
`repos` in their own `.Rprofile`. On a distribution P3M has no binaries
for, pak compiles from source and needs the toolchain `zur doctor`
lists. The same holds for a package P3M has not built for the running
distribution: its Debian 12 index lacks `data.table`, `RcppArmadillo`
and `xml2` (§18.2), so there the recipe compiles `data.table` from
source. Debian 13 has them.

## 7. macOS: CRAN's binaries, unmodified

- **Input:** CRAN's R `.pkg` for each architecture, by version and
  SHA-256. Extracted with `pkgutil --expand-full`, never installed.
- **No Mach-O file is changed (D18).** CRAN's binaries refer to each
  other by absolute framework path
  (`/Library/Frameworks/R.framework/Versions/4.6-x86_64/Resources/lib/libR.dylib`).
  dyld resolves those by file name through two variables, which
  CRAN's signature explicitly permits (hardened runtime with the
  `allow-dyld-environment-variables` entitlement):
  - `DYLD_FALLBACK_LIBRARY_PATH=$R_HOME/lib`, which R's own `ldpaths`
    already sets, is searched when the absolute path does not exist:
    the normal case on a CI runner.
  - `DYLD_LIBRARY_PATH=$R_HOME/lib`, which zur adds in `bin/R`, is
    searched before the absolute path, so zur's libraries win even
    when a framework R of the same version is installed.
- **Edits are text only:** `bin/R` takes `R_HOME` from its own
  location and exports `DYLD_LIBRARY_PATH`; `bin/Rscript` becomes the
  script of §5, with CRAN's binary moved to `Resources/libexec/Rscript`
  (not `bin/exec/`, §5); `Renviron`
  loses the framework path; in `Makeconf` framework linking becomes
  `-L"$(R_HOME)/lib" -lR`, so source packages compile and link against
  the copy. A package built that way records `libR.dylib`'s own
  identifier, which is still the framework path, and so loads the same
  way a CRAN binary does. CRAN's Developer ID signatures stay valid,
  so there is no ad-hoc signing, and the build needs `pkgutil` only.
- **`Rscript`** has the framework path compiled in. Started without
  `RHOME` it runs the framework R if there is one and fails otherwise
  (measured: the first run of the experiment silently used the
  machine's own R). Its compiled home is never read, because
  `bin/Rscript` always sets `RHOME` (§5).
- **CRAN binary packages load as downloaded**, in any library and
  under `--vanilla`. No patcher, no hook in `install.packages()`, no
  write to the user's library. Measured (§18): `jsonlite`, `Rcpp`,
  `data.table`, `mvtnorm` and `stringi` return known answers with one
  `libR` in the process, both with the framework path absent and with
  a framework R 4.6 installed. Without `DYLD_LIBRARY_PATH` the second
  case loads the framework's `libR` as well and segfaults, which is
  why the variable is required and not an optimisation.
- **Limits of the variables.** macOS strips `DYLD_*` when a process
  passes through a protected binary such as `/bin/sh`, so they do not
  leak into `system()` children (measured), and a child R started
  through `bin/R` sets them again. Any library in `$R_HOME/lib`
  (`libomp.dylib`, `libgfortran.5.dylib`, `libgcc_s.1.1.dylib`) wins by
  file name over another copy a package might expect, which is what a
  CRAN R does for its own packages anyway.
- **Graphics.** `png()` (Quartz, the default) and `pdf()` work with
  nothing installed. CRAN's `cairo.so` links XQuartz's libraries under
  `/opt/X11`, so `svg()`, `cairo_pdf()` and `png(type = "cairo")` fail
  without XQuartz although `capabilities("cairo")` says `TRUE`
  (measured). GitHub's macOS images do not list XQuartz. zur keeps
  `cairo.so` so those devices work where XQuartz is installed, and
  `zur doctor` says so (§6.5).
- **Still to confirm in CI:** the same on arm64, and on a runner that
  never had a framework R (the local test simulated its absence).

## 8. Windows: CRAN's binaries

- **Input:** CRAN's R installer by version and SHA-256. R on Windows is
  relocatable as shipped.
- **Unpacking:** `innoextract`, on Linux, so the build needs no
  Windows runner (testing does). The R 4.6.2 installer declares Inno
  Setup data format 6.1.0, which `innoextract` 1.9 supports as
  "preliminary" (§18); the first Windows build checks that it unpacks
  and that the tree matches a silent install file for file. If it
  does not, the build runs the installer silently for the current user
  on a Windows runner (`/VERYSILENT /CURRENTUSER /DIR=...`), which
  needs no administrator rights.
- Prune (§9), then pack. CRAN's Windows binary packages link `R.dll` by
  name and work unchanged.
- Source packages need Rtools on the machine, as with any CRAN R.

## 9. What a headless wheel leaves out

Only whole files that nothing remaining links to, so prebuilt binaries
stay intact. Each item is decision D6; anything else stays.

| Removed | Why it is safe headless |
| --- | --- |
| Recommended packages (Linux: never built) | Installable from CRAN; D1 |
| `doc/manual`, `NEWS.pdf`, other manuals | Not read on a CI runner; `?` uses `help/` |
| Package vignettes (`library/*/doc`), `tests/`, `demo/` | Not used by scripts |
| X11 module (macOS), Tcl/Tk and `tcltk` (Windows, macOS) | No display; D6 |
| GUI front-ends (`Rgui.exe`, `R.app`) | Not headless |
| Debug symbol bundles (`*.dSYM`) | Not used at run time |
| The `translations` package and `po/` directories (about 3 MB) | CI logs and remote consoles; English messages are also the searchable ones. D19 |

Kept: help pages (`?` in remote sessions), headers (`R CMD INSTALL`),
`Makeconf`, cairo and the bitmap devices, readline, ICU (faithful
collation, as on CRAN's macOS and Windows), libcurl.

On Linux the list is `PRUNE` in `scripts/build.py`, glob patterns
relative to the wheel's root, applied after the repair (2026-11-24,
stage 9): a pruned file that something still needed would leave a
`DT_NEEDED` the build check of §13 reports, rather than a graft the
repair silently skipped. "Other manuals" is read as `doc/manual` and
the PDFs in `doc/`; the plain-text `doc/` files (`COPYING`, `NEWS*`,
`FAQ`, `RESOURCES`) stay: `news()` reads `NEWS.rds`, and the rest are
small. The sizes are in §10.

## 10. Size

PyPI's default per-file limit is "100.0 MB"
([storage limits](https://docs.pypi.org/project-management/storage-limits/),
read 2026-11-24), which is 100 MiB, 104,857,600 bytes: warehouse's
`MAX_FILESIZE = 100 * ONE_MIB` (`warehouse/constants.py`), refused only
when an upload is larger (`forklift/legacy.py`; pypi/warehouse at
`de56d81`). `scripts/build.py` names it `PYPI_MAX_FILE_SIZE`, the default
of `--max-size`; the build fails when the wheel is larger (stage 9,
2026-11-24). Estimates for R 4.6.2, from portable-r's archives
re-measured as deflate-compressed wheel members, 2026-11-23:

| Platform | CRAN as shipped | After §9 |
| --- | --- | --- |
| macOS arm64 | 87.5 MB | about 37 MB |
| Windows x64 | 108.4 MB | about 55 MB |
| Linux x86_64 | (source build) 64.3 MB unpruned | 59.3 MB, measured (stage 9) |

The Linux x86_64 wheel before §9's prune was 63,122,429 bytes
(63.1 MB) on 2026-11-24 (stage 5): R 4.6.2 from source with the 31
libraries of §6.3 grafted (run
https://github.com/pedrobtz/py-zur/actions/runs/45310120781). With the
font and fontconfig of stage 6 it was 64,332,528 bytes on `main` before
stage 9 (run https://github.com/pedrobtz/py-zur/actions/runs/45340151137).

**Linux x86_64 after §9's prune, 2026-11-24 (stage 9):** 59,307,399
bytes, 692,601 bytes (0.7 MB) under the 60 MB target and 45,550,201
bytes under the limit (run
https://github.com/pedrobtz/py-zur/actions/runs/45343171531). The prune
removed 10.0 MB before compression, 5.0 MB of the wheel:

| Pruned (`PRUNE` in `scripts/build.py`) | Files | Bytes unpacked |
| --- | --- | --- |
| `doc/manual` (HTML manuals only; no PDFs are built) | 6 | 32,596 |
| `doc/*.pdf` (`NEWS.pdf`) | 1 | 568,280 |
| `library/*/doc` (vignette PDFs of grid, parallel, stats, utils) | 18 | 1,087,407 |
| `library/*/demo` | 20 | 113,518 |
| `library/translations` (D19) | 485 | 9,314,286 |
| `library/*/tests`, `tests`, `library/*/po` | 0 | 0 |

`make install` writes no tests and no per-package `po/` for the base
packages; the patterns stay for the CRAN layouts of §7 and §8.

The size report (`scripts/build.py --size-report`) of that wheel, by
part; "in wheel" is the compressed size, and the last row is the zip
structure, so the column sums to the file's size:

| Part | Files | In wheel | Unpacked | Share |
| --- | --- | --- | --- | --- |
| R's libraries (`lib/R/lib`) | 5 | 10,907,095 | 32,044,541 | 18.4% |
| ICU data (`libicudata`) | 1 | 10,741,968 | 27,265,081 | 18.1% |
| bundled libraries (`zur.libs`, the rest) | 29 | 10,447,523 | 129,449,117 | 17.6% |
| help (`library/*/help`, `html`) | 105 | 7,797,885 | 8,376,635 | 13.1% |
| base packages (the rest of `library`) | 343 | 13,813,441 | 20,223,192 | 23.3% |
| fonts | 4 | 847,671 | 1,638,872 | 1.4% |
| everything else | 856 | 4,520,772 | 9,610,617 | 7.6% |
| zip headers and directory | | 231,044 | | 0.4% |

**What D14 saves, 2026-11-24 (stage 9):** the same commit built once
with `pango-devel` installed (the `pango` dispatch input of `build.yml`,
run https://github.com/pedrobtz/py-zur/actions/runs/45343819148) gives
63,412,225 bytes: 4,104,826 bytes (4.1 MB, 6.9%) more, all of it in
bundled libraries: 50 grafted instead of 30 (14,553,077 bytes in the
wheel, 217,788,817 unpacked). The 20 more are pango, pangocairo,
pangoft2, harfbuzz, graphite2, fribidi, thai, datrie, glib, gobject,
ffi, pcre, and a GnuTLS chain (gnutls, nettle, hogweed, gmp,
p11-kit, tasn1, idn2, unistring). Without D14 the wheel would be 3.4 MB
over the 60 MB target.

Largest items removed on macOS and Windows: recommended packages about
26 MB, manuals about 18–19 MB, Tcl/Tk or X11 about 4–5 MB. The build
fails when a wheel exceeds its budget (§13).

The project total (10 GB default) holds about 30 R versions at five
wheels of about 50 MB each.

## 11. Versioning and R versions

- **Distribution version = R version.** A rebuild of the same R (a
  launcher fix, a security update of a bundled library) is
  `4.6.2.post1`, `4.6.2.post2`, ….
- **The documented pin is `zur==4.6.2.*`**, which selects R 4.6.2 in
  its newest build. Under PEP 440 a plain `zur==4.6.2` matches only the
  first build, never a `.postN`, and still installs it after a yank, so
  it would hold users on a build that was replaced for a reason. The
  README, the examples and the benchmark all use the `.*` form; an
  exact `zur==4.6.2.post1` is for those who want one build and no
  other. *(Maintainer, 2026-11-23.)*
- **A rebuild carries every platform already published** for that R
  version, so `zur==4.6.2.*` never resolves to a build that lacks a
  wheel the previous one had.
- **First release:** R 4.6.2, the current R on 2026-11-23. Later
  releases follow each R patch release. A back-catalogue from 4.4.0 is
  after the first release.
- **Pinned inputs:** R source tarball and CRAN installers by SHA-256,
  dependency versions in a committed manifest per platform
  (`manifests/linux.toml` from stage 4, §6.1). `BUILD-INFO.json`'s
  `inputs` section repeats them: R's tarball and SHA-256, the image by
  digest, and every manifest entry.
- **R-devel** stays off PyPI.

## 12. Speed benchmark

A workflow measures, on `ubuntu-latest`, `ubuntu-24.04-arm`,
`macos-latest` and `windows-latest`, two end points:

- **R ready:** `Rscript -e 1` succeeds.
- **Package ready:** a package with compiled code (`data.table`) is
  installed and `library()` loads it. This is the number a CI user
  feels; R alone is a small part of a job.

For each end point:

- **cold:** `uv tool install 'zur==<v>.*'` with an empty uv cache, then
  the §6.6 recipe;
- **warm:** the same with the uv cache restored by `actions/cache`;
- **baseline:** `r-lib/actions/setup-r@v2` with `r-version: <v>`, then
  `setup-r-dependencies@v2` for the same package, with no cache for
  cold.

One more cold case runs in a `debian:13-slim` container that has uv
but no Python, where uv first downloads an interpreter for the
launcher: the honest cost on a bare image.

Each case runs at least five times and reports the median. Before the
release it runs against TestPyPI, which is a release gate: on Linux
the cold median must not exceed the baseline's for either end point.
After the release it runs against PyPI, and the results go in the
README. *(Builder's call, from review.)*

**Release order.** `linux x86_64` ships first, as soon as it passes
its tests and the gate above. The other four wheels are added to the
same version as each passes its own tests; PyPI accepts further wheels
for an existing version. No other platform is on the first wheel's
critical path. *(Maintainer, 2026-11-23, replacing "all five
together" of the same day.)*

## 13. How it is tested

Tests run against the installed tool and compare against known answers:

- **User library:** with `R_LIBS_USER` unset, `.libPaths()[1]` is
  zur's directory and exists; with it set, the user's value wins; the
  same holds when R is started through `$R_HOME/bin/R` without the
  launcher; on Linux the directory names the distribution, and the
  same home used from Ubuntu and Rocky containers gives two libraries;
  a package in it still loads after `uv tool install --reinstall`; with
  a read-only home R starts and exits 0.
- **`pak` recipe (§6.6):** in the Ubuntu 24.04, Debian 13 and Rocky
  Linux 8 containers `pak` installs, the recipe's `pkg_install("data.table")` after
  `repo_add(CRAN = "PPM@latest")` downloads a binary (no compiler is
  run) and the package loads; `getOption("repos")` is `@CRAN@` before
  and after (D9).
- **Front-ends:** `R.home()` inside the tool; an inherited `R_HOME`
  ignored; `R` reads a script from stdin; exit status passed through;
  an `Rscript` started from inside R with `system2()` reports the same
  `R.home()`.
- **Base packages only:** `installed.packages(.Library)` lists exactly
  R's base packages.
- **Binary packages:** on Ubuntu, a set of P3M binaries chosen to cover
  plain C, C++ (`Rcpp`), Fortran and BLAS/LAPACK users installs and
  loads, and each returns a known answer; CRAN binaries likewise on
  Windows, and on macOS in zur's library, in a user-set `R_LIBS_USER`,
  in a `lib=` directory and under `--vanilla`, each time with exactly
  one `libR` image in the process, on a runner with and without a
  framework R installed.
- **Source packages:** C, C++ and Fortran fixture packages compile with
  the system toolchain. `Makeconf` and `Rconfig.h` record what the
  build's compiler supports but are used with the host's, so the
  fixtures run across a matrix: the oldest supported host compiler
  (gcc 8, Rocky Linux 8), the newest in a current distribution, and
  one in between, each covering C, C++ and Fortran. The default-standard
  C++ fixture is never skipped and reports `__cplusplus`: `201703` on
  gcc 8, `201709` on gcc 10 (its C++20 is pre-final), `202002` on
  gcc 11 onward. The newest-standard fixture runs
  where the host's `g++` accepts that flag and is skipped elsewhere,
  with the gcc version in the reason.
- **`zur doctor`:** in a minimal container it names `libgomp1` and
  `libgfortran5` and exits non-zero, while `Rscript -e 1` still
  succeeds; after running the line `zur doctor --command` prints, it
  exits 0 and a P3M `data.table` loads; on an image with those two
  libraries but no compiler it exits 0 and lists the toolchain as
  absent; on a full image it prints nothing to install.
- **First-run warning:** in a minimal container the first `Rscript -e
  1` prints the note on stderr and exits 0, and the second prints
  nothing; with `ZUR_NO_CHECK=1` the first prints nothing; on a full
  image stderr is empty.
- **Graphics headless:** `png()` and `pdf()` each write a file with
  the right magic bytes, including non-ASCII text, with no `DISPLAY`;
  `svg()` likewise on Linux and Windows, and on macOS once XQuartz is
  installed.
- **macOS signatures:** every Mach-O file in the wheel still verifies
  with CRAN's signature (`codesign -v`), proving none was changed.
- **Network and data:** HTTPS download; `Europe/Zurich` conversion in a
  container without `tzdata`.
- **Relocation:** under `strace` (Linux), no successful open of any
  build-prefix path; no build path in any wheel file; on macOS no
  framework path in any text file R reads (`bin/R`, `Makeconf`,
  `Renviron`).
- **Build checks:** every `DT_NEEDED` (or Mach-O load command) resolves
  inside the wheel or to an allowed system library; no symlinks;
  `RECORD` hashes match; wheel at or under its size budget, and a
  synthetic wheel one byte over fails.
- **Containers:** the Linux tests run in Ubuntu 24.04, Debian 12 and
  Rocky Linux 8 images, and in two minimal ones: `debian:12-slim`
  (no fonts or CA bundle; its `tzdata` is deleted before the tests) and
  `rockylinux:8-minimal` (no `which`). The `pak` recipe runs in Debian
  13 instead of Debian 12, where P3M's coverage is partial (§18.2).

## 14. Licensing

R is GPL-2.0-or-later; bundled libraries carry their own licences.
Each wheel ships every licence text under `zur/_runtime/licenses/`, and
`BUILD-INFO.json` names the exact sources: the R tarball and dependency
sources for Linux (the build scripts are in this repository), and the
CRAN installers for macOS and Windows.

zur is the distributor of these binaries, so it provides the
corresponding source itself and does not rely on a third party keeping
it online. Each zur version has a GitHub release holding the source
archives: R, every library built for Linux, and for macOS and Windows
the sources of the libraries CRAN links into its builds, as named by
CRAN's macOS recipes and the Rtools source archives. Any linked
library whose exact source cannot be identified is listed as such in
`BUILD-INFO.json`, not left out silently. The README and
`BUILD-INFO.json` point to the release.

The repository's own `LICENSE` is the GNU GPL version 2 text as the FSF
publishes it now (`gnu.org/licenses/old-licenses/gpl-2.0.txt`), the
licence the wheel metadata names; `tests/test_unit.py` pins its SHA-256.
2026-11-24, stage 2: the build scripts and launcher are distributed in
every wheel next to R, so they carry R's licence.
2026-11-24, stage 6: zur's edits to R's installed files are text only:
`bin/R` (§5, §6.4), `bin/Rscript` (a script in place of the binary,
§5), and on Linux the line `options(bitmapType = "cairo")` appended to
the system profile `library/base/R/Rprofile` (§6.4).

## 15. Decisions

- **D1. Hybrid runtime source.** Linux built from source; macOS and
  Windows from CRAN's binaries. No recommended packages on any platform.
  Maintainer, 2026-11-23 (approach C, §4).
- **D2. Name `zur`.** Short, free on PyPI, no collision with `rlang`,
  `renv`, `box` or conda-forge's `r-base`.
- **D3. Launcher: Python console scripts.** 17 ms per start, identical
  on every platform.
- **D4. Linux tag `manylinux_2_28`.** RHEL 8 and Ubuntu 20.04 onward.
- **D5. No compiler in the wheel.** Source packages use the machine's
  toolchain; `Makeconf` names plain `gcc`, `g++`, `gfortran` on Linux.
  The C standard is the host's default; the default C++ standard is
  chosen per host, `gnu++20` or `gnu++17` (§6.3). Minimum supported
  host compiler: gcc 8.
- **D6. Headless prune set** as in §9; anything else needs a new
  decision.
- **D7. The build scripts write the wheels directly.** `pyproject.toml`
  describes the build environment only.
- **D8. No zig for R itself** in the first release (§4).
- **D9. No repository configuration.** `repos` stays as base R sets it
  (`@CRAN@`), and zur sets no user agent, environment variable or
  profile for repositories. The documented recipe (§6.6) names P3M
  itself, through `pak::repo_add(CRAN = "PPM@latest")`. Decided
  2026-11-24 after stage 8 (§18.2): pak maps `@CRAN@` to a source-only
  mirror, so the recipe, not zur, chooses the repository. Rejected:
  setting `repos` in R's profile (differs from CRAN's R, skipped by
  `--vanilla`, freezes a distribution table in the wheel).
- **D10. zur's own user library**, `~/R/zur/<platform>/<major.minor>`,
  with the distribution in `<platform>` on Linux, set through
  `R_LIBS_USER` only when the user has not set it.
  Maintainer, 2026-11-23. Rejected: R's standard user library (shared
  with any other R of that minor version, and on Linux across
  distributions) and a library inside the tool environment (wiped by
  every reinstall). Measured 2026-11-24 (stage 7): `uv tool install
  --reinstall` (uv 0.7.11, and the uv `setup-uv@v6` installs in CI)
  rewrites the wheel's files and keeps every other file in the tool's
  directory, so a library inside it would survive that; `uv tool
  uninstall`, and a tool directory made afresh (a CI runner without
  it cached), remove it. The decision stands on those cases and on the
  other reasons.
- **D11. `zur doctor`, no bundled compiler runtimes.** R needs no
  compiler runtime from the machine (D17) and ships no `libgfortran`,
  `libgomp` or `libquadmath` on the library path; `zur doctor` reports
  the system libraries binary packages assume and prints the command
  to install them; the user runs it. The first `R`/`Rscript` start on
  Linux warns once on stderr when something is missing (§6.5).
  Maintainer, 2026-11-23. Rejected: bundling the runtimes under their
  real names (shadows the host's),
  installing at `uv tool install` time (wheels run no code on install),
  and zur installing system packages itself (needs root, changes the
  machine).
- **D12. Linux x86_64 ships first**; other platforms join the same
  version as they pass (§12). Maintainer, 2026-11-23.
- **D13. Environment in R's own files** (`bin/R`, `etc/Renviron`) on
  POSIX, not in the launcher (§5). An inherited `RHOME` or `R_HOME` is
  always overridden, never honoured (2026-11-24).
- **D14. Cairo without pango** on Linux (§6.2).
- **D15. zur mirrors corresponding source** per version (§14).
- **D16. Reference BLAS and LAPACK on Linux**, also under
  `libblas.so.3` and `liblapack.so.3` (§6.2, §6.3).
- **D17. R's own code without OpenMP; Fortran runtime grafted** on
  Linux under a hashed name, used only by R's own libraries (§6.2).
  2026-11-24: the static runtime was the first choice and does not
  link in the manylinux image (§18).
- **D18. macOS binaries unmodified.** `DYLD_LIBRARY_PATH` and
  `DYLD_FALLBACK_LIBRARY_PATH` in `bin/R` replace `install_name_tool`,
  re-signing and any package patcher (§7). From the experiment of
  2026-11-23 (§18).
- **D19. Translations dropped**, ICU kept (§9).
- **D20. `innoextract` first on Windows**, silent install as the
  fallback (§8).

Decisions D12 to D20 were agreed by the maintainer on 2026-11-23.

## 16. Open questions

None of these blocks starting the Linux build. They stay open on
purpose and are settled by tests and spikes during implementation
(maintainer, 2026-11-23). Questions 2 to 4 carry a working default,
not a decision.

1. **Confirm in CI what was measured locally or read.** Each is the
   first step of its platform's build, and each names what changes if
   the answer is no:
   - Linux: R's link with `-static-libgfortran` succeeds in the
     manylinux image. If not, the hashed graft of §6.2. Answered
     2026-11-24 (stage 3): no; the graft is taken (§18).
   - `pak` selects P3M binaries on a zur-built R (§6.6). Decided
     2026-11-24 (stage 8): not with `repos` as base R sets it (§18.2);
     the recipe adds P3M with `pak::repo_add()`, and D9 stands as
     restated in §15.
   - macOS: §7 holds on arm64 and on a runner that never had a
     framework R. If not, the patcher that §7 replaced comes back as
     a new decision.
   - Windows: `innoextract` 1.9 unpacks the installer. If not, the
     silent install of §8.
2. **Rebuild policy.** The Linux wheel bundles libcurl and OpenSSL
   (built from source since 2026-11-24, stage 4: §6.1).
   Default: a scheduled workflow checks the libraries in
   `BUILD-INFO.json` against published advisories and opens an issue;
   the maintainer decides on a `.postN`; only the newest patch release
   of the current and the previous R minor version is rebuilt.
3. **Environment on Windows.** D13 moves the user library and the
   other settings out of the launcher on POSIX only. Default for the
   first release: the launcher sets them, and R started outside the
   launcher on Windows does not get zur's user library. A place in R's
   own files is looked for when the Windows wheel is built.
4. **macOS x86_64 runners.** GitHub is retiring Intel macOS runners.
   Default: the x86_64 wheel is built and tested while such a runner
   exists, and stops at the R version current when it goes.
5. **RHEL-family survey.** §18's sample is Ubuntu 24.04 only; the same
   reading of `DT_NEEDED` for P3M's RHEL 8 and 9 binaries confirms
   `zur doctor`'s list there. Done as part of the `zur doctor` work.
6. Later: `rpy2` embedding as a supported, tested use; rig-like helpers
   for binary repositories and system requirements; registering with
   Positron's R discovery; offering `zur` to `ir`; zig for building C
   dependencies; a bundled OpenBLAS as an option.

## 17. Background: the conda-forge prototype

The first prototype (PR #1, `scripts/build.py` until stage 5 replaced
it) repackaged conda-forge's
`r-base` and `r-recommended` for `linux-64`. It built a working wheel,
passed 13 installed-tool checks in Ubuntu 24.04, Debian 12 and Rocky
Linux 8 containers, and established the launcher, the relocation of
`bin/R`, `Makeconf`, `Renviron` and `ldpaths`, symlink removal by
SONAME, and the `strace` relocation test, all of which carry over.

It was set aside on 2026-11-23 because:

- **Size.** The R 4.5.3 wheel was 138 MB (113 MB without recommended
  packages). Prebuilt conda binaries can only lose files, not be rebuilt
  smaller, and conda-forge ships ICU, glib and others as separate
  libraries.
- **R 4.6.** conda-forge has no `r-recommended` for R 4.6 (newest 4.5,
  pinned to `r-base <4.6`).
- **Scope.** A headless R aimed at CI is better served by a build whose
  options we choose.

Two lessons stay: rattler-style hardlinked caches must be unshared before
anything edits a prefix; and base R's dependence on `/usr/bin/which`
breaks minimal images.

## 18. Measurements, 2026-11-23

Read or run on the maintainer's machine (macOS 15.7, x86_64, with
framework R 4.2 to 4.6 installed), without Docker. Linux results come
from reading package files, not from running the image.

**Posit's R (`rstudio/r-builds`, `builder/`).** Default options:
`--enable-R-shlib --with-tcltk --enable-memory-profiling --with-x
--with-blas --with-lapack`. The Ubuntu 24.04 package depends on
`libopenblas-dev`, `libgomp1`, `gfortran`, `gcc`, `g++`, `make`, the
libraries R links (cairo, pango, curl, ICU, readline, Tcl/Tk, X11) and
the `-dev` packages in `Makeconf`'s `LIBS`; the RHEL 9 package on
`gcc`, `gcc-c++`, `gcc-gfortran`, `make`, `which`, a BLAS and the same
kind of libraries.

**P3M binaries, Ubuntu 24.04, built for R 4.6.0.** `DT_NEEDED` of the
shared objects of 16 packages (`Rcpp`, `jsonlite`, `data.table`,
`Matrix`, `mgcv`, `quantreg`, `glmnet`, `deSolve`, `RcppEigen`,
`RcppArmadillo`, `stringi`, `xml2`, `curl`, `nloptr`, `robustbase`,
`igraph`):

| Library | Packages |
| --- | --- |
| `libR.so` | 16 |
| `libstdc++.so.6`, `libgcc_s.so.1` | 8 |
| `libblas.so.3` | 8 |
| `liblapack.so.3` | 7 |
| `libgomp.so.1` | 3 (`data.table`, `mgcv`, `RcppArmadillo`) |
| `libgfortran.so.5` | 1 (`deSolve`) |
| Package-declared (`libxml2`, `libcurl`, ICU 74, `libglpk`) | 4 |

None links `libRblas.so` or `libRlapack.so`.

**manylinux_2_28 toolchain (gcc-toolset-14, AlmaLinux 8 packages).**
`libgfortran.so`, `libgomp.so` and `libquadmath.so` in the toolset are
linker scripts pointing at the system's gcc 8.5 libraries in
`/usr/lib64` (`libgfortran.so.5` plus a static `libgfortran_nonshared.a`).
There is no toolset runtime package. Static archives exist:
`libquadmath.a` is position independent; `libgfortran.a` has 13
absolute relocations among about 23,000; `libgomp.a` has 701
local-exec TLS relocations and cannot go into a shared library.

**macOS, CRAN's R 4.6.2 x86_64** (`R-4.6.2-x86_64.pkg`, SHA-256
`aa9b1dba09cc69710571ad93efba26a9bcb5a0098543dadc4bb8e419a423195e`),
expanded with `pkgutil`, with only `bin/R` edited. The absence of a
framework R was simulated by pointing every reference at a path that
does not exist and re-signing ad hoc; the last row changes no binary.

| Case | Result |
| --- | --- |
| R starts, `lm()` runs, BLAS and LAPACK from the copy | Works |
| CRAN binary packages, framework path absent, `--vanilla` | Work; one `libR` |
| Same, framework R 4.6 present, fallback variable only | Segfault in the first package call |
| Same, with `DYLD_LIBRARY_PATH` in `bin/R` | Work; one `libR` |
| No Mach-O changed, CRAN signatures valid, framework R present | Work; one `libR` |

In the same copy `png()` and `pdf()` write files; `svg()` fails for
want of `/opt/X11/lib/libXrender.1.dylib`. `DYLD_LIBRARY_PATH` is set
inside R and unset in a `system()` child. The expanded `Resources`
tree is 167 MB before pruning.

**Windows.** The R 4.6.2 installer declares "Inno Setup Setup Data
(6.1.0)". `innoextract`'s latest release is 1.9 (August 2020), which
lists preliminary support for 6.1.0. Not run.

**GitHub runner images** (readme files, `actions/runner-images`):
Windows 2022 and 2025 list R 4.6.2 and no Rtools; macOS 15 lists
gfortran 13 to 15 from Homebrew and no XQuartz.

### 18.1 Measurements, 2026-11-24 (stage 3)

Run in GitHub Actions (`ubuntu-latest`, 4 cores) in
`quay.io/pypa/manylinux_2_28_x86_64:2026.11.21-1` (digest in
`scripts/linux/image`), gfortran 14.2.1 (gcc-toolset-14), building R
4.6.2 with the options of §6.2.

**Static Fortran runtime: does not link.** `FLIBS` with
`-l:libgfortran.a -l:libquadmath.a` (the C driver that links R's
libraries ignores `-static-libgfortran`) links `libRblas.so` and
`libR.so`, then fails at `libRlapack.so`:
`libgfortran.a(async.o): relocation R_X86_64_TPOFF32 against symbol
'thread_unit' can not be used when making a shared object`. The
blocker is a local-exec TLS relocation, not one of the 13 absolute
relocations counted above. Failing run:
https://github.com/pedrobtz/py-zur/actions/runs/45291538897.

**Fallback build.** With the shared runtime, `configure`, `make -j4`
and `make install` take 147 to 189 s over two runs (CI profiles in the roadmap). Of the 18
ELF files installed, exactly five need `libgfortran.so.5` and
`libquadmath.so.0`: `libR.so`, `libRblas.so`, `libRlapack.so`,
`modules/lapack.so` and `library/stats/libs/stats.so`; none needs
`libgomp.so.1`. The image's libraries R links, for stage 4: ICU 60
(`libicuuc.so.60`), readline 7, PCRE2, liblzma, libbz2, zlib, libcurl
(`internet.so`), and cairo, freetype, libpng16, libjpeg 62 and libtiff 5
(`cairo.so`). Run:
https://github.com/pedrobtz/py-zur/actions/runs/45292200006.

**Pinned dependencies (stage 4).** OpenSSL 3.5.9 and libcurl 8.23.0
build in 124 to 219 s (five runs); a warm run restores them from the
cache (8 MB) in about 1 s. With them, R reports libcurl 8.23.0 over
OpenSSL/3.5.9, and the image's zlib 1.2.11, bzip2 1.0.6, xz 5.2.4,
PCRE2 10.32, ICU 60.3, readline 7.0 and cairo 1.15.12. R's files link
nothing outside `manifests/linux.toml` besides R's own libraries, the
Fortran runtime and glibc; fontconfig reaches R only through cairo.
The image sets `PKG_CONFIG_PATH=/usr/local/lib/pkgconfig`, which the
build now overrides (§6.1). Green run:
https://github.com/pedrobtz/py-zur/actions/runs/45301423657.

### 18.2 Measurements, 2026-11-24 (stage 8)

**`pak` on a zur-built R does not select P3M binaries.** The §6.6
recipe (`tests/recipes.py`), run in `ubuntu:24.04` and `rockylinux:8`
with no compiler and with `libgomp1`/`libgfortran5` (`libgomp`/
`libgfortran`) installed, in job `pak-linux-x86_64` of
https://github.com/pedrobtz/py-zur/actions/runs/45329772193:

- `pak` 0.11.2 installs from its own repository
  (`pak_0.11.2_R-4-6_x86_64-linux-gnu.tar.gz`).
- `pak::pkg_install("data.table")` then reports `Got data.table
  1.18.6.1 (source)` and `Building data.table 1.18.6.1`, and fails in
  the build for want of a compiler, in both containers.
- With `repos` as base R sets it (`CRAN = "@CRAN@"`), pak resolves CRAN
  to `https://cran.rstudio.com` (pkgcache's and pkgdepends'
  `default_cran_mirror()`), which serves source only: the URL pak used
  was `https://cran.rstudio.com/src/contrib/data.table_1.18.6.1.tar.gz`.
  R's user agent is the standard `R (4.6.2 x86_64-pc-linux-gnu x86_64
  linux-gnu)`; pak never asks P3M, so the user agent plays no part.
- pak detects the platform correctly (`x86_64-pc-linux-gnu-ubuntu-24.04`,
  `x86_64-pc-linux-gnu-rocky-8.10`) and `pak::ppm_has_binaries()` is
  `TRUE` in both. After `pak::repo_add(CRAN = "PPM@latest")`, CRAN
  becomes `https://packagemanager.posit.co/cran/__linux__/noble/latest`
  (Ubuntu) or `…/__linux__/centos8/latest` (Rocky), and `pkg_deps()`
  gives data.table with the binary platform of the container. P3M
  binaries are therefore reachable on a zur-built R, but only with a
  `repos` setting. D9 was restated on that basis (2026-11-24): the
  recipe of §6.6 makes that setting itself, and zur makes none.


**With `repo_add(CRAN = "PPM@latest")` (the recipe of §6.6, D9 as
restated).** In run
https://github.com/pedrobtz/py-zur/actions/runs/45331374753, with no
compiler in the container:

- Ubuntu 24.04 and Rocky Linux 8: `data.table` installs as a P3M binary
  and gives its known answer.
- Debian 12: pak reports `Got data.table 1.18.6.1 (source)` and fails
  with "Could not find tools necessary to compile a package". P3M lists
  bookworm with binaries, but its `4.6-bookworm` index (16,181 packages,
  against 25,629 for `4.6-noble`) has no `data.table`, `RcppArmadillo`
  or `xml2`. Its `4.5-bookworm` index has had no `data.table` since at
  least 2026-10-01; the 2026-06-01 snapshot had 1.18.4. Trixie's
  `4.6` index has `data.table`.
- On 2026-11-24 `4.6-trixie` held 25,538 packages, with `data.table`,
  `RcppArmadillo` and `xml2`; the pak job and the benchmark's slim case
  use Debian 13.

**Binary set (stage 8).** Chosen by reading `DT_NEEDED` of the P3M
binaries for R 4.6 on Ubuntu 24.04 (noble, 2026-11-24); known answers in
`BINARY_SET` of `tests/test_installed.py`:

| Package | Covers | `DT_NEEDED` beyond `libR.so` and glibc |
| --- | --- | --- |
| `jsonlite` 2.0.0 | plain C | none |
| `Rcpp` 1.1.2 | C++ | `libstdc++.so.6`, `libgcc_s.so.1` |
| `deSolve` 1.42 | Fortran | `libblas.so.3`, `libgfortran.so.5` |
| `data.table` 1.18.6.1 | OpenMP | `libgomp.so.1`, `libz.so.1` |
| `RcppArmadillo` 15.8.0-1 | BLAS and LAPACK | `liblapack.so.3`, `libblas.so.3`, `libgomp.so.1`, `libstdc++.so.6`, `libgcc_s.so.1` |

`RcppArmadillo` is the BLAS user: it needs both reference names and only
`Rcpp` besides. The others read were `robustbase` (both names; imports
`DEoptimR`), `quantreg` (both names; needs `Matrix`, `survival`, which
are recommended packages zur does not ship), `nloptr` and `xml2`
(neither name). Its process maps `libblas.so.3` and `liblapack.so.3`
from `R.home("lib")` (D16), and `data.table::getDTthreads()` is 2 on a
4-core runner with the host's `libgomp` (D17). As root on Ubuntu,
`pak::pkg_install("xml2")` installs `libxml2` with apt. Green run:
https://github.com/pedrobtz/py-zur/actions/runs/45332854896; pak job
39 to 57 s per container.

### 18.3 Measurements, 2026-11-24 (stage 10)

**C++ standards `Makeconf` offers.** As configure wrote it with
gcc-toolset-14 (g++ 14.2.1): `CXX = g++ -std=gnu++20` (R's default),
and `CXX17STD` to `CXX26STD` as `-std=gnu++17`, `-std=gnu++20`,
`-std=gnu++23` and `-std=gnu++26`; there is no `CXX11` or `CXX14`
entry. gcc 8's manual (8.5.0, C Dialect Options) lists C++ dialects up
to `gnu++17` and the experimental `gnu++2a` only; `gnu++20` first
appears in gcc 10's. So gcc 8 accepts only `gnu++17` of the four, and
rejected R's default: the decision on
[#11](https://github.com/pedrobtz/py-zur/issues/11) (§6.3) makes the
default per host.

**Host compilers of the matrix** (`compilers-linux-x86_64`, run
https://github.com/pedrobtz/py-zur/actions/runs/45349233117), each
probed with `g++ -std=<flag> -fsyntax-only` by the tests:

| Leg | g++ | `R CMD config CXX` | gnu++17 | gnu++20 | gnu++23 | gnu++26 |
| --- | --- | --- | --- | --- | --- | --- |
| `rockylinux:8` | 8.5.0 (Red Hat 8.5.0-31) | `g++ -std=gnu++17` | yes | no | no | no |
| `ubuntu:24.04` | 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) | `g++ -std=gnu++20` | yes | yes | yes | no |
| `fedora:45` | 16.2.1 (Red Hat 16.2.1-3) | `g++ -std=gnu++20` | yes | yes | yes | yes |

The gnu++23 column is from GCC's documentation (gcc 11 onward), not
probed; the others are the fixtures' probes. `fedora:45` was the
newest Fedora release on Docker Hub on 2026-11-24 (`latest`; `46` is
`rawhide`). The default-standard fixture reports `__cplusplus` 201703
on gcc 8 and 202002 on gcc 13 and 16; the C++26 fixture reports a value
above 202302 on gcc 16. `R CMD SHLIB` and `R CMD INSTALL` print the
resolved flag; R reads `Makeconf` as text only for `SHLIB_EXT`
(`tools:::.install_packages`, `.shlib_internal`) and in `javareconf`
for the Java lines, so nothing reads `CXX` without make. The MASS check
(`install.packages("MASS", type = "source")` from
`cloud.r-project.org`, MASS 7.3-66) compiles and gives `1/2` on Ubuntu
24.04.
The compiler legs take 45 to 54 s each; the wheel is unchanged in
size (59,307,417 bytes).
