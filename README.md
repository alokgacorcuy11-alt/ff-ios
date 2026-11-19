# ff — Android

Free Fire, read from outside the process. Root on the device, a small daemon
holding `/proc/<pid>/mem`, and an overlay that draws what it reads.

**Status: stage 1 (the bridge) is complete and testable.** The app finds the
game, finds `libil2cpp.so`, and reads its ELF header out of live memory. Reader,
ESP and aim build on that channel — see `android/README.md` for the exact
sequence and the offset workflow.

```
android/daemon/memd.c        root service: find pid, module base, read/write, maps
android/app/                 the app: su, socket, connect sequence, (next) overlay
android/offsets/example.json what the reader needs, with the ?? gate
tools/lan_config.py          laptop side: push settings over the hotspot
tools/apk_triage.py          inspect any APK with no SDK: manifest, cert, libs
```

## Why external and not injected

The iOS build had to live inside the game: a sandboxed process cannot be read
from outside. Android can. A root process opens `/proc/<pid>/mem` and the game
never learns it happened — no extra mapping, no extra thread, no modified APK,
no re-signing. The APK on the device stays byte-identical to the store copy, so
signature and file-hash checks pass by construction.

What that costs: we can read data, not call functions. Positions come from
fields; world-to-screen is recomputed from the camera matrices. That is a
smaller problem than hiding a dylib inside a process that is looking for one.

## Build

CI builds both artefacts and publishes them to the `build` branch:

```bash
curl -fL -o ffmenu.apk https://raw.githubusercontent.com/<owner>/<repo>/build/ffmenu.apk
curl -fL -O https://raw.githubusercontent.com/<owner>/<repo>/build/IDENTITY.txt
```

Every build gets a **new identity** — random package id, random label, fresh
signing key — so a package that lands on a blacklist is retired with the build
that carried it. `IDENTITY.txt` is what that build installs as:

```
package: com.4f2a91.b7c0de
label:   Utilities
SHA256:  <signing cert digest>
```

Installing a new build next to an old one leaves both; `adb uninstall <old id>`
removes the old one. Local builds keep `com.alokg.ffmenu`.

Locally: `bash android/daemon/build.sh` then `gradle -p android/app assembleDebug`
(Ndk + SDK + JDK 17).

## Run

```bash
adb install -r ffmenu.apk
```

Grant root when Magisk asks, open Free Fire, reach the lobby, then CONNECT in
FFMenu. The screen prints each layer's result, so a failure names itself instead
of hiding in a stack trace. Expected output and the failure table are in
`android/README.md` §3.

## Settings from the laptop, over the hotspot

The phone opens `udp/47811` and never answers. The laptop broadcasts one
HMAC-SHA256-signed packet; the phone applies it on the next frame. No
connection, no DNS, no TLS, and nothing on the wire that says a device was
being driven.

```bash
# phone on the laptop's hotspot, FFMenu open -> "config: listening udp/47811"
python tools/lan_config.py --set esp_box=true --set aim_smooth=6
python tools/lan_config.py --watch 0x7c00d01234:u32:health
```

If nothing happens, the packet left through the wrong interface — a hotspot is
not the default route. Send it to the hotspot's broadcast instead:

```bash
python tools/lan_config.py --target 192.168.137.255 --set esp_box=true
```

`--listen` verifies the channel without a phone. Format, fields, the watch
list and the troubleshooting table: `android/README.md` §7.

## Nothing left behind

| | |
|---|---|
| inside the game | nothing — no image, no thread, no hook |
| game APK | byte-identical to the store copy; signature and hash checks pass |
| daemon binary | unlinked after exec — no path on disk |
| daemon `cmdline` / `comm` | `logd.writer`, uid argument erased in place |
| socket | abstract namespace — no filesystem node |
| boot script / module | none — a reboot leaves no daemon and nothing restarts it |
| phone → laptop traffic | none, unless `--reply` is explicitly enabled |

After `STOP` the only thing on the device is the app you installed. Uninstall
it and there is nothing to find. Full table: `android/README.md` §6.

## Blacklist

A blacklist is not a ban. It is keyed on something the game can observe: the
package id, the signing certificate, the label. All three are randomised per
build (§Build), so the list entry dies with the build. The APK's bytes are not
readable by another app since Android 11, and nothing of ours is inside the
game anyway.

If the "blacklist" is server-side — cheater lobby, trust score, a flagged
device — no client change fixes it. That verdict came from behaviour and it
follows the device. Table and the two cases: `android/README.md` §6b.

## Detection

Nothing of ours is inside the game, so the in-process tells are gone: no dylib,
no hook, no image in `maps`, no APK modification. What remains is the rooted
device, which is decided by how Magisk is set up (DenyList on the game, renamed
manager, root grant revoked after the daemon starts), not by this app.
Behaviour still decides most bans — smooth ≥ 6, chest, FOV ≤ 90, ESP before aim,
and nothing in ranked until you know the build is stable. Full table and the
Magisk checklist: `android/README.md` §8.

## The old mitmproxy panel (optional)

`ffproxy/`, `menu/`, `rules/`, `run.py` are the mitmproxy panel from the iOS
build: it serves the feature JSON and the HTML panel over the game's own
WebView. On Android the overlay replaces it, so it is optional — kept because
it is tested, and because a laptop-side config that the app polls is sometimes
easier than editing a file on the device.

```bash
pip install -r requirements.txt
python run.py            # panel on http://localhost:8081
```

## History

The iOS build (Theos dylib, IPA packaging, entitlement handling, and the ban
post-mortem that concluded the ban was a launch-time integrity verdict) is
tagged **`ios-final`**. Nothing was lost — it is one `git checkout` away.
