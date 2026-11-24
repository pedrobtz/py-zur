#!/usr/bin/env bash
# Build R from its source tarball inside the manylinux_2_28 image
# (design §6.1, §6.2). Run in the image named by scripts/linux/image,
# from the repository root:
#
#   scripts/linux/build-r.sh deps     # dnf install devel-packages.txt
#   scripts/linux/build-r.sh source   # fetch the tarball, check SHA-256
#   scripts/linux/build-r.sh build    # configure, make, make install
#
# or `all` for the three in order. R is installed into $ZUR_PREFIX
# (default /opt/zur); the build tree is $ZUR_WORK (default /tmp/zur-build).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=r-source
. "$here/r-source"
prefix=${ZUR_PREFIX:-/opt/zur}
work=${ZUR_WORK:-/tmp/zur-build}
tarball="$work/R-$R_VERSION.tar.gz"
src="$work/R-$R_VERSION"

deps() {
  mapfile -t pkgs < <(grep -v '^\s*\(#\|$\)' "$here/devel-packages.txt")
  dnf install -y "${pkgs[@]}"
  # D14: configure uses pango whenever it finds it.
  if rpm -q pango-devel >/dev/null; then
    echo "pango-devel is installed; R must be built without pango (D14)" >&2
    exit 1
  fi
}

source_() {
  mkdir -p "$work"
  curl -fsSL --retry 3 -o "$tarball" "$R_URL"
  # Checked before anything is unpacked or run.
  echo "$R_SHA256  $tarball" | sha256sum -c -
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
  # TEMPORARY (seen failing, reverted before merge): each line below
  # breaks one check of tests/test_prefix.py.
  echo 4.6.9 > VERSION            # test_version_string
  dnf install -y pango-devel      # test_cairo_without_pango
  # LIBnn=lib: R_HOME is $prefix/lib/R, as §6.3 names it, not lib64.
  LIBnn=lib ./configure --prefix="$prefix" \
    --enable-R-shlib --enable-memory-profiling \
    --without-x --without-tcltk --without-aqua \
    --without-recommended-packages \
    --with-cairo --with-libpng --with-jpeglib --without-libtiff \
    --with-readline --with-pcre2 --with-ICU \
    --with-internal-tzcode \
    --disable-java
  make -j"$(nproc)"
  make install
  rm -rf "$prefix/lib/R/library/splines"   # test_base_packages_and_no_recommended
  mv "$prefix/lib/R/lib/libRlapack.so" "$prefix/lib/R/lib/libRlapack.so.off"  # LAPACK tests
  echo "R build time (configure, make, make install): $((SECONDS - start)) s"
}

case "${1:-all}" in
  deps) deps ;;
  source) source_ ;;
  build) build ;;
  all) deps; source_; build ;;
  *) echo "usage: $0 [deps|source|build|all]" >&2; exit 2 ;;
esac
