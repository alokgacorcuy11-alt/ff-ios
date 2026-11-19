#!/usr/bin/env bash
# build_ipa.sh - package + sign in one step.
#
# Injection is deliberately NOT duplicated here. tools/package_ipa.py is the
# tested path: cryptid gate, framework camouflage, LC_LOAD_DYLIB injected into
# header padding, signature strip, load-command verification. This script only
# adds the part Python cannot do - zsign with your certificate.
#
#   ./tools/build_ipa.sh <decrypted.ipa> <FFMenu.dylib> <cert.p12> <profile> [outdir]
#
# Environment:
#   CERTPASS=...      p12 password
#   ENTITLEMENTS=...  plist from tools/make_sideload_entitlements.py.
#                     Leave it unset and zsign derives them from the profile,
#                     which is always correct. Do not hand-write one.
#   BUNDLE_ID=...     rewrite the bundle id (required for a free Apple ID:
#                     com.dts.freefireth is Garena's registered App ID)
#   NAME=...          framework name for the camouflage (default UnityServices)
#
# Requires: python3, zsign.
set -euo pipefail

IPA="${1:?usage: build_ipa.sh <ipa> <dylib> <cert.p12> <profile> [outdir]}"
DYLIB="${2:?dylib}"
CERT="${3:?cert.p12}"
PROV="${4:?profile.mobileprovision}"
OUTDIR="${5:-out}"
CERTPASS="${CERTPASS:-}"
NAME="${NAME:-UnityServices}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null || { echo "[!] python3 not on PATH"; exit 1; }
[ -f "$IPA" ]   || { echo "[!] no such IPA: $IPA"; exit 1; }
[ -f "$DYLIB" ] || { echo "[!] no such dylib: $DYLIB"; exit 1; }
[ -f "$PROV" ]  || { echo "[!] no such profile: $PROV"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUTDIR"

echo "[1/3] package (python)"
PKG_ARGS=(-o "$WORK/pkg" --name "$NAME")
[ -n "${BUNDLE_ID:-}" ] && PKG_ARGS+=(--bundle-id "$BUNDLE_ID")
"$PY" "$HERE/tools/package_ipa.py" "$IPA" "$DYLIB" "${PKG_ARGS[@]}"
PAYLOAD="$(find "$WORK/pkg" -name '*-payload.ipa' | head -1)"
[ -n "$PAYLOAD" ] || { echo "[!] packager produced no payload"; exit 1; }
echo "    payload: $PAYLOAD"

echo "[2/3] sign"
if ! command -v zsign >/dev/null; then
  echo "[!] zsign not on PATH - the payload above is ready but unsigned."
  echo "    Either install zsign, or import it into Sideloadly / ESign / Father"
  echo "    and sign there (see native/INSTALL.md section 2b)."
  cp "$PAYLOAD" "$OUTDIR/$(basename "$PAYLOAD")"
  echo "  -> $OUTDIR/$(basename "$PAYLOAD")"
  exit 0
fi

SIGN_ARGS=(-k "$CERT" -m "$PROV" -o "$WORK/signed.ipa")
[ -n "$CERTPASS" ] && SIGN_ARGS+=(-p "$CERTPASS")
# Only pass entitlements if they were generated from THIS profile. An
# unset ENTITLEMENTS is the safe default: zsign reads the profile.
[ -n "${ENTITLEMENTS:-}" ] && SIGN_ARGS+=(-e "$ENTITLEMENTS")
zsign "${SIGN_ARGS[@]}" "$PAYLOAD"

echo "[3/3] verify + publish"
"$PY" "$HERE/tools/ipa_probe.py" --quiet "$WORK/signed.ipa" || {
  echo "[!] the signed IPA does not probe clean - not publishing it"; exit 1; }
STAMP="$(date +%Y%m%d-%H%M%S)"
BID="$(basename "$PAYLOAD" -payload.ipa)"
FINAL="$OUTDIR/${BID}-ffmenu-$STAMP.ipa"
cp "$WORK/signed.ipa" "$FINAL"
echo
echo "  -> $FINAL  ($(du -h "$FINAL" | cut -f1))"
echo
echo "install:"
echo "  any signer  : import the payload IPA, or use the signed file above"
echo "  TrollStore  : needs no cert - install the payload IPA as-is"
echo "  free Apple ID: 7 days per signature, Developer Mode must be ON (iOS 16+)"
echo "  jailbroken  : unzip to /var/containers/Bundle/Application/... + uicache"
