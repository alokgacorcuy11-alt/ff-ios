package com.alokg.ffmenu;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;

import com.alokg.ffmenu.device.DeviceProfile;
import com.alokg.ffmenu.device.Tier;
import com.alokg.ffmenu.vision.BoxOverlay;
import com.alokg.ffmenu.vision.Capture;
import com.alokg.ffmenu.vision.Detector;
import com.alokg.ffmenu.vision.Touch;

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
 *
 * <p>Everything about size and rate comes from {@link Tier}, which measured the
 * phone. This class has no opinion about how fast it should run; a phone that
 * cannot keep up gets a smaller capture, not a stutter.
 */
final class Vision {

    private static Vision live;

    private final Config cfg;
    private final Tier tier;
    private final int densityDpi;

    private BoxOverlay overlay;
    private Capture capture;
    private MediaProjection projection;

    /** Display size in screen pixels, current orientation. */
    private int screenW, screenH;
    /** Capture size in pixels, which is what the detector sees. */
    private int capW, capH;

    private final Detector.Params params = new Detector.Params();
    private long lastAimAt;
    private volatile boolean assist = false;
    private volatile boolean boxes = true;

    private Vision(Context ctx, Config cfg, Tier tier, MediaProjection projection) {
        this.cfg = cfg;
        this.tier = tier;
        this.projection = projection;
        this.densityDpi = ctx.getResources().getDisplayMetrics().densityDpi;

        int[] screen = DeviceProfile.screenSize(ctx);
        screenW = screen[0];
        screenH = screen[1];

        overlay = new BoxOverlay(ctx, screenW, screenH);
        overlay.attach();

        params.stride = tier.stride;
        startCapture(ctx);
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
                + " - " + v.tier.describe(v.screenW, v.screenH)
                + (Touch.ready() ? " + sentuh" : " (sentuh belum aktif)");
    }

    /** Called with the MediaProjection grant - from the Activity or the service. */
    static String start(Context ctx, Config cfg, int resultCode, Intent data) {
        stop();
        if (resultCode != Activity.RESULT_OK || data == null) {
            return "vision: izin rekam layar ditolak";
        }
        MediaProjectionManager mpm = (MediaProjectionManager)
                ctx.getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        if (mpm == null) {
            return "vision: layanan rekam layar tidak ada di HP ini";
        }
        MediaProjection projection = mpm.getMediaProjection(resultCode, data);
        if (projection == null) {
            return "vision: projection refused";
        }

        Tier tier = DeviceProfile.read(ctx).tier;
        if (tier.level == Tier.UNSUPPORTED) {
            try {
                projection.stop();
            } catch (RuntimeException ignored) {
            }
            return "vision: " + (tier.reasons.isEmpty()
                    ? "HP ini tidak mendukung rekam layar" : tier.reasons.get(0));
        }

        live = new Vision(ctx, cfg, tier, projection);
        return status();
    }

    static void stop() {
        Vision v = live;
        if (v == null) {
            return;
        }
        live = null;
        v.release();
    }

    /**
     * Re-creates the capture at the display's current size.
     *
     * <p>The app is portrait and the game is landscape. Without this the first
     * frame after the game starts is the right pixels in the wrong orientation,
     * every box lands somewhere wrong, and it looks like the detector broke.
     * The projection survives - only the virtual display is rebuilt.
     */
    static String resize(Context ctx) {
        Vision v = live;
        if (v == null) {
            return "";
        }
        int[] screen = DeviceProfile.screenSize(ctx);
        if (screen[0] == v.screenW && screen[1] == v.screenH) {
            return "";
        }
        if (v.capture != null) {
            v.capture.stop();
            v.capture = null;
        }
        v.screenW = screen[0];
        v.screenH = screen[1];
        v.overlay.resize(v.screenW, v.screenH);
        v.startCapture(ctx);
        return v.tier.describe(v.screenW, v.screenH);
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

    // ---- internals ---------------------------------------------------------

    private void startCapture(Context ctx) {
        int[] cap = tier.captureSize(screenW, screenH);
        capW = cap[0];
        capH = cap[1];
        // The size limits are lengths, so they follow the capture down. Without
        // this a half-resolution profile searches for nameplates twice as wide
        // as anything it will ever see.
        params.scaleFrom(capW);
        capture = new Capture(ctx, projection, capW, capH, densityDpi, tier.fps, this::onFrame);
    }

    private void release() {
        if (capture != null) {
            capture.stop();
            capture = null;
        }
        if (projection != null) {
            try {
                projection.stop();
            } catch (RuntimeException ignored) {
            }
            projection = null;
        }
        if (overlay != null) {
            overlay.detach();
            overlay = null;
        }
    }

    /** Called from the capture thread, once per frame the profile allows. */
    private void onFrame(int[] argb, int w, int h) {
        if (live != this || !License.unlocked()) {
            return;
        }
        params.bandTop = 0;
        params.bandBottom = 1000;

        List<Detector.Blob> blobs = Detector.detect(argb, w, h, params);

        // Capture pixels to screen pixels: one multiply, and every consumer
        // below works in the coordinates the user actually sees.
        float fx = w <= 0 ? 1f : screenW / (float) w;
        float fy = h <= 0 ? 1f : screenH / (float) h;

        if (boxes) {
            int[] rects = new int[blobs.size() * 4];
            int i = 0;
            for (Detector.Blob b : blobs) {
                rects[i++] = Math.round(b.x * fx);
                rects[i++] = Math.round(b.y * fy);
                rects[i++] = Math.round((b.x + b.w) * fx);
                rects[i++] = Math.round((b.y + b.h) * fy);
            }
            overlay.updateRects(rects);
        } else {
            overlay.clear();
        }

        if (assist && cfg.aimEnabled && !blobs.isEmpty()) {
            steer(blobs.get(0), fx, fy);
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
    private void steer(Detector.Blob target, float fx, float fy) {
        if (!Touch.ready()) {
            return;
        }
        long now = System.currentTimeMillis();
        int smooth = Math.max(1, cfg.aimSmooth);
        long gap = Math.max(24, smooth * 12L);
        if (now - lastAimAt < gap) {
            return;
        }

        int cx = screenW / 2;
        int cy = screenH / 2;
        int tx = Math.round((target.x + target.w / 2f) * fx);
        int ty = Math.round((target.y + target.h / 2f) * fy);
        int dx = tx - cx;
        int dy = ty - cy;
        double dist = Math.hypot(dx, dy);

        int fovPx = (int) (Math.min(screenW, screenH) * (cfg.aimFov / 720.0));
        if (dist > fovPx || dist < 3) {
            return;
        }

        double step = Math.max(2.0, dist / smooth);
        int nx = cx + (int) Math.round(dx / dist * step);
        int ny = cy + (int) Math.round(dy / dist * step);

        if (Touch.swipe(cx, cy, nx, ny, (int) Math.min(120, 20 + dist))) {
            lastAimAt = now;
        }
    }
}
