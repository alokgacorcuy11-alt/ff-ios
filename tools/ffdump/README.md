# ff dump pipeline

Regenerating the offsets a memory ESP needs, from the APK alone, with no rooted
device and no running game. This is the tool that keeps the cheat alive after a
game update: the vendor ships a new binary, this runs again, and the offsets come
out the other side.

Every number below was measured on Free Fire 1.132.1
(`split_config.arm64_v8a.apk`). None of it is copied from a forum.

## what the binary gives us

| fact | number | how it was checked |
|---|---|---|
| `libil2cpp.so` | 184,044,016 B | `split_config.arm64_v8a.apk` |
| `global-metadata.dat` | 56,970,552 B | `split_asset_pack_install_time.apk` |
| metadata version | 31 | header |
| dynamic symbols | 2,717 | `.dynsym` |
| ...that are `il2cpp_*` | **0** | the runtime API is not exported |
| `.rela.dyn` entries | 1,014,714 | section header |
| ...of type `R_AARCH64_RELATIVE` | 1,014,705 | relocation table |
| ...targeting `.data.rel.ro` | **332,825** | relocation table |
| `.data.rel.ro` live words before | 61,257 of 399,773 | 97.6% zeros |
| `.data.rel.ro` live words after sofix | 394,081 | every slot filled |

**The zeros are not missing data.** A shared object whose pointer tables are
filled at load time stores zeros in the file and the values in `.rela.dyn`. For
an `R_AARCH64_RELATIVE` entry at `r_offset` with `r_addend`, the value is the
addend - the link-time virtual address - which is exactly what a dumper wants.
`sofix.py` applies all 332,825 of them.

This corrects an earlier conclusion that the static dump was dead. It was not;
the file just needed its relocations applied first.

## what is still intact in the metadata

The names were never obfuscated. Counted in `global-metadata.dat`:

```
GameFacade 2   GameObjectManager 4   GameManager 1   MainCamera 8
Camera 451     Transform 495         Vector3 65      Matrix4x4 2
Player 656     Enemy 64              PlayerInfo 98   LocalPlayer 34
PlayerAttributes 3   uY 34  uW 25
get_position 2   get_transform 3   get_rotation 1   WorldToScreenPoint 2
get_HP 1   get_Health 1   get_CurHP 1   get_TeamID 1   get_TeamId 1
get_IsDead 1   get_IsDieing 1
```

That is the whole vocabulary a real ESP is written in: the object manager, the
local player, the player list, position and transform accessors, health, team,
death state, and the camera's world-to-screen projection.

## where it stops today

```
Il2CppDumper 6.7.46
  Metadata Version: 31
  Applying relocations...
  WARNING: find .init_proc
  ERROR: This file may be protected.
  Searching...
  CodeRegistration    : a41c8b8
  MetadataRegistration: a5c7f60
  System.OverflowException  (Il2Cpp.Init, a negative array length)
```

The `MetadataRegistration` the search lands on is **correct and reads perfectly**:

```
+00 count 27718     +08 ptr 0xa47e938   genericClasses
+10 count 21286     +18 ptr 0xa507dc8   genericInsts
+20 count 77228     +28 ptr 0x1f13128   genericMethodTable
+30 count 103553    +38 ptr 0xa63cb40   types
+40 count 150683    +48 ptr 0x1d599e4   methodSpecs
+50 count 43627     +58 ptr 0xa8bf168   fieldOffsets
```

The `CodeRegistration` it lands on is **not** - read as pairs, the field at
`+0x20` is a pointer where a count belongs, so the struct is misaligned and a
count comes out negative.

`sofix.py` is not the cause: the dumper fails identically on the untouched file.
The cause is the missing `.init_proc` section, which Garena strips. Il2CppDumper
uses that section to find the registration directly and falls back to a byte
pattern when it is gone; the pattern matches eight bytes early.

## next step

Find `Il2CppCodeRegistration` by shape instead of by pattern, then dump.

The anchor is that `codeGenModulesCount` equals the metadata's image count, and
`interopDataCount` equals the metadata's `interopDataCount` - two independent
values a wrong candidate will not match. A scan over `.data.rel.ro` for as many
consecutive `(uint32 count, uint32 zero, uint64 pointer)` triples as fit, ranked
by how many of the two anchors hold, is the bounded version of this.

A naive scan finds 57,718 candidates because small integers and pointers are
everywhere; the anchors are what cut it down.

## usage

```
python tools/ffdump/sofix.py libil2cpp.so libil2cpp-fixed.so
  ...then point a dumper at libil2cpp-fixed.so + global-metadata.dat
```

`--stats-only` counts and reports without writing.
