#!/usr/bin/env python3
"""patch_repositories.py - stop a flaky mirror from breaking the build.

The base's settings.gradle lists five Aliyun mirrors before google() and
mavenCentral(). Gradle asks repositories in the order they are declared, so every
dependency is requested from maven.aliyun.com first. When that mirror answers
502, Gradle disables it - and the build dies even though the same artifacts are
sitting in Google's repository and in Maven Central.

Observed:

    > Could not resolve androidx.lifecycle:lifecycle-runtime-ktx:2.6.2.
       > Repository maven3 is disabled due to earlier error below:
          > Could not resolve org.jetbrains.kotlinx:kotlinx-coroutines-android:1.6.4.
             > Could not GET 'https://maven.aliyun.com/repository/google/...'
               Received status code 502 from server: Bad Gateway
       > There are 17 more failures with identical causes.

Nothing about the source changed. The fix is order: Google and Maven Central
first, the mirrors kept below as fallbacks for anything they do not carry. The
mirrors still work when they work; they are simply no longer on the critical
path for artifacts that Google already serves.

  python3 container/patch_repositories.py <base-dir>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BLOCK = re.compile(r"^(\s*)repositories\s*\{\s*$")


MARK = "// Added: google() and mavenCentral() first, because the mirrors below are"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = [p for p in root.rglob("settings.gradle") if "build" not in p.parts]
    if not hits:
        raise SystemExit(f"  [FAIL] settings.gradle not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    original = target.read_text(encoding="utf-8")
    if MARK in original:
        print("  [skip] already applied")
        return 0

    lines = original.splitlines()
    out = []
    blocks = 0
    for line in lines:
        out.append(line)
        m = BLOCK.match(line)
        if not m:
            continue
        indent = m.group(1) + "    "
        out.append(f"{indent}{MARK}")
        out.append(f"{indent}// tried in order, and a mirror that answers 502 disables")
        out.append(f"{indent}// itself and fails the build, even when the artifact")
        out.append(f"{indent}// exists here.")
        out.append(f"{indent}google()")
        out.append(f"{indent}mavenCentral()")
        blocks += 1

    if blocks == 0:
        raise SystemExit("  [FAIL] no repositories block found - the base changed.")

    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  [ok  ] google() and mavenCentral() placed first in {blocks} block(s)")

    # Show the result, so the change is visible rather than assumed.
    for i, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s in ("google()", "mavenCentral()", "repositories {") or "aliyun" in s:
            print(f"      {i:>3}: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())