#!/usr/bin/env python3
"""qclasses.py - query out/classes.json, the offline metadata class dump.

What the dump actually is: a JSON object mapping each class name to the LIST OF
ITS FIELD NAMES. There are no field types in it, and there cannot be: the type
table for this build was not locatable offline (see native/REPORT-1.132.1.md
section 5), so field types only exist at runtime, on the device.

That is still useful, because it answers the "is this name even present?"
question that the resolver's candidate lists depend on:

  python tools/qclasses.py names COW.GamePlay.Player
  python tools/qclasses.py grep Transform            # classes with such a field
  python tools/qclasses.py field COW.GamePlay.Player MainCameraTransform
  python tools/qclasses.py classes Player
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DUMP = ROOT / "out" / "classes.json"
STATS = ROOT / "out" / "classes.stats.json"


def partial_note() -> str:
    """The class map is partial by construction, and a missing class must not be
    read as an empty one."""
    if not STATS.exists():
        return ("note: classes.json has no companion stats file, so it is not "
                "known whether it is complete.")
    try:
        s = json.loads(STATS.read_text(encoding="utf-8"))
    except Exception as e:                                      # noqa: BLE001
        return f"note: could not read {STATS.name}: {e}"
    return ("note: this map is PARTIAL - "
            f"{s.get('classes_skipped_above_cap')} class(es) above the "
            f"{s.get('class_field_cap')}-field cap have no entry at all "
            "(the player class is one of them). A miss here is not a "
            "confirmation that the field does not exist.")


def load() -> dict:
    if not DUMP.exists():
        sys.exit(f"missing {DUMP} - run tools/metadata_structure.py first")
    db = json.loads(DUMP.read_text(encoding="utf-8"))
    if not isinstance(db, dict):
        sys.exit(f"{DUMP} has an unexpected shape: {type(db).__name__}")
    return db


def fields_of(cls) -> list:
    """Field names for a class. Tolerates both shapes seen in the wild: a list of
    names, or a list of {name: ...} records."""
    out = []
    for f in (cls or []):
        if isinstance(f, str):
            out.append(f)
        elif isinstance(f, dict):
            n = f.get("name")
            if n:
                out.append(n)
    return out


def resolve(db: dict, want: str):
    if want in db:
        return want, db[want]
    tail = "." + want
    for k, v in db.items():
        if k.endswith(tail):
            return k, v
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["names", "field", "grep", "classes"])
    ap.add_argument("target")
    ap.add_argument("extra", nargs="?")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()

    db = load()
    print(f"classes in dump: {len(db)}")
    print(partial_note())

    if a.cmd == "field":
        key, cls = resolve(db, a.target)
        if key is None:
            print(f"no entry for {a.target} - see the note above before concluding "
                  f"the class has no such field")
            return 1
        names = fields_of(cls)
        hits = [n for n in names if a.extra and a.extra.lower() in n.lower()]
        print(f"{key}: {len(names)} field(s), {len(hits)} matching '{a.extra}'")
        for n in hits[:a.limit]:
            print(f"  {n}")
        return 0

    if a.cmd == "names":
        key, cls = resolve(db, a.target)
        if key is None:
            print(f"no class {a.target}")
            return 1
        names = fields_of(cls)
        print(f"{key}: {len(names)} field(s)")
        for n in names[:a.limit]:
            print(f"  {n}")
        if len(names) > a.limit:
            print(f"  ... ({len(names) - a.limit} more)")
        return 0

    if a.cmd == "grep":
        want = a.target.lower()
        found = 0
        for key, cls in db.items():
            hits = [n for n in fields_of(cls) if want in n.lower()]
            if not hits:
                continue
            print(f"{key}  ({len(fields_of(cls))} fields)")
            for n in hits[:6]:
                print(f"    {n}")
            found += 1
            if found >= a.limit:
                break
        print(f"{found} class(es) with a matching field name")
        return 0

    # classes
    want = a.target.lower()
    n = 0
    for key in db:
        if want in key.lower():
            print(f"  {key}   ({len(fields_of(db[key]))} fields)")
            n += 1
            if n >= a.limit:
                break
    print(f"shown {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
