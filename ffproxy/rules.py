"""Rule model + hot-reloadable store.

A rule = matcher (host / path / method) + one action + params.
All rules that match a flow are applied in file order; the last write wins.
"""

from __future__ import annotations

import pathlib
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

KNOWN_ACTIONS = {
    "log",           # record only
    "pass",          # explicit no-op (used to override a later rule? no -> keep for docs)
    "block",         # synthesize a response, never reach the server
    "redirect",      # 302 to another URL
    "delay",         # add latency (ms)
    "header",        # set/del request or response headers
    "mock",          # replace the whole response body
    "rewrite_json",  # set/delete/merge json paths
    "pb_set",        # protobuf field-level patch (no schema needed)
    "rewrite_urls",  # walk JSON, replace any URL string matching a regex
    "probe_urls",    # find + record every URL string in the body
    "bytes_sub",     # raw find/replace on the body
    "strip_headers", # drop response headers
}


@dataclass
class Rule:
    name: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    host_re: Optional[re.Pattern] = None
    path_re: Optional[re.Pattern] = None
    method: Optional[str] = None
    stage: str = "response"          # request | response | both
    enabled: bool = True
    feature: Optional[str] = None    # gate: only runs while this menu toggle is ON
    comment: str = ""

    def matches(self, flow) -> bool:
        if not self.enabled:
            return False
        req = flow.request
        if self.host_re and not self.host_re.search(req.host or ""):
            return False
        if self.path_re and not self.path_re.search(req.path or ""):
            return False
        if self.method and req.method.upper() != self.method.upper():
            return False
        return True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Rule":
        action = str(d.get("action", "log"))
        if action not in KNOWN_ACTIONS:
            raise ValueError(
                f"rule {d.get('name')!r}: unknown action {action!r} "
                f"(valid: {', '.join(sorted(KNOWN_ACTIONS))})"
            )
        m = d.get("match") or {}
        params = dict(d.get("params") or {})
        # also accept flattened keys so rules stay readable
        for k in ("status", "body", "body_file", "to", "ms", "set", "set_bytes",
                  "append_bytes", "delete", "merge", "strip", "find", "replace",
                  "replace_all", "encoding", "url_regex", "max"):
            if k in d and k not in params:
                params[k] = d[k]
        r = Rule(
            name=str(d.get("name") or f"rule-{action}"),
            action=action,
            params=params,
            host_re=re.compile(m["host_regex"], re.I) if m.get("host_regex") else None,
            path_re=re.compile(m["path_regex"], re.I) if m.get("path_regex") else None,
            method=m.get("method"),
            stage=str(d.get("stage", d.get("on", "response"))).lower(),
            enabled=bool(d.get("enabled", True)),
            feature=(str(d["feature"]) if d.get("feature") else None),
            comment=str(d.get("comment", "")),
        )
        return r


class RuleStore:
    """Loads one or more rules files and reloads when any mtime changes.

    Accepts a single path or a list of paths (later files do not override
    earlier ones - all matching rules run, in file order).
    """

    def __init__(self, paths):
        if isinstance(paths, (str, pathlib.Path)):
            paths = [paths]
        self.paths = [pathlib.Path(p) for p in paths]
        self.rules: List[Rule] = []
        self.defaults: Dict[str, Any] = {}
        self._mtimes: Dict[pathlib.Path, float] = {}
        self._lock = threading.Lock()
        self.error: Optional[str] = None
        self.reload()

    @property
    def path(self) -> pathlib.Path:
        return self.paths[0] if self.paths else pathlib.Path("(none)")

    def _stamp(self) -> Dict[pathlib.Path, float]:
        out: Dict[pathlib.Path, float] = {}
        for p in self.paths:
            try:
                out[p] = p.stat().st_mtime
            except OSError:
                out[p] = -1.0
        return out

    def maybe_reload(self) -> bool:
        if self._stamp() != self._mtimes:
            return self.reload()
        return False

    def reload(self) -> bool:
        with self._lock:
            rules: List[Rule] = []
            defaults: Dict[str, Any] = {}
            for path in self.paths:
                try:
                    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                except Exception as e:  # keep the previous ruleset on a broken file
                    self.error = f"{path.name}: {type(e).__name__}: {e}"
                    self._mtimes = self._stamp()
                    return False
                if not isinstance(raw, dict):
                    self.error = f"{path.name}: top level must be a mapping"
                    self._mtimes = self._stamp()
                    return False
                defaults.update(raw.get("defaults") or {})
                for item in raw.get("rules", []) or []:
                    try:
                        rules.append(Rule.from_dict(item))
                    except Exception as e:
                        self.error = f"{path.name}: {type(e).__name__}: {e}"
                        self._mtimes = self._stamp()
                        return False
            self.rules = rules
            self.defaults = defaults
            self.error = None
            self._mtimes = self._stamp()
            return True
