#!/usr/bin/env python3
"""sofix.py - put the pointer tables back into an Android libil2cpp.so.

Why this exists
---------------
A Unity Android build ships libil2cpp.so with its big pointer tables - the
metadata registration, the code registration, every field offset array - as
zeros in the file. That is not damage. It is what a shared object with
`R_AARCH64_RELATIVE` relocations is supposed to look like: the linker was told
those values can be computed at load time, so storing them in the file would be
wasted bytes.

Which means a dumper that reads the file sees tables full of zeros and refuses,
and that is exactly the state Free Fire 1.132.1 was in. The data is not missing.
It is in .rela.dyn, and it can be put back:

    R_AARCH64_RELATIVE at r_offset with r_addend
        value = r_addend        (the link-time virtual address of the target)

A dumper wants link-time virtual addresses, not load-time ones, so the addend is
the value to write. After this runs, the same file that read as zeros reads as a
complete image, with no rooted device and no running game involved.

Verified against Free Fire 1.132.1 before this was written:

    .data.rel.ro            3,198,184 B, 97.6% zeros, 61,257 live words
    .rela.dyn               1,014,714 entries, 1,014,705 of type RELATIVE
    332,825 of them target .data.rel.ro

  python tools/ffdump/sofix.py IN.so OUT.so
  python tools/ffdump/sofix.py IN.so OUT.so --stats-only
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

R_AARCH64_ABS64 = 257
R_AARCH64_GLOB_DAT = 1025
R_AARCH64_RELATIVE = 1027
R_AARCH64_JUMP_SLOT = 1026


class Elf:
    """Just enough ELF64 to move between virtual addresses and file offsets."""

    def __init__(self, data: bytes):
        if data[:4] != b"\x7fELF":
            raise ValueError("not an ELF file")
        if data[4] != 2 or data[5] != 1:
            raise ValueError("only 64-bit little-endian is handled")
        self.data = data
        self.e_type, = struct.unpack_from("<H", data, 0x10)
        self.phoff, = struct.unpack_from("<Q", data, 0x20)
        self.shoff, = struct.unpack_from("<Q", data, 0x28)
        self.phentsize, self.phnum = struct.unpack_from("<HH", data, 0x36)
        self.shentsize, self.shnum, self.shstrndx = struct.unpack_from("<HHH", data, 0x3A)
        self.loads = self._loads()
        self.sections = self._sections()

    def _loads(self):
        out = []
        for i in range(self.phnum):
            o = self.phoff + i * self.phentsize
            p_type, p_flags = struct.unpack_from("<II", self.data, o)
            p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = struct.unpack_from(
                "<QQQQQ", self.data, o + 8)
            if p_type == 1:  # PT_LOAD
                out.append((p_vaddr, p_offset, p_filesz, p_memsz, p_flags))
        return out

    def _sections(self):
        names_at = self.shoff + self.shstrndx * self.shentsize
        str_off, = struct.unpack_from("<Q", self.data, names_at + 0x18)
        str_size, = struct.unpack_from("<Q", self.data, names_at + 0x20)
        strings = self.data[str_off:str_off + str_size]
        out = []
        for i in range(self.shnum):
            o = self.shoff + i * self.shentsize
            name, sh_type = struct.unpack_from("<II", self.data, o)
            flags, = struct.unpack_from("<Q", self.data, o + 8)
            addr, off, size = struct.unpack_from("<QQQ", self.data, o + 0x10)
            entsize, = struct.unpack_from("<Q", self.data, o + 0x38)
            end = strings.find(b"\x00", name)
            out.append(dict(name=strings[name:end].decode("ascii", "replace"),
                            type=sh_type, flags=flags, addr=addr, off=off,
                            size=size, entsize=entsize))
        return out

    def find(self, name: str):
        for s in self.sections:
            if s["name"] == name:
                return s
        return None

    def to_offset(self, vaddr: int):
        for p_vaddr, p_offset, p_filesz, _memsz, _f in self.loads:
            if p_vaddr <= vaddr < p_vaddr + p_filesz:
                return p_offset + (vaddr - p_vaddr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="apply RELATIVE relocations to an ELF")
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--stats-only", action="store_true",
                    help="count and report, write nothing")
    args = ap.parse_args()

    if not args.src.is_file():
        print(f"  missing: {args.src}")
        return 1

    raw = args.src.read_bytes()
    elf = Elf(raw)
    print(f"  {args.src.name}: {len(raw):,} B, e_type={elf.e_type}, "
          f"{len(elf.loads)} PT_LOAD, {len(elf.sections)} sections")

    rela = elf.find(".rela.dyn")
    if rela is None:
        print("  no .rela.dyn")
        return 1
    step = rela["entsize"] or 24
    entries = rela["size"] // step
    print(f"  .rela.dyn: {entries:,} entries")

    out = bytearray(raw)
    applied = skipped_missing = 0
    others = {}
    sample = []

    for i in range(entries):
        o = rela["off"] + i * step
        r_offset, r_info, r_addend = struct.unpack_from("<QQq", raw, o)
        r_type = r_info & 0xFFFFFFFF
        if r_type != R_AARCH64_RELATIVE:
            others[r_type] = others.get(r_type, 0) + 1
            continue
        at = elf.to_offset(r_offset)
        if at is None:
            skipped_missing += 1
            continue
        struct.pack_into("<Q", out, at, r_addend & 0xFFFFFFFFFFFFFFFF)
        applied += 1
        if len(sample) < 5:
            sample.append((r_offset, r_addend & 0xFFFFFFFFFFFFFFFF))

    print(f"  RELATIVE applied: {applied:,}")
    if skipped_missing:
        print(f"  RELATIVE skipped (target not in a PT_LOAD): {skipped_missing:,}")
    for t, c in sorted(others.items(), key=lambda kv: -kv[1]):
        name = {R_AARCH64_ABS64: "ABS64", R_AARCH64_GLOB_DAT: "GLOB_DAT",
                R_AARCH64_JUMP_SLOT: "JUMP_SLOT"}.get(t, str(t))
        print(f"  not RELATIVE, left alone: type {t} ({name}) x{c:,}")

    for off, val in sample:
        sec = next((s["name"] for s in elf.sections
                    if s["addr"] <= val < s["addr"] + max(s["size"], 1)), None)
        print(f"    {off:#012x} -> {val:#012x}  {sec or 'outside any section'}")

    dro = elf.find(".data.rel.ro")
    if dro:
        before = sum(1 for i in range(0, dro["size"] - 8, 8)
                     if struct.unpack_from("<Q", raw, dro["off"] + i)[0] != 0)
        after = sum(1 for i in range(0, dro["size"] - 8, 8)
                    if struct.unpack_from("<Q", out, dro["off"] + i)[0] != 0)
        print(f"  .data.rel.ro live words: {before:,} -> {after:,}")

    if args.stats_only:
        print("  stats only, nothing written")
        return 0

    args.dst.write_bytes(out)
    print(f"  wrote {args.dst} ({len(out):,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
