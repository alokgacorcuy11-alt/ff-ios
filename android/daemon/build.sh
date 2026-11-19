#!/usr/bin/env bash
# build.sh - cross-compile the root-side daemon for arm64 Android.
#
#   ANDROID_NDK_HOME=~/Android/Sdk/ndk/26.1.10909125 ./build.sh
#
# Output: ../app/app/src/main/assets/memd  (packed into the APK)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
API="${API:-26}"
OUT="$HERE/../app/app/src/main/assets"

if [ -z "$NDK" ]; then
  for c in "$HOME/Android/Sdk/ndk"/* "$HOME/Library/Android/sdk/ndk"/* \
           "/usr/local/lib/android/sdk/ndk"/*; do
    [ -d "$c" ] && NDK="$c" && break
  done
fi
[ -n "$NDK" ] || { echo "[!] no NDK: set ANDROID_NDK_HOME"; exit 1; }

HOST="linux-x86_64"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) HOST="darwin-x86_64" ;;   # the NDK ships x86_64 host tools, run under Rosetta
  Darwin-x86_64) HOST="darwin-x86_64" ;;
esac

CC="$NDK/toolchains/llvm/prebuilt/$HOST/bin/aarch64-linux-android$API-clang"
[ -x "$CC" ] || { echo "[!] compiler not found: $CC"; exit 1; }

mkdir -p "$OUT"

COMMON="-O2 -fPIE -pie -s -fno-asynchronous-unwind-tables -fno-unwind-tables -Wall -Wextra -Wno-unused-parameter"

# Prefer a static binary: nothing to resolve on the device. The NDK's static
# libc is complete but some NDK releases refuse -static with -pie, so fall back
# to bionic - the device always has libc.so, and the binary is only run there.
if ! "$CC" $COMMON -static -o "$OUT/memd" "$HERE/memd.c"; then
  echo "    static link refused, falling back to bionic"
  "$CC" $COMMON -o "$OUT/memd" "$HERE/memd.c"
fi

file "$OUT/memd" 2>/dev/null || true
echo "-> $OUT/memd ($(du -h "$OUT/memd" | cut -f1))"
