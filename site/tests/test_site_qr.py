#!/usr/bin/env python3
"""test_site_qr.py - the QR encoder, proved two ways.

A QR that is one module wrong is worse than no QR: the buyer scans it, the
wallet says "bukan QRIS", and the sale is gone. So this does not check the
encoder against its own arithmetic.

  1. exactness  every matrix, every mask, compared bit-for-bit with python
                `qrcode` - an independent, widely used implementation. This is
                the proof; if it passes, the symbol is correct by definition.
  2. decode     the matrix is rasterised and handed to OpenCV's QRCodeDetector.
                A detector miss is not a symbol error, so each sample gets three
                rasterisations and passes if any of them reads back the exact
                string.
  3. wire       the base64 packing the API ships must unpack to the same
                matrix, and the SVG fallback must stay small.

Skips (exit 0) when node is missing. The exactness layer additionally skips
when python `qrcode` is not installed; the decode layer skips when cv2 is not.
"""

from __future__ import annotations

import base64
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent

PASS = 0
FAIL = 0
SKIP = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"[ok  ] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}")


def skip(msg: str) -> None:
    global SKIP
    SKIP += 1
    print(f"[skip] {msg}")


SAMPLES = [
    "A",
    "HELLO WORLD",
    "DEMIOS",
    "https://demios.pages.dev/android",
    # a QRIS payload shape: 000201... tag 54 carries the amount
    "00020101021226670016COM.NOBUBANK.WWW01189360050300000898210214300000000000030303UMI"
    "51440014ID.CO.QRIS.WWW0215ID20200000000000303UMI52045411530336054072500005802ID5910"
    "DEMIOSSTORE6007JAKARTA61051234062070703A0163041A2B",
    "X" * 120,
    "Y" * 200,
    "Z" * 330,
    "Q" * 420,
]


def node_matrix(text: str) -> list[dict] | None:
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "tools/qr_masks.mjs", text], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        bad(f"qr_masks.mjs failed for {text[:20]!r}: {(r.stderr or '').strip()[-300:]}")
        return None
    return json.loads(r.stdout)


def node_probe() -> list[dict] | None:
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "tools/qr_probe.mjs"], cwd=str(SITE),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        bad("qr_probe.mjs failed: " + (r.stderr or "").strip()[-400:])
        return None
    return json.loads(r.stdout)


def layer_exact() -> None:
    try:
        import qrcode
        from qrcode.util import QRData, MODE_8BIT_BYTE
    except Exception as e:  # noqa: BLE001
        skip(f"python qrcode not installed ({e}); exactness layer skipped")
        return

    def reference(text: str, version: int, mask: int):
        q = qrcode.QRCode(version=version,
                          error_correction=qrcode.constants.ERROR_CORRECT_M,
                          mask_pattern=mask, border=0)
        q.add_data(QRData(text.encode(), mode=MODE_8BIT_BYTE))
        q.make(fit=False)
        return [[1 if v else 0 for v in row] for row in q.modules]

    compared = 0
    for text in SAMPLES:
        rows = node_matrix(text)
        if rows is None:
            return
        for row in rows:
            size = row["size"]
            mine = [row["m"][i * size:(i + 1) * size] for i in range(size)]
            want = reference(text, row["v"], row["mask"])
            if mine != want:
                diff = sum(1 for r in range(size) for c in range(size)
                           if mine[r][c] != want[r][c])
                bad(f"v{row['v']} mask {row['mask']} {text[:22]!r}: {diff} modules differ")
                return
            compared += 1
    ok(f"exact: {compared} matrices identical to the reference encoder "
       f"({len(SAMPLES)} payloads x 8 masks, v1-v16)")


def layer_decode() -> None:
    try:
        import numpy as np
        import cv2
    except Exception as e:  # noqa: BLE001
        skip(f"numpy/cv2 unavailable ({e}); decode layer skipped")
        return

    det = cv2.QRCodeDetector()
    misses = []
    for text in SAMPLES:
        rows = node_matrix(text)
        if rows is None:
            return
        # the mask the encoder actually ships is the lowest-penalty one; the
        # probe reports it, so decode exactly that symbol
        chosen = None
        for row in rows:
            chosen = row
            break
        probe = {r["text"]: r for r in (node_probe() or [])}
        pick = probe.get(text)
        if pick is not None:
            chosen = {"mask": pick["mask"], "v": pick["version"],
                      "size": pick["size"], "m": pick["matrix"]}
        if chosen is None:
            continue

        size = chosen["size"]
        m = np.array(chosen["m"], dtype=np.uint8).reshape(size, size)
        got = None
        for scale, quiet in ((8, 6), (11, 6), (6, 6), (8, 4)):
            dim = (size + quiet * 2) * scale
            img = np.full((dim, dim), 255, dtype=np.uint8)
            big = np.kron(m, np.ones((scale, scale), dtype=np.uint8))
            img[quiet * scale:quiet * scale + size * scale,
                quiet * scale:quiet * scale + size * scale] = np.where(big, 0, 255)
            decoded, _pts, _ = det.detectAndDecode(img)
            if decoded == text:
                got = (scale, quiet)
                break
        label = (text[:28] + "...") if len(text) > 31 else text
        if got:
            ok(f"decode v{chosen['v']:<2} {size:>2}x{size:<2} mask {chosen['mask']}"
               f"  {label}")
        else:
            misses.append((chosen["v"], chosen["mask"], label))
            bad(f"decode v{chosen['v']} mask {chosen['mask']} {label}: no rasterisation read back")

    if not misses:
        ok("decode: every shipped symbol read back exactly")


def layer_wire() -> None:
    data = node_probe()
    if data is None:
        return
    for row in data:
        size = row["size"]
        raw = base64.b64decode(row["bits"])
        got = [(raw[i >> 3] >> (7 - (i & 7))) & 1 for i in range(size * size)]
        if got == row["matrix"]:
            ok(f"pack v{row['version']} {size}x{size} -> {len(row['bits'])} b64 chars "
               f"({len(raw)} bytes for {size * size} modules)")
        else:
            bad(f"pack v{row['version']}: {sum(a != b for a, b in zip(got, row['matrix']))} "
                f"modules differ")

    for row in data:
        if row["svgLen"] > 200_000:
            bad(f"svg for v{row['version']} is {row['svgLen']} bytes")
        else:
            ok(f"svg v{row['version']} {row['svgLen']} bytes")


def layer_tool() -> None:
    """The tool the seller uses to get QRIS_STATIC, on an image we made."""
    try:
        import numpy as np
        import cv2
    except Exception as e:  # noqa: BLE001
        skip(f"numpy/cv2 unavailable ({e}); tool layer skipped")
        return
    import base64
    import tempfile

    text = SAMPLES[4]  # the QRIS-shaped payload
    data = node_probe()
    if data is None:
        return
    row = next((r for r in data if r["text"] == text), None)
    if row is None:
        bad("the QRIS sample is missing from qr_probe.mjs")
        return

    size = row["size"]
    raw = base64.b64decode(row["bits"])
    m = np.array([(raw[i >> 3] >> (7 - (i & 7))) & 1 for i in range(size * size)],
                 dtype=np.uint8).reshape(size, size)
    scale, quiet = 6, 4
    dim = (size + quiet * 2) * scale
    img = np.full((dim, dim), 255, dtype=np.uint8)
    big = np.kron(m, np.ones((scale, scale), dtype=np.uint8))
    img[quiet * scale:quiet * scale + size * scale,
        quiet * scale:quiet * scale + size * scale] = np.where(big, 0, 255)

    with tempfile.TemporaryDirectory(prefix="qris-") as tmp:
        path = pathlib.Path(tmp) / "qris.png"
        cv2.imwrite(str(path), img)
        r = subprocess.run([sys.executable, "tools/qris_from_image.py", str(path)],
                           cwd=str(SITE), capture_output=True, text=True, encoding="utf-8")
        first = (r.stdout or "").strip().splitlines()
        got = first[0] if first else ""
        if got == text and r.returncode == 0:
            ok(f"tools/qris_from_image.py reads a {dim}px render back byte for byte")
        else:
            bad(f"qris_from_image.py returned {got[:40]!r} (exit {r.returncode})")


def main() -> int:
    if shutil.which("node") is None:
        skip("node not on PATH - cannot exercise the encoder")
        return 0
    layer_exact()
    layer_decode()
    layer_wire()
    layer_tool()
    print()
    print(f"qr: {PASS} ok, {FAIL} failed, {SKIP} skipped")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
