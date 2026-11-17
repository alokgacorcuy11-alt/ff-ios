"""Symbolicate crash offsets against the exact dylib we shipped.

Usage:
    python tools/symbolicate.py <dylib> <offset-hex> [<offset-hex> ...]

Reads LC_SYMTAB from every slice, then prints the symbol that contains each
offset (the one with the largest address <= offset). No guesswork: if the
dylib is the one in the crash log, this names the function.
"""
import pathlib
import struct
import sys

LC_SYMTAB = 0x2
FAT_MAGIC = 0xCAFEBABE


def slices_of(data: bytes):
    be = struct.unpack_from(">I", data, 0)[0]
    out = []
    if be == FAT_MAGIC:
        n = struct.unpack_from(">I", data, 4)[0]
        for i in range(n):
            _, _, off, size, _ = struct.unpack_from(">IIIII", data, 8 + i * 20)
            out.append((off, size))
    else:
        out.append((0, len(data)))
    return out


def symtab(data: bytes, off: int):
    """Return (nlist list, strtab) for the Mach-O at `off`."""
    magic = struct.unpack_from("<I", data, off)[0]
    if magic != 0xFEEDFACF:
        return [], b""
    ncmds = struct.unpack_from("<I", data, off + 16)[0]
    p = off + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, p)
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, p + 8)
            syms = []
            for i in range(nsyms):
                q = symoff + i * 16
                n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from(
                    "<IBBHQ", data, q)
                syms.append((n_strx, n_type, n_sect, n_desc, n_value))
            return syms, data[stroff:stroff + strsize]
        p += size
    return [], b""


def name_of(strtab: bytes, strx: int) -> str:
    end = strtab.find(b"\x00", strx)
    return strtab[strx:end if end >= 0 else None].decode("utf-8", "replace")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    want = [int(a, 16) for a in sys.argv[2:]]
    data = path.read_bytes()
    print(f"{path.name}: {len(data)} bytes, sha256 skipped")
    for off, size in slices_of(data):
        syms, strtab = symtab(data, off)
        if not syms:
            continue
        # defined symbols in __TEXT only (n_sect != 0, n_type & N_TYPE == N_SECT)
        defined = [(v, name_of(strtab, s)) for s, t, sec, d, v in syms
                   if sec != 0 and (t & 0x0E) == 0x0E and v != 0]
        defined.sort()
        print(f"\nslice 0x{off:x}: {len(defined)} defined symbols, "
              f"text ends ~0x{max(v for v, _ in defined):x}" if defined else "")
        for w in want:
            best = None
            for v, nm in defined:
                if v <= w:
                    best = (v, nm)
                else:
                    break
            if best:
                print(f"  +0x{w:05x} -> {best[1]} +0x{w - best[0]:x}"
                      f"   [{best[1]}]")
            else:
                print(f"  +0x{w:05x} -> (before the first symbol)")
        # Also show the 4 symbols closest above each offset, to catch
        # local/static functions that nlist may not carry.
        print("  nearby symbols:")
        for v, nm in defined:
            if any(abs(v - w) < 0x2000 for w in want):
                print(f"    0x{v:05x} {nm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
