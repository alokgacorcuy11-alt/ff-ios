#!/usr/bin/env python3
"""metadata_structure.py - turn global-metadata.dat into class -> fields.

The string table (tools/metadata_hunt.py) gives you every name in the build but
not which class owns which field. This walks the actual metadata sections.

No hardcoded offsets. Every section is located by validating its own content:

  * every IL2CPP metadata record ends with a `token` whose high byte is the
    ECMA-335 table id - 0x02 TypeDef, 0x04 Field, 0x06 MethodDef, 0x08 Param.
    That pins a section and its record size with no version table at all.
  * the per-type `fieldStart` / `field_count` slots are found by requiring the
    constraint to hold for ALL records: start non-decreasing, start+count within
    the field array, count within uint16 range.

If any of that fails it says so instead of inventing a mapping.

  python tools/metadata_structure.py <ipa-or-global-metadata.dat>
  python tools/metadata_structure.py <ipa> --find-field players
  python tools/metadata_structure.py <ipa> --class Player --fields
  python tools/metadata_structure.py <ipa> --propose
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import sys
import zipfile

SANITY = 0xFAB11BAF
STRING_OFF_POS = 24
STRING_SIZE_POS = 28
MAX_HEADER_PAIRS = 96

TYPEDEF_TABLE = 0x02
FIELD_TABLE = 0x04
METHOD_TABLE = 0x06


class StructError(RuntimeError):
    pass


# ------------------------------------------------------------------ loading
def read_metadata(src: pathlib.Path) -> bytes:
    if src.suffix.lower() in (".dat",):
        return src.read_bytes()
    if src.suffix.lower() == ".ipa":
        with zipfile.ZipFile(src) as z:
            hits = sorted((n for n in z.namelist() if n.endswith("global-metadata.dat")),
                          key=len)
            if not hits:
                raise StructError("global-metadata.dat not found in the IPA")
            return z.read(hits[0])
    raise StructError(f"don't know how to read {src}")


def header_pairs(data: bytes) -> list[tuple[int, int, int]]:
    """(index, offset, size) for each metadata section.

    A real section always starts after the header that describes it, so anything
    whose offset falls inside the header is garbage read past the end of it.
    That one rule removes every false pair without knowing the version.
    """
    out = []
    for i in range(MAX_HEADER_PAIRS):
        p = 8 + i * 8
        if p + 8 > len(data):
            break
        off, size = struct.unpack_from("<ii", data, p)
        if off <= 0 or size <= 0 or off + size > len(data):
            continue
        if off < 8 + (i + 1) * 8:          # lies inside the header -> not a section
            continue
        out.append((i, off, size))
    return out


def find_by_token(data: bytes, pairs, table: int, sizes: list[int],
                  sample: int = 400, need: float = 0.97, min_count: int = 200):
    """A section whose every sampled record ends in a token for `table`.

    Requires a meaningful record count: a 38-record section that happens to
    match must not beat the real 43k-record one.
    """
    best = None
    for size in sizes:
        for (idx, off, length) in pairs:
            if size <= 4 or length % size:
                continue
            n = length // size
            if n < min_count:
                continue
            take = min(n, sample)
            good = 0
            for r in range(take):
                p = off + r * size + size - 4
                t = struct.unpack_from("<I", data, p)[0]
                if (t >> 24) == table:
                    good += 1
            ratio = good / take
            if ratio < need:
                continue
            if best is None or n > best[4]:
                best = (size, off, length, ratio, n)
    return best  # (rec_size, offset, length, ratio, count)


# ------------------------------------------------------------------ string table
def string_table(data: bytes):
    sanity, version = struct.unpack_from("<Ii", data, 0)
    if sanity != SANITY:
        raise StructError(f"bad sanity 0x{sanity:08x}")
    off, size = struct.unpack_from("<ii", data, STRING_OFF_POS)
    if not (0 < off < len(data)) or size <= 0 or off + size > len(data):
        raise StructError(f"string table out of range (off={off} size={size})")
    blob = data[off:off + size]

    # offset -> string, so a nameIndex (an offset) resolves in O(1)
    index: dict[int, str] = {}
    pos = 0
    while pos < len(blob):
        end = blob.find(b"\x00", pos)
        if end < 0:
            end = len(blob)
        if end > pos:
            try:
                index[pos] = blob[pos:end].decode("utf-8")
            except UnicodeDecodeError:
                pass
        pos = end + 1
    return version, off, size, blob, index


def name_at(index: dict[int, str], pos: int) -> str:
    return index.get(pos, "")


# ------------------------------------------------------------------ type->field
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_<>,\.\[\]]{0,95}$")


def locate_type_layout(data: bytes, typedef_sec, fields_count: int,
                       field_names: list[str], tcount: int):
    """Solve for (struct_size, fieldStartOff, fieldCountOff) with no hardcoding.

    The struct carries eight `*Start` / `*Count` pairs (field, method, event,
    property, nestedTypes, interfaces, vtable, interfaceOffsets). Six of them are
    internally consistent, so "contiguous" and "resolves to real names" alone can
    NOT tell fieldStart from vtableStart. The one thing that can:

        the field array is fully covered, so max(fieldStart + field_count)
        must reach the END of the fields section.

    For vtableStart/vtable_count the maximum stops at the vtable section size.
    That is the discriminator, and it needs a full scan, not a sample.
    """
    size, off, length, ratio, n = typedef_sec

    def rec(r: int) -> int:
        return off + r * size

    def u16(r: int, c: int) -> int:
        return struct.unpack_from("<H", data, rec(r) + c)[0]

    def i32(r: int, g: int) -> int:
        return struct.unpack_from("<i", data, rec(r) + g)[0]

    # ---- cheap pass: find (g, c) pairs that are at least self-consistent
    N = min(tcount, 400)
    cands = []
    for g in range(0, size - 4, 4):
        vals = [i32(r, g) for r in range(N)]
        if any(v != -1 and (v < 0 or v > fields_count) for v in vals):
            continue
        for c in range(g + 2, min(size - 1, g + 96), 2):
            ok = 0
            for r in range(N):
                fc = u16(r, c)
                fs = vals[r]
                if fs == -1:
                    if fc == 0:
                        ok += 1
                elif fc <= 512 and fs + fc <= fields_count:
                    ok += 1
            if ok == N:
                cands.append((g, c))

    # ---- full pass: coverage + violations over every record
    #
    # The decisive test is that the field array is fully covered:
    #   max(fieldStart + field_count) == number of fields, and no type may
    #   overflow the array (fs + fc <= field_count for all), and the first
    #   type that owns fields must start at index 0.
    # vtableStart/vtable_count also looks contiguous, but 18k of its types
    # overflow the FIELD array, so it dies here and fieldStart does not.
    scored = []
    for g, c in cands:
        maxend = 0
        minstart = 1 << 30
        violations = 0
        prev_fs = None
        prev_fc = 0
        good = considered = 0
        nok = ntot = 0
        for r in range(tcount):
            fc = u16(r, c)
            if fc == 0:
                continue
            fs = i32(r, g)
            if fs < 0 or fs + fc > fields_count:
                violations += 1
            else:
                if fs + fc > maxend:
                    maxend = fs + fc
                if fs < minstart:
                    minstart = fs
                if prev_fs is not None:
                    considered += 1
                    if fs == prev_fs + prev_fc:
                        good += 1
                prev_fs, prev_fc = fs, fc
            if ntot < 3000:
                for k in range(min(fc, 4)):
                    if 0 <= fs + k < fields_count:
                        ntot += 1
                        if IDENT.match(field_names[fs + k]):
                            nok += 1

        if fields_count <= 0 or violations:
            continue
        cover = maxend / fields_count
        if cover < 0.999 or (minstart != 0 and minstart < (1 << 30)):
            continue
        contig = (good / considered) if considered else 0.0
        names = (nok / ntot) if ntot else 0.0
        scored.append((cover, contig, names, g, c))

    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    cover, contig, names, g, c = scored[0]
    return (size, g, c, round(cover, 4), names, contig)


def sanity_check(classes: dict) -> list[str]:
    """Prove the solved layout is right by reading structures we can predict.

    UnityEngine.Transform and GameObject are absent on purpose - IL2CPP code
    stripping removes engine types nothing managed references. Vector3 and
    Camera are always present, so those are the checks that mean something.
    """
    notes = []
    v3 = classes.get("UnityEngine.Vector3")
    if v3:
        want = {"x", "y", "z"}
        notes.append(f"UnityEngine.Vector3 fields={len(v3)} x/y/z={sorted(want & set(v3))}")
    else:
        notes.append("UnityEngine.Vector3 MISSING - layout is wrong")
    cam = classes.get("UnityEngine.Camera")
    if cam:
        want = {"m_FieldOfView", "m_NearClipPlane", "fieldOfView", "nearClipPlane"}
        notes.append(f"UnityEngine.Camera fields={len(cam)} expected={sorted(want & set(cam))}")
    else:
        notes.append("UnityEngine.Camera MISSING - layout is wrong")
    return notes


OBFUSCATED = re.compile(r"^[A-Z]{8,16}$")


def obfuscation_stats(classes: dict) -> dict:
    """How much of the surface is name-obfuscated, and where.

    This matters a lot: obfuscated member names are re-randomised every build,
    so a name-driven offset map MUST be regenerated per patch. Readable names
    (UI, protobuf, tcp messages) survive across patches.
    """
    def buckets(pred):
        tot = obf = 0
        for k, v in classes.items():
            if not pred(k):
                continue
            for f in v:
                tot += 1
                if OBFUSCATED.match(f):
                    obf += 1
        return tot, obf

    out = {}
    for label, pred in (
        ("all", lambda k: True),
        ("COW.GamePlay.*", lambda k: k.startswith("COW.GamePlay") or k.startswith("COW.Gameplay")),
        ("COW.UI*", lambda k: k.split(".")[-1].startswith("UI")),
        ("tcp.*", lambda k: k.startswith("tcp.")),
        ("proto.*", lambda k: k.startswith("proto.")),
        ("UnityEngine.*", lambda k: k.startswith("UnityEngine.")),
        ("COW.* (not UI/GamePlay)", lambda k: k.startswith("COW.")
            and not k.split(".")[-1].startswith("UI")
            and not k.startswith("COW.GamePlay") and not k.startswith("COW.Gameplay")),
    ):
        tot, obf = buckets(pred)
        if tot:
            out[label] = {"fields": tot, "obfuscated": obf,
                          "pct": round(100.0 * obf / tot, 1)}
    return out


def build_map(data: bytes):
    version, str_off, str_size, blob, sindex = string_table(data)
    pairs = header_pairs(data)

    fields_sec = find_by_token(data, pairs, FIELD_TABLE, [12])
    if not fields_sec:
        raise StructError("could not locate the `fields` section "
                          "(no 12-byte records with a 0x04 token)")
    fsize, foff, flen, fratio, fcount = fields_sec

    typedef_sec = find_by_token(data, pairs, TYPEDEF_TABLE, list(range(80, 120, 4)))
    if not typedef_sec:
        raise StructError("could not locate the `typeDefinitions` section "
                          "(no record size in 80..116 with a 0x02 token)")
    tsize, toff, tlen, tratio, tcount = typedef_sec

    # field names, in section order - needed by the layout solver to verify that
    # a candidate slot pair resolves to real identifiers
    field_names = []
    for r in range(fcount):
        ni = struct.unpack_from("<i", data, foff + r * fsize)[0]
        field_names.append(name_at(sindex, ni) if 0 <= ni < str_size else "")

    layout = locate_type_layout(data, typedef_sec, fcount, field_names, tcount)
    if not layout:
        raise StructError("located typeDefinitions but could not solve for "
                          "fieldStart/field_count - layout unknown for this build")
    _sz, gpos, cpos, cover, names_ratio, contig = layout

    classes = {}
    # Classes with more fields than the cap are excluded from the map. That is a
    # deliberate guard for the field-tiling proof above, but it makes the dump
    # PARTIAL - and the player class (1689 fields) is one of the excluded ones.
    # Recording the exclusions is what stops the map from reading as complete:
    # "the class is absent" and "the class has no such field" are different
    # answers, and a query tool that cannot tell them apart will mislead.
    CAP = 512
    skipped_above_cap = []
    no_fields = 0
    bad_offsets = 0
    unnamed = 0
    for r in range(tcount):
        base = toff + r * tsize
        ni = struct.unpack_from("<i", data, base)[0]
        nsi = struct.unpack_from("<i", data, base + 4)[0]
        fs = struct.unpack_from("<i", data, base + gpos)[0]
        fc = struct.unpack_from("<H", data, base + cpos)[0]
        cname = name_at(sindex, ni) if 0 <= ni < str_size else ""
        ns = name_at(sindex, nsi) if 0 <= nsi < str_size else ""
        full = f"{ns}.{cname}" if ns else cname
        if not cname:
            unnamed += 1
            continue
        if fc > CAP:
            skipped_above_cap.append((full, fc))
            continue
        if fc == 0:
            # Not a defect: interfaces, enums and static-only types genuinely
            # have no instance fields. Counting these as "malformed" was wrong.
            no_fields += 1
            continue
        if fs < 0 or fs + fc > fcount:
            bad_offsets += 1
            continue
        classes[full] = field_names[fs:fs + fc]

    # Names collide: the key is namespace.name, and generic instantiations and
    # nested types can share one. So the record count and the key count differ,
    # and the difference has to be reported or the totals do not add up.
    records = tcount
    duplicates = records - len(classes) - len(skipped_above_cap) \
        - no_fields - bad_offsets - unnamed

    return {
        "version": version,
        "field_rec_size": fsize, "field_count": fcount, "field_token_ratio": round(fratio, 4),
        "typedef_rec_size": tsize, "typedef_count": tcount,
        "typedef_token_ratio": round(tratio, 4),
        "field_start_off": gpos, "field_count_off": cpos,
        "layout_coverage": cover,
        "layout_names_ratio": round(names_ratio, 4),
        "layout_contiguity": round(contig, 4),
        "class_field_cap": CAP,
        "type_records": records,
        "classes_mapped": len(classes),
        "classes_skipped_above_cap": len(skipped_above_cap),
        "classes_skipped_above_cap_largest": sorted(
            skipped_above_cap, key=lambda x: -x[1])[:25],
        "classes_without_fields": no_fields,
        "classes_bad_offsets": bad_offsets,
        "classes_unnamed": unnamed,
        "name_collisions": max(0, duplicates),
        "classes": classes,
        "sanity": sanity_check(classes),
        "obfuscation": obfuscation_stats(classes),
    }


# ------------------------------------------------------------------ queries
NEXT_KEYS = {
    "player.hp_field": ("hp", "health", "life", "m_Hp", "Hp", "HP"),
    "player.max_hp_field": ("maxHp", "maxHP", "m_MaxHp", "maxHealth"),
    "player.team_field": ("teamId", "team", "camp", "side", "group", "squad"),
    "player.name_field": ("nickName", "nickname", "playerName", "userName", "name"),
    "player.is_local_field": ("isLocal", "isLocalPlayer", "isSelf", "isMe"),
}
PLAYER_WORDS = ("player", "actor", "avatar", "hero", "character", "entity", "unit", "pawn")
CONTAINER_WORDS = ("room", "game", "match", "battle", "world", "arena", "scene", "mode")


def score_player(fields: list[str]) -> int:
    s = 0
    for key, cands in NEXT_KEYS.items():
        low = {f.lower() for f in fields}
        if any(c.lower() in low for c in cands):
            s += 1
    return s


def is_playerish(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in PLAYER_WORDS) and "manager" not in low and "list" not in low


def is_containerish(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in CONTAINER_WORDS)


def resolve_class(classes: dict, wanted: str) -> str | None:
    if wanted in classes:
        return wanted
    for k in classes:
        if k.split(".")[-1] == wanted:
            return k
    for k in classes:
        if k.lower().endswith("." + wanted.lower()) or k.lower() == wanted.lower():
            return k
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="IPA or global-metadata.dat")
    ap.add_argument("--class", dest="cls", default=None)
    ap.add_argument("--fields", action="store_true", help="dump the class field list")
    ap.add_argument("--find-field", default=None, help="classes having this field name")
    ap.add_argument("--propose", action="store_true", help="emit a filled ff_native.json draft")
    ap.add_argument("--out", default="out/ff_native.proposed.json")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()

    src = pathlib.Path(a.src)
    print("=" * 74)
    print(f" metadata_structure  {src.name}")
    print("=" * 74)
    try:
        data = read_metadata(src)
        info = build_map(data)
    except StructError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1

    classes = info["classes"]
    print(f" metadata version : {info['version']}")
    print(f" fields           : {info['field_count']} "
          f"(record {info['field_rec_size']}B, token match {info['field_token_ratio']:.1%})")
    print(f" typeDefinitions  : {info['typedef_count']} "
          f"(record {info['typedef_rec_size']}B, token match {info['typedef_token_ratio']:.1%})")
    print(f" solved layout    : fieldStart@+{info['field_start_off']}  "
          f"field_count@+{info['field_count_off']}  "
          f"(coverage {info['layout_coverage']:.1%}, contiguous {info['layout_contiguity']:.1%})")
    print(f" classes with fields: {len(classes)}")
    recs = info.get("type_records", 0)
    mapped = len(classes)
    parts = [
        f"{info.get('classes_skipped_above_cap', 0)} above cap",
        f"{info.get('classes_without_fields', 0)} without fields",
        f"{info.get('classes_unnamed', 0)} unnamed",
        f"{info.get('classes_bad_offsets', 0)} bad offsets",
        f"{info.get('name_collisions', 0)} name collisions",
    ]
    print(f" of {recs} records   : " + ", ".join(parts))
    check = (mapped + info.get("classes_skipped_above_cap", 0)
             + info.get("classes_without_fields", 0)
             + info.get("classes_unnamed", 0)
             + info.get("classes_bad_offsets", 0)
             + info.get("name_collisions", 0))
    print(f" accounting       : {check} of {recs}  "
          f"({'adds up' if check == recs else 'DOES NOT ADD UP'})")
    if info.get("classes_skipped_above_cap", 0):
        print("                    the class map is PARTIAL: those have no entry at all,")
        print("                    and the player class is among them")
        for nm, nf in info.get("classes_skipped_above_cap_largest", [])[:5]:
            print(f"                      {nm}  ({nf} fields)")
    for s in info.get("sanity", []):
        print(f" layout check     : {s}")
    print()
    print("--- name obfuscation (obfuscated member names are re-randomised every patch) ---")
    for label, st in info.get("obfuscation", {}).items():
        print(f"  {label:<26} {st['obfuscated']:>7}/{st['fields']:<7} fields  {st['pct']:>5.1f}% obfuscated")
    print()

    # full class map, so every query below (and anything you write later) is offline
    cmap = pathlib.Path(a.out).with_name("classes.json")
    cmap.parent.mkdir(parents=True, exist_ok=True)
    cmap.write_text(json.dumps(classes, ensure_ascii=False), encoding="utf-8")
    print(f" full class map   : {cmap}  ({cmap.stat().st_size / 1e6:.1f} MB, {len(classes)} classes)")
    # A companion file, because the map alone cannot express its own gaps.
    stats = cmap.with_name("classes.stats.json")
    stats.write_text(json.dumps({
        "type_records": info.get("type_records"),
        "classes_mapped": info.get("classes_mapped"),
        "class_field_cap": info.get("class_field_cap"),
        "classes_skipped_above_cap": info.get("classes_skipped_above_cap"),
        "classes_skipped_above_cap_largest": info.get("classes_skipped_above_cap_largest"),
        "classes_without_fields": info.get("classes_without_fields"),
        "classes_bad_offsets": info.get("classes_bad_offsets"),
        "classes_unnamed": info.get("classes_unnamed"),
        "name_collisions": info.get("name_collisions"),
        "typedef_count": info.get("typedef_count"),
        "field_count": info.get("field_count"),
        "note": ("classes above the cap are ABSENT from classes.json, not empty. "
                 "Absence is not evidence that a class has no such field."),
    }, indent=2), encoding="utf-8")
    print(f" class map stats  : {stats}")
    print()

    if a.find_field:
        want = a.find_field.lower()
        hits = [(k, v) for k, v in classes.items()
                if any(f.lower() == want for f in v)]
        hits.sort(key=lambda kv: len(kv[1]))
        print(f"--- classes containing a field literally named '{a.find_field}' "
              f"({len(hits)} total) ---")
        for k, v in hits[:a.top]:
            print(f"  {k}")
            print(f"      {', '.join(v[:18])}")
        print()

    if a.cls:
        key = resolve_class(classes, a.cls)
        if not key:
            print(f"[!] class '{a.cls}' not found", file=sys.stderr)
            return 1
        print(f"--- {key} ---")
        for i, f in enumerate(classes[key]):
            print(f"  {i:>3}  {f}")
        print()

    # rank the player-like classes by how many player fields they carry
    ranked = []
    for k, v in classes.items():
        short = k.split(".")[-1]
        if not is_playerish(short):
            continue
        sc = score_player(v)
        if sc >= 2:
            ranked.append((sc, len(v), k, v))
    ranked.sort(key=lambda r: (-r[0], r[1]))

    print(f"--- player-class candidates (>=2 of hp/team/name/local) ---")
    for sc, n, k, v in ranked[:a.top]:
        print(f"  [{sc}/4] {k}   ({n} fields)")
        print(f"         {', '.join(v[:20])}")
    print()

    # containers: classes that hold a field whose name screams "list of players"
    containers = []
    for k, v in classes.items():
        short = k.split(".")[-1]
        if not is_containerish(short):
            continue
        for f in v:
            lf = f.lower()
            if any(w in lf for w in ("player", "actor", "unit", "entity", "avatar")):
                containers.append((k, f, v))
                break
    containers.sort(key=lambda c: len(c[2]))
    print(f"--- container candidates (class + a player-ish list field) ---")
    for k, f, v in containers[:a.top]:
        print(f"  {k}   ->  .{f}")
        print(f"         fields: {', '.join(v[:16])}")
    print()

    if a.propose and ranked and containers:
        best_p = ranked[0]
        best_c = containers[0]
        draft = {
            "_source": src.name,
            "_metadata_version": info["version"],
            "_note": "AUTO-DRAFTED from metadata structure. Names are real; the "
                     "exact static/instance split and the list type still need one "
                     "Frida confirmation (tools/il2cpp_hunt.js).",
            "proxy": "http://192.168.1.23:8082",
            "image": "UnityFramework",
            "player": {
                "_container_hint": best_c[0],
                "container_class": best_c[0].split(".")[-1],
                "container_namespace": best_c[0].split(".")[0] if "." in best_c[0] else "",
                "container_static": "??",
                "list_field": best_c[1],
                "list_kind": "list",
                "_element_hint": best_p[2],
                "is_local_field": "??",
                "name_field": "??",
                "team_field": "??",
                "hp_field": "??",
                "max_hp_field": "??",
                "transform_method": "get_transform",
                "position_method": "get_position",
                "head_offset": 1.55,
                "chest_offset": 1.20,
            },
            "camera": {
                "class": "Camera",
                "namespace": "UnityEngine",
                "static": "main",
                "w2s_method": "WorldToScreenPoint",
                "boxed_value_offset": 16,
            },
            "local": {"class": best_p[2].split(".")[-1], "namespace": "", "static_method": "??"},
            "aim": {
                "apply": "camera_transform",
                "transform_method": "get_transform",
                "rotation_field": "m_LocalRotation",
                "apply_method": "SetRotation",
            },
        }
        # fill the four player fields from the candidate's own field list
        chosen = best_p[3]
        lowmap = {f.lower(): f for f in chosen}
        for slot, cands in NEXT_KEYS.items():
            for c in cands:
                if c.lower() in lowmap:
                    draft["player"][slot.split(".")[1]] = lowmap[c.lower()]
                    break
        p = pathlib.Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(draft, indent=2), encoding="utf-8")
        print(f"--- draft written: {p} ---")
        print(json.dumps(draft["player"], indent=2))
        print()

    print("field names are real for this build. static-vs-instance and the exact")
    print("list type still need: frida -l tools/il2cpp_hunt.js  ->  dumpStatics(\"<class>\")")
    return 0


if __name__ == "__main__":
    sys.exit(main())