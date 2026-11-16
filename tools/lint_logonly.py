#!/usr/bin/env python3
"""Find variables whose only use is inside FFLOG(...).

Why this exists: `make RELEASE=1` compiles FFLOG out (Core/Obf.h turns it into
`do { } while (0)`). A variable that is only ever printed by a log call is
therefore write-only in the release build, and clang rejects that with
-Wunused-but-set-variable - as an ERROR, because Theos builds with -Werror. The
first CI build died on exactly this, in Core/Game.m, on a counter that only the
log ever read.

Run:  python tools/lint_logonly.py [--verbose]
Exit: 0 clean, 1 findings
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"

# a local declaration, with or without an initialiser
DECL = re.compile(
    r"^\s*(?:static\s+)?(?:const\s+)?"
    r"(?:unsigned\s+|signed\s+|long\s+|short\s+)*"
    r"(?:NSInteger|NSUInteger|CGFloat|BOOL|int32_t|uint32_t|int64_t|uint64_t|"
    r"size_t|double|float|int|uint8_t|uint16_t|char|"
    r"NSArray|NSMutableArray|NSString|NSDictionary|NSMutableDictionary|"
    r"Il2CppObject|Il2CppClass|FieldInfo|Vec3|Quat|Mat4|CGPoint|CGSize|CGRect|id)"
    r"\s*\*?\s*([A-Za-z_]\w*)\s*(?:=|;)")

SKIP = {"i", "j", "k", "n", "x", "y", "z", "e", "r", "s", "p", "v", "q", "f",
        "a", "b", "c", "d", "m", "t", "w", "h", "lo", "hi", "ok", "ret"}


def strip_comments(text: str) -> str:
    """Blank out comments AND string/char literals, keeping line structure.

    Literals matter here: a variable name appearing inside a format string
    ("  tried: %@") counted as a read and hid a real finding - the same name
    happened to appear in an unrelated report string elsewhere in the file.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c in "\"'":
            q = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q or text[j] == "\n":
                    break
                j += 1
            j = min(j + 1, n)
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def fflog_spans(lines):
    """Line indices that are part of an FFLOG(...) call, paren-balanced."""
    spans = set()
    for idx, line in enumerate(lines):
        if "FFLOG(" not in line:
            continue
        depth = 0
        started = False
        for j in range(idx, min(idx + 12, len(lines))):
            for ch in lines[j]:
                if ch == "(":
                    depth += 1
                    started = True
                elif ch == ")":
                    depth -= 1
            spans.add(j)
            if started and depth <= 0:
                break
    return spans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    findings = []
    checked = 0
    files = sorted(list(NATIVE.rglob("*.m")) + list(NATIVE.rglob("*.x")))
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(raw)
        lines = code.splitlines()
        log_lines = fflog_spans(lines)

        declared = {}
        for idx, line in enumerate(lines, 1):
            m = DECL.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in SKIP or name.startswith("kFF") or len(name) < 2:
                continue
            declared.setdefault(name, idx)

        for name, decl_line in declared.items():
            checked += 1
            word = re.compile(rf"\b{re.escape(name)}\b")
            # A write is not a read. `tried += 1;` is how the CI failure slipped
            # past the first version of this linter: it counted the increment as
            # a use, so a counter that only the log ever READ looked fine.
            write_only = re.compile(
                rf"^\s*(?:if\s*\([^)]*\)\s*)?{re.escape(name)}\s*"
                rf"(\+\+|--|\+=|-=|\*=|/=|=)")
            inside_log = 0
            outside = 0
            for idx, line in enumerate(lines):
                hits = len(word.findall(line))
                if not hits:
                    continue
                if idx + 1 == decl_line:
                    hits -= 1                       # the declaration itself
                if hits <= 0:
                    continue
                if idx in log_lines:
                    inside_log += hits
                    continue
                # a line that only assigns to it is not a read
                if write_only.match(line) and len(word.findall(line)) == 1:
                    continue
                outside += hits
            if outside == 0 and inside_log > 0:
                findings.append((path, decl_line, name, inside_log))

    rel = lambda p: p.relative_to(ROOT).as_posix()
    if a.verbose:
        print(f"scanned {len(files)} files, {checked} declared locals")
    if not findings:
        print(f"[ok  ] no FFLOG-only variables ({checked} locals scanned)")
        return 0

    print("variables whose only use is inside FFLOG(...) - these become")
    print("write-only in a RELEASE build, which is -Werror there:\n")
    for path, line, name, uses in findings:
        print(f"  {rel(path)}:{line}  {name}  ({uses} use(s), all in the log)")
    print(f"\n{len(findings)} finding(s). Fix by using the value in a path that "
          f"survives FF_QUIET_BUILD (an error list, a report string, a return).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
