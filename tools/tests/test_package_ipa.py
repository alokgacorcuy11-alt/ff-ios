"""Test tools/package_ipa.py end to end: build a synthetic decrypted IPA,
inject a dylib, then verify the output with tools/ipa_probe.py."""
import json, os, pathlib, plistlib, struct, tempfile, subprocess, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TMP = pathlib.Path(os.environ.get("FFPROXY_TEST_TMP")
                   or (pathlib.Path(tempfile.gettempdir()) / "ffproxy-tests")) / "pkgtest"
TMP.mkdir(parents=True, exist_ok=True)
# The path is fixed across runs, so anything left over from an older version of
# the tool would otherwise sit there and make "what changed?" checks lie.
import shutil as _shutil
_shutil.rmtree(TMP / "out", ignore_errors=True)
FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def lc(cmd, payload):
    size = (8 + len(payload) + 7) & ~7
    return struct.pack("<II", cmd, size) + payload + b"\x00" * (size - 8 - len(payload))


def dylib_cmd(path, cmd=0x0C):
    raw = path.encode() + b"\x00"
    payload = struct.pack("<IIII", 24, 2, 0x10000, 0x10000) + raw
    size = (8 + len(payload) + 7) & ~7
    return struct.pack("<II", cmd, size) + payload + b"\x00" * (size - 8 - len(payload))


def segment(segname, vmaddr, vmsize, fileoff, filesize, nsects, sections):
    body = segname.encode().ljust(16, b"\x00")
    body += struct.pack("<QQQQiiII", vmaddr, vmsize, fileoff, filesize, 7, 5, nsects, 0)
    for (sn, addr, size, off) in sections:
        body += sn.encode().ljust(16, b"\x00")
        body += segname.encode().ljust(16, b"\x00")
        body += struct.pack("<QQIIIIIII", addr, size, off, 4, 0, 0, 0x80000400, 0, 0)
    return lc(0x19, body)


def build_macho(cryptid=0, extra_dylibs=(), arch="arm64"):
    cmds = b""
    n = 0
    cmds += lc(0x1B, bytes(range(16))); n += 1
    cmds += lc(0x32, struct.pack("<IIII", 2, 0x000E0000, 0x000F0000, 0)); n += 1
    cmds += segment("__TEXT", 0x100000000, 0x5000, 0, 0x5000, 1,
                    [("__text", 0x100004000, 0x100, 0x4000)]); n += 1
    cmds += segment("__LINKEDIT", 0x100005000, 0x1000, 0x5000, 0x1000, 0, []); n += 1
    for d in ("/usr/lib/libSystem.B.dylib",
              "/System/Library/Frameworks/Foundation.framework/Foundation"):
        cmds += dylib_cmd(d); n += 1
    cmds += dylib_cmd("@rpath/UnityFramework.framework/UnityFramework"); n += 1
    for d in extra_dylibs:
        cmds += dylib_cmd(d); n += 1
    if cryptid is not None:
        cmds += lc(0x2C, struct.pack("<IIII", 0x4000, 0x1000, cryptid, 0)); n += 1
    cmds += lc(0x1D, struct.pack("<II", 0x5000, 0x100)); n += 1

    cpusub = 2 if arch == "arm64e" else 0
    hdr = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, cpusub, 2, n, len(cmds), 0x200085, 0)
    blob = hdr + cmds
    assert len(blob) < 0x4000, f"load commands too big: {len(blob)}"
    blob += b"\x00" * (0x4000 - len(blob))
    blob += b"\xAA" * 0x1000        # __text
    blob += b"\xBB" * 0x1000        # __LINKEDIT
    return blob


def make_ipa(name, cryptid=0):
    p = TMP / name
    app = "Payload/FreeFire.app"
    info = {
        "CFBundleIdentifier": "com.dts.freefireth",
        "CFBundleExecutable": "FreeFire",
        "CFBundleShortVersionString": "1.108.1",
        "CFBundleVersion": "1081001",
        "MinimumOSVersion": "12.0",
    }
    archs = ["arm64", "arm64e"]
    blobs = [build_macho(cryptid, arch=a) for a in archs]
    n = len(archs)
    hdr_size = 8 + n * 20
    base = (hdr_size + 0xFFF) & ~0xFFF
    hdrs = b""
    cur = base
    for blob in blobs:
        hdrs += struct.pack(">iiIII", 0x0100000C, 0, cur, len(blob), 12)
        cur += len(blob)
    fat = (struct.pack(">II", 0xCAFEBABE, n) + hdrs
           + b"\x00" * (base - hdr_size) + b"".join(blobs))
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{app}/Info.plist", plistlib.dumps(info))
        z.writestr(f"{app}/FreeFire", fat)
        z.writestr(f"{app}/Frameworks/UnityFramework.framework/UnityFramework", b"\x00" * 64)
        z.writestr(f"{app}/_CodeSignature/CodeResources", b"<plist/>")
        z.writestr(f"{app}/embedded.mobileprovision", b"\x30\x82fake")
    return p


def probe(ipa):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "ipa_probe.py"),
                        str(ipa), "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("probe failed")
    return json.loads(r.stdout)[0]


def run_pkg(ipa, dylib, *extra):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "package_ipa.py"),
                           str(ipa), str(dylib), "-o", str(TMP / "out"), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


dylib = TMP / "FFMenu.dylib"
dylib.write_bytes(b"\xCF\xFA\xED\xFE" + b"\x00" * 4096)

print("--- encrypted IPA must be refused ---")
enc = make_ipa("enc.ipa", cryptid=1)
r = run_pkg(enc, dylib)
check("refuses cryptid 1", r.returncode == 1 and "cryptid=1" in (r.stdout + r.stderr),
      f"-> rc={r.returncode}")
check("tells you how to decrypt", "frida-ios-dump" in (r.stdout + r.stderr))

print("\n--- decrypted IPA, camouflaged framework (default) ---")
dec = make_ipa("dec.ipa", cryptid=0)
r = run_pkg(dec, dylib)
check("packaging succeeded", r.returncode == 0, (r.stdout + r.stderr)[-400:] if r.returncode else "")
out = TMP / "out" / "com.dts.freefireth-payload.ipa"
check("output ipa exists", out.exists(), f"-> {out.name}")
check("reports the injected target", "UnityServices.framework" in r.stdout)
check("reports stripped signature", "stripped" in r.stdout)

pr = probe(out)
check("probe still decrypts", pr["cryptid"] == 0)
check("probe sees the big bundle id", pr["bundle_id"] == "com.dts.freefireth")
allc = [d["name"] for d in pr.get("shipped", [])] + [d["name"] for d in pr.get("injected", [])]
check("load command landed", any("UnityServices" in x for x in allc), f"-> {allc}")
check("camouflage hides it from the loose-dylib detector",
      pr["injected"] == [], f"-> injected={pr['injected']}")
check("probe lists it as a vendor framework",
      any("UnityServices" in d["name"] for d in pr.get("shipped", [])))
# The payload IS modified - the signature is gone and a framework was added - so
# the verdict must not read "original". It used to, because it only looked at
# loose dylibs.
check("verdict says MODIFIED, not original",
      "MODIFIED" in pr["verdict"] and "original" not in pr["verdict"],
      f"-> {pr['verdict']}")
check("verdict names the framework it now loads",
      "UnityServices" in pr["verdict"], f"-> {pr['verdict']}")
check("verdict does not claim a loose injection",
      "injected dylib load command" not in pr["verdict"], f"-> {pr['verdict']}")
check("_CodeSignature removed", pr["has_codesignature_dir"] is False)
check("mobileprovision removed", pr["provisioning"] is False)

print("\n--- dry run: every check, no artefact ---")
# wipe the output dir first, so "still empty afterwards" is a real assertion
# and not a statement about leftovers from the packaging runs above.
_shutil.rmtree(TMP / "out", ignore_errors=True)

def _fingerprint():
    d = TMP / "out"
    if not d.exists():
        return []
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in d.glob("*.ipa"))

before = _fingerprint()
r = run_pkg(dec, dylib, "--dry-run")
after = _fingerprint()
check("dry run succeeds", r.returncode == 0, (r.stdout + r.stderr)[-400:] if r.returncode else "")
check("dry run wrote no IPA", after == [], f"-> {[a[0] for a in after]}")
check("dry run reports the target", "UnityServices.framework" in r.stdout)
check("dry run says so", "dry run complete" in r.stdout)
# the same failure that stops a real run must stop a dry run
r = run_pkg(enc, dylib, "--dry-run")
check("dry run still refuses an encrypted IPA",
      r.returncode == 1 and "cryptid=1" in (r.stdout + r.stderr),
      f"-> rc={r.returncode}")

print("\n--- decrypted IPA, no camouflage (loose dylib) ---")
r = run_pkg(dec, dylib, "--no-camouflage")
check("packaging succeeded", r.returncode == 0)
out2 = TMP / "out" / "com.dts.freefireth-payload.ipa"
pr2 = probe(out2)
check("probe NOW flags it as injected", len(pr2["injected"]) == 2,
      f"-> {[d['name'] for d in pr2['injected']]}")
check("probe verdict says MODIFIED", "MODIFIED" in pr2["verdict"])

print("\n--- bundle id rewrite for free-cert sideload ---")
r = run_pkg(dec, dylib, "--bundle-id", "com.me.fftest")
check("packaging succeeded", r.returncode == 0)
out3 = TMP / "out" / "com.me.fftest-payload.ipa"
check("new file name uses the new id", out3.exists(), f"-> {out3.name}")
pr3 = probe(out3)
check("bundle id rewritten", pr3["bundle_id"] == "com.me.fftest")

print("\n--- verify the header was not corrupted ---")
with zipfile.ZipFile(out3) as z:
    exe = z.read("Payload/FreeFire.app/FreeFire")
be = struct.unpack_from(">I", exe, 0)[0]
check("still a fat binary after patching", be == 0xCAFEBABE, f"-> 0x{be:08x}")
# each slice: ncmds grew, sizeofcmds grew, and it still parses
nfat = struct.unpack_from(">I", exe, 4)[0]
ok = True
for i in range(nfat):
    o = struct.unpack_from(">I", exe, 8 + i * 20 + 8)[0]
    magic = struct.unpack_from("<I", exe, o)[0]
    ncmds, sizeofcmds = struct.unpack_from("<II", exe, o + 16)
    if magic != 0xFEEDFACF or ncmds == 0 or sizeofcmds == 0:
        ok = False
check("every slice still has a valid header", ok)

print()
if FAIL:
    print(f"{FAIL} check(s) FAILED")
    sys.exit(1)
print("all packaging checks passed")