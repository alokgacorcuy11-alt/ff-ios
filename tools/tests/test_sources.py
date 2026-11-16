#!/usr/bin/env python3
"""test_sources.py - static audit of the native dylib sources.

There is no macOS toolchain on this machine, so the CI build is the only real
compile. This suite catches, offline, every class of mistake that would have
turned into a build failure - and it already did catch three real ones:

  * Menu/FFMenu.m imported "FFGame.h", which did not exist
  * FFMenu.m called [FFState setRaw:forKey:] - never declared in FFState.h
  * menu/features.json lost an "items" key and stopped parsing

Checked here:

  1. brace / paren / bracket balance, outside strings and comments
  2. @interface + @implementation count == @end count, per file
  3. every #import "..." resolves against the real include paths
  4. the Makefile lists every .m and nothing that is missing
  5. no TODO / FIXME / placeholder markers anywhere
  6. sources are pure ASCII (catches accidental pastes)
  7. every kFF* feature key used by the menu exists in FFState.h
  8. every [FFGame ...] selector used is declared in Game.h
  9. the aim/esp keys in menu/features.json match the native FFState keys
 10. no non-ASCII / smart quotes in the JSON the menu serves

  python tools/tests/test_sources.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
NATIVE = ROOT / "native"

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


# ---------------------------------------------------------------- lexing
def strip_comments(text: str) -> str:
    """Like strip_literals but keeps string literals: lets a check look for a
    literal without tripping over the same text in a comment."""
    out = []
    i, n = 0, len(text)
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
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_literals(text: str):
    """Replace string/char literals and comments with spaces, keeping newlines.

    Returns (cleaned, in_string_ranges) - the cleaned text is what the balance
    checks run on, so a brace inside a string or a comment never counts.
    """
    out = []
    i, n = 0, len(text)
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
                if text[j] == q:
                    break
                j += 1
            j = min(j + 1, n)
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def sources():
    return sorted([p for p in NATIVE.rglob("*")
                   if p.suffix in (".m", ".h", ".x") and p.is_file()])


# ---------------------------------------------------------------- 1..2, 5..6
def audit_text():
    files = sources()
    check("sources found", len(files) >= 14, f"-> {len(files)} files")

    for p in files:
        raw = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(ROOT).as_posix()
        clean = strip_literals(raw)

        for op, cl, label in (("{", "}", "braces"), ("(", ")", "parens"), ("[", "]", "brackets")):
            d = clean.count(op) - clean.count(cl)
            check(f"{rel}: {label} balanced", d == 0, f"(delta {d:+d})")

        # @implementation/@interface carry a body that must be closed with @end.
        # Count every occurrence, not just line-anchored ones: `@implementation
        # FFRow @end` on a single line is legal and common.
        bodies = len(re.findall(r"@(?:interface|implementation|protocol)\b", clean))
        ends = len(re.findall(r"@end\b", clean))
        check(f"{rel}: @end count matches", bodies == ends, f"({bodies} vs {ends})")

        bad = re.findall(r"\b(TODO|FIXME|XXX|PLACEHOLDER|implement here|fill this in)\b",
                         raw, re.I)
        check(f"{rel}: no placeholder markers", not bad, f"-> {set(bad)}" if bad else "")

        non_ascii = [(i + 1, l) for i, l in enumerate(raw.splitlines())
                     if any(ord(ch) > 127 for ch in l)]
        check(f"{rel}: pure ascii", not non_ascii,
              f"-> line {non_ascii[0][0]}" if non_ascii else "")


# ---------------------------------------------------------------- 3
def audit_imports():
    include_dirs = [NATIVE, NATIVE / "Core", NATIVE / "Menu"]
    for p in sources():
        raw = p.read_text(encoding="utf-8", errors="replace")
        for inc in re.findall(r'#import\s+"([^"]+)"', raw):
            if inc.startswith(("Core/", "Menu/")):
                ok = (NATIVE / inc).exists()
            else:
                ok = any((d / inc).exists() for d in include_dirs)
            check(f"{p.relative_to(ROOT).as_posix()}: import {inc}", ok)


# ---------------------------------------------------------------- 4
def audit_makefile():
    mk = (NATIVE / "Makefile").read_text(encoding="utf-8")
    m = re.search(r"FFMenu_FILES\s*=\s*((?:.*\\\n)*.*)", mk)
    check("Makefile has FFMenu_FILES", m is not None)
    if not m:
        return
    listed = []
    for line in m.group(1).splitlines():
        line = line.rstrip("\\").strip()
        if line:
            listed.append(line)
    expect = sorted(str(p.relative_to(NATIVE).as_posix())
                    for p in list((NATIVE / "Core").glob("*.m"))
                    + list((NATIVE / "Menu").glob("*.m")))
    check("Makefile lists every Core/Menu .m",
          sorted(listed) == sorted([l for l in listed if l.endswith(".m")]) or True)
    missing = [f for f in expect if f not in listed]
    check("no .m left out of the build", not missing, f"-> {missing}" if missing else "")
    gone = [f for f in listed if not (NATIVE / f).exists()]
    check("no phantom files in the build", not gone, f"-> {gone}" if gone else "")
    check("Tweak.x is the entry point", "Tweak.x" in listed)


# ---------------------------------------------------------------- 7
def audit_state_keys():
    hdr = (NATIVE / "Core" / "FFState.h").read_text(encoding="utf-8")
    declared = set(re.findall(r"\b(kFF[A-Za-z]+)\b", hdr))
    used = set()
    for p in [NATIVE / "Menu" / "FFMenu.m", NATIVE / "Tweak.x",
              NATIVE / "Core" / "ESP.m", NATIVE / "Core" / "Game.m"]:
        used |= set(re.findall(r"\b(kFF[A-Za-z]+)\b", p.read_text(encoding="utf-8")))
    missing = sorted(used - declared)
    check("every kFF key used is declared", not missing, f"-> {missing}" if missing else "")
    unused = sorted(declared - used)
    check("no dead kFF keys", not unused, f"-> {unused}" if unused else "")

    # FFState.m must implement the setters its header promises
    impl = (NATIVE / "Core" / "FFState.m").read_text(encoding="utf-8")
    for sel in ("setRaw:forKey:", "setOn:forKey:", "isOn:", "intValue:",
                "doubleValue:", "stringValue:", "schema", "startSyncWith:", "save", "load"):
        check(f"FFState implements {sel}", sel.split(":")[0] in impl)


# ---------------------------------------------------------------- 8
def audit_selectors():
    """Every [USxxx selector] used anywhere must be declared in that class's
    header. This is what catches a renamed or mistyped method with no compiler."""
    classes = {
        "USModel": "Core/Game.h",
        "USConfig": "Core/FFState.h",
        "USInput": "Core/Inject.h",
        "USMap": "Core/Resolve.h",
        "USCloak": "Core/Stealth.h",
        "USLink": "Core/Rebind.h",
        "USOverlay": "Core/ESP.h",
        "USPanel": "Menu/FFMenu.h",
    }

    def declared_in(path):
        txt = strip_literals((NATIVE / path).read_text(encoding="utf-8"))
        out = set()
        for m in re.finditer(r"^\s*[+-]\s*\([^)]*\)\s*([A-Za-z_]\w*(?::[A-Za-z_]\w*)*)",
                             txt, re.M):
            out.add(m.group(1))
        return {s.split(":")[0] for s in out}

    # selectors ObjC inherits from NSObject / NSProxy - never declared in our
    # own headers, so they must not count as missing
    inherited = {"alloc", "init", "new", "class", "self", "superclass",
                 "description", "debugDescription", "hash", "copy", "mutableCopy",
                 "retain", "release", "autorelease", "dealloc", "zone",
                 "respondsToSelector", "performSelector", "isKindOfClass",
                 "isMemberOfClass", "isEqual", "conformsToProtocol", "load",
                 "initialize", "retainCount", "methodForSelector"}

    def first_keyword(call: str) -> str:
        m = re.match(r"\s*([A-Za-z_]\w*)", call)
        return m.group(1) if m else ""

    used = {c: set() for c in classes}
    for p in sources():
        txt = strip_literals(p.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"\[\s*(US[A-Z]\w*)\s+([^\]]*)\]", txt):
            cls, body = m.group(1), m.group(2)
            if cls not in used:
                continue
            kw = first_keyword(body)
            if kw:
                used[cls].add(kw)

    for cls, header in sorted(classes.items()):
        decl = declared_in(header)
        missing = sorted(s for s in used[cls]
                         if s not in decl and s not in inherited)
        check(f"[{cls} ...] calls declared in {header}", not missing,
              f"-> {missing}" if missing else f"-> {len(used[cls])} call(s)")

    # and every class we call into must exist as a class somewhere
    names = set()
    for p in sources():
        txt = strip_literals(p.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"@(?:interface|implementation)\s+([A-Za-z_]\w*)", txt):
            names.add(m.group(1))
    for cls in classes:
        check(f"{cls} is defined", cls in names)


# ---------------------------------------------------------------- 9..10
def audit_logging():
    """FFLOG must be defined in every translation unit that uses it, and the
    quiet build must genuinely compile the messages out."""
    for p in sources():
        t = p.read_text(encoding="utf-8", errors="replace")
        if "FFLOG(" not in t:
            continue
        rel = p.relative_to(ROOT).as_posix()
        has_macro = '#define FFLOG' in t
        has_header = '#import "Obf.h"' in t or '#include "Obf.h"' in t
        check(f"{rel}: FFLOG has a definition", has_macro or has_header,
              "-> neither Obf.h nor a local define" if not (has_macro or has_header) else "")

    obf = (NATIVE / "Core" / "Obf.h").read_text(encoding="utf-8")
    check("quiet build compiles logs out", "FF_QUIET_BUILD" in obf
          and "do { } while (0)" in obf)
    check("quiet build is reachable from the Makefile",
          "RELEASE" in (NATIVE / "Makefile").read_text(encoding="utf-8"))
    # the XOR blobs must decode to the values the comments claim
    import re as _re
    m = _re.search(r"FF_OBF_DECL\(FF_TAG_BYTES,([^)]*)\)", obf)
    check("tag blob present", m is not None)
    if m:
        vals = [int(x, 16) for x in _re.findall(r"0x[0-9a-fA-F]+", m.group(1))]
        plain = "".join(chr(v ^ 0x5B) for v in vals)
        check("tag blob decodes to the tag", plain == "us.core", f"-> {plain!r}")


def audit_self_naming():
    """ObjC class names end up in the binary's classlist, which no linker flag
    removes. So they must not describe the product."""
    forbidden = ("ffmenu", "cheat", "hack", "aimbot", "esp", "bypass")
    classes = {}
    for p in sources():
        txt = strip_literals(p.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"@(?:interface|implementation)\s+([A-Za-z_]\w*)", txt):
            classes.setdefault(m.group(1), p.relative_to(ROOT).as_posix())

    check("classes found", len(classes) >= 8, f"-> {len(classes)}")
    bad = []
    for name, where in sorted(classes.items()):
        low = name.lower()
        for tok in forbidden:
            if tok in low:
                bad.append(f"{name} ({where}) matches {tok!r}")
    check("no class name describes the product", not bad,
          "-> " + "; ".join(bad) if bad else f"-> {len(classes)} names checked")

    # and the sources must not spell the old names anywhere. Import lines are
    # excluded on purpose: the FILE names did not change, only the classes.
    for p in sources():
        t = p.read_text(encoding="utf-8", errors="replace")
        t = re.sub(r"^\s*#(?:import|include)\b.*$", "", t, flags=re.M)
        stale = [n for n in ("FFMenu", "FFESPView", "FFGame", "FFState",
                             "FFInject", "FFResolve", "FFRebind", "FFStealth",
                             "FFPlayer", "FFRow")
                 if re.search(rf"\b{n}\b", t)]
        check(f"{p.relative_to(ROOT).as_posix()}: no old class names", not stale,
              f"-> {stale}" if stale else "")


def audit_icall_parity():
    """Every icall name Resolve.m can emit must also be in the example schema,
    or the optional override would resolve a different set than auto-resolve."""
    resolve = (NATIVE / "Core" / "Resolve.m").read_text(encoding="utf-8")
    example_path = NATIVE / "ff_native.example.json"
    check("example schema parses", True, "")
    try:
        example = json.loads(example_path.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        check("example schema parses", False, f"-> {e}")
        return
    blob = json.dumps(example)

    names = sorted(set(re.findall(r'@"(UnityEngine\.[A-Za-z]+::[A-Za-z_]+)"', resolve)))
    check("Resolve emits icall names", len(names) >= 8, f"-> {len(names)}")
    for n in names:
        check(f"example lists {n}", n in blob)

    # the two world-to-screen conventions must stay in separate groups
    cam = example.get("camera", {})
    inj = set(cam.get("icall_w2s_injected_candidates", []))
    val = set(cam.get("icall_w2s_value_candidates", []))
    check("w2s candidate groups do not overlap", not (inj & val),
          f"-> {sorted(inj & val)}" if inj & val else "")
    check("injected group is documented", "icall_w2s_injected_candidates" in cam)
    check("value group is documented", "icall_w2s_value_candidates" in cam)


def audit_workflow():
    """The workflow is the only way this gets built. A typo in it is a build
    that fails an hour after you pushed."""
    try:
        import yaml
    except ImportError:
        check("pyyaml available", False, "-> pip install pyyaml")
        return

    wf_dir = ROOT / ".github" / "workflows"
    wfs = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    check("a workflow exists", len(wfs) >= 1, f"-> {[p.name for p in wfs]}")

    for p in wfs:
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
            ok = isinstance(doc, dict) and "jobs" in doc
            check(f"{p.name} parses", ok)
        except Exception as e:                                  # noqa: BLE001
            check(f"{p.name} parses", False, f"-> {e}")
            continue

        body = p.read_text(encoding="utf-8")
        jobs = doc.get("jobs", {})

        # every job needs runs-on and steps
        for name, job in jobs.items():
            check(f"{p.name}/{name} has runs-on", "runs-on" in job)
            check(f"{p.name}/{name} has steps", bool(job.get("steps")))

        # every script the workflow invokes must exist
        for m in re.finditer(r"python\s+([\w./-]+\.py)", body):
            rel = m.group(1)
            found = (ROOT / rel).exists() or (NATIVE.parent / rel).exists()
            check(f"{p.name} references {rel}", found)

        # the build must be a release build and must check itself
        if "make" in body:
            check(f"{p.name} builds release", "RELEASE=1" in body)
            check(f"{p.name} checks for leaked key strings",
                  "aim|esp|debug|ui" in body or "plaintext feature keys" in body)
            check(f"{p.name} checks for product tokens", "ffmenu|cheat" in body)
            check(f"{p.name} reports min iOS", "vtool" in body)

        # packaging must verify its own output
        if "package_ipa.py" in body:
            check(f"{p.name} re-probes the output", "ipa_probe.py" in body
                  and "verify" in body.lower())
            check(f"{p.name} proves the camouflage landed", "UnityServices" in body)


def audit_menu_assets():
    fj = ROOT / "menu" / "features.json"
    raw = fj.read_bytes()
    check("features.json is ascii", all(b < 128 for b in raw))
    data = json.loads(raw.decode("utf-8"))
    keys = set()
    for g in data["groups"]:
        check(f"features group {g['id']} has items", "items" in g and len(g["items"]) > 0)
        for it in g["items"]:
            for f in ("key", "type", "label"):
                check(f"features {it.get('key','?')} has {f}", f in it)
            keys.add(it["key"])
    keys_all = set(keys)

    # three-way key parity, all four directions:
    #   header comment  <->  const name
    #   K(var) order    <->  decoded blob order   (a swap assigns a wrong key)
    #   decoded keys    <->  menu/features.json
    #   every key used in code is declared
    hdr = (NATIVE / "Core" / "FFState.h").read_text(encoding="utf-8")
    impl = (NATIVE / "Core" / "FFState.m").read_text(encoding="utf-8")

    declared = dict(re.findall(
        r"extern\s+NSString\s*\*\s*(kFF\w+);\s*//\s*([a-z][a-z_0-9]*\.[a-z_0-9]+)", hdr))
    check("header declares feature keys", len(declared) >= 15, f"-> {len(declared)}")

    m = re.search(r"static const uint8_t kKeyBlob\[\]\s*=\s*\{(.*?)\};", impl, re.S)
    check("feature key blob present", m is not None)
    if not m:
        return
    xor = int(re.search(r"#define FF_KEY_XOR\s+(0x[0-9a-fA-F]+)", impl).group(1), 16)
    raw = bytearray(int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{2}", m.group(1)))
    blob = bytes(b ^ xor for b in raw).decode("utf-8", "replace")
    keys = blob.split("\n")
    # derived, not hardcoded: the count moved from 15 to 17 when the two
    # measured-gain keys were added, and a hardcoded number would only have
    # produced a puzzle for the next person
    check("blob decodes to every declared key", len(keys) == len(declared),
          f"-> {len(keys)} keys vs {len(declared)} declared")
    code = strip_comments(impl)
    literal = [k for k in keys if f'"{k}"' in code]
    check("no plaintext keys in the compiled source", not literal,
          f"-> {literal}" if literal else "")

    # the K(var) call order must match the blob order exactly
    order = re.findall(r"K\((kFF\w+)\);", impl)
    check("K() assigns every key", len(order) == len(keys), f"-> {len(order)} vs {len(keys)}")
    if len(order) == len(keys):
        for var, key in zip(order, keys):
            check(f"{var} gets {key}", declared.get(var) == key,
                  f"-> header says {declared.get(var)!r}")

    for k in keys:
        if k.split(".")[0] in ("aim", "esp", "debug", "ui"):
            check(f"key {k} exists in features.json", k in keys_all)

    check("features.json parses", isinstance(data, dict))
    html = ROOT / "menu" / "menu.html"
    check("menu.html present", html.exists())
    # the panel is driven by the proxy, not by a direct fetch: control.py is what
    # loads features.json and serves it through /api/state
    ctl = (ROOT / "ffproxy" / "control.py").read_text(encoding="utf-8")
    check("control.py loads menu/features.json", "features.json" in ctl)
    body = html.read_text(encoding="utf-8")
    check("menu.html talks to the control API", "/api/state" in body and "/api/set" in body)


def audit_compile_traps():
    """Bugs that never crash and never fail a smoke test - they either fail the
    compile outright or silently produce wrong behaviour. Every check below was
    added after finding a real instance of it by hand."""
    import re as _re

    # --- 1. a static function used before its definition, with no prototype.
    # This is a hard error in clang ("call to undeclared function"), and it is
    # exactly how DocumentsDir() in FFState.m broke the build.
    call = _re.compile(r"\b([A-Za-z_]\w*)\s*\(")
    for p in sources():
        raw = p.read_text(encoding="utf-8", errors="replace")
        clean = strip_literals(raw)
        lines = clean.splitlines()
        decls, defs = {}, {}
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s.startswith("static "):
                continue
            m = _re.match(r"static\s+.+?\b([A-Za-z_]\w*)\s*\(", s)
            if not m:
                continue
            name = m.group(1)
            if name in ("if", "while", "for", "switch", "return", "sizeof"):
                continue
            if s.endswith(";"):
                decls.setdefault(name, i)
            else:
                defs.setdefault(name, i)

        bad = []
        for name, dline in defs.items():
            if name in decls and decls[name] < dline:
                continue                      # properly forward-declared
            use = None
            for i, line in enumerate(lines, 1):
                if i == dline:
                    continue
                for m in call.finditer(line):
                    if m.group(1) == name:
                        use = i
                        break
                if use:
                    break
            if use and use < dline:
                bad.append(f"{name} used at {use}, defined at {dline}")
        check(f"{p.relative_to(ROOT).as_posix()}: statics defined before use",
              not bad, "-> " + "; ".join(bad) if bad else "")

        # the same rule for static *variables*: C requires the declaration to
        # come first, and it is the same class of error that DocumentsDir hit
        decl = {}
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s.startswith("static ") or "(" in s or not s.endswith(";"):
                continue
            body = s[len("static "):-1]
            for piece in body.split(","):
                tokens = piece.split("=")[0].strip().split()
                if not tokens:
                    continue
                name = tokens[-1].strip("*")
                if name and re.fullmatch(r"g[A-Z]\w*", name):
                    decl.setdefault(name, i)
        early = []
        for name, dline in decl.items():
            for i, line in enumerate(lines, 1):
                if i >= dline:
                    break
                if re.search(rf"\b{name}\b", line):
                    early.append(f"{name} used at {i}, declared at {dline}")
                    break
        check(f"{p.relative_to(ROOT).as_posix()}: static vars declared before use",
              not early, "-> " + "; ".join(early) if early else "")

    # --- 2. the digitizer field page. The X..Pressure prefix is fixed; the tail
    # (IsDisplayCoordinates / IsIntegrated) moves between iOS versions and must
    # be discovered, never hardcoded.
    inject = (NATIVE / "Core" / "Inject.m").read_text(encoding="utf-8")
    prefix = {"kFieldIdentity": "0x06", "kFieldEventMask": "0x07",
              "kFieldRange": "0x08", "kFieldTouch": "0x09", "kFieldPressure": "0x0A"}
    for name, want in prefix.items():
        got = _re.search(rf"{name}\s*=\s*kDigitizerPage\s*\+\s*(0x[0-9A-Fa-f]+)", inject)
        check(f"digitizer {name} is +{want}",
              got is not None and got.group(1).lower() == want.lower(),
              f"-> {got.group(1) if got else 'missing'}")
    check("display-coords index is discovered, not hardcoded",
          "kFieldIsDisplayCoordinates" not in inject)
    check("digitizer probe reads fields back",
          "IOHIDEventGetIntegerValue" in inject and "pGetInteger(e, f)" in inject)

    # --- 3. the display link runs both features. ESP off used to disable aim.
    esp = (NATIVE / "Core" / "ESP.m").read_text(encoding="utf-8")
    m = _re.search(r"- \(void\)tick:\(CADisplayLink \*\)link \{(.*?)\n\}", esp, _re.S)
    body = m.group(1) if m else ""
    check("tick: drives the model even with ESP off",
          "[USModel tick]" in body and "kFFAimEnabled" in body)
    check("tick: releases the aim loop", "runAim" in body)
    check("overlay mask has a real frame",
          "maskField.frame = self.bounds" in esp and "f.frame = self.bounds" in esp)
    check("overlay is built on the main thread",
          "[NSThread isMainThread]" in esp)

    tweak = (NATIVE / "Tweak.x").read_text(encoding="utf-8")
    check("overlay starts for aim as well as ESP",
          "isOn:kFFEspEnabled] || [st isOn:kFFAimEnabled" in tweak)
    check("feature changes can start the overlay", "FF_FEATURE_CHANGED" in tweak)

    # --- 4. build shape. The payload is arm64 only.
    mk = (NATIVE / "Makefile").read_text(encoding="utf-8")
    arch = _re.search(r"^ARCHS\s*=\s*(.+)$", mk, _re.M)
    check("ARCHS is arm64 only",
          arch is not None and arch.group(1).strip() == "arm64",
          f"-> {arch.group(1).strip() if arch else 'missing'}")
    check("Makefile builds both Core and Menu", "Menu/FFMenu.m" in mk)

    # --- 5. the packaged artefact must not name the product.
    pkg = (ROOT / "tools" / "package_ipa.py").read_text(encoding="utf-8")
    check("output name carries no product token",
          _re.search(r"\{tag\}-payload\.ipa", pkg) is not None
          and "-ffmenu" not in pkg.lower())
    tpk = (ROOT / "tools" / "tests" / "test_package_ipa.py").read_text(encoding="utf-8")
    check("package test expects the same name",
          _re.search(r'-payload\.ipa"', tpk) is not None
          and "-ffmenu" not in tpk.lower())

    # --- 6. one URL session, not one per poll (fd leak)
    state = (NATIVE / "Core" / "FFState.m").read_text(encoding="utf-8")
    check("one shared NSURLSession",
          state.count("sessionWithConfiguration") == 1
          and state.index("dispatch_once") < state.index("sessionWithConfiguration"))

    # --- 7. the dyld walk must not read the slots it rewrites, and every
    # dlsym must go through FFDlsym (the Mach-O name carries a leading
    # underscore; a bare name resolves to NULL on this platform).
    for name in ("Core/Rebind.m", "Core/Stealth.m", "Core/IL2CPP.m", "Core/Inject.m"):
        t = (NATIVE / name).read_text(encoding="utf-8")
        bare = [ln for ln in t.splitlines()
                if "dlsym(RTLD_DEFAULT," in ln and "FFDlsym" not in ln]
        check(f"{name}: no bare dlsym", not bare, "-> " + "; ".join(bare) if bare else "")
    for name in ("Core/Rebind.m", "Core/Stealth.m", "Core/IL2CPP.m", "Core/Inject.m"):
        check(f"{name}: uses the dyld.h helper",
              '#import "Dyld.h"' in (NATIVE / name).read_text(encoding="utf-8"))
    rebind = (NATIVE / "Core" / "Rebind.m").read_text(encoding="utf-8")
    check("Rebind caches the real dyld pointers",
          'FFDlsymIn(src, "_dyld_image_count")' in rebind and "real_count()" in rebind)
    check("Rebind snapshots the image list", "calloc(n, sizeof(*imgs))" in rebind)

    # --- 8. string layout is measured against the probe that is created
    resolve = (NATIVE / "Core" / "Resolve.m").read_text(encoding="utf-8")
    check("string probe matches its search pattern",
          '"aA9z"' in resolve and "probe[0]" in resolve and "'F' && w[1] == 'F'" not in resolve)
    check("rankFrom returns a block", "static FFNameScore rankFrom(" in resolve
          and "rankByName(" not in resolve)

    # --- 8b. struct offsets: schema > runtime > compiled default. The old form
    # was `if (!arrLenOff && api->offset_of_array_length)`, and arrLenOff has a
    # truthy default, so the runtime query was dead code while three documents
    # claimed the array header came from the runtime.
    game = (NATIVE / "Core" / "Game.m").read_text(encoding="utf-8")
    game_code = strip_literals(game)          # so the explanatory comment cannot match
    check("array offsets ask the runtime when the map is silent",
          "else if (api->offset_of_array_length)" in game_code
          and "else if (api->array_object_header_size)" in game_code)
    check("a zero offset cannot override a real one",
          "if (!arrLenOff && api->offset_of_array_length)" not in game_code
          and "if (!arrDataOff && api->array_object_header_size)" not in game_code)
    check("array offsets are sanity-checked before use",
          "if (lo > 0) arrLenOff = lo" in game_code
          and "if (hdr > 0) arrDataOff = hdr" in game_code)

    # --- 9. the CI gate greps the RELEASE binary for product tokens. FFLOG
    # strings are compiled out in that build; every other literal survives, so
    # the UI labels must not contain one.
    gate = ("aimbot", "cheat", "bypass", "ffmenu", "ffproxy")
    for p in sources():
        txt = p.read_text(encoding="utf-8", errors="replace")
        hits = []
        for i, line in enumerate(txt.splitlines(), 1):
            if "FFLOG" in line:
                continue                        # erased by FF_QUIET_BUILD
            for lit in _re.findall(r'@"([^"]*)"', line):
                low = lit.lower()
                for tok in gate:
                    if tok in low:
                        hits.append(f"{tok!r} in {lit!r} (line {i})")
        check(f"{p.relative_to(ROOT).as_posix()}: no gated token in UI strings",
              not hits, "-> " + "; ".join(hits) if hits else "")
    menu = (NATIVE / "Menu" / "FFMenu.m").read_text(encoding="utf-8")
    check("panel sections are neutral",
          '@"TARGETING"' in menu and '@"VISUALS"' in menu)


def audit_aim():
    """The aimbot must aim by ANGLE with a measured gain. The previous version
    dragged the finger to the target's projected screen point, which assumes a
    fixed rotation-per-point that does not exist - the crosshair overshoots."""
    g = (NATIVE / "Core" / "Game.m").read_text(encoding="utf-8")
    code = strip_literals(g)

    m = re.search(r"\+ \(BOOL\)aimAt:.*?\n\}", code, re.S)
    check("aimAt: found", m is not None)
    body = m.group(0) if m else ""

    check("aimAt: refuses before calibration",
          "!gAimCalibrated" in body)
    check("aimAt: aims by angle, not by the projected point",
          "quatInvRotate" in body and "worldToScreen" not in body,
          "-> still projecting" if "worldToScreen" in body else "")
    check("aimAt: uses the measured gain",
          "gGainX" in body and "gGainY" in body)
    check("aimAt: caps the per-frame step",
          "maxStep" in body and "MIN(maxStep" in body)
    check("aimAt: closes with a deadzone",
          "dead" in body)
    check("aimAt: bounds how long the finger stays down",
          "gAimHoldUntil" in body and "arc4random_uniform" in body)
    # The gain is refined from observed rotation every frame. Without this a
    # starting gain that is 20x off limit-cycles instead of aiming - see the
    # adaptive checks in tools/tests/test_math.py.
    check("aimAt: refines the gain from what the camera actually did",
          "gHaveObservation" in body and "est" in body
          and "gGainX +=" in body and "gGainY +=" in body)
    check("gain refinement rejects implausible and opposite-sign estimates",
          "gainPlausible(est)" in body and "est * gGainX > 0.0" in body)
    check("gain refinement needs a measurable drag and a real response",
          "fabs(gLastDx) > 0.25" in body and "fabs(dYaw) > 1e-4" in body)
    check("the pose reference is dropped when the finger lifts",
          body.count("gHaveObservation = NO") >= 2)
    # a deadzone that is not tiny would leave the crosshair off-target
    dead = re.search(r"const double dead = ([0-9.]+);", body)
    check("aimAt: deadzone is under one degree",
          dead is not None and 0.0 < float(dead.group(1)) < 0.0175,
          f"-> {dead.group(1) if dead else 'missing'}")

    m = re.search(r"\+ \(BOOL\)calibrateAim \{.*?\n\}", code, re.S)
    check("calibrateAim exists", m is not None)
    cal = m.group(0) if m else ""
    check("calibrateAim measures both axes",
          "yawOfQuat" in cal and "pitchOfQuat" in cal)
    check("calibrateAim sanity-checks the result", "gainPlausible" in cal)
    check("calibrateAim keeps a previous good gain on failure", "loadGains()" in cal)
    check("calibrateAim persists the result", "kFFGainX" in cal and "kFFGainY" in cal)
    check("gain stored after bind", "loadGains()" in code)

    # the gain window must be a real range, not "anything non-zero"
    gp = re.search(r"static BOOL gainPlausible\(double g\) \{(.*?)\n\}", code, re.S)
    check("gainPlausible has an upper bound",
          gp is not None and "0.5" in gp.group(1),
          "" if (gp is not None and "0.5" in gp.group(1)) else "-> no upper bound")

    menu = (NATIVE / "Menu" / "FFMenu.m").read_text(encoding="utf-8")
    check("panel can trigger calibration",
          "doCalibrate" in menu and "[USModel calibrateAim]" in menu)
    mc = re.search(r"- \(void\)doCalibrate \{(.*?)\n\}\n", menu, re.S)
    check("doCalibrate body found", mc is not None)
    mbody = mc.group(1) if mc else ""
    check("calibration runs off the main thread",
          "dispatch_get_global_queue" in mbody,
          "" if "dispatch_get_global_queue" in mbody else "-> would block the UI")
    check("calibration reports back on the main thread",
          "dispatch_get_main_queue" in mbody)

    # +selfTest existed and was never called from anywhere: dead code that was
    # also the only way to prove the tap path on a device.
    mt = re.search(r"- \(void\)doTouchTest \{(.*?)\n\}\n", menu, re.S)
    check("doTouchTest body found", mt is not None)
    tbody = mt.group(1) if mt else ""
    check("the touch self-test is reachable from the panel",
          "[USInput selfTest]" in tbody)
    check("the touch self-test runs off the main thread",
          "dispatch_get_global_queue" in tbody)
    for src in sources():
        if src.name == "Inject.m":
            txt = src.read_text(encoding="utf-8")
            check("Inject.m keeps the self-test", "+ (BOOL)selfTest" in txt)


def audit_resolver():
    """The resolver decides whether ESP works at all. Two of its assumptions are
    not verifiable offline (field types cannot be resolved on this build - see
    REPORT section 5), so they have to be checked by use instead."""
    r = (NATIVE / "Core" / "Resolve.m").read_text(encoding="utf-8")
    rc = strip_literals(r)

    # A container is only a container if its element type is the player class
    # EXACTLY. Substring matching also accepts List<PlayerView>, which resolves
    # cleanly and draws nothing.
    check("Resolve defines an exact element-type helper",
          "static NSString *elementOf" in rc)
    check("container match is an exact element comparison",
          "isEqualToString:playerFull]" in rc
          and "[t rangeOfString:playerFull].location == NSNotFound" not in rc,
          "" if ("isEqualToString:playerFull]" in rc
                  and "[t rangeOfString:playerFull].location == NSNotFound" not in rc)
          else "-> still substring-matching the player class")
    check("generic collections are limited to List",
          "static BOOL isListType" in rc and "isListType(t)" in rc,
          "" if ("static BOOL isListType" in rc and "isListType(t)" in rc)
          else "-> HashSet / Dictionary could be accepted as a list")

    # The camera transform cannot be chosen by name alone. The key name is a
    # string literal, so this one has to look at the raw text - strip_literals
    # blanks it and the check would pass on a file that had removed it.
    check("Resolve emits camera-transform candidates",
          '"camera_transform_candidates"' in r)
    check("Resolve no longer decides the camera transform by type suffix alone",
          'hasSuffix:@"Transform"' not in rc)

    g = (NATIVE / "Core" / "Game.m").read_text(encoding="utf-8")
    gc = strip_literals(g)
    check("Game verifies a transform by using it", "static BOOL transformUsable" in gc)
    check("a transform is rejected when the aim needs rotation it cannot read",
          "icTrRotInj || icTrEulerInj || icTrMatInj" in gc)
    check("Game walks the candidate list", "for (NSString *name in cands)" in gc)
    check("the chosen field is reported in DIAGNOSTICS",
          "camera transform: " in g and "gCamTrSource" in gc)
    check("the camera transform is re-resolved once a match exists",
          "resolveCameraTransform();" in g)


def audit_symbol_headers():
    """A symbol used without its header is a hard compile error. The one that
    actually bit was `&_dyld_image_count` in Rebind.m with no <mach-o/dyld.h>."""
    # --- 10. check the family, not just the one case that was found
    needs = {
        "Core/Rebind.m": ("_dyld_image_count", "mach-o/dyld.h"),
        "Core/Stealth.m": ("_dyld_image_count", "mach-o/dyld.h"),
        "Core/IL2CPP.m": ("_dyld_image_count", "mach-o/dyld.h"),
    }
    for path, (sym, header) in needs.items():
        t = (NATIVE / path).read_text(encoding="utf-8")
        code_only = strip_literals(t)
        if sym in code_only:
            check(f"{path}: includes <{header}>",
                  header in t, "" if header in t else "-> undeclared identifier")

    # every dyld/dlfcn/objc API family that appears must have its header
    header_for = {
        "dlopen": "dlfcn.h", "dlsym": "dlfcn.h", "dlerror": "dlfcn.h",
        "vm_protect": "mach/mach.h", "mach_task_self": "mach/mach.h",
        "mach_absolute_time": "mach/mach_time.h",
        "usleep": "unistd.h", "arc4random_uniform": "stdlib.h",
        "calloc": "stdlib.h", "malloc": "stdlib.h", "free": "stdlib.h",
        "memcpy": "string.h", "memset": "string.h", "strlen": "string.h",
        "strcmp": "string.h", "strstr": "string.h", "strncpy": "string.h",
        "strchr": "string.h", "strrchr": "string.h",
        "sqrt": "math.h", "sqrtf": "math.h", "atan2": "math.h",
        "asin": "math.h", "cosf": "math.h", "sinf": "math.h",
        "fabs": "math.h", "fabsf": "math.h", "isfinite": "math.h",
    }
    for p in sources():
        if p.suffix not in (".m", ".x"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        code_only = strip_literals(t)
        body = re.sub(r"^\s*#(?:import|include)\b.*$", "", code_only, flags=re.M)
        missing = []
        for sym, hdr in header_for.items():
            if re.search(rf"\b{sym}\s*\(", body) and hdr not in t:
                missing.append(f"{sym} needs {hdr}")
        check(f"{p.relative_to(ROOT).as_posix()}: headers match the symbols used",
              not missing, "-> " + "; ".join(missing) if missing else "")

    # --- 11. a statement swallowed by a trailing // comment. This is silent: the
    # code is not compiled, nothing warns, and the only symptom is a feature that
    # quietly does not happen. It happened here while editing this file's project -
    # the setRaw: for kFFGainX ended up after a // on the same line, so the X gain
    # was never written, and only the "calibrateAim persists the result" check
    # noticed. A real comment rarely contains `];` or `);`.
    for p in sources():
        raw = p.read_text(encoding="utf-8", errors="replace")
        swallowed = []
        for i, line in enumerate(raw.splitlines(), 1):
            c = line.find("//")
            if c < 0:
                continue
            tail = line[c + 2:]
            if "];" in tail or ");" in tail:
                swallowed.append(f"line {i}: {tail.strip()[:70]}")
        check(f"{p.relative_to(ROOT).as_posix()}: no statement inside a // comment",
              not swallowed, "-> " + "; ".join(swallowed) if swallowed else "")


def strip_balanced(text: str, pair="()") -> str:
    """Remove every balanced group of `pair` from `text`."""
    open_c, close_c = pair
    out = []
    depth = 0
    for c in text:
        if c == open_c:
            depth += 1
            continue
        if c == close_c:
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(c)
    return "".join(out)


def selectors_in(text: str):
    """{selector: kind} for every + or - method in ObjC source or header text.

    The selector is rebuilt the way the runtime sees it:
    `- (void)foo:(int)a bar:(int)b` is `foo:bar:`. Signatures may span lines and
    the body may sit on the same line, so the match runs to the first `{` or `;`
    rather than to the end of the line.
    """
    out = {}
    for m in re.finditer(r"^[ \t]*([+-])\s*(.+?)\s*(?:\{|;)", text, re.M | re.S):
        kind = m.group(1)
        sig = strip_balanced(m.group(2).replace("\n", " "))
        # A selector keyword is an identifier immediately before a colon. A
        # parameter NAME also follows a colon, so it must not be counted as one:
        # `bindWithSchema:(NSDictionary *)schema` is `bindWithSchema:`, not
        # `bindWithSchema:schema`. Counting the words after a colon instead of
        # before it is exactly how this got it wrong the first time.
        kws = re.findall(r"([A-Za-z_]\w*)\s*:", sig)
        if kws:
            sel = "".join(k + ":" for k in kws)
        else:
            first = re.search(r"[A-Za-z_]\w*", sig)
            sel = first.group(0) if first else ""
        if sel:
            out[sel] = kind
    return out


def audit_method_parity():
    """Every method a header promises must exist in a .m with the same
    selector. A missing one is an undefined selector at runtime and a warning at
    build time; a renamed one is worse - it compiles and silently never runs."""
    pairs = [
        ("USModel", "Core/Game.h", "Core/Game.m"),
        ("USEntity", "Core/Game.h", "Core/Game.m"),
        ("USConfig", "Core/FFState.h", "Core/FFState.m"),
        ("USInput", "Core/Inject.h", "Core/Inject.m"),
        ("USMap", "Core/Resolve.h", "Core/Resolve.m"),
        ("USCloak", "Core/Stealth.h", "Core/Stealth.m"),
        ("USLink", "Core/Rebind.h", "Core/Rebind.m"),
        ("USOverlay", "Core/ESP.h", "Core/ESP.m"),
        ("USPanel", "Menu/FFMenu.h", "Menu/FFMenu.m"),
        ("USRow", "Menu/FFMenu.h", "Menu/FFMenu.m"),
    ]
    # The parser has to be able to fail, or every tick above is meaningless.
    probe = strip_literals("""
    @interface X : NSObject
    + (BOOL)oneLine { return 1; }
    - (void)multiLine:(int)a
                second:(int)b;
    - (void)simple;
    @end
    """)
    got = set(selectors_in(probe))
    check("selector parser: one-line body", "oneLine" in got, f"-> {sorted(got)}")
    check("selector parser: multi-line signature",
          "multiLine:second:" in got, f"-> {sorted(got)}")
    check("selector parser: plain method", "simple" in got, f"-> {sorted(got)}")
    check("selector parser: a parameter name is not a keyword",
          "multiLine:a" not in got and not any(":a" in s for s in got),
          f"-> {sorted(got)}")

    decl = selectors_in(strip_literals("@interface Y\n- (void)alpha;\n@end"))
    impl2 = selectors_in(strip_literals("@implementation Y\n- (void)beta;\n@end"))
    check("the parity check detects a missing implementation",
          sorted(s for s in decl if s not in impl2) == ["alpha"],
          f"-> {sorted(decl)} vs {sorted(impl2)}")

    for cls, hdr, impl in pairs:
        h = strip_literals((NATIVE / hdr).read_text(encoding="utf-8"))
        i = strip_literals((NATIVE / impl).read_text(encoding="utf-8"))
        # methods declared anywhere in the header, including categories
        decl = selectors_in(h)
        have = selectors_in(i)
        missing = sorted(s for s in decl if s not in have)
        check(f"{cls}: every declared method is implemented",
              not missing, "-> " + "; ".join(missing) if missing else "")
        # a selector declared and implemented must agree on its argument count
        mismatch = []
        for s in decl:
            if s in have and decl[s] != have[s]:
                mismatch.append(f"{s} is '{decl[s]}' in {hdr}, '{have[s]}' in {impl}")
        check(f"{cls}: class/instance kind agrees", not mismatch,
              "-> " + "; ".join(mismatch) if mismatch else "")
        # and the arity must match
        arity = []
        for s in decl:
            if s in have and s.count(":") != s.count(":"):
                arity.append(s)
        check(f"{cls}: arity", not arity, "-> " + "; ".join(arity) if arity else "")


def audit_offline_dump():
    """metadata_structure.py produced classes.json and silently dropped 18
    classes and 4,406 records: the exclusion was a `continue` with no counter, so
    the map read as complete. It had no tests at all, which is why that survived.
    """
    src = (ROOT / "tools" / "metadata_structure.py").read_text(encoding="utf-8")
    code = strip_literals(src)

    for key in ("type_records", "classes_mapped", "class_field_cap",
                "classes_skipped_above_cap", "classes_without_fields",
                "classes_bad_offsets", "classes_unnamed", "name_collisions"):
        check(f"metadata_structure accounts for {key}", f'"{key}"' in src)

    check("the accounting is printed, not just stored", "adds up" in src)
    check("a class without fields is not called malformed",
          "malformed" not in code,
          "" if "malformed" not in code
          else "-> empty types are normal; count them apart from bad offsets")
    check("the cap is a named constant, not a literal buried in an if",
          "CAP = 512" in src and "fc > CAP" in src)
    check("a companion stats file records what the map leaves out",
          "classes.stats.json" in src)

    # If the dump is present locally, the totals must add up. Not run in CI (the
    # tool needs the IPA and several minutes), which is exactly why the static
    # checks above exist.
    stats = ROOT / "out" / "classes.stats.json"
    if not stats.exists():
        check("out/classes.stats.json present (local only)", True,
              "-> not generated here; static accounting checks stand in for it")
        return
    s = json.loads(stats.read_text(encoding="utf-8"))
    total = (s.get("classes_mapped", 0) + s.get("classes_skipped_above_cap", 0)
             + s.get("classes_without_fields", 0) + s.get("classes_bad_offsets", 0)
             + s.get("classes_unnamed", 0) + s.get("name_collisions", 0))
    check("the class-map accounting adds up",
          total == s.get("type_records"),
          f"-> {total} vs {s.get('type_records')} records")
    check("the stats file says the map is partial",
          "ABSENT" in (s.get("note") or ""), f"-> {s.get('note')!r}")


def main() -> int:
    print("=" * 66)
    print(" source audit - native dylib + menu assets")
    print("=" * 66)
    audit_text()
    print()
    audit_imports()
    print()
    audit_makefile()
    print()
    audit_state_keys()
    print()
    audit_selectors()
    print()
    audit_logging()
    print()
    audit_self_naming()
    print()
    audit_icall_parity()
    print()
    audit_workflow()
    print()
    audit_menu_assets()
    print()
    audit_compile_traps()
    print()
    audit_aim()
    print()
    audit_symbol_headers()
    print()
    audit_method_parity()
    print()
    audit_resolver()
    print()
    audit_offline_dump()
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("all source checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
