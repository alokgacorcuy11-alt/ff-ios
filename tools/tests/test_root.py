#!/usr/bin/env python3
"""test_root.py - the verdict the root tier shows a buyer, run for real.

Device and Guide have no Android imports, so the decision that says "your phone
can be unlocked" or "your model is locked" is compiled and executed here. That
decision is worth testing because it is the only thing standing between a buyer
and an evening wasted on a phone that never could be unlocked.

  python tools/tests/test_root.py

Skipped with a clear message when there is no JDK on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = (ROOT / "android" / "app" / "rootapp" / "src" / "main" / "java"
       / "com" / "alokg" / "roottools")
HARNESS = HERE / "java" / "DeviceHarness.java"

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def main() -> int:
    print("=" * 66)
    print(" root tier (device verdict + guide, plain java)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    for name in ("Device.java", "Guide.java"):
        text = (SRC / name).read_text(encoding="utf-8")
        if "import android" in text:
            check(f"{name} has no Android imports", False)
            return 1

    tmp = Path(tempfile.mkdtemp(prefix="root-"))
    out = tmp / "classes"
    out.mkdir()
    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(SRC / "Device.java"),
                        str(SRC / "Guide.java"), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("Device and Guide compile with no Android imports", True)

    def run(*args: str) -> str:
        p = subprocess.run([java, "-cp", str(out), "DeviceHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip()

    # --- the four states a real ROM can be in ------------------------------
    out_ = run("verdict", "1", "1", "green")
    check("vendor says yes, still locked -> path is open",
          out_.startswith("vendor MENGIZINKAN") and "LOCKED" in out_, out_.splitlines()[0])

    out_ = run("verdict", "0", "1", "green")
    check("vendor says no, still locked -> path is closed",
          out_.startswith("vendor MENGUNCI") and "LOCKED" in out_, out_.splitlines()[0])

    out_ = run("verdict", "", "1", "green")
    check("a ROM that says nothing is not turned into a no",
          "tidak menyebutkan" in out_, out_.splitlines()[0])

    out_ = run("verdict", "", "0", "orange")
    check("flash.locked=0 means unlocked, whatever the vendor flag says",
          "SUDAH TERBUKA" in out_ and "UNLOCKED" in out_, out_.splitlines()[0])

    out_ = run("verdict", "0", "", "orange")
    check("verifiedbootstate=orange also means unlocked",
          "SUDAH TERBUKA" in out_, out_.splitlines()[0])

    out_ = run("verdict", "0", "1", "green")
    check("a plain retail phone is not called unlocked", "LOCKED" in out_)

    # --- brand routing: the guide must match the phone ---------------------
    for man, want in (("Xiaomi", "xiaomi"), ("Redmi", "xiaomi"), ("POCO", "xiaomi"),
                      ("samsung", "samsung"), ("Google", "google"),
                      ("OnePlus", "oneplus"), ("OPPO", "locked"),
                      ("realme", "locked"), ("vivo", "locked"),
                      ("Nothing", "other")):
        got = run("brand", man)
        check(f"{man} -> {want}", got == want, got)

    # --- every brand has something to read ---------------------------------
    for brand in ("xiaomi", "samsung", "google", "oneplus", "locked", "other"):
        check(f"guide for {brand} is written", run("guide", brand) == "LONG")

    # --- the honest parts are actually in the text -------------------------
    guide = run("afterroot")
    for needle in ("magisk --denylist add com.dts.freefireth", "Zygisk"):
        check(f"after-root instructions mention {needle[:34]}", needle in guide)

    locked = subprocess.run([java, "-cp", str(out), "DeviceHarness", "guidebody", "locked"],
                            capture_output=True, text=True).stdout
    check("the locked-brand guide says the truth instead of a maybe",
          "TIDAK punya" in locked and "OEM unlocking" in locked)

    print()
    print(f"root tier: {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())