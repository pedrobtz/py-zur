#!/usr/bin/env bash
# Build R from its source tarball inside the manylinux_2_28 image
# (design §6.1, §6.2). Run in the image named by scripts/linux/image,
# from the repository root:
#
#   uv run --frozen scripts/linux/build-r.sh deps     # pinned dnf packages
#   uv run --frozen scripts/linux/build-r.sh source   # fetch, check SHA-256
#   scripts/linux/build-r.sh build    # configure, make, make install
#
# or `all` for the three in order; `deps` and `source` need Python
# (scripts/deps.py). Between `deps` and `build`, build-deps.sh builds the
# manifest's "source" entries into $ZUR_DEPS (default /opt/zur-deps),
# which R is configured against. R is installed into $ZUR_PREFIX
# (default /opt/zur); the build tree is $ZUR_WORK (default
# /tmp/zur-build). ZUR_R_SOURCE names another r-source file and
# ZUR_PYTHON another interpreter (tests).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=r-source
. "${ZUR_R_SOURCE:-$here/r-source}"
py=${ZUR_PYTHON:-python3}
deps_py="$here/../deps.py"
prefix=${ZUR_PREFIX:-/opt/zur}
deps=${ZUR_DEPS:-/opt/zur-deps}
work=${ZUR_WORK:-/tmp/zur-build}
tarball="$work/R-$R_VERSION.tar.gz"
src="$work/R-$R_VERSION"

# The manifest's "image" entries at their pinned versions, and the build
# tools (build-tools.txt). rpm-check then refuses any other version.
deps() {
  pinned=$("$py" "$deps_py" dnf)
  mapfile -t tools < <(grep -v '^\s*\(#\|$\)' "$here/build-tools.txt")
  # shellcheck disable=SC2086 # one package spec per word
  dnf install -y "${tools[@]}" $pinned
  "$py" "$deps_py" rpm-check
  # D14: configure uses pango whenever it finds it.
  if rpm -q pango-devel >/dev/null; then
    echo "pango-devel is installed; R must be built without pango (D14)" >&2
    exit 1
  fi
}

source_() {
  # Checked before anything is unpacked or run, by the same code as the
  # manifest's sources (scripts/deps.py fetch).
  "$py" "$deps_py" fetch "$R_URL" "$R_SHA256" "$tarball"
  rm -rf "$src"
  tar -xzf "$tarball" -C "$work"
}

# Fortran runtime (D17): R's libraries link the shared libgfortran.so.5
# and libquadmath.so.0 that configure finds. The static link was tried
# first and fails: the toolset's libgfortran.a(async.o) carries a
# local-exec TLS relocation (R_X86_64_TPOFF32) that cannot go into
# libRlapack.so (design §18, 2026-11-24). The fallback, the runtime
# grafted under a hashed name for R's own libraries, is stage 5's
# repair step.

build() {
  cd "$src"
  echo "gfortran: $(gfortran --version | head -1)"
  start=$SECONDS
  # LIBnn=lib: R_HOME is $prefix/lib/R, as §6.3 names it, not lib64.
  # CPPFLAGS and LDFLAGS replace configure's default -I/usr/local/include
  # and -L/usr/local/lib: the image keeps its own builds there, which are
  # no pinned input (stage 4). The -L directory also becomes part of
  # R_LD_LIBRARY_PATH in etc/ldpaths, so R finds the libcurl built there;
  # curl-config on PATH is that libcurl's. PKG_CONFIG_PATH replaces the
  # image's /usr/local/lib/pkgconfig for the same reason; pkg-config still
  # searches the system's own directories.
  PATH="$deps/bin:$PATH" PKG_CONFIG_PATH="$deps/lib/pkgconfig" \
    CPPFLAGS="-I$deps/include -I/usr/local/include" LDFLAGS="-L$deps/lib" \
    LIBnn=lib ./configure --prefix="$prefix" \
    --enable-R-shlib --enable-memory-profiling \
    --without-x --without-tcltk --without-aqua \
    --without-recommended-packages \
    --with-cairo --with-libpng --with-jpeglib --with-libtiff \
    --with-readline --with-pcre2 --with-ICU \
    --with-internal-tzcode \
    --disable-openmp \
    --disable-java
  make -j"$(nproc)"
  make install
  echo "R build time (configure, make, make install): $((SECONDS - start)) s"
}

case "${1:-all}" in
  deps) deps ;;
  source) source_ ;;
  build) build ;;
  all) deps; source_; "$here/build-deps.sh"; build ;;
  *) echo "usage: $0 [deps|source|build|all]" >&2; exit 2 ;;
esac
