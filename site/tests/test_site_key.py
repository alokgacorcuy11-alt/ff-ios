#!/usr/bin/env python3
"""test_site_key.py - keys minted by the site, checked by the real Java class.

The site is now the thing that mints keys. If its arithmetic drifts from
License.java by one day, or the device hash is written big-endian instead of
little, every key sold is dead and nothing in either language notices.

So this does what tools/tests/test_license.py does for keygen.py, but for
site/functions/_lib/key.js:

  * the secret is read out of the compiled License class, not hard-coded
  * node mints, java verifies - unbound, bound, expiring, never-expiring
  * node and python keygen produce the identical key for the same day
  * node and java agree on the device hash
  * node's verify() reaches the same verdict as java's
  * a flipped character, a wrong device and an expired key are all refused

  python site/tests/test_site_key.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent
ROOT = SITE.parent

LICENSE_SRC = (ROOT / "android" / "app" / "app" / "src" / "main"
               / "java" / "com" / "alokg" / "ffmenu" / "License.java")
HARNESS = ROOT / "tools" / "tests" / "java" / "LicenseHarness.java"
EPOCH_MS = 1767225600000

sys.path.insert(0, str(ROOT / "tools"))
import keygen as kg  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def skip(msg: str) -> None:
    global SKIP
    SKIP += 1
    print(f"[skip] {msg}")


def check(cond: bool, msg: str, extra: str = "") -> None:
    (ok if cond else bad)(f"{msg}{('  ' + extra) if extra else ''}")


def node(*args: str) -> dict:
    r = subprocess.run(["node", "tools/mint.mjs", *args], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or "").strip()
    if not out:
        return {"ok": False, "error": (r.stderr or "").strip()[-300:]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": out[-300:]}


def py_issue(secret: str, days: int, tier: int, device_id: str, today: date) -> str:
    """keygen.py's arithmetic with the day pinned, so it is comparable."""
    dev = kg.device_hash(secret, device_id)
    payload = kg.payload_bytes(kg.VERSION, tier, days, dev, today=today)
    mac = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:5]
    body = kg.b32_encode(payload, 12) + kg.b32_encode(mac, 8)
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def fmt(body: str) -> str:
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def main() -> int:
    if shutil.which("node") is None:
        skip("node not on PATH - cannot exercise the site minter")
        return 0
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        skip("no jdk on PATH - cannot check against License.java")
        return 0

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sitekey-"))
    classes = tmp / "classes"
    classes.mkdir()
    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(classes), str(LICENSE_SRC), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                bad(ln)
        return 1

    def jrun(*args: str) -> str:
        p = subprocess.run([java, "-cp", str(classes), "LicenseHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip()

    secret = jrun("secret")
    if not secret:
        bad("the harness printed no secret")
        return 1
    ok(f"secret read from the compiled License class ({len(secret)} chars)")

    # ---- 1. the day number the site computes is the one java computes -----
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    minted = node("mint", "--secret", secret, "--days", "30", "--tier", "1")
    check(minted.get("ok"), "node mints a key", str(minted.get("error", "")))
    key = minted["key"]
    check(len(key) == 24 and key.count("-") == 4, "key is five groups of four", key)

    want_day = (now_ms - EPOCH_MS) // 86400000
    check(abs(minted["day"] - want_day) <= 1,
          f"day number {minted['day']} matches utc day {want_day}")

    # ---- 2. java accepts what node mints ----------------------------------
    out = jrun("verify", key, "")
    check(out.startswith("OK"), "java accepts an unbound key from the site", out)
    check(out.split()[1] == "1", "tier reaches the app", out)
    left = int(out.split()[2])
    check(28 <= left <= 30, f"days remaining reported as {left} (want ~30)")

    # ---- 3. exactness against keygen.py -----------------------------------
    today = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
    mismatches = []
    for days, tier, dev in ((30, 1, ""), (7, 2, "a1b2c3d4e5f6a7b8"),
                            (0, 1, ""), (1, 3, "ZZZZZZZZZZZZZZZZ"),
                            (365, 2, "99887766ffeeddcc")):
        want = py_issue(secret, days, tier, dev, today)
        args = ["mint", "--secret", secret, "--days", str(days), "--tier", str(tier)]
        if dev:
            args += ["--device", dev]
        got = node(*args).get("key")
        if got != want:
            mismatches.append((days, tier, dev, want, got))
    check(not mismatches, "node and keygen.py mint identical keys for 5 shapes",
          "" if not mismatches else str(mismatches[:2]))

    # ---- 4. device binding -------------------------------------------------
    dev_a, dev_b = "a1b2c3d4e5f6a7b8", "99887766ffeeddcc"
    py_hash = kg.device_hash(secret, dev_a)
    java_hash = int(jrun("device", dev_a)) & 0xFFFFFFFF
    node_hash = node("mint", "--secret", secret, "--days", "30", "--device", dev_a)["deviceHash"]
    check(py_hash == java_hash == node_hash,
          "device hash agrees across python, java and node",
          f"py={py_hash} java={java_hash} node={node_hash}")

    bound = node("mint", "--secret", secret, "--days", "7", "--tier", "2",
                 "--device", dev_a)["key"]
    check(jrun("verify", bound, dev_a).startswith("OK"),
          "a bound key works on its device", jrun("verify", bound, dev_a))
    out = jrun("verify", bound, dev_b)
    check(out.startswith("BAD") and "another device" in out,
          "a bound key is refused on another device", out)
    out = jrun("verify", bound, "")
    check(out.startswith("BAD") and "bound" in out,
          "a bound key with no device id is refused", out)

    # ---- 5. expiry ---------------------------------------------------------
    never = node("mint", "--secret", secret, "--days", "0", "--tier", "1")["key"]
    check(jrun("verify", never, "").startswith("OK"), "days=0 means never expires")

    past = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - 40 * 86400000
    dead = node("mint", "--secret", secret, "--days", "5", "--now", str(past))["key"]
    out = jrun("verify", dead, "")
    check(out.startswith("BAD") and "expired" in out,
          "a key minted 40 days ago for 5 days is refused", out)

    # ---- 6. tampering ------------------------------------------------------
    body = key.replace("-", "")
    i = 6
    swapped = kg.ALPHABET[(kg.ALPHABET.index(body[i]) + 1) % 32]
    tampered = fmt(body[:i] + swapped + body[i + 1:])
    out = jrun("verify", tampered, "")
    check(out.startswith("BAD") and "signature" in out,
          "one flipped character is refused", out)

    # ---- 7. node's verify agrees with java's ------------------------------
    verdicts = [
        (key, "", True),
        (bound, dev_a, True),
        (bound, dev_b, False),
        (tampered, "", False),
        (never, "", True),
        ("ABCD-EFGH", "", False),
        ("0OOO-" + key[5:], "", False),
        (key.replace("-", "").lower(), "", True),
    ]
    agree = 0
    for k, dev, should in verdicts:
        n = node("verify", "--secret", secret, "--key", k, "--device", dev)
        j = jrun("verify", k, dev)
        if bool(n.get("ok")) == should and j.startswith("OK") == should:
            agree += 1
        else:
            bad(f"verdict disagreement on {k[:12]}... device={dev!r}: "
                f"node={n.get('ok')} java={j[:40]} want={should}")
    check(agree == len(verdicts),
          f"node and java reach the same verdict on {agree}/{len(verdicts)} keys")

    # ---- 8. the admin fingerprint is stable and short ---------------------
    a = node("mint", "--secret", secret, "--days", "30")["fingerprint"]
    b = node("mint", "--secret", secret, "--days", "30")["fingerprint"]
    check(len(a) == 8 and a == b, "fingerprint is 8 hex chars and deterministic", a)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(f"site keys: {PASS} ok, {FAIL} failed, {SKIP} skipped")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
