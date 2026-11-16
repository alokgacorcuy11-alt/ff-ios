#!/usr/bin/env python3
"""metadata_hunt.py - harvest real IL2CPP names straight out of the IPA.

No device, no jailbreak, no Frida. `global-metadata.dat` inside the bundle holds
the complete string table for the game's managed code: every class name, field
name, method name and namespace. That is exactly what ff_native.json needs.

IMPORTANT: this works on an **encrypted App Store IPA too**. FairPlay only
encrypts the Mach-O `__TEXT` of executable binaries; `global-metadata.dat` is a
plain data file inside the zip. So the name harvest does not need a decrypted
payload and does not need a jailbroken device. (Patching does - that is a
separate step.)

This does NOT guess structure - it cannot tell a method from a field from the
string table alone. What it does is tell you which names EXIST in this build, so
the Frida pass (tools/il2cpp_hunt.js) becomes a confirmation instead of a hunt.

  python tools/metadata_hunt.py FreeFire.ipa
  python tools/metadata_hunt.py FreeFire.ipa --grep "player|actor|camera"
  python tools/metadata_hunt.py FreeFire.ipa --out-json state/ff_native.suggest.json

Outputs:
  out/metadata_strings.txt   every string in the table, sorted, one per line
  out/metadata_suggest.json  ranked candidates per ff_native.json key
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import zipfile
from collections import Counter

SANITY = 0xFAB11BAF

# header field positions, stable from metadata version 24 through 31
OFF_SANITY = 0
OFF_VERSION = 4
OFF_STR_OFF = 24
OFF_STR_SIZE = 28

# what to look for, per key in ff_native.json
KEYWORDS = {
    "player.container_class": [
        r"^Room", r"^Game(Manager|Mode|World|Scene)", r"^(Match|Battle)\w*",
        r"^World\w*", r"^Level\w*", r"^Scene\w*", r"^Arena\w*",
    ],
    "player.element_class": [
        r"^(Player|Actor|Avatar|Hero|Character|Entity|Unit|Fighter|Bot|Tank)\w*$",
    ],
    "player.list_field": [
        r"^(players|playerList|actors|actorList|entities|units|fighters|"
        r"allPlayers|enemyList|alivePlayers|_players|m_Players)$",
    ],
    "player.hp_field": [
        r"^(hp|HP|health|Health|curHp|currentHp|m_Hp|m_Health|life|Life|"
        r"hitPoint|hitPoints)$",
    ],
    "player.max_hp_field": [
        r"^(maxHp|maxHP|maxHealth|MaxHp|max_hp|m_MaxHp|lifeMax|fullHp)$",
    ],
    "player.team_field": [
        r"^(team|teamId|TeamId|teamID|teamIndex|group|squad|faction|"
        r"side|camp|m_Team)$",
    ],
    "player.name_field": [
        r"^(name|nickName|nickname|playerName|userName|displayName|"
        r"m_Name|callSign)$",
    ],
    "player.is_local_field": [
        r"^(isLocal|isLocalPlayer|isMe|isSelf|local|isMainPlayer|isMyPlayer)$",
    ],
    "camera.class": [
        r"^(MainCamera|CameraController|CameraManager|GameCamera|FollowCamera|"
        r"CameraRig|Camera)\w*$",
    ],
    "camera.static": [r"^(main|instance|Instance|Current|current|_main|s_Instance)$"],
    "camera.w2s_method": [
        r"^(WorldToScreen(Point)?|ScreenToWorld(Point)?|WorldToViewportPoint|"
        r"WorldToGUIPoint)$",
    ],
    "local.static_method": [
        r"^(GetLocalPlayer|get_LocalPlayer|get_Local|GetMainPlayer|GetMyPlayer|"
        r"get_Instance|GetInstance)$",
    ],
    "aim.transform_method": [r"^(get_transform|GetTransform|get_CameraTransform)$"],
    "aim.rotation_field": [
        r"^(m_LocalRotation|m_Rotation|rotation|localRotation|aimRotation)$",
    ],
    "player.transform_method": [r"^(get_transform|GetTransform)$"],
    "player.position_method": [
        r"^(get_position|GetPosition|get_worldPosition|GetWorldPosition|"
        r"get_localPosition|get_Position)$",
    ],
}

HEADER_KEYS = [
    "stringLiteral", "stringLiteralData", "string", "events", "properties",
    "methods", "parameterDefaultValues", "fieldDefaultValues",
    "fieldAndParameterDefaultValueData", "fieldMarshaledSizes", "parameters",
    "fields", "genericParameters", "genericParameterConstraints",
    "genericContainers", "nestedTypes", "interfaces", "vtableMethods",
    "interfaceOffsets", "typeDefinitionsSizes", "rgctxEntries", "images",
    "assemblies", "metadataUsageLists", "metadataUsagePairs",
    "fieldRefs", "referencedAssemblies", "attributeData", "attributeDataRange",
    "unresolvedVirtualCall",
]


class MetaError(RuntimeError):
    pass


def find_metadata(ipa: pathlib.Path) -> tuple[bytes, str]:
    with zipfile.ZipFile(ipa) as z:
        cands = [n for n in z.namelist()
                 if n.endswith("global-metadata.dat")]
        if not cands:
            raise MetaError(
                "global-metadata.dat not found in the IPA.\n"
                "  expected at Payload/<App>.app/Data/Managed/Metadata/\n"
                "  (if the game is not IL2CPP, or the file is absent, this tool "
                "cannot help - use tools/il2cpp_hunt.js on device)")
        cands.sort(key=len)
        name = cands[0]
        return z.read(name), name


def parse_string_table(data: bytes) -> tuple[int, list[str], dict]:
    if len(data) < 32:
        raise MetaError("metadata file is too small to be real")
    import struct
    sanity, version = struct.unpack_from("<Ii", data, 0)
    if sanity != SANITY:
        raise MetaError(f"bad sanity 0x{sanity:08x} - not global-metadata.dat")
    str_off, str_size = struct.unpack_from("<ii", data, OFF_STR_OFF)
    if not (0 < str_off < len(data)) or str_size <= 0 or str_off + str_size > len(data):
        raise MetaError(
            f"string table out of range (off={str_off} size={str_size} file={len(data)}). "
            f"Metadata version {version} may use a different header layout - "
            f"send this file and the version number and the offsets get pinned.")

    blob = data[str_off:str_off + str_size]
    raw = blob.split(b"\x00")
    strings = []
    seen = set()
    for b in raw:
        if not b:
            continue
        try:
            s = b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if s in seen:
            continue
        seen.add(s)
        strings.append(s)

    # peek at the section table so the report can show what is where
    sections = {}
    for i in range(len(HEADER_KEYS)):
        o = 8 + i * 8
        if o + 8 > len(data):
            break
        import struct as _s
        off, size = _s.unpack_from("<ii", data, o)
        if off > 0 and size > 0 and off + size <= len(data):
            sections[HEADER_KEYS[i]] = {"offset": off, "size": size}
    return version, strings, sections


def machine_grep(pattern: str) -> "re.Pattern[str]":
    return re.compile(pattern)


def rank(strings: list[str], patterns: list[str], limit: int = 12) -> list[str]:
    rxs = [re.compile(p) for p in patterns]
    hits = [s for s in strings if any(r.search(s) for r in rxs)]
    # prefer shorter, cleaner identifiers - they are far more likely to be the
    # type or member name and not a display string
    hits.sort(key=lambda s: (len(s), s))
    return hits[:limit]


def looks_like_identifier(s: str) -> bool:
    if not s or len(s) > 96:
        return False
    if " " in s or s.startswith(("http", "/", "@", "assets/")):
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_<>,\.\[\]]*", s))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipa")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--grep", default=None, help="extra regex to search for")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--limit", type=int, default=12)
    a = ap.parse_args()

    ipa = pathlib.Path(a.ipa)
    if not ipa.exists():
        print(f"[!] not found: {ipa}", file=sys.stderr)
        return 1

    print("=" * 70)
    print(f" metadata_hunt  {ipa.name}")
    print("=" * 70)
    try:
        data, internal = find_metadata(ipa)
        version, strings, sections = parse_string_table(data)
    except MetaError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1

    idents = [s for s in strings if looks_like_identifier(s)]
    print(f" file        : {internal}")
    print(f" size        : {len(data) / 1e6:.1f} MB")
    print(f" version     : {version}")
    print(f" strings     : {len(strings)}  (identifiers: {len(idents)})")
    if sections:
        print(f" sections    : {', '.join(sorted(sections))}")
    print()

    # full dump, so nothing is lost
    outdir = pathlib.Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    dump = outdir / "metadata_strings.txt"
    dump.write_text("\n".join(sorted(strings)), encoding="utf-8")
    print(f" all strings -> {dump}")

    suggest: dict = {"_meta": {"ipa": ipa.name, "version": version,
                              "strings": len(strings),
                              "note": "candidates from the metadata string table; "
                                      "confirm structure with tools/il2cpp_hunt.js"}}
    print()
    print(f"{'key':<32} {'candidate':<40}")
    print("-" * 74)
    for key, pats in KEYWORDS.items():
        hits = rank(idents, pats, a.limit)
        suggest[key] = hits or ["??"]
        first = hits[0] if hits else "??"
        print(f"{key:<32} {first:<40}")
        if len(hits) > 1:
            for h in hits[1:4]:
                print(f"{'':<32} {h:<40}")

    if a.grep:
        print()
        print(f"--- grep /{a.grep}/ ---")
        rx = re.compile(a.grep)
        for s in sorted({s for s in strings if rx.search(s)})[:60]:
            print("  " + s[:110])

    out_json = pathlib.Path(a.out_json) if a.out_json else (outdir / "metadata_suggest.json")
    out_json.write_text(json.dumps(suggest, indent=2), encoding="utf-8")
    print()
    print(f" suggestions -> {out_json}")

    # quick sanity signal: do the obvious names even exist?
    must = ["get_transform", "get_position"]
    missing = [m for m in must if m not in strings]
    if missing:
        print(f"[!] missing base Unity names: {missing}")
        print("    -> either the build is heavily obfuscated or the string table")
        print("       offset is wrong for this metadata version.")
    else:
        print("[ok] base Unity/IL2CPP names present - the table is readable")

    print()
    print("note: this worked because global-metadata.dat is a plain data file.")
    print("      It reads fine from an ENCRYPTED App Store IPA as well - only")
    print("      Mach-O binaries are FairPlay-encrypted, not bundled data.")
    print()
    print("next:")
    print("  1. copy native/ff_native.example.json -> state/ff_native.json")
    print("  2. fill the ?? from the candidates above")
    print("  3. confirm each one on device with: frida -U -f com.dts.freefireth \\")
    print("         -l tools/il2cpp_hunt.js   then   dumpFields(\"<class>\")")
    return 0


if __name__ == "__main__":
    sys.exit(main())