#!/usr/bin/env python3
"""patch_launch_trace.py - make the launch path say which step it reached.

Why this exists rather than another guess.

On the OPPO A7 the toast appears 25 seconds after tapping a game, which is the
timeout in patch_launcher.py, and its wording says the launch call did not
answer. So `BlackBoxCore.launchApk` is reached and does not return. What is not
known is which of its calls hangs, and there is no more information to be had
from the in-app log export: it covers roughly eleven seconds, the launch attempt
falls out of the buffer before anyone can export it, and the two logs that came
back both stop before the tap.

Guessing at a hang is how a day disappears. So every step of the path now
announces itself, and the step that never reports is the one that is stuck. A
short log is enough, because the useful line is whatever came last.

Note this is instrumentation, not a fix. Nothing about the behaviour changes.

  python3 container/patch_launch_trace.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = """        Intent launchIntentForPackage = getBPackageManager().getLaunchIntentForPackage(packageName, userId);
        if (launchIntentForPackage == null) {
            return false;
        }
        startActivity(launchIntentForPackage, userId);
        return true;
    }"""

NEW = """        // Every step announces itself. A hang in here is otherwise invisible:
        // the caller's timeout says only that the call did not return, and the
        // in-app log export is too short to reach the attempt. The step that
        // does not report is the step that is stuck.
        Slog.d(TAG, "launchApk: getLaunchIntentForPackage pkg=" + packageName + " user=" + userId);
        Intent launchIntentForPackage = getBPackageManager().getLaunchIntentForPackage(packageName, userId);
        Slog.d(TAG, "launchApk: intent=" + launchIntentForPackage);
        if (launchIntentForPackage == null) {
            Slog.w(TAG, "launchApk: no launch intent for " + packageName);
            return false;
        }
        Slog.d(TAG, "launchApk: startActivity pkg=" + packageName + " user=" + userId);
        startActivity(launchIntentForPackage, userId);
        Slog.d(TAG, "launchApk: startActivity returned pkg=" + packageName);
        return true;
    }"""

HEAD_OLD = """    public boolean launchApk(String packageName, int userId) {
        onBeforeMainLaunchApk(packageName, userId);"""

HEAD_NEW = """    public boolean launchApk(String packageName, int userId) {
        Slog.d(TAG, "launchApk: enter pkg=" + packageName + " user=" + userId);
        onBeforeMainLaunchApk(packageName, userId);
        Slog.d(TAG, "launchApk: onBeforeMainLaunchApk returned");"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = list(root.rglob("BlackBoxCore.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] BlackBoxCore.java not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    text = target.read_text(encoding="utf-8")
    if "launchApk: enter pkg=" in text:
        print("  [skip] already applied")
        return 0

    for old, new, label in ((HEAD_OLD, HEAD_NEW, "entry and first callback"),
                            (OLD, NEW, "intent lookup and startActivity")):
        if old not in text:
            raise SystemExit(
                f"  [FAIL] anchor not found: {label}.\n"
                "         The base changed. Read the file before patching it.")
        text = text.replace(old, new, 1)

    target.write_text(text, encoding="utf-8")
    print("  [ok  ] every step of launchApk now logs")
    print("  2 changes applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())