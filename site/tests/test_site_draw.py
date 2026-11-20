#!/usr/bin/env python3
"""test_site_draw.py - runs public/qr.js in a VM and rebuilds the matrix.

The assertions live in tests/draw_run.mjs, because the thing under test is a
browser script: it is loaded with a recording 2D context and every fillRect it
emits is mapped back to the module it should represent. This wrapper gives
run_all.py a single exit code.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("[skip] node not on PATH - cannot run the canvas drawer")
        return 0
    r = subprocess.run([node, "tests/draw_run.mjs"], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
