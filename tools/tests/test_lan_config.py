#!/usr/bin/env python3
"""test_lan_config.py - pin the laptop -> phone config wire format.

The sender is Python, the receiver is Java. Nothing in a Python test can run
the Java side, so the next best thing is to pin every constant that both sides
hardcode, then run the Python sender against a real socket:

  * Config.KEY in the app is the same 32 bytes as DEFAULT_KEY here
  * Config.PORT, HDR/MAC/MAX_JSON, and the "FFC1" magic match
  * a packet round-trips, and its total length is 18 + len(json) + 32
  * a wrong key, a replayed sequence, a flipped byte, a truncated packet and
    an oversized payload are all rejected
  * the CLI actually delivers over UDP: a real listener accepts a real packet
  * a listener with the wrong key rejects it and says so

  python tools/tests/test_lan_config.py
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = ROOT / "tools" / "lan_config.py"
JAVA = ROOT / "android" / "app" / "app" / "src" / "main" / "java" / "com" / "alokg" / "ffmenu"

sys.path.insert(0, str(ROOT / "tools"))
import lan_config as lc  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def rejects(name, fn):
    try:
        fn()
    except ValueError as e:
        check(name, True, f"({e})")
        return
    except Exception as e:                                  # noqa: BLE001
        check(name, False, f"wrong exception {type(e).__name__}: {e}")
        return
    check(name, False, "accepted")


def java_const(path: Path, pattern: str):
    text = path.read_text(encoding="utf-8")
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else None


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    print("=" * 66)
    print(" laptop config channel (lan_config.py <-> LanConfig.java)")
    print("=" * 66)

    # ---- the two sides agree on every constant -------------------------------
    cfg_java = JAVA / "Config.java"
    lan_java = JAVA / "LanConfig.java"
    check("Config.java exists", cfg_java.exists())
    check("LanConfig.java exists", lan_java.exists())

    jkey = java_const(cfg_java, r'static final byte\[\] KEY\s*=\s*"([^"]*)"')
    check("the app key is the same 32 bytes", jkey == lc.DEFAULT_KEY,
          f"java={jkey!r} py={lc.DEFAULT_KEY!r}")
    check("the key really is 32 bytes", len(lc.key_bytes()) == 32,
          str(len(lc.key_bytes())))

    jport = java_const(cfg_java, r"static final int PORT\s*=\s*(\d+)")
    check("the port matches", jport == str(lc.DEFAULT_PORT), f"{jport} vs {lc.DEFAULT_PORT}")

    check("the magic matches", "'F', 'F', 'C', '1'" in lan_java.read_text(encoding="utf-8"))
    check("the header is 18 bytes", lc.HDR.size == 18, str(lc.HDR.size))
    check("LanConfig uses the same header size",
          java_const(lan_java, r"static final int HDR\s*=\s*(\d+)") == "18")
    check("LanConfig uses a 32-byte tag",
          java_const(lan_java, r"static final int MAC\s*=\s*(\d+)") == "32")
    check("LanConfig caps json at 1400",
          java_const(lan_java, r"static final int MAX_JSON\s*=\s*(\d+)") == "1400")
    check("lan_config.py caps json at the same number", lc.MAX_JSON == 1400)

    # the Java client reads little-endian explicitly; a big-endian header would
    # parse as a gigantic length and be dropped as "length"
    check("LanConfig reads the header little-endian",
          "ByteOrder.LITTLE_ENDIAN" in lan_java.read_text(encoding="utf-8"))

    # ---- the format itself ---------------------------------------------------
    payload = {
        "esp_box": True,
        "aim_enabled": False,
        "aim_smooth": 6,
        "esp_color": "#00ff7e",
        "watch": [{"addr": "0x7c00d01234", "type": "u32", "label": "health"}],
    }
    pkt = lc.pack(payload, 7)
    body = json.dumps(payload, separators=(",", ":")).encode()
    check("packet length is 18 + json + 32", len(pkt) == 18 + len(body) + 32,
          f"{len(pkt)} vs {18 + len(body) + 32}")
    check("the header carries the json length", pkt[16:18] == len(body).to_bytes(2, "little"))

    got, seq = lc.unpack(pkt)
    check("round trip returns the same payload", got == payload)
    check("round trip returns the sequence", seq == 7, str(seq))

    rejects("a wrong key is refused", lambda: lc.unpack(pkt, "x" * 32))
    rejects("a replayed sequence is refused", lambda: lc.unpack(pkt, last_seq=7))
    rejects("a truncated packet is refused", lambda: lc.unpack(pkt[:-1]))
    rejects("a header-only packet is refused", lambda: lc.unpack(pkt[:10]))
    rejects("a flipped json byte is refused",
            lambda: lc.unpack(pkt[:20] + bytes([pkt[20] ^ 1]) + pkt[21:]))
    rejects("a flipped tag byte is refused",
            lambda: lc.unpack(pkt[:-1] + bytes([pkt[-1] ^ 1])))
    rejects("bad magic is refused", lambda: lc.unpack(b"XXXX" + pkt[4:]))
    rejects("an oversized json is refused",
            lambda: lc.pack({"pad": "a" * 1400}, 1))

    # sequence numbers are u32, and the phone's guard is `seq <= last`
    rejects("a sequence past u32 is refused", lambda: lc.pack({}, 1 << 32))

    # ---- CLI helpers ---------------------------------------------------------
    check("--set parses booleans", lc.parse_value("true") is True
          and lc.parse_value("off") is False)
    check("--set parses ints in hex", lc.parse_value("0x10") == 16)
    check("--set parses floats", lc.parse_value("1.5") == 1.5)
    check("--set keeps strings", lc.parse_value("chest") == "chest")
    w = lc.parse_watch("0x1000:vec3:pos")
    check("--watch splits addr:type:label",
          w == {"addr": "0x1000", "type": "vec3", "label": "pos"}, str(w))
    rejects("--watch rejects an unknown type", lambda: lc.parse_watch("0x1000:i64:x"))

    # ---- end to end over a real socket ---------------------------------------
    port = free_port()

    def run_listener(key=None, timeout=6):
        cmd = [sys.executable, str(TOOL), "--listen", "--port", str(port),
               "--count", "1", "--timeout", str(timeout)]
        if key:
            cmd += ["--key", key]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)

    def run_sender(key=None, seq="1"):
        cmd = [sys.executable, str(TOOL), "--target", "127.0.0.1",
               "--port", str(port), "--seq", seq,
               "--set", "esp_box=false", "--watch", "0x2000:u32:test"]
        if key:
            cmd += ["--key", key]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)

    lis = run_listener()
    time.sleep(0.7)
    snd = run_sender()
    out, _ = lis.communicate(timeout=20)
    check("the sender exits 0", snd.returncode == 0, snd.stderr.strip()[:80])
    check("the listener accepts the packet", "[ok  ]" in out, out.strip().splitlines()[:1])
    check("the listener sees the sequence", "seq=1" in out)
    check("the listener sees the feature value", '"esp_box":false' in out)
    check("the listener sees the watch entry", "0x2000 u32 test" in out)
    check("the listener exits 0", lis.returncode == 0, str(lis.returncode))

    lis = run_listener(key="z" * 32, timeout=3)
    time.sleep(0.7)
    run_sender()                                    # signed with the real key
    out, _ = lis.communicate(timeout=15)
    check("a wrong-key listener rejects it", "[fail]" in out and "bad key" in out,
          out.strip().splitlines()[:1])
    check("a wrong-key listener exits 1", lis.returncode == 1, str(lis.returncode))

    # a replay of the same sequence must be dropped by the phone, so it must be
    # dropped by the listener too
    port2 = free_port()
    lis = subprocess.Popen(
        [sys.executable, str(TOOL), "--listen", "--port", str(port2),
         "--count", "2", "--timeout", "3"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(0.7)
    for _ in range(2):
        subprocess.run([sys.executable, str(TOOL), "--target", "127.0.0.1",
                        "--port", str(port2), "--seq", "42", "--set", "esp_box=true"],
                       capture_output=True, text=True, timeout=20)
    out, _ = lis.communicate(timeout=15)
    check("the second packet with the same seq is refused",
          out.count("[ok  ]") == 1 and "replayed" in out, out.strip().splitlines()[-2:])

    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
