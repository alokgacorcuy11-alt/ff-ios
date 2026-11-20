#!/usr/bin/env python3
"""qris_from_image.py - pull the payload string out of a QRIS screenshot.

The seller's static QRIS lives in a merchant app, usually as an image and
nothing else. `QRIS_STATIC` needs the string behind that image, not the picture.

    python site/tools/qris_from_image.py qris.png
    python site/tools/qris_from_image.py qris.jpg --json

It tries the image as given, then upscaled and greyscaled, because screenshots
of QR codes are often small enough that the detector gives up on the first pass.
Prints the payload on one line and a verdict on the next. Needs opencv-python:

    pip install opencv-python-headless
"""

from __future__ import annotations

import argparse
import json
import sys


def attempts(path: str):
    import cv2

    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"cannot read {path}")

    yield img
    yield cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    yield cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    yield cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=3, fy=3,
                     interpolation=cv2.INTER_NEAREST)
    yield cv2.threshold(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 128, 255,
                        cv2.THRESH_BINARY)[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="decode a QRIS image into its payload string")
    ap.add_argument("image")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        import cv2  # noqa: F401
    except Exception:
        print("opencv is not installed: pip install opencv-python-headless", file=sys.stderr)
        return 2

    det = cv2.QRCodeDetector()
    text = ""
    for frame in attempts(a.image):
        text, _pts, _ = det.detectAndDecode(frame)
        if text:
            break

    if not text:
        print("no QR found. crop the image to the code itself, then try again.",
              file=sys.stderr)
        return 1

    looks_like = text.startswith("000201")
    out = {
        "payload": text,
        "bytes": len(text),
        "qris": looks_like,
        "hint": ("paste this as QRIS_STATIC" if looks_like
                 else "decoded, but it does not start with 000201 - this is not a QRIS code"),
    }
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(text)
        print(f"// {len(text)} bytes, "
              + ("QRIS payload - paste as QRIS_STATIC" if looks_like
                 else "not a QRIS payload"))
    return 0 if looks_like else 1


if __name__ == "__main__":
    sys.exit(main())
