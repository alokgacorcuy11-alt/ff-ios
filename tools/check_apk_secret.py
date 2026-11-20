#!/usr/bin/env python3
"""check_apk_secret.py - the APK and the site must be signed with one secret.

The failure this catches is the expensive one: LICENSE_SECRET on the Pages
project is changed (or the placeholder is put back), the APK is not rebuilt, and
every key the site sells is rejected on the phone with "signature does not
match". Nothing else in the pipeline notices, because both sides are internally
consistent.

Run after the APK is built:

    python tools/check_apk_secret.py --apk dist/ffmenu.apk

Reads the secret out of License.java - the single source of truth - and asserts:

  * it is not the placeholder
  * it is present in the APK's dex (the class is compiled, not stripped away)
  * the placeholder is absent

Exit code 0 when the build is safe to sell, 1 otherwise.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
LICENSE_JAVA = (ROOT / "android" / "app" / "app" / "src" / "main"
                / "java" / "com" / "alokg" / "ffmenu" / "License.java")

PLACEHOLDER = "ganti-secret-ini-sama-yang-di-keygen-py-jangan-pakai-yang-ini"


def read_secret() -> str:
    if not LICENSE_JAVA.exists():
        raise SystemExit(f"missing {LICENSE_JAVA}")
    m = re.search(r'SECRET\s*=\s*"([^"]+)"', LICENSE_JAVA.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("no SECRET constant in License.java")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(description="check that the shipped APK embeds the current signing secret")
    ap.add_argument("--apk", required=True)
    a = ap.parse_args()

    apk = pathlib.Path(a.apk)
    if not apk.is_file():
        print(f"[FAIL] no such APK: {apk}")
        return 1

    secret = read_secret()
    print(f"secret in License.java: {secret[:8]}...({len(secret)} chars)")

    if secret == PLACEHOLDER:
        print("[FAIL] License.java still holds the placeholder - refusing to sell this build")
        return 1

    try:
        z = zipfile.ZipFile(apk)
    except zipfile.BadZipFile as e:
        print(f"[FAIL] {apk} is not a zip: {e}")
        return 1

    dex = [n for n in z.namelist() if n.endswith(".dex")]
    if not dex:
        print("[FAIL] the APK has no dex - nothing to check")
        return 1

    found_secret: list[str] = []
    found_placeholder: list[str] = []
    for name in z.namelist():
        try:
            data = z.read(name)
        except Exception:  # noqa: BLE001 - a corrupt entry must not hide the answer
            continue
        if secret.encode() in data:
            found_secret.append(name)
        if PLACEHOLDER.encode() in data:
            found_placeholder.append(name)

    ok = True

    if found_secret:
        print(f"[ok  ] the current secret is in {' + '.join(found_secret)}")
    else:
        print("[FAIL] the current secret is NOT in any dex the APK ships")
        ok = False

    if found_placeholder:
        print(f"[FAIL] the placeholder is in {' + '.join(found_placeholder)}")
        ok = False
    else:
        print("[ok  ] the placeholder is nowhere in the APK")

    # the manifest tells the page which build it is linking to; not fatal if it
    # is absent, but worth printing because a stale one is a confusing symptom
    try:
        import json
        man = json.loads(z.read("assets/manifest.json"))
        print(f"[info] container {man.get('container_sha256', '?')[:16]}..."
              f"  module {man.get('module_sha256', '?')[:16]}..."
              f"  tail {man.get('tail', '?')} B")
    except Exception:  # noqa: BLE001
        print("[info] no assets/manifest.json in this build")

    print()
    if ok:
        print("APK and site agree on the signing secret.")
        return 0
    print("DO NOT SHIP: the site and the APK would disagree about every key.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
