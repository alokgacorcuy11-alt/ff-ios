#!/usr/bin/env python3
"""lan_config.py - push FFMenu settings from the laptop over WiFi or a hotspot.

The phone listens on udp/47811 and never answers. This sends one authenticated
datagram; the phone applies it on the next frame. No connection, no DNS, no
TLS, nothing the phone has to acknowledge, so the wire carries no evidence that
a device ever talked to this machine.

Packet, little-endian - byte for byte what LanConfig.java parses:

    0..3    "FFC1"
    4..7    u32 sequence       must strictly increase on the phone
    8..15   u64 timestamp ms
    16..17  u16 json length    <= 1400
    18..    json
    +32     HMAC-SHA256(key, header + json)

Usage:

    # feature toggles
    python tools/lan_config.py --set esp_box=true --set aim_enabled=true

    # watch an address (offset hunting before the overlay exists)
    python tools/lan_config.py --watch 0x7c00d01234:u32:health

    # a live slider: resend every 250 ms with a new sequence number
    python tools/lan_config.py --set aim_smooth=6 --repeat 0 --interval 0.25

    # receive-side check, no phone needed
    python tools/lan_config.py --listen --port 47899

If the phone does not react, it is almost always the target address: a laptop
hotspot is not the default route, so 255.255.255.255 leaves through the wrong
interface. Pass the hotspot subnet's broadcast explicitly:

    python tools/lan_config.py --target 192.168.137.255 --set esp_box=true
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import socket
import struct
import sys
import time

MAGIC = b"FFC1"
HDR = struct.Struct("<4sIQH")          # 18 bytes, packed
MAC_LEN = 32
MAX_JSON = 1400
DEFAULT_PORT = 47811
DEFAULT_KEY = "ganti-ini-32-byte-rahasia-anda!!"

# A laptop hotspot is usually not the default route, so a datagram addressed to
# 255.255.255.255 leaves through WiFi/Ethernet and never reaches the phone.
# These are the broadcast addresses of the hotspots people actually use; the
# first one that matches the phone's subnet is the one that lands.
HOTSPOT_BROADCASTS = [
    "255.255.255.255",
    "192.168.137.255",   # Windows Mobile hotspot
    "192.168.43.255",    # Android tethering
    "192.168.49.255",    # some OEM Android tethering
    "172.20.10.255",     # iPhone personal hotspot
    "10.42.0.255",       # GNOME / Linux hotspot
]


def key_bytes(key: str | bytes | None = None) -> bytes:
    if key is None:
        key = DEFAULT_KEY
    if isinstance(key, bytes):
        return key
    return key.encode("utf-8")


def pack(payload: dict, seq: int, key: str | bytes | None = None,
         ts_ms: int | None = None) -> bytes:
    """Serialise one config packet. Raises ValueError if it cannot fit."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_JSON:
        raise ValueError(f"json is {len(body)} bytes, limit is {MAX_JSON}")
    if not 0 <= seq <= 0xFFFFFFFF:
        raise ValueError("seq must be a u32")

    ts = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    head = HDR.pack(MAGIC, seq, ts, len(body))
    mac = hmac.new(key_bytes(key), head + body, hashlib.sha256).digest()
    return head + body + mac


def unpack(data: bytes, key: str | bytes | None = None,
           last_seq: int = 0) -> tuple[dict, int]:
    """Verify and parse one packet. Raises ValueError on anything wrong."""
    if len(data) < HDR.size + MAC_LEN:
        raise ValueError(f"short packet ({len(data)} bytes)")

    magic, seq, _ts, jlen = HDR.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("bad magic")
    if jlen > MAX_JSON:
        raise ValueError(f"json length {jlen} over the limit")
    if HDR.size + jlen + MAC_LEN > len(data):
        raise ValueError("truncated packet")
    if seq <= last_seq:
        raise ValueError(f"replayed sequence {seq} (last {last_seq})")

    body = data[HDR.size:HDR.size + jlen]
    expect = hmac.new(key_bytes(key), data[:HDR.size + jlen], hashlib.sha256).digest()
    if not hmac.compare_digest(expect, data[HDR.size + jlen:HDR.size + jlen + MAC_LEN]):
        raise ValueError("bad key")

    return json.loads(body.decode("utf-8")), seq


def parse_value(text: str):
    low = text.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_watch(spec: str) -> dict:
    """addr:type:label  ->  {"addr": "0x...", "type": ..., "label": ...}"""
    parts = spec.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"--watch needs addr:type[:label], got {spec!r}")
    addr, typ = parts[0], parts[1]
    label = parts[2] if len(parts) > 2 else addr
    if typ not in ("u8", "u16", "u32", "u64", "f32", "vec3"):
        raise ValueError(f"unknown type {typ!r}")
    return {"addr": addr, "type": typ, "label": label}


def build_payload(sets: dict, watches: list[dict], reply: str | None,
                  raw_json: str | None) -> dict:
    payload: dict = {}
    if raw_json:
        loaded = json.loads(raw_json)
        if not isinstance(loaded, dict):
            raise ValueError("--json must be an object")
        payload.update(loaded)
    payload.update(sets)
    if watches:
        payload["watch"] = watches
    if reply:
        host, _, port = reply.partition(":")
        payload["reply"] = True
        payload["reply_to"] = {"host": host, "port": int(port or DEFAULT_PORT)}
    return payload


def listen(port: int, key: str | bytes | None, count: int, timeout: float) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    s.settimeout(0.5)
    print(f"listening udp/{port} (key {len(key_bytes(key))} bytes)")

    last = 0
    good = bad = 0
    deadline = time.time() + timeout
    while time.time() < deadline and (count <= 0 or good < count):
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as e:
            print(f"[err ] socket: {e}")
            break
        try:
            payload, seq = unpack(data, key, last)
        except ValueError as e:
            bad += 1
            print(f"[fail] {addr[0]}: {e}")
            continue
        last = seq
        good += 1
        keys = ",".join(k for k in payload if k not in ("watch", "reply_to"))
        print(f"[ok  ] seq={seq} {len(data)}B from {addr[0]} keys={keys}")
        print("       " + json.dumps(payload, separators=(",", ":")))
        for w in payload.get("watch", []):
            print(f"       watch {w.get('addr')} {w.get('type')} {w.get('label')}")

    s.close()
    print(f"{good} accepted, {bad} rejected")
    if good == 0:
        return 1
    return 0 if bad == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="push FFMenu config over the hotspot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="key defaults to the one compiled into the app; both must match")
    ap.add_argument("--target", action="append", metavar="IP",
                    help="send here instead of the hotspot broadcast list")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--seq", type=int, default=None,
                    help="sequence number (default: ms since epoch, low 31 bits)")
    ap.add_argument("--set", action="append", default=[], metavar="K=V",
                    help="a feature field, repeatable")
    ap.add_argument("--json", metavar="OBJ", help="raw JSON object to merge in")
    ap.add_argument("--watch", action="append", default=[], metavar="ADDR:TYPE[:LABEL]",
                    help="address to read on the phone, repeatable")
    ap.add_argument("--reply", metavar="HOST[:PORT]",
                    help="ask the phone to answer with the watch values")
    ap.add_argument("--repeat", type=int, default=1,
                    help="send N times, 0 = forever")
    ap.add_argument("--interval", type=float, default=0.25,
                    help="seconds between repeats")
    ap.add_argument("--listen", action="store_true",
                    help="receive and verify instead of sending")
    ap.add_argument("--count", type=int, default=0,
                    help="--listen: stop after N accepted packets")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="--listen: give up after this many seconds")
    args = ap.parse_args(argv)

    if args.listen:
        return listen(args.port, args.key, args.count, args.timeout)

    try:
        sets = {}
        for kv in args.set:
            k, sep, v = kv.partition("=")
            if not sep:
                raise ValueError(f"--set needs key=value, got {kv!r}")
            sets[k.strip()] = parse_value(v)
        watches = [parse_watch(w) for w in args.watch]
        payload = build_payload(sets, watches, args.reply, args.json)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[!] {e}", file=sys.stderr)
        return 2

    targets = args.target or HOTSPOT_BROADCASTS
    seq = args.seq if args.seq is not None else int(time.time() * 1000) & 0x7FFFFFFF

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    print(f"json: {json.dumps(payload, separators=(',', ':'))}")
    sent = 0
    try:
        while True:
            try:
                pkt = pack(payload, seq, args.key)
            except ValueError as e:
                print(f"[!] {e}", file=sys.stderr)
                return 2
            for host in targets:
                try:
                    s.sendto(pkt, (host, args.port))
                    print(f"-> {host}:{args.port} seq={seq} {len(pkt)}B")
                except OSError as e:
                    print(f"[!] {host}: {e}", file=sys.stderr)
            sent += 1
            if args.repeat and sent >= args.repeat:
                break
            seq = (seq + 1) & 0xFFFFFFFF
            time.sleep(max(args.interval, 0.02))
    finally:
        s.close()

    print("sent. if the phone did nothing, pass --target <hotspot broadcast>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
