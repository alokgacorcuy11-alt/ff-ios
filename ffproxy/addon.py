"""mitmproxy addon: Free Fire interceptor + live menu + rule engine.

Request path
    1. virtual host `menu.ff`            -> serve the control panel, never upstream
    2. sec.quiet ON                      -> pure pass-through, touch nothing
    3. ad / telemetry kill switches      -> 204, logged
    4. net.lag_ms / net.drop_pct         -> shape the traffic
    5. rules (gated on their `feature`)  -> rewrite / mock / block / log

Response path
    1. rules
    2. URL extraction (feeds the menu + the webview hijack)
    3. trace

All feature state comes from ControlState, which the menu writes. Toggling a
switch in the panel changes behaviour on the very next request.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# mitmproxy is only needed to RUN this addon, not to import it. The pure
# helpers in this module (_collect_urls, _walk_strings, the protobuf patching)
# are unit-tested by tools/selftest.py without the proxy stack installed, and a
# top-level import made that whole suite depend on mitmproxy being present -
# which is exactly how CI failed while the same suite passed locally.
#
# `http` appears in annotations only as a name (PEP 563, see __future__ above);
# every real use is inside a method body. So None here is safe at import time
# and is caught explicitly in running() if someone tries to run the addon
# without the dependency.
try:
    from mitmproxy import ctx, http
except ImportError:                                   # pragma: no cover
    ctx = None                                        # type: ignore[assignment]
    http = None                                       # type: ignore[assignment]

from . import jsonpath, pb
from .control import ControlState
from .menu_api import MenuAPI
from .rules import Rule, RuleStore

URL_RE = re.compile(rb"https?://[A-Za-z0-9\.\-_~:/?#\[\]@!$&'()*+,;=%]+")
ROOT = Path(__file__).resolve().parent.parent


def _resolve(p: Any) -> Path:
    """Relative paths are relative to the project root, not the cwd."""
    return ROOT / Path(str(p))


# ---------------------------------------------------------------- body specs

def bytes_from_spec(spec: Any) -> bytes:
    """hex:.. | str:.. | b64:.. | raw text"""
    if not isinstance(spec, str):
        spec = str(spec)
    if spec.startswith("hex:"):
        return bytes.fromhex(spec[4:].replace(" ", "").replace("\n", ""))
    if spec.startswith("str:"):
        return spec[4:].encode("utf-8")
    if spec.startswith("b64:"):
        return base64.b64decode(spec[4:])
    return spec.encode("utf-8")


def _text_of(msg: http.Message) -> Optional[str]:
    try:
        return msg.get_text(strict=True)
    except Exception:
        return None


def _set_body(msg: http.Message, data: bytes) -> None:
    msg.headers.pop("content-encoding", None)
    msg.content = data
    msg.headers["content-length"] = str(len(data))


def _redact(data: bytes, cap: int) -> str:
    if cap and len(data) > cap:
        return data[:cap].hex() + f"...(+{len(data) - cap}B)"
    return data.hex()


def _collect_urls(data: bytes, limit: int = 12) -> List[str]:
    out: List[str] = []
    for m in URL_RE.finditer(data or b""):
        try:
            u = m.group(0).decode("utf-8", "replace").rstrip("\\\"'")
        except Exception:
            continue
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def _walk_strings(node: Any, fn) -> Any:
    """Apply fn to every string in a JSON tree (in place)."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str):
                nv = fn(v)
                if nv != v:
                    node[k] = nv
            else:
                _walk_strings(v, fn)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                nv = fn(v)
                if nv != v:
                    node[i] = nv
            else:
                _walk_strings(v, fn)
    return node


# ---------------------------------------------------------------- addon

class FfAddon:
    def __init__(self, cfg: Dict[str, Any], store: RuleStore,
                 control: Optional[ControlState] = None):
        self.cfg = cfg
        self.store = store
        self.ff_re = re.compile(cfg["ff_hosts_regex"], re.I)
        self.telemetry_re = re.compile(cfg.get("telemetry_hosts_regex") or r"(?!x)x", re.I)
        self.ads_re = re.compile(cfg.get("ads_hosts_regex") or r"(?!x)x", re.I)
        self.only_ff = bool(cfg.get("only_ff", True))
        self.log_path = _resolve(cfg.get("log_dir", "logs")) / "flows.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        menu_html = _resolve(cfg.get("menu_html") or "menu/menu.html")
        self.control = control or ControlState(_resolve(cfg.get("state_dir", "state")),
                                               ROOT / "menu" / "features.json")
        self.menu = MenuAPI(self.control, menu_html, self.log_path, self._info)

        self._fh = None
        self._seq = 0
        self._counters = {"flows": 0, "blocked": 0, "modified": 0}
        self._started = time.time()
        self._warned: set[str] = set()

    # ------------------------------------------------------------ lifecycle
    def running(self) -> None:
        if ctx is None or http is None:
            raise RuntimeError(
                "mitmproxy is not installed - this addon cannot run without it. "
                "`pip install -r requirements.txt`. (Importing the module works "
                "without it on purpose: the pure helpers are unit-tested that way.)")
        self._fh = self.log_path.open("a", encoding="utf-8")
        ctx.log.info(f"[ff] trace   -> {self.log_path.resolve()}")
        ctx.log.info(f"[ff] rules   -> {len(self.store.rules)} loaded from {self.store.path}")
        ctx.log.info(f"[ff] menu    -> http://{self.menu.virtual_host}/  (and tools/menu_server.py on LAN)")
        ctx.log.info(f"[ff] state   -> {self.control.state_path.resolve()}")
        if self.store.error:
            ctx.log.warn(f"[ff] rules error: {self.store.error}")
        names = ", ".join(r.name for r in self.store.rules) or "(none)"
        ctx.log.info(f"[ff] active  : {names}")

    def done(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------ logging
    def _write(self, rec: Dict[str, Any]) -> None:
        if self._fh is None:
            return
        self._seq += 1
        rec["seq"] = self._seq
        rec["ts"] = round(time.time(), 3)
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()

    def _is_ff(self, flow: http.HTTPFlow) -> bool:
        return bool(self.ff_re.search(flow.request.host or ""))

    def _info(self) -> Dict[str, Any]:
        return {
            "rules": len(self.store.rules),
            "flows": self._counters["flows"],
            "blocked": self._counters["blocked"],
            "modified": self._counters["modified"],
            "uptime": int(time.time() - self._started),
            "rules_file": str(self.store.path),
            "address": self.menu.virtual_host,
            "quiet": self.control.is_on("sec.quiet"),
        }

    # ------------------------------------------------------------ request
    async def request(self, flow: http.HTTPFlow) -> None:
        self.control.maybe_reload()

        # 1. control panel virtual host
        if (flow.request.host or "").lower() == self.menu.virtual_host:
            body = flow.request.raw_content or b""
            status, ctype, payload = self.menu.handle(
                flow.request.method, flow.request.path, body)
            flow.response = http.Response.make(
                status, payload, MenuAPI.headers(ctype))
            return

        host = flow.request.host or ""

        # 2. quiet mode: touch nothing at all
        if self.control.is_on("sec.quiet", False):
            return

        # 3. ad / telemetry kill switches (these fire on non-FF hosts too)
        if self.control.is_on("net.block_ads", False) and self.ads_re.search(host):
            flow.response = http.Response.make(204, b"", {"content-length": "0"})
            self._counters["blocked"] += 1
            self._write({"kind": "blocked", "reason": "net.block_ads",
                         "host": host, "path": flow.request.path})
            return
        if self.control.is_on("net.block_telemetry", False) and self.telemetry_re.search(host):
            flow.response = http.Response.make(204, b"", {"content-length": "0"})
            self._counters["blocked"] += 1
            self._write({"kind": "blocked", "reason": "net.block_telemetry",
                         "host": host, "path": flow.request.path})
            return

        is_ff = self._is_ff(flow)
        if self.only_ff and not is_ff:
            return

        self._counters["flows"] += 1

        # 4. traffic shaping
        lag = self.control.number("net.lag_ms", 0) if is_ff else 0
        if lag > 0:
            await asyncio.sleep(lag / 1000.0)
        drop = self.control.number("net.drop_pct", 0) if is_ff else 0
        if drop > 0 and random.random() * 100.0 < drop:
            flow.response = http.Response.make(504, b"drop", {"content-length": "4"})
            self._counters["blocked"] += 1
            self._write({"kind": "blocked", "reason": "net.drop_pct",
                         "host": host, "path": flow.request.path})
            return

        # 5. rules
        self.store.maybe_reload()
        applied: List[str] = []
        for rule in self.store.rules:
            if rule.stage not in ("request", "both"):
                continue
            if not self._rule_active(rule, flow):
                continue
            applied.append(rule.name)
            try:
                acted = await self._apply_request(rule, flow)
            except Exception as e:
                ctx.log.warn(f"[ff] rule {rule.name} failed: {type(e).__name__}: {e}")
                continue
            if acted:
                break

        modified = bool(applied)
        if flow.response is not None:
            self._write(self._rec(flow, "request", applied, blocked=True,
                                  rule=applied[-1] if applied else None))
        elif modified or self.control.is_on("rule.enable_probe", True):
            if modified:
                self._counters["modified"] += 1
            self._write(self._rec(flow, "request", applied))

    async def _apply_request(self, rule: Rule, flow: http.HTTPFlow) -> bool:
        """True == flow short-circuited with a synthetic response."""
        p = rule.params
        a = rule.action

        if a in ("pass", "log", "probe_urls"):
            return False

        if a == "delay":
            await asyncio.sleep(float(p.get("ms", 0)) / 1000.0)

        elif a == "header":
            for k, v in (p.get("set") or {}).items():
                flow.request.headers[str(k)] = str(v)
            for k in (p.get("strip") or p.get("delete") or []):
                flow.request.headers.pop(str(k), None)

        elif a == "bytes_sub":
            _set_body(flow.request, _sub(flow.request.raw_content or b"", p))

        elif a == "block":
            status = int(p.get("status", 403))
            body = bytes_from_spec(p["body"]) if p.get("body") else b""
            flow.response = http.Response.make(
                status, body, {"content-type": p.get("content_type", "text/plain")})
            self._counters["blocked"] += 1
            ctx.log.info(f"[ff] BLOCK {flow.request.host}{flow.request.path} -> {status}")
            return True

        elif a == "redirect":
            flow.response = http.Response.make(
                int(p.get("status", 302)), b"", {"location": str(p["to"])})
            ctx.log.info(f"[ff] REDIR {flow.request.host}{flow.request.path} -> {p['to']}")
            return True

        elif a == "mock":
            flow.response = self._make_mock(p)
            ctx.log.info(f"[ff] MOCK  {flow.request.host}{flow.request.path}")
            return True

        return False

    # ------------------------------------------------------------ response
    async def response(self, flow: http.HTTPFlow) -> None:
        self.control.maybe_reload()
        if self.control.is_on("sec.quiet", False):
            return
        is_ff = self._is_ff(flow)
        if self.only_ff and not is_ff:
            return

        self.store.maybe_reload()
        applied: List[str] = []
        self._builtin_response(flow, applied)
        for rule in self.store.rules:
            if rule.stage not in ("response", "both"):
                continue
            if not self._rule_active(rule, flow):
                continue
            applied.append(rule.name)
            try:
                await self._apply_response(rule, flow)
            except Exception as e:
                ctx.log.warn(f"[ff] rule {rule.name} failed: {type(e).__name__}: {e}")

        if applied:
            self._counters["modified"] += 1
        self._write(self._rec(flow, "response", applied,
                              rule=applied[-1] if applied else None))

    def _builtin_response(self, flow: http.HTTPFlow, applied: List[str]) -> None:
        """Menu-driven edits that need no rule: maintenance / notices / region.

        Runs before the rules so a rule can still override the result.
        """
        resp = flow.response
        if resp is None:
            return
        want = (self.control.is_on("cfg.keep_alive", False)
                or self.control.is_on("cfg.mute_notices", False)
                or bool(str(self.control.get("cfg.force_region", "") or "").strip()))
        if not want:
            return
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            return
        text = _text_of(resp)
        if text is None:
            return
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return
        done = self._config_overrides(obj)
        if not done:
            return
        _set_body(resp, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())
        applied.append("builtin:cfg")
        ctx.log.info(f"[ff] CFG   {flow.request.path}: {', '.join(done)}")

    async def _apply_response(self, rule: Rule, flow: http.HTTPFlow) -> None:
        p = rule.params
        a = rule.action
        resp = flow.response
        if resp is None:
            return

        if a in ("pass", "log"):
            return

        if a == "delay":
            await asyncio.sleep(float(p.get("ms", 0)) / 1000.0)

        elif a == "mock":
            mock = self._make_mock(p)
            resp.status_code = mock.status_code
            for k, v in mock.headers.items():
                resp.headers[k] = v
            _set_body(resp, mock.content)
            ctx.log.info(f"[ff] MOCK  {flow.request.host}{flow.request.path}")

        elif a == "header":
            for k, v in (p.get("set") or {}).items():
                resp.headers[str(k)] = str(v)
            for k in (p.get("strip") or p.get("delete") or []):
                resp.headers.pop(str(k), None)

        elif a == "strip_headers":
            for k in (p.get("strip") or p.get("delete") or []):
                resp.headers.pop(str(k), None)

        elif a == "rewrite_json":
            text = _text_of(resp)
            if text is None:
                raise ValueError("response body is not decodable text")
            obj = json.loads(text)
            for path, val in (p.get("set") or {}).items():
                jsonpath.set_(obj, path, val)
            for path in (p.get("delete") or []):
                jsonpath.delete(obj, path)
            for path, val in (p.get("merge") or {}).items():
                jsonpath.set_(obj, path, jsonpath.deep_merge(jsonpath.get(obj, path, {}), val))
            _set_body(resp, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())
            ctx.log.info(f"[ff] JSON  {rule.name} <- {flow.request.host}{flow.request.path}")

        elif a == "rewrite_urls":
            text = _text_of(resp)
            if text is None:
                raise ValueError("response body is not decodable text")
            obj = json.loads(text)
            rx = re.compile(str(p.get("url_regex") or r"(?i)^https?://"))
            target = str(p.get("to") or p.get("replace") or f"http://{self.menu.virtual_host}/")
            cap = int(p.get("max", 8))
            hits = 0

            def sub_url(s: str) -> str:
                nonlocal hits
                if hits >= cap or not rx.search(s):
                    return s
                hits += 1
                return target

            _walk_strings(obj, sub_url)
            if hits:
                _set_body(resp, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())
                ctx.log.info(f"[ff] URLS  {rule.name}: rewrote {hits} url(s) -> {target}")
            else:
                ctx.log.debug(f"[ff] URLS  {rule.name}: no match in {flow.request.path}")

        elif a == "pb_set":
            data = resp.raw_content or b""
            for path, val in (p.get("set") or {}).items():
                data = pb.set_varint(data, str(path), int(val))
            for path, val in (p.get("set_bytes") or {}).items():
                data = pb.set_bytes(data, str(path), bytes_from_spec(val))
            for path, val in (p.get("append_bytes") or {}).items():
                data = pb.append_bytes(data, str(path), bytes_from_spec(val))
            _set_body(resp, data)
            ctx.log.info(f"[ff] PBPATCH {rule.name} <- {flow.request.host}{flow.request.path}")

        elif a == "bytes_sub":
            _set_body(resp, _sub(resp.raw_content or b"", p))
            ctx.log.info(f"[ff] BYTES {rule.name} <- {flow.request.host}{flow.request.path}")

    # ------------------------------------------------------------ features
    def _rule_active(self, rule: Rule, flow: http.HTTPFlow) -> bool:
        if not rule.matches(flow):
            return False
        if not rule.feature:
            return True
        if rule.feature not in self.control.defs and rule.feature not in self._warned:
            self._warned.add(rule.feature)
            ctx.log.warn(f"[ff] rule {rule.name}: feature {rule.feature!r} is not in "
                         f"menu/features.json - treating as ON")
        return self.control.is_on(rule.feature, True)

    def _config_overrides(self, obj: Any) -> List[str]:
        """Built-in response edits driven by the menu, applied to any JSON body."""
        done: List[str] = []
        if not isinstance(obj, (dict, list)):
            return done

        if self.control.is_on("cfg.keep_alive", False):
            n = _walk_keys_set(obj, {"maintenance": 0, "is_maintenance": False,
                                     "under_maintenance": False, "server_closed": False,
                                     "is_closed": False, "closed": False, "maintain": 0})
            if n:
                done.append(f"maintenance->off x{n}")

        if self.control.is_on("cfg.mute_notices", False):
            n = _walk_keys_set(obj, {"notice": "", "notices": [], "announcement": "",
                                     "announcements": [], "marquee": "", "popup": [],
                                     "banner": [], "ads": []})
            if n:
                done.append(f"notices->blank x{n}")

        region = str(self.control.get("cfg.force_region", "") or "").strip()
        if region:
            n = _walk_keys_set(obj, {"region": region, "reg": region, "country": region})
            if n:
                done.append(f"region->{region} x{n}")
        return done

    # ------------------------------------------------------------ helpers
    def _make_mock(self, p: Dict[str, Any]) -> http.Response:
        if p.get("body_file"):
            body = _resolve(p["body_file"]).read_bytes()
        elif p.get("body"):
            body = bytes_from_spec(p["body"])
        else:
            body = b""
        headers = {"content-type": str(p.get("content_type", "application/json"))}
        headers.update({str(k): str(v) for k, v in (p.get("headers") or {}).items()})
        return http.Response.make(int(p.get("status", 200)), body, headers)

    def _rec(self, flow: http.HTTPFlow, kind: str, applied: List[str],
             blocked: bool = False, rule: Optional[str] = None) -> Dict[str, Any]:
        req, resp = flow.request, flow.response
        rec: Dict[str, Any] = {
            "kind": kind,
            "method": req.method,
            "scheme": req.scheme,
            "host": req.host,
            "port": req.port,
            "path": req.path,
            "req_len": len(req.raw_content or b""),
            "matched": applied,
            "rule": rule,
            "blocked": blocked,
        }
        ct = req.headers.get("content-type") or ""
        if ct:
            rec["req_ct"] = ct
        if resp is not None:
            rec["status"] = resp.status_code
            rec["resp_len"] = len(resp.raw_content or b"")
            rec["resp_ct"] = (resp.headers.get("content-type") or "").split(";")[0].strip()
            urls = _collect_urls(resp.raw_content or b"")
            if urls:
                rec["urls"] = urls
        if self.control.is_on("debug.capture_bodies", bool(self.cfg.get("capture_bodies", False))):
            cap = int(self.cfg.get("max_capture_bytes", 32768))
            if req.raw_content:
                rec["req_hex"] = _redact(req.raw_content, cap)
            if resp is not None and resp.raw_content:
                rec["resp_hex"] = _redact(resp.raw_content, cap)
        return rec

    # ------------------------------------------------------------ errors
    def error(self, flow: http.HTTPFlow) -> None:
        if self._is_ff(flow):
            self._write({"kind": "error", "host": flow.request.host,
                         "path": flow.request.path, "error": str(flow.error)})


# ---------------------------------------------------------------- json utils

def _walk_keys_set(node: Any, mapping: Dict[str, Any], depth: int = 0) -> int:
    """Set v for every key in mapping, wherever it appears in the tree."""
    n = 0
    if depth > 12:
        return 0
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in mapping and not isinstance(v, (dict, list)):
                node[k] = mapping[k]
                n += 1
            elif isinstance(v, (dict, list)):
                n += _walk_keys_set(v, mapping, depth + 1)
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                n += _walk_keys_set(v, mapping, depth + 1)
    return n


def _sub(body: bytes, p: Dict[str, Any]) -> bytes:
    find = p.get("find")
    repl = p.get("replace", "")
    if find is None:
        return body
    if str(find).startswith("hex:"):
        needle = bytes.fromhex(str(find)[4:].replace(" ", ""))
        hay = bytes_from_spec(repl)
        if p.get("replace_all"):
            return body.replace(needle, hay)
        return body.replace(needle, hay, 1)
    needle_s, hay_s = str(find), str(repl)
    enc = "latin1" if p.get("encoding") == "latin1" else "utf-8"
    if p.get("replace_all"):
        return body.replace(needle_s.encode(enc), hay_s.encode(enc))
    return body.replace(needle_s.encode(enc), hay_s.encode(enc), 1)
