#!/usr/bin/env python3
"""test_entitlements.py - the sideload entitlements generator, offline.

Two things are being protected here:

  1. the generator's contract - what it puts in, what it takes out
  2. the repo's own plists - a hand-edited entitlements.plist is the single
     most common way a working build stops installing, so the shipped file is
     pinned to "empty, and nothing private in it"

  python tools/tests/test_entitlements.py
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
NATIVE = ROOT / "native"

sys.path.insert(0, str(ROOT / "tools"))
import make_sideload_entitlements as m  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def main() -> int:
    print("=" * 66)
    print(" sideload entitlements")
    print("=" * 66)

    prof = m._synthetic_profile()
    ent, warns = m.build(prof, "com.alokgacorcuy11.freefire")

    # ---- the contract
    check("application-identifier is the profile prefix + the IPA's id",
          ent["application-identifier"] == "ABCDE12345.com.alokgacorcuy11.freefire",
          ent["application-identifier"])
    check("team identifier is carried",
          ent["com.apple.developer.team-identifier"] == "ABCDE12345")
    check("get-task-allow never survives", "get-task-allow" not in ent)
    check("macOS-only network keys never survive",
          not any(k in ent for k in m.MACOS_ONLY))
    check("no private/jailbreak entitlement is ever added",
          "platform-application" not in ent
          and "com.apple.private.security.no-container" not in ent)
    check("the app's own keychain group is first",
          ent["keychain-access-groups"][0]
          == "ABCDE12345.com.alokgacorcuy11.freefire",
          str(ent["keychain-access-groups"]))
    check("every keychain group stays inside the profile's prefix",
          all(g.startswith("ABCDE12345.") for g in ent["keychain-access-groups"]))
    check("a profile-granted push entitlement is kept",
          ent.get("aps-environment") == "development")

    # round-trip: what we write must parse back identically
    blob = plistlib.dumps(ent, fmt=plistlib.FMT_XML, sort_keys=False)
    check("the plist round-trips", plistlib.loads(blob) == ent)

    # ---- warnings that keep a user from wasting an install
    _, w = m.build(m._synthetic_profile(), "com.other.id")
    check("a wildcard profile is reported as such", any("wildcard" in x for x in w))
    exact = m._synthetic_profile()
    exact["Entitlements"]["application-identifier"] = "ABCDE12345.com.example.app"
    _, w2 = m.build(exact, "com.other.id")
    check("a non-wildcard profile refuses a foreign id loudly",
          any("NOT a wildcard" in x for x in w2))
    _, w3 = m.build(exact, "com.example.app")
    check("...and is quiet for its own id", not any("NOT a wildcard" in x for x in w3))

    # ---- UDID gate
    check("dashed UDID matches", m.check_udid(prof, "00008030-000A1B2C3D4E5F60"))
    check("dashless UDID matches", m.check_udid(prof, "00008030000a1b2c3d4e5f60"))
    check("foreign UDID is rejected", not m.check_udid(prof, "11111111-2222333344445555"))
    ent_all = m._synthetic_profile()
    ent_all["ProvisionsAllDevices"] = True
    check("an enterprise profile accepts any device", m.check_udid(ent_all, "whatever"))

    # ---- CMS parsing: a real profile is a signed envelope, not bare XML
    wrapped = b"0\x82\x1a\xb6\x06\t*\x86H\x86\xf7" + plistlib.dumps(prof) + b"\x00sig"
    check("a plist is found inside a CMS envelope",
          m.plist_from_cms(wrapped)["TeamIdentifier"] == ["ABCDE12345"])

    # ---- the shipped plists
    side = plistlib.loads((NATIVE / "entitlements.plist").read_bytes())
    troll = plistlib.loads((NATIVE / "entitlements.trollstore.plist").read_bytes())
    check("native/entitlements.plist is empty by design", side == {}, str(side))
    check("...so a sideload can never inherit a bad key",
          not any(k in side for k in m.DROP + m.MACOS_ONLY))
    check("the TrollStore plist has the private pair",
          troll.get("platform-application") is True
          and troll.get("com.apple.private.security.no-container") is True)
    check("...and still no get-task-allow", "get-task-allow" not in troll)

    # ---- the tool must run on a bare python, no pip
    src = (ROOT / "tools" / "make_sideload_entitlements.py").read_text(encoding="utf-8")
    imports = {ln.split()[1].split(".")[0] for ln in src.splitlines()
               if ln.startswith("import ")}
    check("stdlib only", imports <= {"argparse", "datetime", "plistlib", "sys", "zipfile"},
          ",".join(sorted(imports)))

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_sideload_entitlements.py"),
                        "--self-test"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("its own --self-test passes", r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else "")

    # ---- doc drift: the install guide must point at the generator
    install = (NATIVE / "INSTALL.md").read_text(encoding="utf-8")
    check("INSTALL.md documents the generator",
          "make_sideload_entitlements.py" in install)
    check("INSTALL.md no longer claims get-task-allow is required",
          "entitlement that lets `dyld` map" not in install)
    check("INSTALL.md names the nested-framework signing requirement",
          "must sign nested code" in install)

    # ---- and if the real payload is here, the id it reports must be the one
    #      the manifest recorded (a rename at package time would drift)
    ipa = ROOT / "out" / "com.alokgacorcuy11.freefire-payload.ipa"
    if ipa.exists():
        bid = m.read_ipa_bundle_id(ipa)
        check("the packaged IPA reports the bundle id it was built with",
              bid == "com.alokgacorcuy11.freefire", bid)
    else:
        print("[skip] out/com.alokgacorcuy11.freefire-payload.ipa not present")

    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
