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
   builds with `-PappId/-PappLabel/-PksFile/...`, then reads the finished APK
   back with `tools/verify_build.py` — package id in the binary manifest, label
   in `resources.arsc`, certificate digest — and refuses to publish on a
   mismatch. `dist/IDENTITY.txt` says what this build is:

   ```
   package: com.a4f2a9.b7c0de
   label:   Utilities
   apk:     <sha256 of the apk>
   sdk:     26 -> 34
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

## 8b. What the XYZ build actually is

Unpacked for comparison: `XYZ VIP V3.0.7z` (key `LANXZ-PROJECKT`) → one APK,
`com.xyz.app` 3.0 (50), 12,356,089 B, sha256
`a48d526040975f73e50d49b67f8c5a41a5df70b23ef4b19516fd5aedaff19ce2`.

The ad says *NO ROOT & VIRTUAL, NO SHIZUKU, NO VPN, NO KEY*. Reading the DEX
says something else.

| what it does | evidence |
|---|---|
| pairs with the phone's own `adbd` over wireless debugging | `AdbClient extends io.github.muntashirakon.adb.AbsAdbConnectionManager`, `libspake2.so`, `libconscrypt_jni.so`, `PairingService`, string `adb pairing_auth aes-128-gcm key` |
| writes into the game's sandbox | `ModuleInstaller.dir()` = `Environment.getExternalStorageDirectory()/Android/data/com.dts.freefireth/files` |
| transfer | `printf '%s' '<base64 chunk>' \| base64 -d >> <tmp>`, then `sha256sum` compare, `chmod 644`, `mv -f` |
| drops two files | `assets/ffrtc_log.txt` (172,823 B) and `assets/localConfig.json` (54 B) |
| container | `RMP1` + 12-byte IV + AES-256-GCM, key derived in `AssetCipher`, AAD = the filename |
| decrypted `localConfig.json` | `{"testCodePatch":true}` — an IFix config switch |
| decrypted `ffrtc_log.txt` | 172 KB of a real Free Fire RTC log from an SM-A065M — a decoy, chosen because the game writes a file by that name itself |
| the actual cheat | `assets/Assembly-CSharp-patch.bytes` → 119,036 B, an IFix patch assembly for `Assembly-CSharp, Version=0.86.0.518` |
| what the patch hooks | `CurrentLocalPlayer`, `GetHeadTF`, `get_HeadCollider`, `get_NeckBone`, spine/arm/leg/foot/hand/hip getters, `GetActiveWeapon`, `IsHoldingFireForSingleShot`, `SetAimRotation`, `get_InSwapWeaponCD`, `ResetSwapWeaponTime` |
| feature flags inside it | `XYZ_V6_DRAG_NECK`, `XYZ_ESP_LINE_V6`, `XYZ_V8_ESP_NAME_NICKNAME`, `XYZ_ESP_HEALTH_FILL_V2`, `XYZ_V25_SKELETON_WHITE_1`, `XYZ_V25_DISTANCE_METRE_1`, `XYZ_V26_NORECOIL_PROFILE_1`, `XYZ_V27_HEAD_PRIORITY_1`, `XYZ_V27_FAST_SWITCH_1`, `XYZ_V28_NOSPREAD_TEST_1`, `XYZ_V30_RELOAD_X2_1` |
| ESP rendering | Android `Canvas`/`Paint`/`Bitmap` plus `UnityEngine.TextMesh`, from inside the Unity process |
| needs a patched client | the app's own error string: *"Modul tidak tersedia. Buka game dengan pemuat yang kompatibel dan diizinkan."* |

So it is not an external cheat. The panel is a delivery mechanism; the cheat is
a patched `Assembly-CSharp` running inside Free Fire. "No root" is true, and it
is the only claim that is: ADB runs as uid 2000 (`shell`), which may write into
another app's external files directory without root — that is the whole reason
the wireless-debugging step exists.

The build is also itself a repack: `SealGuard.full/integrity/key` all
`return 1`, and it carries `assets/SignatureKiller/origin.apk` (6.1 MB) plus
`libSignatureKiller.so` for four ABIs — MT Manager's signature killer.

The decrypted patch also settles what the target client is. It references
`Assembly-CSharp` and IFix, and starts an HTTP server in-process that serves
`/item1` … `/item5` and `/itemN/desativar` — which is the panel: a WebView
pointed at `127.0.0.1` inside the game. `Assembly-CSharp` and IFix only exist
in a Unity build with the Mono scripting backend, so the client under this is
**not** the store APK and not IL2CPP. The member list it resolves, grouped by
what it is for, is in `android/offsets/hook_candidates.md` - and it is the
reason the next thing needed is `Assembly-CSharp.dll` from the installed
client, not `libil2cpp.so`.

### Why it is blacklisted

Every one of these is a stable value shared by every user of XYZ V3.0:

| what the game can match | value |
|---|---|
| `localConfig.json` after decode | `3e5c4915bb8c0718aa6fbb8143cf954ff32221f9c0ef0b9d83fd0b1fc1c95535` |
| `ffrtc_log.txt` after decode | `eb185928fdf262bfe0a0a98ba1aad4cd5e40ae673070155e529aac0dd5c837c7` |
| the IFix patch after decode | `4a5bdc2295c73a83c69bd5790f2c53b22e8912f4ac58aafe7e8f43a2e472838e` |
| the patched client | repack: `classes.dex`, `libil2cpp.so` and every asset hash differ from the store build |

And two of those three files sit in **the game's own external files dir** —
the one directory Free Fire needs no permission and no trickery to list. A
single hash match on `ffrtc_log.txt` flags every user of the build at once.

On top of the files:

- the IFix patch adds a managed assembly; the game can walk
  `AppDomain.CurrentDomain.GetAssemblies()`
- ESP is drawn by Android views created from the game's own uid
- `SetAimRotation` snapping, no-recoil, 2× reload and fast-switch are timing
  anomalies the server sees regardless of what the client claims

"Blacklisted, not banned" is a response tier, not safety — and the ad itself
says *PAKAI AKUN KEDUA*, which is the author admitting the account gets burned.

### The comparison that matters

| | XYZ V3.0 | this project |
|---|---|---|
| package the game can name | yes, fixed | yes, random per build, and optional |
| code inside the game process | **yes** (IFix) | no |
| files in the game's sandbox | **yes**, two, fixed hashes | none |
| game APK modified | **yes** | no |
| needs root | no | yes |
| detectable by behaviour | yes | yes |

There is no no-root design that is also blacklist-safe. No-root means one of
two things:

1. **code inside the game** (XYZ's way, modded client, IFix, virtual space,
   cloned app) — detectable by construction, because everything it does happens
   where the game can see it;
2. **never touching the game process** — screen capture through `MediaProjection`
   plus touch injection through an Accessibility service. That one is genuinely
   clean, but it only sees pixels: no bone data, no player list, no names, no
   distance. Aim assist yes; ESP no.

The external reader in this repo is the third option, and it costs root.

## 8c. Checking a build before shipping it

`tools/footprint.py` answers "what could a list key on" for any APK:

```bash
python tools/footprint.py dist/ffmenu.apk --check
```

It reports the identity (package, certificate), the mechanisms it can recognise
(signature killer, managed patch, runtime dex loading, Frida, Xposed, native
hooking, ADB pairing, overlay, accessibility, screen capture), every
`Android/data/<other package>/` path in the DEX, the file-like strings that
could be payload names, and then the values that are identical for every user
of the file — package, certificate, APK hash, every asset over 4 KB.

`--check` exits non-zero on a hard finding, so it can gate a release. Run
against this project's APK it finds no cross-app writes and no runtime code
loading; run against XYZ it prints the table above.

## 8d. No root, no ADB, no files - the delivery layer

Three constraints, taken as given: **no root, no virtual space, no Shizuku**.
XYZ satisfies them with wireless ADB pairing and a file drop. This does it with
neither.

### The part that cannot be avoided

Full ESP - box, health, name, distance, skeleton - needs bone transforms and
the player array. Those live in Free Fire's process. No-root means no
`ptrace`, so the code that reads them has to be **inside** that process. There
are exactly two ways in: a repacked client, or a virtual space. Virtual space
is out by constraint, so the client is repacked. Nothing in this section
changes that; it changes what the repack has to carry.

### What it does not carry

XYZ's repack carries an IFix patch assembly that runs inside the managed
runtime, plus two files in `/Android/data/com.dts.freefireth/files` whose
decoded hashes are identical for every user. Both are permanent, nameable
artefacts.

Instead:

```
panel (random package, random cert)                client (repacked)
┌───────────────────────────┐                     ┌──────────────────────┐
│ FeedServer               │  127.0.0.1:<port>   │ FeedFormat.fetch     │
│  container, in memory    │ ──────────────────► │  verify MAC          │
│  HMAC challenge-response │                     │  decrypt             │
│  never written to disk   │                     │  module bytes in RAM │
└───────────────────────────┘                     │  memfd_create        │
                                                   │  dlopen(/proc/self/fd/N)
                                                   └──────────────────────┘
```

- the container exists only in the panel's heap; it is never written anywhere,
  so Free Fire has nothing to list, stat or hash
- the client's key lives in the client; the panel cannot read the module back
  out either
- the module is never a file on the client: it goes straight from the socket
  into an anonymous `memfd` and is loaded from `/proc/self/fd/N`, so it does
  not appear in the APK and has no path in `/proc/<pid>/maps`

### Per install, nothing is shared

`tools/payload_forge.py` writes a container and a manifest:

```
container = XYZF | ver | nonce(12) | padlen(u16) | moduleLen(u32) | ct | tag(32)
encKey       = HMAC-SHA256(master, "xyz-enc")
macKey       = HMAC-SHA256(master, "xyz-mac")
keystream[i] = HMAC-SHA256(encKey, nonce | u32le(i))
tag          = HMAC-SHA256(macKey, header | ct | installName)
```

Every forge draws a new 32-byte master, a new install name, a new padding
length and a new port. Two forges of the same module share no bytes and no
hash, which is the property XYZ is missing.

```bash
python tools/payload_forge.py --module out/libxyz.so --out feed
python tools/payload_forge.py --open feed            # verify + decode
```

`feed/container.bin` goes into the panel's assets, `feed/manifest.json` gives
the client its key, name and port.

### Why the MAC is not optional

Every app on the device can connect to `127.0.0.1`. Two things follow, and both
are handled:

| attack | why it fails |
|---|---|
| another app connects and takes the container | it is ciphertext; the key is only in the client |
| another app squats the port and answers our client | the response is `HMAC(macKey, challenge \| container)` and the client checks it before decrypting |
| a stale container from an older install is replayed | the install name is inside the tag |

`tools/tests/test_feed.py` compiles the Java classes and speaks the protocol to
them from Python - challenge, response, MAC check, decode - then serves from
Python and makes Java's `fetch` pull it. If the two sides ever drift, that test
fails instead of the module silently not arriving.

### Commands ride the same socket

The container goes over once. Everything after that - every toggle, every
slider - uses the connection that is already open:

```
frame = type(1) | seq(u32le) | len(u32le) | payload | mac(16)
mac   = HMAC-SHA256(macKey, type | seq | len | payload)[0:16]
```

`seq` increases strictly in each direction and is checked, so a toggle cannot
be replayed by another app watching loopback. No command file, no second port,
no second artefact.

In the app this is `FeedService`: reads `container.bin` and `manifest.json` from
assets, binds the port the client was built with, hands the container over,
then keeps the socket and pushes whatever the panel sends. It is a foreground
service because the panel is backgrounded the moment Free Fire comes up.

```bash
python tools/payload_forge.py --module out/libxyz.so --out android/app/feed
# build.gradle.kts picks the directory up as an asset source if it exists
```

### Against XYZ, measured

| | XYZ V3.0 | this |
|---|---|---|
| files in the game's data dir | **2**, fixed hashes | **0** |
| values shared by every user | **3 payload hashes** + package + cert | package and cert, both random per build |
| wireless debugging left enabled | **yes** | no |
| ADB public key registered on the device | **yes** | no |
| code added to the managed runtime | **yes** (IFix assembly) | no |
| module visible in the APK or on disk | **yes** | no |
| panel package | fixed `com.xyz.app` | random per build |

### What this still does not fix

The repacked client is the remaining tell, and no amount of transport work
removes it:

1. **its signature is not Garena's.** If Free Fire compares its own signing
   certificate against a constant, the comparison is inside code that is
   already patched - patch it too.
2. **its file hashes differ from the store build.** Same answer: the check runs
   in the client, so the client decides the answer. Keep the patch small, keep
   the native module out of the APK, and the number of things that have to be
   changed to lie about it stays small.
3. **behaviour is still behaviour.** `SetAimRotation` snapping, 2× reload,
   no-recoil and fast-switch are timing anomalies the server sees whatever the
   client says. The module has to be written smooth by design, same as §8.

That is the honest ceiling of no-root: the cheat is inside the game, so the
game can find it. It cannot find a file that does not exist, a hash that is
different for every user, or a package it has never seen - which is what this
layer buys.

## 8e. The panel - settings without a laptop

`Panel.java`. A floating window over the game, one row per setting, and every
row pushes the whole config down the feed channel the moment it changes.

```
┌─────────────────────────┐
│  ≡  PANEL     AIM  ESP  │   <- drag here; tap to hide
│  ESP                    │
│   [x] BOX               │
│   [x] LINE              │
│   [ ] SKELETON          │
│   MAX DIST: 200  −   +  │
│  AIM                    │
│   [ ] AIMBOT            │
│   BONE: CHEST           │   <- cycles head/neck/chest/body
│   FOV SIZE: 90  −  +    │
│  feed: connected        │   <- tap to resend
│  HIDE PANEL             │
└─────────────────────────┘
```

It needs `SYSTEM_ALERT_WINDOW`, which the manifest already asks for; the
`PANEL` button opens the settings page if it has not been granted.

The whole thing is a table over `Config`, which is the same object the laptop
writes to. So there is one vocabulary, not two:

```
panel row  ->  cfg.aimFov = 95        ->  cfg.toJson() -> feed channel -> module
laptop     ->  --set aim_fov=95       ->  udp packet   -> cfg.apply()  -> module
```

`apply()` and `toJson()` have to stay in step or a toggle silently does
nothing, so `tools/tests/test_java.py` reads both methods out of the source and
checks the key sets: everything `toJson()` writes must be something `apply()`
reads, and the only keys allowed to be one-directional are the input-only ones
(`esp_color`, `reply`, `watch` and the watch entry fields). Adding a setting to
one side and forgetting the other fails the suite.

Two hazards worth knowing before this is used in anger:

- `PANEL` does not depend on the feed. With the channel down the rows still
  change `Config`; the module just never hears about it. The status row says
  `feed: not connected` for that reason - tap it to resend once the client is up.
- the row list is rebuilt on every change, which is fine for twenty rows and
  would not be for two hundred. If it ever grows, keep the views and update the
  labels instead.

## 8f. Keys - install, type, unlocked

`tools/keygen.py` issues, `License.java` verifies, on the device, with no
server round trip. Twenty characters in five groups:

```
XXXX-XXXX-XXXX-XXXX-XXXX
```

```
alphabet   23456789ABCDEFGHJKLMNPQRSTUVWXYZ   (no 0/O/1/I)
payload    12 chars = 7 bytes
           b0       version | tier
           b1..b2   expiry in days since 2026-01-01, LE. 0 = never
           b3..b6   device hash, LE. 0 = any device
mac        8 chars = 5 bytes = HMAC-SHA256(secret, payload)[0:5]
```

```bash
python tools/keygen.py --secret "<secret>" --days 30            # any device
python tools/keygen.py --secret "<secret>" --days 30 --device <android_id>
python tools/keygen.py --secret "<secret>" --verify <key>
```

The device hash is `sha256(secret + "|" + ANDROID_ID)[0:4]` as a big-endian
int, computed identically in Python and Java. `tools/tests/test_license.py`
issues a key in Python and feeds it to the real `License` class: fresh key,
flipped character, expired key, bound key on the wrong phone, unbound key
anywhere, bad alphabet, lowercase. If the two implementations ever drift, every
key already sold stops working - so that test is the one that matters.

**What this is and is not.** The secret ships in the APK, so anyone who
decompiles it can mint keys; offline licensing cannot be stronger than that.
What it buys: a leaked key can be bound to one phone, an expiry can be set, and
nothing has to ask the network for permission in the middle of a match.

The panel refuses to open past the lock row until a key verifies, and the vision
loop returns immediately, so an unlicensed build draws nothing even if someone
starts the capture by hand.

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
│           ├─ Panel.java         floating settings menu over the game
│           ├─ FeedService.java    foreground loopback feed: deliver, then push
│           ├─ feed/FeedFormat.java   container format + the client-side fetch
│           ├─ feed/FeedServer.java   delivers once, keeps the socket
│           ├─ feed/FeedChannel.java  authenticated, sequence-numbered frames
│           ├─ Root.java          su wrapper, unlinks the binary after spawn
│           ├─ MemClient.java     the wire protocol (little-endian)
│           ├─ Config.java        live settings + the shared key
│           └─ LanConfig.java     the UDP receiver, HMAC + replay guard
└─ offsets/
   └─ example.json     the shape the reader will consume

tools/
├─ lan_config.py      the laptop side: send settings, watch addresses
├─ apk_triage.py      inspect any APK with no SDK: manifest, cert, libs, strings
├─ verify_build.py    the publish gate: does the APK carry the identity asked for
├─ footprint.py       what a game could blacklist a build on, before shipping
├─ payload_forge.py   one container per install: unique key, name, padding, port
├─ keygen.py          issue and check activation keys
└─ tests/
   ├─ test_memd.py          drives the real daemon on Linux
   ├─ test_lan_config.py    pins the packet format, runs sender -> listener
   ├─ test_java.py          compiles the app's Java against API stubs
   ├─ test_apk_triage.py    the APK inspector against a synthetic APK
   ├─ test_verify_build.py  the gate against APKs broken one field at a time
   ├─ test_footprint.py     the blacklist-surface scanner, dirty vs clean APK
   ├─ test_payload_forge.py the container: uniqueness, tamper, manifest
   ├─ test_feed.py          python forge <-> java transport over a real socket
   ├─ test_vision.py        the detector against frames whose answer is known
   └─ test_license.py       keys issued in python, checked by the java verifier
```

The old iOS tree is not deleted from history: tag `ios-final` on `main` is the
last state of it, including the ban post-mortem in `native/ANTIDETECT.md`.
