#!/usr/bin/env python3
"""test_site_qris.py - the static -> dynamic QRIS builder.

The thing that must not break: the buyer scans the code, the wallet shows the
exact rupiah amount, and the money reaches the same merchant account as the
seller's static code. Every assertion below is one of those.

The fixture is built by tools/qris_probe.mjs with a local TLV assembler that
does not import the module under test, and the CRC is pinned against the
canonical CRC-16/CCITT-FALSE check value ("123456789" -> 0x29B1).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def check(cond: bool, msg: str) -> None:
    (ok if cond else bad)(msg)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("[skip] node not on PATH")
        return 0

    r = subprocess.run([node, "tools/qris_probe.mjs"], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        bad("qris_probe.mjs failed: " + (r.stderr or "").strip()[-500:])
        print(f"\nqris: {PASS} ok, {FAIL} failed")
        return 1

    d = json.loads(r.stdout)

    check(d["crcCheck"]["got"] == "29B1",
          f"crc16 check value 123456789 -> {d['crcCheck']['got']} (want 29B1)")

    check(d["staticValid"], "static fixture passes its own CRC")
    check(d["staticIsQris"], "static fixture is recognised as a QRIS code")
    check(d["staticAmount"] == 0, "static code carries no amount")
    check(d["staticPmi"] == "11", "static code is point-of-initiation 11")

    check(d["dynamicValid"], "dynamic code passes its own CRC")
    check(d["dynamicIsQris"], "dynamic code is still a QRIS code")
    check(d["dynamicAmount"] == 140000,
          f"amount reads back as {d['dynamicAmount']} (want 140000)")
    check(d["dynamicPmi"] == "12", "dynamic code is point-of-initiation 12")

    check(d["amountOnce"] == 1, "exactly one tag 54 in the dynamic code")
    check(d["amountAfter53"], "tag 54 sits after currency (53)")
    check(d["amountLastRoot"],
          "tag 54 is the last root tag, right before the CRC (tags: " + " ".join(d["tags"]) + ")")
    check(d["merchantSame"], "merchant account, MCC, name, city are byte-identical")
    check(d["lengthsOk"], "parse -> build reproduces both payloads exactly")

    check(d["againValid"] and d["againAmount"] == 300000 and d["againCount"] == 1,
          "re-applying an amount replaces it, does not append a second one")

    for row in d["rejects"]:
        check(row["threw"], f"refuses malformed payload {row['bad'][:24]!r}")

    for row in d["badAmounts"]:
        check(row["threw"], f"refuses amount {row['amount']}")

    check(d["sweep"]["distinct"] == 6 and d["sweep"]["allValid"],
          "six amounts -> six distinct, valid payloads")

    # ---- recovering the merchant template ---------------------------------
    t = d["template"]
    check(t["tplIsStatic"], "the recovered template is point-of-initiation 11")
    check(t["tplHasNoAmount"], "and carries no tag 54")
    check(t["tplValid"] and t["tplIsQris"], "the recovered template is a valid QRIS code")
    check(t["tplEqualsStatic"], "recovering the template returns the original static payload")
    check(t["identical"],
          "putting the amount back reproduces the dynamic payload byte for byte, CRC included")

    # ---- the acquirer's two-decimal form ----------------------------------
    check(d["decimals"]["two"] == "140000.00",
          f"decimals:2 writes {d['decimals']['two']} (the acquirer's own format)")
    check(d["decimals"]["zero"] == "140000", "and the default writes whole rupiah")
    check(d["decimals"]["amountOfTwo"] == 140000, "both read back as the same amount")

    # the dynamic payload must be small enough to fit a QR at level M
    check(len(d["dynamic"]) <= 666,
          f"dynamic payload is {len(d['dynamic'])} bytes (fits v20-M, 666)")

    print()
    print(f"qris: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
