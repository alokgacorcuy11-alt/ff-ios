# native/ — the layer that actually makes ESP and aimbot exist

## TL;DR — where this build stands

The dylib **resolves itself at runtime**. It does not need a class/offset map, a
Frida session, or a device dump, because `UnityFramework` exports all 256
`il2cpp_*` symbols and `dlsym` finds every one of them:

```bash
python tools/il2cpp_symbols.py <ipa> --require
# il2cpp_* symbols in the symbol table : 256
# defined + external (dlsym-visible)   : 256
# required by IL2CPP.m : 22/22 present
# optional             : 32/32 present
```

`Core/Resolve.m` then walks the live object model and works out the player
class, its health/nickname/team/position fields, the collection that holds the
players and the singleton that holds the collection. Field **types** are never
obfuscated, so a patch that re-randomises every member name changes nothing.
`ff_native.json` still exists as an optional override — it is no longer required.

What that means for you:

| | |
|---|---|
| build | GitHub Actions (macOS) or `make RELEASE=1` on a Mac |
| install | TrollStore, Sideloadly, AltStore, ESign/GBox — iOS 12+ |
| configure | the in-game menu, or the proxy panel on your laptop |
| after a game patch | nothing. Reinstall only if the game itself updates |
| stealth | `native/ANTIDETECT.md` — layer by layer, with the residual risk spelled out |

Verification offline, no device needed:

```bash
python tools/tests/run_all.py        # every suite, no device
python tools/ipa_probe.py      <ipa> # identity, cryptid, load commands
python tools/il2cpp_symbols.py <ipa> --require
python tools/got_scan.py       <ipa> # the dyld slots tier 2 rewrites
```

Build it with `make RELEASE=1`. That flag compiles every log string out of the
binary and is what the CI verifies; the default build keeps the messages (gated
off at runtime) for debugging.

The ObjC classes are named `USPanel`, `USModel`, `USOverlay`, `USConfig`,
`USInput`, `USMap`, `USLink`, `USCloak` — deliberately generic, because the ObjC
classlist lives in the binary and no linker flag removes it. File names are
unchanged and do not reach the binary.

## First, the part that matters

A proxy cannot make ESP work. Not "shouldn't" — **cannot**, and the reason is
concrete:

iOS only maps executable code into a process if that code is inside a
code-signed bundle. `dyld` reads the load commands *inside* the Mach-O at
launch. An HTTP server can change bytes that the app *reads*; it can never add
a Mach-O load command, because the app is already running when the bytes arrive,
and nothing it receives over a socket becomes executable.

So there is exactly one class of mechanism that produces an in-game menu with
ESP and aimbot, and it is always this:

| Mechanism | What it is | Where the menu comes from |
|---|---|---|
| **Re-signed IPA + injected dylib** | official Garena bundle + one extra `LC_LOAD_DYLIB` + a signed dylib | in-process, ours |
| Jailbreak + injection into the running process | dylib pushed into the live process | in-process, ours |
| TrollStore loader + `task_for_pid` | permanent app that injects into Free Fire | in-process, ours |

All three are **in-process code**. The "official Free Fire" you see in those
videos *is* the official IPA — with a dylib stapled on and re-signed with the
seller's certificate. The bundle id, the icon and the login are still Garena's.
That is the entire trick, and it is why they can do it and a bare proxy cannot.

So what was the "proxy" in those products? Exactly what `ff-proxy/` already is:

- license / auth / device bind / expiry
- remote config and feature flags
- the class + offset map (so a game patch doesn't need a new IPA)

Look at this repo's `state/`, `logs/`, `/api/set`, `/api/state`. That is that
product's server. You already have it. What was missing is the dylib.

## This build: one panel, two layers

```
   ┌─────────────────────────── laptop ───────────────────────────┐
   │  ff-proxy                                                    │
   │    /            panel (WebView menu, renders inside FF)      │
   │    /api/state   live feature values  ────────────┐           │
   │    /api/native  state/ff_native.json (class map)─┼───┐       │
   │    rules/*.yaml, logs/flows.jsonl                 │   │       │
   └───────────────────────────────────────────────────┼───┼──────┘
                                                       │   │
                             polls every 2s / 30s      │   │
                                                       ▼   ▼
   ┌──────────────── Free Fire (re-signed IPA) ───────────────────┐
   │  Tweak.x  ─ constructor ─ FFBoot                             │
   │    ├── IL2CPP.m   resolve il2cpp_* exports by dlsym          │
   │    ├── FFState.m  feature flags + schema, polled from proxy   │
   │    ├── Game.m     read players / camera via il2cpp API        │
   │    ├── ESP.m      overlay UIWindow + CADisplayLink, aimbot    │
   │    └── FFMenu.m   native floating panel (same look as menu.ht│
   └──────────────────────────────────────────────────────────────┘
```

Result: the WebView menu you already built **and** the native menu drive the
**same** switches. Flip ESP in the in-game browser page, the overlay starts.
Flip it on the laptop, same thing. You are not choosing between the two.

No `%hook`, no libsubstrate, no jailbreak runtime. Every read and write goes
through the exported IL2CPP API (`il2cpp_field_get_value`,
`il2cpp_runtime_invoke`, …), which is why this works on a re-signed official
build.

## Prerequisites

| Need | Why |
|---|---|
| macOS + [Theos](https://theos.dev) | compiling the dylib |
| a **decrypted** Free Fire IPA | you cannot patch an encrypted binary |
| `insert_dylib`, `ldid`, `zsign`, `zip` | inject + sign |
| TrollStore (iOS 14–16.6.1) or AltStore/SideStore or a jailbroken device | install |
| your own signing cert + provisioning profile | the re-sign |

Get a decrypted IPA from a jailbroken device:

```bash
frida-ios-dump -H 192.168.1.50 com.dts.freefireth
# or
bagbak com.dts.freefireth
```

Then confirm it is actually decrypted — the build script checks this and bails
if not:

```bash
otool -l Payload/FreeFire.app/FreeFire | grep -A4 LC_ENCRYPTION_INFO
# cryptid 0  <- must be 0
```

## Build

```bash
cd native
make clean && make
# -> .theos/obj/arm64/FFMenu.dylib
```

Rootless devices: `make ROOTLESS=1`.

You do **not** need a `.deb`. `make` produces a plain dylib, which is what
`insert_dylib` wants.

## Hunt the values (this is the step nobody can skip)

`ff_native.example.json` is intentionally full of `"??"`. Those are the real
class and field names of *your current build*. They change between patches.
Guessing them is how you get a menu that loads and does nothing.

```bash
frida -U -f com.dts.freefireth -l ../tools/il2cpp_hunt.js --no-pause
```

Then, in the Frida console:

```
findClasses("player")       # every class with player/actor/avatar/entity in the name
dumpStatics("Room")         # static fields + their real type names
dumpFields("Player")        # every field: name, offset, static/instance, type
dumpMethods("Player")       # every method + param count
findW2S()                   # camera methods taking exactly 1 arg -> WorldToScreenPoint
findPlayerContainers()      # static fields whose type is List<T>/[]/Dictionary
```

What you are looking for, mapped to the JSON keys:

| JSON key | How to find it |
|---|---|
| `player.container_class` | the class whose static field holds the actor list (`findPlayerContainers`) |
| `player.container_static` | that field's name |
| `player.list_field` | field on the container instance holding the list |
| `player.list_kind` | `list` for `List<T>` (has `get_Count`/`get_Item`), `array` for `T[]` |
| `player.hp_field` / `max_hp_field` / `team_field` / `name_field` | `dumpFields` on the element class |
| `player.transform_method` | almost always `get_transform` |
| `player.position_method` | almost always `get_position` |
| `camera.class` / `camera.static` | the class owning `WorldToScreenPoint`, and its singleton field |
| `camera.w2s_method` | from `findW2S()` |
| `local.class` / `local.static_method` | a class with a 0-arg static method returning the local player, if one exists |
| `aim.rotation_field` | `dumpFields` on the transform class → the quaternion field |

Copy the file and fill it in:

```bash
cp native/ff_native.example.json state/ff_native.json   # OPTIONAL - only to pin a build
# ... edit every CHANGE_ME ...
```

**It is all-or-nothing.** The map is an override, so it has to describe this
build completely: `container_class`, `container_static`, `list_field` and
`position_field` must be real names, not `CHANGE_ME`. The dylib validates them
(`FFSchemaUsable`) and, if the map is still a template or fails to bind, it
deletes the file and falls back to the live resolver instead of binding to
nothing. The panel header then reads `schema none`, not `schema file`.

The proxy serves it immediately at `GET /api/native`, and the dylib picks it up
within 30 s **without relaunching the game**. Verify:

```bash
curl http://127.0.0.1:8082/api/native | head -20
```

## Install

```bash
./tools/build_ipa.sh decrypted.ipa native/.theos/obj/arm64/FFMenu.dylib \
                     cert.p12 profile.mobileprovision
# -> out/com.dts.freefireth-ffmenu-YYYYmmdd-HHMMSS.ipa
```

What it does: unpack → verify `cryptid 0` → drop the dylib in `Frameworks/` →
`insert_dylib --inplace --overwrite --strip-codesig --codesign` → `ldid -S` every
nested binary → sign with entitlements generated from your own profile
(`tools/make_sideload_entitlements.py`; `get-task-allow` is *not* needed - dyld
maps an `LC_LOAD_DYLIB` either way) → re-sign the bundle with `zsign` → repack.

Then open the `.ipa` in TrollStore (permanent, no revocation) or AltStore
(7-day loop). On a jailbroken device, unzip into the app container and `uicache`.

## Verify it works — in this order

1. **Dylib mapped?** Console.app, filter `ffmenu`. You want
   `[ffmenu] dylib loaded (pid N)`.
2. **il2cpp resolved?** `[ffmenu] il2cpp resolved (array_length: yes|no)`.
   If you get `missing il2cpp symbol: ...` then `image` in the JSON is wrong —
   some builds keep il2cpp in the main binary, not `UnityFramework`.
3. **Bind OK?** `[ffmenu] bind OK` — not `PARTIAL`. Partial means a name in the
   JSON is wrong, and the log line tells you which.
4. **Menu present?** a lime `FF` orb bottom-left. Drag it, tap it.
5. **DIAGNOSTICS** button in the panel prints live state:
   `container:ok cam:ok w2s:ok local:ok elem:Player` plus every bind error.
   This is your ground truth — don't guess, read it.
6. **ESP**: get into a match, enable ESP. Boxes should appear. If nothing
   appears, `worldToScreen` is returning `NO` (target behind camera) or the
   position method is wrong.

## Version-dependent constants, honestly labelled

These four are IL2CPP-runtime layout values, not game values. They are correct
for standard 64-bit IL2CPP builds and are exposed in the JSON so you can fix
them without a rebuild:

| Key | Default | Meaning |
|---|---|---|
| `camera.boxed_value_offset` | 16 | where a boxed value type's payload starts (`klass*` + `monitor*`) |
| `player.string_len_offset` | 16 | `Il2CppString.length` |
| `player.string_data_offset` | 20 | `Il2CppString.chars` (first UTF-16 unit) |
| `player.array_len_offset` | 24 | `Il2CppArray.max_length` (only used when `il2cpp_array_length` is absent) |
| `player.array_data_offset` | 32 | `Il2CppArray` vector base |

If names come out as empty strings and HP comes out as garbage, one of these is
off. Verify with the hunt script: it prints the same offsets from the runtime,
so you compare instead of guessing.

## Anti-detection, straight

What the dylib does that is visible to Free Fire:

- **A new image is mapped.** `_dyld_image_count` grows, and
  `_dyld_get_image_name` returns `libFFMenu.dylib`. A `get-task-allow`
  entitlement is present on a build that normally would not have it. This is the
  single loudest tell and there is no clean way to hide it from in-process code —
  the only real answer is not being in-process.
- **The overlay is a second UIWindow** at a raised level. An app can enumerate
  its own windows; a separate process cannot.
- **Reads through the IL2CPP API are normal.** `il2cpp_field_get_value` from
  outside the VM does not trip anything by itself.
- **Server-side is where you actually get caught.** Rotation written every 50 ms
  with zero human jitter, 100% headshot rate, cross-map target acquisition.
  That is what a human reviewer sees. `aim.smooth` and `aim.fov` are there for
  exactly this reason — keep them high and narrow.
- `ui.hide_from_capture` masks the overlay's layer with a secure `UITextField`
  layer, which keeps it out of screen recording and most screenshot paths. It
  does not hide anything from the game itself.

Practical read: this layer is for your own device and your own account. Nothing
here is undetectable in principle, and the client-side tells are structural.

## Files

```
native/
├─ Makefile                  Theos, produces FFMenu.dylib (no substrate)
├─ Tweak.x                   constructor -> wait for il2cpp -> bind -> install
├─ entitlements.plist        empty by design - signer derives from the profile
├─ entitlements.trollstore.plist   platform-application + no-container
├─ ff_native.example.json    the class/field map template (copy to state/)
├─ Core/
│  ├─ IL2CPP.h/.m            il2cpp export resolution by dlsym + class finder
│  ├─ FFState.h/.m           feature flags, schema fetch, proxy polling
│  ├─ Game.h/.m              reader over the resolved map: players, camera, aim
│  └─ ESP.h/.m               overlay window, CADisplayLink, aimbot loop
└─ Menu/
   └─ FFMenu.h/.m            native floating panel
```

## Honest status

The Python side (proxy, panel, `/api/native`, rules, state sync) is **verified by
tests** — see `tools/selftest.py` and the e2e harness.

The ObjC side is **not compiled here** — this machine has no macOS/UIKit
toolchain. It is written against the real IL2CPP API and the real Theos layout,
and every value it needs is either in the JSON or printed by `il2cpp_hunt.js`.
First build on a Mac will surface normal integration work (a wrong import, an
obfuscated name that needs the hunt re-run). Send me the compiler output and the
`DIAGNOSTICS` dump and I'll close it out.

What that means concretely, in the order it has to be settled on a device:

1. **Build.** GitHub Actions is the build path (`build-and-package`). The CI
   asserts arm64-only, the iOS 12 floor, and that the shipped image names
   nothing. First red run = send the log.
2. **Bind.** `DIAGNOSTICS` prints one `resolve:` line per decision (player class,
   container, singleton, position field, w2s mode). A `WARNING` or
   `none of ...` there names the exact remaining gap.
3. **Projection.** `w2s:` must read `icall` or `matrix`; `NOT PROBED` means the
   camera was not found yet (normal between matches).
4. **Touch.** `inject:` prints `space=points` or `space=pixels`. If a tap lands
   in the wrong place on your device, that line is the first thing to read.
5. **Aim.** `targeting:` reads `NOT CALIBRATED` until you press `CALIBRATE` in a
   match. That is by design: the rotation-per-point gain is measured, not
   guessed, because it depends on your sensitivity, your screen and whether
   look-Y is inverted.
