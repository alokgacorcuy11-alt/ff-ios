# USAGE — what it does, and how to actually run it

## Part 0 — the App Store IPA problem (read this first)

You asked about **Free Fire from the App Store**. Here is the exact situation,
because it decides everything:

The App Store IPA is **FairPlay-encrypted**. Its `__TEXT` is ciphertext, and the
decryption key only exists inside the iOS kernel, only for a device that
installed it from Apple with Apple's own signature.

Check it:

```bash
unzip -q FreeFire.ipa -d x
otool -l x/Payload/FreeFire.app/FreeFire | grep -A4 LC_ENCRYPTION_INFO
#  cryptoff  ...
#  cryptsize ...
#  cryptid  1        <- 1 = encrypted, 0 = decryptable payload
```

Or without macOS/Xcode, with the tool in this repo:

```bash
python tools/ipa_probe.py FreeFire.ipa
```

It parses the Mach-O load commands directly and prints the verdict:

```
 cryptid     : 1   -> still App Store encrypted
 -- no injected load commands
 VERDICT: ENCRYPTED (App Store / FairPlay) - payload cannot be patched  |  load commands are original
```

Run it on **any** IPA you find being sold as a "proxy cheat" and it will show
you the thing nobody mentions:

```
 !! INJECTED LOAD COMMANDS (loose dylib resolved in bundle)
      [arm64]  LC_LOAD_DYLIB  @executable_path/Frameworks/libSomething.dylib
 VERDICT: DECRYPTED  |  MODIFIED - injected dylib load command(s): libSomething.dylib
```

That line is the whole answer. Their "official Free Fire with a menu" is a
decrypted official build with a dylib stapled on. `ipa_probe.py` is how you
confirm it yourself instead of trusting anyone, including me.

Consequences, in order:

| Thing | Verdict |
|---|---|
| Patch the App Store IPA directly | **No.** `cryptid 1`; you cannot re-sign a FairPlay binary and still get the kernel to decrypt it |
| Add `LC_LOAD_DYLIB` to the encrypted header anyway | The header is plaintext so the command lands, but the app is still Apple-signed, so you cannot install your modified copy |
| Decrypt it on a device first | **Yes — this is the only path.** It is what every cheat seller does |
| Then re-sign with your cert / TrollStore | **Yes** |

So "Free Fire resmi dengan menu" always means: **the official IPA, decrypted,
one load command added, re-signed.** Same bundle, same login, same account,
same servers. Only difference is the extra image.

Decrypt on a jailbroken device:

```bash
frida-ios-dump -H 192.168.1.50 com.dts.freefireth     # needs Frida + frida-ios-dump
bagbak com.dts.freefireth                              # needs jailbreak, rootless ok
# output: com.dts.freefireth-decrypted.ipa
```

Then `tools/build_ipa.sh` does the rest and refuses to run while `cryptid != 0`.

### One entitlement that matters more than the rest

Your injected dylib only gets mapped if the main binary carries
**`get-task-allow`**. That is the switch that tells `dyld` "this process may hold
its own debug/injection rights". A normal App Store binary does not have it.

`build_ipa.sh` does **not** replace the entitlements — it extracts the original
ones with `ldid -e`, adds `get-task-allow` on top, and signs with the merged set.
Replacing them wholesale breaks keychain, some SDKs, and anything bound to the
Team ID.

Related and worth knowing: Apple **App Attest** produces assertions bound to the
original Team ID + bundle signature. Re-signing changes that signature, so an
attestation made by your build will not verify against Garena's records.
Whether Free Fire hard-gates on it is build-specific — **`[perlu verifikasi]`**,
and here is how you check instead of guessing:

```bash
# is the DeviceCheck / App Attest class even called?
frida -U -f com.dts.freefireth -l - <<'EOF'
try {
  const S = ObjC.classes.DCAppAttestService;
  console.log('DCAppAttestService present:', !!S);
  if (S) Interceptor.attach(S['- generateAssertion:clientDataHash:completionHandler:'].implementation,
    { onEnter: a => { console.log('generateAssertion called'); console.log(a[3]); } });
} catch (e) { console.log('no DCAppAttestService'); }
EOF
```

If nothing prints during login, FF is not using it on this build and the
question is moot. Observed behaviour in the wild is that sideloaded builds log in
and play, which means there is a non-attested path — verify on your own build
with the probe above.

## Part 1 — the full feature list

Two layers, one panel. Native = in-process, real. Proxy = network, real.

### Native layer (only after the modded IPA is installed)

| Feature | Key | What it does in a match |
|---|---|---|
| **ESP — Enable** | `esp.enabled` | starts the overlay window |
| Box | `esp.box` | cornered bounding box, scaled by real projected depth |
| Snapline | `esp.line` | line from crosshair to each target |
| Name | `esp.name` | nickname above the box |
| Health Bar | `esp.hp` | vertical bar, live `hp/maxHp` |
| Distance | `esp.distance` | metres, from world positions |
| FOV Circle | `esp.fov_circle` | shows the aimbot radius |
| Max Distance | `esp.max_dist` | culls anything past 10–800 m |
| **Aimbot — Enable** | `aim.enabled` | 20 Hz control loop, nearest target to the crosshair |
| Smooth | `aim.smooth` | frames taken to close the gap (2 = fast, 20 = slow). **Leave it 4+** |
| FOV | `aim.fov` | pixel radius from screen centre (10–400) |
| Target Bone | `aim.bone` | `head` or `chest` (height offset 1.55 / 1.20 m) |
| On Screen Only | `aim.visible_only` | skips targets behind the camera |
| Hide From Recording | `ui.hide_from_capture` | masks the overlay layer with a secure field |

**The aimbot needs one measurement before it will move at all.** Press
`CALIBRATE` in the panel footer while you are in a match, standing somewhere
open. The view swings right, then up, and comes back; that is the measurement.
It writes two numbers (`cal.gain_x`, `cal.gain_y` — radians of camera rotation
per point of finger travel, with sign) and from then on the aim knows how far to
move for a given angle. They persist, so you calibrate once per device, and
again only if you change in-game sensitivity.

The measurement is a **starting point, not a fixed constant**. A 48-point drag
can trigger the game's look-acceleration, which makes the measured gain larger
than the small-signal gain that a few points of fine correction actually use. So
every frame that the aim applies a correction, it compares the camera rotation
it got with the one it asked for and nudges the gain (25% toward the observed
value). Estimates that contradict the measured sign, or that come from a frame
where nothing measurably moved, are discarded. In practice the aim gets more
accurate over the first second of use rather than less.

Without that refinement a gain that is 20× off does not merely aim badly: the
per-frame cap turns it into a limit cycle, so the crosshair swings back and
forth across the target and never settles. `tools/tests/test_math.py` simulates
both cases and fails if the refinement is removed.

Without that measurement the aimbot stays off and `DIAGNOSTICS` says
`targeting: NOT CALIBRATED`. That is deliberate: rotation-per-point depends on
the player's sensitivity, the screen size and whether look-Y is inverted, so a
guessed value does not aim — it spins the camera past the target.

Notes that matter:

- ESP culls **teammates** (`team_field`), dead players (`hp <= 0`) and the local
  player. If `team_field` is wrong in your map you will see teammates boxed —
  that is the symptom, and the fix is one JSON value.
- The aimbot drives a **synthetic finger** through IOKit's HID event system, the
  same path backboardd uses for a real touch. Nothing in the network layer or in
  movement validation can tell it apart from a thumb, because it *is* a touch
  stream. What is left is behavioural: the loop closes the angular error over
  `aim.smooth` frames at 20 Hz, caps a single correction at 24 points, stops
  inside a 0.33° deadzone, and lifts the finger after 0.35 s without a target.
- Aim is computed from the **angle** between camera forward and the target, so
  it does not depend on `worldToScreen` being correct. It works even while the
  projection path is still falling back between `icall` and `matrix`.
- The two tail fields of the digitizer page (`IsDisplayCoordinates`,
  `IsIntegrated`) moved between iOS versions, so they are resolved at runtime by
  probing a throwaway event instead of being hardcoded - see `Core/Inject.m`.
  `DIAGNOSTICS` prints the mode it settled on (`space=points` /
  `space=pixels`). If taps land in the wrong place, that line is the first thing
  to read.
- Box size comes from `WorldToScreenPoint` depth, not from a fixed pixel guess,
  so it stays correct at any distance.

### Proxy layer (works without the modded IPA, on stock Free Fire)

| Feature | What it does |
|---|---|
| Hijack Webview | rewrites in-game notice/event URLs → the panel renders **inside** Free Fire |
| Config Overrides | gates your hand-written `rewrite_json` / `pb_set` rules |
| Suppress Maintenance | flips maintenance/closed keys to open in any JSON reply |
| Mute Notices | blanks notice/announcement/banner payloads |
| Force Region | overwrites `region` / `reg` / `country` |
| Block Telemetry | 204s analytics/metrics/crash-report hosts |
| Block Ads | 204s ad-network hosts |
| Added Latency | 0–2000 ms delay on game traffic |
| Packet Drop | random 504s (sharp tool) |
| Quiet Mode | kill switch — touch nothing |

### Not implemented, and what each would actually need

So you know the difference between "not built yet" and "impossible":

| Feature | Needs |
|---|---|
| Triggerbot | a hook on the fire/attack method + a raycast or a screen-centre target test. Straightforward next step, ~60 lines |
| Fast fire / no recoil | writing the weapon spread/recoil fields, or hooking the fire-rate method. Needs `dumpFields` on the weapon class |
| Speed / teleport | writing movement fields. **Server validates position** — you will rubberband. Not a code problem, a server-authority problem |
| Wallhack (through geometry) | ESP already draws through walls — the overlay has no depth test. "Wallhack" and "ESP" are the same thing here |
| Bone ESP / skeleton | needs a bone-transform chain per player; `dumpFields` on the character class for the bone array |
| Aimbot to specific enemy | needs a target-pick UI; the engine side already exists |
| Damage / godmode / currency | server-authoritative. Not reachable from the client, at all |

## Part 2 — how to use it

### Once per device

```bash
# 1. build the dylib (macOS + Theos)
cd native && make
# -> .theos/obj/arm64/FFMenu.dylib

# 2. inject + sign the decrypted IPA
cd ..
./tools/build_ipa.sh /path/com.dts.freefireth-decrypted.ipa \
                     native/.theos/obj/arm64/FFMenu.dylib \
                     /path/cert.p12 /path/profile.mobileprovision
# -> out/com.dts.freefireth-ffmenu-<date>.ipa

# 3. install
#    TrollStore  : open the .ipa on device -> Install (permanent)
#    AltStore    : 7-day resign loop
#    jailbroken  : unzip into the app container + uicache
```

### Once per game patch

Every FF update changes class and field names. **You do not rebuild the IPA.**

```bash
# 1. re-hunt the names
frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js --no-pause
#    findClasses("player")   dumpFields("Player")   findW2S()   findPlayerContainers()

# 2. write them into the map the proxy serves
notepad state\ff_native.json   # OPTIONAL - the dylib self-resolves          # or your editor
#    check it:  curl http://127.0.0.1:8082/api/native

# 3. relaunch FF. The dylib pulls it within 30s and logs:
#    [ffmenu] schema refreshed from proxy
#    [ffmenu] game bound
```

### In a match

1. A lime **FF** orb sits on the left. Drag it anywhere, tap to open.
2. Sections: **AIMBOT**, **ESP**, **SYSTEM**. Toggle, slide, done. Nothing needs
   a restart — the ESP window starts the moment you flip `Enable`.
3. Every change is saved to `NSUserDefaults` immediately. `SAVE` just forces it.
4. **DIAGNOSTICS** opens a dump of what actually resolved:

```
il2cpp:ok  container:ok  cam:ok  w2s:ok  local:ok  elem:Player
```

`PARTIAL` plus a `!` line telling you which key is wrong is the normal state
while you are filling in the map. `elem:-` means no player object has been seen
yet, i.e. you are not in a match.

### Recommended first settings

```
ESP -> Enable          on
ESP -> Box             on
ESP -> Name/Distance   on
ESP -> Max Distance    200
Aimbot -> Enable       OFF until ESP is confirmed working
Aimbot -> Smooth       6
Aimbot -> FOV          80
Aimbot -> Bone         chest
```

Confirm ESP draws boxes on real players **before** enabling the aimbot. If boxes
land on teammates, `team_field` is wrong. If boxes sit at the wrong height,
`head_offset`/`chest_offset` need tuning. If nothing draws at all, `worldToScreen`
or `position_method` is wrong — DIAGNOSTICS tells you which.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No orb | dylib not mapped | Console.app → filter `ffmenu` → `dylib loaded (pid N)`. Nothing = entitlement/sign issue |
| `missing il2cpp symbol` | wrong `image` | some builds keep il2cpp in the main binary: set `image` to the app binary name |
| `bind PARTIAL` | a name in the map is wrong | the log names the exact field/class |
| Orb appears, toggles do nothing | schema never filled | `state/ff_native.json` still has `"??"` |
| No boxes, DIAGNOSTICS all ok | not in a match, or positions wrong | check `position_method`; try `get_position` vs `get_worldPosition` |
| Boxes on teammates | `team_field` wrong | re-run `dumpFields` on the element class |
| Boxes huge/tiny | depth wrong | `camera.boxed_value_offset` off, or the w2s method returns world units not pixels |
| Empty names | string offsets off | tune `string_len_offset` / `string_data_offset` |
| Garbage HP | hp field type is not float | `dumpFields` shows the type; int32 needs a different read |

## Part 3 — the detection picture, plainly

Client-side tells, structural and not removable:

- a new image in the process (`libFFMenu.dylib`)
- `get-task-allow` present on a binary that should not have it
- a raised-level `UIWindow` the app can enumerate

Runtime tells you control:

- rotation written at a fixed rate with no human jitter → **`aim.smooth` high, `aim.fov` narrow**
- 100% headshot rate over a session → **use `chest`, not `head`**
- acquiring targets across the map instantly → **keep `esp.max_dist` low so aim never reaches far targets**
- never missing → aim assist, not aimbot. `smooth 8-12` and `fov 60-90` reads as "good player" instead of "software"

Server-side is what actually gets accounts, and it is behavioural, not
signature-based. Nothing in `native/` can fix that from the client. Set your own
risk tolerance and keep it on your own account.
