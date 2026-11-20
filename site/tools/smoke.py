#!/usr/bin/env python3
"""smoke.py - check a deployment actually works, from the outside.

A page that returns 200 can still be broken: if a stylesheet it references is
missing, the browser renders unstyled HTML and the page looks like a text file.
That happens for real when an overlay is deployed without the files it sits next
to - the preview branch carries the Android pages but not the project's
`store.css`, so `/android` came back 200 and looked like a document dump.

So this fetches the pages, extracts every local asset they reference, and
demands each one load. Then it drives the API.

    python site/tools/smoke.py --base https://android.demios.pages.dev
    python site/tools/smoke.py --base https://demios.pages.dev --admin-secret <S>

Exit code 0 when everything answers, 1 when anything does not, with the exact
path that failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

PAGES = [
    ("/android", True),
    ("/android-admin", True),
]

# What the page needs in order to render versus what it merely links to.
# A missing stylesheet or script makes the page broken; a missing nav target is
# a dead link. Conflating them makes the tool cry wolf on an overlay-only
# deployment, where the wider site (store, activate, install) is simply absent.
ASSET_SUFFIXES = (".css", ".js", ".mjs", ".svg", ".png", ".jpg", ".jpeg", ".gif",
                  ".ico", ".webmanifest", ".json", ".woff", ".woff2", ".txt", ".xml")

# Assets the wider site serves, which an overlay deployment will not have and
# which the page does not need in order to render.
OPTIONAL = {"/store.js", "/api/buy"}


def is_asset(path: str) -> bool:
    return path.lower().endswith(ASSET_SUFFIXES)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PASS = 0
FAIL = 0
WARN = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def warn(msg: str) -> None:
    global WARN
    WARN += 1
    print(f"[warn] {msg}")


def fetch(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "*/*")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", "") if e.headers else "", e.read()
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e).encode()


def local_assets(html: str) -> list[str]:
    out = []
    for m in re.finditer(r'(?:href|src)="(/[^"#?]*)"', html):
        p = m.group(1)
        if p not in out:
            out.append(p)
    return out


def script_order(html: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'<script[^>]*src="(/[^"]+)"', html)]


def main() -> int:
    ap = argparse.ArgumentParser(description="smoke-test a demios deployment")
    ap.add_argument("--base", required=True, help="e.g. https://android.demios.pages.dev")
    ap.add_argument("--admin-secret", default=None, help="also drives the admin endpoints")
    ap.add_argument("--skip-api", action="store_true", help="only check pages and assets")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    print(f"base {base}")
    print()

    # ---- pages and the assets they reference ------------------------------
    for path, required in PAGES:
        status, ctype, raw = fetch(base + path)
        if status != 200:
            bad(f"GET {path} -> {status} {raw[:80]!r}")
            if required:
                continue
        else:
            ok(f"GET {path} -> 200  {ctype}  {len(raw)} B")

        if "text/html" not in ctype:
            bad(f"{path} is not html ({ctype})")
            continue

        html = raw.decode("utf-8", "replace")
        assets = local_assets(html)

        missing_critical = []
        for asset in assets:
            astatus, actype, _ = fetch(base + asset)
            if astatus == 200:
                ok(f"  {asset} -> 200  {actype}")
            elif asset in OPTIONAL:
                warn(f"  {asset} -> {astatus}  (asset of the wider site, not of the overlay)")
            elif is_asset(asset):
                bad(f"  {asset} -> {astatus}  referenced by {path} and missing")
                missing_critical.append(asset)
            else:
                warn(f"  {asset} -> {astatus}  (a link to a page of the wider site,"
                     f" absent in an overlay-only deployment)")

        if missing_critical:
            print(f"       {path} will render unstyled: " + ", ".join(missing_critical))

        # A stylesheet is not decoration here: store.css carries the layout, and
        # the classes below are the ones the page cannot do without. Checking the
        # status alone would accept a 200 that is an error page or the wrong file.
        if "/store.css" in assets:
            st, ctype, body = fetch(base + "/store.css")
            if st != 200:
                bad(f"  /store.css -> {st}: the page has no layout (this is the"
                    f" 'plain HTML' symptom)")
            else:
                css = body.decode("utf-8", "replace")
                needed = [".wrap", ".nav", ".hero", ".pcard", ".activate"]
                absent = [c for c in needed if c not in css]
                if absent:
                    bad(f"  /store.css loads but does not define {' '.join(absent)}"
                        f" - the page will render wrong")
                else:
                    ok(f"  /store.css loads and defines {', '.join(needed)}")

        scripts = script_order(html)
        if "/qr.js" in scripts and "/android.js" in scripts:
            if scripts.index("/qr.js") < scripts.index("/android.js"):
                ok("  /qr.js is loaded before /android.js")
            else:
                bad("  /android.js loads before /qr.js - the drawer will not exist yet")

    if a.skip_api:
        print()
        print(f"smoke: {PASS} ok, {FAIL} failed, {WARN} warnings")
        return 1 if FAIL else 0

    # ---- api ---------------------------------------------------------------
    print()
    status, ctype, raw = fetch(base + "/api/android/plans")
    if status != 200:
        bad(f"GET /api/android/plans -> {status} {raw[:120]!r}")
    else:
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            bad(f"/api/android/plans is not json: {raw[:120]!r}")
            j = {}
        if j.get("ok"):
            ok(f"GET /api/android/plans -> 200, {len(j.get('plans', []))} plans, "
               f"ready={j.get('ready')} gateway={j.get('gateway')}")
            if not j.get("ready"):
                warn(f"payment is not configured: {j.get('reason')}")
        else:
            bad(f"/api/android/plans answered {j}")

    # create -> poll -> cancel, so the check leaves nothing behind
    order = None
    status, _, raw = fetch(base + "/api/android/order", "POST",
                           json.dumps({"plan": "a1d"}).encode(),
                           {"content-type": "application/json"})
    if status != 200:
        bad(f"POST /api/android/order -> {status} {raw[:120]!r}")
    else:
        order = json.loads(raw)
        ok(f"POST /api/android/order -> {order.get('orderId')} "
           f"amount {order.get('amount')} (price {order.get('price')})")
        if order.get("configured"):
            ok("  payment is configured, a QR came back")
        else:
            warn(f"  no QR: {order.get('note')}")

        st, _, raw2 = fetch(f"{base}/api/android/order?id={order['orderId']}"
                            f"&token={order['token']}")
        if st == 200 and json.loads(raw2).get("status") == "pending":
            ok("  polling the order works and it is pending")
        else:
            bad(f"  polling failed: {st} {raw2[:120]!r}")

        st, _, _ = fetch(f"{base}/api/android/order?id={order['orderId']}"
                         "&token=" + "0" * 24)
        if st == 404:
            ok("  a wrong token is a 404")
        else:
            bad(f"  a wrong token returned {st}, expected 404")

    # ---- admin -------------------------------------------------------------
    if a.admin_secret:
        print()
        hdr = {"x-admin-secret": a.admin_secret, "content-type": "application/json"}

        def admin(payload: dict):
            return fetch(base + "/api/android/admin", "POST",
                         json.dumps(payload).encode(), hdr)

        st, _, raw = admin({"action": "ping"})
        if st == 200:
            p = json.loads(raw)
            ok(f"admin ping: kv={p.get('kv')} qris={p.get('qris')} "
               f"licenseSecret={p.get('licenseSecret')} payMode={p.get('payMode')}")
            if p.get("kv") != "bound":
                bad(f"  KV is not bound: {p.get('kv')}")
            if p.get("licenseSecret") != "set":
                bad(f"  LICENSE_SECRET is not usable: {p.get('licenseSecret')}")
        else:
            bad(f"admin ping -> {st} {raw[:120]!r}")

        st, _, raw = fetch(base + "/api/android/admin", "POST",
                           json.dumps({"action": "list"}).encode(),
                           {"content-type": "application/json"})
        if st == 401:
            ok("admin without a secret is refused")
        else:
            bad(f"admin without a secret returned {st}, expected 401")

        if order:
            st, _, raw = admin({"action": "cancel", "orderId": order["orderId"],
                                "note": "smoke test"})
            if st == 200 and json.loads(raw).get("status") == "cancelled":
                ok("  the smoke test's own order was cancelled, nothing left behind")
            else:
                warn(f"  could not cancel the test order: {st} {raw[:80]!r}")

    print()
    print(f"smoke: {PASS} ok, {FAIL} failed, {WARN} warnings")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
