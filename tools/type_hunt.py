#!/usr/bin/env python3
"""type_hunt.py - find the real gameplay classes by field TYPE, not by name.

Why this exists: in 1.132.1 the `COW.GamePlay.*` layer has 57.6% of its member
names randomised (see native/REPORT-1.132.1.md). Name matching finds UI code.
But field *types* are not obfuscated - a player entity is still
`{ Vector3, float, int, List<...> }` no matter what the members are called.

How it works, and why each step is self-validating:

  1. parse LC_SEGMENT_64 in the il2cpp-bearing binary -> vmaddr -> file offset
  2. scan the data segments for the longest run of 8-byte words that each point
     at a valid `Il2CppType` (flags word at +8 with a type tag in 1..0x1E).
     That run IS `Il2CppMetadataRegistration.types`.
     Confirmed by: the 4 bytes just before the pointer that references the array
     must equal the run length.
  3. GROUND TRUTH CHECK: every typeDefinition's `byvalTypeIndex` must resolve to
     an Il2CppType of kind CLASS/VALUETYPE whose klassIndex is that same
     definition index. This is circular by construction, so if it passes for
     ~all 43k types, the type table is the right one.
  4. resolve every field's type, recursing through SZARRAY / ARRAY / GENERICINST,
     so `List<X>` and `X[]` come out with their element type.

Then it scores classes by shape. No device needed.

  python tools/type_hunt.py <ipa> --selftest
  python tools/type_hunt.py <ipa> --fields COW.GamePlay.BaseLevelObject
  python tools/type_hunt.py <ipa> --shape
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import struct
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.metadata_structure import (  # noqa: E402
    read_metadata, header_pairs, string_table, find_by_token, name_at,
)

# ---------------------------------------------------------------- constants
LC_SEGMENT_64 = 0x19
MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

T = {
    0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "sbyte", 0x05: "byte",
    0x06: "short", 0x07: "ushort", 0x08: "int", 0x09: "uint",
    0x0A: "long", 0x0B: "ulong", 0x0C: "float", 0x0D: "double",
    0x0E: "string", 0x0F: "ptr", 0x10: "byref", 0x11: "VALUETYPE",
    0x12: "CLASS", 0x13: "VAR", 0x14: "ARRAY", 0x15: "GENERICINST",
    0x16: "typedref", 0x18: "IntPtr", 0x19: "UIntPtr", 0x1B: "fnptr",
    0x1C: "object", 0x1D: "SZARRAY", 0x1E: "MVAR",
}
PRIMITIVE = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,
             0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x16, 0x18, 0x19, 0x1C}


class HuntError(RuntimeError):
    pass


# ---------------------------------------------------------------- mach-o
class MachO:
    def __init__(self, data: bytes, label: str):
        self.data = data
        self.label = label
        self.segments: list[dict] = []
        self._parse()

    def _parse(self) -> None:
        be = struct.unpack_from(">I", self.data, 0)[0]
        if be in (FAT_MAGIC, FAT_MAGIC_64):
            n = struct.unpack_from(">I", self.data, 4)[0]
            off = 8
            wide = be == FAT_MAGIC_64
            # take the first 64-bit slice - arm64 and arm64e share the layout
            for _ in range(n):
                if wide:
                    _c, _s, o, sz, _a, _r = struct.unpack_from(">iiQQII", self.data, off)
                    off += 32
                else:
                    _c, _s, o, sz, _a = struct.unpack_from(">iiIII", self.data, off)
                    off += 20
                if struct.unpack_from("<I", self.data, o)[0] == MH_MAGIC_64:
                    self.slice_off = o
                    break
            else:
                raise HuntError("no 64-bit slice in the fat binary")
        else:
            if struct.unpack_from("<I", self.data, 0)[0] != MH_MAGIC_64:
                raise HuntError("not a 64-bit Mach-O")
            self.slice_off = 0
        self._parse_slice()

    def _parse_slice(self) -> None:
        base = self.slice_off
        ncmds = struct.unpack_from("<I", self.data, base + 16)[0]
        p = base + 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", self.data, p)
            if cmdsize < 8:
                break
            if cmd == LC_SEGMENT_64 and cmdsize >= 72:
                nm = self.data[p + 8:p + 24].split(b"\x00")[0].decode("ascii", "replace")
                vmaddr, vmsize = struct.unpack_from("<QQ", self.data, p + 24)
                fileoff, filesize = struct.unpack_from("<QQ", self.data, p + 40)
                self.segments.append({
                    "name": nm, "vmaddr": vmaddr, "vmsize": vmsize,
                    "fileoff": fileoff, "filesize": filesize,
                })
            p += cmdsize

    # vm address -> file offset
    def foff(self, vm: int) -> int | None:
        for s in self.segments:
            if s["vmaddr"] <= vm < s["vmaddr"] + max(s["filesize"], 1):
                return s["fileoff"] + (vm - s["vmaddr"])
        return None

    def vm_of(self, foff: int) -> int | None:
        for s in self.segments:
            if s["fileoff"] <= foff < s["fileoff"] + max(s["filesize"], 1):
                return s["vmaddr"] + (foff - s["fileoff"])
        return None

    def data_segments(self):
        for s in self.segments:
            if s["name"] == "__TEXT" or s["name"] == "__LINKEDIT" or s["filesize"] == 0:
                continue
            yield s

    def read_u32(self, vm: int) -> int | None:
        o = self.foff(vm)
        if o is None or o + 4 > len(self.data):
            return None
        return struct.unpack_from("<I", self.data, o)[0]

    def read_u64(self, vm: int) -> int | None:
        o = self.foff(vm)
        if o is None or o + 8 > len(self.data):
            return None
        return struct.unpack_from("<Q", self.data, o)[0]

    def read_u16(self, vm: int) -> int | None:
        o = self.foff(vm)
        if o is None or o + 2 > len(self.data):
            return None
        return struct.unpack_from("<H", self.data, o)[0]


# ---------------------------------------------------------------- il2cpp type
class TypeTable:
    """Locate Il2CppMetadataRegistration.types and resolve type indices.

    Candidate runs of Il2CppType pointers are scored by the ground-truth
    relation, not by length: for the real table, `types[typeDefinition.byvalTypeIndex]`
    must be a CLASS/VALUETYPE whose klassIndex is that type's own index. A
    coincidental run of pointers fails that immediately.
    """

    def __init__(self, macho: MachO, classes_names: list[str], sindex: dict,
                 tdefs: list[dict]):
        self.m = macho
        self.class_names = classes_names        # index -> "Ns.Name"
        self.tdefs = tdefs
        self.sindex = sindex
        self.types_off = 0                      # file offset of the pointer array
        self.types_vm = 0
        self.count = 0
        self.score = 0.0
        self.candidates = 0
        self._cache: dict[int, str] = {}
        self._locate()

    # ---- step 2: find the array
    def _valid_type_ptr(self, vm: int) -> bool:
        o = self.m.foff(vm)
        if o is None or (o & 7):
            return False
        if o + 12 > len(self.m.data):
            return False
        flags = struct.unpack_from("<I", self.m.data, o + 8)[0]
        tag = (flags >> 16) & 0xFF
        if tag == 0 or tag > 0x1E:
            return False
        if ((flags >> 24) & 0x3F) > 1:           # num_mods
            return False
        # attrs is a 16-bit TypeAttributes bitmask - the high bits never set
        if (flags & 0xFFFF) > 0x4000:
            return False
        return True

    def _sample_ground_truth(self, sample: int = 300) -> float:
        """Fraction of sampled typeDefinitions that resolve back to themselves."""
        if not self.count or not self.tdefs:
            return 0.0
        step = max(1, len(self.tdefs) // sample)
        good = tot = 0
        for t in self.tdefs[::step]:
            idx = t["byval"]
            if not (0 <= idx < self.count):
                tot += 1
                continue
            vm = self.array_entry(idx)
            tot += 1
            if self.kind(vm) in (0x11, 0x12) and self.klass_index(vm) == t["index"]:
                good += 1
            if tot >= sample:
                break
        return good / tot if tot else 0.0

    def _locate(self) -> None:
        """Scan every file-backed segment for runs of Il2CppType pointers.

        The array is usually in __TEXT,__const on Mach-O (not __DATA), so we
        scan everything. The per-pointer check is inlined and uses a single
        segment delta, because a type array never straddles segments.
        """
        d = self.m.data
        n = len(d)
        tcount = len(self.tdefs)
        runs: list[tuple[int, int]] = []          # count, file_off

        for s in self.m.segments:
            if s["filesize"] == 0 or s["name"] in ("__PAGEZERO", "__LINKEDIT"):
                continue
            start = s["fileoff"]
            end = min(start + s["filesize"], n)
            delta = s["fileoff"] - s["vmaddr"]
            run = 0
            run_start = 0
            p = start
            unpack = struct.unpack_from
            while p + 8 <= end:
                vm = unpack("<Q", d, p)[0]
                ok = False
                if vm:
                    o = vm + delta
                    if 0 <= o < n - 12 and (o & 7) == 0:
                        flags = unpack("<I", d, o + 8)[0]
                        tag = (flags >> 16) & 0xFF
                        if 1 <= tag <= 0x1E and ((flags >> 24) & 0x3F) <= 1 \
                                and (flags & 0xFFFF) <= 0x4000:
                            du = unpack("<q", d, o)[0]
                            # data union: 0, a typeDefinition index, or a pointer
                            if du == 0 or 0 < du < tcount or (0 <= du + delta < n - 12):
                                ok = True
                if ok:
                    if run == 0:
                        run_start = p
                    run += 1
                else:
                    if run >= 500:
                        runs.append((run, run_start))
                    run = 0
                p += 8
            if run >= 500:
                runs.append((run, run_start))

        if not runs:
            raise HuntError(
                "no run of Il2CppType pointers found.\n"
                "  the il2cpp type table may live in a different binary")

        runs.sort(key=lambda c: -c[0])
        self.candidates = len(runs)
        best = None
        for count, foff in runs[:80]:
            self.types_off = foff
            self.count = count
            self._cache.clear()
            sc = self._sample_ground_truth()
            if best is None or sc > best[0]:
                best = (sc, count, foff)
            if sc >= 0.995:
                break
        if best is None:
            raise HuntError("no candidate run scored")
        self.score, self.count, self.types_off = best
        self.types_vm = self.m.vm_of(self.types_off) or 0
        self._cache.clear()

        if self.score < 0.90:
            raise HuntError(
                f"best candidate run only scored {self.score:.1%} on the "
                f"ground-truth relation over {self.candidates} runs - the type "
                f"table was not found. The binary or metadata pair may not match.")

        # the count must sit right before the pointer that references the array
        ref_off = None
        for s in self.m.segments:
            if s["filesize"] == 0:
                continue
            start = s["fileoff"] & ~7
            end = min(s["fileoff"] + s["filesize"], n)
            p = start
            while p + 8 <= end:
                if struct.unpack_from("<Q", d, p)[0] == self.types_vm:
                    if p >= 4 and struct.unpack_from("<i", d, p - 4)[0] == self.count:
                        ref_off = p
                        break
                p += 8
            if ref_off is not None:
                break
        self.ref_off = ref_off
        self.registration_off = (ref_off - 4 - 48) if ref_off is not None else None
        self.warning = None if ref_off is not None else (
            "array verified by ground truth but its count field was not found "
            "next to a reference - harmless, the relation is what matters")

    # ---- step 3: ground truth
    def ground_truth(self, td: list[dict]) -> tuple[int, int, list[str]]:
        good = bad = 0
        examples = []
        for t in td:
            idx = t["byval"]
            if not (0 <= idx < self.count):
                bad += 1
                continue
            vm = self.array_entry(idx)
            tag = self.kind(vm)
            k = self.klass_index(vm)
            if tag in (0x11, 0x12) and k == t["index"]:
                good += 1
            else:
                bad += 1
                if len(examples) < 5:
                    examples.append(f"{t['name']}: idx={idx} tag={T.get(tag, hex(tag))} klass={k} != {t['index']}")
        return good, bad, examples

    # ---- raw accessors
    def array_entry(self, i: int) -> int:
        return struct.unpack_from("<Q", self.m.data, self.types_off + i * 8)[0]

    def kind(self, vm: int) -> int:
        flags = self.m.read_u32(vm + 8) or 0
        return (flags >> 16) & 0xFF

    def klass_index(self, vm: int) -> int:
        o = self.m.foff(vm)
        if o is None:
            return -1
        return struct.unpack_from("<i", self.m.data, o)[0]

    def ptr_at(self, vm: int, delta: int = 0) -> int:
        return self.m.read_u64(vm + delta) or 0

    # ---- step 4: resolve to a readable name
    def resolve(self, index: int, depth: int = 0) -> str:
        if index in self._cache:
            return self._cache[index]
        if depth > 6 or not (0 <= index < self.count):
            return "?"
        self._cache[index] = "..."          # break recursion
        name = self._resolve_uncached(index, depth)
        self._cache[index] = name
        return name

    def _resolve_uncached(self, index: int, depth: int) -> str:
        vm = self.array_entry(index)
        tag = self.kind(vm)
        if tag in PRIMITIVE:
            return T.get(tag, f"tag{tag}")
        if tag in (0x11, 0x12):                       # CLASS / VALUETYPE
            k = self.klass_index(vm)
            if 0 <= k < len(self.class_names):
                return self.class_names[k]
            return f"class#{k}"
        if tag == 0x1D:                               # SZARRAY
            elem = self.ptr_at(vm)
            return self._resolve_vm(elem, depth + 1) + "[]"
        if tag == 0x14:                               # ARRAY
            arr = self.ptr_at(vm)                     # Il2CppArrayType*
            etype = self.m.read_u64(arr) if arr else 0
            return self._resolve_vm(etype, depth + 1) + "[,]"
        if tag == 0x15:                               # GENERICINST
            gc = self.ptr_at(vm)                      # Il2CppGenericClass*
            tdef = self.m.read_u64(gc) if gc else 0
            base = self._resolve_vm(tdef, depth + 1)
            inst = self.m.read_u64(gc + 8) if gc else 0   # context.class_inst
            out = base
            if inst:
                argc = self.m.read_u32(inst) or 0
                argv = self.m.read_u64(inst + 8) or 0
                if 0 < argc <= 8 and argv:
                    parts = []
                    for i in range(argc):
                        a = self.m.read_u64(argv + i * 8) or 0
                        parts.append(self._resolve_vm(a, depth + 1))
                    out = f"{base}<{', '.join(parts)}>"
            return out
        if tag == 0x0F:                               # PTR
            return self._resolve_vm(self.ptr_at(vm), depth + 1) + "*"
        if tag in (0x13, 0x1E):
            return "T"
        return T.get(tag, f"tag{tag}")

    def _resolve_vm(self, vm: int, depth: int) -> str:
        """Resolve an Il2CppType* by its address (needed for generic args)."""
        if not vm or depth > 7:
            return "?"
        tag = self.kind(vm)
        if tag in PRIMITIVE:
            return T.get(tag, f"tag{tag}")
        if tag in (0x11, 0x12):
            k = self.klass_index(vm)
            if 0 <= k < len(self.class_names):
                return self.class_names[k]
            return f"class#{k}"
        # rebuild an index so the cache still applies: linear scan is too slow,
        # so resolve inline for the nested cases
        if tag == 0x1D:
            return self._resolve_vm(self.ptr_at(vm), depth + 1) + "[]"
        if tag == 0x15:
            gc = self.ptr_at(vm)
            base = self._resolve_vm(self.m.read_u64(gc) if gc else 0, depth + 1)
            inst = self.m.read_u64(gc + 8) if gc else 0
            if inst:
                argc = self.m.read_u32(inst) or 0
                argv = self.m.read_u64(inst + 8) or 0
                if 0 < argc <= 8 and argv:
                    parts = [self._resolve_vm(self.m.read_u64(argv + i * 8) or 0, depth + 1)
                             for i in range(argc)]
                    return f"{base}<{', '.join(parts)}>"
            return base
        if tag == 0x14:
            arr = self.ptr_at(vm)
            return self._resolve_vm(self.m.read_u64(arr) if arr else 0, depth + 1) + "[,]"
        return T.get(tag, f"tag{tag}")


# ---------------------------------------------------------------- loading
def pick_binary(ipa: pathlib.Path) -> tuple[bytes, str]:
    with zipfile.ZipFile(ipa) as z:
        names = z.namelist()
        app = None
        for n in names:
            i = n.find(".app/")
            if n.startswith("Payload/") and i > 0:
                app = n[:i + 4]
                break
        if not app:
            raise HuntError("no Payload/*.app")
        app = app.rstrip("/") + "/"
        pref = []
        for n in names:
            if n.startswith(f"{app}Frameworks/") and n.endswith("UnityFramework"):
                pref.append(n)
        for n in names:
            if n.startswith(f"{app}") and n.count("/") == app.count("/") and not n.endswith("/"):
                pref.append(n)
        for n in pref:
            if n.endswith("/") or n.endswith(".plist") or n.endswith(".png"):
                continue
            data = z.read(n)
            if len(data) < 1_000_000:
                continue
            try:
                MachO(data, n)
            except HuntError:
                continue
            return data, n
    raise HuntError("no Mach-O binary with il2cpp data found")


def load_metadata(ipa: pathlib.Path):
    data = read_metadata(ipa)
    version, str_off, str_size, blob, sindex = string_table(data)
    pairs = header_pairs(data)
    fields_sec = find_by_token(data, pairs, 0x04, [12])
    typedef_sec = find_by_token(data, pairs, 0x02, list(range(80, 120, 4)))
    if not fields_sec or not typedef_sec:
        raise HuntError("could not locate metadata sections")
    fsize, foff, _fl, _fr, fcount = fields_sec
    tsize, toff, _tl, _tr, tcount = typedef_sec

    td = []
    for r in range(tcount):
        base = toff + r * tsize
        ni = struct.unpack_from("<i", data, base)[0]
        nsi = struct.unpack_from("<i", data, base + 4)[0]
        byval = struct.unpack_from("<i", data, base + 8)[0]
        fs = struct.unpack_from("<i", data, base + 32)[0]
        fc = struct.unpack_from("<H", data, base + 68)[0]
        cn = name_at(sindex, ni) if 0 <= ni < str_size else ""
        ns = name_at(sindex, nsi) if 0 <= nsi < str_size else ""
        td.append({"index": r, "name": f"{ns}.{cn}" if ns else cn,
                   "byval": byval, "fs": fs, "fc": fc})
    field_defs = []
    for r in range(fcount):
        ni = struct.unpack_from("<i", data, foff + r * fsize)[0]
        ti = struct.unpack_from("<i", data, foff + r * fsize + 4)[0]
        field_defs.append((name_at(sindex, ni) if 0 <= ni < str_size else "", ti))
    return td, field_defs


# ---------------------------------------------------------------- shape scoring
PLAYER_STATS = ("hp", "health", "life")
POS_TYPES = ("UnityEngine.Vector3", "UnityEngine.Vector2", "UnityEngine.Vector3Int")


def shape_score(fields: dict[str, str]) -> dict:
    """Fields is name -> resolved type. Score by structure, never by name."""
    s = {"pos": 0, "float": 0, "int": 0, "list": 0, "bool": 0, "str": 0,
         "obj": 0, "score": 0, "evidence": []}
    for n, t in fields.items():
        if t in POS_TYPES:
            s["pos"] += 1
            s["evidence"].append(f"{n}:{t}")
        elif t == "float":
            s["float"] += 1
        elif t == "int":
            s["int"] += 1
        elif t == "bool":
            s["bool"] += 1
        elif t == "string":
            s["str"] += 1
        elif t.endswith("[]") or "<" in t:
            s["list"] += 1
            s["evidence"].append(f"{n}:{t}")
        elif t.startswith("UnityEngine.") or "." in t:
            s["obj"] += 1
    # a player entity: exactly the shape of a transform + stats + identity
    score = 0
    if s["pos"] >= 1:
        score += 3
    if s["float"] >= 1:
        score += 2
    if s["int"] >= 1:
        score += 1
    if s["str"] >= 1:
        score += 1
    if s["list"] >= 1:
        score += 1
    if 1 < len(fields) < 60:
        score += 1
    s["score"] = score
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fields", default=None, help="dump resolved field types for a class")
    ap.add_argument("--shape", action="store_true", help="rank classes by shape")
    ap.add_argument("--ns", default="COW.GamePlay", help="namespace filter for --shape")
    ap.add_argument("--out", default="out/types.json")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()

    src = pathlib.Path(a.ipa)
    print("=" * 76)
    print(f" type_hunt  {src.name}")
    print("=" * 76)

    bin_data, bin_name = pick_binary(src)
    print(f" binary      : {bin_name}  ({len(bin_data) / 1e6:.1f} MB)")
    m = MachO(bin_data, bin_name)
    print(f" segments    : {', '.join(s['name'] for s in m.segments)}")

    td, field_defs = load_metadata(src)
    names = [t["name"] for t in td]
    print(f" types       : {len(td)}")

    tt = TypeTable(m, names, {}, td)
    print(f" types array : file+0x{tt.types_off:x}  count={tt.count}"
          f"  candidates={tt.candidates}  groundtruth={tt.score:.1%}")
    if tt.warning:
        print(f" warn        : {tt.warning}")

    good, bad, ex = tt.ground_truth(td)
    tot = good + bad
    print(f" ground truth: {good}/{tot} typeDefinitions resolve back to themselves "
          f"({100.0 * good / max(tot, 1):.1f}%)")
    for e in ex:
        print(f"   ! {e}")
    if good / max(tot, 1) < 0.90:
        print("[!] ground truth failed - the type table is not the right one")
        return 1
    print("[ok] type table verified")
    print()

    if a.selftest:
        print("--- selftest: engine types must resolve to known shapes ---")
        want = {
            "UnityEngine.Vector3": {"x": "float", "y": "float", "z": "float"},
            "UnityEngine.Vector2": {"x": "float", "y": "float"},
        }
        fails = 0
        for cls, expect in want.items():
            if cls not in names:
                print(f"  {cls}: not present (stripped)")
                continue
            i = names.index(cls)
            t = td[i]
            got = {field_defs[t["fs"] + k][0]: tt.resolve(field_defs[t["fs"] + k][1])
                   for k in range(t["fc"])}
            ok = all(got.get(k) == v for k, v in expect.items())
            print(f"  {cls}: {got}  {'OK' if ok else 'MISMATCH'}")
            fails += 0 if ok else 1
        print(f"  -> {'PASS' if not fails else f'{fails} MISMATCH'}")
        print()
        if fails:
            return 1

    if a.fields:
        if a.fields not in names:
            print(f"[!] class {a.fields} not found")
            return 1
        i = names.index(a.fields)
        t = td[i]
        print(f"--- {a.fields}  ({t['fc']} fields) ---")
        for k in range(t["fc"]):
            nm, ti = field_defs[t["fs"] + k]
            print(f"  {nm:<34} {tt.resolve(ti)}")
        print()
        return 0

    # ---- shape pass over every class
    print(f"--- resolving field types for {len(td)} classes ---")
    resolved: dict[str, dict[str, str]] = {}
    for t in td:
        if t["fc"] == 0 or t["fs"] < 0 or not t["name"]:
            continue
        d = {}
        for k in range(t["fc"]):
            nm, ti = field_defs[t["fs"] + k]
            d[nm] = tt.resolve(ti)
        resolved[t["name"]] = d

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resolved, ensure_ascii=False), encoding="utf-8")
    print(f" wrote        : {out}  ({out.stat().st_size / 1e6:.1f} MB, "
          f"{len(resolved)} classes)")
    print()

    if a.shape:
        pool = {k: v for k, v in resolved.items()
                if not a.ns or k.startswith(a.ns)}
        scored = []
        for k, v in pool.items():
            s = shape_score(v)
            if s["pos"] and s["score"] >= 6:
                scored.append((s["score"], k, s, v))
        scored.sort(key=lambda x: -x[0])
        print(f"--- entity candidates in {a.ns} (Vector3 + scalars shape) ---")
        for score, k, s, v in scored[:a.top]:
            print(f"  [{score}] {k}  ({len(v)} fields)")
            for n, ty in list(v.items())[:12]:
                mark = "  <-- position" if ty in POS_TYPES else ""
                print(f"        {n:<30} {ty}{mark}")
            print()

        # containers: a class with a List<X> where X is an entity candidate
        ent = {k.split(".")[-1] for _s, k, _x, _v in scored[:400]}
        cont = []
        for k, v in resolved.items():
            for n, ty in v.items():
                if not ty.startswith(("System.Collections.Generic.List<", "System.Collections.Generic.Dictionary<")):
                    continue
                inner = ty[ty.find("<") + 1:ty.rfind(">")]
                base = inner.split(",")[0].split(".")[-1].strip()
                if base in ent:
                    cont.append((k, n, ty, v))
                    break
        cont.sort(key=lambda c: len(c[3]))
        print(f"--- container candidates (hold a List/Dictionary of an entity) ---")
        for k, n, ty, v in cont[:a.top]:
            print(f"  {k}")
            print(f"        {n} : {ty}   [{len(v)} fields total]")
        print()
        sing = [c for c in cont if any(
            f.startswith("m_") or f[0].isupper() for f in c[3])][:a.top]
        if cont:
            print(f"--- next: confirm static/instance + the singleton on device ---")
            print(f"  frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js")
            print(f'  then:  dumpStatics("{cont[0][0]}")')

    return 0


if __name__ == "__main__":
    from tools import metadata_structure  # noqa: F401
    sys.exit(main())