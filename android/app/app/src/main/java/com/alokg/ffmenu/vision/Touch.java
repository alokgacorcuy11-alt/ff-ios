package com.alokg.ffmenu.vision;

/**
 * The touch half of the vision path - absent from this build on purpose.
 *
 * <p>Moving the crosshair means putting a synthetic swipe into another app, and
 * on Android the only door to that is an accessibility service. It works, but
 * declaring one is the strongest "this is malware" signal an installer sees,
 * and it is exactly what made Play Protect refuse this APK outright. So the
 * boxes ship and the touch does not: capture, detector and overlay are each
 * useful without it.
 *
 * <p>What would implement this: a class extending AccessibilityService, a
 * BIND_ACCESSIBILITY_SERVICE declaration in the manifest, and a README that
 * says out loud it costs an install warning. The shape is kept here so that
 * swap is one file rather than a redesign.
 */
public final class Touch {

    private Touch() {
    }

    /** False: there is no touch service in this build, and callers say so. */
    public static boolean ready() {
        return false;
    }

    /**
     * @return false, always. The caller treats that as "no aim assist", which is
     *     a state the panel reports, not an error it raises.
     */
    public static boolean swipe(int fromX, int fromY, int toX, int toY, int ms) {
        return false;
    }
}
