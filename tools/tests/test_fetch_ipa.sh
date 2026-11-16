#!/usr/bin/env bash
# Run the fetch_ipa.sh failure paths. Local only: uses file:// URLs, no network.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FETCH="$ROOT/tools/fetch_ipa.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
LAST_OUT=""

# Annotate on CI: a failing run on a public repo cannot be read back through the
# API without a token, but check-run annotations can. Without this the only
# signal from a red build is "exit code 1".
annotate() {
  [ "${GITHUB_ACTIONS:-}" = "true" ] || return 0
  local msg="${1//%/%25}"
  msg="${msg//$'\n'/%0A}"
  echo "::error title=fetch_ipa::$msg"
}

# check <name> <got> <expected> [context]
check() {
  local name="$1" got="$2" want="$3" ctx="${4:-}"
  if [ "$got" = "$want" ]; then
    printf '[ok  ] %s\n' "$name"
    pass=$((pass + 1))
    return 0
  fi
  printf '[FAIL] %s -> expected %s, got %s\n' "$name" "$want" "$got"
  if [ -n "$ctx" ]; then
    printf '       context: %s\n' "$(printf '%s' "$ctx" | tail -12 | tr '\n' '|')"
  fi
  annotate "$name -> expected $want, got $got | ${ctx:0:600}"
  fail=$((fail + 1))
}

# run_fetch <url> [outfile] -> sets RC and LAST_OUT
run_fetch() {
  LAST_OUT="$("$FETCH" "$1" "${2:-$TMP/out.ipa}" 2>&1)"
  RC=$?
}

for t in curl od wc head tr mktemp python3; do
  if ! command -v "$t" >/dev/null 2>&1; then
    echo "missing required tool: $t"
    annotate "missing required tool: $t"
    exit 2
  fi
done

echo "bash   : $BASH_VERSION"
echo "curl   : $(curl --version | head -1)"
echo "python3: $(python3 -V 2>&1)"
echo

# 1. a real IPA shape (a zip with Payload/*.app/Info.plist) and big enough
python3 - "$TMP/good.ipa" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as z:
    z.writestr("Payload/FreeFire.app/Info.plist", "<plist/>")
    z.writestr("Payload/FreeFire.app/FreeFire", b"\xcf\xfa\xed\xfe" + b"\x00" * (21 * 1024 * 1024))
PY
echo "fixture size: $(wc -c < "$TMP/good.ipa") bytes (needs >= 20971520)"

run_fetch "file://$TMP/good.ipa" "$TMP/out.ipa"
check "accepts a valid IPA" "$RC" "0" "$LAST_OUT"

# 2. an HTML error page - the failure that used to look like a success
printf '<!DOCTYPE html>\n<html><body>404 Not Found</body></html>\n' > "$TMP/page.html"
run_fetch "file://$TMP/page.html" "$TMP/out2.ipa"
check "refuses an error page" "$RC" "1" "$LAST_OUT"
check "says why it refused the page" \
      "$(case "$LAST_OUT" in *"too small"*|*"not a zip"*) echo yes;; *) echo no;; esac)" "yes" "$LAST_OUT"
check "leaves no bogus file behind" "$([ -e "$TMP/out2.ipa" ] && echo yes || echo no)" "no"

# 3. a zip, but not an IPA
python3 - "$TMP/notipa.ipa" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as z:
    z.writestr("readme.txt", "not an ipa")
    z.writestr("padding.bin", b"\x00" * (21 * 1024 * 1024))
PY
run_fetch "file://$TMP/notipa.ipa" "$TMP/out3.ipa"
check "refuses a zip that is not an IPA" "$RC" "1" "$LAST_OUT"
check "names the missing shape" \
      "$(case "$LAST_OUT" in *Info.plist*) echo yes;; *) echo no;; esac)" "yes" "$LAST_OUT"

# 4. a URL that does not exist at all
run_fetch "file://$TMP/absent.ipa" "$TMP/out4.ipa"
check "refuses a missing URL" "$RC" "1" "$LAST_OUT"
check "mentions the download failure" \
      "$(case "$LAST_OUT" in *"download failed"*) echo yes;; *) echo no;; esac)" "yes" "$LAST_OUT"

# 5. no argument at all is a usage error, not a crash
LAST_OUT="$("$FETCH" 2>&1)"; RC=$?
check "usage error has its own exit code" "$RC" "2" "$LAST_OUT"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
