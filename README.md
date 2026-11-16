# ff-proxy — Free Fire (iOS) proxy interceptor

A real MITM server that runs on your laptop. Your iPhone routes through it over
WiFi, every Free Fire request/response passes through a rule engine, and you can
rewrite, stub, delay, or drop them live. Rules hot-reload — edit the YAML while
the game is running, no restart.

```
 iPhone ──WiFi──> laptop:8080 ──> garena / freefiremobile / ggblue ...
                      │
                      ├── rules/*.yaml           (rules engine, hot reload)
                      ├── state/state.json       (live toggles from the panel)
                      ├── logs/flows.jsonl       (full trace, every host+path)
                      ├── menu.ff                (control panel, served by the proxy)
                      └── laptop:8081            (serves the CA cert to Safari)

 Free Fire WebView ──> http://menu.ff/  ──> the panel renders inside the game
```

**Status of this build:** core verified end to end (see `tools/selftest.py` and
the *Verification* section). It runs, it intercepts, it rewrites, it logs.

---

## 0. Install (Windows / macOS / Linux)

```bash
cd ff-proxy
python -m pip install -r requirements.txt
python tools/selftest.py          # 30 checks: pb codec, jsonpath, rules
```

Requires Python 3.11+ (tested on 3.13 + mitmproxy 12.3.1).

---

## 1. Start the server

```bash
python run.py
```

Output tells you everything you need:

```
 proxy      : 0.0.0.0:8080  (set this on the iPhone)
 rules      : ...\rules\freefire.yaml   [6 rules, hot-reload ON]
 trace      : ...\logs\flows.jsonl
 LAN ip     : 192.168.1.23
```

Useful flags:

```bash
python run.py --port 8888            # different listen port
python run.py --body                 # capture hex bodies into the trace
python run.py --rules rules/dev.yaml
python run.py --no-ca-server         # skip the :8081 CA helper
```

Allow `python` through Windows Firewall on **private** networks, otherwise the
phone can't reach port 8080.

---

## 2. Point the iPhone at it

**WiFi proxy path (5 minutes):**

1. iPhone → Settings → WiFi → (your network) → *Configure Proxy* → **Manual**
   - Server: `192.168.1.23`  *(your laptop LAN IP from the startup banner)*
   - Port: `8080`
   - Authentication: off
2. Open **Safari** → `http://192.168.1.23:8081/ca.cer` → *Allow* to download
   the profile.
3. Settings → General → VPN & Device Management → install the **ff-proxy CA**
   profile.
4. Settings → General → About → **Certificate Trust Settings** → enable full
   trust for ff-proxy CA. **Without this step TLS interception silently fails.**

**Stealth path (recommended — no system proxy flag):**

Leave WiFi proxy OFF. Install Shadowrocket / Surge / Loon and add a rule that
sends the FF domains to your laptop, then chain:

```ini
# Shadowrocket [Rule]
DOMAIN-SUFFIX,garena.com,PROXY,<laptopIP>
DOMAIN-SUFFIX,freefiremobile.com,PROXY,<laptopIP>
DOMAIN-SUFFIX,ggblue.com,PROXY,<laptopIP>
DOMAIN-SUFFIX,booyah.com,PROXY,<laptopIP>
```

Why this matters: iOS apps can call `CFNetworkCopySystemProxySettings()` and see
that a WiFi proxy is configured. Apps *cannot* see an upstream proxy that the
VPN/tunnel extension is using internally. Same interception power, no flag.

---

## 3. Prove it's actually intercepting

Enable the self-test rule (bottom of `rules/freefire.yaml`):

```yaml
  - name: prove-hook
    enabled: true          # <- flip this
```

Relaunch Free Fire. The first `garena`/`freefire` request now stalls for 3
seconds. Stall = the proxy is in the path and the rules engine is live. Turn it
back off.

Then look at what the client actually talks to:

```bash
python tools/hosts.py
```

```
   flows  host                                    status / content-type
      41  api.freefiremobile.com                   200x39,403x2   application/json
      18  ff.garena.com                            200x18         application/octet-stream
      12  login.garena.com                         200x12         application/json
```

and get a ready-to-paste regex + ignore-list for `config.yaml`:

```bash
python tools/hosts.py --regex
```

**Always do this before writing rules.** The endpoint names change between
patches and region builds; guessing them wastes hours. `grep` the trace instead:

```bash
findstr /i "config manifest attest token" logs\flows.jsonl
# or in PowerShell
Select-String -Path logs\flows.jsonl -Pattern 'oauth|config|manifest|attest' | Select-Object -First 30
```

---

## 4. Rules

Rules live in `rules/freefire.yaml`. Edit → save → **no restart**. All matching
rules run in file order.

| action | what it does | params |
|---|---|---|
| `log` | record only, no modification | — |
| `delay` | add latency to prove the hook | `ms` |
| `header` | set / strip headers | `set: {}`, `strip: []` |
| `block` | synthesize a reply, never hit the server | `status`, `body`, `content_type` |
| `redirect` | 302 somewhere else | `to`, `status` |
| `mock` | replace the whole body | `status`, `body_file`, `headers` |
| `rewrite_json` | set / delete / merge JSON paths | `set: {}`, `delete: []`, `merge: {}` |
| `pb_set` | patch protobuf fields, **no .proto needed** | `set: {"1.5": 99}`, `set_bytes` |
| `rewrite_urls` | walk JSON, replace every URL matching a regex | `url_regex`, `to`, `max` |
| `probe_urls` | find and record URLs in the body | — |
| `bytes_sub` | raw find/replace | `find: "hex:.."`, `replace` |

Every rule optionally takes `feature: <key>` — it only runs while that menu
toggle is ON. Unknown feature keys are logged and treated as ON, so a typo never
silently disables a rule.

Body specs anywhere: `hex:deadbeef` · `str:plain` · `b64:AAAA` · bare text.

### The JSON rewrite (works today on any JSON endpoint)

```yaml
  - name: config-override
    action: rewrite_json
    stage: response
    match:
      host_regex: '(?i)freefire'
      path_regex: '(?i)/config'
    params:
      set:
        "data.limits.max_players": 50
      delete:
        - "data.telemetry_url"
      merge:
        "data": {"custom_flag": true}
```

### The protobuf rewrite (for `application/octet-stream` bodies)

Many Garena endpoints answer in protobuf. No schema needed — work on field tags.
Find the field first:

```bash
# put the body in a file (enable capture_bodies: true in config.yaml first)
python tools/pbdecode.py --from-trace logs/flows.jsonl --seq 42 -o body.bin

# dump the tree
python tools/pbdecode.py body.bin

# every nested field currently holding the value 1  (candidate boolean flags)
python tools/pbdecode.py body.bin --find 1

# read one field without the noise
python tools/pbdecode.py body.bin --get 3.1
```

Then patch it, either offline or as a live rule:

```bash
python tools/pbdecode.py body.bin --set 3.1=0 -o body.patched
```

```yaml
  - name: pb-flag-off
    action: pb_set
    stage: response
    match:
      path_regex: '(?i)/config'
    params:
      set:
        "3.1": 0
      set_bytes:
        "2": "str:ok"
```

`pb_set` re-encodes length prefixes for you and the codec round-trips (verified
in `tools/selftest.py`, including nested mutation and repeated fields).

---

## 4.5 The menu inside Free Fire

This is the part you actually asked for. There are two ways the panel shows up,
and only one of them renders *inside* the game.

### Step 1 — open the panel and confirm it works

```bash
python tools/menu_server.py          # http://<laptop LAN IP>:8082
```

Or, with the proxy running and the phone on it, just open **`http://menu.ff/`**
in Safari. `menu.ff` is a virtual host: the proxy answers it directly, so no DNS
record, no extra port, no install. Toggling a switch there changes proxy
behaviour on the next request.

### Step 2 — make it render inside Free Fire

Free Fire opens in-game pages (news, events, banners, notices) in a WebView from
URLs it receives over HTTPS. Hijack those URLs and the game renders **your**
page inside itself.

```bash
python run.py --body                 # 1. capture with bodies
# 2. play one session, then find the carriers:
python tools/hosts.py
Select-String -Path logs\flows.jsonl -Pattern 'urls' | Select-Object -First 20
```

The enabled rule `probe-webview-urls` already records every URL it sees in each
response body. The trace shows them as a `urls` array:

```json
{"kind":"response","host":"api.freefiremobile.com","path":"/notice/list",
 "status":200,"matched":["probe-webview-urls"],
 "urls":["https://h5.garena.com/activity/xyz","https://cdn.garena.com/b.png"]}
```

That `/notice/list` path is your carrier. Now arm the hijack — flip
**INTERFACE → Hijack Webview** in the panel, or by hand:

```bash
curl -X POST http://127.0.0.1:8082/api/set -d '{"ui.inject_menu":true}'
```

The rule `hijack-webview-to-menu` (in `rules/menu.yaml`) rewrites every URL in
that response to `http://menu.ff/`. Reopen the notice/event page in game and the
panel renders inside Free Fire.

If the game only opens the page once per launch, force it: kill and relaunch FF
after arming the toggle. If the WebView refuses `http://`, point it at a real
LAN address instead — `to: 'http://192.168.1.23:8082/'` with
`tools/menu_server.py` running.

### What each toggle really does

Every row is wired to actual addon behaviour — no decoration.

| Toggle | Backed by |
|---|---|
| `sec.quiet` | addon returns before touching anything. True kill switch |
| `debug.capture_bodies` | switches on hex body capture in the trace, live |
| `net.block_telemetry` | 204s every host matching `telemetry_hosts_regex` |
| `net.block_ads` | 204s every host matching `ads_hosts_regex` |
| `net.lag_ms` | `asyncio.sleep` on matching game traffic before it leaves |
| `net.drop_pct` | randomly 504s matching requests |
| `cfg.keep_alive` | walks any JSON response and flips maintenance/closed keys to open |
| `cfg.mute_notices` | walks any JSON response and blanks notice/announcement/banner keys |
| `cfg.force_region` | overwrites `region` / `reg` / `country` keys |
| `cfg.apply_overrides` | gates your hand-written `rewrite_json` / `pb_set` rules |
| `ui.inject_menu` | gates `hijack-webview-to-menu` |
| `ui.theme` | panel accent colour (lime / amber / cyan / red) |
| `rule.enable_probe` | log every intercepted flow, not only rule matches |
| `rule.strict_pin` | baseline for upstream cert verification |

The `cfg.*` walkers are schema-free on purpose: they search the whole JSON tree
for those key names at any depth, so they work across regions and patches
without you knowing the exact shape of the document.

### Adding your own toggle (3 steps)

1. Add an item to `menu/features.json`:
   ```json
   {"key": "my.feature", "type": "toggle", "label": "My Feature",
    "default": false, "hint": "what it does"}
   ```
   Refresh the panel — the row appears. Types: `toggle`, `range` (needs
   `min`/`max`/`step`/`unit`), `select` (needs `options`), `text`, `number`.
2. Gate a rule on it in `rules/*.yaml`:
   ```yaml
   - name: my-rule
     feature: my.feature
     action: rewrite_json
     stage: response
     match: { path_regex: '/config' }
     params: { set: {"data.something": false} }
   ```
3. Save. Rules reload on mtime, state reloads within 250 ms. No restart.

### The menu API (it's a real API, use it from anywhere)

```
GET  /                 -> panel html
GET  /api/state        -> {values, definitions, groups, info, stats}
POST /api/set          -> {"net.lag_ms": 300} -> applied/accepted/rejected
GET  /api/log?n=60     -> pretty trace tail
POST /api/reset        -> back to defaults
```

Unknown keys and malformed values are rejected, ranges are clamped server-side.
Try it: `POST /api/set {"net.lag_ms": 99999}` returns `2000`, not `99999`.

### Honest boundary

This menu is a **proxy control surface** rendered in the game's WebView. Its
buttons do what a proxy can do: config, network shape, endpoints, content
delivery.

It is **not** a native in-match cheat menu on its own. Two groups — `AIMBOT /
NATIVE` and `ESP / NATIVE` — are there because the injected dylib reads those
same keys over `/api/state`; without the dylib in the process they store a value
and change nothing, and each group says so in its own note. Everything else is
real proxy behaviour with no native layer behind it.

The real native menu (floating overlay over the render, IL2CPP-driven ESP) needs
the IPA route. **That route is built — see `native/README.md`.**
It is a Theos dylib injected into the official IPA, driven by this same panel.

### Build status

| job | state | where |
|---|---|---|
| offline suites (894 checks) | **green** | `ubuntu-latest`, every push |
| `FFMenu.dylib` (arm64, iOS 12 floor) | **green**, ~295 KB | `macos-15-intel`, every push |
| package IPA | runs when an `ipa_url` is supplied | `ubuntu-latest` |

The compiled dylib is the `FFMenu-dylib` artefact on each run. The build asserts
arm64-only, the iOS 12 deployment target, that no plaintext feature key survives,
and that the image does not name itself — all of which pass.

Two notes on the runner, because both cost a run to learn:

- **The runner is `macos-15-intel`, not `macos-latest`.** `macos-latest` resolves
  to an arm64 image here; those jobs sat in the queue unassigned for 15 minutes
  and were then cancelled, with no step ever recorded. The Intel image schedules
  immediately, and building an arm64 iOS dylib on an x86_64 host is the classic
  Theos configuration — nothing is lost.
- **The build is not gated on the test suite.** A failing Python test used to skip
  the compile entirely, which hid the compiler output — the exact thing you need
  when the toolchain is what is broken.

Compiler diagnostics are published as check-run annotations, so the reason for a
red build is readable without downloading the job log.

## 4.6 The native layer (`native/`)

The proxy cannot create ESP. That is not a limitation of this implementation —
iOS never maps code into a process unless it is inside the code-signed bundle,
and bytes arriving over a socket never become executable. So an in-game menu
with real ESP/aimbot is always in-process code:

| Path | What it is |
|---|---|
| re-signed IPA + injected dylib | official Garena bundle + one extra `LC_LOAD_DYLIB`, signed with your cert |
| jailbreak injection | dylib pushed into the live process |
| TrollStore loader | permanent app that injects into Free Fire |

All three are the same thing. What the sellers call "proxy" is the license
server and the offset map — i.e. exactly what `ff-proxy/` already is.

`native/` closes the loop so both layers share one panel:

```
panel / WebView menu  ->  state/state.json  ->  dylib polls /api/state   ->  ESP, aimbot
                        state/ff_native.json ->  dylib polls /api/native ->  class + offset map
```

Because the class map comes over HTTP, a game patch is fixed by editing
`state/ff_native.json` — no rebuild, no re-sign, no reinstall.

```bash
cd native && make                     # -> .theos/obj/arm64/FFMenu.dylib
frida -U -f com.dts.freefireth -l tools/il2cpp_hunt.js --no-pause   # fill the ??
python tools/package_ipa.py decrypted.ipa native/.theos/obj/arm64/FFMenu.dylib
# -> out/com.dts.freefireth-payload.ipa  (works with TrollStore,
#    Sideloadly, AltStore, ESign, GBox, or a jailbroken install)
```

Full walkthrough, the value-hunting procedure, the verification ladder and the
detection notes are in **`native/README.md`**. The all-iOS range, the installer
matrix and the unvarnished ban analysis are in **`native/INSTALL.md`**. Read the
"Honest status" section at the bottom of `native/README.md` before you build.

## 5. What a proxy can and cannot reach — read this before you spend hours

Free Fire is **server-authoritative** with a custom encrypted UDP transport for
in-match gameplay. Be precise about the boundary:

| Layer | Over HTTPS? | Proxy can touch it? |
|---|---|---|
| Login / OAuth / guest / device register | yes | **yes** — inspect & rewrite |
| Remote config, feature flags, AB tests, limits | yes | **yes** — this is the real win |
| Region / server list, maintenance flags | yes | **yes** |
| Hot-update manifest, asset versioning | yes | **yes** — redirect to your own host |
| Matchmaking RPC (some protobuf over HTTPS) | mostly yes | **yes for the HTTP ones** |
| Any HTTP-side anti-cheat / integrity report | yes | **yes** — stub it (see cautions) |
| **In-match gameplay** (movement, damage, fire rate, ESP targets) | **no — UDP, encrypted, server-side** | **no** |
| App Attest / Secure Enclave | hardware-bound, talks to Apple | **no** |
| Certificate pinning | if enabled in the build | **no** — kill the pin first |

So: proxy layer = API / config / manifest / attestation-response territory. In-match
features (aim, ESP, speed, godmode) are **client-side** — that needs an IPA
decrypt + dylib inject, not a proxy. If a video promises you an in-match aimbot
from a WiFi proxy alone, that video is selling you skins or nothing.

**The honest caveat about "viral prox" methods:** most of them are re-signed
APKs/asset packs with a costume skin swapped in, or plain fakes. What is real
and worth building is exactly what this project does: full API-layer control with
a laptop-side rule engine and a real trace. Build on that.

### Certificate pinning

If FF pins its certs, intercepted TLS will fail as a connection error. Detect it
instantly: with the proxy running, if requests to `garena` hosts error out in the
mitmproxy log ("Client TLS handshake failed" / "connection closed") while other
sites work, pinning is on. Options, in order of effort:

1. `ssl_insecure: true` in `config.yaml` — this affects the *upstream* leg, not
   the client leg. It will not defeat client-side pinning. Try it anyway to rule
   out an upstream cert problem.
2. Chain behind a phone-side MITM (Shadowrocket with "MITM" enabled for those
   domains) — the phone's own unpinning/CA substitution happens before your laptop.
3. Real fix: locate the pin and patch it in the binary (`SecTrustEvaluate`,
   `SSL_set_verify`, `NSURLSession didReceiveChallenge`). That's the IPA route.
   Mark this `[perlu dump dulu]` — confirm with `class-dump`/`strings` before
   assuming which is used.

### Anti-cheat caution (practical, not moral)

`probe-anticheat` is in the ruleset **disabled on purpose**. Read the trace first.
Blindly stubbing an integrity or attestation endpoint is the single fastest way
to get a flag on the account — a client that reports "clean" while behaving
abnormally is a stronger signal than a client that reports normally. Log it,
understand the response shape, then decide. Prefer staying quiet over lying loud.

---

## 6. The stealth / forced-routing path

WiFi proxy only captures traffic from apps that respect the system proxy
(CFNetwork / URLSession). Raw-socket code ignores it. Two real fixes:

**A. Phone-side redirector (best).** Shadowrocket/Surge/Loon as described in
section 2. Everything goes through your laptop, nothing shows in
`CFNetworkCopySystemProxySettings()`.

**B. DNS hijack + forward on the laptop.** Included:

```bash
# hijack every domain the trace has seen, answer with this laptop's LAN IP
python tools/dns_hijack.py --auto

# or be explicit
python tools/dns_hijack.py --domain garena.com --domain freefiremobile.com --upstream 1.1.1.1
```

Then set the iPhone's DNS to the laptop IP. Caveat that bites everyone: DNS
points the phone at your laptop, but the connection still lands on port 443,
which is not where mitmproxy is listening. You need a TCP redirector. On
Windows the workable pattern is per-host reverse mode:

```bash
# one instance per hijacked host, upstream = the real host
mitmdump --mode reverse:https://api.freefiremobile.com --listen-port 8443 \
         -s "ffproxy/plugin_entry.py"
netsh interface portproxy add v4tov4 listenport=443 listenaddress=192.168.1.23 \
      connectport=8443 connectaddress=127.0.0.1
```

Windows `netsh portproxy` can only map one listen port to one target, so if you
need several hosts simultaneously, run the redirector on the router (OpenWRT
`iptables -t nat -A PREROUTING ... REDIRECT`) or just use path A. Path A is
strictly better and is what you should ship.

**Also carry over from the stealth side:** `ignore_hosts` in `config.yaml`
already keeps Apple/iCloud/Google/Meta out of the TLS-intercept path. That keeps
CPU down, keeps your trace readable, and avoids breaking unrelated logins.

---

## 7. Verification

```bash
# 1. units: protobuf codec, jsonpath, rules, control state, menu api
python tools/selftest.py
# expect: 57x [ok  ] -> all checks passed

# 2. end-to-end: real proxy + menu.ff + live toggles
python ..\..\..\AppData\Local\Temp\opencode\ffproxy_menu_e2e.py
# expect: menu.ff serves the panel / hijack fires / lag is real / quiet stops it -> PASS

# 3. panel renders
python tools/menu_server.py
# open http://<LAN IP>:8082 -> pill says LIVE, 5 groups, stats populated

# 4. live
python run.py
python tools/hosts.py       # expect: your FF hosts appear with 200s
```

Proof that the toggle actually reached the engine (not just the UI): flip
`net.lag_ms` to 700, then watch a request stall ~0.7s. That number comes from
`asyncio.sleep` in the addon, gated by the value the panel wrote to
`state/state.json` — the whole chain in one observable.

Live check that interception is real (not just configured):

```bash
python tools/hosts.py --top 5
# if the host list is empty, nothing is being intercepted. Fix the proxy setting
# or the CA trust before touching any rules.
```

---

## 8. File map

```
ff-proxy/
├─ run.py                  launcher: mitmproxy + CA server + banner
├─ config.yaml             listen, filter, ignore_hosts, kill-switch regexes
├─ serve.bat               double-click start on Windows
├─ ffproxy/
│  ├─ addon.py             mitmproxy addon: hooks, menu host, rule dispatch, trace
│  ├─ rules.py             rule model + multi-file store + hot reload
│  ├─ control.py           live feature state (menu <-> addon), 250ms reload
│  ├─ menu_api.py          panel http surface, shared by both front doors
│  ├─ pb.py                schema-less protobuf wire codec (parse/mutate/encode)
│  ├─ jsonpath.py          dotted-path + [i] accessor
│  └─ plugin_entry.py      entry for `mitmdump -s` / reverse mode
├─ menu/
│  ├─ features.json        toggle definitions - the single source of truth
│  └─ menu.html            the panel (self-contained, no external requests)
├─ rules/
│  ├─ freefire.yaml        recon rules on, payload rules parked disabled
│  └─ menu.yaml            webview hijack + feature-gated payload rules
├─ native/                 the in-process layer (injected dylib)
│  ├─ README.md            why a proxy can't do ESP, and how this does
│  ├─ INSTALL.md           all-iOS range, installer matrix, ban reality
│  ├─ USAGE.md             feature list, per-patch flow, troubleshooting
│  ├─ Makefile             Theos -> FFMenu.dylib, iOS 12 floor, no substrate
│  ├─ Tweak.x              stealth -> wait for il2cpp -> bind -> install
│  ├─ entitlements.plist   get-task-allow + identity + network
│  ├─ ff_native.example.json   class/field map template (copy to state/)
│  ├─ Core/                IL2CPP bridge, FFState, Game, ESP, Stealth, Compat
│  └─ Menu/                native floating panel
├─ state/state.json        written by the panel, read live by the addon
├─ state/ff_native.json    class/field map, served at /api/native
├─ tools/
│  ├─ selftest.py          63 assertions, run after every edit
│  ├─ tests/               offline suites + runner
│  │  ├─ run_all.py        python tools/tests/run_all.py  (the count is printed)
│  │  ├─ test_ipa_probe.py
│  │  ├─ test_package_ipa.py
│  │  └─ test_metadata.py
│  ├─ menu_server.py       standalone panel on :8082 (same state file)
│  ├─ hosts.py             trace -> host/path summary -> suggested regex
│  ├─ pbdecode.py          decode/read/patch protobuf bodies from the trace
│  ├─ dns_hijack.py        DNS hijack resolver for the forced-routing path
│  ├─ metadata_hunt.py     harvest real class/field names from the IPA itself
│  ├─ il2cpp_hunt.js       Frida: confirm structure on a live device
│  ├─ ipa_probe.py         read any IPA: cryptid, injected dylibs, verdict
│  ├─ package_ipa.py       dylib -> sign-agnostic IPA, no Xcode needed
│  └─ build_ipa.sh         macOS variant (insert_dylib + ldid + zsign)
├─ .github/workflows/
│  └─ build-dylib.yml      build the dylib on a macOS runner, no Mac needed
└─ logs/flows.jsonl        generated trace
```

---

## 9. Where to go next

1. **Trace first.** Run a real session with `--body`, then `tools/hosts.py`, then
   `pbdecode.py` on every interesting body. Write down host + path + content-type
   + field tree. That document is the actual product.
2. **Find the WebView carrier.** Look for `urls` in the trace on notice/event/
   banner endpoints, then arm `ui.inject_menu`. That is the moment the panel
   appears inside Free Fire.
3. **Turn on what you found.** Remote config is usually the cheapest real change:
   flags, limits, telemetry switches.
4. **Manifest redirection** if the client pulls assets over HTTPS — that's the
   path to client-side content changes without touching the binary.
5. **For a native in-match menu you need the binary**, not the proxy: decrypt the
   IPA → confirm `cryptid == 0` → `class-dump`/`dsdump` → `il2cppdumper` for the
   IL2CPP metadata → Theos dylib with a `UIWindow` overlay above the render
   surface. Free Fire is Unity/IL2CPP, so hooks go by metadata name or RVA, not
   ObjC selectors — almost nothing of interest is ObjC.
6. **Keep a per-version config.** After every game patch: re-run `tools/hosts.py`,
   diff the paths, re-dump the bodies, update only the rules that broke.

Two builds I can ship next, pick one:

- **Native menu** — decrypt → dump → Theos tweak with a floating overlay, real
  feature hooks, menu styled to match `menu/menu.html` so both layers look like
  one product.
- **Rule generator** — diff two captured sessions and auto-write the YAML +
  `features.json` entries, so finding a new carrier takes one command instead of
  a hunting session.
