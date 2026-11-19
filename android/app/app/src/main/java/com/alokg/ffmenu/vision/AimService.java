package com.alokg.ffmenu.vision;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.view.accessibility.AccessibilityEvent;

/**
 * Moves the crosshair.
 *
 * <p>An accessibility service is the only way a normal app can put a touch into
 * another app. It has to be switched on by hand in Settings once - that is the
 * user granting input control, the same grant a screen reader gets - and it
 * needs no root, no shell and no second APK.
 *
 * <p>The gesture is a real swipe along a path, not a teleport: the game sees a
 * finger moving. Duration is what makes it look human, which is why the aim
 * assist scales duration with distance instead of moving instantly.
 */
public final class AimService extends AccessibilityService {

    private static volatile AimService live;

    public static boolean ready() {
        return live != null;
    }

    /** True if the gesture was accepted. */
    public static boolean swipe(int x1, int y1, int x2, int y2, int durationMs) {
        AimService s = live;
        if (s == null) {
            return false;
        }
        int ms = Math.max(8, Math.min(400, durationMs));
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, ms);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(stroke)
                .build();
        try {
            return s.dispatchGesture(gesture, null, null);
        } catch (RuntimeException e) {
            return false;
        }
    }

    /** A press and release, for a tap on the fire button. */
    public static boolean tap(int x, int y, int durationMs) {
        return swipe(x, y, x, y, durationMs);
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        live = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // nothing is read from the tree: a Unity surface exposes no nodes, and
        // reading them would be a second, pointless permission surface
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public void onDestroy() {
        live = null;
        super.onDestroy();
    }
}