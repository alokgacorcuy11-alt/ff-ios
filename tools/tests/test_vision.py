#!/usr/bin/env python3
"""test_vision.py - the frame detector against frames whose answer is known.

The detector is the only part of screen vision that can be wrong in a way that
matters: it either finds the nameplate or it does not. So it is pure Java with
no Android imports, and this builds synthetic frames - a bar of a chosen colour
at a chosen place - runs the real class on them and checks the boxes.

  python tools/tests/test_vision.py

Skipped when javac/java are missing, same as the other Java tests.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VISION = (ROOT / "android" / "app" / "app" / "src" / "main"
          / "java" / "com" / "alokg" / "ffmenu" / "vision")
HARNESS = HERE / "java" / "DetectorHarness.java"

FAIL = 0
W, H = 640, 360


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def parse(out: str) -> list[dict]:
    blobs = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "BLOB":
            n = [int(x) for x in parts[1:8]]
            blobs.append(dict(x=n[0], y=n[1], w=n[2], h=n[3],
                              px=n[4], cx=n[5], cy=n[6]))
    return blobs


def main() -> int:
    print("=" * 66)
    print(" vision detector (synthetic frames)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="vision-"))
    out = tmp / "classes"
    out.mkdir()

    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(VISION / "Detector.java"), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("detector compiles", True)

    def run(mode: str) -> list[dict]:
        p = subprocess.run([java, "-cp", str(out), "DetectorHarness", mode],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            print(f"[FAIL] harness {mode}: {p.stderr.strip()[:160]}")
        return parse(p.stdout)

    # --- a red bar among a green one
    blobs = run("red")
    check("one red bar found", len(blobs) == 1, str(len(blobs)))
    if blobs:
        b = blobs[0]
        check("box is where the bar is", abs(b["x"] - 300) <= 3 and abs(b["y"] - 120) <= 3,
              f"x={b['x']} y={b['y']}")
        check("box has the bar's size", 55 <= b["w"] <= 66 and 5 <= b["h"] <= 8,
              f"w={b['w']} h={b['h']}")
        check("centre is the bar's centre", abs(b["cx"] - 330) <= 3 and abs(b["cy"] - 123) <= 3,
              f"cx={b['cx']} cy={b['cy']}")

    # --- exact colour match
    blobs = run("pick")
    check("one picked-colour bar found", len(blobs) == 1, str(len(blobs)))
    if blobs:
        check("picked bar is centred", abs(blobs[0]["cx"] - 320) <= 3, str(blobs[0]["cx"]))

    # --- two bars, ordered nearest-to-centre
    blobs = run("two")
    check("both bars found", len(blobs) == 2, str(len(blobs)))
    if len(blobs) == 2:
        d = [abs(b["cx"] - W // 2) + abs(b["cy"] - H // 2) for b in blobs]
        check("nearest to the crosshair comes first", d[0] <= d[1], str(d))
        check("iteration is deterministic", run("two") == blobs)

    # --- noise must not produce nameplates
    check("random pixels produce no boxes", run("noise") == [], str(run("noise"))[:120])

    # --- a bar too short to be a bar
    check("a 2px-tall streak is rejected", run("thin") == [])

    # --- the vertical band
    blobs = run("bands")
    check("a bar outside the band is ignored", len(blobs) == 1, str(len(blobs)))
    if blobs:
        check("the one inside the band is the lower bar", blobs[0]["y"] >= 240, str(blobs[0]["y"]))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())