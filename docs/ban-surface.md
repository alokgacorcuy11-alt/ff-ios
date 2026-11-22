# the device-ban surface

Written because the question "can this get the buyer's phone banned" has been
answered with confidence four times in this project and never once with evidence.
Everything below is measured from the shipped binaries of Free Fire 1.132.1.
Nothing here is inferred from how anti-cheat is usually built.

## what was measured

| file | size | unique strings (>= 5 chars) | imported symbols |
|---|---|---|---|
| `libanogs.so` (ACE) | 5,537,616 B | **7,626** | 216 |
| `libanort.so` (ACE runtime) | 1,692,040 B | **1,759** | 145 |

Two beliefs died here.

**"The ACE libraries are heavily obfuscated with near-zero strings."** False.
7,626 unique strings in `libanogs.so`. What is obfuscated is the *interesting*
subset: the sample contains `ir"ekphc(}bjx-w`e1saq5dbvwsu{=wq`, which is
ciphertext for something. So the file is not a wall of noise and it is not a
plain string table either; the names that matter are decrypted at runtime.

**"A static read tells you which device identifiers are collected."** False, for
the same reason. Searching for `ANDROID_ID`, `getImei`, `getSubscriberId`,
`ro.serialno`, `/proc/cpuinfo`, `MediaDrm`, `deviceUniqueId` finds **none of
them** in `libanogs.so`. They are there, encrypted, and decrypted when needed.

## what the imports do say, and why that is enough

Obfuscation cannot rename imports: the dynamic linker resolves them by name at
load time. So the import table is a reliable statement of capability.

`libanogs.so` imports:

```
__system_property_get      __system_property_find_nth   __system_property_read
open   __open_2   __openat_2   fopen   fdopen   opendir   fstat   fstatat
popen                      dlopen                      mmap
ioctl                      ptrace                      pthread_create
__android_log_print
```

`libanort.so` adds:

```
syscall                    readlink                    readdir
socket                     pread                       stat
```

Five conclusions follow, in order of how much they matter.

**1. It reads system properties.** `__system_property_get` is `getprop`. Build
identity - `ro.serialno`, `ro.product.model`, `ro.build.fingerprint` - is
reachable this way, with the key names decrypted in memory.

**2. It reads files, and does not need to know the path at build time.** `open`,
`openat`, `opendir`, `readdir`, `fstatat` are enough to walk `/proc` and `/sys`
and find what it wants. This is why the paths are absent from the string table
and why "just find the path in the binary" will never work.

**3. It can run a command.** `popen`. A shell is available to it.

**4. It checks for a debugger.** `ptrace` is imported by both.

**5. It can issue raw syscalls.** `syscall` appears in `libanort.so`, and this is
the one that decides the whole design. Any hook placed on a libc function is
bypassed by a direct `svc` instruction. PLT and GOT hooking - which is what the
container's Dobby layer does - cannot see it.

## the game keeps its own identity, and it is not on the client

The metadata's own vocabulary says what the game collects:

```
 97  UniqueID          55  UniqueId
 25  DeviceID          26  DeviceId
 14  SystemInfo         7  DeviceInfo      7  DeviceModel
  1  getAdvertisingId   2  deviceUniqueId
```

`getAdvertisingId` is the Google advertising ID. `deviceUniqueId` is the string
a `MediaDrm` instance returns for `deviceUniqueId` - a hardware-backed value
that survives reinstall and is unique per device, issued by the DRM service over
binder.

The game derives a fingerprint from these, and then **sends it to Garena**. The
record of which device belongs to which account lives on a server that no
client-side change can reach.

## what can be covered, and what cannot

| identity source | read by | coverable from a container | how |
|---|---|---|---|
| `Settings.Secure.ANDROID_ID` | Java | yes | container returns its own value for the guest |
| `Build.*` static fields | Java | yes | container sets them for the guest |
| `TelephonyManager` ids | Java | yes | already null on Android 10+; container returns the spoof |
| advertising ID | Java | yes | container returns a random id |
| `ro.*` properties | native, via `__system_property_get` | only if PLT-hooked | Dobby on the guest's libc |
| `/proc/*`, `/sys/*` paths | native, via `open`/`opendir` | only if PLT-hooked | Dobby on `open`/`openat`/`opendir` |
| MAC via `ioctl` | native | only if PLT-hooked | Dobby on `ioctl` |
| anything via `syscall` | native, direct | **no** | nothing userspace can do |
| `MediaDrm deviceUniqueId` | binder to the DRM service | **no** | hardware-backed, per device |
| Play Integrity / hardware attestation | binder to Play services | **no** | by design |

The last three rows are the answer. **There is no client-side change that can
hide the device from a native component that issues raw syscalls and can ask the
DRM service for a hardware identifier.**

## what this means for the risk, concretely

A device ban is not the first punishment. The escalation is:

```
detected  ->  account action
detected again  ->  longer account action
detected repeatedly, same device  ->  the device is recorded and blocked
```

So the device-ban risk is driven by **how often the same device is flagged**, not
by whether any single identifier can be hidden. That reorders the work:

| what actually reduces device-ban risk | why |
|---|---|
| **Read-only operation** | the cheat never writes to game memory, so nothing the server computes disagrees with itself. No rollback, no impossible state, no anomaly to escalate on. Already true of this build. |
| **A disposable account per buyer** | the ban lands on the account. It costs nothing and it is the largest single reduction available. |
| **Container-held identity** | the game's locally stored fingerprint lives in the container's own data. Wiping it changes what the game sees. |
| **Java-layer spoofing** | covers the identifiers the game reads through the framework. |
| **Native PLT hooking** | covers the identifiers ACE reads through libc - real, and defeated by `syscall`. |
| **Manual-mapped payload** | removes the library path from `/proc/self/maps`, which is a *detection* signal rather than an identity one. |

## the honest verdict

A build that cannot be detected at all does not exist, and a build that
guarantees no device ban does not exist either, for a reason that has nothing to
do with how good the code is: the punishment is decided by a server holding a
record the client cannot alter.

What can be honestly promised, and what the design above delivers:

1. Nothing is written to the game, so nothing the server validates can disagree.
2. The payload has no path in `maps` once manual mapping lands.
3. The identity the game can see is the container's, not the phone's, for
   everything except the three hardware-backed sources.
4. The account is disposable, so a detection costs an account and not a device.

What cannot be promised, stated plainly so it is never mistaken for a guarantee:
`syscall`, `MediaDrm`, and hardware attestation are out of reach, and a
determined anti-cheat that decides to escalate on those will escalate.
