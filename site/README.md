# DEMIOS Android - storefront + order API

The Android product sold on the same site as the iOS one. A buyer picks a plan on
`/android`, pays with QRIS, and gets a key that the APK accepts offline. Nothing
here talks to the APK at runtime; the only shared thing is the key format, which
`functions/_lib/key.js` mints and `License.java` verifies on the phone.

This directory is a **drop-in overlay** for the existing Pages project. It does
not replace anything that is already there.

```
site/
  functions/
    _lib/
      key.js        key minting - the same 20-char format as keygen.py
      qris.js       static QRIS -> dynamic QRIS with the order's amount
      qr.js         QR encoder (byte mode, EC level M, v1-v20), no dependencies
      orders.js     order records in KV, token auth, mint-on-approve
      pay.js        manual QRIS (default) / klikqris adapter / webhook signature
      plans.js      the price table
      http.js       json/fail/allow, CSPRNG ids, constant-time compare
    api/android/
      plans.js      GET  - price list + whether payment is configured
      order.js      POST - create an order;  GET - poll it
      admin.js      POST - approve, cancel, list, mint (ADMIN_SECRET)
      webhook.js    POST - gateway callback (disabled without WEBHOOK_SECRET)
  public/
    android.html        /android
    android.css         only the deltas on top of /store.css
    android.js          the storefront
    qr.js               draws the QR canvas from packed bits
    android-admin.html  /android-admin - the back office
  tools/
    install.py          copy the overlay into a real project, add the nav link
    mint.mjs            mint/verify a key from the command line
    pay_probe.mjs       ask a live gateway to make one QR and report the shape
    qris_from_image.py  decode a QRIS screenshot into the payload string
    qr_probe.mjs        encoder dump used by the tests
    qris_probe.mjs      QRIS builder dump used by the tests
  tests/                7 suites, wired into tools/tests/run_all.py
  wrangler.toml         a fragment - merge, do not paste over
  package.json          only to mark the tree as ESM; Pages ignores it
```

---

## 1. Deploy

`site/public` is **not a website**. It is five files that belong next to the ones
already there. Running `wrangler pages deploy site/public` on its own would
replace the whole project - `store.html`, `/api/buy`, the iOS storefront - with
five files. Do not do that.

### A. Copy it in (the only safe path)

```bash
# point it at your project's real output dir; --functions is auto-detected
python site/tools/install.py --static web/public --dry-run   # see what changes
python site/tools/install.py --static web/public
```

It copies `public/*` next to your pages, copies `functions/*` next to your
Functions, and adds one line to `store.html`:

```html
<a href="/android" class="nav-admin">ANDROID</a>
```

That link goes in before `ACTIVATE`, once. Running it twice changes nothing, and
it refuses a directory that does not contain `store.html`/`index.html`, or a
`--static` that points back at the overlay.

### B. KV, secrets, deploy

```bash
wrangler kv namespace create DEMIOS_KV        # prints the id

# merge the [[kv_namespaces]] and [vars] blocks from site/wrangler.toml
# into the project's own wrangler.toml, then:

wrangler pages secret put LICENSE_SECRET --project-name demios
wrangler pages secret put ADMIN_SECRET   --project-name demios
wrangler pages secret put QRIS_STATIC    --project-name demios

wrangler pages deploy web/public --project-name demios
```

`install.py` prints the exact `LICENSE_SECRET` value, read out of
`License.java`, so the two sides cannot be set to different strings by hand.

### C. Preview first, if you want

A deployment to any branch other than `main` is a preview: production is
untouched, and the URL is `<branch>.demios.pages.dev`.

```bash
wrangler pages deploy site/public --project-name demios --branch android
# -> https://android.demios.pages.dev/android
```

Useful for checking the Functions in the real runtime before touching the live
store. It will not have your homepage or `/api/buy` - it is a preview of the
overlay only.

**Preview does not inherit production secrets.** Cloudflare keeps two separate
environments, and a variable set for production is not visible to a preview
deployment until it is added to the preview environment as well (dashboard:
Settings -> Variables and Secrets -> Preview). Bindings and `[vars]` declared in
the deploy config do apply to preview, which is why KV works there before any
secret does.

### D. What is already set up on the project

Done for `demios` (direct upload, no Git connection):

| | |
|---|---|
| KV namespace | `DEMIOS_ANDROID`, id `d6ff8b28077b43caad79594c87c7b407` |
| Binding, both environments | `DEMIOS_KV` |
| Production secrets | `LICENSE_SECRET`, `ADMIN_SECRET` added; the nine iOS secrets and the `KEYS` binding untouched |
| Preview | `LICENSE_SECRET`, `ADMIN_SECRET`, `PAY_MODE=manual`, `ORDER_RATE_PER_HOUR=30` |
| Preview branch | `android` -> https://android.demios.pages.dev/android |

Production still needs `QRIS_STATIC` (or `PAY_MODE=klikqris`), and a deploy that
includes the overlay. Everything else is in place.

---

## 2. Environment

| name | kind | required | what it does |
|---|---|---|---|
| `LICENSE_SECRET` | secret | **yes** | signs keys. Must be byte-identical to `License.SECRET` in the APK. |
| `ADMIN_SECRET` | secret | **yes** | unlocks `/android-admin` and `POST /api/android/admin`. |
| `QRIS_STATIC` | secret | yes for manual | the seller's static QRIS payload (see §3). |
| `DEMIOS_KV` | binding | **yes** | order storage. The code also accepts `ORDERS`, `STORE_KV` or `KV` if one is already bound. |
| `PAY_MODE` | var | no | `manual` (default) or `klikqris`. |
| `ORDER_RATE_PER_HOUR` | var | no | orders per IP per hour, default `30`, `0` disables. |
| `WEBHOOK_SECRET` | secret | no | enables `POST /api/android/webhook`. Without it the endpoint refuses everything. |
| `KLIKQRIS_CREATE_URL` | secret | only for `klikqris` | the create endpoint. |
| `KLIKQRIS_KEY` | secret | only for `klikqris` | the credential. Falls back to `KLIKQRIS_API_KEY`, which this project already has - so the existing secret is picked up with no new variable. |
| `KLIKQRIS_MERCHANT_ID` | secret | no | sent as `merchant_id` when set. Already present in the project. |
| `KLIKQRIS_AUTH_HEADER` | var | no | default `x-api-key`. Set `Authorization` if the account wants a bearer token. |
| `KLIKQRIS_AUTH_PREFIX` | var | no | default empty. Set `Bearer ` (trailing space) with `Authorization`. |
| `KLIKQRIS_BODY` | var | no | a JSON template with `{{amount}}`, `{{ref_id}}`, `{{merchant_id}}`. |
| `KLIKQRIS_QR_FIELDS`, `KLIKQRIS_URL_FIELDS` | var | no | dotted paths into the gateway's JSON, comma separated. Set these instead of editing code when the response shape differs. |

`LICENSE_SECRET` is the one that will bite. It was rotated off the placeholder
before the first sale; read the current value with:

```bash
python site/tools/install.py --static web/public --dry-run | grep LICENSE_SECRET
# or straight from the source of truth:
grep -o 'SECRET = "[^"]*"' android/app/app/src/main/java/com/alokg/ffmenu/License.java
```

Change it in both places, in the same commit, then rebuild the APK. The suite
`site/tests/test_site_key.py` reads the secret out of the compiled Java class and
mints with it, so a drift is caught - but only if the two agree when the test
runs, not if you change one and never redeploy.

---

## 3. `QRIS_STATIC`

The seller's *static* QRIS string - the one behind the QR in GoPay Merchant /
DANA Bis / BRI / whatever. A static code has no amount, so every buyer would
type it themselves and nothing could be matched to an order.

The site turns it into a **dynamic** code per order: tag 01 becomes `12`, tag 54
(the amount) is inserted in order, and tag 63's CRC is recomputed. Same merchant
account, fixed amount, one scan.

Get the string from the image:

```bash
pip install opencv-python-headless
python site/tools/qris_from_image.py qris.png
# 0002010102112665...63048160
# // 207 bytes, QRIS payload - paste as QRIS_STATIC
```

Or scan it with any QR app and copy the text. It must start with `000201` and
end with `6304` plus four hex characters. `/api/android/plans` reports
`ready: false` with the reason if it does not validate, so a typo is visible
before a buyer sees it.

**Do not** put a per-order amount into `QRIS_STATIC` - the site inserts tag 54
itself and will replace whatever is there.

---

## 4. Daily use - `/android-admin`

1. Open `/android-admin`, paste `ADMIN_SECRET`, **MASUK**. It is kept in
   `sessionStorage`: closing the tab forgets it.
2. The **MENUNGGU** tab is the queue, with the amount and the order age.
3. When the money shows up in the merchant app, press **SUDAH BAYAR -> TERBITKAN
   KEY**. The key appears on the row and on the buyer's page within ~4 seconds.
4. **BATALKAN** closes an order that will never be paid.
5. **MINT** issues a key by hand - replacements, a refund, a friend.

Approving twice returns the first key, not a second licence. That is deliberate:
a double-click must not cost a sale.

The buyer's page polls `GET /api/android/order` every 4 seconds and stops when
the key arrives. If the buyer closes the tab and loses `localStorage`, the key is
still in the order record - find the order id in `/android-admin` and read it off
the row.

### Making approval exact

Two pending orders of the same plan have the same amount, so matching a payment
to an order is by time and amount. If that becomes ambiguous, add a few rupiah
per plan in `site/functions/_lib/plans.js` - or turn on `PAY_MODE=klikqris` so
the gateway settles orders itself.

---

## 5. Prices

`site/functions/_lib/plans.js`, one array. Ids are `a1d`, `a7d`, `a30d` - the
`a` prefix matters: the iOS store uses `1d`, `7d`, `30d` in the same KV
namespace, and an Android order must never be answerable by the iOS path.

`tier` goes into the key's payload nibble (`1` basic, `2` pro). `License.java`
reports it but nothing gates on it yet.

---

## 6. The order lifecycle

```
POST /api/android/order { plan: "a7d", device?: "<android id>" }
  -> { orderId: "AND-7K2M9Q", token: "<24 hex>", amount: 140000,
       qr: { size: 61, bits: "<base64>" }, expiresAt, note }

GET  /api/android/order?id=AND-7K2M9Q&token=<24 hex>
  -> { status: "pending" | "paid" | "cancelled" | "expired", key? }
```

- The token is returned **once**, at creation. The order id is short enough to
  read over the phone, so the token is what makes it unguessable. Wrong token =
  404, never a key.
- A pending order lives 30 minutes. Expiry is computed on read, not written, so
  nothing has to sweep the namespace.
- `device` is optional. Filled in, the key binds to that Android ID and works
  nowhere else; empty, it works on any phone until it expires.
- The APK prints the device id after a successful activation, so a buyer who
  already has a key can find theirs.

---

## 7. Payment modes

### manual (default)

No gateway account, no callback URL, no API key. The seller's static QRIS is
turned into a dynamic code for the order's amount, the buyer scans it, and the
seller presses one button. Honest about the trade-off: a human is in the loop,
and the page says the key appears after the payment is confirmed.

### klikqris

`POST $KLIKQRIS_CREATE_URL` with `x-api-key: $KLIKQRIS_KEY` and
`{ "amount": 140000, "ref_id": "AND-7K2M9Q" }`. The response is searched for a QR
string or a payment URL at a list of known paths, then at whatever
`KLIKQRIS_QR_FIELDS` / `KLIKQRIS_URL_FIELDS` say.

Nothing about the account is assumed. The endpoint, the credential, the header
name, the auth prefix and the request body all come from env (`KLIKQRIS_AUTH_HEADER`,
`KLIKQRIS_AUTH_PREFIX`, `KLIKQRIS_BODY`, `KLIKQRIS_MERCHANT_ID`), so a gateway that
wants `Authorization: Bearer <key>` and `{"nominal":"140000"}` is a set of
variables, not a patch:

```bash
# what the account might be:
KLIKQRIS_AUTH_HEADER=Authorization
KLIKQRIS_AUTH_PREFIX="Bearer "
KLIKQRIS_BODY='{"nominal":"{{amount}}","reff":"{{ref_id}}","merchant":"{{merchant_id}}"}'
```

Set `KLIKQRIS_API_KEY` and it is used as the credential when `KLIKQRIS_KEY` is
absent - the project already has that secret, so there is nothing new to add.

Do not guess the account's response shape. Ask it:

```bash
node site/tools/pay_probe.mjs --url https://api.gateway/create --key sk_test_...
# POST ... / auth header x-api-key: sk_t...(24 chars) / body {...}
# status 200 / { ... }
# [ok] production would accept this response.
# or:
# [FAIL] no known field carries the QR or the payment URL.
#        KLIKQRIS_QR_FIELDS=result.qris_content
```

The probe runs the **same** `extract()`, `gatewayConfig()` and `requestBody()`
that production runs, so a green probe means the checkout will work - not that a
second implementation agrees with itself. It creates no order and stores nothing.
Every knob is also a flag (`--auth-header`, `--auth-prefix`, `--body`, `--qr`,
`--payment`), so a shape can be tried without exporting anything.

Exit codes: `0` accepted, `2` nothing matched, `3` unreachable or HTTP error.

### webhook

`POST /api/android/webhook` with
`signature = hex(HMAC-SHA256(WEBHOOK_SECRET, orderId + ":" + status + ":" + amount))`.
Disabled unless `WEBHOOK_SECRET` is set: an unauthenticated endpoint that marks
orders paid is a free-licence endpoint.

---

## 8. What this is not

- **Not a gateway integration by default.** Manual mode means a human presses a
  button. That is the mode that works with no merchant API credentials.
- **Not a licence server.** The APK never calls the site. A key is a signed
  string; the phone checks the signature offline. The site can mint, but it
  cannot revoke, and it cannot see who is using what.
- **Not unbreakable.** `LICENSE_SECRET` is inside the APK, so anyone who
  decompiles it can mint keys. Offline licensing cannot be stronger than that.
  What it buys: an expiry, a device binding, and no network dependency during a
  match.
- **KV is eventually consistent** (up to ~60 s across regions). Fine for this
  flow - the buyer and the admin are reading the same record minutes apart. It
  is not fine as a lock, which is why nothing here uses it as one.

---

## 9. Tests

```bash
python tools/tests/run_all.py          # everything, including these seven
python site/tests/test_site_key.py     # site keys -> the real License.java
python site/tests/test_site_qr.py      # QR encoder vs a reference implementation
python site/tests/test_site_qris.py    # static -> dynamic QRIS, CRC, tag order
python site/tests/test_site_api.py     # the four Functions against a fake KV
python site/tests/test_site_draw.py    # public/qr.js drawn into a fake canvas
python site/tests/test_site_markup.py  # page ids and assets vs the scripts
python site/tests/test_site_pay.py     # the gateway adapter, 6 response shapes
python site/tests/test_site_install.py # the installer, against a fake project
```

What each one actually proves:

- **keys** - compiles `License.java`, reads `SECRET` out of the class, mints with
  `key.js`, feeds it to the Java verifier. Also asserts node and `keygen.py`
  produce the identical key for the same day, and that the device hash agrees
  across python, java and node.
- **qr** - every matrix, every mask, compared bit-for-bit with python's
  `qrcode` library; then rasterised and decoded back with OpenCV; then the
  shipping decoder tool reads a PNG made from the same matrix.
- **qris** - CRC-16/CCITT-FALSE against the canonical `123456789 -> 29B1`
  vector, tag 54 in the right position, merchant fields untouched, malformed
  payloads refused.
- **api** - the whole lifecycle: create, poll, wrong token, admin auth, approve,
  idempotent approve, device binding, cancel, expiry, rate limit, webhook
  signature, missing KV, missing QRIS, wrong methods.
- **draw** - `public/qr.js` runs in a VM with a recording 2D context; every
  rectangle it emits is mapped back to a module and compared with what the
  server sent, and the quiet zone is checked to be empty.
- **markup** - every id a script reaches for exists in the page, every local
  asset exists, no TODOs ship.
- **pay** - a real HTTP server, the real `present()`: flat and nested
  `qr_string`, `checkout_url` only, an unknown nesting (refused, then fixed by
  an env var), an empty shape, HTTP 500 and a non-JSON body. The probe is run
  as a subprocess and its exit codes are asserted.
- **install** - a temporary project with a `store.html` shaped like the live
  one: dry run writes nothing, the real run copies everything and adds exactly
  one nav link inside `<nav>` before `ACTIVATE`, a second run is byte-identical,
  and a directory that is not a site is refused.

One check needs a built APK, so it is a CI step rather than part of the suite:

```bash
python tools/check_apk_secret.py --apk dist/ffmenu.apk
```

It reads the secret out of `License.java` and looks for it inside the APK's dex,
and fails the build if the current secret is missing or the placeholder is
present. Without it, changing the site secret without rebuilding the APK ships a
product where every key is rejected - and both sides look correct on their own.

---

## 10. Verified on the live deployment

The overlay was deployed to the preview branch `android` and driven end to end,
not just unit-tested. What was actually run and what came back:

| step | result |
|---|---|
| `GET /android`, `/android-admin` | 200, `text/html` |
| `GET /api/android/plans` | 200, `ready:false`, reason `QRIS_STATIC is not set`, 3 plans |
| `POST /api/android/order {"plan":"a7d"}` | 200, `AND-XXXXXX`, 24-hex token, amount 140000, QR absent with an honest note |
| `GET /api/android/order?id=..&token=..` | 200, `pending`, no key |
| wrong token | 404 `no such order` |
| `POST admin {action:"ping"}` | 200, `kv: bound`, `licenseSecret: set`, `webhookSecret: missing (webhooks disabled)` |
| `POST admin {action:"list"}` | the pending order, and no token in the listing |
| `POST admin {action:"paid"}` | 200, key `4772-4222-2222-ZSDE-5CA6` |
| the same approve again | the identical key - no second licence |
| buyer poll after approval | `paid` + that key |
| `POST admin` with no secret / wrong secret | 401 |
| `POST /api/android/webhook` unsigned | 503, fails closed |
| the minted key, in the real `License.class` | `OK 1 7` |
| the same key, in `keygen.py` | `ok (tier 1)` |
| the same key, in `key.js` | `{"ok":true,"left":7}` |
| `GET /api/android/_env` after cleanup | 404 - the temporary diagnostic is gone |

The last three matter most: a key minted by the live site verifies in the Java
class that ships inside the APK. That is the whole sale, in one line.

The test orders were then deleted from KV, so the queue starts empty.

---

## 11. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `/api/android/plans` says `ready: false`, reason mentions `QRIS_STATIC` | not set, or bad CRC | re-run `qris_from_image.py`, paste the exact string |
| `no KV binding: bind one as DEMIOS_KV` | binding missing or named differently | add it, or the code will pick up `ORDERS` / `STORE_KV` / `KV` |
| admin says `wrong secret` | `ADMIN_SECRET` differs from what you typed | re-set the secret, redeploy, retype |
| keys rejected by the APK | `LICENSE_SECRET` != `License.SECRET` | make them identical, rebuild the APK |
| `gateway returned no qr_string and no url` | the account's response shape is not in the list | `node site/tools/pay_probe.mjs ...`, then set `KLIKQRIS_QR_FIELDS` |
| QR shows but no wallet reads it | the buyer's app is old, or the screen is too dim | the code is dark-on-white at ~1024 px; turn the brightness up |
| order stuck at `pending` after payment | manual mode, nobody approved it | `/android-admin` -> MENUNGGU -> approve |
| `webhooks are disabled` | `WEBHOOK_SECRET` unset | that is the default and it is safe; set it only if a gateway calls you |
| the iOS store disappeared after deploying | `site/public` was deployed as the whole site | redeploy from the project root that has your `store.html`, then use `install.py` |
| a preview deployment says a secret is not set | preview has its own environment; production secrets do not reach it | add the variable to the Preview environment (dashboard: Settings -> Variables and Secrets -> Preview) |
