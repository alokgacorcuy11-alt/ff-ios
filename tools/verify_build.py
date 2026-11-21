#!/usr/bin/env python3
"""verify_build.py - the gate between a build and the world.

CI decides what identity a build should have. This decides whether the
artefact actually has it. Everything written into IDENTITY.txt is read back
out of the APK, not copied from what the build was asked to produce, so a
build that silently kept the old package id fails here instead of shipping.

Checks, in order:

  * `assets/memd` is NOT inside the APK - the buyer build ships no native
    binary; an ELF in assets is one of the things an installer scans for
  * the package id in the binary manifest equals --package
  * the label appears in `resources.arsc` (the manifest only holds a
    `@string/app_name` reference, so the string itself lives in the table)
  * the signing certificate digest, when keytool is available

  python tools/verify_build.py --apk dist/ffmenu.apk \\
      --package com.alokg.toolbox --label Toolbox \\
      --identity dist/IDENTITY.txt

Exit codes: 0 everything matches, 1 a check failed, 2 the APK could not be
read at all.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apk_triage as at  # noqa: E402

DAEMON_ASSET = "assets/memd"


def find_label(arsc: bytes, label: str) -> str | None:
    """Which encoding the label is stored in, or None.

    aapt2 keeps resource strings in a string pool that is UTF-8 unless the
    value needs UTF-16, so a plain byte search covers both cases without
    parsing the table.
    """
    if label.encode("utf-8") in arsc:
        return "utf-8"
    if label.encode("utf-16-le") in arsc:
        return "utf-16le"
    return None


def cert_info(apk: Path) -> tuple[dict, str | None]:
    """(facts, problem).

    A missing keytool is not a failed check - it means the machine cannot
    look. A keytool that runs and finds no certificate is a failed check,
    because that is exactly the case where IDENTITY.txt would ship without a
    digest and nobody would notice.
    """
    kt = shutil.which("keytool")
    if not kt:
        return {}, None
    try:
        r = subprocess.run([kt, "-printcert", "-jarfile", str(apk)],
                           capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return {}, None
    out = {}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        for key in ("Owner:", "Issuer:", "SHA256:", "SHA1:"):
            if line.startswith(key):
                out.setdefault(key[:-1].lower(), line[len(key):].strip())
    if not out.get("sha256"):
        return out, ("keytool found no signing certificate - the APK has no "
                     "v1 signature block, so IDENTITY.txt would carry no digest")
    return out, None


def verify(apk: Path, package: str, label: str,
           check_cert: bool = True) -> tuple[list[str], dict]:
    """Returns (problems, facts). An empty problems list means it passed."""
    problems: list[str] = []
    facts: dict = {"apk": str(apk), "package": package, "label": label}

    if not apk.is_file():
        return [f"no such file: {apk}"], facts
    try:
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
            arsc = zf.read("resources.arsc") if "resources.arsc" in names else b""
    except (zipfile.BadZipFile, OSError) as e:
        return [f"cannot read the APK: {e}"], facts

    if DAEMON_ASSET in names:
        problems.append(
            f"{DAEMON_ASSET} is inside the APK - the buyer build ships no native "
            f"binaries, and an ELF in assets is exactly what an installer flags")

    rep = at.triage(apk, [])
    man = rep.get("manifest") or {}
    facts["manifest_package"] = man.get("package")
    facts["min_sdk"] = man.get("minSdk")
    facts["target_sdk"] = man.get("targetSdk")
    facts["apk_sha256"] = rep["sha256"]

    if man.get("error"):
        problems.append(f"manifest could not be parsed: {man['error']}")
    elif man.get("package") != package:
        problems.append(
            f"manifest package is {man.get('package')!r}, expected {package!r}")

    if not arsc:
        problems.append("resources.arsc is missing, so the label cannot be checked")
    else:
        enc = find_label(arsc, label)
        facts["label_encoding"] = enc
        if enc is None:
            problems.append(
                f"label {label!r} is not in resources.arsc - the build kept "
                f"the default app_name")

    cert, cert_problem = cert_info(apk) if check_cert else ({}, None)
    if cert:
        facts["cert"] = cert
    if cert_problem:
        problems.append(cert_problem)

    return problems, facts


def identity_text(facts: dict) -> str:
    lines = [
        f"package: {facts['package']}",
        f"label:   {facts['label']}",
        f"apk:     {facts.get('apk_sha256', '?')}",
        f"sdk:     {facts.get('min_sdk')} -> {facts.get('target_sdk')}",
    ]
    cert = facts.get("cert") or {}
    for key, out in (("owner", "Owner:  "), ("issuer", "Issuer: "),
                     ("sha256", "SHA256: "), ("sha1", "SHA1:   ")):
        if cert.get(key):
            lines.append(f"{out}{cert[key]}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="check a built APK's identity")
    ap.add_argument("--apk", type=Path, required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--identity", type=Path,
                    help="write the facts here when the checks pass")
    ap.add_argument("--no-cert-check", action="store_true",
                    help="skip the certificate digest (for fixtures and "
                         "machines with no JDK; never used by CI)")
    args = ap.parse_args(argv)

    problems, facts = verify(args.apk, args.package, args.label,
                             check_cert=not args.no_cert_check)

    print(f"apk       {args.apk}")
    print(f"package   {facts.get('manifest_package')}")
    print(f"label     {facts['label']} ({facts.get('label_encoding') or 'not found'})")
    print(f"sdk       {facts.get('min_sdk')} -> {facts.get('target_sdk')}")
    print(f"sha256    {facts.get('apk_sha256')}")
    cert = facts.get("cert") or {}
    for key in ("owner", "sha256"):
        if cert.get(key):
            print(f"{key:<9} {cert[key]}")

    if problems:
        gh = os.environ.get("GITHUB_ACTIONS") == "true"
        for p in problems:
            print(f"::error::{p}" if gh else f"[!] {p}")
        print(f"\n{len(problems)} check(s) failed", file=sys.stderr)
        return 1

    if args.identity:
        args.identity.parent.mkdir(parents=True, exist_ok=True)
        args.identity.write_text(identity_text(facts), encoding="utf-8")
        print(f"\n-> {args.identity}")
    print("identity verified")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
