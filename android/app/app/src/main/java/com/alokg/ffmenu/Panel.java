package com.alokg.ffmenu;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The settings panel: a floating window over the game, driven by the same
 * Config the laptop writes to.
 *
 * <p>This is the part that makes the app usable without a laptop. Every row
 * changes one field and immediately pushes the whole config down the feed
 * channel, so the module always holds the current state rather than a diff it
 * has to reassemble.
 *
 * <p>Nothing here is game-specific: it is a table of toggles and steppers over
 * {@link Config}, which means the same panel works whatever the module turns
 * out to be.
 */
final class Panel {

    private static final int ON = 0xFF7EE787;
    private static final int OFF = 0xFF8B949E;
    private static final int BG = 0xE60D1117;
    private static final int BG_ON = 0x6622C55E;

    private static Panel live;

    private final Activity ctx;
    private final Config cfg;
    private final WindowManager wm;
    private final WindowManager.LayoutParams lp;

    private LinearLayout root;
    private TextView status;
    private int dx, dy;

    /** Vision toggles are local to the app - they are not part of the feed. */
    private static boolean visionBoxes = true;
    private static boolean visionAssist = true;

    private Panel(Activity ctx, Config cfg) {
        this.ctx = ctx;
        this.cfg = cfg;
        this.wm = (WindowManager) ctx.getSystemService(Activity.WINDOW_SERVICE);
        this.lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        this.lp.gravity = Gravity.TOP | Gravity.START;
        this.lp.x = 24;
        this.lp.y = 160;
    }

    static boolean visible() {
        return live != null;
    }

    static void toggle(Activity ctx, Config cfg) {
        if (live != null) {
            live.detach();
            live = null;
            return;
        }
        live = new Panel(ctx, cfg);
        live.attach();
    }

    static void refresh() {
        if (live != null) {
            live.rebuild();
        }
    }

    static void hide() {
        if (live != null) {
            live.detach();
            live = null;
        }
    }

    // ---- window ------------------------------------------------------------

    private void attach() {
        root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        rebuild();
        try {
            wm.addView(root, lp);
        } catch (RuntimeException e) {
            live = null;
        }
    }

    private void detach() {
        try {
            wm.removeView(root);
        } catch (RuntimeException ignored) {
            // already gone
        }
    }

    private View.OnTouchListener drag() {
        return (v, e) -> {
            switch (e.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    dx = lp.x - (int) e.getRawX();
                    dy = lp.y - (int) e.getRawY();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    lp.x = (int) e.getRawX() + dx;
                    lp.y = (int) e.getRawY() + dy;
                    try {
                        wm.updateViewLayout(root, lp);
                    } catch (RuntimeException ignored) {
                    }
                    return true;
                default:
                    return false;
            }
        };
    }

    // ---- rows --------------------------------------------------------------

    private void rebuild() {
        if (root == null) {
            return;
        }
        root.removeAllViews();

        TextView bar = new TextView(ctx);
        bar.setText("  ≡  PANEL        " + (cfg.aimEnabled ? "AIM" : "--")
                + (cfg.espBox ? "  ESP" : ""));
        bar.setTextColor(ON);
        bar.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        bar.setTextSize(12);
        bar.setPadding(10, 14, 10, 14);
        bar.setBackgroundColor(BG);
        bar.setOnTouchListener(drag());
        root.addView(bar);

        if (!License.unlocked()) {
            local("LOCKED - masukkan key di app", false, () -> {
            });
            status = new TextView(ctx);
            status.setTextColor(OFF);
            status.setTypeface(Typeface.MONOSPACE);
            status.setTextSize(10);
            status.setPadding(10, 8, 10, 8);
            status.setBackgroundColor(BG);
            status.setText(License.current() == null
                    ? "key: none" : License.current().toString());
            root.addView(status);

            TextView closeLocked = new TextView(ctx);
            closeLocked.setText("  HIDE PANEL  ");
            closeLocked.setTextColor(OFF);
            closeLocked.setTypeface(Typeface.MONOSPACE);
            closeLocked.setTextSize(11);
            closeLocked.setPadding(10, 12, 10, 16);
            closeLocked.setBackgroundColor(BG);
            closeLocked.setOnClickListener(v -> toggle(ctx, cfg));
            root.addView(closeLocked);
            return;
        }

        section("ESP");
        bool("BOX", cfg.espBox, () -> cfg.espBox = !cfg.espBox);
        bool("LINE", cfg.espLine, () -> cfg.espLine = !cfg.espLine);
        bool("NAME", cfg.espName, () -> cfg.espName = !cfg.espName);
        bool("DISTANCE", cfg.espDistance, () -> cfg.espDistance = !cfg.espDistance);
        bool("HEALTH", cfg.espHealth, () -> cfg.espHealth = !cfg.espHealth);
        bool("SKELETON", cfg.espSkeleton, () -> cfg.espSkeleton = !cfg.espSkeleton);
        number("MAX DIST", (int) cfg.espMaxDist, 25, 25, 500,
                v -> cfg.espMaxDist = v);

        section("AIM");
        bool("AIMBOT", cfg.aimEnabled, () -> cfg.aimEnabled = !cfg.aimEnabled);
        cycle("BONE", cfg.aimBone, new String[]{"head", "neck", "chest", "body"},
                v -> cfg.aimBone = v);
        number("FOV SIZE", cfg.aimFov, 5, 10, 360, v -> cfg.aimFov = v);
        number("SMOOTH", cfg.aimSmooth, 1, 1, 20, v -> cfg.aimSmooth = v);
        number("AIM DIST", (int) cfg.aimMaxDist, 25, 25, 400,
                v -> cfg.aimMaxDist = v);
        bool("HIDE FOV", cfg.aimHideFov, () -> cfg.aimHideFov = !cfg.aimHideFov);
        bool("DRAG HS", cfg.dragHeadshot, () -> cfg.dragHeadshot = !cfg.dragHeadshot);

        section("VISION (screen only)");
        local("BOXES", Vision.running() && visionBoxes, () -> {
            visionBoxes = !visionBoxes;
            Vision.setBoxes(visionBoxes);
        });
        local("ASSIST", visionAssist, () -> {
            visionAssist = !visionAssist;
            Vision.setAssist(visionAssist);
        });
        local(com.alokg.ffmenu.vision.AimService.ready()
                ? "TOUCH: ready" : "TOUCH: tap to enable", false, () -> {
            if (!com.alokg.ffmenu.vision.AimService.ready()) {
                ctx.startActivity(new android.content.Intent(
                        android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS));
            }
        });

        section("WEAPON");
        bool("NO RECOIL", cfg.noRecoil, () -> cfg.noRecoil = !cfg.noRecoil);
        bool("NO SPREAD", cfg.noSpread, () -> cfg.noSpread = !cfg.noSpread);
        bool("FAST SWITCH", cfg.fastSwitch, () -> cfg.fastSwitch = !cfg.fastSwitch);
        bool("RELOAD x2", cfg.reloadX2, () -> cfg.reloadX2 = !cfg.reloadX2);

        status = new TextView(ctx);
        status.setTextColor(OFF);
        status.setTypeface(Typeface.MONOSPACE);
        status.setTextSize(10);
        status.setPadding(10, 8, 10, 8);
        status.setBackgroundColor(BG);
        status.setText("feed: " + (FeedService.connected() ? "connected" : "not connected"));
        status.setOnClickListener(v -> {
            status.setText(push() ? "sent" : "feed not running");
        });
        root.addView(status);

        TextView close = new TextView(ctx);
        close.setText("  HIDE PANEL  ");
        close.setTextColor(OFF);
        close.setTypeface(Typeface.MONOSPACE);
        close.setTextSize(11);
        close.setPadding(10, 12, 10, 16);
        close.setBackgroundColor(BG);
        close.setOnClickListener(v -> toggle(ctx, cfg));
        root.addView(close);
    }

    private void local(String label, boolean on, Runnable flip) {
        TextView t = new TextView(ctx);
        t.setText("   " + (on ? "[x] " : "[ ] ") + label);
        t.setTextColor(on ? ON : OFF);
        t.setTypeface(Typeface.MONOSPACE);
        t.setTextSize(12);
        t.setPadding(10, 10, 10, 10);
        t.setBackgroundColor(on ? BG_ON : 0x00000000);
        t.setOnClickListener(v -> {
            flip.run();
            rebuild();
        });
        root.addView(t);
    }

    private void section(String name) {        TextView t = new TextView(ctx);
        t.setText("  " + name);
        t.setTextColor(Color.parseColor("#58A6FF"));
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextSize(11);
        t.setPadding(10, 12, 10, 4);
        t.setBackgroundColor(BG);
        root.addView(t);
    }

    private void bool(String label, boolean on, Runnable flip) {        TextView t = new TextView(ctx);
        t.setText("   " + (on ? "[x] " : "[ ] ") + label);
        t.setTextColor(on ? ON : OFF);
        t.setTypeface(Typeface.MONOSPACE);
        t.setTextSize(12);
        t.setPadding(10, 10, 10, 10);
        t.setBackgroundColor(on ? BG_ON : 0x00000000);
        t.setOnClickListener(v -> {
            flip.run();
            rebuild();
            push();
        });
        root.addView(t);
    }

    private void cycle(String label, String value, String[] values,
                       java.util.function.Consumer<String> set) {
        TextView t = new TextView(ctx);
        t.setText("   " + label + ": " + value.toUpperCase());
        t.setTextColor(Color.parseColor("#D2A8FF"));
        t.setTypeface(Typeface.MONOSPACE);
        t.setTextSize(12);
        t.setPadding(10, 10, 10, 10);
        t.setOnClickListener(v -> {
            int i = 0;
            for (int k = 0; k < values.length; k++) {
                if (values[k].equals(value)) {
                    i = k;
                }
            }
            set.accept(values[(i + 1) % values.length]);
            rebuild();
            push();
        });
        root.addView(t);
    }

    private void number(String label, int value, int step, int lo, int hi,
                        java.util.function.IntConsumer set) {
        LinearLayout row = new LinearLayout(ctx);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        row.addView(step("−", () -> set.accept(Math.max(lo, value - step))));
        TextView name = new TextView(ctx);
        name.setText(" " + label + ": " + value + " ");
        name.setTextColor(OFF);
        name.setTypeface(Typeface.MONOSPACE);
        name.setTextSize(12);
        name.setPadding(8, 10, 8, 10);
        row.addView(name);
        row.addView(step("+", () -> set.accept(Math.min(hi, value + step))));
        root.addView(row);
    }

    private TextView step(String glyph, Runnable action) {
        TextView t = new TextView(ctx);
        t.setText(" " + glyph + " ");
        t.setTextColor(ON);
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextSize(14);
        t.setPadding(12, 8, 12, 8);
        t.setBackgroundColor(0x33FFFFFF);
        t.setOnClickListener(v -> {
            action.run();
            rebuild();
            push();
        });
        return t;
    }

    private boolean push() {
        return FeedService.push(cfg.toJson().toString());
    }
}