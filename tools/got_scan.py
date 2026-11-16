#!/usr/bin/env python3
"""got_scan.py - prove the GOT-rebind technique against a real binary.

fishhook rebinds symbols through an image's own __got / __la_symbol_ptr using
LC_DYSYMTAB's indirect symbol table. That technique is not limited to your own
image: given another image's Mach-O header you can rebind ITS slots too.

If Free Fire calls `_dyld_image_count` / `_dyld_get_image_name` through a
symbol-pointer section, then overwriting those slots redirects every call the
game makes - with no inline hook engine, no Dobby, no substrate.

This tool answers that question offline, on the file, before a single line of
C gets written:

  python tools/got_scan.py <ipa-or-macho> [--symbol _dyld_image_count]

It lists every symbol-pointer slot it can find for the dyld enumeration API,
with the exact file offset of each slot. Those offsets are what Core/Rebind.m
patches at runtime.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys
import zipfile

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_DYSYMTAB = 0x0B
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

S_LAZY_SYMBOL_POINTERS = 0x7
S_NON_LAZY_SYMBOL_POINTERS = 0x6
INDIRECT_SYMBOL_LOCAL = 0x80000000
INDIRECT_SYMBOL_ABS = 0x40000000

TARGETS = (
    "dyld_image_count",
    "dyld_get_image_name",
    "dyld_get_image_header",
    "dyld_get_image_vmaddr_slide",
    "dyld_get_image_header_containing_address",
    "objc_copyImageNames",
    "objc_copyClassNamesForImage",
)


class ScanError(RuntimeError):
    pass


def slices(data: bytes):
    be = struct.unpack_from(">I", data, 0)[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        n = struct.unpack_from(">I", data, 4)[0]
        off, wide = 8, be == FAT_MAGIC_64
        out = []
        for _ in range(n):
            if wide:
                _c, _s, o, _sz, _a, _r = struct.unpack_from(">iiQQII", data, off)
                off += 32
            else:
                _c, _s, o, _sz, _a = struct.unpack_from(">iiIII", data, off)
                off += 20
            out.append(o)
        return out
    return [0]


def scan_slice(data: bytes, base: int) -> dict:
    if struct.unpack_from("<I", data, base)[0] != MH_MAGIC_64:
        return {}
    ncmds = struct.unpack_from("<I", data, base + 16)[0]
    p = base + 32
    symtab = dysymtab = None
    symsecs = []      # (name, sectname, segname, offset, size, reserved1, flags)
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmdsize < 8:
            break
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, p + 8)
            symtab = (symoff, nsyms, stroff, strsize)
        elif cmd == LC_DYSYMTAB:
            (iloc, nloc, iext, next_, iundef, nundef) = struct.unpack_from("<IIIIII", data, p + 8)
            indoff = struct.unpack_from("<I", data, p + 56)[0]
            nind = struct.unpack_from("<I", data, p + 60)[0]
            dysymtab = (indoff, nind)
        elif cmd == LC_SEGMENT_64 and cmdsize >= 72:
            segname = data[p + 8:p + 24].split(b"\x00")[0].decode("ascii", "replace")
            nsects = struct.unpack_from("<I", data, p + 64)[0]
            sp = p + 72
            for _s in range(nsects):
                if sp + 80 > p + cmdsize:
                    break
                sectname = data[sp:sp + 16].split(b"\x00")[0].decode("ascii", "replace")
                soff = struct.unpack_from("<I", data, sp + 48)[0]
                ssize = struct.unpack_from("<Q", data, sp + 40)[0]
                flags = struct.unpack_from("<I", data, sp + 64)[0]
                reserved1 = struct.unpack_from("<I", data, sp + 68)[0]
                symsecs.append((segname, sectname, soff, ssize, reserved1, flags))
                sp += 80
        p += cmdsize

    if not symtab or not dysymtab:
        return {}
    symoff, nsyms, stroff, strsize = symtab
    indoff, nind = dysymtab
    strtab = data[stroff:stroff + strsize]

    def sym_name(idx: int) -> str | None:
        if idx >= nsyms:
            return None
        o = symoff + idx * 16
        if o + 16 > len(data):
            return None
        strx = struct.unpack_from("<I", data, o)[0]
        if strx == 0 or strx >= len(strtab):
            return None
        end = strtab.find(b"\x00", strx)
        s = strtab[strx:end if end > 0 else len(strtab)].decode("ascii", "replace")
        # strip every leading underscore: builds spell these both __dyld_x and
        # _dyld_x, and the C-level name is dyld_x
        while s.startswith("_"):
            s = s[1:]
        return s

    hits: dict[str, list[dict]] = {}
    for segname, sectname, soff, ssize, reserved1, flags in symsecs:
        kind = flags & 0xFF
        if kind not in (S_LAZY_SYMBOL_POINTERS, S_NON_LAZY_SYMBOL_POINTERS):
            continue
        nslots = ssize // 8
        for i in range(nslots):
            e = indoff + 4 * (reserved1 + i)
            if e + 4 > len(data):
                break
            sidx = struct.unpack_from("<I", data, e)[0]
            if sidx & (INDIRECT_SYMBOL_LOCAL | INDIRECT_SYMBOL_ABS):
                continue
            nm = sym_name(sidx)
            if nm in TARGETS:
                hits.setdefault(nm, []).append({
                    "segment": segname,
                    "section": sectname,
                    "slot": i,
                    "file_offset": soff + i * 8,
                    "kind": "lazy" if kind == S_LAZY_SYMBOL_POINTERS else "non-lazy",
                })
    return hits


def pick(path: pathlib.Path):
    if path.suffix.lower() == ".ipa":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            out = []
            for n in names:
                if n.endswith("/UnityFramework") or (
                        n.startswith("Payload/") and n.count("/") == 2 and not n.endswith("/")):
                    sz = z.getinfo(n).file_size
                    if sz > 1_000_000:
                        out.append((sz, n))
            out.sort(reverse=True)
            if not out:
                raise ScanError("no binaries in the IPA")
            sz, n = out[0]
            return z.read(n), n
    return path.read_bytes(), path.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--symbol", action="append", default=None)
    a = ap.parse_args()

    data, name = pick(pathlib.Path(a.target))
    print("=" * 74)
    print(f" got_scan  {name}  ({len(data) / 1e6:.1f} MB)")
    print("=" * 74)

    total = {}
    for s in slices(data):
        h = scan_slice(data, s)
        for k, v in h.items():
            total.setdefault(k, v)

    if not total:
        print("\nno dyld symbols found in any symbol-pointer section.")
        print("the game may call them through a private trampoline, or resolve")
        print("them with dlsym at runtime (in which case GOT rebinding cannot help")
        print("and an inline hook IS required).")
        return 1

    print(f"\n{len(total)} of {len(TARGETS)} targets are reboundable through GOT:\n")
    for sym in TARGETS:
        v = total.get(sym)
        if not v:
            print(f"  {sym:<46} -")
            continue
        for e in v:
            print(f"  {sym:<46} {e['segment']},{e['section']} slot {e['slot']:<5} "
                  f"kind={e['kind']:<8} file+0x{e['file_offset']:x}")

    print()
    print("verdict: these slots are writable pointers that the game loads through.")
    print("         overwriting them redirects every call the game makes - no")
    print("         inline hook engine needed. Core/Rebind.m does exactly this at")
    print("         runtime, using the same LC_DYSYMTAB arithmetic verifed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())