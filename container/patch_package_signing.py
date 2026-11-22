#!/usr/bin/env python3
"""patch_package_signing.py - stop a version-specific field from killing installs.

What was measured, on BlueStacks (Android 9, x86_64):

    tapping a game in the container's "Installed App" list
      -> AppsRepository.installApk: source=com.dts.freefireth
      -> BPackageManagerService.installPackageAsUserLocked(...:798)
         file=/data/app/com.dts.freefireth-.../base.apk
      -> java.lang.NoSuchFieldError: No field pastSigningCertificates of type
         [Landroid/content/pm/Signature; in class
         Landroid/content/pm/PackageParser$SigningDetails;
             at BPackage$SigningDetails.<init>(BPackage.java:447)
             at BPackage.<init>(BPackage.java:121)
             at Settings.getPackageLPw(Settings.java:46)
      -> install finish: 549ms

549 ms for a 400 MB game is not an install. The exception is caught further up,
the install reports success, no package record is written, the app never appears
in the list - and the only trace is a stack trace on System.err that nothing
reads. Observed afterwards: applicationList.size=0 and no freefire files under
the container's data directory.

`pastSigningCertificates` was added to PackageParser.SigningDetails in some
Android version and is absent in others. Reading it as a field is a runtime
error on the versions that lack it, and that error is not survivable because it
is thrown from a constructor on the install path.

The fix is reflection with a fallback, so the same code is correct everywhere:
where the field exists it is used, where it does not the current signatures are
the answer.

  python3 container/patch_package_signing.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = """        public SigningDetails(PackageParser.SigningDetails signingDetails) {
            if (signingDetails.pastSigningCertificates == null) {
                this.signatures = signingDetails.signatures;
            } else {
                this.signatures = signingDetails.pastSigningCertificates;
            }
        }"""

NEW = """        public SigningDetails(PackageParser.SigningDetails signingDetails) {
            // pastSigningCertificates is not present on every Android version
            // this has to run on, and reading it as a field is a NoSuchFieldError
            // on the ones that lack it. That error is thrown from this
            // constructor, which sits on the install path, so it travels up
            // through the package constructor and out of the install - where it
            // is caught. The install then reports success, no package record is
            // written, the app never appears in the list, and the only trace is
            // a stack trace on System.err that nothing reads.
            //
            // Reflection with a fallback is correct on every version: where the
            // field exists it is used, where it does not the current signatures
            // are the answer.
            if (signingDetails == null) {
                this.signatures = null;
                return;
            }
            Signature[] past = null;
            try {
                java.lang.reflect.Field field =
                        signingDetails.getClass().getField("pastSigningCertificates");
                past = (Signature[]) field.get(signingDetails);
            } catch (Throwable ignored) {
                // absent, or not public, on this version - that is the case this
                // exists to survive
            }
            this.signatures = (past != null) ? past : signingDetails.signatures;
        }"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = list(root.rglob("BPackage.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] BPackage.java not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    text = target.read_text(encoding="utf-8")
    if "pastSigningCertificates\");" in text or "field.get(signingDetails)" in text:
        print("  [skip] already applied")
        return 0
    if OLD not in text:
        raise SystemExit(
            "  [FAIL] anchor not found in BPackage.java.\n"
            "         The base changed. Read the file before patching it - a\n"
            "         patch that silently does nothing is worse than none.")
    target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("  [ok  ] constructor no longer reads a version-specific field directly")

    # The class must still import what the replacement uses.
    if "import android.content.pm.Signature;" not in text:
        head = target.read_text(encoding="utf-8")
        if "package top.niunaijun.blackbox.core.system.pm;" not in head:
            raise SystemExit("  [FAIL] unexpected file layout")
        head = head.replace(
            "package top.niunaijun.blackbox.core.system.pm;",
            "package top.niunaijun.blackbox.core.system.pm;\n\n"
            "import android.content.pm.Signature;",
            1)
        target.write_text(head, encoding="utf-8")
        print("  [ok  ] added the Signature import")

    print("  1 change applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())