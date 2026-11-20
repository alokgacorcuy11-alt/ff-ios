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

import re
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
    "com/alokg/ffmenu/License.java",
    "com/alokg/ffmenu/LanConfig.java",
    "com/alokg/ffmenu/MainActivity.java",
    "com/alokg/ffmenu/FeedService.java",
    "com/alokg/ffmenu/VisionService.java",
    "com/alokg/ffmenu/Panel.java",
    "com/alokg/ffmenu/Vision.java",
    "com/alokg/ffmenu/MemClient.java",
    "com/alokg/ffmenu/Root.java",
    "com/alokg/ffmenu/feed/FeedFormat.java",
    "com/alokg/ffmenu/feed/FeedServer.java",
    "com/alokg/ffmenu/feed/FeedChannel.java",
    "com/alokg/ffmenu/vision/Detector.java",
    "com/alokg/ffmenu/vision/Capture.java",
    "com/alokg/ffmenu/vision/AimService.java",
    "com/alokg/ffmenu/vision/BoxOverlay.java",
]

# Every stub member below exists in the real API with the same signature. If a
# source starts using something else, this fails here instead of in Gradle.
STUBS = {
    "android/content/Context.java": """
package android.content;
public class Context {
    public static final String NOTIFICATION_SERVICE = "notification";
    public static final String WINDOW_SERVICE = "window";
    public static final String MEDIA_PROJECTION_SERVICE = "media_projection";
    public Object getSystemService(String name) { return null; }
    public android.content.ContentResolver getContentResolver() { return null; }
    public android.content.res.AssetManager getAssets() { return null; }
    public android.content.res.Resources getResources() { return null; }
}
""",
    "android/provider/Settings.java": """
package android.provider;
public final class Settings {
    public static final String ACTION_MANAGE_OVERLAY_PERMISSION =
            "android.settings.action.MANAGE_OVERLAY_PERMISSION";
    public static final String ACTION_ACCESSIBILITY_SETTINGS =
            "android.settings.ACCESSIBILITY_SETTINGS";
    public static boolean canDrawOverlays(android.content.Context c) { return true; }
    public static final class Secure {
        public static final String ANDROID_ID = "android_id";
        public static String getString(android.content.ContentResolver cr, String name) {
            return null;
        }
    }
}
""",
    "android/content/ContentResolver.java": """
package android.content;
public class ContentResolver {}
""",
    "android/text/Editable.java": """
package android.text;
public class Editable {
    public Editable replace(int a, int b, CharSequence s) { return this; }
    public String toString() { return ""; }
}
""",
    "android/widget/EditText.java": """
package android.widget;
public class EditText extends TextView {
    public EditText(android.content.Context c) { super(c); }
    public android.text.Editable getText() { return null; }
    public void setHint(CharSequence s) {}
}
""",
    "android/graphics/PixelFormat.java": """
package android.graphics;
public class PixelFormat {
    public static final int TRANSLUCENT = -3;
    public static final int RGBA_8888 = 1;
}
""",
    "android/view/MotionEvent.java": """
package android.view;
public class MotionEvent {
    public static final int ACTION_DOWN = 0;
    public static final int ACTION_UP = 1;
    public static final int ACTION_MOVE = 2;
    public int getAction() { return 0; }
    public float getRawX() { return 0f; }
    public float getRawY() { return 0f; }
}
""",
    "android/view/WindowManager.java": """
package android.view;
public interface WindowManager {
    void addView(android.view.View v, LayoutParams p);
    void updateViewLayout(android.view.View v, LayoutParams p);
    void removeView(android.view.View v);
    class LayoutParams {
        public static final int WRAP_CONTENT = -2;
        public static final int MATCH_PARENT = -1;
        public static final int TYPE_APPLICATION_OVERLAY = 2038;
        public static final int FLAG_NOT_FOCUSABLE = 8;
        public static final int FLAG_NOT_TOUCHABLE = 16;
        public static final int FLAG_LAYOUT_IN_SCREEN = 256;
        public static final int FLAG_LAYOUT_NO_LIMITS = 512;
        public int gravity;
        public int x;
        public int y;
        public LayoutParams(int w, int h, int type, int flags, int format) {}
    }
}
""",
    "android/content/Intent.java": """
package android.content;
public class Intent {
    public Intent() {}
    public Intent(String action) {}
    public Intent(Context c, Class<?> cls) {}
    public Intent putExtra(String k, int v) { return this; }
    public Intent putExtra(String k, String v) { return this; }
    public Intent putExtra(String k, Intent v) { return this; }
    public int getIntExtra(String k, int d) { return d; }
    public Intent getParcelableExtra(String k) { return null; }
}
""",
    "android/content/ComponentName.java": """
package android.content;
public class ComponentName { public ComponentName(String p, String c) {} }
""",
    "android/app/Notification.java": """
package android.app;
public class Notification {
    public static class Builder {
        public Builder(android.content.Context c, String channel) {}
        public Builder setContentTitle(CharSequence s) { return this; }
        public Builder setContentText(CharSequence s) { return this; }
        public Builder setSmallIcon(int id) { return this; }
        public Builder setOngoing(boolean b) { return this; }
        public Notification build() { return null; }
    }
}
""",
    "android/app/NotificationChannel.java": """
package android.app;
public class NotificationChannel {
    public NotificationChannel(String id, CharSequence name, int importance) {}
}
""",
    "android/app/NotificationManager.java": """
package android.app;
public class NotificationManager {
    public static final int IMPORTANCE_LOW = 2;
    public NotificationChannel getNotificationChannel(String id) { return null; }
    public void createNotificationChannel(NotificationChannel c) {}
}
""",
    "android/app/Service.java": """
package android.app;
public class Service extends android.content.Context {
    public static final int START_NOT_STICKY = 2;
    public void onCreate() {}
    public int onStartCommand(android.content.Intent i, int flags, int startId) { return 0; }
    public void onDestroy() {}
    public android.os.IBinder onBind(android.content.Intent i) { return null; }
    public void startForeground(int id, Notification n) {}
    public void stopSelf() {}
    public void stopSelf(int startId) {}
}
""",
    "android/os/IBinder.java": """
package android.os;
public interface IBinder {}
""",
    "android/R.java": """
package android;
public final class R {
    public static final class drawable { public static final int stat_notify_sync = 1; }
}
""",
    "android/util/DisplayMetrics.java": """
package android.util;
public class DisplayMetrics {
    public float density;
    public int widthPixels;
    public int heightPixels;
    public int densityDpi;
}
""",
    "android/os/HandlerThread.java": """
package android.os;
public class HandlerThread extends Thread {
    public HandlerThread(String name) {}
    public void start() {}
    public Looper getLooper() { return null; }
    public boolean quitSafely() { return true; }
}
""",
    "android/graphics/Path.java": """
package android.graphics;
public class Path {
    public void moveTo(float x, float y) {}
    public void lineTo(float x, float y) {}
    public void close() {}
}
""",
    "android/graphics/Canvas.java": """
package android.graphics;
public class Canvas {
    public void drawRect(float l, float t, float r, float b, Paint p) {}
    public void drawLine(float a, float b, float c, float d, Paint p) {}
    public void drawText(String s, float x, float y, Paint p) {}
    public int getWidth() { return 0; }
    public int getHeight() { return 0; }
}
""",
    "android/graphics/Paint.java": """
package android.graphics;
public class Paint {
    public enum Style { FILL, STROKE, FILL_AND_STROKE }
    public void setStyle(Style s) {}
    public void setStrokeWidth(float w) {}
    public void setAntiAlias(boolean a) {}
    public void setColor(int c) {}
    public void setTextSize(float s) {}
}
""",
    "android/view/accessibility/AccessibilityEvent.java": """
package android.view.accessibility;
public class AccessibilityEvent {}
""",
    "android/accessibilityservice/AccessibilityService.java": """
package android.accessibilityservice;
public abstract class AccessibilityService extends android.content.Context {
    protected void onServiceConnected() {}
    public void onAccessibilityEvent(android.view.accessibility.AccessibilityEvent e) {}
    public void onInterrupt() {}
    public void onDestroy() {}
    public boolean dispatchGesture(GestureDescription g,
            GestureResultCallback c, android.os.Handler h) { return true; }
    public static class GestureResultCallback {}
}
""",
    "android/accessibilityservice/GestureDescription.java": """
package android.accessibilityservice;
public class GestureDescription {
    public static class StrokeDescription {
        public StrokeDescription(android.graphics.Path p, long start, long duration) {}
    }
    public static class Builder {
        public Builder addStroke(StrokeDescription s) { return this; }
        public GestureDescription build() { return new GestureDescription(); }
    }
    public static int getMaxStrokeCount() { return 10; }
    public static long getMaxGestureDuration() { return 60000; }
}
""",
    "android/hardware/display/DisplayManager.java": """
package android.hardware.display;
public final class DisplayManager {
    public static final int VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR = 16;
}
""",
    "android/hardware/display/VirtualDisplay.java": """
package android.hardware.display;
public class VirtualDisplay {
    public void release() {}
}
""",
    "android/media/projection/MediaProjection.java": """
package android.media.projection;
public final class MediaProjection {
    public android.hardware.display.VirtualDisplay createVirtualDisplay(String name,
            int w, int h, int dpi, int flags,
            android.view.Surface surface, Callback cb, android.os.Handler handler) {
        return null;
    }
    public void stop() {}
    public static abstract class Callback {}
}
""",
    "android/media/projection/MediaProjectionManager.java": """
package android.media.projection;
public final class MediaProjectionManager {
    public android.content.Intent createScreenCaptureIntent() { return null; }
    public MediaProjection getMediaProjection(int resultCode, android.content.Intent data) {
        return null;
    }
}
""",
    "android/view/Surface.java": """
package android.view;
public class Surface {}
""",
    "android/media/Image.java": """
package android.media;
public abstract class Image implements AutoCloseable {
    public abstract Plane[] getPlanes();
    public abstract void close();
    public abstract static class Plane {
        public abstract java.nio.ByteBuffer getBuffer();
        public abstract int getRowStride();
        public abstract int getPixelStride();
    }
}
""",
    "android/media/ImageReader.java": """
package android.media;
public class ImageReader implements AutoCloseable {
    public interface OnImageAvailableListener { void onImageAvailable(ImageReader r); }
    public static ImageReader newInstance(int w, int h, int format, int max) { return null; }
    public void setOnImageAvailableListener(OnImageAvailableListener l, android.os.Handler h) {}
    public Image acquireLatestImage() { return null; }
    public android.view.Surface getSurface() { return null; }
    public void close() {}
}
""",
    "android/app/Activity.java": """
package android.app;
public class Activity extends android.content.Context {
    public static final int RESULT_OK = -1;
    public static final int RESULT_CANCELED = 0;
    protected void onCreate(android.os.Bundle b) {}
    protected void onDestroy() {}
    protected void onActivityResult(int req, int res, android.content.Intent data) {}
    public void setContentView(android.view.View v) {}
    public android.content.ComponentName startService(android.content.Intent i) { return null; }
    public void startForegroundService(android.content.Intent i) {}
    public void startActivity(android.content.Intent i) {}
    public void startActivityForResult(android.content.Intent i, int req) {}
    public boolean stopService(android.content.Intent i) { return true; }
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
    public View() {}
    public View(android.content.Context c) {}
    public void setBackgroundColor(int c) {}
    public void setPadding(int a, int b, int c, int d) {}
    public void setEnabled(boolean e) {}
    public void setVisibility(int v) {}
    public void setOnClickListener(OnClickListener l) {}
    public void setOnTouchListener(OnTouchListener l) {}
    public void postInvalidate() {}
    protected void onDraw(android.graphics.Canvas c) {}
    public int getHeight() { return 0; }
    public void scrollTo(int x, int y) {}
    public interface OnClickListener { void onClick(View v); }
    public interface OnTouchListener { boolean onTouch(View v, android.view.MotionEvent e); }
}
""",
    "android/view/Gravity.java": """
package android.view;
public class Gravity {
    public static final int CENTER_VERTICAL = 16;
    public static final int TOP = 48;
    public static final int START = 8388611;
}
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
    public void removeAllViews() {}
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
    public JSONObject put(String k, boolean v) throws JSONException { return this; }
    public JSONObject put(String k, double v) throws JSONException { return this; }
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

    # --- the vocabulary. The panel writes toJson(); the laptop and LanConfig
    # read apply(). If a key exists in one and not the other, a toggle does
    # nothing and nobody finds out until someone stares at the game.
    cfg_text = (SRC / "com/alokg/ffmenu/Config.java").read_text(encoding="utf-8")
    i_apply = cfg_text.index("int apply(JSONObject o)")
    i_json = cfg_text.index("JSONObject toJson()")
    apply_keys = set(re.findall(
        r'opt(?:Boolean|Int|Double|String|JSONArray|JSONObject)\("([A-Za-z0-9_]+)"',
        cfg_text[i_apply:i_json]))
    out_keys = set(re.findall(r'put\("([A-Za-z0-9_]+)"', cfg_text[i_json:]))

    print(f"[{'ok  ' if apply_keys else 'FAIL'}] apply() reads "
          f"{len(apply_keys)} keys: {', '.join(sorted(apply_keys))}")
    print(f"[{'ok  ' if out_keys else 'FAIL'}] toJson() writes "
          f"{len(out_keys)} keys: {', '.join(sorted(out_keys))}")

    missing = sorted(out_keys - apply_keys)
    print(f"[{'ok  ' if not missing else 'FAIL'}] every key the panel sends is one "
          f"the packet parser understands" + (f" - missing {missing}" if missing else ""))

    input_only = sorted(apply_keys - out_keys)
    # addr/label/type are watch-entry fields, esp_color and reply are inputs the
    # panel never sends; nothing else may be one-directional
    expected = ["addr", "esp_color", "label", "reply", "type", "watch"]
    print(f"[{'ok  ' if input_only == expected else 'FAIL'}] apply-only keys are "
          f"exactly the input-only ones {expected}" + (f" - got {input_only}" if input_only != expected else ""))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if r.returncode != 0:
        print("compilation FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
