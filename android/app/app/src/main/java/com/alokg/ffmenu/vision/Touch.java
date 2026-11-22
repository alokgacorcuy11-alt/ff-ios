package com.alokg.ffmenu.vision;

import android.graphics.Path;

/**
 * Synthetic touch: a swipe to lean the crosshair, a tap to fire.
 *
 * <p>Both go through {@link TouchService}, which the user enables once. When it
 * is not enabled every call returns false and the caller carries on without
 * aim - the boxes do not depend on this and must not stop working because a
 * permission was not given.
 *
 * <p>The service reference is checked on every call rather than cached at
 * startup: the system can unbind the service at any moment (another
 * accessibility service takes over, the user toggles it off), and a cached
 * reference would then dispatch into a dead object.
 */
public final class Touch {

    /** Shortest gesture Android accepts as a move rather than a tap. */
    private static final int MIN_MS = 16;

    private Touch() {
    }

    /** True when the user has enabled the service and the system has bound it. */
    public static boolean ready() {
        return TouchService.live() != null;
    }

    /**
     * Drags from one point to another.
     *
     * @param ms how long the gesture takes. Short values move the camera a lot
     *     per frame and look like a snap; long values are smooth and slow.
     * @return false when there is no service, or the system dropped the gesture.
     */
    public static boolean swipe(int fromX, int fromY, int toX, int toY, int ms) {
        TouchService s = TouchService.live();
        if (s == null) {
            return false;
        }
        Path p = new Path();
        p.moveTo(fromX, fromY);
        p.lineTo(toX, toY);
        return s.send(p, 0, Math.max(MIN_MS, ms));
    }

    /**
     * A tap at a point - the trigger for auto-fire.
     *
     * <p>A path of two points a pixel apart, not {@code Path.moveTo} alone:
     * a zero-length stroke is a tap on some ROMs and a no-op on others, and the
     * one-pixel line is a tap everywhere.
     */
    public static boolean tap(int x, int y, int ms) {
        TouchService s = TouchService.live();
        if (s == null) {
            return false;
        }
        Path p = new Path();
        p.moveTo(x, y);
        p.lineTo(x + 1, y);
        return s.send(p, 0, Math.max(MIN_MS, ms));
    }
}
