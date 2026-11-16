"""Tiny dotted-path accessor for JSON objects.

    "data.user.level"
    "items[0].id"
    "skus.list[3].price"
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

_TOKEN = re.compile(r"([^\.\[\]]+)|\[(\d+)\]")


class PathError(KeyError):
    pass


def tokens(path: str) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    for m in _TOKEN.finditer(path):
        if m.group(1) is not None:
            out.append(("key", m.group(1)))
        else:
            out.append(("idx", int(m.group(2))))
    if not out:
        raise PathError(f"bad path: {path!r}")
    return out


def get(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for kind, key in tokens(path):
        try:
            cur = cur[key] if kind == "key" else cur[key]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def set_(obj: Any, path: str, value: Any) -> Any:
    parts = tokens(path)
    cur = obj
    for kind, key in parts[:-1]:
        if kind == "key":
            if not isinstance(cur, dict):
                raise PathError(f"{path}: expected object at {key!r}")
            cur = cur.setdefault(key, {})
        else:
            if not isinstance(cur, list):
                raise PathError(f"{path}: expected array at [{key}]")
            while len(cur) <= key:
                cur.append({})
            cur = cur[key]
    kind, key = parts[-1]
    if kind == "key":
        if not isinstance(cur, dict):
            raise PathError(f"{path}: parent is not an object")
        cur[key] = value
    else:
        if not isinstance(cur, list):
            raise PathError(f"{path}: parent is not an array")
        while len(cur) <= key:
            cur.append(None)
        cur[key] = value
    return obj


def delete(obj: Any, path: str) -> bool:
    parts = tokens(path)
    cur = obj
    for kind, key in parts[:-1]:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return False
    kind, key = parts[-1]
    try:
        if kind == "key":
            cur.pop(key)
        else:
            cur.pop(key)
        return True
    except (KeyError, IndexError, TypeError):
        return False


def deep_merge(dst: Any, src: Any) -> Any:
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if k in dst and isinstance(dst[k], (dict, list)) and isinstance(v, type(dst[k])):
                deep_merge(dst[k], v)
            else:
                dst[k] = v
        return dst
    return src
