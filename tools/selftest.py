#!/usr/bin/env python3
"""Self-test: protobuf codec + jsonpath + rule loading. Run after any edit.

  python tools/selftest.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffproxy import jsonpath, pb  # noqa: E402
from ffproxy.control import ControlState  # noqa: E402
from ffproxy.menu_api import MenuAPI  # noqa: E402
from ffproxy.rules import Rule, RuleStore  # noqa: E402

FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAIL
    mark = "ok  " if cond else "FAIL"
    print(f"[{mark}] {name} {extra}")
    if not cond:
        FAIL += 1


def build_sample() -> bytes:
    """Hand-crafted protobuf:
    1: varint 1
    2: string "ff"
    3: message { 1: varint 10, 2: string "x" }
    4: varint 42
    """
    inner = pb.encode([
        pb.Field(1, 0, 10),
        pb.Field(2, 2, b"x"),
    ])
    return pb.encode([
        pb.Field(1, 0, 1),
        pb.Field(2, 2, b"ff"),
        pb.Field(3, 2, inner),
        pb.Field(4, 0, 42),
    ])


def main() -> int:
    buf = build_sample()

    # round trip
    check("parse round-trip", pb.encode(pb.parse(buf)) == buf)

    # walk
    check("walk 1 = 1", pb.walk(buf, "1").value == 1)
    check("walk 2 = b'ff'", pb.walk(buf, "2").value == b"ff")
    check("walk 3.1 = 10", pb.walk(buf, "3.1").value == 10)
    check("walk 3.2 = b'x'", pb.walk(buf, "3.2").value == b"x")
    check("walk 4 = 42", pb.walk(buf, "4").value == 42)

    # mutate nested varint
    patched = pb.set_varint(buf, "3.1", 999)
    check("nested varint patch", pb.walk(patched, "3.1").value == 999)
    check("patch preserved siblings", pb.walk(patched, "3.2").value == b"x"
          and pb.walk(patched, "4").value == 42)
    check("length prefix recomputed", patched != buf and pb.parse(patched))

    # mutate bytes
    patched2 = pb.set_bytes(buf, "2", b"hello")
    check("bytes patch", pb.walk(patched2, "2").value == b"hello")

    # append
    patched3 = pb.append_bytes(buf, "3.3", b"new")
    check("append nested", pb.walk(patched3, "3.3").value == b"new")

    # append top-level
    patched4 = pb.append_bytes(buf, "9", b"top")
    check("append top-level", pb.walk(patched4, "9").value == b"top")

    # find
    hits = pb.find_varint(buf, 10)
    check("find_varint(10)", "3.1" in hits, f"-> {hits}")
    hits2 = pb.find_varint(buf, 1)
    check("find_varint(1)", "1" in hits2, f"-> {hits2}")

    # error handling
    try:
        pb.parse(b"\x08")  # tag 1 varint, truncated
        check("truncated varint raises", False)
    except pb.PbError:
        check("truncated varint raises", True)

    try:
        pb.walk(buf, "77")
        check("missing field raises", False)
    except KeyError:
        check("missing field raises", True)

    try:
        pb.walk(buf, "1.2")  # field 1 is varint, not a message
        check("non-message descent raises", False)
    except pb.PbError:
        check("non-message descent raises", True)

    # big varint
    big = pb.encode([pb.Field(7, 0, 2**40 + 5)])
    check("big varint round-trip", pb.walk(big, "7").value == 2**40 + 5)

    # repeated same tag
    rep = pb.encode([pb.Field(1, 0, 1), pb.Field(1, 0, 2), pb.Field(1, 0, 3)])
    check("repeated varints", [f.value for f in pb.parse(rep)] == [1, 2, 3])

    # describe does not crash on binary garbage
    try:
        list(pb.describe(b"\x00\x01\x02\xff\xff"))
        check("describe survives garbage", True)
    except Exception as e:  # noqa: BLE001
        check("describe survives garbage", False, repr(e))

    # ---------------------------------------------------------- jsonpath
    obj = {"data": {"user": {"level": 1}, "items": [{"id": "a"}, {"id": "b"}]}}
    check("json get nested", jsonpath.get(obj, "data.user.level") == 1)
    check("json get index", jsonpath.get(obj, "data.items[1].id") == "b")
    check("json get default", jsonpath.get(obj, "data.nope.deep", "d") == "d")
    jsonpath.set_(obj, "data.user.level", 99)
    check("json set nested", obj["data"]["user"]["level"] == 99)
    jsonpath.set_(obj, "data.items[1].id", "z")
    check("json set index", obj["data"]["items"][1]["id"] == "z")
    jsonpath.set_(obj, "data.new.path.x", 5)
    check("json set creates", obj["data"]["new"]["path"]["x"] == 5)
    check("json delete", jsonpath.delete(obj, "data.user.level"))
    check("json merge", jsonpath.deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}})

    # ---------------------------------------------------------- rules
    root = pathlib.Path(__file__).resolve().parent.parent
    try:
        store = RuleStore([root / "rules" / "freefire.yaml", root / "rules" / "menu.yaml"])
        check("ruleset loads", store.error is None, f"-> {len(store.rules)} rules, err={store.error}")
        check("actions valid", all(r.action for r in store.rules))
        featured = [r for r in store.rules if r.feature]
        check("feature gates parsed", len(featured) >= 2,
              f"-> {[r.feature for r in featured]}")
        check("multi-file merge", len(store.paths) == 2)
    except Exception as e:  # noqa: BLE001
        check("ruleset loads", False, repr(e))

    # ---------------------------------------------------------- control
    import json as _json
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ffproxy-test-"))
    try:
        defs = root / "menu" / "features.json"
        ctl = ControlState(tmp / "state", defs)
        check("features loaded", len(ctl.defs) >= 10, f"-> {len(ctl.defs)} keys")
        check("default applied", ctl.is_on("sec.quiet", None) is False)
        check("unknown key defaults", ctl.is_on("does.not.exist", True) is True)
        ctl.set_values({"sec.quiet": True, "net.lag_ms": 250})
        check("toggle persisted", ctl.is_on("sec.quiet") is True)
        check("range persisted", ctl.number("net.lag_ms") == 250)
        check("state file written", ctl.state_path.exists())
        # range clamping
        ctl.set_values({"net.lag_ms": 99999})
        check("range clamped to max", ctl.number("net.lag_ms") == 2000)
        ctl.set_values({"net.lag_ms": -500})
        check("range clamped to min", ctl.number("net.lag_ms") == 0)
        # rejects
        res = ctl.set_values({"bogus.key": 1})
        check("unknown key rejected", "bogus.key" in res["rejected"])
        # reload from disk (simulates the addon seeing the menu's write)
        ctl2 = ControlState(tmp / "state", defs)
        check("second reader sees values", ctl2.is_on("sec.quiet") is True)
        ctl2.set_values({"sec.quiet": False})
        import time as _t
        _t.sleep(0.30)  # cross-process write; the addon re-checks at most 4x/sec
        check("hot reload picks up change", ctl.maybe_reload() and ctl.is_on("sec.quiet") is False)
        ctl.reset()
        check("reset restores defaults", ctl.is_on("sec.quiet") is False)

        # ------------------------------------------------------ menu api
        api = MenuAPI(ctl, root / "menu" / "menu.html", tmp / "trace.jsonl",
                      info_fn=lambda: {"rules": 11, "flows": 3})
        st, ct, body = api.handle("GET", "/")
        check("GET / serves html", st == 200 and b"FF PROXY" in body, f"-> {ct}, {len(body)}B")
        check("html has no external refs",
              b"src=\"http" not in body and b"href=\"http" not in body)
        st, ct, body = api.handle("GET", "/api/state")
        snap = _json.loads(body)
        check("GET /api/state", st == 200 and "values" in snap and "groups" in snap)
        check("state carries live info", snap["info"]["rules"] == 11)
        st, ct, body = api.handle("POST", "/api/set",
                                  _json.dumps({"net.lag_ms": 120}).encode())
        applied = _json.loads(body)["applied"]["accepted"]
        check("POST /api/set applies", applied.get("net.lag_ms") == 120)
        check("POST /api/set writes through", ctl.number("net.lag_ms") == 120)
        st, ct, body = api.handle("POST", "/api/set", b"not json")
        check("POST /api/set rejects garbage", st == 400)
        st, ct, body = api.handle("POST", "/api/set", _json.dumps({"nope": 1}).encode())
        check("POST /api/set rejects unknown key",
              "nope" in _json.loads(body)["applied"]["rejected"])
        (tmp / "trace.jsonl").write_text(
            _json.dumps({"seq": 1, "kind": "response", "host": "a.garena.com",
                         "path": "/x", "status": 200, "matched": ["r1"]}) + "\n",
            encoding="utf-8")
        st, ct, body = api.handle("GET", "/api/log?n=5")
        check("GET /api/log", b"a.garena.com" in body)
        st, ct, body = api.handle("GET", "/nope")
        check("unknown route 404", st == 404)
        check("virtual host set", api.virtual_host == "menu.ff")

        # native schema endpoint (serves state/ff_native.json to the dylib)
        st, ct, body = api.handle("GET", "/api/native")
        native = _json.loads(body)
        check("GET /api/native", st == 200 and isinstance(native, dict))
        check("native endpoint points at state/ff_native.json",
              "_missing" in native or "player" in native,
              f"-> keys {list(native)[:4]}")
        (ctl.dir / "ff_native.json").write_text(
            _json.dumps({"player": {"container_class": "Room"}, "camera": {"class": "Cam"}}),
            encoding="utf-8")
        st, ct, body = api.handle("GET", "/api/native")
        got = _json.loads(body)
        check("native endpoint serves the real file", got.get("player", {}).get("container_class") == "Room")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------- feature gate
    fake = Rule(name="gated", action="log", feature="sec.quiet")
    plain = Rule(name="plain", action="log")

    class FakeReq:
        host = "api.freefiremobile.com"
        path = "/config"
        method = "GET"

    class FakeFlow:
        request = FakeReq()

    check("rule matches without gate", plain.matches(FakeFlow()))
    check("gated rule matches by matcher", fake.matches(FakeFlow()))

    # ---------------------------------------------------------- url hijack
    from ffproxy.addon import _collect_urls, _walk_strings
    payload = _json.dumps({
        "banner": [{"img": "https://cdn.garena.com/a.png",
                    "jump": "https://h5.garena.com/act/1"}],
        "note": "no url here",
    }).encode()
    urls = _collect_urls(payload)
    check("url extraction", any("h5.garena.com" in u for u in urls), f"-> {urls}")
    obj = _json.loads(payload)
    hits = []
    _walk_strings(obj, lambda s: (hits.append(s), "http://menu.ff/")[1]
                  if s.startswith("http") else s)
    check("url walking rewrites", len(hits) == 2 and obj["banner"][0]["jump"] == "http://menu.ff/")
    check("non-url strings untouched", obj["note"] == "no url here")

    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
