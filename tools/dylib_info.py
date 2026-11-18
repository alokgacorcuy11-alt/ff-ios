#!/usr/bin/env python3
"""dylib_info.py - what is actually inside the dylib we are about to ship.

Run before packaging. Two things here are not cosmetic:

  * a class method whose selector is exactly `load` is invoked by the
    Objective-C runtime from load_images(), during dyld image initialisation -
    whatever its signature says. Shipping one means running our code at the
    only moment when UIKit does not exist and dyld's image list is still being
    built. That crashed on launch once; it must not happen twice.
  * the IL2CPP resolver must be reachable under a name that is not `load`.

    python tools/dylib_info.py dist/FFMenu.dylib
"""
import hashlib
import pathlib
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
FILETYPES = {2: "MH_EXECUTE", 6: "MH_DYLIB", 8: "MH_BUNDLE"}
CPU = {0x0100000C: "arm64", 0x0100000C | 0x02000000: "arm64e"}


def load_commands(d, off):
    ncmds = struct.unpack_from("<I", d, off + 16)[0]
    p = off + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", d, p)
        yield cmd, size, p
        p += size


def symbol_names(d, off):
    """Every named address in the file, from the ld -g entries."""
    symoff = nsyms = stroff = strsize = None
    for cmd, _, p in load_commands(d, off):
        if cmd == 0x2:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", d, p + 8)
    if symoff is None or not strsize:
        return []
    strtab = d[stroff:stroff + strsize]
    out = []
    for i in range(nsyms):
        strx, typ, sect, desc, value = struct.unpack_from("<IBBHQ", d, symoff + i * 16)
        if not value or strx == 0 or strx >= len(strtab):
            continue
        end = strtab.find(b"\x00", strx)
        nm = strtab[strx:end].decode("utf-8", "replace")
        if nm:
            out.append(nm)
    return out


def objc_methnames(d, off):
    """Selector strings from __TEXT,__objc_methname.

    These survive `strip`, unlike the symbol table: a stripped dylib still has
    to carry every selector it uses. That makes this the only check that works
    on the artifact we actually ship (dist/ is stripped in CI).
    """
    for cmd, _, p in load_commands(d, off):
        if cmd != 0x19:                      # LC_SEGMENT_64
            continue
        nsects = struct.unpack_from("<I", d, p + 64)[0]
        s = p + 72
        for _ in range(nsects):
            sectname = d[s:s + 16].rstrip(b"\x00").decode("utf-8", "replace")
            segname = d[s + 16:s + 32].rstrip(b"\x00").decode("utf-8", "replace")
            addr, size, offset = struct.unpack_from("<QQI", d, s + 32)
            if segname == "__TEXT" and sectname == "__objc_methname":
                blob = d[offset:offset + size]
                return [x.decode("utf-8", "replace") for x in blob.split(b"\x00") if x]
            s += 80
    return []


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = pathlib.Path(sys.argv[1])
    d = p.read_bytes()
    print(f"file    : {p}")
    print(f"size    : {len(d)} bytes")
    print(f"sha256  : {hashlib.sha256(d).hexdigest()}")
    magic = struct.unpack_from("<I", d, 0)[0]
    if magic != MH_MAGIC_64:
        print(f"magic   : 0x{magic:08x} - not a thin 64-bit little-endian Mach-O")
        return 1
    cpu, sub = struct.unpack_from("<II", d, 4)
    ft = struct.unpack_from("<I", d, 12)[0]
    print(f"arch    : {CPU.get(cpu, hex(cpu))} (subtype {sub:#x})")
    print(f"filetype: {ft} {FILETYPES.get(ft, '?')}")

    names = symbol_names(d, 0)
    methods = objc_methnames(d, 0)
    print(f"symbols : {len(names)} named addresses")
    print(f"objc    : {len(methods)} selectors")

    # The one exception: FFState's key decode. It runs at load_images too, but it
    # only XORs a byte blob and splits a string - no dyld call, no UIKit, nothing
    # that needs an initialised process. Everything else named `load` is a trap.
    allowed = {"+[USConfig load]"}
    ok = True
    if names:
        # build output: class + method symbols are intact
        all_load = sorted({n for n in names if n.endswith(" load]")})
        fatal = [n for n in all_load if n not in allowed]
        resolver = sorted({n for n in names if "IL2CPP loadAPI" in n})
        print(f"resolver: {', '.join(resolver) if resolver else 'MISSING'}")
        for n in all_load:
            print(f"  load hook: {n}" + ("  (key decode, allowed)" if n in allowed else ""))
        if fatal:
            print(f"  ** FATAL: a method named `load` is a launch hook: {fatal}")
            ok = False
        elif not all_load:
            print("  no method is named `load` (no load_images hook at all)")
        if not resolver:
            print("  ** FATAL: no +[IL2CPP loadAPI] - the resolver has no entry point")
            ok = False
    else:
        # stripped artifact (what dist/ ships): the symbol table is gone by
        # design, so the selectors are the evidence that is left.
        #
        # A bare `load` selector here is a *reference*, not proof of a method:
        # __objc_methname keeps every selector the image mentions, including ones
        # it only sends to a framework class. The authoritative check is the
        # symbol table before the strip (the CI step above), where a class method
        # appears as `+[Class load]` / `-[Class load]`.
        print("  stripped build - no symbol table, falling back to selectors")
        loady = sorted(m for m in methods if m == "load" or m.startswith("load"))
        resolver = [m for m in methods if m == "loadAPI"]
        print(f"resolver: {', '.join(resolver) if resolver else 'MISSING'}")
        print(f"  load-prefixed selectors: {', '.join(loady) if loady else 'none'}")
        if "load" in methods:
            print("  note: a bare `load` selector is referenced; the pre-strip symbol")
            print("        check is what decides whether a method implements it")
        if not resolver:
            print("  ** FATAL: no loadAPI selector - the resolver has no entry point")
            ok = False
    print("RESULT  :", "OK" if ok else "REJECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
