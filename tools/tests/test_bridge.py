#!/usr/bin/env python3
"""test_bridge.py - the panel's control surface, against a real socket.

The module answers on loopback and nothing about that is negotiable: the paths,
the `Origin` header, and which number means which feature. Get one of those
wrong and the panel looks like it works while the game does nothing - the
failure mode that wastes an afternoon because there is no error to read.

So this starts a server that behaves like the module, compiles the real
Bridge.java against a plain JDK, and drives it:

  * the item table matches the reference build, holes included
  * /itemN and /itemN/desativar are the two verbs
  * /fov/N carries the size
  * every request carries the Origin the module requires
  * a non-2xx is an error, not an empty string
  * a malformed status line is refused instead of parsed into nonsense
  * out-of-range item and FOV numbers are rejected before the socket opens

  python tools/tests/test_bridge.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BRIDGE = (ROOT / "android" / "app" / "app" / "src" / "main" / "java"
          / "com" / "alokg" / "ffmenu" / "mirror" / "Bridge.java")
HARNESS = HERE / "java" / "BridgeHarness.java"

FAIL = 0

# The mapping read out of the reference panel's if/else chain. Holes at 3 and 4
# are real: 5 is the drag headshot, not the fifth row.
EXPECTED = {
    1: "LOCK HEAD",
    2: "ESP LINE",
    5: "DRAG HS 85%",
    6: "ESP NAME",
    7: "ESP HEALTH",
    8: "LOCK BADAN",
    9: "ESP SKELETON",
    10: "ESP DISTANCE",
    11: "AIM FOV",
    12: "NO RECOIL",
    13: "HIDE AIM FOV",
    14: "FAST SWITCH",
    15: "NO SPREAD",
    16: "FAST RELOAD",
}

ORIGIN = "https://patoteam.shop"
GOOD_STATUS = "XYZFOV3|6|3|3|2|1|0|0"


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


class Module(BaseHTTPRequestHandler):
    """A stand-in for the in-game server: binary acks, one status line."""

    seen: list[tuple[str, str | None]] = []
    status_body = GOOD_STATUS
    fail_path = "/item12"

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Origin")))
        if self.path == self.fail_path:
            self.send_response(500)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"no")
            return

        if self.path == "/xyz/fov3-status":
            body = self.status_body.encode()
        elif self.path == "/":
            body = b"ok"
        else:
            # a real ack is a short opaque byte string, not text
            body = bytes([0x00, 0xff, 0x7f, 0x01])

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(status_body=GOOD_STATUS, fail_path="/item12"):
    handler = type("M", (Module,), {"status_body": status_body, "fail_path": fail_path})
    handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, handler, srv.server_address[1]


def main() -> int:
    print("=" * 66)
    print(" module control surface (java client -> real socket)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="bridge-"))
    out = tmp / "classes"
    out.mkdir()

    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out), str(BRIDGE), str(HARNESS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("Bridge compiles with no Android imports", True)

    def run(*args: str):
        p = subprocess.run([java, "-cp", str(out), "BridgeHarness", *args],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "").strip()

    # --- the table ---------------------------------------------------------
    code, body = run("labels")
    got = {}
    for line in body.splitlines():
        n, label = line.split(" ", 1)
        got[int(n)] = label
    check("14 items", len(got) == 14, str(len(got)))
    check("item table matches the reference build", got == EXPECTED,
          "" if got == EXPECTED else f"got {got}")
    check("3 and 4 are absent", 3 not in got and 4 not in got)
    check("5 is the drag headshot", got.get(5) == "DRAG HS 85%")

    code, body = run("origin")
    check("Origin is the one the module accepts", body == ORIGIN, body)

    # --- requests ----------------------------------------------------------
    srv, handler, port = serve()
    try:
        code, body = run("alive", str(port))
        check("alive sees a running module", body == "true", body)

        handler.seen.clear()
        code, body = run("cmd", str(port), "1", "on")
        check("item 1 on returns on", body == "true", body)
        check("item 1 on is a bare path", handler.seen and handler.seen[-1][0] == "/item1",
              str(handler.seen[-1:]))

        handler.seen.clear()
        code, body = run("cmd", str(port), "5", "off")
        check("item 5 off returns off", body == "false", body)
        check("off appends /desativar",
              handler.seen and handler.seen[-1][0] == "/item5/desativar",
              str(handler.seen[-1:]))

        handler.seen.clear()
        code, body = run("cmd", str(port), "16", "on")
        check("the last item is 16", handler.seen and handler.seen[-1][0] == "/item16",
              str(handler.seen[-1:]))

        handler.seen.clear()
        code, body = run("fov", str(port), "60")
        check("fov returns the size", body == "60", body)
        check("fov is /fov/N", handler.seen and handler.seen[-1][0] == "/fov/60",
              str(handler.seen[-1:]))

        origins = {o for _, o in handler.seen}
        check("every request carries Origin", origins == {ORIGIN}, str(origins))

        code, body = run("status", str(port))
        check("status parses", code == 0 and body.startswith("FOV: target siap"), body)
        check("status carries the counts", "pemain 3, valid 3, dalam FOV 2" in body, body)
        check("status carries the flags", "tembak YA | kamera TIDAK | error TIDAK" in body, body)

        # a binary ack must not be mistaken for text and must not throw
        code, body = run("cmd", str(port), "2", "on")
        check("a binary ack is tolerated", code == 0 and body == "true", body)
    finally:
        srv.shutdown()

    # --- failures ----------------------------------------------------------
    srv, handler, port = serve(fail_path="/item12")
    try:
        code, body = run("cmd", str(port), "12", "on")
        check("a 500 is an error", code == 3, f"exit {code} {body}")
        check("and it names the code", "HTTP 500" in body, body)
    finally:
        srv.shutdown()

    srv, handler, port = serve(status_body="XYZFOV3|99")
    try:
        code, body = run("status", str(port))
        check("a malformed status is refused", code == 3, f"exit {code} {body}")
        check("and it says so", "tidak valid" in body, body)
    finally:
        srv.shutdown()

    srv, handler, port = serve(status_body="XYZFOV3|9|1|1|1|0|0|0")
    try:
        code, body = run("status", str(port))
        check("the error mode is named", "kesalahan modul" in body, body)
    finally:
        srv.shutdown()

    # nothing listening at all
    code, body = run("alive", "1")
    check("alive is false with no module", code == 0 and body == "false", body)

    # --- arguments ---------------------------------------------------------
    code, body = run("cmd", "1", "0", "on")
    check("item 0 is rejected before the socket", code == 4, f"exit {code} {body}")
    code, body = run("cmd", "1", "17", "on")
    check("item 17 is rejected before the socket", code == 4, f"exit {code} {body}")
    code, body = run("fov", "1", "0")
    check("fov 0 is rejected", code == 4, f"exit {code} {body}")
    code, body = run("fov", "1", "201")
    check("fov 201 is rejected", code == 4, f"exit {code} {body}")

    # --- the verdict line a buyer is shown ---------------------------------
    #
    # This is what the app prints when MIRROR is tapped. It is the only thing
    # standing between "it does not work" and "the patch is not installed", so it
    # is tested as a function rather than assembled inside a click handler.
    code, body = run("verdict", "0", "-1", "0")
    check("no patch yet: says to install", "INSTALL" in body, body)

    code, body = run("verdict", "1", "0", "0")
    check("empty patch: says to install again", "INSTALL" in body, body)

    code, body = run("verdict", "1", "121084", "1")
    check("patch + module answering: says it is ready",
          "MENJAWAB" in body and "121084" in body, body)

    code, body = run("verdict", "1", "121084", "0")
    check("patch but no answer: names the two next steps",
          "TIDAK menjawab" in body and "INSTALL" in body and "tail" in body, body)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
