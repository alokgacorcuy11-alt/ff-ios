"""Verify tools/metadata_hunt.py against a synthetic global-metadata.dat."""
import io
import json
import os
import pathlib
import plistlib
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TMP = pathlib.Path(os.environ.get("DEMIOS_TEST_TMP")
                   or (pathlib.Path(tempfile.gettempdir()) / "demios-tests")) / "metadata"
TMP.mkdir(parents=True, exist_ok=True)
FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


SANITY = 0xFAB11BAF

STRINGS = [
    # base Unity plumbing
    "get_transform", "get_position", "get_rotation", "m_LocalRotation",
    "WorldToScreenPoint", "get_transform", "set_position",
    # plausible game classes
    "Player", "PlayerController", "PlayerManager", "Actor", "Avatar",
    "GameManager", "RoomManager", "Room", "BattleController",
    "MainCamera", "CameraController", "GameCamera",
    # fields
    "players", "playerList", "hp", "maxHp", "teamId", "nickname",
    "isLocal", "isLocalPlayer", "team", "health",
    # noise that must not be suggested
    "https://cdn.garena.com/a.png", "assets/bundles/ui", "Loading...",
    "A" * 200,
]


def build_metadata(version=27, strings=STRINGS) -> bytes:
    """Real header layout: sanity, version, then (offset,size) pairs.

    We place the string table immediately after a 264-byte header and set
    stringOffset/stringCount at byte 24/28, which is where every metadata
    version from 24 to 31 keeps them.
    """
    table = b"\x00".join(s.encode("utf-8") for s in strings) + b"\x00"
    hdr_size = 264
    str_off = hdr_size
    hdr = bytearray(hdr_size)
    struct.pack_into("<Ii", hdr, 0, SANITY, version)
    struct.pack_into("<ii", hdr, 24, str_off, len(table))
    # a plausible section table so the report has something to show
    struct.pack_into("<ii", hdr, 8, hdr_size + len(table), 16)     # typeDefs-ish
    struct.pack_into("<ii", hdr, 16, hdr_size + len(table) + 16, 8)
    return bytes(hdr) + table + b"\x00" * 32


def make_ipa(name, metadata: bytes, with_metadata=True) -> pathlib.Path:
    p = TMP / name
    app = "Payload/FreeFire.app"
    info = {"CFBundleIdentifier": "com.dts.freefireth",
            "CFBundleExecutable": "FreeFire",
            "CFBundleShortVersionString": "1.108.1"}
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{app}/Info.plist", plistlib.dumps(info))
        z.writestr(f"{app}/FreeFire", b"\xCF\xFA\xED\xFE" + b"\x00" * 128)
        if with_metadata:
            z.writestr(f"{app}/Data/Managed/Metadata/global-metadata.dat", metadata)
    return p


def run(ipa, *extra):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "metadata_hunt.py"),
                           str(ipa), "--out-dir", str(TMP / "out"), *extra],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


print("--- normal metadata ---")
ipa = make_ipa("ff.ipa", build_metadata())
r = run(ipa)
out = r.stdout + r.stderr
check("runs clean", r.returncode == 0, out[-300:] if r.returncode else "")
check("reports version 27", "version     : 27" in out)
check("counts strings", "strings     :" in out)
check("base unity names detected", "base Unity/IL2CPP names present" in out)
check("suggests a Player class", "Player" in out)
check("suggests players list field", "players" in out or "playerList" in out)
check("suggests hp", "hp" in out)
check("suggests WorldToScreenPoint", "WorldToScreenPoint" in out)

strings_file = TMP / "out" / "metadata_strings.txt"
check("dumps all strings", strings_file.exists())
if strings_file.exists():
    lines = set(strings_file.read_text(encoding="utf-8").splitlines())
    check("dump contains get_transform", "get_transform" in lines)
    check("dump contains a URL (nothing filtered out)",
          any("garena.com" in l for l in lines))

sug_file = TMP / "out" / "metadata_suggest.json"
check("writes suggestions json", sug_file.exists())
if sug_file.exists():
    sug = json.loads(sug_file.read_text(encoding="utf-8"))
    check("suggestion has meta", "_meta" in sug and sug["_meta"]["version"] == 27)
    check("container candidates", len(sug.get("player.container_class", [])) >= 1
          and sug["player.container_class"][0] != "??")
    check("noise excluded from candidates",
          all("http" not in c and " " not in c
              for k, v in sug.items() if k != "_meta" for c in v if isinstance(c, str)))

print("\n--- header offsets wrong for the version (must fail loudly) ---")
bad = bytearray(build_metadata())
struct.pack_into("<ii", bad, 24, 999999, 100)     # bogus string table
ipa_bad = make_ipa("bad.ipa", bytes(bad))
r = run(ipa_bad)
out = r.stdout + r.stderr
check("refuses a bad table", r.returncode == 1)
check("explains why", "out of range" in out)
check("asks for the version", "version 27" in out or "different header layout" in out)

print("\n--- not metadata at all ---")
ipa_no = make_ipa("none.ipa", b"", with_metadata=False)
r = run(ipa_no)
out = r.stdout + r.stderr
check("reports missing file", r.returncode == 1 and "not found in the IPA" in out)
check("points at the frida fallback", "il2cpp_hunt.js" in out)

print("\n--- wrong magic ---")
ipa_w = make_ipa("wrong.ipa", b"\x00" * 512)
r = run(ipa_w)
out = r.stdout + r.stderr
check("rejects bad sanity", r.returncode == 1 and "sanity" in out)

print("\n--- grep mode ---")
r = run(ipa, "--grep", "Camera")
check("grep works", "MainCamera" in r.stdout)

print()
if FAIL:
    print(f"{FAIL} check(s) FAILED")
    sys.exit(1)
print("all metadata checks passed")