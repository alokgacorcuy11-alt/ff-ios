#!/usr/bin/env python3
"""test_site_install.py - the installer, run against a fake project tree.

The failure this is guarding against is expensive: the overlay is five files
that must sit next to the real site. Copy them into the wrong place, or deploy
site/public as if it were the whole site, and the iOS storefront disappears.

So the installer is exercised for real - a temporary project with a store.html
that looks like the live one, a functions/api that already exists, then:

  * dry run writes nothing
  * the real run copies the overlay in and adds exactly one nav link
  * the link lands inside <nav>, before the existing buttons
  * a second run changes nothing (no duplicate link, no rewritten files)
  * it refuses a directory that is not a site
  * it refuses to be pointed at the overlay itself
  * the secret it prints is the one in License.java

  python site/tests/test_site_install.py
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent
ROOT = SITE.parent
INSTALL = SITE / "tools" / "install.py"
LICENSE_JAVA = (ROOT / "android" / "app" / "app" / "src" / "main"
                / "java" / "com" / "alokg" / "ffmenu" / "License.java")

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


# A stand-in for the real store.html: same nav shape, same order of buttons.
STORE_HTML = """<!DOCTYPE html>
<html lang="id">
<head><meta charset="utf-8"><title>DEMIOS // FREE FIRE iOS</title>
<link rel="stylesheet" href="/store.css"></head>
<body>
<main class="wrap">
  <nav class="nav">
    <a href="/store" class="brand">
      <span class="logo">D</span>
      <span class="glitch" data-t="DEMIOS">DEMIOS</span>
    </a>
    <span class="ver">v1.132.6</span>
    <span class="nav-dot" aria-hidden="true"></span>
    <span class="nav-state">UNDETECTED</span>
    <a href="/activate" class="nav-admin">ACTIVATE</a>
    <a href="/install" class="nav-admin" style="margin-left:0">INSTALL</a>
  </nav>
  <section class="plans"></section>
</main>
<script src="/store.js"></script>
</body>
</html>
"""


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALL), *args],
                          capture_output=True, text=True, encoding="utf-8")


def project(tmp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    static = tmp / "web" / "public"
    functions = tmp / "functions"
    (functions / "api").mkdir(parents=True)
    static.mkdir(parents=True)
    (static / "store.html").write_text(STORE_HTML, encoding="utf-8")
    (static / "store.css").write_text(":root{--bg:#05060A}", encoding="utf-8")
    (functions / "api" / "buy.js").write_text(
        "export function onRequest(){return new Response('ios')}", encoding="utf-8")
    return static, functions


def main() -> int:
    if not INSTALL.exists():
        bad("site/tools/install.py is missing")
        return 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="install-"))
    try:
        static, functions = project(tmp)

        # ---- 1. dry run ---------------------------------------------------
        before = {p: p.read_bytes() for p in static.rglob("*") if p.is_file()}
        r = run("--static", str(static), "--functions", str(functions), "--dry-run")
        check(r.returncode == 0, "dry run exits 0", r.stderr.strip()[:120])
        check("DRY RUN" in r.stdout, "dry run says so")
        after = {p: p.read_bytes() for p in static.rglob("*") if p.is_file()}
        check(before == after, "dry run wrote nothing",
              f"{len(after) - len(before)} files appeared")
        check(not (static / "android.html").exists(), "dry run did not create android.html")

        # ---- 2. the real run ----------------------------------------------
        r = run("--static", str(static), "--functions", str(functions))
        check(r.returncode == 0, "install exits 0", r.stderr.strip()[:200])

        for name in ("android.html", "android.css", "android.js", "qr.js"):
            check((static / name).exists(), f"public/{name} was copied")

        api = functions / "api" / "android"
        for name in ("plans.js", "order.js", "admin.js", "webhook.js"):
            check((api / name).exists(), f"functions/api/android/{name} was copied")

        check((functions / "_lib" / "key.js").exists(), "functions/_lib/key.js was copied")
        check((functions / "api" / "buy.js").exists(), "the existing /api/buy was not touched")
        check((static / "store.css").read_text(encoding="utf-8") == ":root{--bg:#05060A}",
              "the existing store.css was not overwritten")

        # ---- 3. the nav link ----------------------------------------------
        html = (static / "store.html").read_text(encoding="utf-8")
        check(html.count('href="/android"') == 1, "exactly one ANDROID link")
        check(html.index('href="/android"') < html.index('href="/activate"'),
              "the link sits before ACTIVATE")
        check(html.index("<nav") < html.index('href="/android"') < html.index("</nav>"),
              "the link is inside <nav>")
        check('href="/activate"' in html and 'href="/install"' in html,
              "the original nav buttons survived")

        # ---- 4. idempotent -------------------------------------------------
        stamp = (static / "store.html").stat().st_mtime_ns
        r2 = run("--static", str(static), "--functions", str(functions))
        check(r2.returncode == 0, "a second run exits 0")
        html2 = (static / "store.html").read_text(encoding="utf-8")
        check(html2 == html, "a second run leaves store.html byte-identical")
        check(html2.count('href="/android"') == 1, "still exactly one ANDROID link")
        check("already links" in r2.stdout, "and says it was already linked")
        check((static / "store.html").stat().st_mtime_ns == stamp,
              "the file was not rewritten at all")

        # ---- 5. refusals ---------------------------------------------------
        empty = tmp / "empty"
        empty.mkdir()
        r3 = run("--static", str(empty), "--functions", str(functions))
        check(r3.returncode == 2 and "does not look like the site" in r3.stdout,
              "a directory that is not a site is refused")

        r4 = run("--static", str(empty), "--functions", str(functions), "--force")
        check(r4.returncode == 0, "--force overrides the check for a deliberately empty target")

        r5 = run("--static", str(SITE / "public"), "--functions", str(functions))
        check(r5.returncode == 2 and "overlay itself" in r5.stdout,
              "pointing --static at the overlay is refused")

        r6 = run("--static", str(static))
        check(r6.returncode == 0 or "could not find" in r6.stdout,
              "a missing --functions is either found next door or reported")

        # ---- 6. the secret it prints is the one in License.java -----------
        r7 = run("--static", str(static), "--functions", str(functions))
        m = re.search(r'SECRET\s*=\s*"([^"]+)"', LICENSE_JAVA.read_text(encoding="utf-8"))
        if not m:
            bad("no SECRET constant in License.java")
        else:
            check(m.group(1) in r7.stdout, "the printed LICENSE_SECRET is the one in the APK",
                  m.group(1)[:12] + "...")
            check("ganti-secret-ini" not in r7.stdout,
                  "the placeholder is not printed anywhere")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"install: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
