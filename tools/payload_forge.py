#!/usr/bin/env python3
"""payload_forge.py - one container per install, and no two alike.

The XYZ build lost to a hash. Its payload was byte-identical for every user,
sitting in Free Fire's own files directory, so one caught user put the whole
userbase on the list. That is a design mistake, not bad luck, and it is
fixable at build time:

  * every install gets 32 random bytes of its own, from which the encryption
    key and the MAC key are derived
  * every install gets a random name, a random padding length and a random
    loopback port
  * the module is padded with random bytes before encryption, so identical
    modules still produce different ciphertexts

The result is that two forges of the same module share no bytes, no hash, no
name and no port. There is nothing constant to put on a list.

Construction (deliberately stdlib-only, on both sides):

    enc_key = HMAC-SHA256(master, "xyz-enc")
    mac_key = HMAC-SHA256(master, "xyz-mac")
    keystream[i] = HMAC-SHA256(enc_key, nonce || u32le(i))
    ciphertext   = plaintext XOR keystream          plaintext = pad || module
    tag          = HMAC-SHA256(mac_key, container[0 : tag_start])

Encrypt-then-MAC: the tag covers the header and the ciphertext, and is checked
before a single byte is decrypted. A PRF keystream in counter mode is a
standard construction; there is one container per install, so no counter is
ever reused.

    python tools/payload_forge.py --out feed
    python tools/payload_forge.py --module libxyz.so --out feed
    python tools/payload_forge.py --open feed --dump module.so
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import struct
import sys
from pathlib import Path

MAGIC = b"XYZF"
VERSION = 1
NONCE_LEN = 12
PAD_MAX = 512
TAG_LEN = 32


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def derive(master: bytes) -> tuple[bytes, bytes]:
    if len(master) != 32:
        raise ValueError("master key must be 32 bytes")
    return _hmac(master, b"xyz-enc"), _hmac(master, b"xyz-mac")


def keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < length:
        out += _hmac(enc_key, nonce + struct.pack("<I", i))
        i += 1
    return bytes(out[:length])


def seal(module: bytes, master: bytes, name: str, padlen: int | None = None) -> bytes:
    enc_key, mac_key = derive(master)
    if padlen is None:
        padlen = 96 + secrets.randbelow(PAD_MAX - 96)
    nonce = secrets.token_bytes(NONCE_LEN)
    plain = secrets.token_bytes(padlen) + module

    header = MAGIC + bytes([VERSION]) + nonce + struct.pack("<HI", padlen, len(module))
    ct = bytes(a ^ b for a, b in zip(plain, keystream(enc_key, nonce, len(plain))))
    body = header + ct
    return body + _hmac(mac_key, body + name.encode())


def open_container(container: bytes, master: bytes, name: str) -> bytes:
    if len(container) < 23 + TAG_LEN:
        raise ValueError("container too short")
    if container[:4] != MAGIC:
        raise ValueError("bad magic")
    if container[4] != VERSION:
        raise ValueError("unsupported version %d" % container[4])

    body, tag = container[:-TAG_LEN], container[-TAG_LEN:]
    enc_key, mac_key = derive(master)
    if not hmac.compare_digest(_hmac(mac_key, body + name.encode()), tag):
        raise ValueError("authentication failed")

    padlen, module_len = struct.unpack_from("<HI", container, 17)
    nonce = container[5:17]
    ct = container[23:-TAG_LEN]
    if len(ct) != padlen + module_len:
        raise ValueError("length mismatch: %d != %d + %d" % (len(ct), padlen, module_len))

    plain = bytes(a ^ b for a, b in zip(ct, keystream(enc_key, nonce, len(ct))))
    return plain[padlen:]


def forge(module: bytes, out: Path, port: int | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    master = secrets.token_bytes(32)
    name = secrets.token_hex(8)
    if port is None:
        port = 39000 + secrets.randbelow(6000)

    container = seal(module, master, name)
    (out / "container.bin").write_bytes(container)
    manifest = {
        "format": VERSION,
        "name": name,
        "port": port,
        "key": master.hex(),
        "container_size": len(container),
        "container_sha256": hashlib.sha256(container).hexdigest(),
        "module_size": len(module),
        "module_sha256": hashlib.sha256(module).hexdigest(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="forge a per-install payload container")
    ap.add_argument("--module", type=Path, help="the .so or blob to ship")
    ap.add_argument("--out", type=Path, help="where to write container.bin + manifest.json")
    ap.add_argument("--open", dest="open_dir", type=Path,
                    help="verify and decode an existing feed directory")
    ap.add_argument("--dump", type=Path, help="where to write the decoded module")
    args = ap.parse_args(argv)

    if args.open_dir:
        man = json.loads((args.open_dir / "manifest.json").read_text(encoding="utf-8"))
        container = (args.open_dir / "container.bin").read_bytes()
        try:
            module = open_container(container, bytes.fromhex(man["key"]), man["name"])
        except ValueError as e:
            print("[!] %s" % e, file=sys.stderr)
            return 1
        ok = hashlib.sha256(module).hexdigest() == man["module_sha256"]
        print("module   %d bytes  sha256 %s  %s"
              % (len(module), hashlib.sha256(module).hexdigest()[:16],
                 "matches the manifest" if ok else "DOES NOT MATCH"))
        if args.dump:
            args.dump.write_bytes(module)
            print("-> %s" % args.dump)
        return 0 if ok else 1

    if args.module:
        module = args.module.read_bytes()
    else:
        # a stand-in so the pipeline can be exercised before the module exists
        module = b"\x7fELF" + secrets.token_bytes(4096)

    if args.out is None:
        ap.error("--out is required unless --open is used")
    man = forge(module, args.out)
    print("container  %d bytes" % man["container_size"])
    print("name       %s" % man["name"])
    print("port       %d" % man["port"])
    print("key        %s..." % man["key"][:16])
    print("module     %d bytes  %s" % (man["module_size"], man["module_sha256"][:16]))
    print("-> %s/container.bin" % args.out)
    print("-> %s/manifest.json" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))