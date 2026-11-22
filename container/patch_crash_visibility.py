#!/usr/bin/env python3
"""patch_crash_visibility.py - make a guest crash say why it crashed.

Observed on BlueStacks (Android 9), after the hidden-API fix let the guest start:

    I/Process (11204): Sending signal. PID: 11204 SIG: 9
    I/Process ( 7927): Sending signal. PID: 11204 SIG: 9
    I/ActivityManager: Process com.onebitmonochrome.blacksbbox:p0 (pid 11204)
        has died: fore TOP

The guest kills itself, and :black kills it too, roughly every three seconds,
eighteen times in a row and counting. SIG: 9, no stack trace anywhere.

`Process.killProcess(myPid())` on the self-kill is CrashMonitor's uncaught
exception handler. The handler does log the stack - Slog.e writes to logcat like
any other Log call - but it logs it *after* `handleCrash(...)`, and handleCrash
runs recovery strategies and file logging and is perfectly capable of throwing.
When it does, the handler aborts before its own log line, and Android's default
handler terminates the process with nothing written.

That is why the loop is silent: the one line that would name the exception never
gets reached, and the log shows only the kill.

The fix is ordering and nothing else. The stack trace is written to logcat as the
first statement of the handler, wrapped so it cannot itself fail, before anything
that might throw. The kill behaviour is deliberately left alone - the comment in
the original says killing avoids leaving the guest in black-screen limbo, and
changing that is a second fix which should not be bundled with this one.

  python3 container/patch_crash_visibility.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = """                public void uncaughtException(Thread thread, Throwable throwable) {
                    handleCrash("JavaException", thread, throwable);"""

NEW = """                public void uncaughtException(Thread thread, Throwable throwable) {
                    // Written first, before anything that can throw.
                    //
                    // This used to be logged further down, after handleCrash(),
                    // and handleCrash runs recovery strategies and file logging -
                    // so when it threw, the handler aborted before its own log
                    // line and the process was terminated with nothing written.
                    // The result was a guest that died every three seconds
                    // forever, with no reason anywhere.
                    try {
                        android.util.Log.e("CrashMonitor",
                                "GUEST CRASH: " + throwable.getClass().getName()
                                        + ": " + throwable.getMessage() + "\\n"
                                        + android.util.Log.getStackTraceString(throwable));
                    } catch (Throwable ignored) {
                    }
                    handleCrash("JavaException", thread, throwable);"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = list(root.rglob("CrashMonitor.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] CrashMonitor.java not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    text = target.read_text(encoding="utf-8")
    if "GUEST CRASH: " in text:
        print("  [skip] already applied")
        return 0
    if OLD not in text:
        raise SystemExit(
            "  [FAIL] anchor not found in CrashMonitor.java.\n"
            "         The base changed. Read the file before patching it.")
    target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("  [ok  ] the crash stack is logged before anything that can throw")
    print("  1 change applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())