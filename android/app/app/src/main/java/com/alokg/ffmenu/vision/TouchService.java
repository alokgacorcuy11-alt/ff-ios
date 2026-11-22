package com.alokg.ffmenu.vision;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.graphics.Path;
import android.view.accessibility.AccessibilityEvent;

/**
 * The only way an ordinary app can put a touch into another app.
 *
 * <p>It does one thing: dispatch a gesture the caller describes. It does not
 * read the screen - {@code canRetrieveWindowContent} is false in the config, so
 * the system never hands it a view tree, and there is nothing here that could
 * read a password field even if someone asked it to.
 *
 * <p>Off until the user turns it on in Settings, visible in that list by name,
 * and revoked by uninstalling the app. That is the entire contract, and it is
 * the same contract every auto-clicker on Play Store has.
 *
 * <p>Why it is needed at all: aiming means moving the crosshair, the crosshair
 * is a touch, and there is no second door.
 */
public final class TouchService extends AccessibilityService {

    private static volatile TouchService live;

    static TouchService live() {
        return live;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        live = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Nothing. This service is not a listener; it is a hand.
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public boolean onUnbind(Intent intent) {
        live = null;
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        live = null;
        super.onDestroy();
    }

    /**
     * @return false when the system refused the gesture - which happens while
     *     another gesture is still in flight, and is not an error worth logging.
     */
    boolean send(Path path, long startMs, long durationMs) {
        try {
            GestureDescription.StrokeDescription stroke =
                    new GestureDescription.StrokeDescription(path, startMs, durationMs);
            return dispatchGesture(
                    new GestureDescription.Builder().addStroke(stroke).build(), null, null);
        } catch (RuntimeException e) {
            return false;
        }
    }
}
