#!/usr/bin/env python3
"""Summarise logs/flows.jsonl -> which hosts, which paths, what content types.

  python tools/hosts.py
  python tools/hosts.py logs/flows.jsonl --top 40
  python tools/hosts.py --regex          # print a ready-to-paste ff_hosts_regex
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(path: pathlib.Path):
    if not path.exists():
        print(f"[!] no trace at {path} - run the proxy first", file=sys.stderr)
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", nargs="?", default=str(ROOT / "logs" / "flows.jsonl"))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--paths", type=int, default=8, help="sample paths per host")
    ap.add_argument("--regex", action="store_true", help="emit a suggested ff_hosts_regex")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    hosts: collections.Counter = collections.Counter()
    paths: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    ctypes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    status: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    matched: collections.Counter = collections.Counter()
    total = 0
    resp_total = 0

    for rec in load(pathlib.Path(args.trace)):
        host = rec.get("host")
        if not host:
            continue
        total += 1
        hosts[host] += 1
        p = re.sub(r"\?.*$", "", rec.get("path", "") or "")
        paths[host][p] += 1
        if rec.get("resp_ct"):
            ctypes[host][rec["resp_ct"].split(";")[0].strip()] += 1
        if rec.get("status"):
            resp_total += 1
            status[host][str(rec["status"])] += 1
        for m in rec.get("matched") or []:
            matched[m] += 1

    if args.json:
        print(json.dumps({
            "hosts": hosts.most_common(),
            "total": total,
            "responses": resp_total,
        }, indent=2))
        return 0

    print(f"\n=== {total} flows ({resp_total} responses) from {args.trace} ===\n")
    print(f"{'flows':>7}  {'host':<44} status / content-type")
    print("-" * 100)
    for host, n in hosts.most_common(args.top):
        st = ",".join(f"{k}x{v}" for k, v in status[host].most_common(4)) or "-"
        ct = ",".join(f"{k}x{v}" for k, v in ctypes[host].most_common(2)) or "-"
        print(f"{n:>7}  {host:<44} {st}   {ct}")

    print("\n--- sample paths per host ---")
    for host, _ in hosts.most_common(args.top):
        print(f"\n{host}")
        for p, n in paths[host].most_common(args.paths):
            print(f"   {n:>5}x  {p[:120]}")

    if matched:
        print("\n--- rules that fired ---")
        for name, n in matched.most_common():
            print(f"   {n:>5}x  {name}")

    if args.regex:
        doms = sorted({".".join(h.split(".")[-2:]) for h in hosts
                       if not re.match(r"^\d+\.\d+\.\d+\.\d+$", h)})
        esc = "|".join(re.escape(d) for d in doms)
        print("\n--- suggested ff_hosts_regex (config.yaml) ---")
        print(f"ff_hosts_regex: '(?i)({esc})'")
        print("\n--- suggested ignore_hosts additions ---")
        for d in doms:
            print(f"  - '(^|\\.){re.escape(d)}$'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
