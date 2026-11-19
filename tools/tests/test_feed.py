#!/usr/bin/env python3
"""test_feed.py - the Python forge and the Java transport, pinned together.

A wire format that only ever gets tested against itself is a guess. The forge
is Python, the code that runs on the phone is Java, and nothing in either
language would notice if they drifted apart - until the module silently failed
to arrive.

So this compiles the two Java classes, runs them, and speaks the protocol to
them from Python:

  * Java decodes a Python-built container and reproduces the module hash
  * Java serves; Python challenges, checks the response MAC, decodes
  * Python serves; Java's FeedFormat.fetch pulls it and reproduces the hash
  * a flipped byte in the container is rejected by the MAC, in both languages

Skipped when javac/java are not installed, same as test_java.py.

  python tools/tests/test_feed.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
JAVA_SRC = ROOT / "android" / "app" / "app" / "src" / "main" / "java" / "com" / "alokg" / "ffmenu" / "feed"
HARNESS = HERE / "java" / "FeedHarness.java"

sys.path.insert(0, str(ROOT / "tools"))
import payload_forge as pf  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def hmac256(key: bytes, *parts: bytes) -> bytes:
    m = hmac.new(key, digestmod=hashlib.sha256)
    for p in parts:
        m.update(p)
    return m.digest()


def java_open(container: bytes, master: bytes, name: str) -> bytes:
    """The Python side of the Java reader, written from the format doc."""
    body, tag = container[:-32], container[-32:]
    mac_key = hmac256(master, b"xyz-mac")
    if not hmac.compare_digest(hmac256(mac_key, body, name.encode()), tag):
        raise ValueError("authentication failed")
    padlen, module_len = struct.unpack_from("<HI", container, 17)
    nonce = container[5:17]
    ct = container[23:-32]
    enc_key = hmac256(master, b"xyz-enc")
    ks = b""
    i = 0
    while len(ks) < len(ct):
        ks += hmac256(enc_key, nonce, struct.pack("<I", i))
        i += 1
    plain = bytes(a ^ b for a, b in zip(ct, ks))
    return plain[padlen:padlen + module_len]


def python_serve(container: bytes, master: bytes, name: str) -> tuple[int, bytes]:
    """A minimal FeedServer, so Java's fetch has something real to talk to."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    srv.settimeout(10)

    def run():
        conn, _ = srv.accept()
        try:
            conn.settimeout(10)
            nonce = b""
            while len(nonce) < 16:
                chunk = conn.recv(16 - len(nonce))
                if not chunk:
                    return
                nonce += chunk
            mac_key = hmac256(master, b"xyz-mac")
            tag = hmac256(mac_key, nonce, container)
            conn.sendall(b"XYZF" + bytes([1]) + struct.pack("<I", len(container)) + tag + container)
        finally:
            conn.close()
            srv.close()

    import threading
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return port, b""


def main() -> int:
    print("=" * 66)
    print(" feed transport (python forge <-> java)")
    print("=" * 66)

    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        print("[skip] no jdk on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    out = tmp / "classes"
    out.mkdir()

    sources = [str(JAVA_SRC / "FeedFormat.java"), str(JAVA_SRC / "FeedServer.java"),
               str(JAVA_SRC / "FeedChannel.java"), str(HARNESS)]
    r = subprocess.run([javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
                        "-d", str(out)] + sources,
                       capture_output=True, text=True)
    if r.returncode != 0:
        for ln in (r.stderr or "").splitlines():
            if ": error:" in ln:
                print(f"[FAIL] {ln}")
        print("\ncompilation FAILED")
        return 1
    check("feed classes compile", True)

    def run_java(args, timeout=40):
        return subprocess.run([java, "-cp", str(out), "FeedHarness"] + args,
                              capture_output=True, text=True, timeout=timeout)

    # --- the forge itself
    module = b"\x7fELF" + secrets.token_bytes(9000)
    feed = tmp / "feed"
    man = pf.forge(module, feed)
    container = (feed / "container.bin").read_bytes()
    master = bytes.fromhex(man["key"])
    name = man["name"]

    check("forge wrote a container", container[:4] == pf.MAGIC, str(container[:4]))
    check("forge recorded the module hash",
          man["module_sha256"] == hashlib.sha256(module).hexdigest())
    check("python decode round-trips", pf.open_container(container, master, name) == module)
    check("the doc-level reader agrees with the tool",
          java_open(container, master, name) == module)

    # --- java reads what python wrote
    r = run_java(["open", str(feed / "container.bin"), man["key"], name])
    check("java open exits 0", r.returncode == 0, r.stderr.strip()[:100])
    check("java reproduces the module hash",
          ("SHA " + man["module_sha256"]) in r.stdout, r.stdout.strip()[:120])
    check("java reports the module length",
          ("LEN %d" % len(module)) in r.stdout)

    # --- java serves, python challenges
    p = subprocess.Popen([java, "-cp", str(out), "FeedHarness", "serve",
                          str(feed / "container.bin"), man["key"], name, "15000"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    port = None
    deadline = time.time() + 20
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        if line.startswith("PORT "):
            port = int(line.split()[1])
            break
    check("java server announced a port", port is not None, str(port))

    if port:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            nonce = secrets.token_bytes(16)
            s.sendall(nonce)
            buf = b""
            while len(buf) < 9:
                chunk = s.recv(9 - len(buf))
                if not chunk:
                    break
                buf += chunk
            magic, ver = buf[:4], buf[4]
            size = struct.unpack("<I", buf[5:9])[0]
            rest = b""
            while len(rest) < 32 + size:
                chunk = s.recv(32 + size - len(rest))
                if not chunk:
                    break
                rest += chunk
            tag, got = rest[:32], rest[32:32 + size]
            want = hmac256(bytes.fromhex(man["key"]), b"xyz-mac")
            check("response magic", magic == b"XYZF", str(magic))
            check("response version", ver == 1)
            check("response size matches the container", size == len(container))
            check("response MAC binds the challenge",
                  hmac.compare_digest(hmac256(want, nonce, got), tag))
            check("served bytes are the forged container", got == container)
            check("served container decodes to the module",
                  java_open(got, master, name) == module)
        finally:
            s.close()

    served = p.wait(timeout=30)
    check("java server reports SERVED", served == 0, str(served))

    # --- python serves, java fetches
    port2, _ = python_serve(container, master, name)
    r = run_java(["fetch", str(feed / "container.bin"), man["key"], name, str(port2)])
    check("java fetch exits 0", r.returncode == 0, r.stderr.strip()[:120])
    check("java fetch reproduces the module hash",
          ("SHA " + man["module_sha256"]) in r.stdout, r.stdout.strip()[:120])

    # --- the command session on the same socket
    def open_session(java_args):
        p = subprocess.Popen([java, "-cp", str(out), "FeedHarness", "session"] + java_args,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        port = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            if line.startswith("PORT "):
                port = int(line.split()[1])
                break
        if port is None:
            p.kill()
            return None, None, p
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.settimeout(10)
        nonce = secrets.token_bytes(16)
        s.sendall(nonce)
        buf = b""
        while len(buf) < 9:
            chunk = s.recv(9 - len(buf))
            if not chunk:
                s.close()
                p.kill()
                return None, None, p
            buf += chunk
        size = struct.unpack("<I", buf[5:9])[0]
        rest = b""
        while len(rest) < 32 + size:
            chunk = s.recv(32 + size - len(rest))
            if not chunk:
                s.close()
                p.kill()
                return None, None, p
            rest += chunk
        return s, rest[32:32 + size], p

    def frame(type_, seq, payload):
        # the frame MAC is keyed with the derived macKey, not the master
        head = bytes([type_]) + struct.pack("<II", seq, len(payload))
        mac = hmac256(chan_key, head, payload)[:16]
        return head + payload + mac

    def read_frame(sock):
        head = b""
        while len(head) < 9:
            chunk = sock.recv(9 - len(head))
            if not chunk:
                raise EOFError("socket closed while reading a frame header")
            head += chunk
        size = struct.unpack("<I", head[5:9])[0]
        rest = b""
        while len(rest) < size + 16:
            chunk = sock.recv(size + 16 - len(rest))
            if not chunk:
                raise EOFError("socket closed inside a frame")
            rest += chunk
        return head[0], struct.unpack("<I", head[1:5])[0], rest[:size], rest[size:]

    chan_key = hmac256(bytes.fromhex(man["key"]), b"xyz-mac")

    s, got, p = open_session([str(feed / "container.bin"), man["key"], name, "2"])
    check("session socket opened", s is not None)
    if s is not None:
        check("session delivered the container", got == container)
        s.sendall(frame(1, 1, b'{"esp":true,"fov":90}'))
        s.sendall(frame(1, 2, b'{"aim":true,"bone":"head"}'))
        a1 = read_frame(s)
        a2 = read_frame(s)
        check("ack 1 came back", a1[0] == 2 and a1[1] == 1, str(a1[:2]))
        check("ack 2 came back", a2[0] == 2 and a2[1] == 2, str(a2[:2]))
        key = hmac256(bytes.fromhex(man["key"]), b"xyz-mac")
        ok = True
        for typ, seq, payload, mac in (a1, a2):
            head = bytes([typ]) + struct.pack("<II", seq, len(payload))
            ok = ok and hmac.compare_digest(hmac256(key, head, payload)[:16], mac)
        check("acks are authenticated", ok)
        s.close()

    rc = p.wait(timeout=30)
    check("session exits 0 after both commands", rc == 0, str(rc))
    check("java saw both commands", p.stdout.read().count("CMD ") == 2)

    # --- a replayed frame is refused
    s, got, p = open_session([str(feed / "container.bin"), man["key"], name, "2"])
    if s is not None:
        s.sendall(frame(1, 2, b'{"esp":true}'))
        s.sendall(frame(1, 2, b'{"esp":false}'))
        read_frame(s)
        s.close()
    rc = p.wait(timeout=30)
    out_text = p.stdout.read()
    check("a replayed sequence number is refused", rc == 4, str(rc))
    check("the refusal names the reason", "replayed" in out_text, out_text.strip()[-80:])

    # --- tamper
    bad = bytearray(container)
    bad[len(bad) // 2] ^= 0x01
    try:
        java_open(bytes(bad), master, name)
        check("a flipped byte is rejected", False)
    except ValueError as e:
        check("a flipped byte is rejected", "auth" in str(e), str(e))

    (tmp / "bad.bin").write_bytes(bytes(bad))
    r = run_java(["open", str(tmp / "bad.bin"), man["key"], name])
    check("java rejects the tampered container", r.returncode != 0, str(r.returncode))

    # --- wrong install key
    other = secrets.token_bytes(32).hex()
    r = run_java(["open", str(feed / "container.bin"), other, name])
    check("java rejects a foreign key", r.returncode != 0, str(r.returncode))

    # --- two forges share nothing
    a = pf.forge(secrets.token_bytes(1000), tmp / "a")
    b = pf.forge(secrets.token_bytes(1000), tmp / "b")
    ca = (tmp / "a" / "container.bin").read_bytes()
    cb = (tmp / "b" / "container.bin").read_bytes()
    check("two installs get different keys", a["key"] != b["key"])
    check("two installs get different names", a["name"] != b["name"])
    check("two installs share no hash",
          hashlib.sha256(ca).digest() != hashlib.sha256(cb).digest())
    check("identical modules still produce different containers",
          pf.seal(b"same", bytes.fromhex(a["key"]), "n1") !=
          pf.seal(b"same", bytes.fromhex(a["key"]), "n1"))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())