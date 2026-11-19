#!/usr/bin/env python3
"""test_apk_triage.py - the APK inspector against a synthetic APK.

There is no aapt and no real APK on this machine, so the test builds one: a
binary AndroidManifest.xml (string pool + element chunks, UTF-8 pool variant),
a DEX with a URL and a keyword, a native library and a signature blob. Then it
checks that the parser gets the package id, sdk levels, permissions, component,
library hash, URL and keyword hits out of it.

The encoder here is deliberately independent of the parser - if both shared a
mistake the round trip would still pass, so the layout is also asserted
directly (chunk types, sizes, string pool flags).

  python tools/tests/test_apk_triage.py
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = ROOT / "tools" / "apk_triage.py"

sys.path.insert(0, str(ROOT / "tools"))
import apk_triage as at  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


# ---- a minimal AXML writer ---------------------------------------------------

def string_pool(strings: list[str]) -> bytes:
    data = b""
    offsets = []
    for s in strings:
        b = s.encode("utf-8")
        assert len(s) < 0x80 and len(b) < 0x80, "the test only uses short strings"
        offsets.append(len(data))
        data += bytes([len(s), len(b)]) + b + b"\x00"
    hsize = 28
    strings_start = hsize + 4 * len(strings)
    size = strings_start + len(data)
    out = struct.pack("<HHI", at.RES_STRING_POOL, hsize, size)
    out += struct.pack("<IIIII", len(strings), 0, 0x100, strings_start, 0)
    out += struct.pack("<%dI" % len(strings), *offsets)
    return out + data


def start_element(name: int, attrs: list[tuple[int, int, int]]) -> bytes:
    size = 16 + 20 + 20 * len(attrs)
    out = struct.pack("<HHIIIII", at.RES_XML_START_ELEMENT, 16, size,
                      1, 0xFFFFFFFF, 0xFFFFFFFF, name)
    out += struct.pack("<HHHHHH", 20, 20, len(attrs), 0, 0, 0)
    for aname, dtype, dval in attrs:
        out += struct.pack("<IIIHBBI", 0xFFFFFFFF, aname, 0xFFFFFFFF,
                           8, 0, dtype, dval)
    return out


def end_element(name: int) -> bytes:
    return struct.pack("<HHIIIII", at.RES_XML_END_ELEMENT, 16, 24,
                       1, 0xFFFFFFFF, 0xFFFFFFFF, name)


def namespace(prefix: int, uri: int) -> bytes:
    return struct.pack("<HHIIIII", at.RES_XML_START_NS, 16, 24,
                       1, 0xFFFFFFFF, prefix, uri)


def build_axml() -> bytes:
    names = [
        "android", "http://schemas.android.com/apk/res/android",
        "manifest", "package", "versionCode", "versionName",
        "uses-sdk", "minSdkVersion", "targetSdkVersion",
        "uses-permission", "name", "application", "label", "debuggable",
        "activity", "com.test.cheat", "7", "1.2", "26", "34",
        "android.permission.SYSTEM_ALERT_WINDOW", "Cheat", ".Main",
    ]
    idx = {s: i for i, s in enumerate(names)}

    body = b""
    body += namespace(idx["android"], idx["http://schemas.android.com/apk/res/android"])
    body += start_element(idx["manifest"], [
        (idx["package"], at.TYPE_STRING, idx["com.test.cheat"]),
        (idx["versionCode"], at.TYPE_INT_DEC, 7),
        (idx["versionName"], at.TYPE_STRING, idx["1.2"]),
    ])
    body += start_element(idx["uses-sdk"], [
        (idx["minSdkVersion"], at.TYPE_INT_DEC, 26),
        (idx["targetSdkVersion"], at.TYPE_INT_DEC, 34),
    ])
    body += end_element(idx["uses-sdk"])
    body += start_element(idx["uses-permission"], [
        (idx["name"], at.TYPE_STRING, idx["android.permission.SYSTEM_ALERT_WINDOW"]),
    ])
    body += end_element(idx["uses-permission"])
    body += start_element(idx["application"], [
        (idx["label"], at.TYPE_STRING, idx["Cheat"]),
        (idx["debuggable"], at.TYPE_INT_BOOLEAN, 1),
    ])
    body += start_element(idx["activity"], [
        (idx["name"], at.TYPE_STRING, idx[".Main"]),
    ])
    body += end_element(idx["activity"])
    body += end_element(idx["application"])
    body += end_element(idx["manifest"])
    body += struct.pack("<HHIIIII", at.RES_XML_END_NS, 16, 24,
                        1, 0xFFFFFFFF, idx["android"],
                        idx["http://schemas.android.com/apk/res/android"])

    pool = string_pool(names)
    total = 8 + len(pool) + len(body)
    return struct.pack("<HHI", at.RES_XML, 8, total) + pool + body


def build_apk(path: Path) -> None:
    axml = build_axml()
    # the layout assumptions the parser relies on, asserted directly
    assert struct.unpack_from("<H", axml, 0)[0] == 0x0003
    assert struct.unpack_from("<H", axml, 8)[0] == 0x0001
    assert struct.unpack_from("<I", axml, 24)[0] & 0x100

    dex = (b"\x64\x65\x78\x0a\x30\x33\x35\x00" + b"\x00" * 64
           + b"http://api.cheat.example/v1/blacklist\x00"
           + b"com.test.cheat.license.check\x00"
           + b"libfrida-gadget.so\x00")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", axml)
        z.writestr("classes.dex", dex)
        z.writestr("lib/arm64-v8a/libcheat.so", b"\x7fELF" + b"\x00" * 512)
        z.writestr("assets/config.json", b'{"x":1}')
        z.writestr("META-INF/CERT.RSA", b"\x30\x82\x01\x00" + b"\x00" * 64)


def main() -> int:
    print("=" * 66)
    print(" apk triage (synthetic APK)")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="apk-triage-"))
    apk = tmp / "test.apk"
    build_apk(apk)

    rep = at.triage(apk, [])
    m = rep["manifest"]

    check("package id", m.get("package") == "com.test.cheat", str(m.get("package")))
    check("versionCode", m.get("versionCode") == 7, str(m.get("versionCode")))
    check("versionName", m.get("versionName") == "1.2")
    check("minSdk", m.get("minSdk") == 26, str(m.get("minSdk")))
    check("targetSdk", m.get("targetSdk") == 34, str(m.get("targetSdk")))
    check("permission", "android.permission.SYSTEM_ALERT_WINDOW" in (m.get("permissions") or []))
    check("activity component", ".Main" in (m.get("components") or {}).get("activity", []))
    check("label", (m.get("app") or {}).get("label") == "Cheat")
    check("debuggable flag", (m.get("app") or {}).get("debuggable") is True)

    libs = {l["path"]: l for l in rep["native_libs"]}
    check("native lib found", "lib/arm64-v8a/libcheat.so" in libs)
    check("native lib hash is the entry's hash",
          libs.get("lib/arm64-v8a/libcheat.so", {}).get("sha256")
          == at.sha256(b"\x7fELF" + b"\x00" * 512))
    check("signature blob found",
          any(s["path"] == "META-INF/CERT.RSA" for s in rep["signature_files"]))
    check("asset listed", "assets/config.json" in rep["assets"])

    check("url extracted", "http://api.cheat.example/v1/blacklist" in rep["urls"],
          str(rep["urls"][:2]))
    check("keyword hit: blacklist", "blacklist" in rep["keyword_hits"])
    check("keyword hit: license", "license" in rep["keyword_hits"])
    check("keyword hit: frida", "frida" in rep["keyword_hits"])
    check("file hash is the file's hash", rep["sha256"] == at.sha256(apk.read_bytes()))

    # a broken manifest must be reported, not crash the tool
    bad = tmp / "bad.apk"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("AndroidManifest.xml", b"not axml at all")
        z.writestr("classes.dex", b"\x00" * 32)
    rep2 = at.triage(bad, [])
    check("a broken manifest is reported as an error",
          "error" in rep2["manifest"], str(rep2["manifest"].get("error"))[:60])
    check("the rest is still reported", rep2["entries"] == 2)

    # the CLI end to end
    r = subprocess.run([sys.executable, str(TOOL), str(apk)],
                       capture_output=True, text=True, timeout=60)
    check("cli exits 0", r.returncode == 0, r.stderr.strip()[:80])
    check("cli prints the package", "com.test.cheat" in r.stdout)
    check("cli prints the permission",
          "android.permission.SYSTEM_ALERT_WINDOW" in r.stdout)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
