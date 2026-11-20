#!/usr/bin/env python3
"""install.py - put the Android storefront into an existing Pages project.

The overlay in site/public is not a website. It is five files that belong next
to the ones already there, plus four Functions. Deploying site/public on its own
would replace the whole project - store.html, /api/buy, everything - so this
does the copy instead, and refuses to run if the target does not look like the
real project.

    python site/tools/install.py --static web/public --functions functions
    python site/tools/install.py --static web/public --dry-run

It does three things:

  1. copies site/public/*       -> --static
  2. copies site/functions/*    -> --functions
  3. adds one nav link to <static>/store.html, once, idempotently

Then it prints the wrangler commands with the real LICENSE_SECRET read out of
License.java, so the two sides cannot be set to different values by hand.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys

SITE = pathlib.Path(__file__).resolve().parent.parent
ROOT = SITE.parent
LICENSE_JAVA = (ROOT / "android" / "app" / "app" / "src" / "main"
                / "java" / "com" / "alokg" / "ffmenu" / "License.java")

NAV_ANCHORS = ('href="/activate"', 'href="/install"')
NAV_LINE = '<a href="/android" class="nav-admin">ANDROID</a>'


def say(msg: str = "") -> None:
    print(msg)


def read_secret() -> str | None:
    if not LICENSE_JAVA.exists():
        return None
    m = re.search(r'SECRET\s*=\s*"([^"]+)"', LICENSE_JAVA.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def find_functions(static: pathlib.Path) -> pathlib.Path | None:
    """Pages keeps functions/ next to the project root, not next to the output."""
    for candidate in (static.parent.parent / "functions", static.parent / "functions",
                      static / "functions"):
        if candidate.is_dir():
            return candidate
    return None


def looks_like_site(static: pathlib.Path) -> bool:
    return any((static / n).exists() for n in ("store.html", "index.html", "store"))


def copy_tree(src: pathlib.Path, dst: pathlib.Path, dry: bool) -> tuple[int, int, list[str]]:
    """Copy files, skipping identical ones. Returns (new, changed, names)."""
    new = changed = 0
    names: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        data = path.read_bytes()
        if target.exists():
            if target.read_bytes() == data:
                continue
            changed += 1
            action = "update"
        else:
            new += 1
            action = "add"
        names.append(f"{action:6} {rel}")
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return new, changed, names


def patch_nav(store: pathlib.Path, dry: bool) -> str:
    """Add the ANDROID link once. Returns a one-line report."""
    if not store.exists():
        return "no store.html - skipped (add the link by hand if your homepage has another name)"

    html = store.read_text(encoding="utf-8")
    if 'href="/android"' in html:
        return "store.html already links to /android - unchanged"

    lines = html.splitlines(keepends=True)
    at = -1
    for i, line in enumerate(lines):
        if any(anchor in line for anchor in NAV_ANCHORS):
            at = i
            break
    if at < 0:
        for i, line in enumerate(lines):
            if "</nav>" in line:
                at = i
                break
    if at < 0:
        return "store.html has no <nav> - skipped, add the link by hand"

    indent = re.match(r"[ \t]*", lines[at]).group(0)
    lines.insert(at, f"{indent}{NAV_LINE}\n")
    if not dry:
        store.write_text("".join(lines), encoding="utf-8")
    return "store.html: ANDROID link added before the existing nav buttons"


def main() -> int:
    ap = argparse.ArgumentParser(description="install the Android storefront into a Pages project")
    ap.add_argument("--static", required=True, help="the project's static output dir, e.g. web/public")
    ap.add_argument("--functions", default=None, help="the project's functions/ dir")
    ap.add_argument("--project", default="demios", help="Pages project name (for the printed commands)")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, touch nothing")
    ap.add_argument("--force", action="store_true", help="proceed even if the target looks empty")
    a = ap.parse_args()

    static = pathlib.Path(a.static).expanduser().resolve()
    if not static.is_dir():
        say(f"error: {static} is not a directory")
        return 2
    if static == (SITE / "public").resolve():
        say("error: --static points at the overlay itself. Give it your project's output dir.")
        return 2
    if not looks_like_site(static) and not a.force:
        say(f"error: {static} has no store.html / index.html - that does not look like the site.")
        say("       if you are sure, pass --force.")
        return 2

    functions = pathlib.Path(a.functions).expanduser().resolve() if a.functions else find_functions(static)
    if functions is None and not a.force:
        say("error: could not find the project's functions/ directory.")
        say("       pass --functions <dir> (it is the one that already contains api/).")
        return 2

    say(f"overlay   {SITE}")
    say(f"static    {static}")
    say(f"functions {functions if functions else '(none)'}")
    say(f"project   {a.project}")
    if a.dry_run:
        say("mode      DRY RUN - nothing will be written")
    say()

    new, changed, names = copy_tree(SITE / "public", static, a.dry_run)
    say(f"public    {new} added, {changed} updated, {len(names)} touched")
    for n in names:
        say("  " + n)

    if functions is not None:
        fnew, fchanged, fnames = copy_tree(SITE / "functions", functions, a.dry_run)
        say(f"functions {fnew} added, {fchanged} updated, {len(fnames)} touched")
        for n in fnames:
            say("  " + n)

    say()
    say(patch_nav(static / "store.html", a.dry_run))

    secret = read_secret()
    say()
    say("--- next, once ------------------------------------------------")
    if secret:
        say(f"LICENSE_SECRET   {secret}")
        say("                 (read out of License.java - it must be this exact string)")
    else:
        say("LICENSE_SECRET   could not read License.java; set it by hand")
    say()
    say("  wrangler kv namespace create DEMIOS_KV")
    say(f"  wrangler pages secret put LICENSE_SECRET --project-name {a.project}")
    say(f"  wrangler pages secret put ADMIN_SECRET   --project-name {a.project}")
    say(f"  wrangler pages secret put QRIS_STATIC    --project-name {a.project}")
    say(f"  wrangler pages deploy {static.name} --project-name {a.project}")
    say()
    say("The first deploy after this must come from your project root, so the")
    say("files that were already there are still there.")
    if a.dry_run:
        say()
        say("(dry run: nothing was written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
