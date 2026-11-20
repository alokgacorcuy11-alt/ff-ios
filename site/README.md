# DEMIOS - the Android storefront

One product: the Android Free Fire build. A buyer picks a plan on the site, pays
with QRIS, and gets a key the APK accepts offline. Nothing here talks to the APK
at runtime; the only shared thing is the key format, which
`functions/_lib/key.js` mints and `License.java` verifies on the phone.

This directory **is** the Pages project: `public/` is the site and `functions/`
is its API. It used to be an overlay that had to be copied into another project
alongside an iOS store; that is gone, and so is the copy step.

```
site/
  public/
    index.html        /            the storefront
    admin.html        /admin       the back office
    site.css          the HUD. the site owns it, so nothing else can restyle it
    app.css           only the parts site.css does not already have
    app.js            the storefront
    qr.js             draws the QR canvas from packed bits
    favicon.svg
    robots.txt        disallows /admin and /api/
    sitemap.xml
    _redirects        keeps the old URLs working (see section 10)
  functions/
    _lib/
      key.js          key minting - the same 24-char format as keygen.py
      qris.js         static QRIS -> dynamic QRIS with the order's amount
      qr.js           QR encoder (byte mode, EC level M, v1-v20), no dependencies
      orders.js       order records in KV, token auth, mint-on-approve
      pay.js          manual QRIS (default) / klikqris adapter / webhook signature
      plans.js        the price table
      http.js         json/fail/allow, CSPRNG ids, constant-time compare
    api/android/
      plans.js        GET  - price list + whether payment is configured
      order.js        POST - create an order;  GET - poll it
      admin.js        POST - approve, cancel, list, mint
      webhook.js      POST - gateway callback (disabled without WEBHOOK_SECRET)
  tools/
    deploy.py         check the tree is deployable, print (or run) the deploy
    smoke.py          check a deployment from the outside, asset by asset
    mint.mjs          mint/verify a key from the command line
    pay_probe.mjs     ask a live gateway to make one QR and report the shape
    qris_from_image.py  decode a QRIS screenshot into the payload string
    qris_static.mjs   recover a merchant template from a dynamic payload
    qr_probe.mjs, qris_probe.mjs   dumps used by the tests
  tests/              9 suites, wired into ../tools/tests/run_all.py
  wrangler.toml       the project: name, output dir, KV binding, vars
```

The API keeps its `/api/android/` prefix even though it is the only product.
Renaming it would touch every route, every test and every document to remove six
characters that no buyer ever sees.

---

## 1. Deploy

```bash
cd site
python tools/deploy.py                    # check, then print the commands
python tools/deploy.py --run              # check, then deploy to production
python tools/deploy.py --run --branch next   # a preview instead
```

`deploy.py` is a check first and a deploy second. It verifies every required
file, resolves every local asset each page references, confirms the redirects
cover the old URLs, and prints the `LICENSE_SECRET` read out of `License.java` so
the two sides cannot be set to different strings by hand.

### Secrets

Set once, on the project. They are write-only, so setting them again is the only
way to be sure:

```bash
wrangler pages secret put LICENSE_SECRET --project-name demios
wrangler pages secret put ADMIN_SECRET   --project-name demios
wrangler pages secret put QRIS_STATIC    --project-name demios
```

### Then prove it

```bash
python site/tools/smoke.py --base https://demios.pages.dev --admin-secret <S>
```

It fetches the pages, follows every local asset, demands each one load, checks
that `site.css` defines the classes the page needs, confirms the retired URLs
redirect, then drives create -> poll -> wrong token -> admin ping and cancels
the order it created. Exit 1 names the path that failed.

This exists because of a real failure: a page whose stylesheet is missing returns
**200** and renders as unstyled HTML. A status check passes that; this does not.

---

## 2. Environment

| name | kind | required | what it does |
|---|---|---|---|
| `LICENSE_SECRET` | secret | **yes** | signs keys. Must be byte-identical to `License.SECRET` in the APK. |
| `ADMIN_SECRET` | secret | **yes** | unlocks `/admin` and `POST /api/android/admin`. |
| `QRIS_STATIC` | secret | yes for manual | the seller's static QRIS payload (section 3). |
| `DEMIOS_KV` | binding | **yes** | order storage. |
| `PAY_MODE` | var | no | `manual` (default) or `klikqris`. |
| `ORDER_RATE_PER_HOUR` | var | no | orders per IP per hour, default `30`, `0` disables. |
| `WEBHOOK_SECRET` | secret | no | enables `POST /api/android/webhook`. Without it the endpoint refuses everything. |
| `KLIKQRIS_CREATE_URL`, `KLIKQRIS_KEY` | secret | only for `klikqris` | see section 6. |
| `KLIKQRIS_MERCHANT_ID`, `KLIKQRIS_AUTH_HEADER`, `KLIKQRIS_AUTH_PREFIX`, `KLIKQRIS_BODY` | var | no | the account's own request shape. |
| `KLIKQRIS_QR_FIELDS`, `KLIKQRIS_URL_FIELDS` | var | no | dotted paths into the response, comma separated. |
| `QRIS_DECIMALS` | var | no | `2` (default) writes `140000.00`, which is what the observed acquirer writes; `0` writes whole rupiah. |

Preview deployments get their own environment and do **not** inherit these.

### What is left over from the iOS product

These are still set on the project and nothing reads them any more:
`KEY_SECRET`, `ADMIN_KEYS`, `INSTALL_SECRET`, `CA_DER`, `CRON_SECRET`,
`PROXY_HOST`, `PROXY_PORT`, and the `KEYS` KV binding.

They are left alone deliberately. Secret values cannot be read back once
written, and the KV namespace holds records that cannot be recovered if it is
deleted. Remove them with `wrangler pages secret delete` and
`wrangler kv namespace delete` only when you are sure the iOS build is not
coming back.

`KLIKQRIS_API_KEY` and `KLIKQRIS_MERCHANT_ID` are the exception: `KLIKQRIS_KEY`
falls back to `KLIKQRIS_API_KEY`, so the gateway credential that is already there
is used as-is.

---

## 3. `QRIS_STATIC`

The seller's static QRIS string - the one behind the QR in GoPay Merchant /
DANA Bis / BRI / whatever. A static code has no amount, so every buyer would
type it themselves and nothing could be matched to an order.

The site turns it into a **dynamic** code per order: tag 01 becomes `12`, tag 54
(the amount) is appended, and tag 63's CRC is recomputed. Same merchant account,
fixed amount, one scan.

Get the string from an image:

```bash
pip install opencv-python-headless
python site/tools/qris_from_image.py qris.png
# 0002010102112665...63048160
```

**Do not** put a per-order amount into `QRIS_STATIC` - the site inserts tag 54
itself and will replace whatever is there.

### If you only have a dynamic code

A static code and a dynamic one for the same merchant differ in exactly two
fields: tag 01 (`11` vs `12`) and tag 54 (absent vs the amount). So any dynamic
QRIS that merchant issues contains the template:

```bash
node site/tools/qris_static.mjs --image qris_from_your_own_store.png
# amount in the payload : 40313.00  (2 decimals)
# template              : 00020101021126...
# rebuild with the amount: byte-identical, CRC included
```

It does not trust itself: after recovering the template it puts the amount back
and requires the bytes to come out identical, CRC included. If they do not, it
prints which tag moved and exits 1. That round trip is why a recovered template
can be used with confidence - it is the acquirer's own arithmetic, not a guess
about which merchant fields to keep.

Keep the result out of the repository. A template identifies the merchant
underneath it.

---

## 4. Daily use - `/admin`

1. Open `/admin`, paste `ADMIN_SECRET`, **MASUK**. It is kept in
   `sessionStorage`: closing the tab forgets it.
2. **MENUNGGU** is the queue, with the amount and the order's age.
3. When the money shows up in the merchant app, press **SUDAH BAYAR -> TERBITKAN
   KEY**. The key appears on the row and on the buyer's page within ~4 seconds.
4. **BATALKAN** closes an order that will never be paid.
5. **MINT** issues a key by hand - replacements, a refund, a friend.

Approving twice returns the first key, not a second licence: a double-click must
not cost a sale. If a buyer loses their browser storage, their key is still on
the order row - find the order id and read it off.

### Matching a payment to an order

Every order gets its own amount: the plan price plus a three-digit code (`a:seq`
in KV, 100-999, advancing per order). A 140.000 plan is charged as 140.101, then
140.102, and so on. The admin sees the amount in the merchant app and in
`/admin`, and the two match without guessing from a timestamp.

The checkout shows the base price and the payable amount separately, and the note
says the last three digits are the order's own code, so the odd number is
explained rather than suspicious.

---

## 5. Prices

`site/functions/_lib/plans.js`, one array: `a1d` 40.000, `a7d` 140.000, `a30d`
300.000. `tier` goes into the key's payload nibble (`1` basic, `2` pro);
`License.java` reports it but nothing gates on it yet.

---

## 6. Payment modes

### manual (default, working today)

No gateway account, no callback URL, no API key. The seller's static QRIS is
turned into a dynamic code for the order's amount, the buyer scans it, and the
seller presses one button. A human is in the loop and the page says so.

### klikqris

`POST $KLIKQRIS_CREATE_URL` with `x-api-key: $KLIKQRIS_KEY` and
`{ "amount": 140000, "ref_id": "AND-7K2M9Q" }`. The response is searched for a QR
string or a payment URL at a list of known paths, then at whatever
`KLIKQRIS_QR_FIELDS` / `KLIKQRIS_URL_FIELDS` say.

Nothing about the account is assumed. The endpoint, the credential, the header
name, the auth prefix and the request body all come from env, so a gateway that
wants `Authorization: Bearer <key>` and `{"nominal":"140000"}` is a set of
variables, not a patch:

```bash
KLIKQRIS_AUTH_HEADER=Authorization
KLIKQRIS_AUTH_PREFIX="Bearer "
KLIKQRIS_BODY='{"nominal":"{{amount}}","reff":"{{ref_id}}","merchant":"{{merchant_id}}"}'
```

Do not guess the account's response shape. Ask it:

```bash
node site/tools/pay_probe.mjs --url https://api.gateway/create --key sk_test_...
# [ok] production would accept this response.
# or:
# [FAIL] no known field carries the QR or the payment URL.
#        KLIKQRIS_QR_FIELDS=result.qris_content
```

The probe runs the **same** `extract()`, `gatewayConfig()` and `requestBody()`
that production runs, so a green probe means the checkout will work - not that a
second implementation agrees with itself. It creates no order and stores nothing.

Exit codes: `0` accepted, `2` nothing matched, `3` unreachable or HTTP error.

### webhook

`POST /api/android/webhook` with
`signature = hex(HMAC-SHA256(WEBHOOK_SECRET, orderId + ":" + status + ":" + amount))`.
Disabled unless `WEBHOOK_SECRET` is set: an unauthenticated endpoint that marks
orders paid is a free-licence endpoint.

---

## 7. The order lifecycle

```
POST /api/android/order { plan: "a7d", device?: "<android id>" }
  -> { orderId: "AND-7K2M9Q", token: "<24 hex>", price: 140000, amount: 140101,
       unique: 101, qr: { size: 61, bits: "<base64>" }, expiresAt, note }

GET  /api/android/order?id=AND-7K2M9Q&token=<24 hex>
  -> { status: "pending" | "paid" | "cancelled" | "expired", key? }
```

- The token is returned **once**, at creation. The order id is short enough to
  read over the phone, so the token is what makes it unguessable. Wrong token =
  404, never a key.
- A pending order lives 30 minutes. Expiry is computed on read, not written.
- `device` is optional. Filled in, the key binds to that Android ID and works
  nowhere else; empty, it works on any phone until it expires.
- Keys are never the same string twice. The payload carries a 24-bit nonce from
  the CSPRNG; `mint()` refuses to run without one, because without it every key
  for one plan on one day is byte-identical and one leaked key would open the
  product for everyone buying that plan that day.

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
  flow. It is not fine as a lock, which is why nothing here uses it as one.

---

## 9. Tests

```bash
python tools/tests/run_all.py            # everything, including these nine
python site/tests/test_site_key.py       # site keys -> the real License.java
python site/tests/test_site_qr.py        # QR encoder vs a reference implementation
python site/tests/test_site_qris.py      # static -> dynamic QRIS, CRC, tag order
python site/tests/test_site_api.py       # the four Functions against a fake KV
python site/tests/test_site_draw.py      # public/qr.js drawn into a fake canvas
python site/tests/test_site_markup.py    # page ids, assets, and no iOS wording
python site/tests/test_site_pay.py       # the gateway adapter, 6 response shapes
python site/tests/test_site_deploy.py    # the deploy check, against broken trees
python site/tests/test_site_smoke.py     # the smoke tool, against broken sites
```

What the less obvious ones prove:

- **markup** - every id a script reaches for exists, every local asset exists,
  nothing shipping mentions iOS, nothing links to a retired route, and the pages
  call only endpoints that exist.
- **deploy** - the real tree passes; then one thing at a time is broken: no
  `site.css`, a dangling asset reference, a link to `/store`, a self-redirect, a
  missing API route, a missing `_lib` module. Each must fail and name itself.
- **smoke** - three loopback sites with Pages' clean URLs and `_redirects`. A
  missing `site.css` must exit 1; the real tree must exit 0; a stylesheet that
  returns 200 and defines nothing must still fail; a missing favicon must be
  reported; an unreachable base must exit 1 rather than crash. A monitoring tool
  that has never been seen to fail is not a tool.

One check needs a built APK, so it is a CI step rather than part of the suite:

```bash
python tools/check_apk_secret.py --apk dist/ffmenu.apk
```

It reads the secret out of `License.java` and looks for it inside the APK's dex,
and fails the build if the current secret is missing or the placeholder is
present. Without it, changing the site secret without rebuilding the APK ships a
product where every key is rejected - and both sides look correct on their own.

---

## 10. What was removed, and how to undo it

The site used to sell an iOS build as well. These were removed:

| removed | was |
|---|---|
| `/`, `/store`, `/activate`, `/install`, `/panel` | the iOS landing, store, activation, profile install, and the seller's console |
| `/hud.css`, `/store.css`, `/store.js`, `/activate.css`, `/activate.js`, `/install.js`, `/panel.css`, `/panel.js` | their assets |
| `/api/buy`, `/api/order`, `/api/activate`, `/api/install`, `/api/admin`, `/api/config` | their Functions |

`site.css` is the old `store.css`, renamed and now owned by this project.

`_redirects` forwards all six retired URLs to `/`, so old bookmarks land on the
Android store instead of a dead end. Change the status to `410` in
`public/_redirects` if you would rather signal that they are gone.

**Two things to know before you consider it finished:**

1. An iOS client already installed in the field may still call `/api/config` for
   its kill switch and feature flags. That endpoint is gone now and will 404.
   Whatever it does on failure - keep going, or fall back to defaults - is what
   it will do.
2. Undoing this is a redeploy of the previous deployment, not a git revert: the
   old files were never in this repository. Cloudflare keeps every deployment;
   roll back from the dashboard, or redeploy from a copy of the old tree.

---

## 11. Verified on the live deployment

Driven end to end on the real runtime, not just unit-tested:

| step | result |
|---|---|
| `GET /`, `/admin` | 200, `text/html` |
| `GET /api/android/plans` | 200, 3 plans, `ready:true`, gateway `manual` |
| `POST /api/android/order {"plan":"a7d"}` | 200, `AND-XXXXXX`, 24-hex token, `price 140000`, `amount 140101` |
| create -> approve -> poll | `paid` and the key, within a poll |
| wrong token | 404 `no such order` |
| approve twice | the identical key - no second licence |
| 20 approvals in a row | 20 distinct keys, all valid |
| `admin` with no secret / wrong secret | 401 |
| unsigned webhook | 503, fails closed |
| the QR the page received, rasterised and decoded with OpenCV | valid QRIS payload, CRC valid |
| that payload's tag 54 | `140101.00`, exactly the order's amount |
| that payload's merchant fields (26, 51, 59, 60) | identical to `QRIS_STATIC` |
| the minted key in the real `License.class`, in `keygen.py`, in `key.js` | accepted by all three |

Two bugs were found by doing that rather than by reading the code:

- **Duplicate keys.** Two orders for the same plan produced the *same* key,
  because the payload was a pure function of (tier, expiry, device). One leaked
  key would have opened the product for every buyer of that plan that day.
- **Duplicate amounts.** Two pending orders of one plan owed the same rupiah, so
  approval was matched by timestamp. Every order now carries its own three-digit
  code.

---

## 12. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| the page renders as plain HTML, links in blue, no layout | `site.css` is not being served where the page expects it | run `smoke.py`; `site.css` must be in the deploy directory |
| `/api/android/plans` says `ready: false`, reason mentions `QRIS_STATIC` | not set, or bad CRC | re-run `qris_from_image.py`, set the exact string |
| `no KV binding: bind one as DEMIOS_KV` | binding missing | add it; `DEMIOS_KV`, `ORDERS`, `STORE_KV` and `KV` are all accepted |
| admin says `wrong secret` | `ADMIN_SECRET` differs from what you typed | re-set the secret, redeploy, retype |
| keys rejected by the APK | `LICENSE_SECRET` != `License.SECRET` | make them identical, rebuild the APK |
| `gateway returned no qr_string and no url` | the account's response shape is not in the list | `node site/tools/pay_probe.mjs ...`, then set `KLIKQRIS_QR_FIELDS` |
| QR shows but no wallet reads it | the buyer's app is old, or the screen is too dim | the code is dark-on-white at ~1024 px; turn the brightness up |
| order stuck at `pending` after payment | manual mode, nobody approved it | `/admin` -> MENUNGGU -> approve |
| an old link 404s | it was removed with the iOS product and is not in `_redirects` | add it to `public/_redirects` |
| `webhooks are disabled` | `WEBHOOK_SECRET` unset | that is the default and it is safe |
