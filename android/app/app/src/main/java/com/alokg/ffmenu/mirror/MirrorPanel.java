package com.alokg.ffmenu.mirror;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.alokg.ffmenu.License;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The switchboard, drawn over the game.
 *
 * <p>One row per feature the module implements, plus the FOV size and a live
 * status line. Every tap is a request to the module; nothing is assumed to have
 * worked. If a call fails the row snaps back and the reason goes on the status
 * line, because a panel that shows ON for a feature that never enabled is worse
 * than no panel.
 *
 * <p>The row order and the item numbers come from {@link Bridge#ITEMS}. They
 * are not sequential: 3 and 4 do not exist and 5 is the drag headshot.
 */
public final class MirrorPanel {

    private static final int BG = Color.parseColor("#0d1117");
    private static final int ROW = Color.parseColor("#161b22");
    private static final int ON = Color.parseColor("#7ee787");
    private static final int OFF = Color.parseColor("#8b949e");
    private static final int ACCENT = Color.parseColor("#58a6ff");
    private static final int ERR = Color.parseColor("#f85149");

    private static MirrorPanel live;

    private final Context ctx;
    private final WindowManager wm;
    private final Bridge bridge = new Bridge();
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<Integer, Boolean> state = new LinkedHashMap<>();
    private final Map<Integer, TextView> rows = new LinkedHashMap<>();

    private LinearLayout root;
    private WindowManager.LayoutParams params;
    private TextView statusLine;
    private TextView fovValue;
    private int fov = 60;
    private boolean polling;

    private MirrorPanel(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.wm = (WindowManager) this.ctx.getSystemService(Context.WINDOW_SERVICE);
        for (int item : Bridge.ITEMS) {
            state.put(item, false);
        }
    }

    // ---- show / hide -------------------------------------------------------

    public static void toggle(Context ctx) {
        if (live != null) {
            live.hide();
            return;
        }
        MirrorPanel p = new MirrorPanel(ctx);
        p.show();
        live = p;
    }

    public static boolean visible() {
        return live != null;
    }

    /** Rebuilds after the key changes, so the lock row disappears. */
    public static void refresh() {
        MirrorPanel p = live;
        if (p == null) {
            return;
        }
        Context c = p.ctx;
        p.hide();
        MirrorPanel next = new MirrorPanel(c);
        next.show();
        live = next;
    }

    /** True when the overlay permission is missing; the caller sends the user. */
    public static boolean needsPermission(Context ctx) {
        return !Settings.canDrawOverlays(ctx);
    }

    private void show() {
        root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(BG);
        bg.setCornerRadius(dp(10));
        bg.setStroke(dp(1), Color.parseColor("#30363d"));
        root.setBackground(bg);
        root.setPadding(dp(10), dp(8), dp(10), dp(8));

        TextView bar = new TextView(ctx);
        bar.setText("  FF · mirror  ");
        bar.setTextColor(ACCENT);
        bar.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        bar.setTextSize(12);
        bar.setPadding(dp(4), dp(8), dp(4), dp(8));
        bar.setOnTouchListener(drag());
        root.addView(bar);

        if (!License.unlocked()) {
            root.addView(row("LOCKED - masukkan key di app", OFF, null));
            root.addView(status("key: " + (License.current() == null
                    ? "none" : License.current().toString()), OFF));
            root.addView(row("  HIDE  ", OFF, this::hide));
        } else {
            ScrollView scroll = new ScrollView(ctx);
            LinearLayout list = new LinearLayout(ctx);
            list.setOrientation(LinearLayout.VERTICAL);
            scroll.addView(list);

            for (int item : Bridge.ITEMS) {
                TextView t = row("", OFF, () -> flip(item));
                rows.put(item, t);
                list.addView(t);
                paint(item, false);
            }

            LinearLayout fovRow = new LinearLayout(ctx);
            fovRow.setOrientation(LinearLayout.HORIZONTAL);
            fovRow.setGravity(Gravity.CENTER_VERTICAL);

            TextView minus = chip("-");
            minus.setOnClickListener(v -> setFov(fov - 5));

            fovValue = new TextView(ctx);
            fovValue.setTextColor(ON);
            fovValue.setTypeface(Typeface.MONOSPACE);
            fovValue.setTextSize(11);
            fovValue.setGravity(Gravity.CENTER);
            fovValue.setPadding(0, dp(8), 0, dp(8));
            fovValue.setText("FOV " + fov);

            TextView plus = chip("+");
            plus.setOnClickListener(v -> setFov(fov + 5));

            fovRow.addView(minus, new LinearLayout.LayoutParams(dp(40),
                    LinearLayout.LayoutParams.WRAP_CONTENT));
            fovRow.addView(fovValue, new LinearLayout.LayoutParams(0,
                    LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            fovRow.addView(plus, new LinearLayout.LayoutParams(dp(40),
                    LinearLayout.LayoutParams.WRAP_CONTENT));
            list.addView(fovRow);

            LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(250));
            root.addView(scroll, slp);
            root.addView(status("modul: menunggu...", OFF));
            root.addView(row("  HIDE  ", OFF, this::hide));
        }

        params = new WindowManager.LayoutParams(
                dp(196),
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
                android.graphics.PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.START;
        params.x = dp(12);
        params.y = dp(90);
        wm.addView(root, params);

        if (License.unlocked()) {
            polling = true;
            poll();
        }
    }

    private void hide() {
        polling = false;
        ui.removeCallbacksAndMessages(null);
        if (root != null) {
            try {
                wm.removeView(root);
            } catch (Exception ignored) {
            }
            root = null;
        }
        io.shutdown();
        if (live == this) {
            live = null;
        }
    }

    // ---- actions -----------------------------------------------------------

    private void flip(int item) {
        boolean next = !state.get(item);
        state.put(item, next);
        paint(item, next);
        io.execute(() -> {
            try {
                bridge.command(item, next);
                say(Bridge.label(item) + (next ? " ON" : " OFF"), ON);
            } catch (Exception e) {
                state.put(item, !next);
                ui.post(() -> paint(item, !next));
                say(Bridge.label(item) + ": " + e.getMessage(), ERR);
            }
        });
    }

    private void setFov(int v) {
        int next = Math.max(10, Math.min(90, v));
        fov = next;
        fovValue.setText("FOV " + next);
        io.execute(() -> {
            try {
                bridge.fov(next);
                say("FOV " + next, ON);
            } catch (Exception e) {
                say("FOV: " + e.getMessage(), ERR);
            }
        });
    }

    private void poll() {
        if (!polling) {
            return;
        }
        io.execute(() -> {
            String text;
            int color;
            try {
                text = bridge.status().toString();
                color = ON;
            } catch (Exception e) {
                text = "modul: " + e.getMessage();
                color = OFF;
            }
            final String t = text;
            final int c = color;
            ui.post(() -> {
                if (polling) {
                    status(t, c);
                }
            });
        });
        ui.postDelayed(this::poll, 1500);
    }

    // ---- widgets -----------------------------------------------------------

    private TextView row(String label, int color, Runnable action) {
        TextView t = new TextView(ctx);
        t.setText(label);
        t.setTextColor(color);
        t.setTypeface(Typeface.MONOSPACE);
        t.setTextSize(11);
        t.setPadding(dp(8), dp(9), dp(8), dp(9));
        GradientDrawable g = new GradientDrawable();
        g.setColor(ROW);
        g.setCornerRadius(dp(4));
        t.setBackground(g);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(3);
        t.setLayoutParams(lp);
        if (action != null) {
            t.setOnClickListener(v -> action.run());
        }
        return t;
    }

    private TextView chip(String text) {
        TextView t = new TextView(ctx);
        t.setText(text);
        t.setTextColor(ACCENT);
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        t.setTextSize(14);
        t.setGravity(Gravity.CENTER);
        t.setPadding(0, dp(6), 0, dp(6));
        GradientDrawable g = new GradientDrawable();
        g.setColor(ROW);
        g.setCornerRadius(dp(4));
        t.setBackground(g);
        return t;
    }

    private TextView status(String text, int color) {
        if (statusLine == null) {
            statusLine = new TextView(ctx);
            statusLine.setTypeface(Typeface.MONOSPACE);
            statusLine.setTextSize(9);
            statusLine.setPadding(dp(4), dp(6), dp(4), dp(6));
            statusLine.setBackgroundColor(BG);
        }
        statusLine.setText(text);
        statusLine.setTextColor(color);
        return statusLine;
    }

    private void paint(int item, boolean on) {
        TextView t = rows.get(item);
        if (t == null) {
            return;
        }
        t.setText((on ? " ● " : " ○ ") + Bridge.label(item) + (on ? "  ON" : "  OFF"));
        t.setTextColor(on ? ON : OFF);
    }

    private void say(String text, int color) {
        ui.post(() -> {
            if (statusLine != null) {
                statusLine.setText(text);
                statusLine.setTextColor(color);
            }
        });
    }

    private View.OnTouchListener drag() {
        return new View.OnTouchListener() {
            private int dx;
            private int dy;

            @Override
            public boolean onTouch(View v, MotionEvent e) {
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        dx = (int) e.getRawX() - params.x;
                        dy = (int) e.getRawY() - params.y;
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        params.x = (int) e.getRawX() - dx;
                        params.y = (int) e.getRawY() - dy;
                        try {
                            wm.updateViewLayout(root, params);
                        } catch (Exception ignored) {
                        }
                        return true;
                    default:
                        return false;
                }
            }
        };
    }

    private int dp(int v) {
        return Math.round(v * ctx.getResources().getDisplayMetrics().density);
    }
}
