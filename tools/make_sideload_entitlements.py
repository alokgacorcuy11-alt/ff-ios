#!/usr/bin/env python3
"""make_sideload_entitlements.py - entitlements a real profile will accept.

Why this exists
---------------
A signer (Sideloadly, AltStore, ESign, GBox, Father, zsign, plain codesign)
either:

  a) derives entitlements from the .mobileprovision it was given, or
  b) signs with the entitlements file you hand it.

Path (a) is always correct. Path (b) is where sideloads break, because a
hand-written plist is almost never the set the profile actually grants:

  * a missing `application-identifier` / `keychain-access-groups`
      -> the app runs, then every keychain call returns -34018
         (errSecMissingEntitlement). Login, token refresh and "remember me"
         break in ways that look like the game is at fault. This one is
         certain, and it is the usual reason a re-signed game "won't log in".

  * a key the profile does not grant
      -> restricted entitlements (platform-application, no-container, app
         groups, push for a non-push profile) are checked against the profile
         and reject the install; unrestricted ones are normally ignored, so
         they are just noise. Either way there is nothing to gain:
         `com.apple.security.network.client` is a macOS App Sandbox key that
         iOS never consults, so it buys nothing and is dropped here.

  * `get-task-allow`
      -> granted by every development profile, so a signer that copies the
         profile will put it in the signature whether or not you ask. It is
         the cheapest "not from the App Store" signal a game can read. It is
         dropped from this plist; Core/Integrity.m also refuses to answer it
         at runtime, because the profile cannot be changed.

So: read the profile, keep exactly what it grants, rewrite the identifier to
the bundle id you are actually installing, and drop `get-task-allow`.

Usage
-----
  # from the profile + the IPA you are about to sign
  python tools/make_sideload_entitlements.py \\
      --profile ~/Downloads/dev.mobileprovision \\
      --ipa out/com.alokgacorcuy11.freefire-payload.ipa \\
      -o out/entitlements.sideload.plist

  # just look at what the profile grants
  python tools/make_sideload_entitlements.py --profile dev.mobileprovision --show

  # will this device even install it?
  python tools/make_sideload_entitlements.py --profile dev.mobileprovision \\
      --udid 00008030-000A1B2C3D4E5F60

  python tools/make_sideload_entitlements.py --self-test

Then:
  zsign -k cert.p12 -p pass -m dev.mobileprovision \\
        -e out/entitlements.sideload.plist -o signed.ipa payload.ipa

Pure stdlib. No macOS needed to generate or inspect.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import plistlib
import sys
import zipfile
from pathlib import Path

# Entitlements that must never reach a sideload, whatever the profile says.
# get-task-allow is a debug grant: it is the cheapest "this build is not from
# the App Store" signal a game can read about itself.
DROP = ("get-task-allow", "beta-reports-active")

# Granted by every profile, requested by nobody on iOS. Kept here so the
# generator can strip it from an input plist that already carries it.
MACOS_ONLY = ("com.apple.security.network.client",
              "com.apple.security.network.server",
              "com.apple.security.app-sandbox")


def plist_from_cms(blob: bytes) -> dict:
    """A .mobileprovision is a CMS/PKCS#7 envelope around an XML plist.

    No crypto needed to read it: the payload is plaintext inside the
    signature. Find the plist and parse it.
    """
    start = blob.find(b"<?xml")
    if start < 0:
        start = blob.find(b"<plist")
    end = blob.find(b"</plist>", start)
    if start < 0 or end < 0:
        raise ValueError("no XML plist inside the profile (not a .mobileprovision?)")
    return plistlib.loads(blob[start:end + len(b"</plist>")])


def read_profile(path: Path) -> dict:
    return plist_from_cms(path.read_bytes())


def read_ipa_bundle_id(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith("Payload/") and n.count("/") == 2
                 and n.endswith(".app/Info.plist")]
        if not names:
            raise ValueError("no Payload/*.app/Info.plist in the IPA")
        info = plistlib.loads(z.read(names[0]))
    bid = info.get("CFBundleIdentifier") or ""
    if not bid:
        raise ValueError("the app's Info.plist has no CFBundleIdentifier")
    return bid


def team_prefix(profile: dict) -> str:
    """The 10-character prefix every identifier in the profile starts with."""
    appid = (profile.get("Entitlements") or {}).get("application-identifier") or ""
    if appid.startswith("*"):
        raise ValueError("application-identifier starts with '*': cannot derive a prefix")
    if "." in appid:
        return appid.split(".", 1)[0]
    teams = profile.get("TeamIdentifier") or []
    if teams:
        return teams[0]
    raise ValueError("profile has neither application-identifier nor TeamIdentifier")


def build(profile: dict, bundle_id: str) -> tuple[dict, list[str]]:
    """Return (entitlements, warnings) for installing `bundle_id`."""
    warnings: list[str] = []
    src = dict(profile.get("Entitlements") or {})
    if not src:
        raise ValueError("profile carries no Entitlements dict")

    prefix = team_prefix(profile)
    appid = src.get("application-identifier") or ""
    wildcard = appid.endswith(".*") or appid == "*"

    if not wildcard and appid and appid.split(".", 1)[1] != bundle_id:
        warnings.append(
            "profile is NOT a wildcard: it only allows '%s'. "
            "Signing '%s' with it will fail at install. Either use the IPA "
            "whose bundle id matches, or use a wildcard profile."
            % (appid, bundle_id))

    out: dict = {}
    for k, v in src.items():
        if k in DROP or k in MACOS_ONLY:
            continue
        out[k] = v

    out["application-identifier"] = "%s.%s" % (prefix, bundle_id)
    out["com.apple.developer.team-identifier"] = prefix

    groups = src.get("keychain-access-groups")
    if isinstance(groups, list):
        fixed = []
        for g in groups:
            if g.endswith(".*") and g.startswith(prefix + "."):
                fixed.append("%s.%s" % (prefix, bundle_id))
            else:
                fixed.append(g)
        # the app's own group must be first: it is the default for keychain calls
        own = "%s.%s" % (prefix, bundle_id)
        fixed = [own] + [g for g in fixed if g != own]
        out["keychain-access-groups"] = fixed
    else:
        # a dev profile always grants this; if it is absent the profile is odd,
        # but adding it is harmless because it is inside the prefix.
        out["keychain-access-groups"] = ["%s.%s" % (prefix, bundle_id)]

    if "get-task-allow" in src:
        warnings.append(
            "profile grants get-task-allow -> the on-disk signature will still "
            "carry it (a signer copies it from the profile). Removed from this "
            "plist; Core/Integrity.m refuses to answer it at runtime.")
    if src.get("com.apple.security.network.client"):
        warnings.append("input plist asked for com.apple.security.network.client "
                        "-> dropped (macOS-only; iOS needs no network entitlement)")
    if profile.get("ProvisionsAllDevices"):
        warnings.append("ProvisionsAllDevices -> this is an ENTERPRISE profile. "
                        "Shared enterprise certs are revoked often and are a "
                        "well-known cheat-install signal.")
    if wildcard:
        warnings.append("wildcard profile -> '%s' is free to use" % bundle_id)
    return out, warnings


def profile_state(profile: dict) -> tuple[str, list[str]]:
    """(summary, warnings) about the profile itself."""
    warns: list[str] = []
    name = profile.get("Name") or "<unnamed>"
    expires = profile.get("ExpirationDate")
    now = _dt.datetime.now(_dt.timezone.utc)
    bits = [name]
    if isinstance(expires, _dt.datetime):
        e = expires if expires.tzinfo else expires.replace(tzinfo=_dt.timezone.utc)
        left = (e - now).total_seconds() / 86400.0
        bits.append("expires %s (%.1f days)" % (e.strftime("%Y-%m-%d"), left))
        if left <= 0:
            warns.append("profile EXPIRED - re-sign before installing")
        elif left < 2:
            warns.append("profile expires in under 2 days - re-sign now")
    else:
        bits.append("no ExpirationDate")
    devices = profile.get("ProvisionedDevices")
    if profile.get("ProvisionsAllDevices"):
        bits.append("all devices")
    elif devices is not None:
        bits.append("%d devices" % len(devices))
    return ", ".join(bits), warns


def norm_udid(s: str) -> str:
    return s.replace("-", "").strip().upper()


def check_udid(profile: dict, udid: str) -> bool:
    devs = profile.get("ProvisionedDevices")
    if profile.get("ProvisionsAllDevices"):
        return True
    if not devs:
        return False
    return norm_udid(udid) in {norm_udid(d) for d in devs}


# ------------------------------------------------------------------ self-test
def _synthetic_profile() -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "Name": "iOS Team Provisioning Profile: com.example.app",
        "AppIDName": "XC Wildcard",
        "TeamIdentifier": ["ABCDE12345"],
        "TeamName": "Example Team",
        "ApplicationIdentifierPrefix": ["ABCDE12345"],
        "CreationDate": now - _dt.timedelta(days=1),
        "ExpirationDate": now + _dt.timedelta(days=6),
        "ProvisionedDevices": ["00008030-000A1B2C3D4E5F60"],
        "Entitlements": {
            "application-identifier": "ABCDE12345.*",
            "com.apple.developer.team-identifier": "ABCDE12345",
            "get-task-allow": True,
            "keychain-access-groups": ["ABCDE12345.*"],
            "com.apple.security.network.client": True,
            "aps-environment": "development",
        },
    }


def self_test() -> int:
    fails = 0
    total = 0

    def ck(name, cond, extra=""):
        nonlocal fails, total
        total += 1
        print("[%s] %s %s" % ("ok  " if cond else "FAIL", name, extra))
        if not cond:
            fails += 1

    # CMS round-trip: junk on both sides of the plist, like a real profile
    p = _synthetic_profile()
    blob = b"0\x82\x1a\xb6\x06\t*\x86H\x86\xf7" + plistlib.dumps(p) + b"\x00\x01sig"
    ck("parses a plist out of a CMS envelope", plist_from_cms(blob)["Name"] == p["Name"])
    ck("prefix comes from application-identifier", team_prefix(p) == "ABCDE12345")

    ent, warns = build(p, "com.alokgacorcuy11.freefire")
    ck("application-identifier is rewritten",
       ent["application-identifier"] == "ABCDE12345.com.alokgacorcuy11.freefire",
       ent["application-identifier"])
    ck("team identifier is present",
       ent["com.apple.developer.team-identifier"] == "ABCDE12345")
    ck("get-task-allow is gone", "get-task-allow" not in ent)
    ck("macOS-only network entitlement is gone",
       "com.apple.security.network.client" not in ent)
    ck("keychain group follows the bundle id",
       ent["keychain-access-groups"][0] == "ABCDE12345.com.alokgacorcuy11.freefire",
       str(ent["keychain-access-groups"]))
    ck("push entitlement survives", ent.get("aps-environment") == "development")
    ck("the get-task-allow removal is reported",
       any("get-task-allow" in w for w in warns))

    # an exact (non-wildcard) profile must complain about a foreign bundle id
    exact = _synthetic_profile()
    exact["Entitlements"]["application-identifier"] = "ABCDE12345.com.example.app"
    exact["Entitlements"]["keychain-access-groups"] = ["ABCDE12345.com.example.app"]
    _, w2 = build(exact, "com.other.app")
    ck("a non-wildcard profile warns on a foreign id",
       any("NOT a wildcard" in w for w in w2))
    _, w3 = build(exact, "com.example.app")
    ck("...and stays quiet on its own id", not any("NOT a wildcard" in w for w in w3))

    # UDID normalisation: iOS shows it with dashes, profiles store it without
    ck("UDID match ignores dashes", check_udid(p, "00008030-000A1B2C3D4E5F60"))
    ck("unknown UDID is rejected", not check_udid(p, "11111111-2222333344445555"))
    ck("lower-case UDID matches", check_udid(p, "00008030000a1b2c3d4e5f60"))

    ent2 = _synthetic_profile()
    ent2["ProvisionsAllDevices"] = True
    _, w4 = build(ent2, "com.x.y")
    ck("enterprise profile is called out", any("ENTERPRISE" in w for w in w4))

    print()
    print("%d checks, %d failed" % (total, fails))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="entitlements a sideload profile will actually accept")
    ap.add_argument("--profile", type=Path, help=".mobileprovision to read")
    ap.add_argument("--ipa", type=Path, help="IPA whose bundle id to sign")
    ap.add_argument("--bundle-id", help="bundle id, if not read from --ipa")
    ap.add_argument("-o", "--out", type=Path, help="write the plist here")
    ap.add_argument("--udid", help="check a device UDID against the profile")
    ap.add_argument("--show", action="store_true", help="print what the profile grants")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if not a.profile:
        ap.error("--profile is required (or --self-test)")

    profile = read_profile(a.profile)
    summary, warns = profile_state(profile)
    print("profile : %s" % summary)
    print("file    : %s" % a.profile)

    if a.udid:
        ok = check_udid(profile, a.udid)
        print("udid    : %s -> %s" % (a.udid, "in profile" if ok else "NOT in profile"))
        if not ok:
            warns.append("this device is not in the profile; the install will be "
                         "rejected. Re-issue the profile with the UDID added.")

    if a.show:
        print("\ngranted by the profile:")
        for k, v in sorted((profile.get("Entitlements") or {}).items()):
            print("  %-46s %s" % (k, v))

    bundle_id = a.bundle_id
    if not bundle_id and a.ipa:
        bundle_id = read_ipa_bundle_id(a.ipa)
        print("ipa     : %s -> %s" % (a.ipa, bundle_id))
    if not bundle_id and not a.show:
        ap.error("give --ipa or --bundle-id (or use --show)")

    if bundle_id:
        ent, w = build(profile, bundle_id)
        warns += w
        print("\nentitlements to sign with:")
        for k, v in ent.items():
            print("  %-46s %s" % (k, v))
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_bytes(plistlib.dumps(ent, fmt=plistlib.FMT_XML,
                                             sort_keys=False))
            print("\nwrote %s (%d bytes)" % (a.out, a.out.stat().st_size))

    if warns:
        print("\nnotes:")
        for w in warns:
            print("  ! %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
