#!/usr/bin/env python3
"""patch_provider_calling_pkg.py - send the container's package, not the guest's.

From the A7, after the recursion fix let the activity actually start:

    RuntimeException: Unable to start activity
      ComponentInfo{com.dts.freefireth/com.dts.freefireth.FFMainActivity}
    Caused by: UndeclaredThrowableException
      at $Proxy105.call
      at Settings$NameValueCache.getStringForUser
      at Settings$Global.getInt
      at com.android.internal.policy.PhoneWindow.<init>
      at android.app.Activity.attach
    Caused by: InvocationTargetException
      at SystemProviderStub.invoke(SystemProviderStub.java:74)
    Caused by: java.lang.SecurityException:
      Package com.dts.freefireth does not belong to 10148

10148 is the container's uid. Creating a window reads a global setting, that read
goes through the container's content-provider proxy, and the system refuses it:
the guest announced itself as com.dts.freefireth while calling from a uid that
belongs to com.onebitmonochrome.blacksbbox.

The proxy already knows how to fix that - but only for Android 12 and later.
`SystemProviderStub.invoke` looks for an AttributionSource argument and repairs
it, and AttributionSource was introduced in Android 12. On Android 8.1 there is
no such argument, the loop finds nothing, and line 74 forwards the call
untouched. That is why this fails on the A7 and not on the emulator, which runs
Android 9 through the same code path but with different argument shapes.

Before Android 12 the calling package travels as a plain String in the first
argument. The container's own package does belong to the container's uid, so
that is what is sent instead.

The rewrite is deliberately narrow: a String in the first position that is
exactly the guest's package name. That can only be the calling-package
parameter - settings keys, provider methods and the extras bundle are never the
guest's own package name - so a provider whose arguments have nothing to do with
this is left alone.

A line is logged either way, so the next run says what the first argument
actually was rather than leaving it to be assumed.

  python3 container/patch_provider_calling_pkg.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = """            }
            return method.invoke(mBase, args);
        }
"""

NEW = """            }
            // Before Android 12 the calling package travels as a plain String
            // in the first argument, and nothing above rewrote it - the
            // AttributionSource repair only applies where that class exists.
            // So the guest's own package went to the system verbatim and the
            // system refused it:
            //
            //   SecurityException: Package com.dts.freefireth does not belong
            //   to 10148
            //
            // 10148 is the container's uid; the guest's package has never
            // belonged to it, and the container's own package does. Narrow on
            // purpose: only a String in the first position that is exactly the
            // guest's package name, which can only be the calling package.
            android.util.Log.d("SystemProviderStub", "call: first arg = "
                    + (args != null && args.length > 0
                            ? String.valueOf(args[0]) + " [" + args[0].getClass().getSimpleName() + "]"
                            : "none")
                    + ", appPkg = " + mAppPkg);
            // The first argument of IContentProvider.call is the calling
            // package, and the log from the A7 settled what the two values are:
            //
            //   call: first arg = com.dts.freefireth [String],
            //         appPkg = com.onebitmonochrome.blacksbbox
            //
            // mAppPkg here is the host package, not the guest's, so comparing
            // the argument against it never matched and the guest's package went
            // to the system unchanged. Every call is made by this process, from
            // this uid, so the name sent must be the one that belongs to this
            // uid - the host package. That is what is set, unconditionally,
            // because that is what this argument means in a call.
            if (args != null && args.length > 0 && args[0] instanceof String
                    && !BlackBoxCore.getHostPkg().equals(args[0])) {
                args[0] = BlackBoxCore.getHostPkg();
            }
            return method.invoke(mBase, args);
        }
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = list(root.rglob("SystemProviderStub.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] SystemProviderStub.java not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    text = target.read_text(encoding="utf-8")
    if "!BlackBoxCore.getHostPkg().equals(args[0])" in text:
        print("  [skip] already applied")
        return 0

    # The anchor must be the call branch, not the generic one at the end.
    idx = text.find('if ("call".equals(methodName))')
    if idx < 0:
        raise SystemExit("  [FAIL] the call branch was not found - the base changed.")
    tail = text[idx:]
    pos = tail.find(OLD)
    if pos < 0:
        raise SystemExit(
            "  [FAIL] anchor not found inside the call branch.\n"
            "         The base changed. Read the file before patching it.")
    tail = tail[:pos] + NEW + tail[pos + len(OLD):]
    text = text[:idx] + tail
    target.write_text(text, encoding="utf-8")
    print("  [ok  ] the calling package is rewritten, and logged")
    print("  1 change applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())