"""Name the functions behind a crash offset without a symbol table.

The dylib is stripped, but Objective-C metadata is not: every class keeps a
method list of (selector, IMP). Reading that gives real names for the offsets a
crash log reports. Pointers in the file may be encoded by dyld chained fixups,
so they are decoded before use.

Usage:
    python tools/objcsym.py <dylib> <offset-hex> [<offset-hex> ...]
"""
import pathlib
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
MASK = 0x00007FFFFFFFFFF8


class Image:
    def __init__(self, data: bytes):
        self.d = data
        self.segments = []
        self.sections = {}
        ncmds = struct.unpack_from("<I", data, 16)[0]
        p = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, p)
            if cmd == LC_SEGMENT_64:
                seg = data[p + 8:p + 24].split(b"\x00")[0].decode()
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from(
                    "<QQQQ", data, p + 24)
                self.segments.append((seg, vmaddr, vmsize, fileoff, filesize))
                nsects = struct.unpack_from("<I", data, p + 64)[0]
                q = p + 72
                for _ in range(nsects):
                    sname = data[q:q + 16].split(b"\x00")[0].decode()
                    addr, sz, off = struct.unpack_from("<QQI", data, q + 32)
                    self.sections[(seg, sname)] = (addr, sz, off)
                    q += 80
            p += size

    # -- addressing ------------------------------------------------------
    def off(self, vm: int):
        for _, vmaddr, vmsize, fileoff, _ in self.segments:
            if vmaddr <= vm < vmaddr + vmsize:
                return fileoff + (vm - vmaddr)
        return None

    def u32(self, vm: int) -> int:
        o = self.off(vm)
        return struct.unpack_from("<I", self.d, o)[0] if o is not None else 0

    def i32(self, vm: int) -> int:
        o = self.off(vm)
        return struct.unpack_from("<i", self.d, o)[0] if o is not None else 0

    def u64(self, vm: int) -> int:
        o = self.off(vm)
        return struct.unpack_from("<Q", self.d, o)[0] if o is not None else 0

    def ptr(self, vm: int) -> int:
        """Read a pointer, undoing dyld chained-fixup encoding if present."""
        v = self.u64(vm)
        if v >> 43:                      # 64-bit chained fixup: target is an offset
            return v & 0xFFFFFFFFF
        return v

    def cstr(self, vm: int) -> str:
        o = self.off(vm)
        if o is None:
            return "?"
        end = self.d.find(b"\x00", o)
        return self.d[o:end].decode("utf-8", "replace")

    # -- objc metadata ---------------------------------------------------
    def methods(self):
        """Yield (imp, label) for every method in every class of the image."""
        sec = self.sections.get(("__DATA", "__objc_classlist"))
        if sec is None:
            sec = self.sections.get(("__DATA_CONST", "__objc_classlist"))
        if sec is None:
            return
        addr, size, _ = sec
        for i in range(size // 8):
            cls = self.ptr(addr + i * 8)
            if not cls:
                continue
            data = self.ptr(cls + 32) & MASK
            if not data:
                continue
            name = self.cstr(self.ptr(data + 24))
            mlist = self.ptr(data + 32)
            yield from self._mlist(mlist, f"+[{name}")
            # instance methods hang off the meta-class, whose ro data is the
            # second field of the meta-class struct - simpler: the class_t's
            # isa points at the meta-class, and its ro list is instance methods
            meta = self.ptr(cls) & MASK
            if meta:
                mdata = self.ptr(meta + 32) & MASK
                if mdata:
                    yield from self._mlist(self.ptr(mdata + 32), f"-[{name}")

    def _mlist(self, mlist: int, prefix: str):
        if not mlist:
            return
        entsize_flags = self.u32(mlist)
        count = self.u32(mlist + 4)
        relative = bool(entsize_flags & 0x80000000)
        step = (entsize_flags & 0x0000FFFC) or (12 if relative else 24)
        if count > 5000 or step < 12:
            return
        p = mlist + 8
        for _ in range(count):
            if relative:
                n = self.i32(p)
                imp = (self.i32(p + 8) + p + 8) & 0xFFFFFFFF
                name = self.cstr(p + n)
            else:
                name = self.cstr(self.ptr(p))
                imp = self.ptr(p + 16)
            yield (imp, f"{prefix} {name}]")
            p += step


def main() -> int:
    path = pathlib.Path(sys.argv[1])
    want = [int(a, 16) for a in sys.argv[2:]]
    img = Image(path.read_bytes())
    print(f"{path.name}  {len(img.d)} bytes")
    for seg, vmaddr, vmsize, fileoff, filesize in img.segments:
        print(f"  {seg:<16} vm 0x{vmaddr:08x}-0x{vmaddr + vmsize:x}  "
              f"file 0x{fileoff:x}")
    ms = sorted(set(img.methods()))
    print(f"  {len(ms)} objc methods with IMPs")
    for w in want:
        inside = [m for m in ms if m[0] <= w]
        if not inside:
            print(f"  +0x{w:05x} -> before every method")
            continue
        imp, label = inside[-1]
        nxt = next((m[0] for m in ms if m[0] > w), None)
        span = f" next@+0x{nxt:x}" if nxt else ""
        print(f"  +0x{w:05x} -> {label} +0x{w - imp:x}{span}   (imp +0x{imp:x})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
