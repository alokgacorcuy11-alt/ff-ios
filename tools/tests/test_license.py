#!/usr/bin/env python3
"""test_license.py - keys issued in Python, checked by the real Java class.

The key format exists twice: tools/keygen.py mints them, License.java verifies
them on the device. If those two ever disagree, every key a customer is sold is
dead, and nothing in either language would notice on its own.

So this compiles License.java, reads the secret out of the source, issues keys
with the Python tool, and feeds them to the Java verifier:

  * a fresh key verifies
  * one flipped character does not
  * an expired key does not, and says so
  * a device-bound key works on its device and not on another
  * an unbound key works anywhere
  * a key with a character outside the alphabet is refused, not crashed on

  python tools/tests/test_license.py
"""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LICENSE_SRC = (ROOT / "android" / "app" / "app" / "src" / "main"
               / "java" / "com" / "alokg" / "ffmenu" / "License.java")
HARNESS = HERE / "java" / "LicenseHarness.java"

sys.path.insert(0, str(ROOT / "tools"))
import keygen as kg  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def fmt(body: str) -> str:
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def main() -> int:
    print("=" * 66)
    print(" activation keys (python issue -> java verify)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    src = LICENSE_SRC.read_text(encoding="utf-8")
    m = re.search(r'SECRET\s*=\s*"([^"]+)"', src)
    if not m:
        print("[FAIL] no SECRET constant in License.java")
        return 1
    source_secret = m.group(1)

    tmp = Path(tempfile.mkdtemp(prefix="license-"))
    out = tmp / "classes"
    out.mkdir()

    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(LICENSE_SRC), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("License compiles with no Android imports", True)

    def run(*args: str) -> str:
        p = subprocess.run([java, "-cp", str(out), "LicenseHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip()

    embedded = run("secret")
    check("the harness reads the embedded secret", embedded == source_secret,
          embedded[:24] + "...")
    secret = embedded

    # --- a fresh key
    key = kg.issue(secret, days=30, tier=1, nonce=7)
    check("key is six groups of four", len(key) == 29 and key.count("-") == 5, key)
    out_line = run("verify", key, "")
    check("a fresh key verifies in java", out_line.startswith("OK"), out_line)

    # --- uniqueness: the nonce is what stops every buyer of a plan on one day
    #     from receiving the identical key
    a = kg.issue(secret, days=30, tier=1, nonce=1001)
    b = kg.issue(secret, days=30, tier=1, nonce=1002)
    check("two keys for the same plan on the same day differ", a != b, a + " / " + b)
    check("and both are accepted", run("verify", a, "").startswith("OK")
          and run("verify", b, "").startswith("OK"))
    again = kg.issue(secret, days=30, tier=1, nonce=1001)
    check("the same nonce reproduces the same key (so a reissue is possible)", again == a)

    # --- one flipped character
    body = key.replace("-", "")
    i = 6
    swapped = kg.ALPHABET[(kg.ALPHABET.index(body[i]) + 1) % 32]
    bad = fmt(body[:i] + swapped + body[i + 1:])
    out_line = run("verify", bad, "")
    check("a flipped character is refused", out_line.startswith("BAD"), out_line)
    check("and it says why", "signature" in out_line, out_line)

    # --- expiry. Payloads are built here rather than through kg.issue so the
    #     expiry can be set to a number java will disagree with, one byte at a
    #     time. 10 bytes now: version|tier, exp LE, device LE, nonce LE.
    days_since = (kg.date.today() - kg.EPOCH).days

    def raw(exp: int, version: int = kg.VERSION, tier: int = 1) -> str:
        payload = bytes([((version & 0xF) << 4) | (tier & 0xF), exp & 0xFF, (exp >> 8) & 0xFF,
                         0, 0, 0, 0, 0, 0, 0])
        mac = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:5]
        return fmt(kg.b32_encode(payload, kg.PAYLOAD_CHARS) + kg.b32_encode(mac, 8))

    out_line = run("verify", raw(days_since + 5), "")
    check("a key 5 days from expiry verifies", out_line.startswith("OK"), out_line)

    out_line = run("verify", raw(0), "")
    check("expiry 0 means never expires", out_line.startswith("OK"), out_line)

    expired = raw(max(1, days_since - 3))
    out_line = run("verify", expired, "")
    check("a key that expired 3 days ago is refused", out_line.startswith("BAD"), out_line)
    check("and it names the expiry", "expired" in out_line, out_line)

    # --- an old-version key must not be accepted by a new build
    old = raw(days_since + 5, version=1)
    out_line = run("verify", old, "")
    check("a version-1 key is refused with the version named",
          out_line.startswith("BAD") and "version" in out_line, out_line)

    # --- device binding
    device_a, device_b = "a1b2c3d4e5f6a7b8", "99887766ffeeddcc"
    py_hash = kg.device_hash(secret, device_a)
    java_hash = int(run("device", device_a)) & 0xFFFFFFFF
    check("device hash agrees across languages", py_hash == java_hash,
          f"py={py_hash} java={java_hash}")

    bound = kg.issue(secret, days=30, tier=2, device_id=device_a)
    check("a bound key works on its device", run("verify", bound, device_a).startswith("OK"),
          run("verify", bound, device_a))
    out_line = run("verify", bound, device_b)
    check("a bound key is refused elsewhere", out_line.startswith("BAD"), out_line)
    check("and it names the reason", "another device" in out_line, out_line)
    check("a bound key with no device id is refused",
          run("verify", bound, "").startswith("BAD"), run("verify", bound, ""))

    any_key = kg.issue(secret, days=30, tier=1)
    check("an unbound key works with no device id", run("verify", any_key, "").startswith("OK"))
    check("an unbound key works on any device", run("verify", any_key, device_b).startswith("OK"))

    # --- malformed input
    check("a short key is refused", run("verify", "ABCD-EFGH", "").startswith("BAD"),
          run("verify", "ABCD-EFGH", ""))
    check("a key with a bad character is refused",
          run("verify", "0OOO-" + key[5:], "").startswith("BAD"),
          run("verify", "0OOO-" + key[5:], ""))
    check("lowercase and no dashes still work",
          run("verify", key.replace("-", "").lower(), "").startswith("OK"))
    check("an empty key is refused", run("verify", "", "").startswith("BAD"))

    # --- tier and the remaining days are reported
    out_line = run("verify", kg.issue(secret, days=10, tier=3), "")
    check("tier reaches the app", out_line.split()[1] == "3", out_line)
    check("days remaining is in the ballpark", 9 <= int(out_line.split()[2]) <= 10, out_line)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())