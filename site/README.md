# DEMIOS Android — storefront + order API

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
      key.js        key minting — the same 20-char format as keygen.py
      qris.js       static QRIS -> dynamic QRIS with the order's amount
      qr.js         QR encoder (byte mode, EC level M, v1-v20), no dependencies
      orders.js     order records in KV, token auth, mint-on-approve
      pay.js        manual QRIS (default) / klikqris adapter / webhook signature
      plans.js      the price table
      http.js       json/fail/allow, CSPRNG ids, constant-time compare
    api/android/
      plans.js      GET  — price list + whether payment is configured
      order.js      POST — create an order;  GET — poll it
      admin.js      POST — approve, cancel, list, mint (ADMIN_SECRET)
      webhook.js    POST — gateway callback (disabled without WEBHOOK_SECRET)
  public/
    android.html        /android
    android.css         only the deltas on top of /store.css
    android.js          the storefront
    qr.js               draws the QR canvas from packed bits
    android-admin.html  /android-admin — the back office
  tools/
    mint.mjs            mint/verify a key from the command line
    qris_from_image.py  decode a QRIS screenshot into the payload string
    qr_probe.mjs        encoder dump used by the tests
    qris_probe.mjs      QRIS builder dump used by the tests
  tests/                5 suites, wired into tools/tests/run_all.py
  wrangler.toml         a fragment — merge, do not paste over
  package.json          only to mark the tree as ESM; Pages ignores it
```

---

## 1. Deploy

The project is a Pages project with static files at the root of its output
directory and `functions/` next to it (the iOS `store.js` says `web/public/`, and
`/store.html` redirects to `/store`, which is Pages' clean-URL behaviour). Three
ways in, easiest first.

### A. Dashboard only, no CLI

1. **KV**: Workers & Pages → KV → *Create namespace* → name it `demios-android`.
2. **Binding**: your Pages project → Settings → Functions → *KV namespace
   bindings* → Variable name `DEMIOS_KV`, namespace `demios-android`.
3. **Secrets**: same page → *Variables and Secrets* → add, type **Secret**:
   `LICENSE_SECRET`, `ADMIN_SECRET`, `QRIS_STATIC`.
4. **Files**: copy `site/public/*` into the static output directory (next to
   `store.html`) and `site/functions/*` into the project's `functions/`.
5. Redeploy.

### B. Wrangler

```bash
# from the repo root of the site project
cp -r site/public/*        web/public/
cp -r site/functions/*     functions/

wrangler kv namespace create DEMIOS_KV          # prints the id
# merge the [[kv_namespaces]] and [vars] blocks from site/wrangler.toml

wrangler pages secret put LICENSE_SECRET --project-name <project>
wrangler pages secret put ADMIN_SECRET   --project-name <project>
wrangler pages secret put QRIS_STATIC    --project-name <project>

wrangler pages deploy web/public --project-name <project>
```

### C. Add a link from the iOS store

One line in `store.html`, in the `<nav>`, before `ACTIVATE`:

```html
<a href="/android" class="nav-admin">ANDROID</a>
```

Without it the Android page exists but nothing points at it.

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
| `KLIKQRIS_CREATE_URL`, `KLIKQRIS_KEY` | secret | only for `klikqris` | see `functions/_lib/pay.js`. |

`LICENSE_SECRET` is the one that will bite. It is currently the placeholder in
`android/app/app/src/main/java/com/alokg/ffmenu/License.java`:

```
ganti-secret-ini-sama-yang-di-keygen-py-jangan-pakai-yang-ini
```

Change it in **both** places, in the same commit, before the first sale. The
suite `site/tests/test_site_key.py` reads the secret out of the compiled Java
class and mints with it, so a drift is caught — but only if the two files agree
when the test runs, not if you change one and never redeploy.

---

## 3. `QRIS_STATIC`

The seller's *static* QRIS string — the one behind the QR in GoPay Merchant /
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

**Do not** put a per-order amount into `QRIS_STATIC` — the site inserts tag 54
itself and will replace whatever is there.

---

## 4. Daily use — `/android-admin`

1. Open `/android-admin`, paste `ADMIN_SECRET`, **MASUK**. It is kept in
   `sessionStorage`: closing the tab forgets it.
2. The **MENUNGGU** tab is the queue, oldest first, with the amount and the
   order age.
3. When the money shows up in the merchant app, press **SUDAH BAYAR → TERBITKAN
   KEY**. The key appears on the row and on the buyer's page within ~4 seconds.
4. **BATALKAN** closes an order that will never be paid.
5. **MINT** issues a key by hand — replacements, a refund, a friend.

Approving twice returns the first key, not a second licence. That is deliberate:
a double-click must not cost a sale.

The buyer's page polls `GET /api/android/order` every 4 seconds and stops when
the key arrives.

### Making approval exact

Two pending orders of the same plan have the same amount, so matching a payment
to an order is by time and amount. If that becomes ambiguous, set
`UNIQUE_AMOUNT`-style pricing by editing `site/functions/_lib/plans.js` and
adding a few rupiah per plan — or turn on `PAY_MODE=klikqris` so the gateway
settles orders itself.

---

## 5. Prices

`site/functions/_lib/plans.js`, one array. Ids are `a1d`, `a7d`, `a30d` — the
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

## 7. What this is not

- **Not a gateway integration by default.** Manual mode means a human presses a
  button. That is the mode that works with no merchant API credentials, and it
  is honest about it: the page says the key appears after the payment is
  confirmed.
- **Not a licence server.** The APK never calls the site. A key is a signed
  string; the phone checks the signature offline. The site can mint, but it
  cannot revoke, and it cannot see who is using what.
- **Not unbreakable.** `LICENSE_SECRET` is inside the APK, so anyone who
  decompiles it can mint keys. Offline licensing cannot be stronger than that.
  What it buys: an expiry, a device binding, and no network dependency during a
  match.
- **KV is eventually consistent** (up to ~60 s across regions). Fine for this
  flow — the buyer and the admin are reading the same record minutes apart. It
  is not fine as a lock, which is why nothing here uses it as one.

---

## 8. Tests

```bash
python tools/tests/run_all.py          # everything, including these five
python site/tests/test_site_key.py     # site keys -> the real License.java
python site/tests/test_site_qr.py      # QR encoder vs a reference implementation
python site/tests/test_site_qris.py    # static -> dynamic QRIS, CRC, tag order
python site/tests/test_site_api.py     # the four Functions against a fake KV
python site/tests/test_site_draw.py    # public/qr.js drawn into a fake canvas
```

What each one actually proves:

- **keys** — compiles `License.java`, reads `SECRET` out of the class, mints with
  `key.js`, feeds it to the Java verifier. Also asserts node and `keygen.py`
  produce the identical key for the same day, and that the device hash agrees
  across python, java and node.
- **qr** — every matrix, every mask, compared bit-for-bit with python's
  `qrcode` library; then rasterised and decoded back with OpenCV; then the
  shipping decoder tool reads a PNG made from the same matrix.
- **qris** — CRC-16/CCITT-FALSE against the canonical `123456789 -> 29B1`
  vector, tag 54 in the right position, merchant fields untouched, malformed
  payloads refused.
- **api** — the whole lifecycle: create, poll, wrong token, admin auth, approve,
  idempotent approve, device binding, cancel, expiry, rate limit, webhook
  signature, missing KV, missing QRIS, wrong methods.
- **draw** — `public/qr.js` runs in a VM with a recording 2D context; every
  rectangle it emits is mapped back to a module and compared with what the
  server sent, and the quiet zone is checked to be empty.

---

## 9. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `/api/android/plans` says `ready: false`, reason mentions `QRIS_STATIC` | not set, or bad CRC | re-run `qris_from_image.py`, paste the exact string |
| `no KV binding: bind one as DEMIOS_KV` | binding missing or named differently | add it, or the code will pick up `ORDERS` / `STORE_KV` / `KV` |
| admin says `wrong secret` | `ADMIN_SECRET` differs from what you typed | re-set the secret, redeploy, retype |
| keys rejected by the APK | `LICENSE_SECRET` ≠ `License.SECRET` | make them identical, rebuild the APK |
| QR shows but no wallet reads it | the buyer's app is old, or the screen is too dim | the code is dark-on-white at ~1024 px; turn the brightness up |
| order stuck at `pending` after payment | manual mode, nobody approved it | `/android-admin` → MENUNGGU → approve |
| `webhooks are disabled` | `WEBHOOK_SECRET` unset | that is the default and it is safe; set it only if a gateway calls you |
