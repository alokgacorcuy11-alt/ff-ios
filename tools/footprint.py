#!/usr/bin/env python3
"""footprint.py - what can a game key a blacklist on, in this APK?

A blacklist needs something stable to match. For an APK that means the package
id, the signing certificate, the bytes of its assets, the bytes of its code -
anything that is identical for every user who installs the same file. One user
gets caught, the value goes on the list, every other user of that build is
already on it.

This tool lists those values, then lists the mechanisms that make an app
*findable in the first place*: writing into another app's data dir, loading
code at runtime, patching a managed assembly, drawing an overlay.

  python tools/footprint.py app.apk
  python tools/footprint.py app.apk --json out.json
  python tools/footprint.py app.apk --check      # exit 1 on a hard finding

The comparison this exists for:

  root daemon reading /proc/<pid>/mem
      no package the game can name, nothing in the game's sandbox, no code in
      its process. Nothing here applies.

  patched client / in-process patch / dropped payload
      package, cert, payload hash, cross-app file writes, loaded assemblies -
      every one of them is a stable, shared value. Table stakes.

Everything is stdlib except keytool, which is optional.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apk_triage as at  # noqa: E402

# mechanism -> strings that reveal it
MARKERS = {
    "signature killer": ["SignatureKiller", "libSignatureKiller", "origin.apk"],
    "managed patch": ["Assembly-CSharp-patch", "IFix", "ILFix", "patch.bytes"],
    "dynamic dex": ["DexClassLoader", "InMemoryDexClassLoader", "BaseDexClassLoader"],
    "frida": ["libfrida-gadget", "frida-server", "frida-gadget", "frida-agent"],
    "xposed": ["XposedBridge", "de.robv.android.xposed", "LSPosed"],
    "native hooking": ["MSHookFunction", "libsubstrate", "DobbyHook", "And64InlineHook"],
    "adb pairing": ["pairing_auth", "libspake2", "AbsAdbConnectionManager", "adb pair"],
    "overlay": ["SYSTEM_ALERT_WINDOW", "TYPE_APPLICATION_OVERLAY", "TYPE_SYSTEM_OVERLAY"],
    "accessibility": ["BIND_ACCESSIBILITY_SERVICE", "AccessibilityService"],
    "screen capture": ["MediaProjection", "createVirtualDisplay", "ImageReader"],
}

CROSS_APP = re.compile(r"Android/(?:data|media|obb)/([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)")
FILE_LIKE = re.compile(r"^[A-Za-z0-9_.-]{3,64}\.(?:json|txt|bin|so|dex|dat|db|bytes|cfg|ini|log|xml)$")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    counts = [0] * 256
    for x in b:
        counts[x] += 1
    import math
    total = len(b)
    return -sum((c / total) * math.log2(c / total) for c in counts if c)


def scan(apk: Path, self_pkg: str | None = None) -> dict:
    rep: dict = {"file": str(apk), "size": apk.stat().st_size, "sha256": sha256(apk.read_bytes())}

    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()

        # --- everything a list could match, with its digest
        blobs = {}
        for n in names:
            if n.startswith(("assets/", "lib/")) or re.match(r"classes\d*\.dex$", n):
                b = zf.read(n)
                blobs[n] = {"size": len(b), "sha256": sha256(b), "entropy": round(entropy(b), 2)}

        dex = b"".join(zf.read(n) for n in names
                       if re.match(r"classes\d*\.dex$", n))
        arsc = zf.read("resources.arsc") if "resources.arsc" in names else b""

    rep["code_and_assets"] = blobs

    # --- identity
    tri = at.triage(apk, [])
    man = tri.get("manifest") or {}
    pkg = man.get("package") or self_pkg
    rep["identity"] = {
        "package": pkg,
        "version": "%s (%s)" % (man.get("versionName"), man.get("versionCode")),
        "permissions": man.get("permissions") or [],
        "certificate": tri.get("certificate") or {},
    }

    # --- mechanisms
    strings = [s for s in re.findall(rb"[\x20-\x7e]{4,}", dex)]
    text = b"\n".join(strings).decode("ascii", "replace")
    found = {}
    for name, needles in MARKERS.items():
        hits = [n for n in needles if n in text]
        if hits:
            found[name] = hits
    rep["mechanisms"] = found

    # --- writes into another app's sandbox: the loudest possible artefact
    cross = {}
    for m in CROSS_APP.finditer(text):
        target = m.group(1)
        if self_pkg and target == self_pkg:
            continue
        cross.setdefault(target, 0)
        cross[target] += 1
    rep["cross_app_paths"] = sorted(cross, key=lambda k: -cross[k])

    # filenames that look like they get written somewhere
    payload_names = sorted({s for s in text.splitlines()
                            if FILE_LIKE.match(s.strip()) and len(s.strip()) < 64})
    rep["payload_names"] = payload_names[:40]

    # --- shared fingerprint: identical for every user of this APK
    shared = [("package", pkg), ("apk sha256", rep["sha256"])]
    cert_sha = (rep["identity"]["certificate"] or {}).get("sha256")
    if cert_sha:
        shared.append(("certificate", cert_sha))
    for n, v in blobs.items():
        if v["size"] > 4096:
            shared.append((n, v["sha256"]))
    rep["shared_fingerprint"] = [{"what": w, "value": v} for w, v in shared]

    # --- verdict
    hard = []
    if rep["cross_app_paths"]:
        hard.append("writes into another app's data dir: " + ", ".join(rep["cross_app_paths"][:4]))
    if rep["mechanisms"].get("managed patch"):
        hard.append("patches a managed assembly - code runs inside the target process")
    if rep["mechanisms"].get("dynamic dex"):
        hard.append("loads code at runtime (DexClassLoader)")
    if rep["mechanisms"].get("signature killer"):
        hard.append("carries a signature killer - the build is already a repack")
    rep["hard_findings"] = hard

    return rep


def report(rep: dict) -> None:
    ident = rep["identity"]
    print("file      %s" % rep["file"])
    print("sha256    %s" % rep["sha256"])
    print("package   %s" % ident["package"])
    print("version   %s" % ident["version"])
    cert = ident.get("certificate") or {}
    if cert.get("owner"):
        print("cert      %s" % cert["owner"])
    if cert.get("sha256"):
        print("cert sha  %s" % cert["sha256"])

    if rep["mechanisms"]:
        print("\nmechanisms")
        for k, v in rep["mechanisms"].items():
            print("  %-18s %s" % (k, ", ".join(v[:4])))

    if rep["cross_app_paths"]:
        print("\ncross-app paths (write targets inside another app's sandbox)")
        for p in rep["cross_app_paths"]:
            print("  /Android/data/%s/" % p)
    if rep["payload_names"]:
        print("\nfile-like strings (candidate payload names)")
        for p in rep["payload_names"][:12]:
            print("  %s" % p)

    print("\nshared fingerprint - identical for every user of this APK")
    for item in rep["shared_fingerprint"]:
        print("  %-26s %s" % (item["what"], item["value"][:24] + ("..." if len(item["value"]) > 24 else "")))

    print("\ncode and assets")
    for n, v in sorted(rep["code_and_assets"].items()):
        tag = ""
        if v["entropy"] > 7.5 and n.startswith("assets/"):
            tag = "  <- high entropy (encrypted payload?)"
        print("  %-46s %9d  %.2f  %s%s" % (n, v["size"], v["entropy"], v["sha256"][:16], tag))

    hard = rep["hard_findings"]
    print("\nverdict")
    if hard:
        for h in hard:
            print("  HARD  %s" % h)
        print("  -> every value above is a list entry the moment one user is caught")
    else:
        print("  no cross-app writes, no runtime code loading, no in-process patching")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="what a game can blacklist this APK on")
    ap.add_argument("apk", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--self", dest="self_pkg", help="own package, excluded from cross-app paths")
    ap.add_argument("--check", action="store_true", help="exit 1 on a hard finding")
    args = ap.parse_args(argv)

    if not args.apk.is_file():
        print("[!] not a file: %s" % args.apk, file=sys.stderr)
        return 2
    try:
        rep = scan(args.apk, args.self_pkg)
    except zipfile.BadZipFile:
        print("[!] not an APK", file=sys.stderr)
        return 2

    report(rep)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print("\n-> %s" % args.json)
    if args.check and rep["hard_findings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))