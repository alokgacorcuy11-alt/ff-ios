#!/usr/bin/env bash
# fetch_ipa.sh - download the decrypted IPA and prove it really is one.
#
# Why this is a script and not four lines of curl in the workflow: a failed
# download that still writes a file is the most confusing failure in the whole
# pipeline. A 404 page, a login redirect or an expired asset URL all save
# happily, and the next step then fails with "not an IPA" and no hint why. So
# the download is validated here, with the reason.
#
# Usage:  tools/fetch_ipa.sh <url> [outfile]        (default: in.ipa)
#
# Private GitHub release assets: set IPA_TOKEN to a token that can read the
# repo. The plain /releases/download/ URL works with a Bearer token; the
# api.github.com/assets/<id> URL additionally needs Accept: application/octet-
# stream, which is sent automatically when a token is present.
#
#   IPA_TOKEN=$GITHUB_TOKEN tools/fetch_ipa.sh https://github.com/o/r/releases/download/v1/dec.ipa
#
set -euo pipefail

url="${1:-}"
out="${2:-in.ipa}"

if [ -z "$url" ]; then
  echo "usage: $0 <url> [outfile]" >&2
  exit 2
fi

# An IPA carrying Unity + assets is hundreds of MB. Anything under this is a
# proxy error page or a truncated transfer, not a payload.
MIN_BYTES=$((20 * 1024 * 1024))

auth=()
if [ -n "${IPA_TOKEN:-}" ]; then
  auth=(-H "Authorization: Bearer ${IPA_TOKEN}")
  # Only meaningful for the api.github.com asset endpoint; ignored elsewhere.
  auth+=(-H "Accept: application/octet-stream")
  echo "using an auth token"
fi

echo "fetching: $url"
echo "      to: $out"

rm -f "$out"

# --fail: a 4xx/5xx leaves no file at all, so a redirect to a login page cannot
# be mistaken for content. --location: release assets redirect to a CDN.
if ! curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 \
     "${auth[@]}" -o "$out" "$url"; then
  echo "ERROR: download failed (curl exit $?)." >&2
  echo "  * is the URL still valid? release assets can be replaced" >&2
  echo "  * private repo? set IPA_TOKEN to a token with read access" >&2
  rm -f "$out"
  exit 1
fi

if [ ! -s "$out" ]; then
  echo "ERROR: the download produced an empty file." >&2
  exit 1
fi

size=$(wc -c < "$out" | tr -d ' ')
echo "    size: $size bytes"

if [ "$size" -lt "$MIN_BYTES" ]; then
  echo "ERROR: $size bytes is far too small to be a Free Fire IPA." >&2
  echo "  A login page or an error body was saved instead of the payload." >&2
  echo "  First bytes: $(head -c 120 "$out" | tr -d '\0' | tr -c '[:print:]' '.')" >&2
  rm -f "$out"
  exit 1
fi

# A zip begins with PK\x03\x04 (or PK\x05\x06 for an empty archive).
magic=$(head -c 4 "$out" | od -An -tx1 | tr -d ' \n')
case "$magic" in
  504b0304*|504b0506*|504b0708*)
    echo "    magic: PK ($magic) - looks like a zip" ;;
  *)
    echo "ERROR: not a zip archive (magic $magic)." >&2
    echo "  An IPA is a zip. First bytes: $(head -c 120 "$out" | tr -d '\0' | tr -c '[:print:]' '.')" >&2
    rm -f "$out"
    exit 1 ;;
esac

# And the thing we actually need to be inside it.
if ! unzip -l "$out" 2>/dev/null | grep -qE 'Payload/[^/]+\.app/Info\.plist'; then
  echo "ERROR: the archive has no Payload/*.app/Info.plist - it is a zip, but not an IPA." >&2
  rm -f "$out"
  exit 1
fi
echo "    shape: Payload/*.app/Info.plist present"
echo "ok"
