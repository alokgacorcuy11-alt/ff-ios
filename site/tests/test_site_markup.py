#!/usr/bin/env python3
"""test_site_markup.py - the pages and their scripts agree.

A typo in an element id is invisible: getElementById returns null, the handler
is never attached, and the page looks fine until someone clicks the button. This
cross-checks every id a script reaches for against the ids the page defines, and
every local asset a page asks for against the files in public/.

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
ATTR_RE = re.compile(r"""(?:src|href)="(/[^"]*)\"""")


def ids_of(html: str) -> set[str]:
    return set(ID_RE.findall(html))


def wanted_of(js: str) -> set[str]:
    return set(GET_RE.findall(js)) | set(DOLLAR_RE.findall(js))


def inline_scripts(html: str) -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


def main() -> int:
    pages = {
        "android.html": (PUBLIC / "android.html", PUBLIC / "android.js"),
        "android-admin.html": (PUBLIC / "android-admin.html", None),
    }

    # assets that live on the deployed site but not in this folder
    REMOTE = {"/store.css", "/favicon.svg", "/store", "/store.js", "/android"}

    for name, (path, script_path) in pages.items():
        if not path.exists():
            bad(f"{name} is missing")
            continue
        html = path.read_text(encoding="utf-8")
        check(True, f"{name} exists ({len(html)} bytes)")

        check('lang="id"' in html, f"{name} declares its language")
        check("<title>" in html and "</title>" in html, f"{name} has a title")
        check('name="viewport"' in html, f"{name} has a viewport meta")
        check("/store.css" in html, f"{name} reuses the site's stylesheet")

        defined = ids_of(html)
        check(len(defined) >= 5, f"{name} defines {len(defined)} ids")

        scripts = ""
        if script_path and script_path.exists():
            scripts += script_path.read_text(encoding="utf-8")
        scripts += inline_scripts(html)

        wanted = wanted_of(scripts)
        missing = sorted(w for w in wanted if w not in defined)
        check(not missing, f"{name}: every id the script asks for exists"
              + (f"  missing {missing}" if missing else f"  ({len(wanted)} ids)"))

        # local assets must exist on disk; Pages serves /name from name.html
        local = {a for a in ATTR_RE.findall(html) if a not in REMOTE}
        absent = []
        for a in sorted(local):
            target = PUBLIC / a.lstrip("/")
            if not target.exists() and not (PUBLIC / (a.lstrip("/") + ".html")).exists():
                absent.append(a)
        check(not absent, f"{name}: every local asset exists"
              + (f"  missing {absent}" if absent else f"  ({len(local)} assets)"))

        check("<div" not in html or html.count("<div") == html.count("</div>") + html.count("/>"),
              f"{name}: div tags are balanced")

    # the drawer the page loads must exist and be referenced before android.js
    a = (PUBLIC / "android.html").read_text(encoding="utf-8")
    check('/qr.js' in a and a.index('/qr.js') < a.index('/android.js'),
          "android.html loads /qr.js before /android.js")

    # no placeholders in anything that ships
    suspects = ["TODO", "FIXME", "XXX", "lorem ipsum", "implement here"]
    offenders = []
    for p in sorted(PUBLIC.iterdir()):
        if p.suffix not in {".html", ".js", ".css"}:
            continue
        body = p.read_text(encoding="utf-8")
        for s in suspects:
            if s.lower() in body.lower():
                offenders.append(f"{p.name}:{s}")
    check(not offenders, "no TODO/FIXME/placeholder in the shipping files"
          + (f"  {offenders}" if offenders else ""))

    # the api surface the page calls must be the one the functions expose
    js = (PUBLIC / "android.js").read_text(encoding="utf-8")
    calls = set(re.findall(r"""['"](/api/android/[a-z]+)""", js))
    check(calls == {"/api/android/plans", "/api/android/order"},
          "the storefront calls only the two endpoints it needs", str(sorted(calls)))

    for route in ("plans", "order", "admin", "webhook"):
        f = SITE / "functions" / "api" / "android" / f"{route}.js"
        check(f.exists(), f"functions/api/android/{route}.js exists")
        body = f.read_text(encoding="utf-8") if f.exists() else ""
        check("export function onRequest" in body,
              f"{route}.js exports a Pages handler")

    admin = (PUBLIC / "android-admin.html").read_text(encoding="utf-8")
    for action in ("ping", "list", "paid", "cancel", "mint"):
        check(f"'{action}'" in admin or f'"{action}"' in admin,
              f"the admin page uses the '{action}' action")

    print()
    print(f"markup: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
