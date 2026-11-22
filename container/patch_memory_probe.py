#!/usr/bin/env python3
"""patch_memory_probe.py - answer the question the whole approach rests on.

Every design so far assumes the container can read the game's memory. That
assumption has never been tested, and it is the one that decides everything:

    the container starts the guest with the container's own uid
      -> kernel allows /proc/<guest pid>/mem only if the uid matches AND the
         target process is "dumpable"
      -> a game's anti-cheat calls prctl(PR_SET_DUMPABLE, 0) precisely so that
         nothing can attach to it
      -> if it does, the read is refused with EACCES no matter how the
         container is written

If the read is refused, no container of our own would help either: the answer
does not depend on who wrote the virtualisation layer. And if it is refused for
the outside process, the only way in is code inside the guest - which puts our
library back into the game's /proc/self/maps and gives up the one real
detection advantage this design had.

So before writing a line of container code, measure it.

The probe runs in the container's :black process - the one that spawned the
guest, same uid - and does three things in the order they can fail:

  1. reads /proc/<pid>/maps      (same ptrace permission as /mem)
  2. finds the guest's first executable range
  3. opens /proc/<pid>/mem, seeks there and reads four bytes

The log says which of the three worked. "READ OK" means the approach is alive.
"READ DENIED" means it is not, and the whole external design has to be
rethought before anything else is built on top of it.

  python3 container/patch_memory_probe.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

JAVA = '''package top.niunaijun.blackbox.utils;

import android.util.Log;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.RandomAccessFile;

/**
 * Can a process read the memory of the guest it started, when both share a uid?
 *
 * That is the question the whole no-root approach rests on, and it is not
 * answered by how the container is written. The kernel permits
 * /proc/<pid>/mem only when the uid matches and the target process is still
 * "dumpable"; a game's anti-cheat calls prctl(PR_SET_DUMPABLE, 0) so that
 * nothing can attach to it, and when it has, the read is refused with EACCES
 * regardless.
 *
 * The three steps are logged in the order they can fail, so a failure names
 * itself rather than leaving a guess.
 */
public final class MemoryProbe {

    private static final String TAG = "MemoryProbe";

    private MemoryProbe() {
    }

    public static void probe(final int pid) {
        if (pid <= 0) {
            Log.w(TAG, "no pid to probe");
            return;
        }
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException e) {
                    return;
                }
                try {
                    Log.d(TAG, "PROBE start: guest pid=" + pid
                            + " myUid=" + android.os.Process.myUid());

                    long start = firstExecutableRange(pid);
                    if (start == 0) {
                        Log.w(TAG, "PROBE: could not read /proc/" + pid
                                + "/maps - cannot even enumerate the guest");
                        return;
                    }
                    Log.d(TAG, "PROBE maps OK: first executable range 0x"
                            + Long.toHexString(start));

                    RandomAccessFile mem = null;
                    try {
                        mem = new RandomAccessFile("/proc/" + pid + "/mem", "r");
                        mem.seek(start);
                        byte[] buf = new byte[4];
                        int n = mem.read(buf);
                        StringBuilder hex = new StringBuilder();
                        for (int i = 0; i < n; i++) {
                            hex.append(String.format("%02x", buf[i]));
                        }
                        // This is the line that matters. If it appears, reading
                        // the game's memory from outside the game is possible.
                        Log.d(TAG, "PROBE READ OK: " + n + " bytes at 0x"
                                + Long.toHexString(start) + " = " + hex);
                    } catch (Throwable e) {
                        Log.w(TAG, "PROBE READ DENIED: " + e.getClass().getSimpleName()
                                + ": " + e.getMessage());
                    } finally {
                        if (mem != null) {
                            try {
                                mem.close();
                            } catch (Throwable ignored) {
                            }
                        }
                    }
                } catch (Throwable t) {
                    Log.w(TAG, "PROBE failed: " + t);
                }
            }
        }, "mem-probe");
        t.setDaemon(true);
        t.start();
    }

    private static long firstExecutableRange(int pid) {
        BufferedReader r = null;
        try {
            r = new BufferedReader(new FileReader("/proc/" + pid + "/maps"));
            String line;
            while ((line = r.readLine()) != null) {
                if (line.contains("r-xp")) {
                    String range = line.split(" ")[0];
                    return Long.parseLong(range.split("-")[0], 16);
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (r != null) {
                try {
                    r.close();
                } catch (Throwable ignored) {
                }
            }
        }
        return 0;
    }
}
'''

OLD = """            } else {
                app.pid = getPid(BlackBoxCore.getContext(), ProxyManifest.getProcessName(app.bpid));
            }"""

NEW = """            } else {
                app.pid = getPid(BlackBoxCore.getContext(), ProxyManifest.getProcessName(app.bpid));
                // One measurement that decides whether the whole approach can
                // work: can this process, which spawned the guest and shares its
                // uid, read the guest's memory? See MemoryProbe.
                top.niunaijun.blackbox.utils.MemoryProbe.probe(app.pid);
            }"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    utils = list(root.rglob("top/niunaijun/blackbox/utils"))
    if not utils:
        raise SystemExit(f"  [FAIL] Bcore utils not found under {root}")
    java_target = utils[0] / "MemoryProbe.java"

    hits = list(root.rglob("BProcessManagerService.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] BProcessManagerService.java not found under {root}")
    svc = hits[0]
    print(f"  target: {java_target}")
    print(f"  target: {svc}")

    changed = 0

    text = svc.read_text(encoding="utf-8")
    if "MemoryProbe.probe" in text:
        print("  [skip] probe already wired in")
    elif OLD not in text:
        raise SystemExit(
            "  [FAIL] anchor not found in BProcessManagerService.java.\n"
            "         The base changed. Read the file before patching it.")
    else:
        svc.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print("  [ok  ] probe wired into startProcessLocked")
        changed += 1

    if java_target.exists() and "PROBE READ OK" in java_target.read_text(encoding="utf-8"):
        print("  [skip] MemoryProbe.java already present")
    else:
        java_target.parent.mkdir(parents=True, exist_ok=True)
        java_target.write_text(JAVA, encoding="utf-8")
        print("  [ok  ] MemoryProbe.java written")
        changed += 1

    print(f"  {changed} change(s) applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())