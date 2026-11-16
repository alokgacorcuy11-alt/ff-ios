"""
Schema-less protobuf wire-format codec.

Free Fire (Unity/IL2CPP, Garena) talks protobuf to several REST endpoints
(config, hot-update manifest, region list, some matchmaking RPCs). You cannot
get the .proto for a live build, so this module works directly on the wire
format: parse -> navigate by field-tag path -> replace the value -> re-encode.

Wire types handled: 0 varint, 1 fixed64, 2 length-delimited, 5 fixed32.
Groups (3/4) are rejected on purpose - they never appear in this target.

Path syntax: dotted field tags, e.g. "1.5.3".
    "5"      -> top-level field number 5
    "1.5"    -> field 5 inside the message carried by field 1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Sequence, Tuple

MAX_DEPTH = 64
_FIXED_SIZE = {1: 8, 5: 4}


class PbError(ValueError):
    pass


@dataclass
class Field:
    tag: int
    wire: int
    value: object   # int (wire 0) | bytes (wire 1/2/5)

    def __repr__(self) -> str:  # pragma: no cover
        if self.wire == 0:
            return f"Field(tag={self.tag}, wire=varint, value={self.value})"
        return f"Field(tag={self.tag}, wire={self.wire}, len={len(self.value)})"


def read_varint(buf: bytes, i: int) -> Tuple[int, int]:
    shift = 0
    val = 0
    n = len(buf)
    while True:
        if i >= n:
            raise PbError(f"truncated varint at offset {i}")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
        if shift > 63:
            raise PbError("varint longer than 10 bytes")


def write_varint(v: int) -> bytes:
    if v < 0:
        v &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse(buf: bytes) -> List[Field]:
    fields: List[Field] = []
    i = 0
    n = len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        tag, wire = key >> 3, key & 0x07
        if tag == 0:
            raise PbError(f"field tag 0 at offset {i}")
        if wire == 0:
            value, i = read_varint(buf, i)
        elif wire == 2:
            ln, i = read_varint(buf, i)
            if i + ln > n:
                raise PbError("truncated length-delimited field")
            value = buf[i:i + ln]
            i += ln
        elif wire in _FIXED_SIZE:
            ln = _FIXED_SIZE[wire]
            if i + ln > n:
                raise PbError("truncated fixed-width field")
            value = buf[i:i + ln]
            i += ln
        else:
            raise PbError(f"unsupported wire type {wire} for tag {tag}")
        fields.append(Field(tag, wire, value))
    return fields


def encode(fields: Sequence[Field]) -> bytes:
    parts: List[bytes] = []
    for f in fields:
        parts.append(write_varint((f.tag << 3) | f.wire))
        if f.wire == 0:
            parts.append(write_varint(int(f.value)))  # type: ignore[arg-type]
        elif f.wire == 2:
            body = f.value  # type: ignore[assignment]
            parts.append(write_varint(len(body)))
            parts.append(body)
        else:
            parts.append(f.value)  # type: ignore[arg-type]
    return b"".join(parts)


def _find(fields: Sequence[Field], tag: int) -> int:
    for idx, f in enumerate(fields):
        if f.tag == tag:
            return idx
    raise KeyError(f"field {tag} not present")


def _path(path: str) -> List[int]:
    if not path:
        raise PbError("empty path")
    return [int(p) for p in path.replace(",", ".").split(".") if p != ""]


def walk(buf: bytes, path: str) -> Field:
    """Return the Field object addressed by `path`."""
    fields = parse(buf)
    tags = _path(path)
    for depth, tag in enumerate(tags):
        f = fields[_find(fields, tag)]
        if depth == len(tags) - 1:
            return f
        if f.wire != 2:
            raise PbError(f"field {tag} is not a message (wire {f.wire})")
        fields = parse(f.value)  # type: ignore[arg-type]
    raise PbError("unreachable")


def _mutate(buf: bytes, tags: Sequence[int], setter, depth: int = 0) -> bytes:
    if depth > MAX_DEPTH:
        raise PbError("nesting too deep")
    fields = parse(buf)
    idx = _find(fields, tags[0])
    f = fields[idx]
    if len(tags) == 1:
        setter(f)
    else:
        if f.wire != 2:
            raise PbError(f"field {tags[0]} is not a message (wire {f.wire})")
        f.value = _mutate(f.value, tags[1:], setter, depth + 1)  # type: ignore[arg-type]
        f.wire = 2
    return encode(fields)


def set_varint(buf: bytes, path: str, value: int) -> bytes:
    def setter(f: Field) -> None:
        if f.wire != 0:
            raise PbError(f"field {f.tag} is wire {f.wire}, expected varint")
        f.value = int(value)
    return _mutate(buf, _path(path), setter)


def set_bytes(buf: bytes, path: str, data: bytes) -> bytes:
    def setter(f: Field) -> None:
        if f.wire != 2:
            raise PbError(f"field {f.tag} is wire {f.wire}, expected length-delimited")
        f.value = bytes(data)
    return _mutate(buf, _path(path), setter)


def append_bytes(buf: bytes, path: str, data: bytes) -> bytes:
    """Append a new length-delimited field with `parent` + tag equal to path."""
    tags = _path(path)
    parent_tags, leaf = tags[:-1], tags[-1]

    def setter_parent(f: Field) -> None:
        sub = parse(f.value)  # type: ignore[arg-type]
        sub.append(Field(leaf, 2, bytes(data)))
        f.value = encode(sub)

    if not parent_tags:
        fields = parse(buf)
        fields.append(Field(leaf, 2, bytes(data)))
        return encode(fields)
    return _mutate(buf, parent_tags, setter_parent)


def describe(buf: bytes, root: str = "", depth: int = 0) -> Iterator[str]:
    """Human readable tree, same shape as `protoc --decode_raw`."""
    pad = "  " * depth
    try:
        fields = parse(buf)
        nested_ok = True
    except PbError:
        nested_ok = False
        fields = []

    if not nested_ok:
        yield f"{pad}{_hex_short(buf)}"
        return

    for f in fields:
        name = f"{root}.{f.tag}" if root else str(f.tag)
        if f.wire == 0:
            yield f"{pad}{f.tag}: {f.value}"
        elif f.wire == 2:
            body: bytes = f.value  # type: ignore[assignment]
            if _looks_like_text(body):
                yield f'{pad}{f.tag}: "{body.decode("utf-8", "replace")}"'
            elif _sniff_message(body):
                yield f"{pad}{f.tag} {{"
                yield from describe(body, name, depth + 1)
                yield f"{pad}}}"
            else:
                yield f"{pad}{f.tag}: {_hex_short(body)}"
        else:
            yield f"{pad}{f.tag}: {_hex_short(f.value)}"  # type: ignore[arg-type]


def _hex_short(b: bytes, limit: int = 48) -> str:
    h = b[:limit].hex()
    return "hex:" + (h + "..." if len(b) > limit else h) + f" ({len(b)}B)"


def _looks_like_text(b: bytes) -> bool:
    if not b:
        return False
    printable = sum(1 for c in b if 32 <= c < 127 or c in (9, 10, 13))
    return printable / len(b) > 0.92


def _sniff_message(b: bytes) -> bool:
    if len(b) < 2:
        return False
    try:
        fields = parse(b)
    except PbError:
        return False
    return bool(fields) and len(fields) <= 64


def find_varint(buf: bytes, target: int, limit: int = 4096) -> List[str]:
    """Brute-force locate every varint field equal to `target` (recursive).

    Useful to find candidate fields to patch when you don't know the schema.
    """
    hits: List[str] = []

    def rec(b: bytes, path: str, depth: int) -> None:
        if depth > 8 or len(hits) >= limit:
            return
        try:
            fields = parse(b)
        except PbError:
            return
        for f in fields:
            p = f"{path}.{f.tag}" if path else str(f.tag)
            if f.wire == 0 and int(f.value) == target:  # type: ignore[arg-type]
                hits.append(p)
            elif f.wire == 2 and _sniff_message(f.value):  # type: ignore[arg-type]
                rec(f.value, p, depth + 1)  # type: ignore[arg-type]

    rec(buf, "", 0)
    return hits
