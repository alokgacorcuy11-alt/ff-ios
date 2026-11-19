#!/usr/bin/env python3
"""test_footprint.py - the blacklist-surface scanner against synthetic APKs.

Two fixtures, and the difference between them is the whole point:

  dirty  - writes into com.dts.freefireth's data dir, patches a managed
           assembly, loads a dex at runtime, carries a high-entropy payload
  clean  - none of that

The scanner has to find every one of those in the dirty one, count the values
that are identical for every user of the file, and stay quiet on the clean one.

  python tools/tests/test_footprint.py
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = ROOT / "tools" / "footprint.py"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import footprint as fp  # noqa: E402
import test_verify_build as tv  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def noise(n: int, seed: int = 1) -> bytes:
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


DIRTY_STRINGS = [
    b"/Android/data/com.dts.freefireth/files",
    b"Assembly-CSharp-patch.bytes",
    b"DexClassLoader",
    b"android.permission.SYSTEM_ALERT_WINDOW",
    b"ffrtc_log.txt",
    b"localConfig.json",
]


def build(path: Path, strings: list[bytes], assets: dict[str, bytes], pkg: str) -> None:
    dex = b"\x64\x65\x78\x0a\x30\x33\x35\x00" + b"\x00" * 64
    for s in strings:
        dex += s + b"\x00"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", tv.build_axml(pkg))
        z.writestr("resources.arsc", tv.build_arsc("Tool"))
        z.writestr("classes.dex", dex)
        for n, b in assets.items():
            z.writestr(n, b)
        z.writestr("META-INF/CERT.RSA", b"\x30\x82\x01\x00" + b"\x00" * 64)


def main() -> int:
    print("=" * 66)
    print(" footprint scanner")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="footprint-"))

    dirty = tmp / "dirty.apk"
    build(dirty, DIRTY_STRINGS,
          {"assets/payload.bin": noise(20000),
           "assets/decoy.log": b"x" * 2048},
          "com.test.cheat")

    rep = fp.scan(dirty, "com.test.cheat")

    check("package read", rep["identity"]["package"] == "com.test.cheat")
    check("cross-app path found",
          "com.dts.freefireth" in rep["cross_app_paths"], str(rep["cross_app_paths"]))
    check("own package excluded from cross-app",
          "com.test.cheat" not in rep["cross_app_paths"])
    check("managed patch detected", "managed patch" in rep["mechanisms"],
          str(list(rep["mechanisms"])))
    check("dynamic dex detected", "dynamic dex" in rep["mechanisms"])
    check("deferred paths are named",
          "Assembly-CSharp-patch.bytes" in rep["payload_names"],
          str(rep["payload_names"]))

    kinds = {i["what"] for i in rep["shared_fingerprint"]}
    check("package is in the shared fingerprint", "package" in kinds)
    check("apk hash is in the shared fingerprint", "apk sha256" in kinds)
    check("asset hash is in the shared fingerprint", "assets/payload.bin" in kinds)
    check("small assets are skipped", "assets/decoy.log" not in kinds)

    check("high entropy payload flagged",
          rep["code_and_assets"]["assets/payload.bin"]["entropy"] > 7.5,
          str(rep["code_and_assets"]["assets/payload.bin"]["entropy"]))
    check("verdict has hard findings", len(rep["hard_findings"]) >= 2,
          str(rep["hard_findings"]))

    # --- a build that talks to nothing and loads nothing
    clean = tmp / "clean.apk"
    build(clean, [b"com.example.good"], {"assets/readme.txt": b"hello" * 2000},
          "com.example.good")
    rep2 = fp.scan(clean, "com.example.good")
    check("clean build has no hard findings", rep2["hard_findings"] == [],
          str(rep2["hard_findings"]))
    check("clean build still lists its package",
          {i["what"] for i in rep2["shared_fingerprint"]} ==
          {"package", "apk sha256", "assets/readme.txt"} or
          "package" in {i["what"] for i in rep2["shared_fingerprint"]})

    # --- CLI
    r = subprocess.run([sys.executable, str(TOOL), str(dirty),
                        "--self", "com.test.cheat", "--check"],
                       capture_output=True, text=True, timeout=120)
    check("cli --check exits 1 on a hard finding", r.returncode == 1,
          "rc=%d" % r.returncode)
    check("cli prints the verdict",
          "HARD" in r.stdout, r.stdout[-120:].replace("\n", " "))

    r = subprocess.run([sys.executable, str(TOOL), str(clean), "--check"],
                       capture_output=True, text=True, timeout=120)
    check("cli --check exits 0 on a clean build", r.returncode == 0,
          "rc=%d" % r.returncode)

    out = tmp / "rep.json"
    r = subprocess.run([sys.executable, str(TOOL), str(dirty), "--json", str(out)],
                       capture_output=True, text=True, timeout=120)
    check("cli writes json", out.is_file())
    if out.is_file():
        import json
        d = json.loads(out.read_text(encoding="utf-8"))
        check("json carries the mechanisms", "managed patch" in d["mechanisms"])

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