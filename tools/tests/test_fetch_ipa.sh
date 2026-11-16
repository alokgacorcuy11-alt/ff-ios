#!/usr/bin/env bash
# Run the fetch_ipa.sh failure paths. Local only: uses file:// URLs, no network.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FETCH="$ROOT/tools/fetch_ipa.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
check() {
  if [ "$2" = "$3" ]; then
    printf '[ok  ] %s\n' "$1"
    pass=$((pass + 1))
  else
    printf '[FAIL] %s -> expected %s, got %s\n' "$1" "$3" "$2"
    fail=$((fail + 1))
  fi
}

for t in curl unzip od wc head tr mktemp; do
  if ! command -v "$t" >/dev/null 2>&1; then
    echo "missing required tool: $t"
    exit 2
  fi
done

# 1. a real IPA shape (a zip with Payload/*.app/Info.plist) and big enough
python3 - "$TMP/good.ipa" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as z:
    z.writestr("Payload/FreeFire.app/Info.plist", "<plist/>")
    z.writestr("Payload/FreeFire.app/FreeFire", b"\xcf\xfa\xed\xfe" + b"\x00" * (21 * 1024 * 1024))
PY
out=$("$FETCH" "file://$TMP/good.ipa" "$TMP/out.ipa" 2>&1); rc=$?
check "accepts a valid IPA" "$rc" "0"

# 2. an HTML error page - the failure that used to look like a success
printf '<!DOCTYPE html>\n<html><body>404 Not Found</body></html>\n' > "$TMP/page.html"
out=$("$FETCH" "file://$TMP/page.html" "$TMP/out2.ipa" 2>&1); rc=$?
check "refuses an error page" "$rc" "1"
case "$out" in *"too small"*|*"not a zip"*) check "says why it refused the page" yes yes ;;
  *) check "says why it refused the page" no yes ;; esac
check "leaves no bogus file behind" "$([ -e "$TMP/out2.ipa" ] && echo yes || echo no)" "no"

# 3. a zip, but not an IPA
python3 - "$TMP/notipa.ipa" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as z:
    z.writestr("readme.txt", "not an ipa")
    z.writestr("padding.bin", b"\x00" * (21 * 1024 * 1024))
PY
out=$("$FETCH" "file://$TMP/notipa.ipa" "$TMP/out3.ipa" 2>&1); rc=$?
check "refuses a zip that is not an IPA" "$rc" "1"
check "names the missing shape" "$(case "$out" in *Info.plist*) echo yes;; *) echo no;; esac)" "yes"

# 4. a URL that does not exist at all
out=$("$FETCH" "file://$TMP/absent.ipa" "$TMP/out4.ipa" 2>&1); rc=$?
check "refuses a missing URL" "$rc" "1"
check "mentions the URL" "$(case "$out" in *"download failed"*) echo yes;; *) echo no;; esac)" "yes"

# 5. no argument at all is a usage error, not a crash
out=$("$FETCH" 2>&1); rc=$?
check "usage error has its own exit code" "$rc" "2"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
