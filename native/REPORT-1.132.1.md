# REPORT — com.dts.freefireth 1.132.1 (metadata v31)

Everything below is measured from the IPA you provided. Reproduce with:

```bash
python tools/ipa_probe.py          com.dts.freefireth_1.132.1_und3fined.ipa
python tools/metadata_hunt.py      com.dts.freefireth_1.132.1_und3fined.ipa
python tools/metadata_structure.py com.dts.freefireth_1.132.1_und3fined.ipa --out out/ff_native.proposed.json
```

---

## 1. The IPA itself

| | |
|---|---|
| bundle | `com.dts.freefireth` |
| version | 1.132.1 (2019121207) |
| executable | `FreeFire` |
| arch | arm64 |
| **min iOS** | **12.0** — exactly our target, nothing to change |
| cryptid | **0** — fully decrypted, patchable |
| injected libs | none (clean) |
| size | 510.4 MB |
| frameworks | 2 (`UnityFramework` + one more) |

Verdict: usable as-is for injection. `tools/package_ipa.py` accepts it.

---

## 2. Metadata

| | |
|---|---|
| file | `Payload/FreeFire.app/Data/Managed/Metadata/global-metadata.dat` |
| size | 56.6 MB |
| **version** | **31** (Unity 2021/2022 era) |
| strings | 576,099 (395,645 identifiers) |
| typeDefinitions | 43,417 records × 88 B |
| fields | 474,210 records × 12 B |
| classes mapped | 34,509 of 43,417 type records — see the note below |

Both sections were located **without any hardcoded offset**, by validating that
every record ends in an ECMA-335 token: `0x02` TypeDef, `0x04` Field. Token match
100% on both, over 400 sampled records.

---

## 3. Layout proof

This is what makes the class→field mapping trustworthy rather than merely
plausible. The record size is 88 bytes and the solver had to pick
`fieldStart`/`field_count` out of ~500 candidate slot pairs. Three independent
checks agree on `+32`/`+68`:

| Check | (+32, +68) | runner-up (+56, +74) |
|---|---|---|
| Coverage `max(fs+fc) / fieldCount` | **1.000** | 1.000 |
| Violations `fs+fc > fieldCount` | **0** | **18,174** |
| First type owning fields starts at | **0** | 0 |
| Adjacent-type contiguity | 76.0% | 75.1% |

The runner-up is `vtableStart`/`vtable_count`. It also looks contiguous and dies
only on the field-array overflow test — which is why coverage alone was not
enough.

Whole-map proof:

```
sum(field_count) over 43417 types = 474210   field array = 474210   delta = 0
overlapping ranges: 0   first start: 0   last end: 474210
UnityEngine.Vector3 fields=15  x/y/z=['x','y','z']
```

**Every field belongs to exactly one type, all 474,210 covered, zero overlaps.**

---

## 4. The finding that changes the plan

`COW.GamePlay.*` — the in-match gameplay layer — has its member names
**randomised**.

| namespace | fields | obfuscated | % |
|---|---|---|---|
| **COW.GamePlay.\*** | 26,590 | 15,311 | **57.6%** |
| COW.UI\* | 105,538 | 677 | 0.6% |
| tcp.\* | 3,488 | 0 | 0.0% |
| proto.\* | 18,003 | 172 | 1.0% |
| UnityEngine.\* | 8,921 | 17 | 0.2% |
| COW.\* (not UI / not GamePlay) | 33,909 | 61 | 0.2% |
| all | 265,853 | 28,229 | 10.6% |

Straight out of the map:

```
COW.GamePlay.BaseLevelObject  [37]
  ModelResName, EKFKPBIMDGO, NGHDCENECNO, JLLJJBEDLJM, BHPEDHABEPD,
  IBAJCLLJJIP, DPJMJDBBDKM, ISVISIBLE_CUSTOM4, ISVISIBLE_CUSTOM3, ...

COW.GamePlay.LevelHealRobot  [10]
  KJHLKDEABFD, CNJEMJMOKDA, GMKCNELKFBG, CKJNKHICMHL, BLKEIIOPJBI,
  LKDPOEOLBNB, CDDOIGLOMDD, m_Hp, BDOEGBMMEOK, OHMKIKHOGHH
```

`m_Hp` is readable only because Unity serialises it. Everything
gameplay-critical is `[A-Z]{11}` noise.

### Consequences

1. **Name matching dead-ends.** Searching for `players` / `hp` / `team` finds UI
   controllers and network message classes, not the entity holding a player's
   health. The auto-drafted map from names picked
   `proto.APIGetRoomPlayerPresetByRoomIDRes` — a room API response — as the
   "container". Confident and wrong.

2. **Obfuscated names are re-randomised every patch.** `EKFKPBIMDGO` in 1.132.1
   becomes a different string in 1.133.0. So a name-keyed map is correct **per
   build**. This is why the dylib pulls `ff_native.json` over HTTP from
   `/api/native` instead of compiling it in — that decision is now load-bearing.

3. **The right class must be identified by shape, not name.** A player entity is
   `{ Vector3, float, int, string, List<...> }` regardless of what the members
   are called. Shape survives re-obfuscation.

---

## 5. Offline type resolution: attempted, blocked, and why I stopped

The plan was to resolve field **types** offline from the binary's
`Il2CppMetadataRegistration.types`. That was attempted and it did not succeed on
v31. Recording the evidence rather than papering over it:

| Attempt | Result |
|---|---|
| Scan every file-backed segment for runs of pointers to `Il2CppType` | longest run 26,207 — too short to be `types` |
| Relax the validity test and rescan | 125,349 type-like objects exist in `__DATA`, but no pointer array of that length exists anywhere |
| Ground truth: `types[td.byvalTypeIndex]` must be CLASS/VALUETYPE with `klassIndex == td.index` | **0%** on every candidate run |
| Solve `object_offset = base + value*K`, 8 strides × 20 int32 slots × 603 sampled pairs | **no consistent mapping** |

So the `Il2CppType` layout and/or the `byvalTypeIndex` slot for v31 is not what
the v24–v27 documentation describes. 125k objects that merely *look* like types
is exactly the trap that produces a confident, wrong answer.

`tools/type_hunt.py` therefore **refuses and says so** instead of emitting a
plausible table. Deliberate: an offset map built on a wrong type table gives an
ESP that draws boxes around UI widgets, and that failure would otherwise ship
silently.

---

## 6. The unlock: all 256 il2cpp_* symbols are exported

This is the finding that made the whole plan work, and it arrived after §5.

`tools/il2cpp_symbols.py` walks the UnityFramework symbol table:

```
il2cpp_* symbols in the symbol table : 256
defined + external (dlsym-visible)   : 256
    256  __TEXT,__text
required by IL2CPP.m : 22/22 present
optional             : 32/32 present
```

Every one is `N_SECT` + external, i.e. **dlsym finds it at runtime**. That means
the dylib does not need an offline type table at all: it asks the live runtime
for the type of every field, and the runtime answers exactly.

```bash
python tools/il2cpp_symbols.py <ipa> --require      # exit 1 if anything is missing
```

So §5 is not a dead end — it became unnecessary. The offline `Il2CppType` table
was a way to *guess* what the runtime knows for certain.

---

## 7. What the dylib does with that

`Core/Resolve.m` — `[FFResolve autoMap]` — walks the live object model and
produces a schema. No file, no device dump, no offsets:

| step | how | rate |
|---|---|---|
| find the player class | `il2cpp_class_from_name(img, "COW.GamePlay", "Player")` — class names are **not** obfuscated | exact |
| nickname / hp / team / bot | field **type** + the readable minority of names (`OriginalNickName`, `TeamModeID`, `IsClientBot`, `MainCameraTransform`) | exact |
| camera transform | `UnityEngine.Transform`-typed field on the player, picked by name | exact |
| position | every `Vector3` on the player, then **calibrated at runtime**: the one that stays 0.3–8 m from its own camera transform over 45 frames | measured |
| player collection | scan every non-UI class for a field whose type contains `COW.GamePlay.Player` and is `List<…>` or `Player[]` | exact |
| singleton | a static field whose type is that container class | exact |
| `List<T>` internals | inflate the field's type via `il2cpp_class_from_type`, then `_items` / `_size` by name | exact |
| array layout | `il2cpp_offset_of_array_length_in_array_object_header()` — asked, not assumed | exact |
| string layout | probe: create `"FFPROBE"` via `il2cpp_string_new`, find where its chars land | measured |
| world-to-screen | `il2cpp_resolve_icall("UnityEngine.Camera::WorldToScreenPoint_Injected")`, with a matrix fallback built from the camera transform | probed |

At runtime that prints one line per decision, and the DIAGNOSTICS panel shows
the result. `tools/il2cpp_hunt.js` still exists for Frida-side exploration, but
it is no longer required to make the cheat work.

---

## 8. Files produced

| file | contents |
|---|---|
| `out/classes.json` | 34,509 of 43,417 classes → field names, 5.9 MB. Partial by construction — see the note below |
| `out/classes.stats.json` | what the map above leaves out, so absence can never be read as "empty" |
| `out/metadata_strings.txt` | all 576,099 strings, sorted, 16.6 MB |
| `out/metadata_suggest.json` | ranked string-table candidates per `ff_native.json` key |
| `out/ff_native.proposed.json` | draft map from names — **see §4, not the answer** |

`out/classes.json` is the useful one: every offline query from here on can be
answered against it without re-reading the 510 MB IPA. Example:

```bash
python -c "import json;c=json.load(open('out/classes.json'));print(c['COW.GamePlay.BaseLevelObject'])"
```

---

## 9. Bugs caught offline, before any of this shipped

Recorded because they are the reason the offline suites exist: none of these
would crash. All of them would have produced a cheat that quietly does the
wrong thing, and you would have blamed the offsets.

| bug | how it would have shown up | caught by |
|---|---|---|
| `quatFromMatrix` extracted the **inverse** rotation (cross terms had the wrong sign order) | on the matrix fallback path the ESP draws mirrored — looks exactly like a bad position offset | `tools/tests/test_math.py` (90° rotation invariants) |
| `findSym` mapped `__LINKEDIT` with `header + fileoff`, but this build has `vmaddr 0xc388000` vs `fileoff 0xbf44000` | il2cpp symbol lookup silently returns NULL on some builds | segment arithmetic, verified against the real binary |
| `WorldToScreenPoint` and `WorldToScreenPoint_Injected` have different calling conventions and were in one candidate list | struct returned in registers read as through a pointer → garbage coordinates, no crash | split into two groups that can never merge |
| a single probe at bind time | mode latched to `matrix` forever, because the camera does not exist until a match starts | `probeW2S` re-probes every 2 s, and the "camera missing" case no longer counts as a failure |
| `Menu/FFMenu.m` imported a header that did not exist; `setRaw:forKey:` was never declared; `features.json` lost an `items` key | compile failure / dead panel | `tools/tests/test_sources.py` |
| `ui.hide_from_capture` existed in the dylib but not in the panel | a switch that does nothing on one side | three-way key parity check |
| `DocumentsDir()` defined *below* its only caller in `FFState.m` | implicit declaration — a hard compile error on this toolchain | statics-defined-before-use audit |
| `rankByName` was declared as a block taking `NSArray *` and returning `int`, but its body returned a **block** | compile error, not a runtime bug | same audit, after reading the type aloud |
| `measureStringLayout` created the probe `"aA9z"` but searched for `'F','F','P','R'` | the loop could never match, so the Il2CppString layout silently stayed at its guessed defaults — every string read would be wrong | probe/pattern cross-check |
| the digitizer field table was off by one (`EventMask` written into `Index`, etc.) and `IsDisplayCoordinates` was hardcoded | the tap lands somewhere else, or nowhere; on iOS versions where the tail moved it is silently wrong. Now the tail is probed at runtime | constant-table audit + "display index is not hardcoded" |
| `tick:` returned before `[USModel tick]` when ESP was off | the aimbot died whenever you turned ESP off — the switch order looked like a feature bug | tick-ordering audit |
| the capture-mask `UITextField` had no frame (0×0 mask) | `Hide From Recording` hid the entire overlay instead of just excluding it from capture | mask-frame check |
| `+[USOverlay shared]` built a `UIView` off the main thread when toggled from the proxy | undefined behaviour; on newer iOS a hard crash | main-thread check |
| `Rebind.m` walked the image list through the very GOT slots it was rewriting | indices drift mid-loop, images get skipped, so some slots stay unpatched | "caches the real dyld pointers" + "snapshots the image list" |
| `dlsym(RTLD_DEFAULT, "dyld_image_count")` — no leading underscore | returns NULL here, so every dyld shim reported **zero** images: the game's enumeration comes back empty, which is a louder tell than not hiding at all | `Core/Dyld.h` + "no bare dlsym" audit |
| `FFIsImagePointer` re-parsed every load command of every image on every call, ~1.7k times for the player class alone | startup stall; and it is the guard that stops a crash on `free()` | cached range table |
| `readStaticObject` called `field_static_get_value` without a NULL check | jump to address 0 if a symbol is missing on a future build | null-check audit |
| positions were read **before** calibration could swap the position field | first frames after calibration draw at the wrong place, which reads as a bad offset | re-read after `calibrate:` |
| the aimbot dragged the finger to the target's **projected screen point** | finger travel does not map to camera rotation by a fixed amount, so the crosshair overshoots and oscillates — the aimbot "works" and is useless | replaced with an angular control loop over a **measured** gain; `audit_aim()` fails the build if `worldToScreen` reappears inside `aimAt:` |

Run them yourself:

```bash
python tools/tests/run_all.py      # every suite, ~6 s, no device
```

### 9b. What the first real compile found that no offline check did

The suites above are static. The first successful CI build (GitHub Actions, Intel
macOS) found two things they could not, and both are worth knowing because they
are invisible on a Windows working copy:

| bug | why nothing offline caught it |
|---|---|
| `tools/package_ipa.py` appended the injected `LC_LOAD_DYLIB` **at the end of the load commands - i.e. after `LC_CODE_SIGNATURE`** | Apple requires `LC_CODE_SIGNATURE` to be the last command: the kernel takes the signature blob from it and covers everything before it, so a command after it can never be part of a valid signature and the process is killed at launch. The symptom is "the app crashes the instant you tap it, before the splash screen" - which reads as a bug in our own code and sends you debugging the wrong layer. The synthetic fixture had `LC_CODE_SIGNATURE` last, so the ordering test now pins it, and `ipa_probe.py` reports the violation on any payload. |
| `Menu/FFMenu.m`: `#define R(g,l,k,kind) ... r.kind=kind ...` expanded to `r.RowToggle=RowToggle` | the preprocessor replaces every occurrence of a parameter name, including the token after the dot. Nine errors at nine call sites, all reading "property 'RowToggle' not found on object of type 'USRow *'". Now guarded by `audit_macro_params()`, with self-tests that prove it can fail. |
| `Core/Game.m`: `int tried` was only read by `FFLOG` | `make RELEASE=1` compiles every `FFLOG` out, so the variable is write-only there — and Theos builds with `-Werror`, so it is a hard error rather than a warning. Now guarded by `tools/lint_logonly.py`, which found a second instance (`Inject.m`, `moved`) that is now part of the self-test's return value. |
| `Tweak.x`: the constructor called `[USCloak install]`, `_dyld_image_count`, `dladdr` and registered a UIKit notification observer **during dyld image initialisation** | at that point UIKit has no `UIApplication`, and dyld is still building the image list we exist to hide from - so we walked a list that was mid-construction and rewrote other images' GOT slots with `vm_protect` while the loader was about to fill them. The initialiser now only schedules; the work happens on the main queue once `UIApplication` exists, which is still before Unity starts. `audit_entry_point()` fails the build if it regresses. |
| `Core/IL2CPP.m`: the symbol resolver was declared `+ (BOOL)load` | any class method whose **selector** is `load`, whatever its signature or return type, is invoked by the Objective-C runtime from `load_images()` - during dyld image initialisation. That ran the whole `dlsym` + Mach-O symbol walk at the worst possible moment, and `symFromMachO` computed `strtab + n_strx` with only a `!= 0` check: one stale entry walked off `__LINKEDIT` into unmapped memory, so the process took a `SIGBUS` (`KERN_PROTECTION_FAILURE`) inside `load_images`, before the splash screen, with the game never reaching `main()`. The symptom is indistinguishable from a signing problem, which is why it cost a round trip. Renamed to `+loadAPI` and called from `FFBind`; the walk is now bounded by `strsize` and by the image's segment ranges. Guarded by `audit_load_selector()` and `audit_symtab_bounds()`. |
| `Core/FFState.m`: `- (void)load` | not a runtime hook (only `+load` is), but the name is a trap for the next reader - it is now `loadPersisted`, so no selector in the tree is called `load` except the key decode. |
| `Menu/FFMenu.m`: the menu was a plain `UIWindow` hosting a plain `UIViewController` view | `-[UIView hitTest:withEvent:]` returns the view **itself** when no subview matches, and `UIWindow` does the same - so a fully transparent, full-screen window is still the touch target for every tap. The game kept rendering (it is only a window *below* ours) while nothing on screen responded: the app reached the login screen and then ignored every press, which looks like a broken app rather than an overlay bug. Both the window (`USPassthroughWindow`) and its root view (`USPassthroughView`) now return nil for a hit on themselves. `audit_passthrough()` guards it, including "no bare `UIWindow` is created in the menu". |

Two more that were infrastructure rather than code, recorded because both cost a
run to find and neither is guessable from the repository alone:- `macos-14` no longer resolves to a usable configuration on this account
  (`The configuration 'macos-14-arm64-us-default' is not supported`), and
  `macos-latest` resolves to an arm64 image that sat unassigned in the queue for
  15 minutes before being cancelled. `macos-15-intel` schedules immediately;
  building an arm64 iOS dylib on an x86_64 host is the classic Theos setup.
- `tools/fetch_ipa.sh` was committed without the executable bit. Windows cannot
  express that mode, so every local run passed and the runner answered
  `Permission denied` (exit 126) on every assertion. `audit_script_modes()` now
  asks git's index.

---

## 10. Status

Verified from your file, on this machine:

- IPA identified, decrypted, min iOS 12.0 — injection will work
- metadata v31 parsed; both sections located by token validation, 100% match
- `typeDefinitions` layout solved (+32 / +68), proven by perfect field tiling,
  zero overlaps and `UnityEngine.Vector3 == {x, y, z}`
- 34,509 classes / 474,210 fields mapped — and 8,908 type records that the map
  does not contain at all (18 above the 512-field cap, 4,484 with no instance
  fields, 4,406 lost to `namespace.name` collisions between generic
  instantiations). `out/classes.stats.json` states this, because a map that
  cannot say what it left out gets mistaken for a complete one. The player class
  is one of the 18.
- obfuscation measured per namespace: gameplay 57.6%, UI 0.6%, proto 1.0%
- **256/256 il2cpp symbols exported**; the 22 required and 32 optional ones the
  dylib resolves are all present and dlsym-visible
- **9 dyld symbol-pointer slots located**, lazy and non-lazy for tier-2 image hiding
- `COW.GamePlay.Player` confirmed at 1689 fields, 393 of them readable —
  including `OriginalNickName`, `TeamModeID`, `IsClientBot`,
  `MainCameraTransform`. That is the anchor the resolver uses.
  Its *field types* are the one thing that is not known offline (§5), which is
  why the resolver resolves types at runtime and why the camera-transform choice
  is confirmed by use rather than by name.

Still needs a device, and is honest about it:

- one run of the dylib against a live match to see `Resolve` pick the container
  and the singleton (the code logs every decision)
- the position-field calibration needs ~45 frames of a moving camera
- whether `WorldToScreenPoint_Injected` survives as an icall in-match — the code
  probes it, and falls back to the matrix path if it does not
- one `CALIBRATE` press in-match, to measure the rotation-per-point gain the aim
  needs. Until then `targeting:` reports `NOT CALIBRATED`, and the aim stays off
  rather than guess a sensitivity it cannot know.

Nothing on that list is an unknown any more. It is a first-run verification
list, not a research gap.