#!/usr/bin/env python3
"""deploy.py - check this directory is deployable, then say how.

The site used to be an overlay that had to be copied into another project, with
one nav link stitched in. It is not any more: the Android product is the whole
site, so `public/` is the whole site and `functions/` is its API.

That removes the copy step and leaves the failure mode that actually bit us: a
page referencing an asset that is not in the deploy directory. `/android`
returned 200 and rendered as plain HTML for exactly that reason - `/store.css`
was not there. So this checks the tree the way a browser would:

  * every required file exists
  * every local asset a page references resolves inside public/
  * the API routes exist and export a handler
  * no page still points at a retired route

    python site/tools/deploy.py                 # check, then print the commands
    python site/tools/deploy.py --run           # check, then deploy to production
    python site/tools/deploy.py --branch next   # deploy a preview instead
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

SITE = pathlib.Path(__file__).resolve().parent.parent
PUBLIC = SITE / "public"
ROOT = SITE.parent
LICENSE_JAVA = (ROOT / "android" / "app" / "app" / "src" / "main"
                / "java" / "com" / "alokg" / "ffmenu" / "License.java")

REQUIRED_FILES = [
    "index.html", "admin.html", "site.css", "app.css", "app.js", "qr.js",
    "favicon.svg", "robots.txt", "sitemap.xml", "_redirects",
]
REQUIRED_ROUTES = ["plans", "order", "admin", "webhook"]
REQUIRED_LIB = ["http.js", "key.js", "orders.js", "pay.js", "plans.js", "qr.js", "qris.js"]

# Routes that were removed with the iOS product. A link to one of these is a
# dangling link, not a redirect: the _redirects file handles visitors.
RETIRED = ["/store", "/activate", "/install", "/panel"]

REF = re.compile(r'(?:href|src)="(/[^"#?]*)"')

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


def read_secret() -> str | None:
    if not LICENSE_JAVA.exists():
        return None
    m = re.search(r'SECRET\s*=\s*"([^"]+)"', LICENSE_JAVA.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description="check and deploy the DEMIOS site")
    ap.add_argument("--project", default="demios")
    ap.add_argument("--branch", default="main", help="main is production, anything else is a preview")
    ap.add_argument("--run", action="store_true", help="actually deploy, instead of printing")
    a = ap.parse_args()

    print(f"site    {SITE}")
    print()

    if not PUBLIC.is_dir():
        bad(f"{PUBLIC} does not exist")
        return 1

    # ---- 1. the files a deployment needs ---------------------------------
    for name in REQUIRED_FILES:
        p = PUBLIC / name
        if p.is_file():
            ok(f"public/{name}  ({p.stat().st_size} B)")
        else:
            bad(f"public/{name} is missing")

    for name in REQUIRED_ROUTES:
        p = SITE / "functions" / "api" / "android" / f"{name}.js"
        if not p.is_file():
            bad(f"functions/api/android/{name}.js is missing")
        elif "export function onRequest" not in p.read_text(encoding="utf-8"):
            bad(f"{name}.js does not export onRequest")
        else:
            ok(f"functions/api/android/{name}.js")

    for name in REQUIRED_LIB:
        p = SITE / "functions" / "_lib" / name
        if p.is_file():
            ok(f"functions/_lib/{name}")
        else:
            bad(f"functions/_lib/{name} is missing")

    # ---- 2. everything a page references must exist ----------------------
    print()
    referenced: set[str] = set()
    for page in sorted(PUBLIC.glob("*.html")):
        html = page.read_text(encoding="utf-8")
        refs = sorted(set(REF.findall(html)))
        broken: list[str] = []
        for ref in refs:
            if ref in RETIRED:
                bad(f"{page.name} links to {ref}, which was removed with the iOS product")
                broken.append(ref)
            elif ref.startswith("/api/"):
                continue
            elif ref == "/":
                # the site root, which Pages serves from index.html
                if (PUBLIC / "index.html").is_file():
                    referenced.add(ref)
                else:
                    bad(f"{page.name} links to / but there is no index.html")
                    broken.append(ref)
            elif (PUBLIC / ref.lstrip("/")).is_file() \
                    or (PUBLIC / (ref.lstrip("/") + ".html")).is_file():
                referenced.add(ref)
            else:
                bad(f"{page.name} references {ref}, which is not in public/")
                broken.append(ref)
        if refs and not broken:
            ok(f"{page.name}: {len(refs)} local references, all of them resolve")

    # ---- 3. the redirects the old URLs need ------------------------------
    print()
    red = (PUBLIC / "_redirects").read_text(encoding="utf-8")
    rules = [ln.split() for ln in red.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    froms = {r[0] for r in rules if len(r) >= 2}
    for old in ["/android", "/android-admin", *RETIRED]:
        if old in froms:
            ok(f"_redirects covers {old}")
        else:
            warn(f"_redirects does not cover {old} - it will 404")
    for r in rules:
        if len(r) < 2:
            bad(f"_redirects line is not 'from to [status]': {r}")
        elif r[0] == r[1]:
            bad(f"_redirects redirects {r[0]} to itself")
    ok(f"_redirects parses, {len(rules)} rules")

    # ---- 4. the deploy ---------------------------------------------------
    print()
    secret = read_secret()
    if secret:
        ok(f"LICENSE_SECRET {secret[:8]}...({len(secret)} chars) from License.java")
        print(f"       it must be set on the project to exactly: {secret}")
    else:
        bad("could not read SECRET out of License.java")

    print()
    print("--- deploy ---------------------------------------------------")
    cmd = ["npx", "wrangler", "pages", "deploy", "public",
           "--project-name", a.project, "--branch", a.branch]
    print("  " + " ".join(cmd))
    print()
    print("  secrets that must already be set (write-only, so set them if unsure):")
    print(f"    wrangler pages secret put LICENSE_SECRET --project-name {a.project}")
    print(f"    wrangler pages secret put ADMIN_SECRET   --project-name {a.project}")
    print(f"    wrangler pages secret put QRIS_STATIC    --project-name {a.project}")
    print()
    print("  then prove it, from the outside:")
    print(f"    python site/tools/smoke.py --base https://{a.project}.pages.dev")

    if a.run:
        print()
        print("--- running ------------------------------------------------")
        r = subprocess.run(cmd, cwd=str(SITE))
        if r.returncode != 0:
            bad(f"wrangler exited {r.returncode}")
    else:
        print()
        print("  (--run to actually deploy)")

    print()
    print(f"deploy check: {PASS} ok, {FAIL} failed, {WARN} warnings")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
