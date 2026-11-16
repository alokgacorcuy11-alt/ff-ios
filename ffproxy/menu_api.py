"""HTTP surface of the menu, shared by the addon (virtual host `menu.ff`) and
by tools/menu_server.py (real LAN port). One implementation, two front doors.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable, Dict, Optional, Tuple

from .control import ControlState

Response = Tuple[int, str, bytes]

JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"
TEXT = "text/plain; charset=utf-8"


class MenuAPI:
    def __init__(self, control: ControlState, html_path: str | pathlib.Path,
                 trace_path: str | pathlib.Path | None = None,
                 info_fn: Optional[Callable[[], Dict[str, Any]]] = None):
        self.control = control
        self.html_path = pathlib.Path(html_path)
        self.trace_path = pathlib.Path(trace_path) if trace_path else None
        self.info_fn = info_fn
        self.virtual_host = "menu.ff"

    # --------------------------------------------------------------- router
    def handle(self, method: str, path: str, body: bytes = b"") -> Response:
        method = method.upper()
        route = path.split("?", 1)[0].rstrip("/") or "/"

        if route in ("/", "/menu", "/index.html"):
            return self._html()

        if route == "/api/state" and method in ("GET", "POST"):
            return self._json(self._state())

        if route == "/api/set" and method in ("GET", "POST"):
            return self._set(body if method == "POST" else
                             path.split("?", 1)[1].encode() if "?" in path else b"")

        if route == "/api/reset" and method in ("GET", "POST"):
            self.control.reset()
            return self._json(self._state())

        if route == "/api/log":
            n = 40
            if "?" in path:
                from urllib.parse import parse_qs
                q = parse_qs(path.split("?", 1)[1])
                try:
                    n = max(1, min(500, int(q.get("n", ["40"])[0])))
                except ValueError:
                    pass
            return self._log(n)

        if route == "/api/ping":
            return self._json({"ok": True})

        if route == "/api/native":
            return self._native()

        if route == "/favicon.ico":
            return (204, TEXT, b"")

        return self._json({"error": "not found", "path": route}, 404)

    # --------------------------------------------------------------- payloads
    def _state(self) -> Dict[str, Any]:
        snap = self.control.snapshot()
        info: Dict[str, Any] = {}
        if self.info_fn:
            try:
                info = self.info_fn() or {}
            except Exception as e:  # noqa: BLE001
                info = {"info_error": repr(e)}
        snap["info"] = info
        snap["api"] = {"set": "/api/set", "state": "/api/state",
                       "log": "/api/log", "reset": "/api/reset"}
        return snap

    def _set(self, raw: bytes) -> Response:
        changes: Dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    changes = parsed
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        if not changes:
            return self._json({"error": "empty or invalid JSON body"}, 400)
        res = self.control.set_values(changes)
        out = self._state()
        out["applied"] = res
        return self._json(out)

    def _log(self, n: int) -> Response:
        if not self.trace_path or not self.trace_path.exists():
            return (200, TEXT, b"(no trace yet)")
        with self.trace_path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-n:]
        pretty = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                pretty.append(line)
                continue
            parts = [f"#{rec.get('seq')}", rec.get('kind', '?'),
                     f"{rec.get('host','')}{rec.get('path','')}"]
            if rec.get("status"):
                parts.append(f"-> {rec['status']}")
            if rec.get("matched"):
                parts.append("[" + ",".join(rec["matched"]) + "]")
            if rec.get("rule"):
                parts.append("rule=" + str(rec["rule"]))
            if rec.get("blocked"):
                parts.append("BLOCKED")
            pretty.append("  ".join(parts))
        return (200, TEXT, ("\n".join(pretty) or "(empty)").encode("utf-8"))

    # --------------------------------------------------------------- helpers
    def _html(self) -> Response:
        try:
            return (200, HTML, self.html_path.read_bytes())
        except OSError as e:
            return (500, TEXT, f"menu.html missing: {e}".encode())

    def _native(self) -> Response:
        """Serve state/ff_native.json - the dylib's class/field map.

        This is what makes the proxy part of the cheat instead of decoration:
        a game patch is fixed by editing this JSON, not by rebuilding the IPA.
        """
        path = self.control.dir / "ff_native.json"
        if not path.exists():
            example = self.html_path.parent.parent / "native" / "ff_native.example.json"
            if example.exists():
                return self._json(
                    {"_missing": str(path),
                     "_hint": "copy native/ff_native.example.json to " + str(path),
                     "example": json.loads(example.read_text(encoding="utf-8"))},
                    200)
            return self._json({"_missing": str(path)}, 200)
        try:
            return (200, JSON, path.read_bytes())
        except OSError as e:
            return self._json({"_error": str(e)}, 500)

    def _json(self, obj: Any, status: int = 200) -> Response:
        return (status, JSON, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def headers(content_type: str) -> Dict[str, str]:
        return {
            "Content-Type": content_type,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Connection": "close",
        }
