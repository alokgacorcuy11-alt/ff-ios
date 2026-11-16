"""Entry point for `mitmdump -s`, so you can run the addon without run.py.

  mitmdump -s ffproxy/plugin_entry.py --listen-port 8080

  # reverse mode for the DNS-hijack path (one instance per host):
  mitmdump -s ffproxy/plugin_entry.py --mode reverse:https://api.freefiremobile.com --listen-port 8443

It reads config.yaml from the project root and appends the same rules.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from mitmproxy import ctx  # noqa: E402

from ffproxy.addon import FfAddon  # noqa: E402
from ffproxy.control import ControlState  # noqa: E402
from ffproxy.rules import RuleStore  # noqa: E402


def _cfg() -> dict:
    path = ROOT / "config.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = cfg or {}
    cfg.setdefault("log_dir", "logs")
    cfg.setdefault("state_dir", "state")
    cfg.setdefault("rules_file", ["rules/freefire.yaml", "rules/menu.yaml"])
    cfg.setdefault("ff_hosts_regex", r"garena|freefire|booyah|ggblue")
    cfg.setdefault("only_ff", True)
    cfg.setdefault("capture_bodies", False)
    cfg.setdefault("max_capture_bytes", 32768)
    # relative paths resolve against the project root, not the cwd
    cfg["log_dir"] = str(ROOT / cfg["log_dir"])
    cfg["state_dir"] = str(ROOT / cfg["state_dir"])
    return cfg


_cfg = _cfg()
_paths = _cfg["rules_file"]
if isinstance(_paths, str):
    _paths = [_paths]
_store = RuleStore([ROOT / p for p in _paths])
if _store.error:
    ctx.log.warn(f"[ff] rules error: {_store.error}")

_control = ControlState(_cfg["state_dir"], ROOT / "menu" / "features.json")

addons = [FfAddon(_cfg, _store, _control)]
