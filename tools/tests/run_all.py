#!/usr/bin/env python3
"""run_all.py - the whole offline test suite, one command.

Covers everything that can be verified without macOS, a device or a real IPA:

  1. tools/selftest.py            proxy, panel, control state, menu api, rules, protobuf
  2. tests/test_ipa_probe.py      IPA classification
  3. tests/test_package_ipa.py    dylib -> IPA packaging, load-command injection
  4. tests/test_metadata.py       offline IL2CPP name harvesting

  python tools/tests/run_all.py
  python tools/tests/run_all.py --fail-fast
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

SUITES = [
    ("selftest      ", ROOT / "tools" / "selftest.py"),
    ("metadata_hunt ", HERE / "test_metadata.py"),
    ("math          ", HERE / "test_math.py"),
    # compiles and drives android/daemon/memd.c on a Linux host; skips on Windows
    ("android daemon", HERE / "test_memd.py"),
    # pins the laptop -> phone packet format against LanConfig.java and runs
    # a real sender/listener pair over UDP
    ("lan config    ", HERE / "test_lan_config.py"),
    # compiles the app's Java against API stubs; no Android SDK needed
    ("app java      ", HERE / "test_java.py"),
    # parses a synthetic APK: binary manifest, DEX strings, libs, signature
    ("apk triage    ", HERE / "test_apk_triage.py"),
    # the publish gate: a built APK must carry the identity it was asked for
    ("verify build  ", HERE / "test_verify_build.py"),
    # what a blacklist could key on: cross-app writes, patch markers, hashes
    ("footprint     ", HERE / "test_footprint.py"),
    # the per-install container: uniqueness, tamper, manifest
    ("forge         ", HERE / "test_payload_forge.py"),
    # python forge <-> java transport, spoken over a real socket
    ("feed          ", HERE / "test_feed.py"),
]

# The shell suites are not Python, so they need their own runner. They are
# skipped with a message rather than failed when bash is unavailable (a bare
# Windows shell), because CI has it and that is what matters.
SHELL_SUITES = []


def run_shell(label, path, verbose):
    import shutil
    bash = shutil.which("bash")
    if not bash:
        print(f"[SKIP] {label} bash not available")
        return None
    if not path.exists():
        print(f"[SKIP] {label} {path} missing")
        return None
    started = time.time()
    # Pass the basename and set cwd. A Windows path ("C:\...") is not valid for
    # the bash that ships with Windows (WSL), while a relative name is valid
    # everywhere - including the ubuntu runner, where CI actually uses this.
    r = subprocess.run([bash, path.name], cwd=str(path.parent),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dt = time.time() - started
    out = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0
    # the shell suite prints its own "[ok  ]" lines
    checks = out.count("[ok  ]")
    if verbose or not ok:
        print(f"\n----- {label.strip()} -----")
        print(out.rstrip())
    else:
        print(f"[PASS] {label} {checks:>3} checks  {dt:5.2f}s")
    if not ok:
        fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[FAIL]")]
        for line in fails[:12]:
            annotate(f"{label.strip()}: {line}", "shell suite")
        if not fails:
            annotate(f"{label.strip()} failed with no [FAIL] lines (exit "
                     f"{r.returncode}); tail: "
                     f"{' | '.join(out.strip().splitlines()[-6:])}", "shell suite")
    return (label.strip(), ok, checks, dt)


def annotate(msg, title="run_all"):
    """Emit a GitHub ::error:: annotation when running in Actions.

    Why: a failing run on a public repo cannot be read back through the API
    without a token, but annotations attached to a check run CAN be. So the
    failing check names are published there, and the reason for a red build is
    readable from outside - which is exactly how the macos runner problem was
    found (`The configuration 'macos-14-arm64-us-default' is not supported`).
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    safe = (msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))
    print(f"::error title={title}::{safe}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    total_fail = 0
    results = []
    t0 = time.time()

    for label, path in SUITES:
        if not path.exists():
            print(f"[SKIP] {label} {path} missing")
            continue
        started = time.time()
        r = subprocess.run([sys.executable, str(path)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        dt = time.time() - started
        out = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0

        checks = out.count("[ok  ]")
        fails = out.count("[FAIL]")
        results.append((label.strip(), ok, checks, fails, dt))

        if a.verbose or not ok:
            print(f"\n----- {label.strip()} -----")
            print(out.rstrip())
        else:
            if ok:
                print(f"[PASS] {label} {checks:>3} checks  {dt:5.2f}s")
        if not ok:
            total_fail += 1
            # publish the failing check names, not just "suite failed"
            fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[FAIL]")]
            for line in fails[:12]:
                annotate(f"{label.strip()}: {line}", "offline suite")
            if not fails:
                annotate(f"{label.strip()} failed with no [FAIL] lines "
                         f"(exit {r.returncode}); last output: "
                         f"{out.strip().splitlines()[-1] if out.strip() else '<empty>'}",
                         "offline suite")
            if a.fail_fast:
                break

    for label, path in SHELL_SUITES:
        res = run_shell(label, path, a.verbose)
        if res is None:
            continue
        results.append((res[0], res[1], res[2], 0, res[3]))
        if not res[1]:
            total_fail += 1
            annotate(f"{res[0]}: shell suite failed", "shell suite")
            if a.fail_fast:
                break

    print()
    print("=" * 62)
    print(f"{'suite':<16}{'result':<8}{'checks':>8}{'time':>9}")
    print("-" * 62)
    for label, ok, checks, fails, dt in results:
        print(f"{label:<16}{'PASS' if ok else 'FAIL':<8}{checks:>8}{dt:>8.2f}s")
    print("-" * 62)
    print(f"total {sum(r[2] for r in results)} checks in {time.time() - t0:.2f}s")
    print("=" * 62)

    if total_fail:
        print(f"\n{total_fail} suite(s) FAILED")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())