# INSTALL — all iOS, any installer, and what "safe" actually means

## 1. "All iOS" — the honest boundary

Three things set the floor:

| Limiter | Reality |
|---|---|
| Free Fire itself | FF's own `MinimumOSVersion`. Currently 12.0+, so no dylib makes it run on iOS 11 |
| The dylib | built with `TARGET = iphone:clang:latest:12.0` and every iOS 13+ API behind a `@available` check (`Core/Compat.h`) — so it never raises the floor |
| Architecture | `arm64` only. Free Fire 1.132.1 ships no arm64e slice, so the dylib is built for the only architecture that exists in the payload |

So: **iOS 12 and up, every arm64 device.** That is the maximum possible
range — the app's own minimum is the wall, and nothing in a dylib can move it.

What was actually fixed to get there:

- `monospacedSystemFontOfSize:weight:` (13.0+) → `FFMono()` falls back to Menlo
  on iOS 12
- `selectedSegmentTintColor` (13.0+) → `FFSetSegmentTint()` falls back to `tintColor`
- window creation goes through `FFMakeWindow()` so no `UIWindowScene` assumptions

## 2. Installer matrix

| Installer | iOS range | Cert needed | Bundle id | Entitlements | 7-day? |
|---|---|---|---|---|---|
| **TrollStore** | 14.0 – 16.6.1 (+ some 17.0) | **none** (fake-signs) | **original kept** | **original kept** | **no** |
| Sideloadly | any (12+) | free Apple ID or dev cert | must be rewritten | from your profile | yes |
| AltStore / SideStore | any (12+) | free Apple ID | must be rewritten | from your profile | yes |
| ESign / GBox / Scarlet | any (12+) | their cert, or yours | whatever you choose | depends on their cert | until revoked |
| **Father / any online signer** | any (12+) | **their** cert, or yours if it accepts a p12 | follows the cert | follows the cert | until revoked |
| Jailbroken (Sileo/dpkg) | matches jailbreak | none | original | original | no |

**TrollStore is the best option by a wide margin** — no certificate, no expiry,
and it keeps the real bundle id and the app's original entitlements. That is why
almost every FF mod IPA in circulation targets the TrollStore range.

If you are outside that range, Sideloadly with your own free Apple ID is the
clean route. Its provisioning profile includes `get-task-allow` — not because
anything needs it (an `LC_LOAD_DYLIB` is mapped by dyld regardless of whether
the app is debuggable), but because every development profile carries it. It is
a signal, not a requirement, and `Core/Integrity.m` refuses to answer it at
runtime.

**One catch nobody mentions:** a free Apple ID cannot register the bundle id
`com.dts.freefireth` — it is already taken. Sideload-only installs must rename
it. That's why the packager has a flag:

```bash
python tools/package_ipa.py decrypted.ipa FFMenu.dylib --bundle-id com.me.ff1
```

Renaming can affect login on rare builds. If FF refuses to sign in, that is the
cause, not the dylib.

### 2b. "Bisa pasang pakai Father?" — yes, with three conditions

Any signer works, because the IPA is deliberately sign-agnostic: decrypted,
no `_CodeSignature`, no `embedded.mobileprovision`, no injected load command
outside the bundle. Father, Sideloadly, ESign, GBox, AltStore and plain
`codesign` all see the same payload. What decides success is not the app:

1. **It must sign nested code.** `Frameworks/UnityServices.framework/UnityServices`
   is where the dylib lives, and it is a *separate* Mach-O that needs its own
   signature with the same certificate. A signer that only signs
   `Payload/FreeFire.app/FreeFire` produces an app that installs and then dies
   at launch with `Library not loaded ... code signature invalid`. Every
   current signer does this correctly; a hand-rolled script often does not.
2. **The bundle id must match the profile.** Free Apple ID and paid teams
   cannot register `com.dts.freefireth` (Garena owns it), so use the renamed
   payload. An enterprise cert with a wildcard profile can take either.
3. **Entitlements must come from the profile, not from a guess.** That is what
   the generator is for:

```bash
python tools/make_sideload_entitlements.py \
    --profile dev.mobileprovision \
    --ipa out/com.alokgacorcuy11.freefire-payload.ipa \
    --udid 00008030-000A1B2C3D4E5F60 \
    -o out/entitlements.sideload.plist
```

It prints what the profile grants, rewrites `application-identifier` and the
keychain group to the IPA's real bundle id (without them every keychain call
returns `-34018` and the game cannot stay logged in), drops `get-task-allow`,
drops the macOS-only network key, and tells you if the profile is expired, is a
wildcard, is an enterprise cert, or does not contain your UDID.

Then sign with that file if the tool asks for one:

```bash
zsign -k cert.p12 -p pass -m dev.mobileprovision \
      -e out/entitlements.sideload.plist -o signed.ipa \
      out/com.alokgacorcuy11.freefire-payload.ipa
```

Two device-side facts for iOS 16 and newer (so: your 26.6 too):

- **Developer Mode must be ON** for a development-signed app to launch:
  Settings → Privacy & Security → Developer Mode → on → reboot → confirm.
  Without it the icon installs and the app refuses to open.
- **Free Apple ID = 7 days**, 3 apps at a time, 10 App IDs per week. After
  expiry the app stops launching until it is signed again; the container and
  the account session usually survive a re-sign of the same bundle id.

| Symptom | Cause |
|---|---|
| `Unable to Verify App` / icon greyed out | cert not trusted: Settings → General → VPN & Device Management → Trust |
| installs, dies instantly, `code signature invalid` in Console | nested framework was not re-signed |
| `Library not loaded: @executable_path/Frameworks/UnityServices...` | the framework was not copied, or `Frameworks/` was stripped by the signer |
| installs, launches, FF login fails / loops | keychain group missing (`-34018`) — regenerate entitlements |
| "This app cannot be installed because its integrity could not be verified" | profile does not include this device's UDID |
| app opens, no orb, no panel | dylib did not load; check Console for `UnityServices` and read the DIAGNOSTICS path in §5 |
| works, then stops after a week | free cert expired — re-sign |

**The one thing a signer decides for you:** whose certificate. A shared
enterprise cert (what online signing services use) is used by thousands of
installs, gets revoked regularly, and its Team ID is a well-known cheat-install
signal. Your own Apple ID — free or paid — is strictly better on both counts.


## 3. Why the output is "sign-agnostic"

`package_ipa.py` produces a **payload-only** IPA:

- `_CodeSignature/` and `embedded.mobileprovision` removed → every signer
  (TrollStore, Sideloadly, ESign, GBox, Father) wants to create those itself,
  and a stale one makes installs fail or apps refuse to launch
- bundle id left as-is unless you pass `--bundle-id`
- entitlements are **not** embedded in the IPA at all — a signer derives them
  from your profile (correct), or takes them from a file you generate with
  `tools/make_sideload_entitlements.py` (§2b). `native/entitlements.plist` is
  deliberately empty; a hand-written one is how sideloads break.

So the same file goes into all of them. Nothing is installer-specific.

## 4. The flow, start to finish

```bash
# once per device
cd native && make                      # -> .theos/obj/arm64/FFMenu.dylib
cd ..
python tools/package_ipa.py com.dts.freefireth-decrypted.ipa \
                            native/.theos/obj/arm64/FFMenu.dylib
# -> out/com.dts.freefireth-payload.ipa
```

As a framework by default (`UnityServices.framework`). Want a different name?

```bash
python tools/package_ipa.py ... --name MobileCore
```

Then:

- **TrollStore** → open the IPA on device → Install
- **Sideloadly** → drag the IPA → your Apple ID → Start (add `--bundle-id` first)
- **ESign/GBox/Father** → import the IPA → sign with your cert → install
- **any signer that asks for entitlements** → §2b, do not hand-write the plist

Verify before installing, on your laptop:

```bash
python tools/ipa_probe.py out/com.dts.freefireth-payload.ipa
```

You should see `cryptid 0`, no `_CodeSignature`, and:

```
 -- vendor frameworks shipped in the bundle: 1
      @executable_path/Frameworks/UnityServices.framework/UnityServices
```

That last line is the camouflage working: the load command points at a framework
path, not a loose `libSomething.dylib`. A scanner looking for loose dylibs sees
nothing unusual.

### 4b. Packaging without uploading a 510 MB IPA

The workflow can package, but that means handing it the payload — and the IPA is
half a gigabyte, which is over GitHub's 100 MB file limit and a long upload
either way. The other direction works just as well: let CI build the 285 KB
dylib, bring *that* back, and package on the machine that already has the IPA.

The build job publishes the compiled dylib to a `build` branch precisely so this
is possible — Actions **artifacts cannot be downloaded without signing in**, even
on a public repository, while a branch file can:

```bash
# 1. fetch the compiled dylib and its manifest (no auth)
curl -fL -o FFMenu.dylib \
  https://raw.githubusercontent.com/<owner>/<repo>/build/dist/FFMenu.dylib
curl -fL https://raw.githubusercontent.com/<owner>/<repo>/build/dist/BUILD.txt
# 2. check it is the build you think it is - BUILD.txt carries the sha256,
#    the source commit and the size
sha256sum FFMenu.dylib
# expected: thin arm64 Mach-O, cputype 0x0100000C, filetype 6 (MH_DYLIB)
# 3. package locally
python tools/package_ipa.py decrypted.ipa FFMenu.dylib
# -> out/com.dts.freefireth-payload.ipa
```

Two outputs are worth keeping, because the installer decides which one you need:

| output | for | why |
|---|---|---|
| `com.dts.freefireth-payload.ipa` | TrollStore, a jailbreak, or a paid certificate | keeps the real bundle id |
| `com.<you>.freefire-payload.ipa` (`--bundle-id`) | a **free** Apple ID via Sideloadly / AltStore / ESign | `com.dts.freefireth` is Garena's registered App ID and collides with a personal team |

`out/PACKAGE.txt` records the dylib sha256, the source commit and a sha256 per
IPA, so a build can be identified later without guessing.

## 5. Stealth: what is real, what is marketing

`Core/Stealth.m` runs first, before anything else touches the image list.

| Tier | What it does | Cost |
|---|---|---|
| **1 — camouflage** (always on) | loads as `Frameworks/UnityServices.framework/UnityServices`, `-fvisibility=hidden`, XOR-decoded log tag, uninformative preference keys, and `make RELEASE=1` compiles every log string out | free |
| **2 — image hiding** | rewrites the game's own symbol-pointer slots, lazy and non-lazy, for `_dyld_image_count` / `_dyld_get_image_name` / `_dyld_get_image_header` / `_dyld_get_image_vmaddr_slide` / `objc_copyImageNames` / `objc_copyClassNamesForImage` | **free — no Dobby** |
| **3 — behavioural** | `aim.smooth`, `aim.fov`, auto-release on idle, input-layer actuation | free, and it's the one that matters |

Tier 2 does **not** need an inline hook engine any more. `Core/Rebind.m`
generalises fishhook: it walks another image's Mach-O, finds its
`LC_DYSYMTAB` indirect symbol table, and overwrites the slot that image calls
through. Verified offline on this build:

```
$ python tools/got_scan.py <ipa>
__DATA,__got            slot  925 -> dyld_image_count        (non-lazy)
__DATA,__la_symbol_ptr  slot 1174 -> dyld_image_count        (lazy)
__DATA,__got            slot  924 -> dyld_get_image_name     (non-lazy)
__DATA,__la_symbol_ptr  slot 1172 -> dyld_get_image_name     (lazy)
__DATA,__got            slot  923 -> dyld_get_image_header   (non-lazy)
__DATA,__la_symbol_ptr  slot 1171 -> dyld_get_image_header   (lazy)
__DATA,__la_symbol_ptr  slot 1173 -> dyld_get_image_vmaddr_slide
__DATA,__la_symbol_ptr  slot 1488 -> objc_copyImageNames
__DATA,__la_symbol_ptr  slot 1487 -> objc_copyClassNamesForImage
```

If Dobby is not linked — and it is not, deliberately — the GOT path is the only
one, which is the point: nothing inside libdyld is modified, so a hash of dyld
still matches. If the slots were not there, it would stay at tier 1 and say so.
The DIAGNOSTICS panel prints the tier you actually got:

```
stealth tier 2 (dyld hidden, 9 slots, idx 12)
```

Tier 1 already defeats the dumb checks (`strstr(image_name, "cheat")`, "is there
a loose dylib load command"). Tier 2 defeats a full dyld enumeration. Neither
defeats a check that walks memory, and nothing defeats server-side behaviour.
See `ANTIDETECT.md` for the honest layer-by-layer table.

## 6. "Tidak kena ban" — the part you asked for, without the sales pitch

There is no anti-ban. Any product claiming it is selling you a feeling. Here is
the real ranking of what actually takes accounts, worst first:

**1. Behaviour (server-side) — this is what catches people.**
Rotation written at a fixed rate with no human jitter. 100% headshot rate over a
session. Acquiring targets across the map in a single frame. Never missing. None
of that is a client tell; it is a statistics problem on their side, and no amount
of hiding your dylib changes it.

What you control:
- `aim.smooth` ≥ 6, ideally 8–12
- `aim.fov` 60–90, not 400
- `aim.bone` = `chest`, not `head`
- `esp.max_dist` 200, so the aimbot can never reach something absurd
- stop everything with Quiet Mode when you're not actively using it — a session
  that is 90% clean and 10% assisted looks far less like software

**2. Certificate, not game.**
Shared enterprise certs (the ones store services use) are used by thousands of
people. Apple revokes them, and separately, anyone tracking installers can see
the same cert across many accounts. Your own free Apple ID, or TrollStore, avoids
this entirely. This is the single biggest difference between "works for a month"
and "gone in a week".

**3. Client tells.**
`get-task-allow` present, injected image enumerable. Tier 1 + Tier 2 above.

**4. History.**
A device already HWID-flagged comes back flagged. New account does not help.

### About "safe while anti-cheat isn't updated yet"

That read is correct, and here is the precise version of it: your exposure window
is defined by **their behavioural model**, not by their client checks. When FF
ships an anti-cheat update, the things that change first are detection
thresholds and new server-side signals. So:

- a client-side stealth improvement buys you time against *client* checks
- a behaviour improvement buys you time against *both*
- and after every FF update, re-run `il2cpp_hunt.js` and re-verify with the
  DIAGNOSTICS panel before you trust the build

Practical rules that are worth more than any obfuscation:
- test with an account you can lose
- one device, one account, no switching
- don't touch ranked until you've confirmed ESP-only works
- after a game update, assume detection changed even if nothing looks different

## 7. After a game patch

You do **not** rebuild the IPA.

```bash
frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js --no-pause
#   findClasses / dumpFields / findW2S / findPlayerContainers
```

OPTIONAL: edit `state/ff_native.json` → the proxy serves it at `/api/native` → the dylib
picks it up within 30 s. Confirm in DIAGNOSTICS.

Rebuild only when `il2cpp_*` exports themselves change (rare) or the header
padding disappears (then use `insert_dylib` on macOS — `package_ipa.py` tells you
that explicitly instead of producing a broken IPA).

## 8. Verified vs not

Verified on this machine (Windows, no Xcode):

- `tools/selftest.py` — proxy, panel, control state, menu API, rules, protobuf ×63
- `ffproxy_menu_e2e.py` — real mitmproxy, live toggles ×13
- `ipaprobe_test.py` — IPA classification ×19
- `pkgtest.py` — packaging: cryptid gate, framework injection, load-command
  verification, signature strip, bundle-id rewrite, fat-header integrity ×20

Not compiled here (no macOS/UIKit toolchain): the ObjC sources. First build on a
Mac will surface ordinary integration fixes. Send the compiler output and the
DIAGNOSTICS dump.

Nothing in this repo can be tested against the real Free Fire binary from here,
so every class/field name in `ff_native.example.json` is `"??"` on purpose.
`il2cpp_hunt.js` is how those get filled, and it is not guesswork — it reads your
build.

## 9. Handoff — what to send, and what happens next

When you have the decrypted IPA, send the file (or a link). Nothing else is
required — no device, no dump, no screenshots.

**What happens with it, in this order, on my side:**

| # | Step | Command | Produces |
|---|---|---|---|
| 1 | Audit | `python tools/ipa_probe.py FreeFire.ipa` | cryptid, archs, injected libs, bundle id, min iOS |
| 2 | Verify the toolchain view | `python tools/got_scan.py FreeFire.ipa` | the dyld slots tier 2 rewrites |
| 3 | Verify the runtime is reachable | `python tools/il2cpp_symbols.py FreeFire.ipa --require` | 256/256 il2cpp symbols, all dlsym-visible |
| 4 | Build | `make -C native RELEASE=1`, or the workflow | `FFMenu.dylib` with no log strings in it |
| 5 | Package | `python tools/package_ipa.py <ipa> FFMenu.dylib` | `out/<bundle>-payload.ipa`, camouflaged |
| 6 | Verify | `python tools/ipa_probe.py out/*.ipa` | proof the load command landed |

**All six run in GitHub Actions.** Push, or run the workflow from the Actions tab
with the IPA URL, and the whole chain happens on its own - including the checks that the
dylib does not name itself, and a manifest with the sha256 of both the dylib and the
final IPA. Steps 1, 2, 3, 5 and 6 also run offline on any machine (the full
suite is a few hundred checks and takes about six seconds - run it yourself for
the current count).

**What you get back**

- the packaged IPA, built from `make RELEASE=1` so the binary carries no log text
- the first-run decision log the resolver prints on the device, so you can see
  each choice it made (player class, container, singleton, position, w2s mode)
- the GitHub Actions workflow, so you can rebuild without me

**Check before sending**

```bash
python tools/ipa_probe.py your.ipa
```

You want `cryptid: 0`. If it says `1`, the dump failed — redo it with
`frida-ios-dump` or `bagbak` on the jailbroken device.

**Before installing:** keep the App Store copy installed so the device stays
usable and you can compare. The injected build is a separate install —
TrollStore gives it its own container, and sideloading needs a different bundle
id anyway.

**Two extras that make the first session easier**

1. the region and `CFBundleShortVersionString`, so the resolver log is labelled
   per build and a patch is easy to compare
2. a 5-second clip of normal gameplay — not for the cheat; so `aim.smooth`,
   `fov` and `bone` get tuned to look like the movement in that clip instead of
   a guess

**If the class names ever get obfuscated too** (they are not in 1.132.1 — the
member names are, the class names are not), `Resolve.m` already prefers type and
shape over names everywhere except the `COW.GamePlay.Player` anchor. Losing that
anchor would mean falling back to
`frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js` and `huntShape()`,
which scores classes by structure and would name the replacement.