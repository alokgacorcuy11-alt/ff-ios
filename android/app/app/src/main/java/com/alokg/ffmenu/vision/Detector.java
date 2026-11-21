package com.alokg.ffmenu.vision;

import java.util.ArrayList;
import java.util.List;

/**
 * Finds small, wide, saturated patches in a captured frame - which is what an
 * enemy nameplate and health bar look like once the screen is pixels.
 *
 * <p>Deliberately free of Android imports: it takes an ARGB array and returns
 * boxes, so it can be tested on a synthetic frame on a desktop instead of being
 * tuned by staring at a phone.
 *
 * <p>Nothing here knows Free Fire's palette, and it should not: the colour is a
 * parameter, set once from a captured frame by tapping the thing you want to
 * find. Guessing a hex value and hoping is how these detectors end up matching
 * the sky.
 */
public final class Detector {

    public static final int MODE_PICK = 0;
    public static final int MODE_DOMINANT = 1;

    private Detector() {
    }

    public static final class Params {
        /** MODE_PICK matches one colour, MODE_DOMINANT matches a channel lead. */
        public int mode = MODE_DOMINANT;
        /** ARGB to match in MODE_PICK. */
        public int rgb = 0xFF4040;
        /** Per-channel tolerance for MODE_PICK. */
        public int tol = 60;
        /** 0 red, 1 green, 2 blue, for MODE_DOMINANT. */
        public int channel = 0;
        /** How far that channel has to lead the others. */
        public int dominance = 45;
        /** Minimum value for a pixel to count at all - kills dark noise. */
        public int minValue = 70;

        public int minW = 18;
        public int maxW = 260;
        public int minH = 3;
        public int maxH = 46;
        public int minPixels = 26;

        /** Vertical band, in tenths of a percent of the frame height. */
        public int bandTop = 0;
        public int bandBottom = 1000;

        public int maxBlobs = 8;
        /** Seed scan step. The fill itself always runs at full resolution. */
        public int stride = 3;

        public boolean inBand(int y, int h) {
            return y * 1000 >= bandTop * Math.max(1, h) / 1000 * 1000
                    && y * 1000 <= bandBottom * Math.max(1, h);
        }
    }

    public static final class Blob {
        public int x, y, w, h, pixels;

        public int cx() {
            return x + (w >> 1);
        }

        public int cy() {
            return y + (h >> 1);
        }

        public int area() {
            return w * h;
        }

        /** Dense and exactly the shape we asked for wins. */
        public int score() {
            int fill = area() == 0 ? 0 : pixels * 100 / area();
            int aspect = h == 0 ? 999 : w / Math.max(1, h);
            return pixels + fill * 4 + Math.min(aspect, 20) * 3;
        }
    }

    public static boolean matches(int px, Params p) {
        int r = (px >> 16) & 0xff;
        int g = (px >> 8) & 0xff;
        int b = px & 0xff;
        int v = Math.max(r, Math.max(g, b));
        if (v < p.minValue) {
            return false;
        }
        if (p.mode == MODE_PICK) {
            int dr = Math.abs(r - ((p.rgb >> 16) & 0xff));
            int dg = Math.abs(g - ((p.rgb >> 8) & 0xff));
            int db = Math.abs(b - (p.rgb & 0xff));
            return Math.max(dr, Math.max(dg, db)) <= p.tol;
        }
        int lead;
        if (p.channel == 0) {
            lead = r - Math.max(g, b);
        } else if (p.channel == 1) {
            lead = g - Math.max(r, b);
        } else {
            lead = b - Math.max(r, g);
        }
        return lead >= p.dominance;
    }

    /** Deterministic: same frame and params give the same boxes, in the same order. */
    public static List<Blob> detect(int[] argb, int w, int h, Params p) {
        List<Blob> out = new ArrayList<>();
        if (argb == null || w <= 1 || h <= 1 || argb.length < w * h) {
            return out;
        }

        int top = Math.max(0, p.bandTop * h / 1000);
        int bottom = Math.min(h - 1, p.bandBottom * h / 1000);
        if (bottom <= top) {
            return out;
        }

        boolean[] seen = new boolean[w * h];
        int[] stack = new int[Math.min(w * h, 1 << 16)];
        int stride = Math.max(1, p.stride);

        for (int sy = top; sy <= bottom; sy += stride) {
            for (int sx = 0; sx < w; sx += stride) {
                int i = sy * w + sx;
                if (seen[i] || !matches(argb[i], p)) {
                    continue;
                }
                Blob blob = fill(argb, w, h, seen, stack, sx, sy, top, bottom, p);
                if (blob != null) {
                    out.add(blob);
                }
            }
        }

        // nearest to the middle of the screen first: that is the one the
        // crosshair is pointing at
        final int mx = w / 2;
        final int my = (top + bottom) / 2;
        out.sort((a, b) -> {
            int da = Math.abs(a.cx() - mx) + Math.abs(a.cy() - my);
            int db = Math.abs(b.cx() - mx) + Math.abs(b.cy() - my);
            if (da != db) {
                return da - db;
            }
            return b.score() - a.score();
        });
        return out.size() > p.maxBlobs ? new ArrayList<>(out.subList(0, p.maxBlobs)) : out;
    }

    private static Blob fill(int[] argb, int w, int h, boolean[] seen, int[] stack,
                             int sx, int sy, int top, int bottom, Params p) {
        int sp = 0;
        stack[sp++] = sy * w + sx;

        int minX = sx, maxX = sx, minY = sy, maxY = sy, count = 0;
        int kind = argb[sy * w + sx];

        while (sp > 0) {
            int cur = stack[--sp];
            if (seen[cur]) {
                continue;
            }
            seen[cur] = true;

            int y = cur / w;
            int x = cur - y * w;
            if (y < top || y > bottom) {
                continue;
            }
            // keeps the fill from walking into a different colour family and
            // welding two nameplates into one box
            if (!matches(argb[cur], p) || family(argb[cur]) != family(kind)) {
                continue;
            }

            count++;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;

            if (sp + 4 >= stack.length) {
                continue;
            }
            if (x > 0) stack[sp++] = cur - 1;
            if (x + 1 < w) stack[sp++] = cur + 1;
            if (y > 0) stack[sp++] = cur - w;
            if (y + 1 < h) stack[sp++] = cur + w;
        }

        Blob b = new Blob();
        b.x = minX;
        b.y = minY;
        b.w = maxX - minX + 1;
        b.h = maxY - minY + 1;
        b.pixels = count;

        if (b.w < p.minW || b.w > p.maxW || b.h < p.minH || b.h > p.maxH
                || b.pixels < p.minPixels) {
            return null;
        }
        return b;
    }

    /** Coarse colour class: 0 dark, 1 red, 2 green, 3 blue, 4 bright/neutral. */
    static int family(int px) {
        int r = (px >> 16) & 0xff;
        int g = (px >> 8) & 0xff;
        int b = px & 0xff;
        int v = Math.max(r, Math.max(g, b));
        if (v < 60) {
            return 0;
        }
        if (r > g + 30 && r > b + 30) {
            return 1;
        }
        if (g > r + 30 && g > b + 30) {
            return 2;
        }
        if (b > r + 30 && b > g + 30) {
            return 3;
        }
        return 4;
    }
}