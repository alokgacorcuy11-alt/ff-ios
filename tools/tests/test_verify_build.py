#!/usr/bin/env python3
"""test_verify_build.py - the publish gate against synthetic APKs.

CI trusts this script to answer one question: does the artefact really carry
the identity the build asked for. So the test builds APKs where the answer is
known - a manifest with a random-looking package id, a resources.arsc holding
the label, the daemon asset - and then breaks one thing at a time.

The certificate check is left out of the synthetic cases: it needs a real v1
signature, which means jarsigner, which means a JDK. CI runs the real thing on
the real APK. What is tested here is that a missing certificate is not treated
as a failed check on a machine that cannot look.

  python tools/tests/test_verify_build.py
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = ROOT / "tools" / "verify_build.py"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import apk_triage as at  # noqa: E402
import verify_build as vb  # noqa: E402
import test_apk_triage as t  # noqa: E402

FAIL = 0
PKG = "com.a1b2c3.d4e5f6"
LABEL = "Utilities"


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def build_axml(package: str) -> bytes:
    names = ["android", "http://schemas.android.com/apk/res/android",
             "manifest", "package", "versionName", "uses-sdk",
             "minSdkVersion", "targetSdkVersion", "application", "label",
             package, "1.0"]
    idx = {s: i for i, s in enumerate(names)}

    body = t.namespace(idx["android"],
                       idx["http://schemas.android.com/apk/res/android"])
    body += t.start_element(idx["manifest"], [
        (idx["package"], at.TYPE_STRING, idx[package]),
        (idx["versionName"], at.TYPE_STRING, idx["1.0"]),
    ])
    body += t.start_element(idx["uses-sdk"], [
        (idx["minSdkVersion"], at.TYPE_INT_DEC, 26),
        (idx["targetSdkVersion"], at.TYPE_INT_DEC, 34),
    ])
    body += t.end_element(idx["uses-sdk"])
    # the real build points at @string/app_name, so the label is a reference
    body += t.start_element(idx["application"], [
        (idx["label"], at.TYPE_REFERENCE, 0x7F010000),
    ])
    body += t.end_element(idx["application"])
    body += t.end_element(idx["manifest"])
    body += struct.pack("<HHIIIII", at.RES_XML_END_NS, 16, 24,
                        1, 0xFFFFFFFF, idx["android"],
                        idx["http://schemas.android.com/apk/res/android"])

    pool = t.string_pool(names)
    return struct.pack("<HHI", at.RES_XML, 8, 8 + len(pool) + len(body)) + pool + body


def build_arsc(label: str | None) -> bytes:
    # RES_TABLE_TYPE header + a string pool; verify_build only byte-searches it
    strings = ([label] if label else []) + ["app_name"]
    pool = t.string_pool(strings)
    return struct.pack("<HHI", 0x0002, 12, 12 + len(pool)) + pool


def build_apk(path: Path, package: str = PKG, arsc_label: str | None = LABEL,
              with_memd: bool = True) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", build_axml(package))
        if arsc_label is not False:  # False means "no resources.arsc at all"
            z.writestr("resources.arsc", build_arsc(arsc_label))
        z.writestr("classes.dex", b"\x64\x65\x78\x0a" + b"\x00" * 60)
        if with_memd:
            z.writestr("assets/memd", b"\x7fELF" + b"\x00" * 256)
        z.writestr("META-INF/CERT.RSA", b"\x30\x82\x01\x00" + b"\x00" * 64)


def main() -> int:
    print("=" * 66)
    print(" verify build (publish gate)")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="verify-build-"))

    # --- the happy path
    good = tmp / "good.apk"
    build_apk(good)
    problems, facts = vb.verify(good, PKG, LABEL, check_cert=False)
    check("clean build passes", problems == [], "; ".join(problems))
    check("package read back", facts.get("manifest_package") == PKG)
    check("sdk read back", (facts.get("min_sdk"), facts.get("target_sdk")) == (26, 34))
    check("label found in the table", facts.get("label_encoding") == "utf-8")
    check("apk hash recorded", facts.get("apk_sha256") == at.sha256(good.read_bytes()))

    # --- the build kept the old package id
    wrong = tmp / "wrong.apk"
    build_apk(wrong, package="com.alokg.ffmenu")
    problems, facts = vb.verify(wrong, PKG, LABEL, check_cert=False)
    check("a stale package id is caught",
          any("manifest package" in p for p in problems), str(problems))
    check("the stale id is reported, not the expected one",
          facts.get("manifest_package") == "com.alokg.ffmenu")

    # --- the label was never substituted
    nolabel = tmp / "nolabel.apk"
    build_apk(nolabel, arsc_label=None)
    problems, _ = vb.verify(nolabel, PKG, LABEL, check_cert=False)
    check("a label that never reached the table is caught",
          any("resources.arsc" in p for p in problems), str(problems))

    # --- no table at all
    noarsc = tmp / "noarsc.apk"
    build_apk(noarsc, arsc_label=False)
    problems, _ = vb.verify(noarsc, PKG, LABEL, check_cert=False)
    check("a missing resources.arsc is caught",
          any("resources.arsc is missing" in p for p in problems), str(problems))

    # --- the daemon was not packaged
    nomemd = tmp / "nomemd.apk"
    build_apk(nomemd, with_memd=False)
    problems, _ = vb.verify(nomemd, PKG, LABEL, check_cert=False)
    check("a missing daemon asset is caught",
          any("assets/memd" in p for p in problems), str(problems))

    # --- not an APK
    junk = tmp / "junk.apk"
    junk.write_bytes(b"this is not a zip")
    problems, _ = vb.verify(junk, PKG, LABEL, check_cert=False)
    check("an unreadable file is a problem, not a crash",
          any("cannot read" in p for p in problems), str(problems))

    # --- label encodings
    check("utf-16le label is found",
          vb.find_label("Util".encode("utf-16-le"), "Util") == "utf-16le")
    check("utf-8 label is found",
          vb.find_label(b"\x00Util\x00", "Util") == "utf-8")
    check("absent label is absent",
          vb.find_label(b"something else", "Util") is None)

    # --- keytool present, and what its absence of output means
    class _Run:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0

    orig_which, orig_run = vb.shutil.which, vb.subprocess.run
    try:
        vb.shutil.which = lambda name: "keytool"

        vb.subprocess.run = lambda *a, **k: _Run("Owner: CN=Android\n")
        cert, prob = vb.cert_info(Path("fixture.apk"))
        check("keytool without a digest is a failure, not silence",
              prob is not None and "v1" in prob, str(prob))

        vb.subprocess.run = lambda *a, **k: _Run(
            "Owner: CN=Android\nSHA256: AB:CD:EF\n")
        cert, prob = vb.cert_info(Path("fixture.apk"))
        check("a readable certificate is a pass",
              prob is None and cert.get("sha256") == "AB:CD:EF", str(cert))
    finally:
        vb.shutil.which, vb.subprocess.run = orig_which, orig_run

    # --- IDENTITY.txt is built from the facts
    text = vb.identity_text({
        "package": PKG, "label": LABEL, "apk_sha256": "ab" * 32,
        "min_sdk": 26, "target_sdk": 34,
        "cert": {"owner": "CN=Android", "sha256": "CD:EF"},
    })
    check("identity has the package", f"package: {PKG}" in text)
    check("identity has the label", f"label:   {LABEL}" in text)
    check("identity has the cert digest", "SHA256: CD:EF" in text)
    check("identity without a cert omits the line",
          "SHA256" not in vb.identity_text({"package": PKG, "label": LABEL}))

    # --- the CLI end to end
    out = tmp / "dist" / "IDENTITY.txt"
    # --no-cert-check: the fixture has no real v1 signature, and this machine
    # may or may not have a JDK. CI never passes this flag.
    r = subprocess.run([sys.executable, str(TOOL), "--apk", str(good),
                        "--package", PKG, "--label", LABEL,
                        "--no-cert-check",
                        "--identity", str(out)],
                       capture_output=True, text=True, timeout=60)
    check("cli exits 0", r.returncode == 0, r.stderr.strip()[:100])
    check("cli wrote IDENTITY.txt", out.is_file())
    if out.is_file():
        body = out.read_text(encoding="utf-8")
        check("cli identity names the package", PKG in body)
        check("cli identity names the label", LABEL in body)

    r = subprocess.run([sys.executable, str(TOOL), "--apk", str(wrong),
                        "--package", PKG, "--label", LABEL, "--no-cert-check"],
                       capture_output=True, text=True, timeout=60)
    check("cli exits 1 on a stale package", r.returncode == 1,
          f"rc={r.returncode}")
    check("cli says what is wrong",
          "expected" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-120:])

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
