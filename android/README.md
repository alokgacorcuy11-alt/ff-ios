# Android build — external reader, no injected code

## 0. Where this stands

| stage | what it does | state |
|---|---|---|
| **1. bridge** | root daemon → socket → game pid → `libil2cpp.so` base → verified read | **this commit** |
| 2. reader | offsets from `il2cppdumper` → player list, positions, health | next |
| 3. overlay | `TYPE_APPLICATION_OVERLAY` panel + ESP drawing at 60 Hz | next |
| 4. aim | `uinput` virtual touch device, same control loop as the iOS build | after 3 |

Stage 1 is not a stub: it is the whole hard part of the plumbing (privilege,
process discovery, module base, a working read that proves the address is
right). Everything after it is offsets and drawing.

## 1. Why nothing is injected

The iOS build had to live inside the game, because a sandboxed process cannot be
read from outside. Android has no such rule: a root process can open
`/proc/<pid>/mem` and read it. That changes the entire risk profile.

| | injected (Zygisk / LSPosed / patched APK) | this (external) |
|---|---|---|
| game's `maps` | one more library | untouched |
| game's threads | one more | untouched |
| APK on disk | modified, re-signed | **byte-identical to the store copy** |
| signature check | fails | passes |
| file-hash check | fails | passes |
| IL2CPP hooking needed | yes, per version | no |
| survives a game update | rebuild | re-dump offsets |
| what the game can see | a foreign image in its own address space | nothing inside itself |

The last row is the point. A process that never touches the game cannot be
found by the game looking at itself. What is left is the environment: a rooted
device. See §6.

## 2. Build

### CI (no local toolchain)

Push, or run the `android` workflow from the Actions tab. It produces:

```
dist/ffmenu.apk     debug-signed, installs on any arm64 device
dist/memd           the daemon (also packed into the APK as an asset)
dist/SHA256SUMS
```

Download from the `build` branch — Actions artifacts need a login even on a
public repo, a branch file does not:

```bash
curl -fL -o ffmenu.apk https://raw.githubusercontent.com/<owner>/<repo>/build/ffmenu.apk
curl -fL https://raw.githubusercontent.com/<owner>/<repo>/build/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

### Locally

```bash
# daemon (needs the NDK)
ANDROID_NDK_HOME=~/Android/Sdk/ndk/26.1.10909125 bash android/daemon/build.sh

# app (needs the SDK + gradle 8.7)
gradle -p android/app assembleDebug
# -> android/app/app/build/outputs/apk/debug/app-debug.apk
```

The daemon is a single static arm64 binary, ~30 KB, no dependencies. It is
built for `arm64-v8a` only: Free Fire ships no 32-bit libil2cpp, and neither
does any phone that can run the current build.

## 3. Install and verify

```bash
adb install -r ffmenu.apk
```

1. Open **FFMenu** → it prints `root: ok (uid=0)` or `root: NOT granted`.
   No root, no bridge — the app is honest about it and stops there.
2. Start Free Fire, get past the loading screen (the IL2CPP module has to be
   mapped before it can be found).
3. Back to FFMenu → **CONNECT**.

Expected, in order:

```
root: ok (uid=0)
asset: 30208 bytes
install: -rwx------ 1 root root 30208 ... /data/local/tmp/.ffmemd
start: spawned
socket: connected
game: com.dts.freefireth pid 12345
il2cpp: base 0x7c00d00000
mem: ELF ok, read path works
ready: offsets next (see android/README.md)
```

If it stops earlier, the line that stops it names the layer:

| last line | meaning |
|---|---|
| `root: NOT granted` | Magisk/SuperSU did not answer; grant it for FFMenu |
| `install: FAILED` | `su` ran but `cp` failed — check `adb shell ls -l /data/local/tmp` |
| `socket: ... unreachable` | the daemon died at startup: run it by hand, it prints to stderr |
| `game: not running` | Free Fire is not started, or it is the MAX package (handled) |
| `il2cpp: module not mapped yet` | game still loading; retry after the lobby |
| `mem: unexpected header` | the base is wrong — send the output, do not guess |

Manual run of the daemon, if it dies silently:

```bash
adb shell su -c "/data/local/tmp/.ffmemd $(adb shell stat -c %u /data/data/com.alokg.ffmenu) "
```

## 4. Offsets — the next blocking step

Everything the reader needs comes out of two files that are already inside the
installed game. No decryption, no jailbreak, no dylib.

```bash
PKG=com.dts.freefireth
adb shell pm path $PKG
# package:/data/app/~~.../base.apk
# package:/data/app/~~.../split_config.arm64_v8a.apk

adb pull /data/app/~~.../base.apk
adb pull /data/app/~~.../split_config.arm64_v8a.apk

unzip -o base.apk 'assets/bin/Data/Managed/Metadata/global-metadata.dat'
unzip -o split_config.arm64_v8a.apk 'lib/arm64-v8a/libil2cpp.so'
```

Then either tool:

```bash
# A. Il2CppDumper (gives dump.cs, script.json, method RVAs)
Il2CppDumper libil2cpp.so global-metadata.dat out/

# B. the one already in this repo - no .NET, no Windows-only binary
python tools/metadata_structure.py global-metadata.dat --out out/classes.json
python tools/metadata_structure.py global-metadata.dat --class COW.GamePlay.Player --fields
```

What to look for, in this order:

1. the player container — a class with a `List<Player>` field (static, or on a
   match/game singleton)
2. `Player` — fields for position (`Vector3`), health (`int`), team, and a
   `Transform` or a `position` struct
3. the camera — `Camera.main` equivalent, or the local player's `Transform`

Write them into `android/offsets/ff_android.json` (shape defined in
`android/offsets/example.json`). The reader resolves classes by **name at
runtime** through `il2cpp_class_from_name` — wait, it cannot: that is a
function, and we are outside the process.

That is the one real cost of being external: we read *data*, we do not call
*functions*. So positions must come from fields, not from `Transform.position`.
`il2cppdumper` gives the field offsets; the values are plain memory. Anything
that only exists as a function result (screen projection, for instance) has to
be recomputed from the camera matrix, which is also a field.

That is why §5 recomputes world-to-screen in the app instead of asking the game.

## 5. What the overlay will need

- `Camera.worldToCameraMatrix` / `projectionMatrix` — both are fields on the
  camera object, so both are readable
- view matrix × projection = the MVP the game already uses; the app multiplies
  it and projects. No game code involved.
- drawing: a normal overlay window, `Choreographer` at 60 Hz, `Canvas`
- input: a `uinput` virtual touch device created by the daemon (root), so aim
  moves the real camera exactly like a finger would — the same principle as the
  iOS build's touch injection, and the reason it looks human to the server

## 6. Detection — honest version

Nothing of ours is inside the game. What remains:

| signal | reachable by the game | mitigation |
|---|---|---|
| device is rooted | **yes** — `su`, Magisk paths, `ro.debuggable`, mount table | Magisk DenyList on `com.dts.freefireth`, rename the Magisk app, no other root apps |
| an overlay window is present | partially — `FLAG_SECURE`/touch filtering, not enumeration | keep the panel small, close it during fights |
| a process is reading its memory | **no** — `/proc/<pid>/mem` reads leave no trace in the target | nothing needed |
| APK integrity | **passes** — the APK is untouched | nothing needed |
| behaviour | **yes**, same as iOS | smooth, chest, FOV ≤ 90, ESP only in ranked |

The root row is the one that decides most bans on Android, and it is decided by
how you set up Magisk, not by this app. `DenyList` + renamed Magisk + no
Xposed/LSPosed modules visible to the game is the baseline.

There is no "undetectable". The external design removes the in-process tells
that the iOS build could only hide; it does not remove the rooted device.

## 7. Files

```
android/
├─ daemon/
│  ├─ memd.c          the root service: find pid, module base, read, write, maps
│  └─ build.sh        NDK cross-compile -> app assets
├─ app/
│  ├─ settings.gradle.kts / build.gradle.kts / gradle.properties
│  └─ app/
│     ├─ build.gradle.kts
│     └─ src/main/
│        ├─ AndroidManifest.xml
│        ├─ assets/memd          (built, not committed)
│        └─ java/com/alokg/ffmenu/
│           ├─ MainActivity.java  the stage-1 UI and the connect sequence
│           ├─ Root.java          su wrapper
│           └─ MemClient.java     the wire protocol
└─ offsets/
   └─ example.json     the shape the reader will consume
```

The old iOS tree is not deleted from history: tag `ios-final` on `main` is the
last state of it, including the ban post-mortem in `native/ANTIDETECT.md`.
