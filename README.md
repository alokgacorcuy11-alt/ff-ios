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
```

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

## Detection

Nothing of ours is inside the game, so the in-process tells are gone: no dylib,
no hook, no image in `maps`, no APK modification. What remains is the rooted
device, which is decided by how Magisk is set up (DenyList on the game, renamed
manager), not by this app. Behaviour still decides most bans — smooth ≥ 6, chest,
FOV ≤ 90, ESP before aim, and nothing in ranked until you know the build is
stable. Full table: `android/README.md` §6.

## The laptop sidecar (optional)

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
