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

# R links its shared libraries with the C driver, which ignores
# -static-libgfortran, and passes the Fortran runtime as FLIBS
# (-lgfortran ... -lquadmath). Naming the archives instead links the
# toolset's static libgfortran and libquadmath into libRblas.so,
# libRlapack.so, libR.so and stats.so (D17). The override is given to
# make only, so the installed etc/Makeconf keeps configure's FLIBS for
# packages, which use the machine's shared runtime (§6.3).
static_flibs() {
  sed -n 's/^FLIBS_IN_SO = //p' "$src/Makeconf" |
    sed -e 's/-lgfortran\b/-l:libgfortran.a/' -e 's/-lquadmath\b/-l:libquadmath.a/'
}

build() {
  cd "$src"
  echo "gfortran: $(gfortran --version | head -1)"
  echo "libgfortran.a: $(gfortran -print-file-name=libgfortran.a)"
  echo "libquadmath.a: $(gfortran -print-file-name=libquadmath.a)"
  start=$SECONDS
  # LIBnn=lib: R_HOME is $prefix/lib/R, as §6.3 names it, not lib64.
  LIBnn=lib ./configure --prefix="$prefix" \
    --enable-R-shlib --enable-memory-profiling \
    --without-x --without-tcltk --without-aqua \
    --without-recommended-packages \
    --with-cairo --with-libpng --with-jpeglib --with-libtiff \
    --with-readline --with-pcre2 --with-ICU \
    --with-internal-tzcode \
    --disable-openmp \
    --disable-java
  flibs=$(static_flibs)
  case "$flibs" in
    *-l:libgfortran.a*) ;;
    *) echo "FLIBS has no -lgfortran to make static: $flibs" >&2; exit 1 ;;
  esac
  echo "FLIBS for R's own libraries: $flibs"
  make -j"$(nproc)" FLIBS="$flibs" FLIBS_IN_SO="$flibs"
  make install FLIBS="$flibs" FLIBS_IN_SO="$flibs"
  echo "R build time (configure, make, make install): $((SECONDS - start)) s"
}

case "${1:-all}" in
  deps) deps ;;
  source) source_ ;;
  build) build ;;
  all) deps; source_; build ;;
  *) echo "usage: $0 [deps|source|build|all]" >&2; exit 2 ;;
esac
