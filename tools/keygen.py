#!/usr/bin/env python3
"""keygen.py - issue activation keys for a build.

The shape of the product: the user installs one APK, types a key, and it
unlocks. No server round trip on the device, no account, nothing to phone home
that could fail in a match.

A key is 24 characters in six groups:

    XXXX-XXXX-XXXX-XXXX-XXXX-XXXX

  alphabet       23456789ABCDEFGHJKLMNPQRSTUVWXYZ   (no 0/O/1/I)
  payload        16 chars = 10 bytes
                 b0       version (high nibble) | tier (low nibble)
                 b1..b2   expiry, days since 2026-01-01, LE. 0 = never
                 b3..b6   device hash, LE. 0 = works on any device
                 b7..b9   nonce, LE. makes every key a different string
  mac             8 chars = 5 bytes = HMAC-SHA256(secret, payload)[0:5]

The nonce is why two buyers of the same plan on the same day do not receive the
same key. Without it the payload is a pure function of (tier, expiry, device), so
every key issued for a plan on a given day is byte-identical - and one buyer
sharing their key would open the product for every other buyer of that plan that
day. The site draws a random 24-bit nonce for every key; the CLI defaults to 0
and --nonce overrides it.

The device hash is

    int.from_bytes(sha256(secret + "|" + deviceId).digest()[:4], "big")

which the Java side recomputes byte for byte. Give a key with device 0 to a
user and it works anywhere; bind it and it works on one phone.

    python tools/keygen.py --secret <hex> --days 30
    python tools/keygen.py --secret <hex> --days 30 --device <android_id>
    python tools/keygen.py --secret <hex> --days 30 --nonce 41
    python tools/keygen.py --verify <key> --secret <hex> --device <android_id>
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
from datetime import date

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
EPOCH = date(2026, 1, 1)
VERSION = 2
PAYLOAD_CHARS = 16
MAC_CHARS = 8
NONCE_MAX = 1 << 24


def b32_encode(data: bytes, chars: int) -> str:
    bits = int.from_bytes(data, "big")
    total = chars * 5
    bits <<= (total - len(data) * 8)
    out = []
    for i in range(chars - 1, -1, -1):
        out.append(ALPHABET[(bits >> (i * 5)) & 31])
    return "".join(out)


def b32_decode(text: str, chars: int) -> bytes:
    total = chars * 5
    bits = 0
    for ch in text:
        idx = ALPHABET.find(ch)
        if idx < 0:
            raise ValueError("not in the alphabet: %r" % ch)
        bits = (bits << 5) | idx
    nbytes = (total - (total % 8)) // 8
    bits >>= (total - nbytes * 8)
    return bits.to_bytes(nbytes, "big")


def device_hash(secret: str, device_id: str) -> int:
    if not device_id:
        return 0
    h = hashlib.sha256(secret.encode() + b"|" + device_id.encode()).digest()
    return int.from_bytes(h[:4], "big")


def payload_bytes(version: int, tier: int, days: int, device: int,
                  nonce: int = 0, today: date | None = None) -> bytes:
    """days is time from today until expiry, not an absolute day number."""
    if days <= 0:
        exp = 0
    else:
        now = today or date.today()
        exp = (now - EPOCH).days + days
    n = int(nonce) % NONCE_MAX
    return bytes([((version & 0xF) << 4) | (tier & 0xF), exp & 0xFF, (exp >> 8) & 0xFF,
                  device & 0xFF, (device >> 8) & 0xFF, (device >> 16) & 0xFF,
                  (device >> 24) & 0xFF,
                  n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF])


def issue(secret: str, days: int = 30, tier: int = 1, device_id: str = "",
          nonce: int = 0, today: date | None = None) -> str:
    device = device_hash(secret, device_id)
    payload = payload_bytes(VERSION, tier, days, device, nonce, today=today)
    mac = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:5]
    body = b32_encode(payload, PAYLOAD_CHARS) + b32_encode(mac, MAC_CHARS)
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def parse(key: str) -> tuple[int, int, int, int, int, bytes]:
    body = key.replace("-", "").replace(" ", "").upper()
    if len(body) != PAYLOAD_CHARS + MAC_CHARS:
        raise ValueError("a key is %d characters, this one is %d"
                         % (PAYLOAD_CHARS + MAC_CHARS, len(body)))
    payload = b32_decode(body[:PAYLOAD_CHARS], PAYLOAD_CHARS)
    mac = b32_decode(body[PAYLOAD_CHARS:], MAC_CHARS)
    version = payload[0] >> 4
    tier = payload[0] & 0xF
    exp = payload[1] | (payload[2] << 8)
    device = payload[3] | (payload[4] << 8) | (payload[5] << 16) | (payload[6] << 24)
    nonce = payload[7] | (payload[8] << 8) | (payload[9] << 16)
    return version, tier, exp, device, nonce, mac


def verify(key: str, secret: str, device_id: str = "", today: date | None = None) -> tuple[bool, str]:
    try:
        version, tier, exp, device, _nonce, mac = parse(key)
    except (ValueError, IndexError) as e:
        return False, str(e)

    if version != VERSION:
        return False, "unsupported version %d" % version

    payload = b32_decode(key.replace("-", "").replace(" ", "").upper()[:PAYLOAD_CHARS], PAYLOAD_CHARS)
    want = hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:5]
    if not hmac.compare_digest(want, mac):
        return False, "the signature does not match"

    if device != 0:
        if not device_id:
            return False, "this key is bound to a device"
        if device_hash(secret, device_id) != device:
            return False, "this key belongs to another device"

    if exp != 0:
        now = today or date.today()
        left = exp - (now.toordinal() - EPOCH.toordinal())
        if left < 0:
            return False, "expired %d days ago" % (-left)

    return True, "ok (tier %d)" % tier


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="issue and check activation keys")
    ap.add_argument("--secret", required=True, help="the same string the app embeds")
    ap.add_argument("--days", type=int, default=30, help="0 means never expires")
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--device", default="", help="android id, empty for a key that works anywhere")
    ap.add_argument("--nonce", type=int, default=0, help="makes the key unique; 0 gives the canonical key for the day")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--verify", help="check a key instead of issuing one")
    args = ap.parse_args(argv)

    if args.verify:
        ok, why = verify(args.verify, args.secret, args.device)
        print(("ok    " if ok else "BAD   ") + why)
        return 0 if ok else 1

    device = device_hash(args.secret, args.device) if args.device else 0
    print("device hash %d%s" % (device, "" if device else "  (any device)"))
    for i in range(max(1, args.count)):
        print(issue(args.secret, args.days, args.tier, args.device, args.nonce + i))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))