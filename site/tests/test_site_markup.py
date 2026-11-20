#!/usr/bin/env python3
"""test_site_markup.py - the pages and their scripts agree, and iOS is gone.

A typo in an element id is invisible: getElementById returns null, the handler
is never attached, and the page looks fine until someone clicks the button. This
cross-checks every id a script reaches for against the ids the page defines, and
every local asset a page asks for against the files in public/.

It also pins the shape of the product: the site sells Android. Nothing shipping
may mention iOS, link to a route that was retired with it, or call an endpoint
that no longer exists.

  python site/tests/test_site_markup.py
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent
PUBLIC = SITE / "public"

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def check(cond: bool, msg: str, extra: str = "") -> None:
    (ok if cond else bad)(msg + (f"  {extra}" if extra else ""))


ID_RE = re.compile(r'\bid="([^"]+)"')
GET_RE = re.compile(r"""getElementById\(['"]([^'"]+)['"]\)""")
DOLLAR_RE = re.compile(r"""\$\(['"]([^'"]+)['"]\)""")
REF_RE = re.compile(r"""(?:src|href)="(/[^"]*)\"""")

PAGES = {
    "index.html": "app.js",
    "admin.html": None,   # its script is inline
    "404.html": None,     # static, no script
}

# Pages that existed for the iOS product and must not come back by accident.
RETIRED = ["/store", "/activate", "/install", "/panel"]

SHIPPING = [p for p in PUBLIC.iterdir() if p.suffix in {".html", ".js", ".css"}]


def ids_of(html: str) -> set[str]:
    return set(ID_RE.findall(html))


def wanted_of(js: str) -> set[str]:
    return set(GET_RE.findall(js)) | set(DOLLAR_RE.findall(js))


def inline_scripts(html: str) -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


def main() -> int:
    print(f"site {SITE}")
    print()

    for name, script_name in PAGES.items():
        path = PUBLIC / name
        if not path.exists():
            bad(f"{name} is missing")
            continue
        html = path.read_text(encoding="utf-8")
        check(True, f"{name} exists ({len(html)} B)")
        check('lang="id"' in html, f"{name} declares its language")
        check("<title>" in html, f"{name} has a title")
        check('name="viewport"' in html, f"{name} has a viewport meta")
        check('href="/site.css"' in html, f"{name} uses the site stylesheet")
        check('href="/app.css"' in html, f"{name} uses the page deltas")

        defined = ids_of(html)
        has_script = bool(script_name) or "<script>" in html
        if has_script:
            check(len(defined) >= 5, f"{name} defines {len(defined)} ids")
        else:
            check(True, f"{name} is static, no ids needed ({len(defined)} found)")

        scripts = inline_scripts(html)
        if script_name:
            sp = PUBLIC / script_name
            if sp.exists():
                scripts += sp.read_text(encoding="utf-8")
            else:
                bad(f"{name} loads {script_name}, which does not exist")

        wanted = wanted_of(scripts)
        missing = sorted(w for w in wanted if w not in defined)
        check(not missing, f"{name}: every id the script asks for exists"
              + (f"  missing {missing}" if missing else f"  ({len(wanted)} ids)"))

        refs = sorted(set(REF_RE.findall(html)))
        broken, retired = [], []
        for ref in refs:
            if ref.startswith("/api/"):
                continue
            if ref in RETIRED:
                retired.append(ref)
                continue
            target = PUBLIC / ref.lstrip("/")
            if ref == "/":
                target = PUBLIC / "index.html"
            elif not target.exists():
                alt = PUBLIC / (ref.lstrip("/") + ".html")
                target = alt
            if not target.exists():
                broken.append(ref)
        check(not broken, f"{name}: every local asset exists"
              + (f"  missing {broken}" if broken else f"  ({len(refs)} refs)"))
        check(not retired, f"{name}: nothing links to a retired iOS route"
              + (f"  {retired}" if retired else ""))

    print()

    # ---- the product is Android only --------------------------------------
    offenders = []
    for p in sorted(SHIPPING):
        body = p.read_text(encoding="utf-8")
        for word in ("iOS", "ios ", "dylib", "il2cpp", "profile install"):
            if word in body:
                offenders.append(f"{p.name}:{word}")
    check(not offenders, "nothing shipping mentions iOS"
          + (f"  {offenders}" if offenders else ""))

    api_calls: set[str] = set()
    for p in sorted(PUBLIC.glob("*.js")) + [PUBLIC / "index.html", PUBLIC / "admin.html"]:
        api_calls |= set(re.findall(r"""['"](/api/[a-z/]+)['"]""", p.read_text(encoding="utf-8")))
    allowed = {"/api/android/plans", "/api/android/order", "/api/android/admin"}
    check(api_calls <= allowed, "the pages call only endpoints that exist",
          "found " + ", ".join(sorted(api_calls)))
    check("/api/buy" not in api_calls and "/api/order" not in api_calls,
          "the retired iOS endpoints are not called")

    # ---- the drawer must be loaded before the script that uses it ---------
    a = (PUBLIC / "index.html").read_text(encoding="utf-8")
    check("/qr.js" in a and a.index("/qr.js") < a.index("/app.js"),
          "index.html loads /qr.js before /app.js")

    # ---- _redirects -------------------------------------------------------
    red = (PUBLIC / "_redirects").read_text(encoding="utf-8")
    for old in ["/android", "/android-admin", *RETIRED]:
        check(re.search(rf"^{re.escape(old)}\s+\S+", red, re.M) is not None,
              f"_redirects forwards {old}")

    # ---- no placeholders ship --------------------------------------------
    suspects = ["TODO", "FIXME", "XXX", "lorem ipsum", "implement here"]
    hits = [f"{p.name}:{s}" for p in SHIPPING for s in suspects
            if s.lower() in p.read_text(encoding="utf-8").lower()]
    check(not hits, "no TODO/FIXME/placeholder in the shipping files"
          + (f"  {hits}" if hits else ""))

    # ---- the api surface the pages use is the one that exists -------------
    for route in ("plans", "order", "admin", "webhook"):
        f = SITE / "functions" / "api" / "android" / f"{route}.js"
        body = f.read_text(encoding="utf-8") if f.exists() else ""
        check(f.exists() and "export function onRequest" in body,
              f"functions/api/android/{route}.js exports a Pages handler")

    print()
    print(f"markup: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
