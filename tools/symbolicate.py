"""Symbolicate a crash offset against the exact dylib we shipped.

Usage:
    python tools/symbolicate.py <dylib> <offset-hex> [<offset-hex> ...]

Crash logs give `imageOffset`, relative to the image base. The dylib is
stripped of its symbol table on the deployment, but the Theos build keeps the
ld -g function entries, which is what names an offset.

The offsets are also matched against the Objective-C method list, because a
crash inside a method is common and only the metadata knows the selector.
"""
import pathlib
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SYMTAB = 0x2
MASK = 0x00007FFFFFFFFFF8


def load_commands(d, off=0):
    ncmds = struct.unpack_from("<I", d, off + 16)[0]
    p = off + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", d, p)
        yield cmd, size, p
        p += size


def segments(d):
    out = []
    for cmd, size, p in load_commands(d):
        if cmd == 0x19:
            name = d[p + 8:p + 24].split(b"\x00")[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", d, p + 24)
            out.append((name, vmaddr, vmsize, fileoff, filesize))
    return out


class Image:
    def __init__(self, data):
        self.d = data
        self.segs = segments(data)
        self.sections = {}
        for cmd, size, p in load_commands(data):
            if cmd != 0x19:
                continue
            seg = data[p + 8:p + 24].split(b"\x00")[0].decode()
            nsects = struct.unpack_from("<I", data, p + 64)[0]
            q = p + 72
            for _ in range(nsects):
                sname = data[q:q + 16].split(b"\x00")[0].decode()
                addr, sz, off = struct.unpack_from("<QQI", data, q + 32)
                self.sections[(seg, sname)] = (addr, sz, off)
                q += 80

    def off(self, vm):
        for _, vmaddr, vmsize, fileoff, _ in self.segs:
            if vmaddr <= vm < vmaddr + vmsize:
                return fileoff + (vm - vmaddr)
        return None

    def u32(self, vm):
        o = self.off(vm)
        return struct.unpack_from("<I", self.d, o)[0] if o is not None else 0

    def i32(self, vm):
        o = self.off(vm)
        return struct.unpack_from("<i", self.d, o)[0] if o is not None else 0

    def u64(self, vm):
        o = self.off(vm)
        return struct.unpack_from("<Q", self.d, o)[0] if o is not None else 0

    def ptr(self, vm):
        """Read a pointer, undoing dyld chained-fixup encoding if present."""
        v = self.u64(vm)
        return v & 0xFFFFFFFFF if v >> 43 else v

    def cstr(self, vm):
        o = self.off(vm)
        if o is None:
            return "?"
        end = self.d.find(b"\x00", o)
        return self.d[o:end].decode("utf-8", "replace")


def function_symbols(d):
    """(address, name) from the ld -g entries, which survive the strip."""
    symoff = nsyms = stroff = strsize = None
    for cmd, _, p in load_commands(d):
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", d, p + 8)
    if symoff is None:
        return []
    strtab = d[stroff:stroff + strsize]
    rows = {}
    for i in range(nsyms):
        strx, typ, sect, desc, value = struct.unpack_from("<IBBHQ", d, symoff + i * 16)
        if not value or strx == 0 or strx >= len(strtab):
            continue
        end = strtab.find(b"\x00", strx)
        nm = strtab[strx:end].decode("utf-8", "replace")
        if nm:
            rows.setdefault(value, nm)
    return sorted(rows.items())


def objc_methods(img):
    sec = img.sections.get(("__DATA", "__objc_classlist")) or \
        img.sections.get(("__DATA_CONST", "__objc_classlist"))
    if not sec:
        return []
    addr, size, _ = sec
    out = []
    for i in range(size // 8):
        cls = img.ptr(addr + i * 8)
        if not cls:
            continue
        ro = img.ptr(cls + 32) & MASK                          # instance methods
        if ro:
            name = img.cstr(img.ptr(ro + 24))
            out += img._mlist(img.ptr(ro + 32), f"-[{name}")
        meta = img.ptr(cls) & MASK                             # class methods
        mro = img.ptr(meta + 32) & MASK if meta else 0
        if mro:
            name = img.cstr(img.ptr(mro + 24))
            out += img._mlist(img.ptr(mro + 32), f"+[{name}")
    return sorted(set(out))


def _mlist(img, mlist, prefix):
    if not mlist:
        return []
    entsize_flags = img.u32(mlist)
    count = img.u32(mlist + 4)
    relative = bool(entsize_flags & 0x80000000)
    step = (entsize_flags & 0x0000FFFC) or (12 if relative else 24)
    if count > 5000 or step < 12:
        return []
    out, p = [], mlist + 8
    for _ in range(count):
        if relative:
            n = img.i32(p)
            imp = (img.i32(p + 8) + p + 8) & 0xFFFFFFFF
            name = img.cstr(p + n)
        else:
            name = img.cstr(img.ptr(p))
            imp = img.ptr(p + 16)
        out.append((imp, f"{prefix} {name}]"))
        p += step
    return out


Image._mlist = _mlist


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1])
    want = [int(a, 16) for a in sys.argv[2:]]
    d = path.read_bytes()
    if struct.unpack_from("<I", d, 0)[0] != MH_MAGIC_64:
        sys.exit("not a thin 64-bit little-endian Mach-O")
    img = Image(d)
    funcs = function_symbols(d)
    methods = objc_methods(img)
    print(f"{path.name}  {len(d)} bytes, {len(funcs)} functions, "
          f"{len(methods)} objc methods\n")
    for w in want:
        best = next(((v, n) for v, n in reversed(funcs) if v <= w), None)
        if best:
            print(f"  0x{w:05x}  {best[1]} +0x{w - best[0]:x}   (function)")
        meth = next(((v, n) for v, n in reversed(methods) if v <= w), None)
        if meth:
            print(f"  0x{w:05x}  {meth[1]} +0x{w - meth[0]:x}   (method)")
        if not best and not meth:
            print(f"  0x{w:05x}  no symbol at or below this offset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
