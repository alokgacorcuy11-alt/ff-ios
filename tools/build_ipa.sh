#!/usr/bin/env bash
# build_ipa.sh - inject the dylib into a (decrypted) Free Fire IPA and re-sign it.
#
#   ./tools/build_ipa.sh <decrypted.ipa> <FFMenu.dylib> <cert.p12> <profile.mobileprovision>
#
# This is the piece that turns "official Free Fire" into "official Free Fire with
# a menu": the bundle identity stays Garena's, we only add a load command and a
# dylib, then sign with our own certificate.
#
# Requires: insert_dylib, ldid, zsign (or codesign on macOS), unzip/zip.
set -euo pipefail

IPA="${1:?usage: build_ipa.sh <ipa> <dylib> <cert.p12> <profile> [outdir]}"
DYLIB="${2:?dylib}"
CERT="${3:?cert.p12}"
PROV="${4:?profile.mobileprovision}"
OUTDIR="${5:-out}"
ENT="${ENTITLEMENTS:-native/entitlements.plist}"
CERTPASS="${CERTPASS:-}"

command -v insert_dylib >/dev/null || { echo "[!] insert_dylib not on PATH (github.com/Tyilo/insert_dylib)"; exit 1; }
command -v zip >/dev/null || { echo "[!] zip missing"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUTDIR"

echo "[1/7] unpack"
unzip -q "$IPA" -d "$WORK/in"
APP="$(find "$WORK/in/Payload" -maxdepth 1 -name '*.app' | head -1)"
[ -n "$APP" ] || { echo "[!] no .app inside Payload"; exit 1; }
BIN="$(defaults read "$APP/Info" CFBundleExecutable 2>/dev/null || \
       /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Info.plist")"
EXEC="$APP/$BIN"
BID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Info.plist")"
echo "    app : $APP"
echo "    bin : $BIN"
echo "    bid : $BID"

echo "[2/7] encryption check"
CRYPT="$(otool -l "$EXEC" | grep -A4 LC_ENCRYPTION_INFO | grep cryptid | awk '{print $2}' | head -1)"
if [ "${CRYPT:-0}" != "0" ]; then
  echo "[!] cryptid=$CRYPT -> the binary is still App Store encrypted."
  echo "    Decrypt first:  frida-ios-dump -H <device-ip> $BID   (jailbroken device)"
  echo "                    bagbak $BID"
  exit 1
fi
echo "    cryptid 0 - decryptable payload, good"

echo "[3/7] inject"
mkdir -p "$APP/Frameworks"
cp "$DYLIB" "$APP/Frameworks/libFFMenu.dylib"
install_name_tool -id "@executable_path/Frameworks/libFFMenu.dylib" "$APP/Frameworks/libFFMenu.dylib" || true
insert_dylib --inplace --overwrite --strip-codesig --codesign \
  "@executable_path/Frameworks/libFFMenu.dylib" "$EXEC"
otool -L "$EXEC" | grep -q libFFMenu && echo "    load command present"

echo "[4/7] sign nested"
find "$APP/Frameworks" -name '*.dylib' -o -name '*.framework' | while read -r f; do
  if [ -d "$f" ]; then
    name="$(basename "$f" .framework)"
    [ -f "$f/$name" ] && ldid -S "$f/$name" && echo "    signed $name"
  else
    ldid -S "$f" && echo "    signed $(basename "$f")"
  fi
done
find "$APP/PlugIns" -name '*.appex' 2>/dev/null | while read -r x; do
  b="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$x/Info.plist")"
  ldid -S "$x/$b" && echo "    signed $x"
done

echo "[5/7] sign main binary + entitlements"
# Preserve the app's ORIGINAL entitlements and only add what the injection
# needs. Replacing them wholesale is the fastest way to break login, keychain
# and any Team-ID-bound SDK - including attestation.
if command -v ldid >/dev/null; then
  ldid -e "$EXEC" > "$WORK/orig.entitlements" 2>/dev/null || true
  if [ -s "$WORK/orig.entitlements" ] && [ "${KEEP_ENTITLEMENTS:-1}" = "1" ]; then
    /usr/libexec/PlistBuddy -c 'Add :get-task-allow bool true' "$WORK/orig.entitlements" 2>/dev/null || \
      /usr/libexec/PlistBuddy -c 'Set :get-task-allow true'   "$WORK/orig.entitlements" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c 'Add :com.apple.security.network.client bool true' "$WORK/orig.entitlements" 2>/dev/null || true
    ENT="$WORK/orig.entitlements"
    echo "    merged original entitlements + get-task-allow"
    /usr/libexec/PlistBuddy -c 'Print' "$ENT" | head -20
  else
    echo "    no original entitlements readable, using $ENT"
  fi
fi
ldid -S"$ENT" "$EXEC"
echo "    entitlements applied from $ENT"

echo "[6/7] re-sign bundle"
PKG="$WORK/signed.ipa"
if command -v zsign >/dev/null; then
  zsign -k "$CERT" ${CERTPASS:+-p "$CERTPASS"} -m "$PROV" -o "$PKG" \
        -b "$BID" "$WORK/in/Payload" 2>/dev/null || \
  zsign -k "$CERT" ${CERTPASS:+-p "$CERTPASS"} -m "$PROV" -o "$PKG" "$IPA"
else
  echo "    [!] zsign not found, packing unsigned"
  ( cd "$WORK/in" && zip -qry "$PKG" Payload )
fi

echo "[7/7] done"
STAMP="$(date +%Y%m%d-%H%M%S)"
FINAL="$OUTDIR/${BID}-ffmenu-$STAMP.ipa"
cp "$PKG" "$FINAL"
echo
echo "  -> $FINAL"
echo "     $(du -h "$FINAL" | cut -f1)"
echo
echo "install:"
echo "  TrollStore        : open the .ipa on the device, Install"
echo "  AltStore/SideStore: 7-day resign loop, needs get-task-allow (it is in the plist)"
echo "  jailbroken        : unzip to /var/containers/Bundle/Application/... + uicache"