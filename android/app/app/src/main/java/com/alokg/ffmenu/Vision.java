package com.alokg.ffmenu;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.util.DisplayMetrics;

import com.alokg.ffmenu.vision.AimService;
import com.alokg.ffmenu.vision.BoxOverlay;
import com.alokg.ffmenu.vision.Capture;
import com.alokg.ffmenu.vision.Detector;

import java.util.List;

/**
 * Screen vision: the only configuration that needs no root, no second APK and
 * no code inside the game.
 *
 * <p>It cannot read what is not drawn. There are no names, no distances, no
 * bones and no health - those live in the game's memory, and reading them from
 * another app requires the same uid or root. What it can do is find the
 * nameplate the game already drew and put a box on it, and lean the crosshair
 * toward it. That is the entire honest feature set of this path.
 *
 * <p>The one thing it buys in return is that the game never sees it. Nothing is
 * loaded into the process, nothing is written to its directories, no packet is
 * altered. The tells are the system's own: a screen-capture session and an
 * accessibility service, both visible in Settings and both revoked by
 * uninstalling this app.
 */
final class Vision {

    private static Vision live;

    private final Config cfg;
    private final BoxOverlay overlay;
    private final Detector.Params params = new Detector.Params();
    private Capture capture;
    private MediaProjection projection;

    private int screenW, screenH;
    private long lastAimAt;
    private volatile boolean assist = true;
    private volatile boolean boxes = true;

    private Vision(Context ctx, Config cfg) {
        this.cfg = cfg;
        DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        this.screenW = dm.widthPixels;
        this.screenH = dm.heightPixels;
        this.overlay = new BoxOverlay(ctx, screenW, screenH);
    }

    static boolean running() {
        return live != null;
    }

    static String status() {
        Vision v = live;
        if (v == null) {
            return "vision: off";
        }
        return "vision: " + (v.capture == null ? "starting" : "on")
                + (AimService.ready() ? " + touch" : " (no touch service)");
    }

    /** Called with the MediaProjection grant - from the Activity or the service. */
    static String start(Context ctx, Config cfg, int resultCode, Intent data) {
        stop();
        if (resultCode != Activity.RESULT_OK || data == null) {
            return "vision: capture permission denied";
        }
        Vision v = new Vision(ctx, cfg);
        MediaProjectionManager mpm = (MediaProjectionManager)
                ctx.getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        if (mpm == null) {
            return "vision: no projection service";
        }
        v.projection = mpm.getMediaProjection(resultCode, data);
        if (v.projection == null) {
            return "vision: projection refused";
        }
        v.overlay.attach();
        v.capture = new Capture(ctx, v.projection, v.screenW, v.screenH,
                ctx.getResources().getDisplayMetrics().densityDpi, v::onFrame);
        live = v;
        return status();
    }

    static void stop() {
        Vision v = live;
        if (v == null) {
            return;
        }
        live = null;
        if (v.capture != null) {
            v.capture.stop();
            v.capture = null;
        }
        if (v.projection != null) {
            try {
                v.projection.stop();
            } catch (RuntimeException ignored) {
            }
            v.projection = null;
        }
        v.overlay.detach();
    }

    static void setBoxes(boolean on) {
        Vision v = live;
        if (v != null) {
            v.boxes = on;
            if (!on) {
                v.overlay.clear();
            }
        }
    }

    static void setAssist(boolean on) {
        Vision v = live;
        if (v != null) {
            v.assist = on;
        }
    }

    static Detector.Params params() {
        Vision v = live;
        return v == null ? null : v.params;
    }

    /** Called from the UI thread, once per captured frame. */
    private void onFrame(int[] argb, int w, int h) {
        if (live != this || !License.unlocked()) {
            return;
        }
        params.bandTop = 0;
        params.bandBottom = 1000;

        List<Detector.Blob> blobs = Detector.detect(argb, w, h, params);

        if (boxes) {
            overlay.update(blobs);
        } else {
            overlay.clear();
        }

        if (assist && cfg.aimEnabled && !blobs.isEmpty()) {
            steer(blobs.get(0), w, h);
        }
    }

    /**
     * Leans the crosshair toward the target instead of snapping to it.
     *
     * <p>One gesture in flight at a time: dispatchGesture cancels the previous
     * one, so firing another before this expires moves the aim nowhere. The
     * gap between gestures is what sets the effective speed, and the step
     * size is what keeps the movement continuous rather than a jump.
     */
    private void steer(Detector.Blob target, int w, int h) {
        if (!AimService.ready()) {
            return;
        }
        long now = System.currentTimeMillis();
        int smooth = Math.max(1, cfg.aimSmooth);
        long gap = Math.max(24, smooth * 12L);
        if (now - lastAimAt < gap) {
            return;
        }

        int cx = w / 2;
        int cy = h / 2;
        int dx = target.cx() - cx;
        int dy = target.cy() - cy;
        double dist = Math.hypot(dx, dy);

        int fovPx = (int) (Math.min(w, h) * (cfg.aimFov / 720.0));
        if (dist > fovPx || dist < 3) {
            return;
        }

        double step = Math.max(2.0, dist / smooth);
        int nx = cx + (int) Math.round(dx / dist * step);
        int ny = cy + (int) Math.round(dy / dist * step);

        if (AimService.swipe(cx, cy, nx, ny, (int) Math.min(120, 20 + dist))) {
            lastAimAt = now;
        }
    }
}