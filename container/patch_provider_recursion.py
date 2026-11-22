#!/usr/bin/env python3
"""patch_provider_recursion.py - stop the provider hook from calling itself.

Measured on the OPPO A7, from a log the crash-visibility patch finally made
readable:

    E CrashMonitor: GUEST CRASH: java.lang.StackOverflowError: stack size 8MB
    E CrashMonitor:   at $Proxy61.resolveContentProvider(Unknown Source)
    E CrashMonitor:   at ApplicationPackageManager.resolveContentProviderAsUser(...)
    E CrashMonitor:   at ApplicationPackageManager.resolveContentProvider(...)
    E CrashMonitor:   at IPackageManagerProxy$ResolveContentProvider.hook(:677)
    E CrashMonitor:   at ClassInvocationStub.invoke(...)
    E CrashMonitor:   at java.lang.reflect.Proxy.invoke(...)
    E CrashMonitor:   at $Proxy61.resolveContentProvider(Unknown Source)
    ... repeated until the 8 MB stack is gone

The call graph is a perfect loop, and line 677 is where it closes:

    providerInfo = BlackBoxCore.getPackageManager().resolveContentProvider(...)

Inside the guest process, `BlackBoxCore.getPackageManager()` is the *hooked*
PackageManager. Calling resolveContentProvider on it re-enters the hook that is
running. Nothing decrements, and the guest dies of StackOverflowError a few
seconds after the game starts - which is also why the launch call never returns
and the caller's 25 s timeout fires.

The original call the hook is wrapping is `method.invoke(who, args)`. That is
what should be used, and it is what the other branch of this same if-statement
already returns - so the two branches converged anyway and the filter in between
was doing nothing except making the recursion look intentional.

The other callers of `BlackBoxCore.getPackageManager()` listed by a project-wide
search are all in the :black server process, where the PackageManager is not
hooked. This is the only one inside a hook.

  python3 container/patch_provider_recursion.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = """            ProviderInfo providerInfo = BlackBoxCore.getBPackageManager().resolveContentProvider(authority, flags, BActivityThread.getUserId());
            if (providerInfo == null) {
                providerInfo = BlackBoxCore.getPackageManager().resolveContentProvider(authority, flags);
                if (providerInfo != null && AppSystemEnv.isOpenPackage(providerInfo.packageName)) {
                    return providerInfo;
                }
                return method.invoke(who, args);
            }
            return providerInfo;"""

NEW = """            ProviderInfo providerInfo = BlackBoxCore.getBPackageManager().resolveContentProvider(authority, flags, BActivityThread.getUserId());
            if (providerInfo == null) {
                // The original call, not BlackBoxCore.getPackageManager().
                //
                // Inside the guest, getPackageManager() is the hooked one, so
                // asking it to resolve a provider re-enters this very hook. The
                // stack is a perfect loop - resolveContentProvider ->
                // resolveContentProviderAsUser -> this hook -> ClassInvocationStub
                // -> Proxy -> resolveContentProvider - and it ends in
                // StackOverflowError a few seconds after the game starts, which
                // is also why the launch call never returns.
                //
                // method.invoke(who, args) is the call this hook is wrapping.
                // Both branches of the old if returned the same thing anyway.
                return method.invoke(who, args);
            }
            return providerInfo;"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    hits = list(root.rglob("IPackageManagerProxy.java"))
    if not hits:
        raise SystemExit(f"  [FAIL] IPackageManagerProxy.java not found under {root}")
    target = hits[0]
    print(f"  target: {target}")

    text = target.read_text(encoding="utf-8")
    if "asking it to resolve a provider re-enters this very hook" in text:
        print("  [skip] already applied")
        return 0
    if OLD not in text:
        raise SystemExit(
            "  [FAIL] anchor not found in IPackageManagerProxy.java.\n"
            "         The base changed. Read the file before patching it.")
    target.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("  [ok  ] ResolveContentProvider no longer calls the hooked manager")
    print("  1 change applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())