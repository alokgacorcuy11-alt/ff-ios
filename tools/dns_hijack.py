#!/usr/bin/env python3
"""DNS hijack for the stealth path.

iOS WiFi proxy settings only capture proxy-aware (CFNetwork/URLSession) traffic.
Games that open raw sockets ignore the system proxy. Two ways around that:

  A) phone-side redirector  (Shadowrocket / Surge / Loon -> PROXY to your laptop)
  B) DNS hijack + transparent/port-forward on the laptop  (this file does DNS)

Usage
-----
  python tools/dns_hijack.py --auto                 # hijack every domain seen in logs/flows.jsonl
  python tools/dns_hijack.py --domain garena.com --domain freefiremobile.com
  python tools/dns_hijack.py --auto --upstream 1.1.1.1 --listen 0.0.0.0 --port 53

Then on the iPhone: WiFi -> Configure DNS -> Manual -> <laptop LAN IP>

NOTE about port 53 on Windows: it is usually free, but Windows may have the
"Internet Connection Sharing" service bound to it. If bind fails:
    net stop SharedAccess
or run with --port 5353 and point the router / a local DNS forwarder at it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import sys
import threading
import time

try:
    from dnslib import QTYPE, RR, A, DNSHeader, DNSRecord
    from dnslib.server import BaseResolver, DNSServer
except ImportError:
    print("[!] pip install dnslib", file=sys.stderr)
    raise SystemExit(1)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def auto_domains(trace: pathlib.Path) -> list[str]:
    doms: set[str] = set()
    if not trace.exists():
        print(f"[!] {trace} not found", file=sys.stderr)
        return []
    for line in trace.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        h = rec.get("host") or ""
        if not h or re.match(r"^\d+\.\d+\.\d+\.\d+$", h):
            continue
        parts = h.split(".")
        for i in range(len(parts) - 1):
            doms.add(".".join(parts[i:]))
    return sorted(doms)


class HijackResolver(BaseResolver):
    def __init__(self, targets: list[str], answer: str, upstream: str | None,
                 ttl: int = 30, verbose: bool = True):
        self.targets = [t.lower().lstrip(".") for t in targets]
        self.answer = answer
        self.upstream = upstream
        self.ttl = ttl
        self.verbose = verbose
        self.hits = 0
        self.misses = 0

    def _wanted(self, qname: str) -> bool:
        q = qname.lower().rstrip(".")
        return any(q == t or q.endswith("." + t) for t in self.targets)

    def resolve(self, request: DNSRecord, handler) -> DNSRecord:
        reply: DNSRecord = request.reply()
        qname = str(request.q.qname)
        qtype = QTYPE[request.q.qtype]

        if self._wanted(qname) and qtype in ("A", "ANY"):
            reply.add_answer(RR(qname, QTYPE.A, rdata=A(self.answer), ttl=self.ttl))
            self.hits += 1
            if self.verbose:
                print(f"  HIJACK {qname:<48} -> {self.answer}")
            return reply

        if not self._wanted(qname):
            self.misses += 1
            if self.upstream:
                fwd = self._forward(request)
                if fwd is not None:
                    return fwd
        if self.verbose:
            print(f"  pass   {qname:<48} {qtype}")
        return reply

    def _forward(self, request: DNSRecord) -> DNSRecord | None:
        try:
            data = request.pack()
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(3.0)
            s.sendto(data, (self.upstream, 53))
            resp, _ = s.recvfrom(4096)
            s.close()
            return DNSRecord.parse(resp)
        except Exception:
            return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", action="append", default=[], help="repeatable")
    ap.add_argument("--auto", action="store_true", help="hijack domains from the trace")
    ap.add_argument("--trace", default=str(ROOT / "logs" / "flows.jsonl"))
    ap.add_argument("--answer", default=None, help="IP to answer with (default: this host)")
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=53)
    ap.add_argument("--upstream", default="1.1.1.1", help="'' to disable forwarding")
    ap.add_argument("--ttl", type=int, default=30)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    targets = list(args.domain)
    if args.auto:
        targets += auto_domains(pathlib.Path(args.trace))
    targets = sorted(set(targets))
    if not targets:
        ap.error("nothing to hijack: pass --auto or --domain")

    answer = args.answer or lan_ip()
    upstream = args.upstream or None

    print("=" * 68)
    print(" DNS hijack")
    print("=" * 68)
    print(f" listen    : {args.listen}:{args.port}")
    print(f" answer    : {answer}")
    print(f" upstream  : {upstream or '(none)'}")
    print(f" targets   : {len(targets)}")
    for t in targets[:40]:
        print(f"   - {t}")
    if len(targets) > 40:
        print(f"   ... +{len(targets) - 40} more")
    print("=" * 68)

    res = HijackResolver(targets, answer, upstream, args.ttl, verbose=not args.quiet)
    server = DNSServer(res, port=args.port, address=args.listen, logger=None)
    server.start_thread()

    try:
        while True:
            time.sleep(30)
            print(f"[stats] hijacked={res.hits} passed={res.misses}")
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
