#!/usr/bin/env python3
"""test_site_smoke.py - the deploy smoke test, proved against two real servers.

smoke.py is the tool that catches "the page returns 200 and renders as unstyled
HTML", which is what happens when an overlay is deployed without the stylesheet
it sits next to. A tool like that is worth nothing unless it is known to fail
when it should, so this serves two lollipop deployments over loopback:

  * an overlay-only one, exactly what site/public is: /android comes back 200
    and /store.css is a 404. smoke.py must exit 1 and name the file.
  * a complete one: the same tree plus a store.css that defines the classes the
    page needs. smoke.py must exit 0.

The second server is also given a decoy: a store.css that is 200 but defines
nothing. Status alone would pass that; the selector check must not.

  python site/tests/test_site_smoke.py
"""

from __future__ import annotations

import functools
import http.server
import pathlib
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent
SMOKE = SITE / "tools" / "smoke.py"

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


GOOD_CSS = """
:root{--bg:#05060A}
*{box-sizing:border-box}
body{margin:0;background:var(--bg)}
.wrap{max-width:1100px;margin:0 auto;padding:0 18px}
.nav{display:flex;align-items:center;gap:14px}
.hero{padding:40px 0}
.pcard{border:1px solid #222;padding:18px}
.activate{width:100%;padding:12px}
"""

# 200, right content type, and defines none of the classes the page uses.
DECOY_CSS = ":root{--bg:#000}\nbody{margin:0}\n"


class Quiet(http.server.SimpleHTTPRequestHandler):
    """A file server with Pages' clean URLs: /android serves android.html."""

    def send_head(self):  # noqa: D102
        path = self.path.split("?", 1)[0]
        if path != "/" and "." not in path.rsplit("/", 1)[-1]:
            candidate = pathlib.Path(self.directory) / (path.lstrip("/") + ".html")
            if candidate.is_file():
                self.path = path + ".html"
        return super().send_head()

    def log_message(self, *a):  # noqa: D102
        pass


def serve(directory: str):
    handler = functools.partial(Quiet, directory=directory)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.allow_reuse_address = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def run_smoke(base: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SMOKE), "--base", base, "--skip-api"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=180)


def build(root: pathlib.Path, css: str | None, with_favicon: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for f in (SITE / "public").iterdir():
        if f.is_file():
            shutil.copy2(f, root / f.name)
    if with_favicon:
        (root / "favicon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>', encoding="utf-8")
    if css is not None:
        (root / "store.css").write_text(css, encoding="utf-8")


def main() -> int:
    if not SMOKE.exists():
        bad("site/tools/smoke.py is missing")
        return 1
    if not (SITE / "public" / "android.html").exists():
        bad("site/public/android.html is missing")
        return 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="smoke-"))
    servers = []
    try:
        # ---- 1. overlay only: the exact shape that looked broken -----------
        only = tmp / "only"
        build(only, css=None)
        check(not (only / "store.css").exists(), "the overlay really has no store.css")
        httpd, port = serve(str(only))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1, "smoke exits 1 on an overlay-only deployment",
              "exit " + str(r.returncode))
        check("/store.css" in r.stdout, "and names the missing stylesheet")
        check("plain HTML" in r.stdout or "no layout" in r.stdout,
              "and explains the symptom it will cause")
        check("GET /android -> 200" in r.stdout,
              "while still reporting that the page itself answered 200")
        check("[FAIL]" in r.stdout and "render unstyled" in r.stdout,
              "and says the page will render unstyled")

        # ---- 2. complete deployment ---------------------------------------
        full = tmp / "full"
        build(full, css=GOOD_CSS)
        httpd, port = serve(str(full))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 0, "smoke exits 0 when the stylesheet is there",
              "exit " + str(r.returncode))
        check("defines .wrap, .nav, .hero, .pcard, .activate" in r.stdout,
              "and confirms the stylesheet defines the classes the page needs")
        check("/qr.js is loaded before /android.js" in r.stdout,
              "and that the script order is right")

        # ---- 3. a 200 that is not a stylesheet ----------------------------
        decoy = tmp / "decoy"
        build(decoy, css=DECOY_CSS)
        httpd, port = serve(str(decoy))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1,
              "a 200 stylesheet that defines nothing still fails",
              "exit " + str(r.returncode))
        check("does not define" in r.stdout, "and says which selectors are missing")

        # ---- 4. no favicon -------------------------------------------------
        bare = tmp / "bare"
        build(bare, css=GOOD_CSS, with_favicon=False)
        httpd, port = serve(str(bare))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1 and "/favicon.svg" in r.stdout,
              "a missing favicon is reported too")

        # ---- 5. a page that is not there ----------------------------------
        r = run_smoke("http://127.0.0.1:1")
        check(r.returncode == 1, "an unreachable base exits 1 rather than crashing")
        check("[FAIL]" in r.stdout, "and says what failed")
    finally:
        for s in servers:
            s.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"smoke tool: {PASS} ok, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
