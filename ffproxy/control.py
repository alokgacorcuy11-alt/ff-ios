"""Runtime feature state, shared between the menu UI and the addon.

Both writers (the addon's built-in `menu.ff` endpoint, and tools/menu_server.py)
persist to the same files, so the menu works over the proxy OR over LAN:

    state/features.json   <- definitions (who exists, label, default, type)
    state/state.json      <- live values written by the menu

The addon polls mtimes, so a toggle takes effect on the next request - no
restart, no reload of anything.
"""

from __future__ import annotations

import json
import hashlib
import os
import pathlib
import threading
import time
from typing import Any, Dict, List, Optional


class ControlState:
    def __init__(self, state_dir: str | pathlib.Path,
                 definitions: str | pathlib.Path | None = None):
        self.dir = pathlib.Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.definitions_path = pathlib.Path(definitions) if definitions else (
            pathlib.Path(__file__).resolve().parent.parent / "menu" / "features.json"
        )
        self.state_path = self.dir / "state.json"
        self._lock = threading.RLock()
        self._def_mtime = -1.0
        self._state_mtime = -1.0
        self._fp: tuple = ()
        self._hash: Optional[str] = None
        self._hash_at = 0.0
        self.groups: List[dict] = []
        self.defs: Dict[str, dict] = {}
        self.values: Dict[str, Any] = {}
        self.stats: Dict[str, Any] = {}
        self._load_definitions()
        self._load_state()

    # ------------------------------------------------------------ loading
    def _load_definitions(self) -> None:
        try:
            raw = json.loads(self.definitions_path.read_text(encoding="utf-8"))
            self._def_mtime = self.definitions_path.stat().st_mtime
        except Exception:
            raw = {"groups": []}
            self._def_mtime = -1.0
        with self._lock:
            self.groups = raw.get("groups", []) or []
            self.defs = {}
            for g in self.groups:
                for item in g.get("items", []) or []:
                    key = item.get("key")
                    if not key:
                        continue
                    d = dict(item)
                    d.setdefault("group", g.get("id"))
                    d.setdefault("group_label", g.get("label"))
                    self.defs[key] = d
            # seed defaults for anything not present in the saved state
            for key, d in self.defs.items():
                self.values.setdefault(key, d.get("default"))

    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._state_mtime = self.state_path.stat().st_mtime
        except Exception:
            return
        with self._lock:
            vals = raw.get("values") or {}
            for k, v in vals.items():
                self.values[k] = v
            self.stats = raw.get("stats") or {}
        try:
            st = self.state_path.stat()
            self._fp = (st.st_mtime, st.st_size)
        except OSError:
            self._fp = ()

    def maybe_reload(self) -> bool:
        """Detect writes from the other front door.

        mtime alone is not enough: two writes inside one filesystem timestamp
        tick look identical. So we fingerprint (mtime, size) and, when that is
        unchanged, fall back to a content hash at most 4x/second.
        """
        changed = False
        try:
            m = self.definitions_path.stat().st_mtime
            if m != self._def_mtime:
                self._load_definitions()
                changed = True
        except OSError:
            pass

        try:
            st = self.state_path.stat()
            fp = (st.st_mtime, st.st_size)
        except OSError:
            return changed

        if fp != self._fp:
            self._load_state()
            self._fp = fp
            self._hash = self._digest()
            self._hash_at = time.time()
            return True

        now = time.time()
        if now - self._hash_at > 0.25:
            self._hash_at = now
            d = self._digest()
            if d is not None and d != self._hash:
                self._hash = d
                self._load_state()
                return True
        return changed

    def _digest(self) -> Optional[str]:
        try:
            return hashlib.sha1(self.state_path.read_bytes()).hexdigest()
        except OSError:
            return None

    # ------------------------------------------------------------ reads
    def is_on(self, key: str, default: bool = True) -> bool:
        with self._lock:
            if key not in self.values and key not in self.defs:
                return default
            v = self.values.get(key)
            if v is None:
                v = self.defs.get(key, {}).get("default")
            return bool(v)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            v = self.values.get(key)
            if v is None:
                v = self.defs.get(key, {}).get("default", default)
            return v

    def number(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "values": dict(self.values),
                "stats": dict(self.stats),
                "groups": self.groups,
                "definitions": {k: {kk: vv for kk, vv in v.items()} for k, v in self.defs.items()},
                "saved_at": self._saved_at(),
            }

    # ------------------------------------------------------------ writes
    def set_values(self, changes: Dict[str, Any]) -> dict:
        accepted: Dict[str, Any] = {}
        rejected: Dict[str, str] = {}
        with self._lock:
            for key, val in changes.items():
                d = self.defs.get(key)
                if d is None:
                    rejected[key] = "unknown feature"
                    continue
                t = d.get("type", "toggle")
                try:
                    if t == "toggle":
                        val = bool(val) if not isinstance(val, str) else val.lower() in ("1", "true", "on", "yes")
                    elif t == "range":
                        val = max(float(d.get("min", 0)), min(float(d.get("max", 1000)), float(val)))
                        val = int(val) if float(val).is_integer() else float(val)
                    elif t == "number":
                        val = float(val)
                    elif t == "text":
                        val = str(val)
                except (TypeError, ValueError):
                    rejected[key] = f"bad value for {t}"
                    continue
                accepted[key] = val
            self.values.update(accepted)
            self._persist_locked()
        return {"accepted": accepted, "rejected": rejected,
                "values": dict(self.values)}

    def reset(self) -> dict:
        with self._lock:
            for key, d in self.defs.items():
                self.values[key] = d.get("default")
            self._persist_locked()
            return dict(self.values)

    def record_stats(self, **kw: Any) -> None:
        with self._lock:
            self.stats.update(kw)
            self.stats["updated"] = int(time.time())
        self._persist_locked()

    def _saved_at(self) -> Optional[int]:
        try:
            return int(self.state_path.stat().st_mtime)
        except OSError:
            return None

    def _persist_locked(self) -> None:
        payload = {"values": self.values, "stats": self.stats,
                   "saved_at": int(time.time())}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)
        self._state_mtime = self.state_path.stat().st_mtime
        st = self.state_path.stat()
        self._fp = (st.st_mtime, st.st_size)
        self._hash = self._digest()
        self._hash_at = time.time()
