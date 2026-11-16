"""Build synthetic IPAs and verify tools/ipa_probe.py classifies them correctly."""
import json, os, pathlib, plistlib, struct, tempfile, subprocess, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TMP = pathlib.Path(os.environ.get("FFPROXY_TEST_TMP")
                   or (pathlib.Path(tempfile.gettempdir()) / "ffproxy-tests")) / "ipaprobe"
TMP.mkdir(parents=True, exist_ok=True)

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def lc(cmd, payload: bytes) -> bytes:
    size = 8 + len(payload)
    size = (size + 7) & ~7
    return struct.pack("<II", cmd, size) + payload + b"\x00" * (size - 8 - len(payload))


def dylib_cmd(name: str, is_weak=False) -> bytes:
    raw = name.encode() + b"\x00"
    cmd = 0x80000018 if is_weak else 0x0C
    payload = struct.pack("<IIII", 24, 0, 0x10000, 0x10000) + raw
    size = 8 + len(payload)
    size = (size + 7) & ~7
    return struct.pack("<II", cmd, size) + payload + b"\x00" * (size - 8 - len(payload))


def build_macho(dylibs, cryptid, arch="arm64", codesig=True) -> bytes:
    cmds = b""
    n = 0
    cmds += lc(0x1B, bytes(range(16)))                       # LC_UUID
    n += 1
    cmds += lc(0x32, struct.pack("<IIII", 2, 0x000E0000, 0x000F0000, 0))  # LC_BUILD_VERSION
    n += 1
    for d in dylibs:
        cmds += dylib_cmd(d)
        n += 1
    if cryptid is not None:
        cmds += lc(0x2C, struct.pack("<IIII", 0x4000, 0x100000, cryptid, 0))
        n += 1
    if codesig:
        cmds += lc(0x1D, struct.pack("<II", 0x200000, 0x1000))
        n += 1

    cputype = 0x0100000C
    cpusub = 2 if arch == "arm64e" else 0
    hdr = struct.pack("<IIIIIIII", 0xFEEDFACF, cputype, cpusub, 2,
                      n, len(cmds), 0x00200085, 0)
    return hdr + cmds + b"\x00" * 0x4000


def make_ipa(name, dylibs, cryptid, extra_fw=(), signed=True):
    p = TMP / name
    app = "Payload/FreeFire.app"
    info = {
        "CFBundleIdentifier": "com.dts.freefireth",
        "CFBundleExecutable": "FreeFire",
        "CFBundleShortVersionString": "1.108.1",
        "CFBundleVersion": "1081001",
        "MinimumOSVersion": "12.0",
        "CFBundleName": "FreeFire",
    }
    archs = ["arm64", "arm64e"]
    blobs = [build_macho(dylibs, cryptid, arch=a) for a in archs]

    n = len(archs)
    hdr_size = 8 + n * 20
    base = (hdr_size + 0xFFF) & ~0xFFF          # page-align the slice data
    hdrs = b""
    payloads = b""
    cur = base
    for blob in blobs:
        hdrs += struct.pack(">iiIII", 0x0100000C, 0, cur, len(blob), 12)
        cur += len(blob)
    payloads = b"".join(blobs)
    fat = (struct.pack(">II", 0xCAFEBABE, n) + hdrs
           + b"\x00" * (base - hdr_size) + payloads)

    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{app}/Info.plist", plistlib.dumps(info))
        z.writestr(f"{app}/FreeFire", fat)
        # vendor framework that the load command actually resolves to
        z.writestr(f"{app}/Frameworks/UnityFramework.framework/UnityFramework",
                   b"\x00" * 64)
        z.writestr(f"{app}/Frameworks/UnityFramework.framework/Info.plist",
                   plistlib.dumps({"CFBundleIdentifier": "unity.Framework"}))
        for f in extra_fw:
            z.writestr(f"{app}/Frameworks/{f}", b"\x00" * 64)
        if signed:
            z.writestr(f"{app}/_CodeSignature/CodeResources", b"<plist/>")
            z.writestr(f"{app}/embedded.mobileprovision", b"\x30\x82fake")
    return p


def run(ipa):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "ipa_probe.py"),
                        str(ipa), "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit(f"probe failed rc={r.returncode}")
    return json.loads(r.stdout)[0]


APPLE = [
    "/usr/lib/libSystem.B.dylib",
    "/System/Library/Frameworks/Foundation.framework/Foundation",
    "/System/Library/Frameworks/UIKit.framework/UIKit",
    "/System/Library/Frameworks/Metal.framework/Metal",
    "@rpath/UnityFramework.framework/UnityFramework",
    "@rpath/libswiftCore.dylib",
]

print("--- case 1: App Store style (encrypted, clean) ---")
a = make_ipa("appstore.ipa", APPLE, cryptid=1)
r = run(a)
check("detects bundle id", r["bundle_id"] == "com.dts.freefireth")
check("detects version", r["version"] == "1.108.1")
check("cryptid 1 reported", r["cryptid"] == 1)
check("no injected dylibs", r["injected"] == [], f"-> {r['injected']}")
check("apple libs not flagged",
      all("UnityFramework" not in d["name"] for d in r["injected"]))
check("verdict says encrypted", "ENCRYPTED" in r["verdict"], f"-> {r['verdict']}")
check("verdict says original load commands", "original" in r["verdict"])
check("both archs parsed", sorted(r["archs"]) == ["arm64", "arm64e"], f"-> {r['archs']}")
check("identified as Free Fire", r["is_freefire"] is True and r["identity"] == "Free Fire")
check("identified as Unity", r["is_unity"] is True)
check("no il2cpp metadata in this fixture", r["has_il2cpp_metadata"] is False)

print("\n--- case 2: the 'proxy cheat' IPA (decrypted + injected dylib) ---")
b = make_ipa("cheat.ipa", APPLE + ["@executable_path/Frameworks/libFFMenu.dylib"],
             cryptid=0, extra_fw=("libFFMenu.dylib",))
r = run(b)
check("cryptid 0 reported", r["cryptid"] == 0)
check("injected dylib found", len(r["injected"]) == 2, f"-> {len(r['injected'])} (one per arch)")
check("names the injected lib",
      all("libFFMenu.dylib" in d["name"] for d in r["injected"]))
check("verdict says MODIFIED", "MODIFIED" in r["verdict"], f"-> {r['verdict']}")
check("dylib file in bundle detected", r["dylibs_in_bundle"] == ["libFFMenu.dylib"],
      f"-> {r['dylibs_in_bundle']}")

print("\n--- case 3: decrypted, clean (legit prep step) ---")
c = make_ipa("clean-decrypted.ipa", APPLE, cryptid=0)
r = run(c)
check("cryptid 0", r["cryptid"] == 0)
check("no injected", r["injected"] == [])
check("verdict says decrypted + original",
      "DECRYPTED" in r["verdict"] and "original" in r["verdict"])

print("\n--- case 4: dylib parked in Frameworks but never loaded ---")
d = make_ipa("parked.ipa", APPLE, cryptid=0, extra_fw=("libSuspicious.dylib",))
r = run(d)
check("no injected commands", r["injected"] == [])
check("bundle dylib noticed", r["dylibs_in_bundle"] == ["libSuspicious.dylib"])
check("verdict warns it is not loaded", "NOT loaded" in r["verdict"], f"-> {r['verdict']}")

print("\n--- case 5: a real app that is NOT Free Fire ---")
e = make_ipa("foreign.ipa", APPLE, cryptid=0)
# rewrite the identity to look like an unrelated native app, and drop the
# untouched UnityFramework marker out of the fixture set
import zipfile as _z, plistlib as _p, tempfile as _t, os as _o, shutil as _sh
with _t.TemporaryDirectory() as td:
    with _z.ZipFile(e) as z:
        z.extractall(td)
    appdir = next(pathlib.Path(td, "Payload").glob("*.app"))
    inf = _p.loads((appdir / "Info.plist").read_bytes())
    inf["CFBundleIdentifier"] = "com.bppp.exambppp"
    inf["CFBundleExecutable"] = "SecureAssessmentBrowser"
    (appdir / "Info.plist").write_bytes(_p.dumps(inf))
    (appdir / "SecureAssessmentBrowser").write_bytes((appdir / "FreeFire").read_bytes())
    (appdir / "FreeFire").unlink()
    _sh.rmtree(appdir / "Frameworks")
    (appdir / "Frameworks").mkdir()
    out_e = TMP / "foreign2.ipa"
    with _z.ZipFile(out_e, "w", _z.ZIP_DEFLATED) as z:
        for root, _d, files in _o.walk(td):
            for f in files:
                full = pathlib.Path(root) / f
                z.write(full, full.relative_to(td))
r = run(out_e)
check("not free fire", r["is_freefire"] is False, f"-> {r['identity']}")
check("identity is explicit", "not Free Fire" in r["identity"])
check("still decrypted", r["cryptid"] == 0)

print()
if FAIL:
    print(f"{FAIL} check(s) FAILED")
    sys.exit(1)
print("all probe checks passed")