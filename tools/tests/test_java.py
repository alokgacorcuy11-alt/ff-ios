#!/usr/bin/env python3
"""test_java.py - compile the app's Java against API stubs, no Android SDK.

There is no SDK on the machine this was written on, so a typo in a method name
would only surface in CI, in a Gradle log, minutes later. This compiles the
same four sources with javac against a minimal set of stubs that declare only
the members the code actually uses. It does not prove the app runs; it proves
every symbol resolves and every type matches - which is exactly the class of
error that Gradle reports worst.

  python tools/tests/test_java.py

Skipped with a clear message when javac is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = ROOT / "android" / "app" / "app" / "src" / "main" / "java"

SOURCES = [
    "com/alokg/ffmenu/Config.java",
    "com/alokg/ffmenu/LanConfig.java",
    "com/alokg/ffmenu/MainActivity.java",
    "com/alokg/ffmenu/MemClient.java",
    "com/alokg/ffmenu/Root.java",
]

# Every stub member below exists in the real API with the same signature. If a
# source starts using something else, this fails here instead of in Gradle.
STUBS = {
    "android/content/Context.java": """
package android.content;
public class Context {}
""",
    "android/app/Activity.java": """
package android.app;
public class Activity extends android.content.Context {
    protected void onCreate(android.os.Bundle b) {}
    protected void onDestroy() {}
    public void setContentView(android.view.View v) {}
    public android.content.pm.ApplicationInfo getApplicationInfo() { return null; }
    public java.io.File getFilesDir() { return null; }
    public android.content.res.AssetManager getAssets() { return null; }
    public android.content.res.Resources getResources() { return null; }
}
""",
    "android/content/pm/ApplicationInfo.java": """
package android.content.pm;
public class ApplicationInfo { public int uid; }
""",
    "android/content/res/AssetManager.java": """
package android.content.res;
public class AssetManager {
    public java.io.InputStream open(String name) throws java.io.IOException { return null; }
}
""",
    "android/content/res/Resources.java": """
package android.content.res;
public class Resources {
    public android.util.DisplayMetrics getDisplayMetrics() { return null; }
}
""",
    "android/util/DisplayMetrics.java": """
package android.util;
public class DisplayMetrics { public float density; }
""",
    "android/os/Bundle.java": """
package android.os;
public class Bundle {}
""",
    "android/os/Looper.java": """
package android.os;
public class Looper { public static Looper getMainLooper() { return null; } }
""",
    "android/os/Handler.java": """
package android.os;
public class Handler {
    public Handler(Looper l) {}
    public boolean post(Runnable r) { return true; }
}
""",
    "android/view/View.java": """
package android.view;
public class View {
    public void setBackgroundColor(int c) {}
    public void setPadding(int a, int b, int c, int d) {}
    public void setEnabled(boolean e) {}
    public void setOnClickListener(OnClickListener l) {}
    public int getHeight() { return 0; }
    public void scrollTo(int x, int y) {}
    public interface OnClickListener { void onClick(View v); }
}
""",
    "android/view/Gravity.java": """
package android.view;
public class Gravity { public static final int CENTER_VERTICAL = 16; }
""",
    "android/text/Layout.java": """
package android.text;
public class Layout { public int getLineTop(int line) { return 0; } }
""",
    "android/text/method/MovementMethod.java": """
package android.text.method;
public interface MovementMethod {}
""",
    "android/text/method/ScrollingMovementMethod.java": """
package android.text.method;
public class ScrollingMovementMethod implements MovementMethod {}
""",
    "android/graphics/Color.java": """
package android.graphics;
public class Color { public static int parseColor(String s) { return 0; } }
""",
    "android/graphics/Typeface.java": """
package android.graphics;
public class Typeface {
    public static final Typeface MONOSPACE = null;
    public static final int BOLD = 1;
}
""",
    "android/widget/TextView.java": """
package android.widget;
public class TextView extends android.view.View {
    public TextView(android.content.Context c) {}
    public void setText(CharSequence s) {}
    public void setTextColor(int c) {}
    public void setTypeface(android.graphics.Typeface t) {}
    public void setTypeface(android.graphics.Typeface t, int style) {}
    public void setTextSize(float s) {}
    public void setMovementMethod(android.text.method.MovementMethod m) {}
    public void setTextIsSelectable(boolean b) {}
    public android.text.Layout getLayout() { return null; }
    public int getLineCount() { return 0; }
}
""",
    "android/widget/Button.java": """
package android.widget;
public class Button extends TextView {
    public Button(android.content.Context c) { super(c); }
}
""",
    "android/widget/LinearLayout.java": """
package android.widget;
public class LinearLayout extends android.view.View {
    public static final int HORIZONTAL = 0;
    public static final int VERTICAL = 1;
    public LinearLayout(android.content.Context c) {}
    public void setOrientation(int o) {}
    public void addView(android.view.View v) {}
    public void addView(android.view.View v, LayoutParams p) {}
    public void setGravity(int g) {}
    public static class LayoutParams {
        public static final int MATCH_PARENT = -1;
        public static final int WRAP_CONTENT = -2;
        public int topMargin;
        public LayoutParams(int w, int h) {}
        public LayoutParams(int w, int h, float weight) {}
    }
}
""",
    "android/net/LocalSocket.java": """
package android.net;
public class LocalSocket implements java.io.Closeable {
    public void connect(LocalSocketAddress a) throws java.io.IOException {}
    public java.io.InputStream getInputStream() throws java.io.IOException { return null; }
    public java.io.OutputStream getOutputStream() throws java.io.IOException { return null; }
    public void close() throws java.io.IOException {}
}
""",
    "android/net/LocalSocketAddress.java": """
package android.net;
public class LocalSocketAddress {
    public enum Namespace { ABSTRACT, FILESYSTEM, RESERVED }
    public LocalSocketAddress(String name, Namespace ns) {}
}
""",
    "org/json/JSONException.java": """
package org.json;
public class JSONException extends Exception {
    public JSONException(String m) { super(m); }
}
""",
    "org/json/JSONObject.java": """
package org.json;
public class JSONObject {
    public JSONObject() {}
    public JSONObject(String s) throws JSONException {}
    public boolean optBoolean(String k, boolean d) { return d; }
    public int optInt(String k, int d) { return d; }
    public double optDouble(String k, double d) { return d; }
    public String optString(String k, String d) { return d; }
    public String optString(String k) { return ""; }
    public JSONArray optJSONArray(String k) { return null; }
    public JSONObject optJSONObject(String k) { return null; }
    public JSONObject getJSONObject(String k) throws JSONException { return null; }
    public boolean has(String k) { return false; }
    public JSONObject put(String k, Object v) throws JSONException { return this; }
    public JSONObject put(String k, int v) throws JSONException { return this; }
    public JSONObject put(String k, long v) throws JSONException { return this; }
    public String toString() { return ""; }
}
""",
    "org/json/JSONArray.java": """
package org.json;
public class JSONArray {
    public int length() { return 0; }
    public JSONObject optJSONObject(int i) { return null; }
    public JSONArray put(Object v) { return this; }
}
""",
}


def main() -> int:
    print("=" * 66)
    print(" app java (javac + API stubs)")
    print("=" * 66)

    javac = shutil.which("javac")
    if not javac:
        print("[skip] no javac on PATH")
        print("\nALL PASS (nothing to run here)")
        return 0

    missing = [s for s in SOURCES if not (SRC / s).is_file()]
    if missing:
        print(f"[FAIL] missing sources: {missing}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="java-stub-"))
    stubs = tmp / "stubs"
    out = tmp / "classes"
    out.mkdir()

    for rel, text in STUBS.items():
        p = stubs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.strip() + "\n", encoding="utf-8")

    files = [str(stubs / rel) for rel in STUBS] + [str(SRC / s) for s in SOURCES]
    r = subprocess.run(
        [javac, "-encoding", "UTF-8", "--release", "17", "-nowarn",
         "-d", str(out)] + files,
        capture_output=True, text=True)

    errors = [ln for ln in (r.stderr or "").splitlines() if ": error:" in ln]
    for ln in errors[:20]:
        print(f"[FAIL] {ln}")
    print(f"[{'ok  ' if r.returncode == 0 else 'FAIL'}] "
          f"{len(SOURCES)} sources compile against the stubs")
    if r.returncode == 0:
        classes = list(out.rglob("*.class"))
        print(f"[{'ok  ' if len(classes) >= len(SOURCES) else 'FAIL'}] "
              f"{len(classes)} class files produced")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if r.returncode != 0:
        print("compilation FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
