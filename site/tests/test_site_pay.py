#!/usr/bin/env python3
"""test_site_pay.py - runs tests/pay_run.mjs against a local fake gateway.

The assertions live in the .mjs: it starts a real HTTP server, calls the real
present() from functions/_lib/pay.js, and checks that a flat qr_string, a nested
one, a checkout_url, an unknown nesting, an HTTP 500 and a non-JSON body each
lead to the right decision. This wrapper gives run_all.py one exit code.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent


def main() -> int:
    if not shutil.which("node"):
        print("[skip] node not on PATH - cannot run the gateway adapter")
        return 0
    r = subprocess.run([ "node", "tests/pay_run.mjs"], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    print((r.stdout or "").rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
