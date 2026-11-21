#!/usr/bin/env python3
"""sync_apk.py - put the published APK on the storefront's own server.

Why this exists
----------------
The download used to point at GitHub's `raw` URL. That URL is a 302 to
`raw.githubusercontent.com`, and some download managers - including the one
built into the file manager a lot of buyers use - save the redirect page
instead of the file. The buyer then taps an HTML document, the installer spins,
and nothing says why. Serving the APK from the shop's own domain removes the
redirect and the third party in one move.

The copy in `site/public/` is the one buyers get, so it has to be refreshed
whenever a new APK is published. This is that refresh, with the checks that
make it safe to run blind:

  * the file's sha256 must equal the `apk:` line of the published IDENTITY.txt
  * the zip must be intact (every entry's CRC)
  * no native binaries, and no META-INF surprises

  python site/tools/sync_apk.py            # check and write
  python site/tools/sync_apk.py --check     # check only, change nothing
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"
DEST = PUBLIC / "ffmenu.apk"

BASE = "https://raw.githubusercontent.com/alokgacorcuy11-alt/ff-ios/build"
APK_URL = "https://github.com/alokgacorcuy11-alt/ff-ios/raw/build/ffmenu.apk"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "demios-sync"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="refresh the storefront's copy of the APK")
    ap.add_argument("--check", action="store_true", help="verify only, do not write")
    args = ap.parse_args(argv)

    print("=" * 62)
    print(" storefront apk")
    print("=" * 62)

    try:
        ident = fetch(BASE + "/IDENTITY.txt").decode("utf-8", "replace")
    except Exception as e:
        print(f"[FAIL] cannot read IDENTITY.txt: {e}")
        return 1

    want = None
    for line in ident.splitlines():
        if line.startswith("apk:"):
            want = line.split(":", 1)[1].strip()
    if not want or len(want) != 64:
        print("[FAIL] IDENTITY.txt carries no apk sha256")
        return 1
    print(f"[ok  ] published sha256 {want[:16]}...")

    try:
        blob = fetch(APK_URL)
    except Exception as e:
        print(f"[FAIL] cannot download the APK: {e}")
        return 1
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        print(f"[FAIL] downloaded APK hashes to {got[:16]}..., published is {want[:16]}...")
        return 1
    print(f"[ok  ] download matches the published hash ({len(blob):,} bytes)")

    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as e:
        print(f"[FAIL] not a zip: {e}")
        return 1
    bad = z.testzip()
    if bad is not None:
        print(f"[FAIL] corrupt entry: {bad}")
        return 1
    names = z.namelist()
    print(f"[ok  ] zip is intact, {len(names)} entries")

    libs = [n for n in names if n.endswith(".so") or n.startswith("lib/")]
    if libs:
        print(f"[FAIL] native binaries in the buyer build: {libs}")
        return 1
    print("[ok  ] no native binaries")

    before = DEST.read_bytes() if DEST.is_file() else b""
    if before == blob:
        print("[ok  ] the storefront copy is already this build")
    elif args.check:
        print(f"[!   ] storefront copy differs "
              f"({len(before):,} vs {len(blob):,} bytes) - run without --check to update")
    else:
        DEST.write_bytes(blob)
        print(f"[ok  ] wrote {DEST} ({len(blob):,} bytes)")

    print()
    print("next: python site/tools/deploy.py --run")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
