package com.alokg.ffmenu.device;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * What this phone can actually run, decided once at startup.
 *
 * <p>The product has to behave the same on a Snapdragon 450 with 4 GB and on a
 * flagship, and those two cannot be handed the same settings: capturing a
 * 2400x1080 frame and running a flood fill over it at 60 fps would freeze the
 * first one and the buyer would call it broken, not slow. So the app measures
 * the phone and picks a profile itself. Nothing here is a preference the user
 * has to understand.
 *
 * <p>Deliberately free of Android imports: it takes five numbers and returns a
 * decision, so the decision can be run against the whole range of real devices
 * on a desktop instead of being trusted because one phone looked fine.
 */
public final class Tier {

    /** Cannot capture at all - older than MediaProjection. */
    public static final int UNSUPPORTED = 0;
    /** Slow: small capture, few frames, no aim assist. */
    public static final int LIGHT = 1;
    /** Ordinary: half-resolution capture, ~12 fps. */
    public static final int BALANCED = 2;
    /** Headroom to spare: 70% capture, 15 fps. */
    public static final int FULL = 3;

    /** Ceiling on captured pixels. Above this the detector costs more than it gives. */
    private static final long PIXEL_BUDGET = 1_200_000L;

    public final int level;
    /** Capture size as a percentage of the screen, before the pixel budget is applied. */
    public final int capturePct;
    /** Frames per second the detector is allowed to see. */
    public final int fps;
    /** Seed scan step. Larger is cheaper and finds fewer small things. */
    public final int stride;
    /** Whether synthetic touch is possible at all on this API level. */
    public final boolean assist;
    /** Why the profile is what it is, in the order the numbers were read. */
    public final List<String> reasons;

    private Tier(int level, int capturePct, int fps, int stride, boolean assist,
                 List<String> reasons) {
        this.level = level;
        this.capturePct = capturePct;
        this.fps = fps;
        this.stride = stride;
        this.assist = assist;
        this.reasons = Collections.unmodifiableList(reasons);
    }

    /**
     * @param api     Build.VERSION.SDK_INT
     * @param ramMb   total physical memory in MB
     * @param cores   available processors
     * @param maxKhz  highest CPU frequency in kHz, or 0 when unreadable
     */
    public static Tier of(int api, int ramMb, int cores, int maxKhz) {
        List<String> why = new ArrayList<>();

        if (api < 21) {
            why.add("Android " + api + " tidak punya rekam layar (butuh 5.0)");
            return new Tier(UNSUPPORTED, 0, 0, 0, false, why);
        }
        boolean assist = api >= 24;   // AccessibilityService.dispatchGesture

        int score = 0;

        if (ramMb >= 3072) {
            score += 2;
        } else if (ramMb >= 2048) {
            score += 1;
        } else {
            why.add("RAM " + ramMb + " MB - mode hemat");
        }

        if (cores >= 8) {
            score += 2;
        } else if (cores >= 6) {
            score += 1;
        } else {
            why.add(cores + " inti CPU - mode hemat");
        }

        if (maxKhz >= 2_000_000) {
            score += 2;
        } else if (maxKhz >= 1_600_000) {
            score += 1;
        } else if (maxKhz > 0) {
            why.add("CPU " + (maxKhz / 1000) + " MHz - mode hemat");
        }

        int level;
        if (score >= 6) {
            level = FULL;
        } else if (score >= 3) {
            level = BALANCED;
        } else {
            level = LIGHT;
        }

        // A phone with 2 GB cannot hold a big capture buffer no matter how many
        // cores it claims, and a 4-core phone cannot flood-fill one.
        if (ramMb < 2048 || cores <= 4) {
            level = Math.min(level, BALANCED);
        }
        if (ramMb < 1536) {
            level = LIGHT;
        }

        switch (level) {
            case FULL:
                return new Tier(FULL, 70, 15, 3, assist, why);
            case BALANCED:
                return new Tier(BALANCED, 50, 12, 4, assist, why);
            default:
                return new Tier(LIGHT, 35, 8, 6, assist, why);
        }
    }

    /**
     * The size the capture surface is created at.
     *
     * <p>MediaProjection scales the display into whatever surface it is given,
     * so this is the whole optimisation: hand it a small surface and every
     * stage after it - the buffer, the copy, the flood fill - gets cheaper by
     * the same factor. Boxes are mapped back to screen coordinates by the
     * caller.
     *
     * @return {width, height}, never zero.
     */
    public int[] captureSize(int screenW, int screenH) {
        int sw = Math.max(1, screenW);
        int sh = Math.max(1, screenH);

        double scale = capturePct / 100.0;
        double px = (sw * scale) * (sh * scale);
        if (px > PIXEL_BUDGET) {
            scale *= Math.sqrt(PIXEL_BUDGET / px);
        }

        int w = (int) Math.round(sw * scale);
        int h = (int) Math.round(sh * scale);
        // A surface below this cannot hold a readable nameplate anyway.
        w = Math.max(160, Math.min(w, sw));
        h = Math.max(90, Math.min(h, sh));
        return new int[]{w, h};
    }

    public String name() {
        switch (level) {
            case FULL:
                return "FULL";
            case BALANCED:
                return "BALANCED";
            case LIGHT:
                return "LIGHT";
            default:
                return "UNSUPPORTED";
        }
    }

    /** One line for the log: profile, capture, rate. */
    public String describe(int screenW, int screenH) {
        if (level == UNSUPPORTED) {
            return "profil: UNSUPPORTED";
        }
        int[] s = captureSize(screenW, screenH);
        return "profil: " + name() + " - rekam " + s[0] + "x" + s[1]
                + " @ " + fps + " fps" + (assist ? "" : ", tanpa sentuh");
    }
}
