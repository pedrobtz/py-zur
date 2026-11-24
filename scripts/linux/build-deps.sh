#!/usr/bin/env bash
# Build the "source" entries of manifests/linux.toml (roadmap stage 4,
# design §6.1) into $ZUR_DEPS (default /opt/zur-deps), in manifest order.
# Run in the image named by scripts/linux/image, from the repository
# root, after `build-r.sh deps` has installed the "image" entries:
#
#   uv run --frozen scripts/linux/build-deps.sh
#
# Every tarball is downloaded and checked against its SHA-256 before any
# is unpacked, so a wrong pin fails before anything compiles. The build
# tree is $ZUR_DEPS_WORK (default /tmp/zur-deps-build); ZUR_MANIFEST
# names another manifest and ZUR_PYTHON another interpreter (tests).
# Runs under macOS's bash 3.2 too, for tests/test_unit.py.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
py=${ZUR_PYTHON:-python3}
deps_py="$here/../deps.py"
deps=${ZUR_DEPS:-/opt/zur-deps}
work=${ZUR_DEPS_WORK:-/tmp/zur-deps-build}
jobs=$(getconf _NPROCESSORS_ONLN)

# OpenSSL: shared libraries only, found by curl through the RPATH.
# OPENSSLDIR is /etc/ssl, where Debian and Ubuntu keep certificates
# (RHEL links it to /etc/pki/tls); bin/R points SSL_CERT_FILE at the
# system bundle (design §6.4), so the directory only matters without it.
build_openssl() {
  ./Configure --prefix="$deps" --libdir=lib --openssldir=/etc/ssl \
    shared no-tests "-Wl,-rpath,$deps/lib"
  make -j"$jobs" build_sw
  make install_sw
}

# curl: libcurl with OpenSSL and the image's zlib, nothing else. No CA
# bundle path is compiled in: OpenSSL's default store is the fallback,
# and R passes CURL_CA_BUNDLE to libcurl when it is set (design §6.4).
build_curl() {
  PKG_CONFIG_PATH="$deps/lib/pkgconfig" LDFLAGS="-Wl,-rpath,$deps/lib" ./configure --prefix="$deps" \
    --with-openssl="$deps" --with-zlib \
    --enable-shared --disable-static \
    --without-libpsl --without-libidn2 --without-nghttp2 \
    --without-brotli --without-zstd --without-libssh2 --without-librtmp \
    --disable-ldap --disable-ldaps --disable-manual --disable-docs \
    --without-ca-bundle --without-ca-path --with-ca-fallback
  make -j"$jobs"
  make install
}

# fontconfig: 2.15 reads <dir prefix="relative">, which the wheel's
# fonts.conf needs (design §6.4); the image's 2.13.1 does not. Against the
# image's freetype and expat. Its compiled-in configuration directory is
# never read: bin/R sets FONTCONFIG_FILE. gperf (build-tools.txt) makes
# its object hash.
build_fontconfig() {
  PKG_CONFIG_PATH="$deps/lib/pkgconfig" ./configure --prefix="$deps" \
    --sysconfdir="$deps/etc" --localstatedir=/var \
    --enable-shared --disable-static --disable-docs --disable-cache-build \
    --disable-nls
  make -j"$jobs"
  make install
}

sources=$("$py" "$deps_py" sources)
mkdir -p "$work"

while read -r name url sha; do
  if ! declare -F "build_$name" >/dev/null; then
    echo "build-deps.sh: no recipe for $name" >&2
    exit 1
  fi
done <<<"$sources"

# Verify everything first: nothing below runs on an unchecked file.
while read -r name url sha; do
  "$py" "$deps_py" fetch "$url" "$sha" "$work/$(basename "$url")"
done <<<"$sources"

start=$SECONDS
while read -r name url sha; do
  file="$work/$(basename "$url")"
  top=$(tar -tf "$file" | awk -F/ 'NR == 1 { print $1 }')
  rm -rf "${work:?}/$top"
  tar -xf "$file" -C "$work"
  echo "== $name"
  (cd "$work/$top" && "build_$name")
done <<<"$sources"
echo "Dependency build time ($(echo $(cut -d' ' -f1 <<<"$sources"))): $((SECONDS - start)) s"
