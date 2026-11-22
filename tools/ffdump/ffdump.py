"""The dump: real class names, real field names, real field offsets.

Everything below is read from Free Fire's own two files.

  global-metadata.dat   type definitions, fields, methods  (decoded above)
  libil2cpp.so          Il2CppMetadataRegistration.fieldOffsets, which holds
                        the byte offset of every field of every type

The field offsets are not in the metadata. They live in the binary, in an array
of 43,627 pointers - one per type - each pointing at that type's offsets. The
struct was found at RVA 0xa5c7f60 and its fieldOffsets entry reads:

    count 43,627   pointer 0xa8bf168

43,627 is exactly the number of types, which is what makes that location certain
rather than probable.
"""

import struct
from pathlib import Path

META = Path(r"D:\ffdump\meta\global-metadata.dat")
SO = Path(r"D:\ffdump\fixed\libil2cpp.so")

blob = META.read_bytes()
so = SO.read_bytes()

STR_OFF, STR_LEN = 2_002_364, 16_048_368
TYP_OFF, TYP_LEN, STRIDE = 51_171_844, 3_839_176, 88
FLD_OFF, FLD_LEN, FSTRIDE = 41_969_288, 5_707_692, 12
MTH_OFF, MTH_LEN, MSTRIDE = 18_574_128, 13_453_160, 40
IMG_OFF = 55_013_260
N_TYPES = TYP_LEN // STRIDE
N_FIELDS = FLD_LEN // FSTRIDE
N_METHODS = MTH_LEN // MSTRIDE

META_REG = 0xA5C7F60
FIELD_OFFSETS_PTR = 0xA8BF168
IMAGE_STRIDE = 56

# --- VA -> file offset, from the program headers --------------------------
phoff, = struct.unpack_from("<Q", so, 0x20)
phent, phnum = struct.unpack_from("<HH", so, 0x36)
SEGS = []
for i in range(phnum):
    o = phoff + i * phent
    t, = struct.unpack_from("<I", so, o)
    off, va, pa, fsz, msz = struct.unpack_from("<QQQQQ", so, o + 8)
    if t == 1:
        SEGS.append((va, off, fsz))


def fo(va):
    for v, o, s in SEGS:
        if v <= va < v + s:
            return o + (va - v)
    return None


def u32(va):
    o = fo(va)
    return struct.unpack_from("<I", so, o)[0] if o is not None else None


def u64(va):
    o = fo(va)
    return struct.unpack_from("<Q", so, o)[0] if o is not None else None


def s(i):
    if i < 0 or i >= STR_LEN:
        return ""
    a = STR_OFF + i
    b = blob.find(b"\x00", a)
    return blob[a:b].decode("utf-8", "replace") if 0 <= b - a <= 300 else ""


def tf(k):
    return TYP_OFF + k * STRIDE


# --- find fieldStart / methodStart by the chain test ----------------------
def chain(off, count_slot):
    good = 0
    for k in range(N_TYPES - 1):
        a = struct.unpack_from("<i", blob, tf(k) + off)[0]
        c = struct.unpack_from("<H", blob, tf(k) + count_slot * 2)[0]
        b = struct.unpack_from("<i", blob, tf(k + 1) + off)[0]
        if a + c == b:
            good += 1
    return good


print("  int32 offsets, chain rate against the counts")
FIELD_SLOT, METHOD_SLOT = 34, 32          # byte 68 and byte 64
cands = []
for off in range(0, STRIDE - 3, 4):
    g = chain(off, FIELD_SLOT)
    cands.append((g, off))
cands.sort(reverse=True)
for g, off in cands[:6]:
    print(f"    +{off:<3} chains {g:,}/{N_TYPES - 1} ({g * 100.0 / (N_TYPES - 1):.1f}%)")

FIELD_START = cands[0][1]
m = []
for off in range(0, STRIDE - 3, 4):
    g = chain(off, METHOD_SLOT)
    m.append((g, off))
m.sort(reverse=True)
METHOD_START = m[0][1]
print(f"  fieldStart  @+{FIELD_START}   methodStart @+{METHOD_START}")

# --- load the four tables -------------------------------------------------
print("  loading tables...")
names, namespaces, fstart, mstart = [], [], [], []
fcount, mcount = [], []
for k in range(N_TYPES):
    b = tf(k)
    ni, nsi = struct.unpack_from("<ii", blob, b)
    names.append(s(ni))
    namespaces.append(s(nsi))
    fstart.append(struct.unpack_from("<i", blob, b + FIELD_START)[0])
    mstart.append(struct.unpack_from("<i", blob, b + METHOD_START)[0])
    fcount.append(struct.unpack_from("<H", blob, b + FIELD_SLOT * 2)[0])
    mcount.append(struct.unpack_from("<H", blob, b + METHOD_SLOT * 2)[0])

fnames = []
for j in range(N_FIELDS):
    ni, ti, tok = struct.unpack_from("<iii", blob, FLD_OFF + j * FSTRIDE)
    fnames.append((s(ni), ti, tok))

mnames = []
for j in range(N_METHODS):
    ni, dt, rt = struct.unpack_from("<iii", blob, MTH_OFF + j * MSTRIDE)
    mnames.append((s(ni), dt, rt))

images = []
for k in range(400):
    o = IMG_OFF + k * IMAGE_STRIDE
    if o + IMAGE_STRIDE > len(blob):
        break
    ni, = struct.unpack_from("<i", blob, o)
    nm = s(ni)
    if nm:
        images.append(nm)

print(f"  {N_TYPES:,} types  {N_FIELDS:,} fields  {N_METHODS:,} methods  "
      f"{len(images)} images")
print()

# --- field offsets, straight out of the binary ---------------------------
import re
import sys

args = sys.argv[1:]
mode = args[0] if args else "--demo"
pat = re.compile(args[1], re.I) if len(args) > 1 else None


def dump_type(k, max_fields=60, max_methods=16, field_filter=None):
    ptr = u64(FIELD_OFFSETS_PTR + k * 8)
    ns = f"{namespaces[k]}." if namespaces[k] else ""
    print(f"{ns}{names[k]}   (type {k}, {fcount[k]} fields, {mcount[k]} methods)")
    if ptr is None or fo(ptr) is None or ptr < 0x10000:
        print("    field offsets: not in the offset array (value type or static)")
        return
    shown = 0
    for j in range(fcount[k]):
        nm, ti, tok = fnames[fstart[k] + j]
        if field_filter is not None and not field_filter.search(nm):
            continue
        if shown >= max_fields:
            print(f"    ... more fields match")
            break
        off = u32(ptr + j * 4)
        tag = "  <static> " if off == 0 else ""
        print(f"    +{off:#06x} {tag:<10} {nm:<44} type {ti:>7}")
        shown += 1
    if field_filter is None and fcount[k] > max_fields:
        print(f"    ... and {fcount[k] - max_fields} more")
    ms = [mnames[mstart[k] + i][0] for i in range(min(mcount[k], max_methods))]
    if ms:
        print(f"    methods: {', '.join(ms)}")


print("=" * 78)
if mode == "--class" and pat is not None:
    ffilter = re.compile(args[2], re.I) if len(args) > 2 else None
    for k in range(N_TYPES):
        full = f"{namespaces[k]}.{names[k]}" if namespaces[k] else names[k]
        if pat.search(names[k]) or pat.search(full):
            dump_type(k, 200, 10, ffilter)
            print()

elif mode == "--fields" and pat is not None:
    hits = []
    for k in range(N_TYPES):
        for j in range(fcount[k]):
            nm = fnames[fstart[k] + j][0]
            if pat.search(nm):
                hits.append((fcount[k], k, nm))
                break
    hits.sort(reverse=True)
    print(f"  {len(hits)} classes have a field matching {pat.pattern!r}")
    for fc, k, nm in hits[:35]:
        ns = f"{namespaces[k]}." if namespaces[k] else ""
        print(f"    {fc:>4} fields  {ns}{names[k]}")

elif mode == "--list" and pat is not None:
    hits = [k for k in range(N_TYPES)
            if pat.search(names[k])
            or pat.search(f"{namespaces[k]}.{names[k]}")]
    print(f"  {len(hits)} types match")
    for k in hits[:60]:
        ns = f"{namespaces[k]}." if namespaces[k] else ""
        print(f"    {ns}{names[k]:<44} {fcount[k]:>4} fields {mcount[k]:>5} methods")

else:
    for want in ("GameFacade", "LocalPlayer", "PlayerAttributes", "MainCamera"):
        for k in range(N_TYPES):
            if names[k] == want:
                dump_type(k, 18, 8)
                print()
                break
print("=" * 78)

