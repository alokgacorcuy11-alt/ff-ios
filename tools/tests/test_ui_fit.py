#!/usr/bin/env python3
"""test_ui_fit.py - the app's screen must fit on a phone.

The report was "the buttons are cut off, it does not fit". Three things caused
it, and each one is cheap to reintroduce by accident:

  1. five buttons in one weighted row - the material button style has an ~88dp
     minimum width, so at 360dp across, labels came out as "CONNEC...";
  2. a fixed vertical column with no way to scroll - on a short screen, or with
     the system font turned up, the last row was pushed off the bottom;
  3. no windowSoftInputMode - typing a key panned the window instead of resizing
     it, so the top of the screen went out of view.

This does not render anything; it asserts the structural properties that make
those three impossible. The layout's real proof is a phone, but this is what
stops a future edit from quietly undoing it.

  python tools/tests/test_ui_fit.py
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = ROOT / "android" / "app" / "app" / "src" / "main"
MAIN = SRC / "java" / "com" / "alokg" / "ffmenu" / "MainActivity.java"
MODULE_SRC = SRC / "java" / "com" / "alokg" / "ffmenu" / "mirror" / "Module.java"
MANIFEST = SRC / "AndroidManifest.xml"

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def check(cond: bool, msg: str, extra: str = "") -> None:
    (ok if cond else bad)(msg + (f"  {extra}" if extra else ""))


CELL = re.compile(r"(\w+)\.addView\((\w+),\s*cell\(\)\)")
LABEL = re.compile(r'(\w+)\.setText\("([^"]*)"\)')


def main() -> int:
    if not MAIN.exists():
        bad(f"{MAIN} is missing")
        return 1
    java = MAIN.read_text(encoding="utf-8")
    xml = MANIFEST.read_text(encoding="utf-8")

    # ---- 1. it scrolls, so nothing can be pushed off the screen ----------
    check("new ScrollView(this)" in java, "the screen lives inside a ScrollView")
    check("setFillViewport(true)" in java,
          "the ScrollView fills its viewport, so the log stretches instead of leaving a gap")
    check(re.search(r"scroller\.addView\(column\)", java) is not None
          and "setContentView(scroller)" in java,
          "setContentView is given the scroller, not a bare LinearLayout")

    # ---- 2. the log yields space instead of pushing the controls ----------
    log_lp = re.search(
        r"out\.setMinimumHeight\(dp\((\d+)\)\);\s*"
        r"LinearLayout\.LayoutParams lp = new LinearLayout\.LayoutParams\(\s*"
        r"LinearLayout\.LayoutParams\.MATCH_PARENT,\s*0,\s*1f\)", java)
    check(log_lp is not None,
          "the log is height 0 + weight 1 with a minimum height (it gives up space first)")
    if log_lp:
        check(int(log_lp.group(1)) >= 100,
              f"the log keeps at least {log_lp.group(1)}dp", log_lp.group(1) + "dp")

    # ---- 3. no row is so crowded that a label gets ellipsised ------------
    rows: dict[str, list[str]] = {}
    for container, child in CELL.findall(java):
        rows.setdefault(container, []).append(child)
    crowded = {c: k for c, k in rows.items() if len(k) > 3}
    check(not crowded, "no horizontal row holds more than three buttons",
          str(crowded) if crowded else ", ".join(f"{c}={len(k)}" for c, k in rows.items()))
    check(len(rows.get("primary", [])) == 2 and set(rows["primary"]) == {"toggle", "mirror"},
          "the on/off switch and MIRROR share the first row of their own",
          str(rows.get("primary")))

    # ---- 4. the style floor that caused the truncation is cleared --------
    check("btn.setMinWidth(0)" in java and "btn.setMinimumWidth(0)" in java,
          "every button clears the ~88dp style minimum width")
    check("btn.setTextSize(12)" in java, "and uses one text size, set in one place")

    # ---- 5. nothing on the screen is a leftover from the research build --
    #
    # These are not layout rules, they are install rules: an accessibility
    # service plus screen capture is what made Play Protect refuse the APK
    # outright. A screen that only has the three buttons a buyer uses cannot
    # drift back into declaring them.
    for gone in ("rootTools", "toggleRootTools", "Vision", "Root.", "MemClient"):
        check(gone not in java, f"the buyer screen carries no {gone}")
    check("BIND_ACCESSIBILITY_SERVICE" not in xml and "AccessibilityService" not in xml,
          "the manifest declares no accessibility service")
    check("MEDIA_PROJECTION" not in xml, "and no screen capture")
    check(xml.count("<uses-permission") == 2,
          "exactly two permissions are requested",
          f"{xml.count('<uses-permission')}")

    # ---- 6. the keyboard resizes instead of panning ----------------------
    check('android:windowSoftInputMode="adjustResize"' in xml,
          "the activity is adjustResize, so the keyboard does not hide the key field")

    # ---- 7. labels stay short enough to fit three across ----------------
    labels = {n: t for n, t in LABEL.findall(java) if n in {
        "toggle", "mirror", "activate"}}
    check(len(labels) == 3, f"all {len(labels)} fixed labels found", str(sorted(labels)))
    # autoclean's label is not a literal: it says which state the setting is in,
    # so it is checked for membership in the one styling loop instead.
    check("autoclean" in java and r"new Button[]{toggle, mirror, activate, autoclean}" in java,
          "and the auto-clean switch is styled with the rest")
    long_ones = {n: t for n, t in labels.items() if len(t) > 12}
    check(not long_ones, "no button label is too long to fit a third of the width",
          str(long_ones) if long_ones else ", ".join(sorted(labels.values())))

    # ---- 8. "off" has to actually put the phone back ---------------------
    #
    # What the app changes on the phone is one file. If this ever stops being
    # undone, the buyer is left with a modified game folder and no way to know
    # - which is the exact complaint this screen exists to answer.
    check("Module.remove" in java, "the app can take the module back out")
    check("isFinishing()" in java and "onDestroy" in java,
          "leaving the app removes it, but a rebuild or a low-memory teardown does not")
    check("autoClean" in java and "CLEAN_FILE" in java,
          "and that behaviour is a setting the buyer can see and flip")
    check("BERSIH" in java,
          "the screen says out loud whether anything is left on the phone")
    check("releasePersistableUriPermission" in MODULE_SRC.read_text(encoding="utf-8"),
          "the folder grant is handed back, not just the file deleted")
    check("writeOwn(KEY_FILE" in java and "KEY_FILE" in java,
          "the key is stored, so it is pasted once and not once per session")

    print()
    print(f"ui fit: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())