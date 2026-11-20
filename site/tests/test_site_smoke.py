#!/usr/bin/env python3
"""test_site_smoke.py - the deploy smoke test, proved against real servers.

smoke.py catches "the page returns 200 and renders as unstyled HTML", which is
what happens when a stylesheet it references is not in the deploy directory. A
monitoring tool is worth nothing unless it is known to fail when it should, so
this serves three lollipop sites over loopback, with Pages' clean URLs and its
_redirects behaviour:

  * the real tree with /site.css removed - exactly the shape that was broken.
    smoke.py must exit 1 and name the file.
  * the real tree as it is: exit 0.
  * the real tree with a /site.css that returns 200 and defines nothing. Status
    alone would pass that; the selector check must not.

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


DECOY_CSS = ":root{--bg:#000}\nbody{margin:0}\n"


class Pages(http.server.SimpleHTTPRequestHandler):
    """Enough of Pages to be useful: clean URLs, and _redirects."""

    def send_head(self):  # noqa: D102
        root = pathlib.Path(self.directory)
        path = self.path.split("?", 1)[0]

        # _redirects: "from to [status]"
        red = root / "_redirects"
        if red.is_file():
            for line in red.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2 and not line.strip().startswith("#") and parts[0] == path:
                    self.send_response(int(parts[2]) if len(parts) > 2 else 302)
                    self.send_header("Location", parts[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None

        # clean URLs: /admin serves admin.html
        if path != "/" and "." not in path.rsplit("/", 1)[-1]:
            if (root / (path.lstrip("/") + ".html")).is_file():
                self.path = path + ".html"
        return super().send_head()

    def log_message(self, *a):  # noqa: D102
        pass


def serve(directory: str):
    handler = functools.partial(Pages, directory=directory)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.allow_reuse_address = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def run_smoke(base: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SMOKE), "--base", base, "--skip-api"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


def build(root: pathlib.Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for f in PUBLIC.iterdir():
        if f.is_file():
            shutil.copy2(f, root / f.name)


def main() -> int:
    if not SMOKE.exists() or not (PUBLIC / "index.html").exists():
        bad("the site or the smoke tool is missing")
        return 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="smoke-"))
    servers = []
    try:
        # ---- 1. the site without its stylesheet ---------------------------
        broken = tmp / "broken"
        build(broken)
        (broken / "site.css").unlink()
        httpd, port = serve(str(broken))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1, "smoke exits 1 when the stylesheet is missing",
              "exit " + str(r.returncode))
        check("/site.css" in r.stdout, "and names /site.css")
        check("no layout" in r.stdout or "plain HTML" in r.stdout,
              "and explains the symptom")
        check("GET / -> 200" in r.stdout, "while reporting the page itself answered 200")
        check("render unstyled" in r.stdout, "and says the page will render unstyled")

        # ---- 2. the site as it is -----------------------------------------
        full = tmp / "full"
        build(full)
        httpd, port = serve(str(full))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 0, "smoke exits 0 on the real tree",
              "exit " + str(r.returncode) + " :: "
              + " | ".join(l for l in r.stdout.splitlines() if "FAIL" in l)[:200])
        check("defines .wrap, .nav, .hero, .pcard, .activate" in r.stdout,
              "and confirms site.css defines the classes the page needs")
        check("loads first, before /app.js" in r.stdout, "and the script order is right")
        check("/admin -> 200" in r.stdout, "and the back office renders")
        for old in ("/store", "/activate", "/install", "/panel", "/android", "/android-admin"):
            check(f"{old} -> 301" in r.stdout, f"and {old} redirects instead of dead-ending")

        # ---- 3. a 200 that is not a stylesheet ----------------------------
        decoy = tmp / "decoy"
        build(decoy)
        (decoy / "site.css").write_text(DECOY_CSS, encoding="utf-8")
        httpd, port = serve(str(decoy))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1, "a 200 stylesheet that defines nothing still fails",
              "exit " + str(r.returncode))
        check("does not define" in r.stdout, "and says which selectors are missing")

        # ---- 4. no favicon -------------------------------------------------
        bare = tmp / "bare"
        build(bare)
        (bare / "favicon.svg").unlink()
        httpd, port = serve(str(bare))
        servers.append(httpd)
        r = run_smoke(f"http://127.0.0.1:{port}")
        check(r.returncode == 1 and "/favicon.svg" in r.stdout,
              "a missing favicon is reported too")

        # ---- 5. unreachable -------------------------------------------------
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
