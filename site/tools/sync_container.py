#!/usr/bin/env python3
"""sync_container.py - put the container APK on the storefront's own server.

Same reasoning as sync_apk.py, and the same failure it prevents. A download that
goes through a redirect is saved as an HTML document by some of the download
managers Android browsers ship with, and the buyer then taps a page that never
installs. Serving it from one origin removes the redirect and the third party.

The checks are the ones worth running blind:

  * the zip is intact, every entry's CRC verified
  * it actually contains native code for the ABI it claims - a container with no
    arm64 libraries installs on an arm64 phone and then cannot start a guest
  * the sha256 is printed, so the phone can be compared against the file

  python site/tools/sync_container.py            # check and write
  python site/tools/sync_container.py --check     # check only
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"
DEST = PUBLIC / "container.apk"

URL = ("https://raw.githubusercontent.com/alokgacorcuy11-alt/ff-ios/"
       "container/BlackBox_4.7.1_arm64-v8a-debug.apk")

WANT_ABI = "arm64-v8a"

PASS = 0
FAIL = 0


def check(cond: bool, msg: str, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[ok  ] {msg} {extra}".rstrip())
    else:
        FAIL += 1
        print(f"[FAIL] {msg} {extra}".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify, change nothing")
    args = ap.parse_args()

    print("=" * 62)
    print(" storefront container apk")
    print("=" * 62)

    print(f"       fetching {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=180) as r:
            blob = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] could not fetch: {e}")
        return 1

    size = len(blob)
    digest = hashlib.sha256(blob).hexdigest()
    check(size > 1_000_000, "the download is a real apk, not an error page",
          f"{size:,} B")
    check(blob[:2] == b"PK", "and starts with a zip signature")

    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(blob))
        bad = z.testzip()
        check(bad is None, "every entry's CRC checks out", bad or f"{len(z.infolist())} entries")
    except Exception as e:  # noqa: BLE001
        check(False, "the zip opens", str(e))
        return 1

    names = z.namelist()
    so = [n for n in names if n.endswith(".so")]
    abi_so = [n for n in so if WANT_ABI in n]
    check(len(so) > 0, "it ships native code", f"{len(so)} shared objects")
    check(len(abi_so) > 0,
          f"native code for the ABI it claims ({WANT_ABI})",
          f"{len(abi_so)} of {len(so)}")

    dex = [n for n in names if n.endswith(".dex")]
    check(len(dex) > 0, "it ships dex", f"{len(dex)}")

    print()
    print(f"       size    {size:,} B")
    print(f"       sha256  {digest}")
    print()

    if args.check:
        print("       check only, nothing written")
        return 0 if FAIL == 0 else 1

    PUBLIC.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(blob)
    check(DEST.is_file(), "written to the deploy directory", str(DEST))
    print()
    print(f"  next: python site/tools/deploy.py --run")
    print()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
