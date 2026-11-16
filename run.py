#!/usr/bin/env python3
"""ff-proxy launcher.

  python run.py                     # use config.yaml
  python run.py --port 8888         # override listen port
  python run.py --rules rules/x.yaml
  python run.py --no-ca-server      # don't serve the CA on :8081

Starts mitmproxy in explicit-proxy mode (WiFi proxy / Shadowrocket upstream)
and, next to it, a tiny HTTP server that hands the CA cert to the iPhone.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import pathlib
import socket
import socketserver
import sys
import threading

import yaml
from mitmproxy import options
from mitmproxy.tools import dump

from ffproxy.addon import FfAddon
from ffproxy.control import ControlState
from ffproxy.rules import RuleStore

ROOT = pathlib.Path(__file__).resolve().parent


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_config(path: pathlib.Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("listen_host", "0.0.0.0")
    cfg.setdefault("listen_port", 8080)
    cfg.setdefault("mode", ["regular"])
    cfg.setdefault("log_dir", "logs")
    cfg.setdefault("rules_file", ["rules/freefire.yaml", "rules/menu.yaml"])
    cfg.setdefault("ff_hosts_regex", r"garena|freefire|booyah|ggblue")
    cfg.setdefault("only_ff", True)
    cfg.setdefault("capture_bodies", False)
    cfg.setdefault("max_capture_bytes", 32768)
    cfg.setdefault("ignore_hosts", [])
    cfg.setdefault("ssl_insecure", False)
    cfg.setdefault("ca_server_port", 8081)
    cfg.setdefault("ca_server_host", "0.0.0.0")
    cfg.setdefault("state_dir", "state")
    cfg.setdefault("menu_html", "menu/menu.html")
    cfg.setdefault("telemetry_hosts_regex",
                   r"(?i)(analytic|telemetry|report|metrics|crash|sentry|bugsnag|"
                   r"firebase|adjust\.com|appsflyer|datadog|newrelic|loggly)")
    cfg.setdefault("ads_hosts_regex",
                   r"(?i)(^|\.)(ad|ads|advert|adservice|admob|adcolony|applovin|vungle|"
                   r"unityads|ironsrc|chartboost|pangle|bytedance|mintegral|mopub|"
                   r"doubleclick|adsystem|adnxs|tapjoy|fyber|smaato|inmobi)\.")
    return cfg


class _CaHandler(http.server.BaseHTTPRequestHandler):
    ca_dir: pathlib.Path = pathlib.Path.home() / ".mitmproxy"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/ca", "/ca.cer", "/mitmproxy-ca-cert.cer"):
            f = self.ca_dir / "mitmproxy-ca-cert.cer"
            if not f.exists():
                self.send_error(404, "CA not generated yet - start mitmproxy once")
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Disposition", 'attachment; filename="ffproxy-ca.cer"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(302)
        self.send_header("Location", "/ca.cer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # silence
        pass


class _Reuse(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_ca_server(cfg: dict) -> None:
    ca_dir = pathlib.Path(cfg.get("confdir") or (pathlib.Path.home() / ".mitmproxy"))
    handler = functools.partial(_CaHandler)
    _CaHandler.ca_dir = ca_dir
    try:
        srv = _Reuse((cfg["ca_server_host"], int(cfg["ca_server_port"])), handler)
    except OSError as e:
        print(f"[!] CA server not started on :{cfg['ca_server_port']} ({e})")
        return
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"[+] CA server  http://{lan_ip()}:{cfg['ca_server_port']}/ca.cer")


async def amain(cfg: dict) -> None:
    rules_paths = cfg["rules_file"]
    if isinstance(rules_paths, str):
        rules_paths = [rules_paths]
    store = RuleStore([ROOT / p for p in rules_paths])
    control = ControlState(ROOT / cfg["state_dir"],
                           ROOT / "menu" / "features.json")

    opts_kwargs = dict(
        listen_host=cfg["listen_host"],
        listen_port=int(cfg["listen_port"]),
        mode=list(cfg["mode"]),
        ssl_insecure=bool(cfg["ssl_insecure"]),
        ignore_hosts=list(cfg["ignore_hosts"]),
        http2=False,
        showhost=False,
    )
    if cfg.get("confdir"):
        opts_kwargs["confdir"] = cfg["confdir"]

    opts = options.Options(**opts_kwargs)
    master = dump.DumpMaster(opts, with_termlog=True, with_dumper=False)
    master.addons.add(FfAddon(cfg, store, control))

    print()
    print("=" * 64)
    print(" ff-proxy  |  Free Fire API interceptor")
    print("=" * 64)
    print(f" proxy      : {cfg['listen_host']}:{cfg['listen_port']}  (set this on the iPhone)")
    print(f" rules      : "
          + ", ".join(p.name for p in store.paths)
          + f"   [{len(store.rules)} rules, hot-reload ON]")
    print(f" trace      : {ROOT / cfg['log_dir'] / 'flows.jsonl'}")
    print(f" ff filter  : {cfg['ff_hosts_regex']}")
    print(f" LAN ip     : {lan_ip()}")
    print()
    print(f" MENU       : http://menu.ff/            (open in Safari, phone is on the proxy)")
    print(f"              http://{lan_ip()}:8082/     (run tools/menu_server.py for direct LAN)")
    print(f" state      : {control.state_path}")
    print(f" features   : {len(control.defs)} defined in menu/features.json")
    if store.error:
        print(f" [!] rules parse error: {store.error}")
    print("=" * 64)
    print()

    try:
        await master.run()
    except asyncio.CancelledError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Free Fire proxy interceptor")
    ap.add_argument("-c", "--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("-p", "--port", type=int)
    ap.add_argument("--rules")
    ap.add_argument("--mode", action="append", help="mitmproxy mode, repeatable")
    ap.add_argument("--body", action="store_true", help="capture request/response bodies")
    ap.add_argument("--no-ca-server", action="store_true")
    ap.add_argument("--confdir")
    args = ap.parse_args()

    cfg_path = pathlib.Path(args.config)
    cfg = load_config(cfg_path) if cfg_path.exists() else {}

    defaults = load_config(ROOT / "config.yaml")
    if not cfg:
        cfg = defaults
    else:
        for k, v in defaults.items():
            cfg.setdefault(k, v)

    if args.port:
        cfg["listen_port"] = args.port
    if args.rules:
        cfg["rules_file"] = args.rules
    if args.mode:
        cfg["mode"] = args.mode
    if args.body:
        cfg["capture_bodies"] = True
    if args.confdir:
        cfg["confdir"] = args.confdir

    (ROOT / cfg["log_dir"]).mkdir(parents=True, exist_ok=True)
    (ROOT / cfg["state_dir"]).mkdir(parents=True, exist_ok=True)

    if not args.no_ca_server:
        start_ca_server(cfg)

    try:
        asyncio.run(amain(cfg))
    except KeyboardInterrupt:
        print("\n[+] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
