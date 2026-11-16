#!/usr/bin/env python3
"""Standalone menu server - same panel + same state as the addon's `menu.ff`,
but on a real LAN port so any device (phone browser, another laptop) can open it.

  python tools/menu_server.py                 # binds 0.0.0.0:8082
  python tools/menu_server.py --port 9000

Both front doors write state/state.json, so toggling here changes what the
proxy does immediately. Run it next to run.py.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ffproxy.control import ControlState  # noqa: E402
from ffproxy.menu_api import MenuAPI  # noqa: E402
from ffproxy.rules import RuleStore  # noqa: E402


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(BaseHTTPRequestHandler):
    api: MenuAPI = None  # type: ignore[assignment]
    server_version = "ffproxy-menu/1"

    def _respond(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        status, ctype, payload = self.api.handle(method, self.path, body)
        self.send_response(status)
        for k, v in MenuAPI.headers(ctype).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if method != "HEAD" and payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._respond("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._respond("POST")

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        for k, v in MenuAPI.headers("text/plain").items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        if "/api/ping" in (self.path or ""):
            return
        if "/api/state" in (self.path or ""):
            return
        print(f"  [menu] {self.address_string()} {self.path}")


def trace_stats(path: pathlib.Path) -> dict:
    out = {"flows": 0, "blocked": 0, "modified": 0}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "request":
                    out["flows"] += 1
                if rec.get("blocked"):
                    out["blocked"] += 1
                if rec.get("matched"):
                    out["modified"] += 1
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--trace", default=str(ROOT / "logs" / "flows.jsonl"))
    args = ap.parse_args()

    control = ControlState(ROOT / "state", ROOT / "menu" / "features.json")

    rules_paths = ["rules/freefire.yaml", "rules/menu.yaml"]
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        rp = cfg.get("rules_file") or rules_paths
        rules_paths = [rp] if isinstance(rp, str) else list(rp)
    except Exception:
        pass
    store = RuleStore([ROOT / p for p in rules_paths])

    trace = pathlib.Path(args.trace)

    def info() -> dict:
        st = trace_stats(trace)
        st["rules"] = len(store.rules)
        st["address"] = f"{lan_ip()}:{args.port}"
        st["rules_file"] = str(control.state_path)
        st["quiet"] = control.is_on("sec.quiet")
        return st

    Handler.api = MenuAPI(control, ROOT / "menu" / "menu.html", trace, info_fn=info)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 60)
    print(" ff-proxy menu server")
    print("=" * 60)
    print(f" open      : http://{lan_ip()}:{args.port}/")
    print(f" state     : {control.state_path}")
    print(f" features  : {len(control.defs)}")
    print("=" * 60)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
