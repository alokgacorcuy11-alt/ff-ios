#!/usr/bin/env python3
"""ipa_probe.py - read any FF IPA and tell the truth about it.

No Xcode, no otool, no macOS. Pure Python, pure stdlib. Point it at an IPA and
it reports:

  * is the binary still App Store encrypted?   (cryptid)
  * is there an INJECTED dylib load command?    <- this is the whole question
  * what else got added / stripped
  * bundle id, version, archs, signature state

Run it on your own decrypted IPA, and run it on any "proxy cheat" IPA you find.
The output settles the argument without anyone's opinion.

  python tools/ipa_probe.py FreeFire.ipa
  python tools/ipa_probe.py *.ipa --json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import plistlib
import re
import struct
import sys
import tempfile
import zipfile

# ---------------------------------------------------------------- constants
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
MH_MAGIC = 0xFEEDFACE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
FAT_MAGIC_64 = 0xCAFEBABF

LC_CODE_SIGNATURE = 0x1D
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_LAZY_LOAD_DYLIB = 0x20
LC_ID_DYLIB = 0x0D
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_IPHONEOS = 0x25
LC_UUID = 0x1B
LC_SYMTAB = 0x02

DYLIB_CMDS = {
    LC_LOAD_DYLIB: "LC_LOAD_DYLIB",
    LC_LOAD_WEAK_DYLIB: "LC_LOAD_WEAK_DYLIB",
    LC_REEXPORT_DYLIB: "LC_REEXPORT_DYLIB",
    LC_LOAD_UPWARD_DYLIB: "LC_LOAD_UPWARD_DYLIB",
    LC_LAZY_LOAD_DYLIB: "LC_LAZY_LOAD_DYLIB",
}

# libraries an untouched iOS game binary legitimately links
# Swift / C++ / zlib runtimes live in the OS, referenced as @rpath/lib*.dylib
SYSTEM_DYLIB_PREFIXES = (
    "libswift", "libobjc", "libc++", "libc++abi", "libz.", "libsqlite3",
    "libxml2", "libicucore", "libSystem", "libcompression", "libnetwork",
)
APPLE_FRAMEWORKS = (
    "Foundation", "UIKit", "CoreGraphics", "QuartzCore", "CoreFoundation",
    "Metal", "MetalKit", "OpenGLES", "AVFoundation", "CoreAudio", "AudioToolbox",
    "CoreMedia", "CoreVideo", "Security", "SystemConfiguration", "CFNetwork",
    "CoreTelephony", "CoreLocation", "CoreMotion", "GameKit", "StoreKit",
    "AdSupport", "AppTrackingTransparency", "WebKit", "JavaScriptCore",
    "MobileCoreServices", "ImageIO", "Accelerate", "CoreImage", "CoreText",
    "CoreData", "CoreML", "Vision", "ARKit", "ReplayKit", "UserNotifications",
    "Photos", "Contacts", "MapKit", "MessageUI", "SafariServices", "AuthenticationServices",
    "DeviceCheck", "CryptoKit", "Combine", "SwiftUI", "UniformTypeIdentifiers",
    "Network", "OSLog", "BackgroundTasks", "MetricKit", "CoreHaptics", "GameController",
)


def _s32(b: bytes, off: int) -> int:
    return struct.unpack_from("<i", b, off)[0]


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _read_cstr(b: bytes, off: int, limit: int = 4096) -> str:
    end = b.find(b"\x00", off, off + limit)
    if end < 0:
        end = min(off + limit, len(b))
    return b[off:end].decode("utf-8", "replace")


def slice_report(data: bytes, offset: int = 0) -> dict:
    """Parse one thin Mach-O starting at `offset`."""
    magic = _u32(data, offset)
    if magic == MH_MAGIC_64:
        endian, is64 = "<", True
    elif magic == MH_CIGAM_64:
        endian, is64 = ">", True
    elif magic == MH_MAGIC:
        endian, is64 = "<", False
    elif _u32(data, offset) == 0xCFFAEDFE:
        endian, is64 = ">", True
    elif _u32(data, offset) == 0xCEFAEDFE:
        endian, is64 = ">", False
    else:
        raise ValueError(f"not a Mach-O at 0x{offset:x} (magic 0x{magic:08x})")

    def s32(o: int) -> int:
        return struct.unpack_from(endian + "i", data, o)[0]

    def u32(o: int) -> int:
        return struct.unpack_from(endian + "I", data, o)[0]

    cpu = s32(offset + 4)
    subtype = s32(offset + 8)
    filetype = u32(offset + 12)
    ncmds = u32(offset + 16)
    sizeofcmds = u32(offset + 20)
    hdr_size = 32 if is64 else 28

    arch = {16777228: "arm64", 12: "armv7", 16777216: "arm64_32"}.get(cpu & 0xFFFFFFFF, f"cpu{cpu}")
    if cpu == 16777228 and (subtype & 0xFF) == 2:
        arch = "arm64e"

    rep = {
        "arch": arch,
        "filetype": {2: "MH_EXECUTE", 6: "MH_DYLIB", 8: "MH_BUNDLE"}.get(filetype, f"type{filetype}"),
        "cryptid": None,
        "cryptsize": 0,
        "code_signature": False,
        "dylibs": [],
        "sections": u32(offset + 4) and 0,
    }

    p = offset + hdr_size
    end = p + sizeofcmds
    for _ in range(ncmds):
        if p + 8 > offset + len(data) or p >= end:
            break
        cmd = u32(p)
        cmdsize = u32(p + 4)
        if cmdsize < 8:
            break
        if cmd == LC_ENCRYPTION_INFO_64 and cmdsize >= 24:
            rep["cryptid"] = u32(p + 16)
            rep["cryptsize"] = u32(p + 12)
        elif cmd == LC_ENCRYPTION_INFO and cmdsize >= 20:
            rep["cryptid"] = u32(p + 16)
            rep["cryptsize"] = u32(p + 12)
        elif cmd == LC_CODE_SIGNATURE:
            rep["code_signature"] = True
        elif cmd in DYLIB_CMDS:
            name_off = u32(p + 8)
            name = _read_cstr(data, p + name_off, cmdsize)
            rep["dylibs"].append({"cmd": DYLIB_CMDS[cmd], "name": name})
        p += cmdsize
    return rep


def parse_macho(path: pathlib.Path) -> dict:
    data = path.read_bytes()
    # fat headers are big-endian regardless of the slices inside
    be = struct.unpack_from(">I", data, 0)[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        nfat = struct.unpack_from(">I", data, 4)[0]
        is64 = be == FAT_MAGIC_64
        slices = []
        off = 8
        for _ in range(nfat):
            if is64:
                _cpu, _sub, o, sz, _al, _res = struct.unpack_from(">iiQQII", data, off)
                off += 32
            else:
                _cpu, _sub, o, sz, _al = struct.unpack_from(">iiIII", data, off)
                off += 20
            try:
                slices.append(slice_report(data, o))
            except ValueError:
                pass
        return {"fat": True, "slices": slices}
    return {"fat": False, "slices": [slice_report(data, 0)]}


def classify(name: str, bundle_frameworks: set[str], bundle_dylibs: set[str]) -> str:
    """Classify one LC_LOAD_DYLIB target.

    The injection signature in this ecosystem is a load command pointing at a
    loose `.dylib` (usually `@executable_path/Frameworks/libX.dylib`), even when
    the file itself was copied into the bundle. Vendor frameworks that resolve
    inside the bundle are normal and must not be flagged.
    """
    if name.startswith(("/usr/lib/", "/System/")):
        return "system"

    m = re.match(r"@(rpath|executable_path|loader_path)/(.+)$", name)
    tail = m.group(2) if m else name

    if ".framework/" in tail:
        fw = tail.split(".framework/")[0].split("/")[-1]
        if fw in bundle_frameworks:
            return "vendor-framework"
        for afw in APPLE_FRAMEWORKS:
            if afw == fw:
                return "apple-framework"
        return "unresolved-framework"

    if tail.endswith(".dylib"):
        base = tail.split("/")[-1]
        if base.startswith(SYSTEM_DYLIB_PREFIXES):
            return "system"
        if base in bundle_dylibs:
            return "injected-dylib"
        return "unresolved-dylib"

    for afw in APPLE_FRAMEWORKS:
        if f"/{afw}.framework/" in name:
            return "apple-framework"
    return "unknown"


def probe(ipa: pathlib.Path) -> dict:
    out: dict = {"file": ipa.name, "size": ipa.stat().st_size, "ok": False}
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        with zipfile.ZipFile(ipa) as z:
            names = z.namelist()
            app_dirs = set()
            for n in names:
                if not n.startswith("Payload/"):
                    continue
                i = n.find(".app/")
                if i > 0:
                    app_dirs.add(n[:i + 4])
            if not app_dirs:
                out["error"] = "no Payload/*.app in the archive"
                return out
            app = sorted(app_dirs)[0]
            out["app"] = app.rsplit("/", 1)[-1]

            # Info.plist
            info_name = f"{app}/Info.plist"
            if info_name not in names:
                info_name = f"{app}/Info.plist".replace(".app", ".app")
            info = {}
            try:
                raw = z.read(info_name)
                info = plistlib.loads(raw)
            except Exception:
                pass
            out["bundle_id"] = info.get("CFBundleIdentifier")
            out["version"] = info.get("CFBundleShortVersionString")
            out["build"] = info.get("CFBundleVersion")
            out["min_ios"] = info.get("MinimumOSVersion")
            exe = info.get("CFBundleExecutable")
            out["executable"] = exe

            # frameworks / plugins present in the bundle
            fw = sorted({m.group(1) for n in names
                         if n.startswith(f"{app}/Frameworks/")
                         for m in [re.search(r"/([^/]+)\.framework/", n)] if m})
            out["frameworks"] = fw[:60]
            out["dylibs_in_bundle"] = sorted({n.split("/")[-1] for n in names
                                              if n.startswith(f"{app}/Frameworks/")
                                              and n.endswith(".dylib")})
            out["has_codesignature_dir"] = any(
                n.startswith(f"{app}/_CodeSignature/") for n in names)
            out["provisioning"] = any(n.endswith("embedded.mobileprovision") for n in names)

            # ---- identity / engine detection, so a wrong file is obvious
            blob = "\n".join(names)
            out["is_unity"] = ("UnityFramework" in blob
                               or "Data/Managed/Metadata/global-metadata.dat" in blob
                               or "Data/Managed" in blob)
            out["has_il2cpp_metadata"] = "global-metadata.dat" in blob
            bid = (info.get("CFBundleIdentifier") or "").lower()
            exen = (exe or "").lower()
            out["is_freefire"] = (bid.startswith("com.dts.freefireth")
                                  or "freefire" in bid or "freefire" in exen)
            if out["is_freefire"]:
                out["identity"] = "Free Fire"
            elif out["is_unity"]:
                out["identity"] = "Unity app (not Free Fire)"
            else:
                out["identity"] = "native iOS app (not Free Fire)"

            if not exe:
                out["error"] = "CFBundleExecutable missing"
                return out
            exe_path = root / "exe"
            with z.open(f"{app}/{exe}") as src, exe_path.open("wb") as dst:
                dst.write(src.read())

            macho = parse_macho(exe_path)
            out["slices"] = macho["slices"]

            fw_set = set(fw)
            dy_set = set(out["dylibs_in_bundle"])
            flagged, shipped, other, warn = [], [], [], []
            for sl in macho["slices"]:
                for d in sl["dylibs"]:
                    kind = classify(d["name"], fw_set, dy_set)
                    rec = {"arch": sl["arch"], "cmd": d["cmd"], "name": d["name"],
                           "kind": kind}
                    if kind == "injected-dylib":
                        flagged.append(rec)
                    elif kind in ("unresolved-dylib", "unresolved-framework", "unknown"):
                        warn.append(rec)
                    elif kind == "vendor-framework":
                        shipped.append(rec)
                    else:
                        other.append(rec)
            out["injected"] = flagged
            out["warnings"] = warn
            out["shipped"] = shipped
            out["system"] = other

            crypts = [s["cryptid"] for s in macho["slices"] if s["cryptid"] is not None]
            out["cryptid"] = max(crypts) if crypts else None
            out["archs"] = [s["arch"] for s in macho["slices"]]
            out["code_signature"] = all(s["code_signature"] for s in macho["slices"])

    out["ok"] = True
    out["verdict"] = verdict(out)
    return out


def verdict(r: dict) -> str:
    enc = r.get("cryptid")
    inj = r.get("injected") or []
    warn = r.get("warnings") or []
    dyl = r.get("dylibs_in_bundle") or []
    shipped = r.get("shipped") or []
    # A payload whose signature directory or provisioning profile is gone has
    # been through a re-sign step, whether or not anything was injected. That is
    # a fact about the bundle on disk, unlike "this framework was not in the
    # original", which the probe cannot know without a reference copy.
    stripped = r.get("has_codesignature_dir") is False or r.get("provisioning") is False

    if enc is None:
        head = "UNKNOWN"
    elif enc != 0:
        head = "ENCRYPTED (App Store / FairPlay) - payload cannot be patched"
    else:
        head = "DECRYPTED - payload can be patched"

    if inj:
        loose = sorted({d["name"].split("/")[-1] for d in inj})
        tail = f"MODIFIED - injected dylib load command(s): {', '.join(loose)}"
    elif stripped:
        # The camouflaged shape lands here: nothing is a loose dylib, but the
        # bundle has been touched and the executable now loads a framework that
        # a stock Free Fire does not. Saying "original" here would be a lie, and
        # the previous wording said exactly that.
        extra = sorted({d["name"].split("/")[-2].replace(".framework", "")
                        for d in shipped
                        if d["name"].count("/") >= 2})
        tail = "MODIFIED - signature stripped for re-signing"
        if extra:
            tail += f"; bundled frameworks the executable loads: {', '.join(extra)}"
    elif dyl:
        tail = f"a dylib sits in Frameworks/ but is NOT loaded: {', '.join(dyl)}"
    else:
        tail = "load commands are original - no injection"

    if warn:
        names = sorted({d["name"] for d in warn})
        tail += f"  |  {len(names)} unresolved reference(s): {', '.join(names[:3])}"
    return f"{head}  |  {tail}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rc = 0
    reports = []
    for p in args.ipa:
        path = pathlib.Path(p)
        if not path.exists():
            print(f"[!] missing: {path}", file=sys.stderr)
            rc = 1
            continue
        try:
            r = probe(path)
        except Exception as e:  # noqa: BLE001
            print(f"[!] {path.name}: {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
            continue
        reports.append(r)
        if args.json:
            continue
        if not r.get("ok"):
            print(f"\n=== {r['file']} ===\n  ERROR: {r.get('error')}")
            continue
        print(f"\n{'=' * 72}")
        print(f" {r['file']}  ({r['size'] / 1e6:.1f} MB)")
        print("=" * 72)
        print(f" bundle      : {r['bundle_id']}  v{r['version']} ({r['build']})")
        print(f" executable  : {r['executable']}")
        print(f" identity    : {r.get('identity', '?')}")
        print(f" engine      : "
              f"{'Unity' if r.get('is_unity') else 'native'} "
              f"| il2cpp metadata: {'yes' if r.get('has_il2cpp_metadata') else 'no'}")
        print(f" archs       : {', '.join(r['archs'])}")
        print(f" min ios     : {r['min_ios']}")
        print(f" cryptid     : {r['cryptid']}   "
              f"{'-> still App Store encrypted' if r['cryptid'] else '-> decrypted'}")
        print(f" codesig     : LC_CODE_SIGNATURE={'yes' if r['code_signature'] else 'no'}  "
              f"_CodeSignature/={'yes' if r['has_codesignature_dir'] else 'no'}  "
              f"mobileprovision={'yes' if r['provisioning'] else 'no'}")
        print(f" frameworks  : {len(r['frameworks'])}")
        if r["dylibs_in_bundle"]:
            print(f" dylibs      : {', '.join(r['dylibs_in_bundle'])}")
        print()
        if r["injected"]:
            print(" !! INJECTED LOAD COMMANDS (loose dylib resolved in bundle)")
            for d in r["injected"]:
                print(f"      [{d['arch']}] {d['cmd']}  {d['name']}")
        else:
            print(" -- no injected load commands")
        if r.get("warnings"):
            names = sorted({d["name"] for d in r["warnings"]})
            print(f" ?? {len(names)} reference(s) that do not resolve inside the bundle:")
            for n in names[:6]:
                print(f"      {n}")
        if r.get("shipped"):
            names = sorted({d["name"] for d in r["shipped"]})
            print(f" -- vendor frameworks shipped in the bundle: {len(names)}")
            for n in names[:8]:
                print(f"      {n}")
        print()
        for sl in r["slices"]:
            print(f" [{sl['arch']}] {sl['filetype']}  "
                  f"dylibs={len(sl['dylibs'])}  "
                  f"cryptid={sl['cryptid']}")
        print()
        if not r.get("is_freefire"):
            print(" !! THIS IS NOT FREE FIRE")
            print(f"    bundle id : {r['bundle_id']}")
            print(f"    expected  : com.dts.freefireth  (~1.5-2.5 GB, Unity + IL2CPP)")
            print(f"    got       : {r['size'] / 1e6:.1f} MB, "
                  f"{'Unity without metadata' if r.get('is_unity') else 'no Unity at all'}")
            print("    packaging this would produce a working dylib inside the")
            print("    wrong app - the cheat would never appear in Free Fire.")
            print()
        print(f" VERDICT: {r['verdict']}")
        print("=" * 72)

    if args.json:
        print(json.dumps(reports, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())