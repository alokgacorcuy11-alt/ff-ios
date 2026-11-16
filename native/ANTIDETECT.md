# ANTI-DETECT — what is actually handled, and what is not

Written against `com.dts.freefireth` 1.132.1. Every claim below is either
backed by a command you can run, or explicitly marked as residual risk.

**There is no such thing as undetectable.** Anything that claims otherwise is
selling something. What this document gives you is an honest table: what this
build defeats, what it cannot, and where the real danger is.

---

## 0. The uncomfortable truth first

For Free Fire specifically, the dominant ban vector is **not** client-side
detection. It is server-side behavioural analysis:

| vector | weight | can a client-side cheat fix it? |
|---|---|---|
| Headshot rate / snap analysis | **highest** | partly — by never snapping |
| Impossible movement (speed, teleport, fly) | **highest** | no — do not do it |
| Damage / fire-rate out of range | **highest** | no — server-authoritative |
| Client integrity / signature checks | low | partly |
| dyld / dylib enumeration | low | yes |
| Jailbreak or Frida presence | low | yes (and we need neither) |

So the design principle here is: **read a lot, write almost nothing, and never
produce an input pattern a human cannot produce.** Everything in §2 follows
from that.

---

## 1. Client-side layers, one by one

### 1.1 Certificate / Team ID check

Garena can read the app's own signing identity and compare it to Apple's.

- Re-signed IPA (Sideloadly, AltStore, ESign, GBox): **different Team ID** →
  detectable if they check.
- TrollStore: signed with a placeholder identity. Also different.
- Official App Store install: unchanged, but then you cannot inject at all.

**Status: not addressable.** Nothing in a dylib can forge a code signature; the
signature is verified by the kernel before our code runs. This is the single
biggest client-side risk and it is a property of *how you install*, not of this
build.

Mitigation that does exist: use a build with `get-task-allow` only if you need
debugging, and prefer TrollStore (no profile expiry, no 7-day re-sign, no
Apple ID attached to the device).

### 1.2 Binary integrity / checksum

The packaging step adds one `LC_LOAD_DYLIB` to `FreeFire` and drops
`UnityServices.framework` into the bundle. Any hash of the executable changes.

**Status: partly addressed, partly not.**
- The *asset* integrity paths are untouched: `global-metadata.dat`,
  `resources.assets`, `resources.assets.resS` are byte-identical. That is where
  Unity games usually look, and it is the reason this build does not modify a
  single game file.
- The executable hash does change. If they hash the Mach-O, that is visible.

Verification that no game data changed:

```bash
python tools/package_ipa.py in.ipa FFMenu.dylib --dry-run   # lists what changes
```

### 1.3 dyld image enumeration  ← **handled**

The game can walk `_dyld_image_count` / `_dyld_get_image_name` and look for an
unexpected image, or call `objc_copyImageNames`.

Handled by `Core/Stealth.m` + `Core/Rebind.m`. No inline hook engine, no Dobby,
no substrate — the game's own symbol-pointer slots are rewritten.

Verified offline on this exact build with `tools/got_scan.py`:

```
__DATA,__got          slot  925 -> dyld_image_count            (non-lazy)
__DATA,__la_symbol_ptr slot 1174 -> dyld_image_count            (lazy)
__DATA,__got          slot  924 -> dyld_get_image_name
__DATA,__la_symbol_ptr slot 1172 -> dyld_get_image_name
__DATA,__got          slot  923 -> dyld_get_image_header
__DATA,__la_symbol_ptr slot 1171 -> dyld_get_image_header
__DATA,__la_symbol_ptr slot 1173 -> dyld_get_image_vmaddr_slide
__DATA,__la_symbol_ptr slot 1488 -> objc_copyImageNames
__DATA,__la_symbol_ptr slot 1487 -> objc_copyClassNamesForImage
```

9 slots across 6 of the 7 targets, in **both** the lazy and the non-lazy
symbol-pointer sections. `USLink rebindEverywhere:` walks every section of
the section type, so both are patched, which matters: a check that binds at
launch goes through `__got` and never touches `__la_symbol_ptr`.

`dyld_get_image_header_containing_address` is the one target that is *not*
reboundable here, because neither binary imports it - so the game has no slot
to call through. It would have to `dlsym` it, which is the same gap as below.

```bash
python tools/got_scan.py <ipa>          # reproduce
```

Limits, stated plainly:
- If the game calls these through its own internal trampoline (not through a
  symbol pointer), rebinding does nothing. On this build it does not — the slots
  above are the ones the two binaries actually bind.
- If they use `dlopen` + `dlsym` instead, rebinding does nothing either. That is
  an accepted gap, not an oversight: covering it would mean patching `libdyld`
  in place, and a modified dyld is both easier to spot (hash / prologue check)
  and a process-wide change that other dylibs can trip over. See §4.

### 1.4 Camouflage  ← **handled**

The injected image does not load as `FFMenu.dylib`. It loads as
`UnityServices.framework/UnityServices` — a real Unity framework name that
belongs in a Unity app.

```bash
python tools/package_ipa.py in.ipa out.dylib      # default: camouflage on
python tools/package_ipa.py in.ipa out.dylib --no-camouflage   # to compare
```

Inside the dylib itself, six things, all of them things a scanner actually
looks at:

| what | how | verify |
|---|---|---|
| symbols | `-fvisibility=hidden` + `-Wl,-x` strips local symbols | `nm -u FFMenu.dylib` shows system imports only |
| install name | `LC_ID_DYLIB` points at the camouflage path, not at the build artefact | `otool -D` / `strings` |
| class names | ObjC classes are named `USPanel`, `USModel`, `USOverlay`, … — the OC classlist is in the binary and **no linker flag removes it** | `strings -a FFMenu.dylib \| grep -i uspanel` |
| feature keys | the 15 `aim.*` / `esp.*` keys live in an XOR-decoded blob, not as literals | `strings -a FFMenu.dylib \| grep 'esp\.'` → empty |
| preference keys | the app's own plist gets `us.core.prefs.3`, not something named after a cheat | read `Library/Preferences/<bundle>.plist` |
| log messages | `make RELEASE=1` compiles every format string out entirely | `strings -a FFMenu.dylib \| grep -i aim` → empty |

The workflow enforces all of that on every build (`build-and-package.yml`,
step "does it advertise itself?"), and it fails the run if a feature key or a
product token is still readable. It is not a promise in a document, it is a
gate.

What it does **not** hide: the feature-key strings do exist on the wire, because
the panel and the dylib speak the same protocol. That is TLS'd traffic between
your laptop and your device, not a fingerprint in the binary.

### 1.5 Jailbreak detection  ← **not applicable**

This build needs no jailbreak. No `/var/jb`, no `Cydia`, no `substrate`,
`libhooker` or `ElleKit` is touched, so the usual `stat("/Applications/Cydia.app")`
family of checks finds nothing to find.

The only thing that touches the filesystem is optional: the `ff_native.json`
override in the app's Documents directory, which is a normal file in a normal
app sandbox.

**If you install on a jailbroken device do not enable the tweak-style install
path** (`make ROOTLESS=1`) — that path *does* leave jailbreak-shaped artefacts
and turns a non-vector into a vector.

### 1.6 Frida / debugger presence  ← **handled by not using them**

The shipped IPA contains no Frida gadget, no `frida-server`, no debugserver.
Frida is used during *research* only (a separate throwaway build), never in the
build you play on.

`Core/Stealth.m` additionally does not open any listening socket, so there is no
port to scan for.

If a check looks at `sysctl` `P_TRACED`: nothing of ours sets it. If you attach
lldb for debugging you are the one who sets it — do not debug on a live account.

### 1.7 App Attest / DeviceCheck  ← **not addressable**

If Garena validates App Attest assertions, a re-signed app fails validation
server-side, and there is no way to forge an assertion: the key lives in the
Secure Enclave and is bound to the App ID and Team ID.

**Status: inherent.** Not a code problem. This is the one thing that would make
all client-side work pointless, and the only way to know is to watch the
server's behaviour (do you get placed in bot lobbies? silend quarantine?).

### 1.8 Anti-cheat self-integrity of the injected path

We do not patch any game function and we do not modify any game file, so there
is no "modified code" for an integrity check to find inside `UnityFramework`.
The only modification is the load command in the main executable (`FreeFire`,
71 KB, a thin shell — see the probe output), not in the 208 MB UnityFramework.

That is deliberate: the smaller and less interesting the file you modify, the
smaller the surface.

---

## 2. Behavioural layer — where bans actually come from

This is `FFStealthTier3`, and it is the layer that matters. Nothing in code can
save an account from behaving like software, so the build is designed around
what *not* to touch.

### What this build will never do

| capability | why it is absent |
|---|---|
| teleport / fly / speed | server validates movement; it is the fastest ban there is |
| damage / fire-rate edits | damage is computed server-side; editing locally makes the client wrong, not the server |
| currency / diamonds | every one of these is server-authoritative and logged |
| instant kill / auto-fire | fire pattern analysis is trivial to implement and they have it |
| blocking anti-cheat endpoints | stubbing an integrity check is how you get flagged on purpose. `rules/freefire.yaml` has that rule, disabled, with a comment saying why |

### What the aimbot does instead

`Core/Game.m` + `Core/ESP.m`:

- **A measured gain, not a guessed one.** Moving a finger does not rotate the
  camera by a fixed amount: it depends on the player's in-game sensitivity, the
  screen size and the look-Y inversion. So the gain is *measured* once per
  device by `+calibrateAim` (drag a known 48 points, read how far the camera
  turned, twice, once per axis) and stored. Before that measurement the aim does
  nothing at all — a wrong gain does not aim, it sweeps the camera past the
  target and back, which is louder than no aim.
- **Angular control loop.** Each tick computes the yaw and pitch error between
  camera forward and the target bone, converts it to finger travel with the
  measured gain, and applies a fraction of it (`aim.smooth` = frames to close).
  A single correction is capped at 24 points, so even at the fastest setting it
  ramps in instead of snapping.
- **Self-correcting gain.** The 48-point calibration drag can trip the game's
  look-acceleration, so the measured gain is only a starting point: every frame
  that a correction is applied, the loop compares the rotation it got with the
  one it asked for and moves the gain 25% toward the observation. That is what
  keeps the loop stable if the calibration is off by a factor of 20 - an
  uncorrected wrong gain limit-cycles at the cap, which swings the crosshair
  across the target and is obvious to anyone watching.
- **Deadzone.** Inside 0.33° the correction is smaller than one point of travel
  and is skipped. That is what stops the crosshair from vibrating on target — a
  jitter signature is easier to detect than a slow approach.
- **Rate limit.** Aim is applied at 20 Hz maximum, not 60.
- **Auto-release.** The synthetic finger lifts after 0.35 s of no target
  (`+tick`). A finger that never lifts is the most obvious signature there is.
- **FOV bound.** Only targets within `aim.fov` pixels of the crosshair are
  considered, so the aim never swings across the screen.
- **Input-layer actuation.** The aim is delivered as an HID touch event
  (`Core/Inject.m`), i.e. the same path a real finger takes. It goes through the
  game's own touch handling and its own camera code, so no impossible rotation,
  no impossible state, nothing the movement validator can see.

Set `aim.smooth` high (8–12) and `aim.fov` low (60–120) for the least visible
result. That is a real trade-off between effectiveness and risk, and the menu
puts both in your hands on purpose.

The one thing this cannot hide: a calibration run swings the camera by about a
screen-width. Do it once, in a corner of the map, not in the middle of a fight.

### What the ESP does not do

The ESP is a local overlay window. It reads object memory and draws. It sends
nothing, changes nothing, and produces no packet. Its only footprint is a
`UIWindow` in your own process, and optionally the secure-layer screenshot
exclusion (`ui.hide_from_capture`).

---

## 3. Detection-shaped things that are *deliberately* left alone

| thing | why |
|---|---|
| IL2CPP metadata | never touched; `global-metadata.dat` is byte-identical |
| game assets / resources | never touched |
| network payloads | the reader is local-only. The proxy exists for config/menu only |
| `probe-anticheat` rule | present but disabled. Read the comment in `rules/freefire.yaml` |
| any hook of game functions | none. We only read fields and invoke library icalls |

---

## 4. Residual risk, ranked

1. **Server-side behavioural analysis.** Highest. Mitigated by never doing
   movement/damage edits and by smoothing the aim; not eliminated.
2. **Signing identity mismatch** (§1.1). Inherent to re-signing. Not fixable
   from inside the process.
3. **App Attest** (§1.7), if used. Inherent. Not fixable at all.
4. **Executable hash** (§1.2). The `FreeFire` shell changes. Asset hashes do not.
5. **dyld checks that bypass the symbol-pointer path** (§1.3). Would need an
   inline hook engine; we intentionally do not vendor one.
6. **Human review.** Reports + replays. Nothing technical addresses this.

---

## 5. Pre-flight checklist

The workflow does all of this on every build and fails the run if any of it is
wrong - so this list is what to do by hand only when you are building locally.

```bash
# 1. the game files are untouched, and the injection is where you think it is
python tools/ipa_probe.py out/ff-final.ipa

# 2. the runtime the dylib needs is really exported by this build
python tools/il2cpp_symbols.py in.ipa --require

# 3. the dylib does not announce itself
make -C native RELEASE=1
strings -a native/.theos/obj/arm64/FFMenu.dylib | grep -Ei 'ffmenu|cheat|aimbot|bypass'   # expect nothing
strings -a native/.theos/obj/arm64/FFMenu.dylib | grep -E 'esp\.|aim\.|debug\.|ui\.'      # expect nothing
nm -u  native/.theos/obj/arm64/FFMenu.dylib                                                 # system only

# 4. it knows where the symbol slots are
python tools/got_scan.py in.ipa
```

In the menu:

- `Verbose Logs` **off** (silent build behaviour)
- `Hide From Recording` **on**
- `aim.smooth` **>= 8**, `aim.fov` **<= 120**
- `net.drop_pct` **0** — a client that drops packets looks like a broken client,
  and a broken client is investigated
- `Quiet Mode` on until you are actually in a match

First run: test with a throwaway account. There is no substitute for that and no
document can replace it.

---

## 6. What would raise the bar further

Things that are real, and not done here because they need something this build
does not have:

1. **An inline hook engine (Dobby or equivalent)** — would cover dyld checks
   that bypass symbol pointers, and `sysctl`-based debugger checks. Costs a
   vendored dependency and a much larger detection surface in the binary.
2. **Server-side observation** — capturing the client's own traffic for a week
   and watching for quarantine signals is the only way to measure the real risk.
   That is what the proxy can do, and `debug.capture_bodies` is there for it.
3. **A per-session behaviour profile** — logging your own shot timing and
   comparing it against a human distribution. If your aimbot is smooth but your
   *reaction time* is 60 ms every time, the input layer was never the problem.
