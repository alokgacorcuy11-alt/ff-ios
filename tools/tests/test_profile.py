#!/usr/bin/env python3
"""test_profile.py - the panel's saved settings, run for real.

The panel is what makes "set it up once" true, and it is the one part of the
app that a buyer interacts with on every session. If parsing drifts, the
symptom on a phone is silent: settings come back wrong, or not at all.

Profile.java deliberately has no Android imports and no JSON dependency, so it
can be compiled and run here with a plain JDK:

  python tools/tests/test_profile.py

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
PROFILE_SRC = (ROOT / "android" / "app" / "app" / "src" / "main"
               / "java" / "com" / "alokg" / "ffmenu" / "mirror" / "Profile.java")
HARNESS = HERE / "java" / "ProfileHarness.java"

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def main() -> int:
    print("=" * 66)
    print(" panel profile (plain java, no phone)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    if "import android" in PROFILE_SRC.read_text(encoding="utf-8"):
        check("Profile has no Android imports (so it can run here)", False)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="profile-"))
    out = tmp / "classes"
    out.mkdir()

    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(PROFILE_SRC), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("Profile compiles with no Android imports", True)

    def run(*args: str) -> str:
        p = subprocess.run([java, "-cp", str(out), "ProfileHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip()

    # --- a phone that has never saved anything
    check("an empty file is the default, not an error",
          run("text", "") == "v1 fov=60 on=", run("text", ""))
    check("nothing is on by default", run("enabled", "") == "")

    # --- a file from a real session
    saved = "v1 fov=45 on=1,5,16"
    check("a saved file round-trips unchanged", run("text", saved) == saved,
          run("text", saved))
    check("the fov comes back", run("fov", saved) == "45", run("fov", saved))
    check("the item list comes back in order", run("enabled", saved) == "1,5,16",
          run("enabled", saved))
    check("a saved item reads as on", run("on", saved, "5") == "1")
    check("an item that was never saved reads as off", run("on", saved, "2") == "0")

    # --- the same item saved twice must not appear twice
    check("a duplicated item is stored once",
          run("text", "v1 fov=60 on=9,9,9") == "v1 fov=60 on=9",
          run("text", "v1 fov=60 on=9,9,9"))

    # --- switching something off removes it from the file
    check("switching off takes it out of the file",
          run("set", saved, "5", "0") == "v1 fov=45 on=1,16",
          run("set", saved, "5", "0"))
    check("switching on adds it",
          run("set", "v1 fov=45 on=1", "7", "1") == "v1 fov=45 on=1,7",
          run("set", "v1 fov=45 on=1", "7", "1"))

    # --- nonsense must never be fatal: this file is on someone's phone
    for junk in ("", "!!!", "v1", "v1 fov", "v1 fov=", "=1", "on=", "v1 on=abc",
                 "v1 fov=abc on=0,-3", "garbage garbage garbage"):
        got = run("text", junk)
        ok = got.startswith("v1 fov=") and " on=" in got
        check(f"nonsense is survived: {junk!r}", ok, got)

    # --- an unknown version is read for what it contains, not thrown away
    check("a future version still yields its settings",
          run("fov", "v9 fov=35 on=2") == "35", run("fov", "v9 fov=35 on=2"))
    check("and its item list",
          run("enabled", "v9 fov=35 on=2,3") == "2,3", run("enabled", "v9 fov=35 on=2,3"))

    # --- the fov is clamped, because the module has an opinion too
    check("fov below the floor is lifted", run("clamp", "-5") == "10", run("clamp", "-5"))
    check("fov above the ceiling is brought down", run("clamp", "500") == "90",
          run("clamp", "500"))
    check("a sane fov is left alone", run("clamp", "55") == "55", run("clamp", "55"))

    # --- item 0 and negatives are not real features
    check("item 0 is ignored", run("enabled", "v1 fov=60 on=0,4") == "4",
          run("enabled", "v1 fov=60 on=0,4"))

    print()
    print(f"profile: {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
