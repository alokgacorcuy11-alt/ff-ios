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
    check(len(rows.get("primary", [])) == 2 and set(rows["primary"]) == {"install", "mirror"},
          "INSTALL and MIRROR share the first row of their own",
          str(rows.get("primary")))

    # ---- 4. the style floor that caused the truncation is cleared --------
    check("btn.setMinWidth(0)" in java and "btn.setMinimumWidth(0)" in java,
          "every button clears the ~88dp style minimum width")
    check("btn.setTextSize(12)" in java, "and uses one text size, set in one place")

    # ---- 5. the root-only tools are folded away --------------------------
    check(re.search(r"rootTools\.setVisibility\(View\.GONE\)", java) is not None,
          "the root tools start collapsed, so they cannot crowd the layout")
    check("toggleRootTools" in java, "and a toggle reveals them")

    # ---- 6. the keyboard resizes instead of panning ----------------------
    check('android:windowSoftInputMode="adjustResize"' in xml,
          "the activity is adjustResize, so the keyboard does not hide the key field")

    # ---- 7. labels stay short enough to fit three across ----------------
    labels = {n: t for n, t in LABEL.findall(java) if n in {
        "connect", "stop", "feed", "panel", "vision", "install", "mirror", "activate"}}
    check(len(labels) >= 8, f"all {len(labels)} button labels found", str(sorted(labels)))
    long_ones = {n: t for n, t in labels.items() if len(t) > 12}
    check(not long_ones, "no button label is too long to fit a third of the width",
          str(long_ones) if long_ones else ", ".join(sorted(labels.values())))

    print()
    print(f"ui fit: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())