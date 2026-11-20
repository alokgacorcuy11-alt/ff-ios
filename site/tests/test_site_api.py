#!/usr/bin/env python3
"""test_site_api.py - runs the Pages Functions against a fake KV.

The real assertions live in tests/api_run.mjs: it imports the four route
modules, builds Requests the way the runtime does, and reads the Responses.
This wrapper exists only so run_all.py sees one Python entry and one exit code.
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
        print("[skip] node not on PATH - cannot run the order lifecycle")
        return 0
    r = subprocess.run([node, "tests/api_run.mjs"], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
