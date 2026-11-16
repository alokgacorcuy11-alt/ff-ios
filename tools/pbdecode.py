#!/usr/bin/env python3
"""Schema-less protobuf inspector / patcher. No .proto file needed.

  # dump a captured body that you pulled out of logs/flows.jsonl
  python tools/pbdecode.py body.bin

  # same, but from hex on the command line
  python tools/pbdecode.py --hex 0a0b1209...

  # read one field
  python tools/pbdecode.py body.bin --get 1.5

  # patch a varint and write body.bin.patched
  python tools/pbdecode.py body.bin --set 1.5=99

  # patch a length-delimited field
  python tools/pbdecode.py body.bin --set-bytes '2.1=str:hello'

  # find every nested field whose value is 1 (candidate boolean flags)
  python tools/pbdecode.py body.bin --find 1

  # pull a hex body straight out of a flows.jsonl line
  python tools/pbdecode.py --from-trace logs/flows.jsonl --seq 42
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ffproxy import pb  # noqa: E402


def bytes_from_spec(spec: str) -> bytes:
    if spec.startswith("hex:"):
        return bytes.fromhex(spec[4:].replace(" ", ""))
    if spec.startswith("str:"):
        return spec[4:].encode("utf-8")
    if spec.startswith("b64:"):
        import base64
        return base64.b64decode(spec[4:])
    return spec.encode()


def from_trace(path: pathlib.Path, seq: int | None, which: str) -> bytes:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if seq is not None and rec.get("seq") != seq:
            continue
        key = f"{which}_hex"
        if key in rec:
            h = rec[key].split("...")[0]
            return bytes.fromhex(h)
    raise SystemExit(f"shim: no {which}_hex found (capture_bodies must be true in config.yaml)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("--hex")
    ap.add_argument("--from-trace")
    ap.add_argument("--seq", type=int)
    ap.add_argument("--which", choices=["req", "resp"], default="resp")
    ap.add_argument("--get")
    ap.add_argument("--set", action="append", default=[], metavar="PATH=INT")
    ap.add_argument("--set-bytes", action="append", default=[], metavar="PATH=SPEC")
    ap.add_argument("--append-bytes", action="append", default=[], metavar="PATH=SPEC")
    ap.add_argument("--find", type=int)
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    if args.from_trace:
        data = from_trace(pathlib.Path(args.from_trace), args.seq, args.which)
    elif args.hex:
        data = bytes.fromhex(args.hex.replace(" ", ""))
    elif args.file:
        data = pathlib.Path(args.file).read_bytes()
    else:
        ap.error("need a file, --hex, or --from-trace")

    if args.find is not None:
        hits = pb.find_varint(data, args.find)
        print(f"{len(hits)} field paths with varint value {args.find}:")
        for h in hits:
            print("  " + h)
        return 0

    if args.get:
        f = pb.walk(data, args.get)
        if f.wire == 0:
            print(f"field {args.get} = {f.value} ({hex(int(f.value))})")
        elif f.wire == 2:
            body: bytes = f.value  # type: ignore[assignment]
            print(f"field {args.get} = {len(body)}B")
            print(body.hex())
        else:
            print(f"field {args.get} = {f.value.hex()}")  # type: ignore[union-attr]
        return 0

    original = data
    changed = False
    for spec in args.set:
        path, val = spec.split("=", 1)
        data = pb.set_varint(data, path, int(val, 0))
        changed = True
        print(f"set varint {path} = {int(val, 0)}")
    for spec in args.set_bytes:
        path, val = spec.split("=", 1)
        data = pb.set_bytes(data, path, bytes_from_spec(val))
        changed = True
        print(f"set bytes  {path} = {len(bytes_from_spec(val))}B")
    for spec in args.append_bytes:
        path, val = spec.split("=", 1)
        data = pb.append_bytes(data, path, bytes_from_spec(val))
        changed = True
        print(f"append     {path} += {len(bytes_from_spec(val))}B")

    if changed:
        out = pathlib.Path(args.out) if args.out else (
            pathlib.Path(args.file).with_suffix(pathlib.Path(args.file).suffix + ".patched")
            if args.file else pathlib.Path("patched.bin")
        )
        out.write_bytes(data)
        print(f"\n{len(original)}B -> {len(data)}B   written: {out}")
        # sanity: does it still parse?
        try:
            pb.parse(data)
            print("wire-format reparse: OK")
        except pb.PbError as e:
            print(f"wire-format reparse: FAILED ({e}) - upstream may reject this")
        return 0

    print("--- decode tree ---")
    for line in pb.describe(data):
        print(line)
    print(f"\n{len(data)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
