#!/usr/bin/env python3
"""package_ipa.py - turn (decrypted IPA + dylib) into a sign-agnostic IPA.

Pure stdlib, runs on Windows / macOS / Linux. No Xcode, no insert_dylib.

What it does
------------
1. verifies the payload is decrypted (cryptid 0) - refuses otherwise
2. installs your dylib as a vendor-looking framework (camouflage by default)
3. injects the LC_LOAD_DYLIB in place, into existing header padding
4. strips _CodeSignature/ and embedded.mobileprovision (every signer wants
   to do that itself: TrollStore, Sideloadly, ESign, GBox, AltStore)
5. optionally rewrites the bundle id for free-cert sideloading
6. repacks and re-reads its own output to prove the command landed

Output is accepted by every iOS installer:
  TrollStore   - permanent fake-sign, keeps the real bundle id
  Sideloadly   - signs with your dev cert (2-factor free Apple ID works)
  AltStore / SideStore - same, 7-day refresh
  ESign / GBox / Scarlet - signs with their cert, or yours
  jailbroken   - drop into the app container, uicache

  python tools/package_ipa.py decrypted.ipa FFMenu.dylib
  python tools/package_ipa.py decrypted.ipa FFMenu.dylib --no-camouflage
  python tools/package_ipa.py decrypted.ipa FFMenu.dylib --bundle-id com.me.ff
  python tools/package_ipa.py decrypted.ipa FFMenu.dylib --name MobileCore
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_SEGMENT_64 = 0x19
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
# Must remain the LAST load command in any binary the kernel will validate.
LC_CODE_SIGNATURE = 0x1D


class PkgError(RuntimeError):
    pass


# ------------------------------------------------------------------ mach-o
def align8(n: int) -> int:
    return (n + 7) & ~7


def dylib_cmd_bytes(path: str) -> bytes:
    name = path.encode("utf-8") + b"\x00"
    size = align8(24 + len(name))
    payload = struct.pack("<IIII", 24, 2, 0x00010000, 0x00010000) + name
    payload += b"\x00" * (size - 8 - len(payload))
    return struct.pack("<II", LC_LOAD_DYLIB, size) + payload


def slice_info(data: bytes, off: int) -> dict:
    magic = struct.unpack_from("<I", data, off)[0]
    if magic != MH_MAGIC_64:
        raise PkgError(f"slice at 0x{off:x} is not a 64-bit little-endian Mach-O "
                       f"(magic 0x{magic:08x})")
    ncmds, sizeofcmds = struct.unpack_from("<II", data, off + 16)
    hdr = 32
    p = off + hdr
    cryptid = None
    first_data = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmdsize < 8:
            break
        if cmd == LC_ENCRYPTION_INFO_64 and cmdsize >= 24:
            cryptid = struct.unpack_from("<I", data, p + 16)[0]
        elif cmd == LC_ENCRYPTION_INFO and cmdsize >= 20:
            cryptid = struct.unpack_from("<I", data, p + 16)[0]
        elif cmd == LC_SEGMENT_64 and cmdsize >= 72:
            fileoff = struct.unpack_from("<Q", data, p + 40)[0]
            nsects = struct.unpack_from("<I", data, p + 64)[0]
            sp = p + 72
            for _s in range(nsects):
                if sp + 80 > p + cmdsize:
                    break
                soff, ssize = struct.unpack_from("<I", data, sp + 48)[0], \
                              struct.unpack_from("<Q", data, sp + 40)[0]
                if soff > 0 and ssize > 0:
                    if first_data is None or soff < first_data:
                        first_data = soff
                sp += 80
            if first_data is None and fileoff > 0:
                first_data = fileoff
        p += cmdsize
    return {
        "off": off,
        "ncmds": ncmds,
        "sizeofcmds": sizeofcmds,
        "hdr": hdr,
        "lc_end": hdr + sizeofcmds,
        "first_data": first_data,
        "cryptid": cryptid,
    }


def slices_of(data: bytes) -> list[dict]:
    be = struct.unpack_from(">I", data, 0)[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        nfat = struct.unpack_from(">I", data, 4)[0]
        out = []
        off = 8
        wide = be == FAT_MAGIC_64
        for _ in range(nfat):
            if wide:
                _c, _s, o, sz, _a, _r = struct.unpack_from(">iiQQII", data, off)
                off += 32
            else:
                _c, _s, o, sz, _a = struct.unpack_from(">iiIII", data, off)
                off += 20
            out.append(slice_info(data, o))
        return out
    return [slice_info(data, 0)]


def inject(data: bytes, target: str) -> tuple[bytes, list[dict]]:
    """Insert one LC_LOAD_DYLIB per slice, in place. Returns patched bytes.

    The new command goes in BEFORE LC_CODE_SIGNATURE, not at the end.

    Apple requires LC_CODE_SIGNATURE to be the LAST load command: the kernel
    locates the signature blob from it and folds everything before it into the
    hash. Appending after it produces a binary whose signature can never be made
    valid - the app then dies at launch, before its own code runs a single
    instruction, which is exactly what a "crash on tap, before the splash
    screen" looks like. insert_dylib solves the same problem the same way.
    """
    buf = bytearray(data)
    report = []
    for sl in slices_of(data):
        cmd = dylib_cmd_bytes(target)
        need = len(cmd)
        lc_end = sl["off"] + sl["lc_end"]

        # where does LC_CODE_SIGNATURE sit?
        sig_at, p = None, sl["off"] + sl["hdr"]
        for _ in range(sl["ncmds"]):
            c, sz = struct.unpack_from("<II", buf, p)
            if c == LC_CODE_SIGNATURE:
                sig_at = p
                break
            p += sz

        # insert before the signature command so it stays last
        at = sig_at if sig_at is not None else lc_end
        limit = sl["off"] + sl["first_data"] if sl["first_data"] else len(buf)
        free = limit - lc_end
        if free < need:
            raise PkgError(
                f"slice at 0x{sl['off']:x}: only {free} bytes of header padding, "
                f"need {need}. This binary has no room for an in-place load "
                f"command.\n  Use insert_dylib on macOS instead:\n"
                f"  insert_dylib --inplace --overwrite --strip-codesig "
                f"--codesign '{target}' <binary>"
            )
        # Shift the commands that follow (the signature command, at minimum) to
        # the right, inside the header padding, and drop ours in the gap. The
        # file length must not change: inserting with `buf[at:at] = cmd` grows the
        # buffer and every offset after it moves, which corrupts the image. The
        # test caught exactly that on the first attempt.
        buf[at + need:lc_end + need] = buf[at:lc_end]
        buf[at:at + need] = cmd
        struct.pack_into("<I", buf, sl["off"] + 16, sl["ncmds"] + 1)
        struct.pack_into("<I", buf, sl["off"] + 20, sl["sizeofcmds"] + need)
        report.append({"slice": hex(sl["off"]), "added": need, "free_before": free,
                       "before_signature": sig_at is not None})
    return bytes(buf), report


def verify(data: bytes, target: str) -> list[str]:
    """Re-read the patched binary and confirm the command is really there."""
    have = []
    for sl in slices_of(data):
        p = sl["off"] + sl["hdr"]
        last_cmd = None
        for _ in range(sl["ncmds"]):
            cmd, cmdsize = struct.unpack_from("<II", data, p)
            if cmd == LC_LOAD_DYLIB:
                name_off = struct.unpack_from("<I", data, p + 8)[0]
                end = data.find(b"\x00", p + name_off)
                have.append(data[p + name_off:end].decode("utf-8", "replace"))
            last_cmd = cmd
            p += cmdsize
        if last_cmd == LC_LOAD_DYLIB:
            # We just put a load command where the signature belongs. The kernel
            # takes the signature from LC_CODE_SIGNATURE and hashes everything
            # before it, so a command after it can never be covered - the app is
            # killed at launch before it runs a single instruction.
            raise PkgError(
                f"slice at 0x{sl['off']:x}: LC_LOAD_DYLIB ended up as the LAST "
                f"load command, which means LC_CODE_SIGNATURE is no longer last. "
                f"Refusing to write a payload that cannot be signed."
            )
    if target not in have:
        raise PkgError(f"verification failed - {target} not present after patch "
                       f"(found: {sorted(set(have))})")
    return sorted(set(have))


# ------------------------------------------------------------------ package
FRAMEWORK_INFO = {
    "CFBundleExecutable": "",
    "CFBundleIdentifier": "",
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundleName": "",
    "CFBundlePackageType": "FMWK",
    "CFBundleShortVersionString": "1.0.0",
    "CFBundleVersion": "1",
    "MinimumOSVersion": "12.0",
    "CFBundleSupportedPlatforms": ["iPhoneOS"],
}


def build(ipa: Path, dylib: Path, outdir: Path, framework: str | None,
          bundle_id: str | None, sign_ready: bool,
          dry_run: bool = False) -> Path | None:
    if not ipa.exists():
        raise PkgError(f"IPA not found: {ipa}")
    if not dylib.exists():
        raise PkgError(f"dylib not found: {dylib}")

    work = Path(tempfile.mkdtemp(prefix="pkg-"))
    try:
        with zipfile.ZipFile(ipa) as z:
            z.extractall(work)
        apps = list((work / "Payload").glob("*.app"))
        if not apps:
            raise PkgError("no Payload/*.app inside the IPA")
        app = apps[0]
        info_path = app / "Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        exe = app / info["CFBundleExecutable"]
        if not exe.exists():
            raise PkgError(f"executable missing: {exe}")
        old_bid = info.get("CFBundleIdentifier", "?")

        # 1. encryption gate
        raw = exe.read_bytes()
        crypts = [s["cryptid"] for s in slices_of(raw) if s["cryptid"] is not None]
        if crypts and max(crypts) != 0:
            raise PkgError(
                f"cryptid={max(crypts)} - this payload is still App Store "
                f"encrypted and cannot be patched.\n  Decrypt it first:\n"
                f"  frida-ios-dump -H <device-ip> {old_bid}\n  bagbak {old_bid}"
            )

        # 2. install the dylib
        fw_dir = app / "Frameworks"
        fw_dir.mkdir(exist_ok=True)
        if framework:
            fdir = fw_dir / f"{framework}.framework"
            fdir.mkdir(exist_ok=True)
            shutil.copy2(dylib, fdir / framework)
            fi = dict(FRAMEWORK_INFO)
            fi["CFBundleExecutable"] = framework
            fi["CFBundleName"] = framework
            # The framework gets its OWN identifier - never the app's, and never
            # derived from it. An earlier version used `bundle_id or ...`, so
            # `--bundle-id com.x.y` stamped the app's identifier onto the
            # framework too and installd refused the install:
            #
            #   DuplicateIdentifier: The parent bundle has the same identifier
            #   (com.x.y.TEAMID) as sub-bundle .../UnityServices.framework
            #
            # A vendored third-party framework carries its own reverse-DNS id in
            # real apps, so this is also the more natural shape to see.
            fi["CFBundleIdentifier"] = f"com.vendor.{framework.lower()}"
            if fi["CFBundleIdentifier"] == (bundle_id or old_bid):
                fi["CFBundleIdentifier"] = f"com.vendor.{framework.lower()}.framework"
            (fdir / "Info.plist").write_bytes(plistlib.dumps(fi))
            target = f"@executable_path/Frameworks/{framework}.framework/{framework}"
            kind = f"framework ({framework}.framework)"
        else:
            name = dylib.name
            shutil.copy2(dylib, fw_dir / name)
            target = f"@executable_path/Frameworks/{name}"
            kind = f"loose dylib ({name})"

        # 3. inject + verify
        patched, report = inject(raw, target)
        verify(patched, target)
        exe.write_bytes(patched)

        # 3b. dry run stops here, after every check that can fail. What the
        # caller wants to know is exactly which load commands the finished
        # payload will carry; the repack costs a minute and tells them nothing
        # new.
        if dry_run:
            print(f"  dry run     : no IPA written")
            print(f"  bundle id   : {info.get('CFBundleIdentifier')}")
            print(f"  executable  : {info['CFBundleExecutable']}")
            print(f"  injected    : {target}")
            print(f"  install as  : {kind}")
            for r in report:
                print(f"  patched     : slice {r['slice']}  +{r['added']}B "
                      f"(padding had {r['free_before']}B)")
            return None

        # 4. optional bundle id
        if bundle_id:
            info["CFBundleIdentifier"] = bundle_id
            info_path.write_bytes(plistlib.dumps(info))

        # 4b. a bundle and its sub-bundles must not share an identifier. installd
        # refuses the whole install with DuplicateIdentifier, and the message it
        # gives names the parent, which sends you looking in the wrong plist.
        if framework:
            fw_plist = fw_dir / f"{framework}.framework" / "Info.plist"
            if fw_plist.exists():
                fw_id = plistlib.loads(fw_plist.read_bytes()).get("CFBundleIdentifier")
                app_id = info.get("CFBundleIdentifier")
                if fw_id == app_id:
                    raise PkgError(
                        f"the framework and the app would share the identifier "
                        f"{app_id!r} - installd rejects that with "
                        f"DuplicateIdentifier. One of them has to differ.")

        # 5. strip anything a signer needs to own
        if sign_ready:
            for p in [app / "_CodeSignature", app / "embedded.mobileprovision"]:
                if p.is_dir():
                    shutil.rmtree(p)
                elif p.exists():
                    p.unlink()
            for sc in app.rglob("_CodeSignature"):
                if sc.is_dir():
                    shutil.rmtree(sc)

        # 6. repack
        outdir.mkdir(parents=True, exist_ok=True)
        # No product token in the filename: the whole point of the camouflage
        # framework is that the artefact does not name itself, and a filename
        # that says what it is gives that away before it is even opened.
        tag = re.sub(r"[^A-Za-z0-9._-]+", "-", (bundle_id or old_bid))
        out = outdir / f"{tag}-payload.ipa"
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(work):
                for f in files:
                    full = Path(root) / f
                    z.write(full, full.relative_to(work))

        print(f"  bundle id   : {info.get('CFBundleIdentifier')}")
        print(f"  executable  : {info['CFBundleExecutable']}")
        print(f"  injected    : {target}")
        print(f"  instal as   : {kind}")
        for r in report:
            print(f"  patched     : slice {r['slice']}  +{r['added']}B "
                  f"(padding had {r['free_before']}B)")
        if sign_ready:
            print("  signature   : stripped (signer will re-sign)")
        else:
            print("  signature   : left as-is")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("dylib")
    ap.add_argument("-o", "--out", default="out")
    ap.add_argument("--name", default="UnityServices",
                    help="framework name (default UnityServices)")
    ap.add_argument("--no-camouflage", action="store_true",
                    help="inject as a loose dylib instead of a framework")
    ap.add_argument("--bundle-id", default=None,
                    help="rewrite CFBundleIdentifier (needed for free-cert sideload)")
    ap.add_argument("--keep-signature", action="store_true",
                    help="do not strip _CodeSignature/ and mobileprovision")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check and report the patch, but write no IPA")
    a = ap.parse_args()

    print("=" * 66)
    print(" package_ipa")
    print("=" * 66)
    print(f" in  : {a.ipa}")
    print(f" dylib: {a.dylib}")
    print()
    try:
        out = build(Path(a.ipa), Path(a.dylib), Path(a.out),
                    None if a.no_camouflage else a.name,
                    a.bundle_id, not a.keep_signature, a.dry_run)
    except PkgError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print()
    if out is None:
        print("  dry run complete - nothing written")
        return 0
    print(f"  -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    print()
    print(" next: verify with   python tools/ipa_probe.py " + str(out))
    print("       (a camouflaged build shows up as a vendor framework, not an")
    print("        injected dylib - that is the point)")
    return 0


if __name__ == "__main__":
    sys.exit(main())