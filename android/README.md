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

## 6. Nothing left behind

"Kebal rules, tidak ada jejak" is not one feature, it is a list of places a
trace can exist. This is every one of them, and what is done about it.

| place | what is there | when it disappears |
|---|---|---|
| game process (`maps`, threads, image list) | **nothing** — never entered | always |
| game APK on disk | **nothing** — byte-identical to the store copy | always |
| `/data/local/tmp/.ffmemd` | written, executed, then **unlinked** while running | immediately after start |
| the daemon's `/proc/<pid>/cmdline` | `logd.writer`, uid argument erased in place | immediately after start |
| the daemon's `/proc/<pid>/comm` | `logd.writer` (`prctl`) | immediately after start |
| socket file | none — abstract namespace, no filesystem node | always |
| app private storage | the extracted copy is deleted after `cp`; only the APK asset remains | immediately after install |
| a boot script / Magisk module / init service | **none** — nothing is persisted | always |
| daemon after reboot | gone, and nothing restarts it | first reboot |
| Magisk superuser history | one grant entry for `com.alokg.ffmenu` | revoke it in Magisk (§8) |
| config traffic from the phone | **none** — one-way UDP, the phone never answers | always |

So after `STOP` (or a reboot) the only thing on the device is the app you
installed. Uninstall it and there is nothing to find — the daemon was never a
file, and a process that holds a deleted inode does not survive a reboot.

What makes this possible is the ordering in `MainActivity.run()`:

```
cp memd /data/local/tmp/.ffmemd        # the only moment a path exists
nohup .ffmemd <uid> & sleep 0.4; rm -f .ffmemd
```

The shell keeps the file open for the exec, the daemon starts, then the path is
removed. `/proc/<pid>/exe` shows `(deleted)`, which is also what a rotated log
writer looks like — and the process name was already changed to match one.

Two consequences worth knowing:

- A second `CONNECT` cannot find the old daemon by name, because there is no
  name. It connects to the abstract socket and sends `CMD_SHUTDOWN`; the
  daemon answers, then kills itself. The socket is the handle, not a file.
- `STOP` does the same, and `rm -f` is kept only to clean up a run that died
  between `cp` and `rm`.

## 6b. Blacklist — what it can and cannot key on

A blacklist is not a ban. It is a set of things the game refuses, and every
entry has to be keyed on something the game can observe from inside its own
sandbox. That list is short.

| key | can the game see it? | what this build does |
|---|---|---|
| package id (`com.foo.bar`) | **yes** — `PackageManager.getInstalledPackages` | **random per CI build** |
| signing certificate | **yes** — `PackageManager.GET_SIGNATURES` | **new RSA key per CI build** |
| app label | **yes** | random from a small list |
| APK bytes / hash | **no** — since Android 11 an app cannot read another app's files | nothing needed |
| overlay window | partially — not enumerable, only touch/`FLAG_SECURE` side effects | small panel, close it in fights |
| number of installed apps | yes, but every phone has 60–200 | not a signal on its own |
| device fingerprint | yes, **server-side** | not this app: a flagged device stays flagged |

So "kena blacklist" almost always means one of two different things:

1. **The app is on a package/certificate list.** Then the fix is identity, and
   it is already in the build: `.github/workflows/android.yml` generates a
   random `applicationId`, a random label and a fresh keystore on every run,
   builds with `-PappId/-PappLabel/-PksFile/...`, and proves the id reached the
   manifest before publishing. `dist/IDENTITY.txt` says what this build is:

   ```
   package: com.4f2a91.b7c0de
   label:   Utilities
   Owner:   CN=Android, OU=Android, ...
   SHA256:  <cert digest>
   ```

   The old package is dead the moment a new build exists. Nothing to clean up
   beyond `adb uninstall <old id>`.

2. **The device or account is flagged server-side** (cheater lobby, trust
   score, "blacklist" in the matchmaking sense). No client change fixes that —
   the verdict was made from behaviour, and it follows the device. What helps:
   a different device identity, and not repeating whatever produced the flag
   (snap aim, impossible movement, wallshot, 100% headshot rate).

   Our architecture removes the *client* tells, but it cannot un-flag a device
   that was already judged. That is why the aim design is smooth-by-default and
   why ESP comes before aim.

The APK is also optional. Nothing in the read path needs it: the daemon is a
root process and the settings come over UDP. If a build's identity is burned,
you can keep using the daemon from the previous install while a new identity is
built — or not install anything at all and drive it over `adb`.

## 7. The laptop channel — WiFi or hotspot

Settings are pushed from the laptop, not edited on the phone. One authenticated
datagram, one way. The phone never sends anything back unless `reply` is
explicitly enabled, so there is no connection, no DNS lookup, no TLS handshake,
and nothing on the network that shows a device was being driven.

### Setup

1. Laptop: turn on the mobile hotspot (Windows: Settings → Network → Mobile
   hotspot), or put both devices on the same WiFi.
2. Phone: connect to that hotspot. Note the phone's IP — Android shows it under
   WiFi → the network → IP address, usually `192.168.137.x` on a Windows
   hotspot or `192.168.43.x` on an Android one.
3. Open FFMenu. The first line after `root:` is
   `config: listening udp/47811`.
4. On the laptop:

```bash
python tools/lan_config.py --set esp_box=true --set esp_max_dist=150
```

That is it. The next packet replaces the values in `Config`, and the overlay
reads them on the next frame — no rebuild, no reconnect.

### If the phone does not react

The packet went out the wrong interface. A laptop hotspot is not the default
route, so `255.255.255.255` leaves through WiFi/Ethernet and never reaches the
phone. Send to the hotspot subnet's broadcast instead:

```bash
# Windows Mobile hotspot
python tools/lan_config.py --target 192.168.137.255 --set esp_box=true
# Android tethering
python tools/lan_config.py --target 192.168.43.255 --set esp_box=true
# iPhone hotspot
python tools/lan_config.py --target 172.20.10.255 --set esp_box=true
```

`--target` is repeatable, and the default list already contains all of these
plus the limited broadcast, so the common case needs no flag. Verify the wire
without a phone:

```bash
python tools/lan_config.py --listen --port 47899      # in one shell
python tools/lan_config.py --port 47899 --set esp_box=false   # in another
```

### Fields

Anything `Config.apply()` reads can be set. `--set k=v` parses `true/false`,
ints (including `0x`), floats, and leaves everything else as a string.

```
esp_box esp_name esp_distance esp_health esp_max_dist esp_color
aim_enabled aim_bone aim_smooth aim_fov aim_max_dist
```

`--json '{"esp_color":"#00ff7e"}'` merges a raw object if you would rather send
the whole thing at once.

### Watch — offset hunting from the laptop

Before the overlay exists, the useful thing is to read an address and see the
value. `--watch` does exactly that:

```bash
python tools/lan_config.py \
  --watch 0x7c00d01234:u32:health \
  --watch 0x7c00d01240:vec3:position
```

Types: `u8 u16 u32 u64 f32 vec3`. With `--reply <phone-ip>:47812` the phone
answers with the current values (this is the only case where it transmits, and
it is off unless you ask). Everything else stays one-way.

### Wire format

Exactly what `LanConfig.java` parses, little-endian:

```
0..3    "FFC1"
4..7    u32 sequence        must strictly increase, compared unsigned
8..15   u64 timestamp ms
16..17  u16 json length     <= 1400
18..    json
+32     HMAC-SHA256(key, bytes[0 .. 18+len))
```

- The key is a shared secret, not a password: a packet with the wrong key is
  rejected before the JSON is parsed, so a stranger on the same hotspot cannot
  move your settings.
- The sequence number is monotonic, so a captured packet cannot be replayed.
- `tools/tests/test_lan_config.py` pins all of this, including that the key in
  `Config.java` and the key in `lan_config.py` are the same 32 bytes. Change
  one without the other and the suite fails instead of the phone silently
  ignoring you.

## 8. Detection — honest version

Nothing of ours is inside the game. What remains:

| signal | reachable by the game | mitigation |
|---|---|---|
| device is rooted | **yes** — `su`, Magisk paths, `ro.debuggable`, mount table | Magisk DenyList on `com.dts.freefireth`, rename the Magisk app, no other root apps |
| an overlay window is present | partially — `FLAG_SECURE`/touch filtering, not enumeration | keep the panel small, close it during fights |
| a process is reading its memory | **no** — `/proc/<pid>/mem` reads leave no trace in the target | nothing needed |
| a process holds an fd to its memory | only a **root** process can enumerate another process's fds; the game is not root | nothing needed |
| APK integrity | **passes** — the APK is untouched | nothing needed |
| behaviour | **yes**, same as iOS | smooth, chest, FOV ≤ 90, ESP only in ranked |

The root row is the one that decides most bans on Android, and it is decided by
how you set up Magisk, not by this app:

1. Magisk → **Configure DenyList** → tick `com.dts.freefireth` (and
   `com.dts.freefiremax`). Magisk then unmounts its own paths for that process,
   so `su`, `/sbin`, and the module mounts are not in the game's mount table.
2. Magisk → hide/rename the manager app (random package name). A game looking
   for `com.topjohnwu.magisk` finds nothing.
3. No other root apps, no LSPosed, no Xposed, no game boosters with root.
4. Once `CONNECT` has started the daemon, **revoke** FFMenu's root grant in
   Magisk → Superuser. The daemon already runs as uid 0 and keeps running; the
   app just cannot start another one until you grant it again. The grant
   history shows a revoked entry instead of a live one.

There is no "undetectable". The external design removes the in-process tells
that the iOS build could only hide; it does not remove the rooted device.

## 9. Files

```
android/
├─ daemon/
│  ├─ memd.c          root service: find pid, module base, read/write, maps,
│  │                  scrub its own cmdline, die on request
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
│           ├─ Root.java          su wrapper, unlinks the binary after spawn
│           ├─ MemClient.java     the wire protocol (little-endian)
│           ├─ Config.java        live settings + the shared key
│           └─ LanConfig.java     the UDP receiver, HMAC + replay guard
└─ offsets/
   └─ example.json     the shape the reader will consume

tools/
├─ lan_config.py      the laptop side: send settings, watch addresses
├─ apk_triage.py      inspect any APK with no SDK: manifest, cert, libs, strings
└─ tests/
   ├─ test_memd.py          drives the real daemon on Linux
   ├─ test_lan_config.py    pins the packet format, runs sender -> listener
   ├─ test_java.py          compiles the app's Java against API stubs
   └─ test_apk_triage.py    the APK inspector against a synthetic APK
```

The old iOS tree is not deleted from history: tag `ios-final` on `main` is the
last state of it, including the ban post-mortem in `native/ANTIDETECT.md`.
