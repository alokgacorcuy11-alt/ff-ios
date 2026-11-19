#!/usr/bin/env python3
"""test_payload_forge.py - the container properties that matter without a phone.

test_feed.py proves the format across Python and Java, but it skips when there
is no JDK. These are the checks that have to hold on any machine:

  * a container round-trips
  * the tag covers the header, the ciphertext and the install name
  * a truncated or over-long body is refused, not silently sliced
  * identical modules produce different containers on every forge
  * the manifest describes what was actually written

  python tools/tests/test_payload_forge.py
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = ROOT / "tools" / "payload_forge.py"

sys.path.insert(0, str(ROOT / "tools"))
import payload_forge as pf  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def rejects(fn, needle=""):
    try:
        fn()
    except ValueError as e:
        return needle in str(e)
    return False


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def main() -> int:
    print("=" * 66)
    print(" payload forge")
    print("=" * 66)

    master = secrets.token_bytes(32)
    module = b"\x7fELF" + secrets.token_bytes(3000)
    name = "a1b2c3d4"

    c = pf.seal(module, master, name)
    check("round-trip", pf.open_container(c, master, name) == module)
    check("magic is XYZF", c[:4] == b"XYZF")
    check("version is 1", c[4] == 1)
    check("header is 23 bytes before the body", len(c) > 23 + 32)

    padlen, mlen = struct.unpack_from("<HI", c, 17)
    check("module length is recorded", mlen == len(module), str(mlen))
    check("padding is applied", padlen > 0, str(padlen))
    check("body length matches pad + module",
          len(c) - 23 - 32 == padlen + mlen)

    # the tag binds the name
    check("a different name is rejected",
          rejects(lambda: pf.open_container(c, master, "a1b2c3d5"), "authentication"))
    # and the key
    check("a different key is rejected",
          rejects(lambda: pf.open_container(c, secrets.token_bytes(32), name), "authentication"))
    # and every byte of the header, the body and the tag
    for off, what in ((0, "magic"), (4, "version"), (10, "nonce"),
                      (17, "padding length"), (30, "ciphertext"),
                      (len(c) - 1, "tag")):
        bad = bytearray(c)
        bad[off] ^= 0x01
        check("a flipped byte in %s is rejected" % what,
              raises(lambda b=bytes(bad): pf.open_container(b, master, name)))

    check("a truncated container is rejected",
          rejects(lambda: pf.open_container(c[:-40], master, name)))
    check("a short container is rejected",
          rejects(lambda: pf.open_container(b"XYZF", master, name), "too short"))
    check("a non-32-byte master is rejected",
          rejects(lambda: pf.seal(b"x", b"short", "n"), "32 bytes"))

    # uniqueness across forges of the same module
    tmp = Path(tempfile.mkdtemp(prefix="forge-"))
    one = pf.forge(module, tmp / "one")
    two = pf.forge(module, tmp / "two")
    c1 = (tmp / "one" / "container.bin").read_bytes()
    c2 = (tmp / "two" / "container.bin").read_bytes()
    check("same module, different container", c1 != c2)
    check("different key", one["key"] != two["key"])
    check("different name", one["name"] != two["name"])
    check("port is in the ephemeral upper range", 39000 <= one["port"] < 45000, str(one["port"]))
    check("same module hash in both manifests",
          one["module_sha256"] == two["module_sha256"] == hashlib.sha256(module).hexdigest())

    # manifest describes the file
    check("manifest size matches the file", one["container_size"] == len(c1))
    check("manifest hash matches the file",
          one["container_sha256"] == hashlib.sha256(c1).hexdigest())
    check("manifest decodes back to the module",
          pf.open_container(c1, bytes.fromhex(one["key"]), one["name"]) == module)

    # CLI
    r = subprocess.run([sys.executable, str(TOOL), "--open", str(tmp / "one")],
                       capture_output=True, text=True, timeout=60)
    check("cli --open verifies", r.returncode == 0, r.stderr.strip()[:80])
    check("cli --open reports a match", "matches the manifest" in r.stdout)

    dump = tmp / "module.bin"
    r = subprocess.run([sys.executable, str(TOOL), "--open", str(tmp / "one"),
                        "--dump", str(dump)], capture_output=True, text=True, timeout=60)
    check("cli --dump writes the module", dump.is_file() and dump.read_bytes() == module)

    (tmp / "one" / "container.bin").write_bytes(c1[:-1] + bytes([c1[-1] ^ 0xFF]))
    r = subprocess.run([sys.executable, str(TOOL), "--open", str(tmp / "one")],
                       capture_output=True, text=True, timeout=60)
    check("cli fails on a tampered feed", r.returncode == 1, str(r.returncode))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())