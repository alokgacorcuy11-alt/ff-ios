#!/usr/bin/env python3
"""il2cpp_symbols.py - prove the il2cpp runtime is reachable, from the file.

native/Core/IL2CPP.m resolves ~50 il2cpp_* symbols with dlsym. That only works
if Unity exported them. This tool answers that question on the IPA itself,
before anything is signed or installed, and reports the address of each one.

  python tools/il2cpp_symbols.py <ipa>                    # audit
  python tools/il2cpp_symbols.py <ipa> --out out/il2cpp_symbols.json
  python tools/il2cpp_symbols.py <ipa> --require           # fail if any are missing

The --out table is a fallback: if a future Unity build marks the symbols hidden
(some do), the dylib can still find them by image-base + offset instead of
dlsym. IL2CPP.m already tries the Mach-O symbol table second for that reason.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.got_scan import pick  # noqa: E402

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_DYSYMTAB = 0x0B

# exactly the set native/Core/IL2CPP.m resolves; keep them in sync
REQUIRED = [
    "il2cpp_domain_get", "il2cpp_domain_get_assemblies", "il2cpp_assembly_get_image",
    "il2cpp_class_from_name", "il2cpp_class_get_name", "il2cpp_class_get_namespace",
    "il2cpp_object_get_class", "il2cpp_object_unbox",
    "il2cpp_class_get_field_from_name", "il2cpp_class_get_fields",
    "il2cpp_field_get_name", "il2cpp_field_get_type", "il2cpp_field_get_offset",
    "il2cpp_field_get_flags", "il2cpp_field_get_value", "il2cpp_field_set_value",
    "il2cpp_field_static_get_value", "il2cpp_type_get_name",
    "il2cpp_runtime_invoke", "il2cpp_resolve_icall", "il2cpp_thread_attach",
    "il2cpp_string_length",
]

OPTIONAL = [
    "il2cpp_image_get_name", "il2cpp_image_get_class_count", "il2cpp_image_get_class",
    "il2cpp_class_from_type", "il2cpp_class_get_type", "il2cpp_class_is_valuetype",
    "il2cpp_class_is_enum", "il2cpp_class_get_element_class", "il2cpp_class_has_parent",
    "il2cpp_class_num_fields", "il2cpp_class_get_nested_types",
    "il2cpp_class_get_declaring_type", "il2cpp_class_get_parent",
    "il2cpp_class_get_type_token", "il2cpp_object_new", "il2cpp_value_box",
    "il2cpp_class_get_method_from_name", "il2cpp_class_get_methods",
    "il2cpp_method_get_name", "il2cpp_method_get_param_count",
    "il2cpp_method_get_return_type", "il2cpp_field_static_set_value",
    "il2cpp_type_get_type", "il2cpp_type_is_static", "il2cpp_array_length",
    "il2cpp_offset_of_array_length_in_array_object_header",
    "il2cpp_array_object_header_size", "il2cpp_string_chars", "il2cpp_string_new",
    "il2cpp_free", "il2cpp_thread_detach", "il2cpp_runtime_class_init",
]

# the icalls the reader needs, resolved by name at runtime, not by symbol
ICALLS = [
    "UnityEngine.Camera::get_main",
    "UnityEngine.Camera::WorldToScreenPoint_Injected",
    "UnityEngine.Camera::WorldToScreenPoint",
    "UnityEngine.Camera::get_fieldOfView",
    "UnityEngine.Transform::get_position_Injected",
    "UnityEngine.Transform::get_position",
    "UnityEngine.Transform::get_rotation_Injected",
    "UnityEngine.Transform::set_rotation_Injected",
]


def parse(data: bytes):
    if struct.unpack_from("<I", data, 0)[0] not in (0xFEEDFACF, 0xFEEDFACE):
        be = struct.unpack_from(">I", data, 0)[0]
        if be not in (0xCAFEBABE, 0xCAFEBABF):
            raise SystemExit("not a Mach-O")
        # fat: take the first 64-bit slice
        n = struct.unpack_from(">I", data, 4)[0]
        off = 8
        for _ in range(n):
            _c, _s, o, _sz, _a = struct.unpack_from(">iiIII", data, off)
            off += 20
            if struct.unpack_from("<I", data, o)[0] == 0xFEEDFACF:
                return parse_slice(data, o)
        raise SystemExit("no 64-bit slice")
    return parse_slice(data, 0)


def parse_slice(data: bytes, base: int):
    ncmds = struct.unpack_from("<I", data, base + 16)[0]
    p = base + 32
    segs, secs = [], []
    symtab = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmd == LC_SEGMENT_64 and cmdsize >= 72:
            nm = data[p + 8:p + 24].split(b"\x00")[0].decode("ascii", "replace")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, p + 24)
            segs.append((nm, vmaddr, vmsize, fileoff, filesize))
            nsects = struct.unpack_from("<I", data, p + 64)[0]
            sp = p + 72
            for _s in range(nsects):
                if sp + 80 > p + cmdsize:
                    break
                sn = data[sp:sp + 16].split(b"\x00")[0].decode("ascii", "replace")
                addr, size = struct.unpack_from("<QQ", data, sp + 32)
                secs.append((nm, sn, addr, size))
                sp += 80
        elif cmd == LC_SYMTAB:
            symtab = struct.unpack_from("<IIII", data, p + 8)
        p += cmdsize
    if not symtab:
        raise SystemExit("no LC_SYMTAB")
    return segs, secs, symtab, base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("--out", default=None)
    ap.add_argument("--require", action="store_true",
                    help="exit 1 if any required symbol is missing")
    ap.add_argument("--all", action="store_true", help="list every il2cpp_* symbol")
    a = ap.parse_args()

    target = pathlib.Path(a.ipa)
    data, name = pick(target)
    segs, secs, symtab, base = parse(data)

    symoff, nsyms, stroff, strsize = symtab
    strtab = data[stroff:stroff + strsize]

    def section_of(vm: int) -> str:
        for seg, sn, addr, size in secs:
            if size and addr <= vm < addr + size:
                return f"{seg},{sn}"
        return "?"

    found: dict[str, dict] = {}
    for i in range(nsyms):
        o = symoff + i * 16
        strx, typ, sect, desc, value = struct.unpack_from("<IBBHQ", data, o)
        if strx == 0 or strx >= len(strtab):
            continue
        e = strtab.find(b"\x00", strx)
        s = strtab[strx:e if e > 0 else len(strtab)].decode("ascii", "replace")
        if not s.startswith("_il2cpp_"):
            continue
        clean = s.lstrip("_")
        kind = typ & 0x0E
        found[clean] = {
            "vm": value,
            "file_offset": next((fo + (value - va) for nm, va, vs, fo, fs in segs
                                 if fs and va <= value < va + max(vs, fs)), None),
            "section": section_of(value),
            "external": bool(typ & 0x01),
            "defined": kind == 0x0E,
        }

    print("=" * 78)
    print(f" il2cpp symbols   {name}")
    print("=" * 78)
    print(f" il2cpp_* symbols in the symbol table : {len(found)}")

    sections = {}
    exported = 0
    for nm, info in found.items():
        sections[info["section"]] = sections.get(info["section"], 0) + 1
        if info["external"] and info["defined"]:
            exported += 1
    print(f" defined + external (dlsym-visible)   : {exported}")
    for k, v in sorted(sections.items(), key=lambda x: -x[1]):
        print(f"   {v:5d}  {k}")
    print()

    miss_req = [s for s in REQUIRED if s not in found]
    miss_opt = [s for s in OPTIONAL if s not in found]
    print(f" required by IL2CPP.m : {len(REQUIRED) - len(miss_req)}/{len(REQUIRED)} present")
    if miss_req:
        for s in miss_req:
            print(f"   MISSING  {s}")
    print(f" optional             : {len(OPTIONAL) - len(miss_opt)}/{len(OPTIONAL)} present")
    for s in miss_opt:
        print(f"   absent   {s}")

    print()
    sel = REQUIRED[:6]
    for s in sel:
        info = found.get(s)
        if not info:
            continue
        print(f"   {s:<48} 0x{info['vm']:x}  {info['section']}  "
              f"ext={'Y' if info['external'] else 'n'}")

    if a.all:
        print()
        for nm in sorted(found):
            i = found[nm]
            print(f"   {nm:<56} 0x{i['vm']:x}  {i['section']}")

    if a.out:
        outp = pathlib.Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        image_base = min(va for _n, va, _vs, _fo, fs in segs if fs) if segs else 0
        outp.write_text(json.dumps({
            "binary": name,
            "image_base": image_base,
            "symbols": {k: {**v, "offset": (v["vm"] - image_base)} for k, v in found.items()},
        }, indent=1), encoding="utf-8")
        print(f"\n wrote {outp} ({len(found)} symbols)")

    print()
    print(" verdict:")
    if not miss_req:
        print("   dlsym will find every required symbol: the dylib can resolve")
        print("   classes, fields, types and icalls on the device with no offsets")
        print("   compiled in, and no schema file to keep updated.")
    else:
        print("   some required symbols are missing - the dylib would refuse to")
        print("   load. Do not ship this build.")

    if a.require and miss_req:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())