#!/usr/bin/env python3
"""test_tier.py - the profile a phone gets, run for real.

Tier and Plan decide two things a buyer will notice immediately: whether the
capture is small enough for their phone to keep up, and whether the setup
checklist mentions the vendor screen their ROM actually has. Both are pure
functions, so both are executed here against the range of devices that exist
instead of being trusted because one phone looked fine.

  python tools/tests/test_tier.py

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
SRC = (ROOT / "android" / "app" / "app" / "src" / "main" / "java"
       / "com" / "alokg" / "ffmenu" / "device")
HARNESS = HERE / "java" / "TierHarness.java"

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def main() -> int:
    print("=" * 66)
    print(" compatibility engine (profile + per-brand plan, plain java)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    for name in ("Tier.java", "Plan.java"):
        text = (SRC / name).read_text(encoding="utf-8")
        if "import android" in text:
            check(f"{name} has no Android imports", False)
            return 1

    tmp = Path(tempfile.mkdtemp(prefix="tier-"))
    out = tmp / "classes"
    out.mkdir()
    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(SRC / "Tier.java"),
                        str(SRC / "Plan.java"), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("Tier and Plan compile with no Android imports", True)

    def run(*args: str) -> str:
        p = subprocess.run([java, "-cp", str(out), "TierHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip()

    def tier(api, ram, cores, khz):
        return run("tier", str(api), str(ram), str(cores), str(khz)).split()

    # --- the four levels, on devices that actually exist ---------------------
    t = tier(34, 8000, 8, 3_000_000)
    check("flagship -> FULL", t[0] == "FULL", " ".join(t))
    check("FULL captures 70% at 15 fps", t[1:4] == ["70", "15", "3"], " ".join(t))

    # OPPO A7 / Snapdragon 450: eight slow cores and 4 GB. Fast enough to run,
    # not fast enough to flood-fill a full-resolution frame.
    t = tier(27, 3800, 8, 1_800_000)
    check("Snapdragon 450 + 4 GB -> BALANCED", t[0] == "BALANCED", " ".join(t))
    check("BALANCED captures 50% at 12 fps", t[1:4] == ["50", "12", "4"], " ".join(t))

    t = tier(26, 1000, 4, 1_300_000)
    check("1 GB, four slow cores -> LIGHT", t[0] == "LIGHT", " ".join(t))
    check("LIGHT captures 35% at 8 fps", t[1:4] == ["35", "8", "6"], " ".join(t))

    t = tier(19, 4000, 8, 2_000_000)
    check("below Android 5 -> UNSUPPORTED", t[0] == "UNSUPPORTED", " ".join(t))

    # --- the two ceilings that are not about the score ----------------------
    t = tier(33, 3000, 8, 3_000_000)
    check("4 GB is enough, 3 GB is not -> BALANCED", t[0] == "BALANCED", " ".join(t))

    t = tier(33, 6000, 4, 3_000_000)
    check("four cores caps at BALANCED whatever the RAM", t[0] == "BALANCED", " ".join(t))

    # --- touch needs API 24, and says so instead of failing later -----------
    check("API 23 cannot dispatch a gesture", tier(23, 4000, 8, 2_000_000)[4] == "false")
    check("API 24 can", tier(24, 4000, 8, 2_000_000)[4] == "true")

    # --- capture sizes ------------------------------------------------------
    def cap(api, ram, cores, khz, w, h):
        a = run("capture", str(api), str(ram), str(cores), str(khz), str(w), str(h)).split()
        return int(a[0]), int(a[1])

    for (api, ram, cores, khz, w, h, label) in (
            (34, 8000, 8, 3_000_000, 2400, 1080, "1080p flagship landscape"),
            (27, 3800, 8, 1_800_000, 1600, 720, "720p OPPO landscape"),
            (26, 1000, 4, 1_300_000, 720, 1280, "low-end, portrait"),
            (34, 8000, 8, 3_000_000, 1440, 3200, "tall QHD portrait"),
    ):
        cw, ch = cap(api, ram, cores, khz, w, h)
        px = cw * ch
        check(f"{label}: {cw}x{ch} inside the budget",
              0 < cw <= w and 0 < ch <= h and px <= 1_300_000, f"{px:,} px")
        ratio_src = w / h
        ratio_cap = cw / ch
        check(f"{label}: aspect kept", abs(ratio_cap - ratio_src) < 0.02,
              f"{ratio_src:.3f} vs {ratio_cap:.3f}")

    # A lower tier must never produce a bigger capture than a higher one.
    big = cap(34, 8000, 8, 3_000_000, 1600, 720)
    mid = cap(27, 3800, 8, 1_800_000, 1600, 720)
    low = cap(26, 1000, 4, 1_300_000, 1600, 720)
    check("capture shrinks as the tier drops",
          big[0] * big[1] > mid[0] * mid[1] > low[0] * low[1],
          f"{big} > {mid} > {low}")

    # --- the checklist ------------------------------------------------------
    def plan(brand, api):
        return run("plan", brand, str(api)).split(",")

    p = plan("xiaomi", 34)
    check("MIUI on Android 14 gets all five steps",
          p == ["overlay", "notif", "battery", "autostart", "access(opt)"], str(p))

    p = plan("xiaomi", 30)
    check("below Android 13 there is no notification step",
          p == ["overlay", "battery", "autostart", "access(opt)"], str(p))

    p = plan("google", 30)
    check("stock Android gets no vendor autostart step",
          p == ["overlay", "battery", "access(opt)"], str(p))

    p = plan("oppo", 33)
    check("ColorOS gets a vendor autostart step",
          "autostart" in p and "notif" in p, str(p))

    p = plan("transsion", 33)
    check("Infinix/Tecno gets a step too", "autostart" in p, str(p))

    for brand in ("xiaomi", "oppo", "realme", "vivo", "samsung", "huawei",
                  "honor", "transsion", "oneplus", "google", "other"):
        p = plan(brand, 34)
        check(f"{brand}: overlay first, access last and optional",
              p[0] == "overlay" and p[-1] == "access(opt)", str(p))
        check(f"{brand}: battery step always present", "battery" in p, str(p))

    # --- brand routing ------------------------------------------------------
    for man, prop, want in (
            ("Xiaomi", "Redmi", "xiaomi"),
            ("Xiaomi", "POCO", "xiaomi"),
            ("OPPO", "OPPO", "oppo"),
            ("realme", "realme", "realme"),
            ("vivo", "iQOO", "vivo"),
            ("samsung", "samsung", "samsung"),
            ("HUAWEI", "HUAWEI", "huawei"),
            ("HONOR", "HONOR", "honor"),
            ("Infinix", "Infinix", "transsion"),
            ("Google", "google", "google"),
            ("OnePlus", "OnePlus", "oneplus"),
            ("Nothing", "Nothing", "other"),
    ):
        got = run("brand", man, prop)
        check(f"{man}/{prop} -> {want}", got == want, got)

    print()
    print(f"compatibility engine: {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
