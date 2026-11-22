#!/usr/bin/env python3
"""patch_launcher.py - make the container's loading dialog survivable.

The problem this fixes, as observed on the OPPO A7:

    tapping Free Fire          -> showLoading() puts up a CatLoadingView
                                  dialog with setClickCancelAble(false) and a
                                  key listener that swallows BACK
    BlackBoxCore.launchApk()   -> called on an IO thread, inside a try/catch
                                  that catches Exception - so a hang inside the
                                  binder call to the :black process blocks that
                                  thread forever and posts nothing
    hideLoading()              -> only runs in the launchLiveData observer, which
                                  needs launchApk to return
    the result                 -> a black screen with no guest process ever
                                  created, no error, and no way out but the
                                  Home button

So the dialog is not the bug. It is the thing that makes the bug unreadable: it
hides the container's own UI and refuses to go away, and the only diagnostic
information - whether launchApk returned, and what it returned - never reaches
the screen.

Two changes:

  1. the dialog can be dismissed by tapping or by BACK, so there is always a way
     back to the app list;
  2. twenty-five seconds after a launch is requested, if the launch data has not
     been set, the dialog is dismissed and a toast names the package. That turns
     "nothing happens" into "this call did not answer", which is the difference
     between a dead end and a finding.

Neither change touches how the guest is started. If the guest can start, it
starts; this only stops the container from hiding the answer.

  python3 container/patch_launcher.py <base-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

DONE = 0


def find(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if not hits:
        raise SystemExit(f"  [FAIL] could not find {name} under {root}")
    return hits[0]


def patch(path: Path, old: str, new: str, label: str) -> None:
    global DONE
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"  [skip] {label} (already applied)")
        return
    if old not in text:
        raise SystemExit(
            f"  [FAIL] {label}: anchor not found in {path.name}.\n"
            f"         The base changed. Re-read the file before patching it -\n"
            f"         a patch that silently does nothing is worse than none.")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  [ok  ] {label}")
    DONE += 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])

    # ---- 1. the loading dialog must always have an exit -------------------
    loading = find(root, "LoadingActivity.kt")
    print(f"  target: {loading}")
    patch(
        loading,
        """            loadingView.setClickCancelAble(false)
            loadingView.dialog?.setOnKeyListener { _, keyCode, _ ->
                if (keyCode == KeyEvent.KEYCODE_BACK || keyCode == KeyEvent.KEYCODE_ESCAPE) {
                    return@setOnKeyListener true
                }
                false
            }""",
        """            // Made cancellable on purpose. A dialog that is only dismissed
            // when the launch call returns is a trap when that call does not
            // return: the user is left on a black screen whose only exit is the
            // Home button, and nothing on screen says why.
            loadingView.setClickCancelAble(true)""",
        "loading dialog is cancellable",
    )

    # ---- 2. a launch that never answers must say so ----------------------
    fragment = find(root, "AppsFragment.kt")
    print(f"  target: {fragment}")
    patch(
        fragment,
        """                    viewModel.launchApk(data.packageName, userID)
                } catch (e: Exception) {
                    Log.e(TAG, "Error launching app: ${e.message}")""",
        """                    viewModel.launchApk(data.packageName, userID)
                    // When the container's launch call never returns - which is
                    // what a guest that never finishes forking looks like from
                    // here - nothing ever posts to launchLiveData, so the
                    // observer never runs and the loading dialog stays up
                    // forever. This gives the screen back and names the package
                    // that did not answer.
                    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                        try {
                            if (viewModel.launchLiveData.value == null) {
                                hideLoading()
                                android.widget.Toast.makeText(
                                    requireContext(),
                                    "launch tidak menjawab: " + data.packageName,
                                    android.widget.Toast.LENGTH_LONG
                                ).show()
                            }
                        } catch (_: Exception) {
                        }
                    }, 25000L)
                } catch (e: Exception) {
                    Log.e(TAG, "Error launching app: ${e.message}")""",
        "a launch that never returns now says so after 25s",
    )

    # ---- 3. say whether the launch call answered at all ------------------
    # The log from the phone showed no launch attempt whatsoever, which is
    # consistent with both "it returned false" and "it never returned". Those
    # need different fixes, so the call now announces itself on both sides.
    repo = find(root, "AppsRepository.kt")
    print(f"  target: {repo}")
    patch(
        repo,
        """    fun launchApk(packageName: String, userId: Int, launchLiveData: MutableLiveData<Boolean>) {
        try {
            val result = BlackBoxCore.get().launchApk(packageName, userId)
            launchLiveData.postValue(result)
        } catch (e: Exception) {
            Log.e(TAG, "Error launching APK: ${e.message}")
            launchLiveData.postValue(false)
        }
    }""",
        """    fun launchApk(packageName: String, userId: Int, launchLiveData: MutableLiveData<Boolean>) {
        try {
            // Both sides are logged, and that is the point: a line before the
            // call and a line after it. "returned false" and "never returned"
            // look identical in a log that only records the result, and they
            // need different fixes.
            Log.d(TAG, "launchApk: begin pkg=$packageName user=$userId")
            val result = BlackBoxCore.get().launchApk(packageName, userId)
            Log.d(TAG, "launchApk: returned $result pkg=$packageName user=$userId")
            launchLiveData.postValue(result)
        } catch (e: Exception) {
            Log.e(TAG, "launchApk: threw ${e.javaClass.simpleName}: ${e.message} pkg=$packageName")
            launchLiveData.postValue(false)
        }
    }""",
        "the launch call announces its return",
    )

    print(f"  {DONE} change(s) applied")
    return 0 if DONE >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())