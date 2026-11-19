#!/usr/bin/env python3
"""apk_triage.py - read an APK without aapt, adb or the Android SDK.

To imitate an existing cheat - or to work out what a blacklist could be keyed
on - the first questions are always the same:

  * what package id does it install as?
  * what certificate signs it?
  * what native libraries and assets does it ship?
  * what strings does it contain (URLs, endpoints, detection keywords)?

`aapt` lives in the SDK, which is not installed here, so this parses the binary
AndroidManifest.xml directly (AXML: string pool + element chunks) and scans the
DEX for printable strings.

  python tools/apk_triage.py app.apk
  python tools/apk_triage.py app.apk --json out.json
  python tools/apk_triage.py app.apk --grep blacklist,ban,detect,license

Everything is stdlib. A malformed chunk ends the parse with a message instead
of a traceback, because APKs in the wild are often patched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

# ---- AXML chunk types --------------------------------------------------------
RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_RESOURCE_MAP = 0x0180
RES_XML_START_NS = 0x0100
RES_XML_END_NS = 0x0101
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103

# Res_value data types
TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

DEFAULT_KEYWORDS = [
    "blacklist", "ban", "detect", "cheat", "hack", "inject", "frida", "magisk",
    "xposed", "root", "emulator", "virtual", "overlay", "screenshot", "record",
    "license", "auth", "hwid", "device_id", "signature", "integrity", "tamper",
    "native", "ptrace", "proc/", "maps",
]

URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{4,200}")
ASCII_RE = re.compile(rb"[\x20-\x7e]{6,}")


class AxmlError(Exception):
    pass


def _read_pool(data: bytes, off: int) -> list[str]:
    ctype, hsize, csize = struct.unpack_from("<HHI", data, off)
    if ctype != RES_STRING_POOL:
        raise AxmlError(f"expected a string pool, got chunk 0x{ctype:04x}")
    count, style_count, flags, strings_start, styles_start = \
        struct.unpack_from("<IIIII", data, off + 8)
    if count > 200_000:
        raise AxmlError(f"implausible string count {count}")
    offsets = struct.unpack_from("<%dI" % count, data, off + hsize)
    base = off + strings_start
    utf8 = bool(flags & 0x100)
    out: list[str] = []

    for o in offsets:
        p = base + o
        if utf8:
            n = data[p]
            p += 1
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p]
                p += 1
            m = data[p]
            p += 1
            if m & 0x80:
                m = ((m & 0x7F) << 8) | data[p]
                p += 1
            out.append(data[p:p + m].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_axml(data: bytes) -> dict:
    """Returns {'tag': '#root', 'attrs': {}, 'children': [...]}."""
    if len(data) < 8:
        raise AxmlError("shorter than a chunk header")
    ctype, hsize, total = struct.unpack_from("<HHI", data, 0)
    if ctype != RES_XML:
        raise AxmlError(f"not an AXML file (chunk 0x{ctype:04x})")
    total = min(total, len(data))

    strings: list[str] = []
    root = {"tag": "#root", "attrs": {}, "children": []}
    stack = [root]
    off = hsize

    while off + 8 <= total:
        ctype, chsize, csize = struct.unpack_from("<HHI", data, off)
        if csize < 8:
            raise AxmlError(f"zero-size chunk at 0x{off:x}")
        if ctype == RES_STRING_POOL:
            strings = _read_pool(data, off)
        elif ctype == RES_XML_START_ELEMENT:
            ns, name, astart, asize, acount = \
                struct.unpack_from("<IIHHH", data, off + 16)
            if not strings:
                raise AxmlError("element before the string pool")
            attrs = {}
            ap = off + 16 + astart
            for _ in range(acount):
                ans, aname, araw, tsize, res0, dtype, dval = \
                    struct.unpack_from("<IIIHBBI", data, ap)
                key = strings[aname] if aname < len(strings) else f"@{aname}"
                if dtype == TYPE_STRING and dval < len(strings):
                    val = strings[dval]
                elif dtype == TYPE_INT_BOOLEAN:
                    val = bool(dval)
                elif dtype == TYPE_INT_HEX:
                    val = dval
                elif dtype == TYPE_INT_DEC:
                    val = dval if dval < 0x80000000 else dval - 0x100000000
                elif dtype == TYPE_REFERENCE:
                    val = f"ref:0x{dval:08x}"
                else:
                    val = dval
                attrs[key] = val
                ap += 20
            node = {"tag": strings[name] if name < len(strings) else f"@{name}",
                    "attrs": attrs, "children": []}
            stack[-1]["children"].append(node)
            stack.append(node)
        elif ctype == RES_XML_END_ELEMENT:
            if len(stack) > 1:
                stack.pop()
        off += csize
    return root


def _find(node: dict, tag: str) -> dict | None:
    for c in node["children"]:
        if c["tag"] == tag:
            return c
        got = _find(c, tag)
        if got:
            return got
    return None


def _all(node: dict, tag: str) -> list[dict]:
    out = []
    for c in node["children"]:
        if c["tag"] == tag:
            out.append(c)
        out.extend(_all(c, tag))
    return out


def dex_strings(zf: zipfile.ZipFile, budget: int = 64 << 20) -> list[str]:
    found: list[str] = []
    used = 0
    for name in zf.namelist():
        if not (name.startswith("classes") and name.endswith(".dex")):
            continue
        if used > budget:
            break
        blob = zf.read(name)
        used += len(blob)
        found.extend(m.group().decode("ascii", "replace")
                     for m in ASCII_RE.finditer(blob))
    return found


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def triage(path: Path, extra_keywords: list[str]) -> dict:
    raw = path.read_bytes()
    rep: dict = {
        "file": str(path),
        "size": len(raw),
        "sha256": sha256(raw),
    }

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        rep["entries"] = len(names)

        # --- manifest
        man: dict = {}
        try:
            tree = parse_axml(zf.read("AndroidManifest.xml"))
            m = _find(tree, "manifest")
            if not m:
                raise AxmlError("no <manifest> element")
            man["package"] = m["attrs"].get("package")
            man["versionName"] = m["attrs"].get("versionName")
            man["versionCode"] = m["attrs"].get("versionCode")

            sdk = _find(m, "uses-sdk")
            if sdk:
                man["minSdk"] = sdk["attrs"].get("minSdkVersion")
                man["targetSdk"] = sdk["attrs"].get("targetSdkVersion")

            man["permissions"] = sorted(
                p["attrs"].get("name") for p in _all(m, "uses-permission")
                if p["attrs"].get("name"))

            app = _find(m, "application")
            if app:
                man["app"] = {k: v for k, v in app["attrs"].items()
                              if k in ("label", "name", "debuggable",
                                       "allowBackup", "extractNativeLibs",
                                       "usesCleartextTraffic", "theme")}
                comps = {}
                for tag in ("activity", "activity-alias", "service",
                            "receiver", "provider"):
                    got = [c["attrs"].get("name") for c in _all(app, tag)
                           if c["attrs"].get("name")]
                    if got:
                        comps[tag] = got
                man["components"] = comps
        except (AxmlError, KeyError, struct.error) as e:
            man["error"] = str(e)
        rep["manifest"] = man

        # --- native libraries
        libs = []
        for n in names:
            if n.startswith("lib/") and n.endswith(".so"):
                blob = zf.read(n)
                libs.append({"path": n, "size": len(blob),
                             "sha256": sha256(blob)})
        rep["native_libs"] = libs

        # --- assets worth a look
        rep["assets"] = [n for n in names
                         if n.startswith("assets/") and not n.endswith("/")][:40]

        # --- signature blobs
        sigs = [n for n in names if n.upper().startswith("META-INF/")
                and n.upper().endswith((".RSA", ".DSA", ".EC"))]
        rep["signature_files"] = [{"path": n, "sha256": sha256(zf.read(n))}
                                  for n in sigs]

        # --- strings
        strings = dex_strings(zf)

    keywords = DEFAULT_KEYWORDS + [k for k in extra_keywords if k]
    hits: dict[str, list[str]] = {}
    for kw in keywords:
        low = kw.lower()
        got = sorted({s for s in strings if low in s.lower()})[:12]
        if got:
            hits[kw] = got
    rep["keyword_hits"] = hits

    urls = sorted({u for s in strings for u in URL_RE.findall(s)})
    rep["urls"] = urls[:60]

    keytool = shutil.which("keytool")
    if keytool:
        try:
            r = subprocess.run([keytool, "-printcert", "-jarfile", str(path)],
                               capture_output=True, text=True, timeout=30)
            cert = {}
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                for key in ("Owner:", "Issuer:", "SHA256:", "SHA1:"):
                    if line.startswith(key):
                        cert.setdefault(key[:-1].lower(), line[len(key):].strip())
            rep["certificate"] = cert
        except (subprocess.SubprocessError, OSError) as e:
            rep["certificate"] = {"error": str(e)}

    return rep


def report(rep: dict) -> None:
    m = rep.get("manifest", {})
    print(f"file      {rep['file']}")
    print(f"size      {rep['size']:,} bytes")
    print(f"sha256    {rep['sha256']}")
    print(f"entries   {rep['entries']}")
    print()
    print(f"package   {m.get('package')}")
    print(f"version   {m.get('versionName')} ({m.get('versionCode')})")
    print(f"sdk       min {m.get('minSdk')}  target {m.get('targetSdk')}")
    if m.get("error"):
        print(f"manifest  ERROR {m['error']}")

    app = m.get("app") or {}
    if app:
        print(f"app       {app.get('label')}  debuggable={app.get('debuggable')}"
              f"  cleartext={app.get('usesCleartextTraffic')}")
    comps = m.get("components") or {}
    for kind, items in comps.items():
        for it in items:
            print(f"{kind:<9} {it}")

    perms = m.get("permissions") or []
    if perms:
        print("\npermissions")
        for p in perms:
            print(f"  {p}")

    if rep.get("native_libs"):
        print("\nnative libs")
        for lib in rep["native_libs"]:
            print(f"  {lib['path']}  {lib['size']:,}  {lib['sha256'][:16]}")

    if rep.get("signature_files"):
        print("\nsignature")
        for s in rep["signature_files"]:
            print(f"  {s['path']}  {s['sha256'][:32]}")
        cert = rep.get("certificate") or {}
        for k in ("owner", "issuer", "sha256", "sha1"):
            if cert.get(k):
                print(f"  {k:<7} {cert[k]}")

    hits = rep.get("keyword_hits") or {}
    if hits:
        print("\nkeyword hits")
        for kw, vals in sorted(hits.items()):
            print(f"  {kw}")
            for v in vals[:6]:
                print(f"    {v[:160]}")

    urls = rep.get("urls") or []
    if urls:
        print("\nurls")
        for u in urls:
            print(f"  {u}")

    assets = rep.get("assets") or []
    if assets:
        print("\nassets")
        for a in assets[:20]:
            print(f"  {a}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="inspect an APK with no SDK")
    ap.add_argument("apk", type=Path)
    ap.add_argument("--json", type=Path, help="also write the raw report here")
    ap.add_argument("--grep", default="", help="extra keywords, comma separated")
    args = ap.parse_args(argv)

    if not args.apk.is_file():
        print(f"[!] not a file: {args.apk}", file=sys.stderr)
        return 2
    try:
        rep = triage(args.apk, [k.strip() for k in args.grep.split(",") if k.strip()])
    except zipfile.BadZipFile:
        print("[!] not a zip / APK", file=sys.stderr)
        return 2

    report(rep)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
