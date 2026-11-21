#!/usr/bin/env python3
"""patch_split_selection.py - keep install-time asset packs when cloning an app.

The bug this fixes
------------------
BlackBox's `BPackageManagerService.selectSplitApks()` picks at most three splits
when it copies an app into the container: one ABI, one density, one language.
Anything that is not one of those three is dropped.

Free Fire installs as three splits:

    base.apk
    split_config.arm64_v8a.apk          <- chosen, it is the ABI split
    split_asset_pack_install_time.apk   <- dropped, it is not

and that third one is 310 MB of the game's actual content: 44,672 entries
including assets/bin/Data/Managed/Metadata/global-metadata.dat. The guest then
starts Unity with no assets at all, which is a black screen that never ends -
exactly the symptom reported from a real phone, and nothing to do with the
game's anti-cheat.

What the patch does
-------------------
Keeps the same three choices, then also keeps every split that is not a config
split - those are the asset packs and other non-configuration pieces the base
APK expects to find beside it. Two things stay excluded: splits carrying a
foreign ABI (x86, mips - loading them would be worse than useless) and config
splits, whose selection the code above has already decided.

The patch is asserted, not hoped for: if the anchor is not found the build
fails, so a future base bump cannot quietly ship the old behaviour again.

  python3 container/patch_split_selection.py <path to the cloned base>
"""

from __future__ import annotations

import pathlib
import sys

REL = ("Bcore/src/main/java/top/niunaijun/blackbox/core/system/pm/"
       "BPackageManagerService.java")

ANCHOR = """        ArrayList<File> result = new ArrayList<>();
        if (abiSplit != null) result.add(abiSplit);
        if (densitySplit != null && densitySplit != abiSplit) result.add(densitySplit);
        if (langSplit != null && langSplit != abiSplit && langSplit != densitySplit) result.add(langSplit);
"""

PATCHED = """        ArrayList<File> result = new ArrayList<>();
        if (abiSplit != null) result.add(abiSplit);
        if (densitySplit != null && densitySplit != abiSplit) result.add(densitySplit);
        if (langSplit != null && langSplit != abiSplit && langSplit != densitySplit) result.add(langSplit);

        // Everything that is not a config split comes along too.
        //
        // The three picks above cover ABI, density and language. An app that
        // ships an install-time asset pack - Free Fire ships 310 MB of one, with
        // every asset the game owns inside it - has a fourth split with none of
        // those qualifiers, and dropping it produces a guest that starts Unity
        // with no data: a black screen that never ends.
        //
        // A foreign ABI split is still refused: copying an x86 library into an
        // arm64 guest is not "more complete", it is broken in a new way. Config
        // splits are left to the code above, which has already chosen.
        for (File f : nonBase) {
            if (f == null || f.equals(abiSplit) || f.equals(densitySplit) || f.equals(langSplit)) continue;
            String n = f.getName();
            if (n == null) continue;
            String lower = n.toLowerCase();
            if (lower.contains("x86") || lower.contains("mips")) continue;
            if (lower.contains("config.")) continue;
            result.add(f);
        }
"""


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: patch_split_selection.py <base dir>")
        return 2
    path = pathlib.Path(argv[0]) / REL

    print("=" * 66)
    print(" split selection patch")
    print("=" * 66)

    if not path.is_file():
        print(f"[FAIL] not found: {path}")
        return 1
    src = path.read_text(encoding="utf-8", errors="surrogateescape")

    if "Everything that is not a config split comes along too." in src:
        print("[ok  ] already patched")
        return 0

    if ANCHOR not in src:
        print("[FAIL] anchor not found - the base changed, the patch needs a look")
        return 1

    path.write_text(src.replace(ANCHOR, PATCHED, 1),
                    encoding="utf-8", errors="surrogateescape")
    print(f"[ok  ] patched {REL}")
    print("[ok  ] asset packs are now copied with the app")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))