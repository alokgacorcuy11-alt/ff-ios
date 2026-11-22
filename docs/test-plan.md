# test plan

Five stages, in this order, because each one gates the next and the first three
need no cheat at all. Every stage states what to do, what a pass looks like, and
what a failure rules out - so a red result narrows the problem instead of just
being red.

Run the stages in order. Do not skip to the end: a stage that passes after the
one before it failed means the earlier failure was misread, and that costs more
time than it saves.

---

## stage 0 - already done, on this machine

| what | result | how it was checked |
|---|---|---|
| the game's classes and field offsets | 43,627 types, 475,641 fields, 336,329 methods, real offsets | `python tools/ffdump/ffdump.py --fields "..."` against the shipped metadata |
| the pointer tables in `libil2cpp.so` | rebuilt; 61,257 live words became 394,081, every pointer landing in a real section | `python tools/ffdump/sofix.py` |
| the offset header | generated, committed at `android/cheat/src/ff_offsets.h` | `ffdump.py --emit` |
| the cheat's core logic | 54 tests, 0 failed | `cmake -S android/cheat -B b -DFFCHEAT_HOST_TEST=ON && cmake --build b && ./b/test_core` |
| **the decoder against the real game files, on a real Android kernel** | **15 tests, 0 failed** - every field offset matches the Python dump | see `docs/evidence/real-decode-bluestacks.txt`; runs natively on x86_64, no ARM translation |
| the library builds for both ABIs | `libffcheat-arm64-v8a.so` 2,873,376 B, `libffcheat-armeabi-v7a.so` 2,202,032 B, 0 warnings | CI job `cheat`, artefacts on the `cheat` branch |
| the product's own suite | 774 checks, all pass | `python tools/tests/run_all.py` |

The real-data decode is worth describing separately, because it is the strongest
evidence in this project and it needed no phone and no game. It maps the shipped
`global-metadata.dat` and `libil2cpp.so` read-only at invented addresses and runs
the shipped decoder through them. Two independent readers - the Python dumper and
the C++ that goes into the cheat - agree on every number:

```
got=1 value=0xa8bf168                        the field-offset pointer
types=43627
COW.GamePlay.Player at type index 31821, 1,689 fields, 4,136 methods
m_GroundHitPos 7608   m_TransformObject 4744   TeamModeID 1076
TeamColorStr 2072     Speed 1376               AimCount 1356
m_AimAssist 1592
```

To run it:

```
# build (CI does this and publishes to the cheat branch)
cmake -S android/cheat -B build-real -DFFCHEAT_REAL_TEST=ON \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_SDK_ROOT/ndk/29.0.13846066/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=x86_64 -DANDROID_PLATFORM=android-26 -DCMAKE_BUILD_TYPE=Release
cmake --build build-real -j

adb push build-real/test_real /data/local/tmp/
adb push global-metadata.dat libil2cpp.so /data/local/tmp/
adb shell "cd /data/local/tmp && chmod 755 test_real && \
  ./test_real ./libil2cpp.so ./global-metadata.dat COW.GamePlay.Player Transform"
```

`libil2cpp.so` must be the one `sofix.py` wrote, not the one out of the APK: the
shipped file has its pointer tables as zeros because the linker fills them at
load time, and this test has no linker.

Pass is `15 passed, 0 failed`. A failure prints the value it read next to the
value it expected, so the difference says whether the offset moved by a patch or
the decoder is wrong.

Nothing in stage 0 needs a phone. It is listed so that a later failure is not
mistaken for something already proven.

---

## stage 1 - does Free Fire boot inside the container

**This is the real blocker and nothing downstream matters until it passes.**

The container is a virtual app: it starts the game itself, as its own child
process. Free Fire has refused to start on the OPPO A7 before - a permanent
black screen, twenty minutes. That has to be understood before any cheat code is
worth writing, because a game that does not start is a game with no memory to
read.

Do:

1. Install the container APK, open it, and let it initialise.
2. Add Free Fire inside the container (the "Installed App" path).
3. Tap it, and time how long the black screen lasts.

Pass: the game reaches its own loading screen within 60 seconds.

Fail, and what to do about it - in this order, cheapest first:

| symptom | most likely cause | next step |
|---|---|---|
| black screen forever, container never complains | the split APKs were not copied into the container's private directory | read the container's own log; `adb logcat` filtered to the container package |
| black screen, then the container closes | the native libraries were not extracted, so `System.loadLibrary` failed | check that the container's `lib/` contains the game's `.so` set; `libil2cpp.so` alone is 184 MB |
| game starts, then dies at the splash | the game's signature or package check refused the container's identity | this is the one that needs the spoof layer, not the IPC layer |
| starts once, then never again | the ROM froze or battery-limited the container | exclude the container from battery optimisation and enable autostart |

Free Fire is 400 MB across three splits. The copy step is where the failure
usually is, and the container's log says which file it choked on. That log is
worth more than any guess.

---

## stage 2 - does the cheat find the game

Prerequisite: the container reaches stage 1's pass.

The library prints what it found, using tag `ffcheat`. Build with the host from
this repo, run the game, then:

```
adb logcat -s ffcheat
```

Expected, in order:

```
il2cpp base 0x...
metadata base 0x... (scan 0x... 0x...)
types 43627
field offsets reachable: yes
Player type index: 31821
Il2CppClass: FOUND (scanned N bytes)
stealth: payload is file-backed ...
```

| line | what a wrong value means |
|---|---|
| `il2cpp base 0x0` | the library loaded before the game's, so `libil2cpp.so` is not mapped yet. Retry after the game reaches its loading screen. |
| `metadata base 0x0` | the metadata is not mapped as a read-only region of at least 1 MB, or its first eight bytes do not match `0xAF 1B B1 FA` followed by version 31. Dump the first 16 bytes of the region and compare. |
| `types` not 43627 | the layout constants in `ff_meta.h` do not match this game version. Regenerate with `ffdump.py --emit`. |
| `field offsets reachable: NO` | `Il2CppMetadataRegistration` was not at RVA `0xa5c7f60`. The offset tables were rebuilt by `sofix.py`, so a mismatch means the game version changed. |
| `Il2CppClass: not found` | the name address was not found in writable memory. Either the class is not loaded yet, or `Il2CppClass.name` points at a copy rather than into the metadata. Both are diagnosable: the scan reports how many bytes it covered. |
| scanned bytes is 0 | the search never ran - a bug, not a miss. |

This stage is the one that proves the whole chain at once: metadata located,
strings addressed, class found, namespace matched.

---

## stage 3 - the player list

The only piece with no implementation, because it cannot be found statically.
Which of `COW.GamePlay.Player`'s 1,689 fields holds the live roster is not
visible in a dump - a `List<T>` field is a pointer and a count, and no metadata
marks it.

How to find it, against a live match:

1. Load into the training ground. There is one player.
2. Walk every field of `Player` whose type is a managed object pointer.
3. For each, read one level down and look for the `System.Collections.Generic.List`
   shape: `_items` pointer, `_size` int, `_version` int, with `_size` in 1..64.
4. Record every candidate where `_size` equals the number of players in the
   match.
5. Go into a real match, and re-check the candidates at the start and at the end.
   The roster is non-empty before the first shot and empty after the last. A
   candidate that is always empty is a cache, not the list.

Pass: one field survives both checks, and its `_size` tracks the player count.

This is a search, not a guess, and the search is mechanical once a match is
running.

---

## stage 4 - does it draw

Prerequisite: stage 3.

Expected on the training ground, with a dummy in view: one box around the dummy,
tracking it as it moves, sized by distance, with the same box staying put when
the camera turns.

| symptom | likely cause |
|---|---|
| boxes in the right place but lagging | the position is read from `m_GroundHitPos`, which updates on the physics step. Switch to the transform object at `+0x1288`. |
| boxes mirrored horizontally | the view-projection matrix is read with the wrong transpose. |
| a column of boxes on one screen edge | projection is clamping off-screen points instead of rejecting them. `world_to_screen` returns false for a reason; check the caller. |
| boxes on teammates | `local_team` was not read, so every player compares against -1. |
| nothing at all | check stage 2 first. If the class was found, the positions are the next suspect: log the raw `Vec3` and see whether it is plausible. |

---

## stage 5 - the safety check, before anything is sold

The point of this stage is that the answer is measured rather than assumed.

During a session with the cheat running, collect:

```
adb shell cat /proc/<game pid>/status | grep TracerPid
adb shell cat /proc/<game pid>/maps   | grep -i ffcheat
```

| reading | meaning |
|---|---|
| `TracerPid: 0` | nothing is attached. Expected, and it is the point of running inside the process. |
| `maps` shows no path containing `ffcheat` | the payload is mapped anonymously. This is what manual mapping buys. |
| `maps` shows the path | expected for a file-backed `.so`. Not a failure of the cheat; a statement that this layer is not done. |

Then the account protocol, which is worth more than any of the above and costs
nothing:

1. Every session uses a **burner account**, never the buyer's main.
2. Before switching accounts, wipe the container's data for the game. The
   fingerprint the game stored locally goes with it.
3. Keep a written record of which account was used on which date. A pattern is
   only visible in the record.

If an account is actioned: stop, wipe, change account, and do not retry the same
build on the same device that day. The escalation to a device ban is driven by
repeated detections on one device - see `docs/ban-surface.md`, which is where the
reasoning behind that is written down and measured.

---

## what is being tested, in one line each

| stage | question | costs |
|---|---|---|
| 0 | is the static work real | already answered |
| 1 | does the game run inside the container | a phone, 10 minutes |
| 2 | does the cheat find the game | a phone with the library injected - but the decode half is already proven above |
| 3 | can the roster be found | one live match |
| 4 | does it draw | one live match |
| 5 | what is visible from outside | same session, no extra work |
