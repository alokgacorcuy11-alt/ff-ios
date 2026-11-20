#!/usr/bin/env python3
"""test_site_deploy.py - the deploy check, against deliberately broken trees.

deploy.py exists because a page referencing an asset that is not in the deploy
directory returns 200 and renders as plain HTML - which is exactly what happened
before. A check like that has to be proved to fail, so this copies the site to a
temporary root and breaks one thing at a time:

  * the real tree: exit 0
  * no site.css: exit 1, names it
  * a page referencing an asset that does not exist: exit 1, names the page
  * a page linking to a retired iOS route: exit 1
  * a _redirects rule pointing at itself: exit 1
  * a missing API route: exit 1
  * --branch: the printed command is a preview, not production

  python site/tests/test_site_deploy.py
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent
ROOT = SITE.parent
DEPLOY = SITE / "tools" / "deploy.py"
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


def stage(dest: pathlib.Path) -> pathlib.Path:
    """A copy of site/ under its own root, with the APK's License.java present."""
    src = dest / "site"
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SITE, src, ignore=shutil.ignore_patterns("__pycache__", ".wrangler"))
    java = dest / "android/app/app/src/main/java/com/alokg/ffmenu/License.java"
    java.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LICENSE_JAVA, java)
    return src


def run(src: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(src / "tools" / "deploy.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)


def main() -> int:
    if not DEPLOY.exists():
        bad("site/tools/deploy.py is missing")
        return 1
    if not LICENSE_JAVA.exists():
        bad("License.java is missing, cannot stage")
        return 1

    # ---- 1. what ships today ---------------------------------------------
    r = run(SITE)
    check(r.returncode == 0, "the real tree passes", "exit " + str(r.returncode))
    check("LICENSE_SECRET" in r.stdout, "and prints the secret it must match")
    secret = LICENSE_JAVA.read_text(encoding="utf-8").split('SECRET = "')[1].split('"')[0]
    check(secret in r.stdout, "the value it prints is the one in License.java")
    check("pages deploy public --project-name demios --branch main" in r.stdout,
          "and prints the production deploy command")

    r = run(SITE, "--branch", "next")
    check("--branch next" in r.stdout, "a preview branch is passed through")
    check("--branch main" not in r.stdout, "and does not also print a production deploy")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="deploy-"))
    try:
        # ---- 2. the stylesheet the page needs is missing -------------------
        a = stage(tmp / "a")
        (a / "public" / "site.css").unlink()
        r = run(a)
        check(r.returncode == 1, "a missing site.css fails the check")
        check("site.css is missing" in r.stdout, "and names the file")

        # ---- 3. a page references something that is not there -------------
        b = stage(tmp / "b")
        index = b / "public" / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace(
            "</head>", '<link rel="stylesheet" href="/gone.css"></head>'), encoding="utf-8")
        r = run(b)
        check(r.returncode == 1, "a dangling asset reference fails the check")
        check("/gone.css" in r.stdout and "index.html" in r.stdout,
              "and names both the asset and the page")

        # ---- 4. a link to a route that was removed with iOS ---------------
        c = stage(tmp / "c")
        aidx = c / "public" / "index.html"
        aidx.write_text(aidx.read_text(encoding="utf-8").replace(
            "</body>", '<a href="/store">toko</a></body>'), encoding="utf-8")
        r = run(c)
        check(r.returncode == 1, "a link to /store fails the check")
        check("removed with the iOS product" in r.stdout, "and says why it is a problem")

        # ---- 5. a redirect that points at itself --------------------------
        d = stage(tmp / "d")
        red = d / "public" / "_redirects"
        red.write_text(red.read_text(encoding="utf-8") + "\n/loop /loop 301\n", encoding="utf-8")
        r = run(d)
        check(r.returncode == 1, "a self-redirect fails the check")
        check("redirects /loop to itself" in r.stdout, "and names the rule")

        # ---- 6. an API route is missing -----------------------------------
        e = stage(tmp / "e")
        (e / "functions" / "api" / "android" / "order.js").unlink()
        r = run(e)
        check(r.returncode == 1, "a missing API route fails the check")
        check("order.js is missing" in r.stdout, "and names it")

        # ---- 7. a _lib module is missing ----------------------------------
        f = stage(tmp / "f")
        (f / "functions" / "_lib" / "qris.js").unlink()
        r = run(f)
        check(r.returncode == 1, "a missing _lib module fails the check")
        check("qris.js is missing" in r.stdout, "and names it")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"deploy check: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
