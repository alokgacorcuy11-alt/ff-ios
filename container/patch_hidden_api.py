#!/usr/bin/env python3
"""patch_hidden_api.py - let the container reach the framework internals it needs.

Measured on BlueStacks (Android 9), adding a game to the container:

    W lacksbbox:blac: Accessing hidden field
        PackageParser$SigningDetails;->pastSigningCertificates (dark greylist)
    W System.err: java.lang.NoSuchFieldError: No field baseCodePath of type
        Ljava/lang/String; in class Landroid/content/pm/PackageParser$Package;
    D BPackageManagerService: install finish: 480ms

Android 9 introduced hidden API restrictions. A field or method the platform
marks hidden cannot be reached from app code at all, and the failure surfaces as
a NoSuchFieldError thrown from somewhere unrelated to the cause. This container
reaches into framework internals by design - PackageParser.Package.baseCodePath,
SigningDetails.pastSigningCertificates, and others we have not hit yet, because
the chain stops at the first refusal - so on Android 9 and up every one of them
is denied, and the install reports success having written nothing.

Two things are done here, and the second is the one that matters:

  1. Bcore/utils/HiddenApi.java exempts the process. One call covers every
     hidden field and method, including the ones we have not discovered, which
     is why it is better than fixing them one at a time.

  2. It is called as the first statement of App.attachBaseContext, which runs in
     every process the app starts - including :black, which is where the package
     manager lives. An exemption applied later is an exemption applied after the
     first refusal has already happened.

setHiddenApiExemptions was removed in Android 14. Where it is gone the call is a
no-op and the per-site reflection in patch_package_signing.py carries the load.

  python3 container/patch_hidden_api.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

JAVA = '''package top.niunaijun.blackbox.utils;

import android.util.Log;

import java.lang.reflect.Method;

/**
 * Android 9 restricts "hidden" platform APIs: a field or method the framework
 * marks hidden cannot be reached from app code, and the refusal arrives as a
 * NoSuchFieldError thrown from code that looks unrelated to the cause.
 *
 * BlackBox reaches into framework internals by design, so on Android 9 and up
 * every one of those reaches is refused, and an install can report success while
 * writing nothing at all. Exempting the process fixes all of them at once,
 * including the ones not yet found - the failure chain stops at the first
 * refusal, so the full list is not knowable in advance.
 *
 * The two-step reflection below is the standard way in: Class.forName and
 * Class.getDeclaredMethod are themselves exempt from the restriction, which is
 * what makes it possible to obtain VMRuntime and ask it for the exemption.
 *
 * Removed in Android 14 - there the call fails and is logged, and the callers
 * that can fall back do.
 */
public final class HiddenApi {

    private HiddenApi() {
    }

    public static void free() {
        try {
            Method forName = Class.class.getDeclaredMethod("forName", String.class);
            Method getDeclaredMethod = Class.class.getDeclaredMethod(
                    "getDeclaredMethod", String.class, Class[].class);

            Class<?> vmRuntime = (Class<?>) forName.invoke(null, "dalvik.system.VMRuntime");
            Method getRuntime = (Method) getDeclaredMethod.invoke(vmRuntime, "getRuntime", new Class[0]);
            Method setExemptions = (Method) getDeclaredMethod.invoke(
                    vmRuntime, "setHiddenApiExemptions", new Class[]{String[].class});

            Object runtime = getRuntime.invoke(null);
            // "L" exempts every class whose name starts with L, which is every
            // class. The cast to Object is what makes the array one argument
            // rather than a spread list.
            setExemptions.invoke(runtime, (Object) new String[]{"L"});
            Log.d("HiddenApi", "hidden api exemptions applied");
        } catch (Throwable t) {
            Log.w("HiddenApi", "hidden api exemptions unavailable: " + t.getClass().getSimpleName());
        }
    }
}
'''

CALL = """    override fun attachBaseContext(base: Context?) {
        try {
            // Before anything else. Android 9 refuses access to hidden platform
            // APIs, and this container reaches into framework internals by
            // design - the refusals surface as NoSuchFieldError from unrelated
            // code and make an install report success having written nothing.
            // This runs in every process the app starts, including :black where
            // the package manager lives, and it only works if it runs first.
            top.niunaijun.blackbox.utils.HiddenApi.free()

            super.attachBaseContext(base)"""

ANCHOR = """    override fun attachBaseContext(base: Context?) {
        try {
            super.attachBaseContext(base)"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    utils = list(root.rglob("top/niunaijun/blackbox/utils"))
    if not utils:
        raise SystemExit(f"  [FAIL] Bcore utils package not found under {root}")
    java_target = utils[0] / "HiddenApi.java"

    apps = list(root.rglob("com/onebitmonochrome/blacksbbox/app/App.kt"))
    if not apps:
        raise SystemExit(f"  [FAIL] App.kt not found under {root}")
    app = apps[0]
    print(f"  target: {java_target}")
    print(f"  target: {app}")

    changed = 0

    text = app.read_text(encoding="utf-8")
    if "HiddenApi.free()" in text:
        print("  [skip] App.kt already calls it")
    elif ANCHOR not in text:
        raise SystemExit(
            "  [FAIL] anchor not found in App.kt.\n"
            "         The base changed. Read the file before patching it.")
    else:
        app.write_text(text.replace(ANCHOR, CALL, 1), encoding="utf-8")
        print("  [ok  ] App.kt calls HiddenApi.free() first")
        changed += 1

    if java_target.exists() and "setHiddenApiExemptions" in java_target.read_text(encoding="utf-8"):
        print("  [skip] HiddenApi.java already present")
    else:
        java_target.parent.mkdir(parents=True, exist_ok=True)
        java_target.write_text(JAVA, encoding="utf-8")
        print("  [ok  ] HiddenApi.java written")
        changed += 1

    print(f"  {changed} change(s) applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())