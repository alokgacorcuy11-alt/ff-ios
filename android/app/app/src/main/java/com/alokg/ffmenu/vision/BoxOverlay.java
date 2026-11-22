package com.alokg.ffmenu.vision;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.view.View;
import android.view.WindowManager;

/**
 * Draws the boxes the detector found, over the game.
 *
 * <p>A separate window from the settings panel, and deliberately so: this one
 * covers the whole screen and takes no touches, while the panel is small and
 * draggable. Both are ordinary overlay windows, which means both are gone the
 * moment the app is uninstalled.
 *
 * <p>Coordinates arrive already in screen space. The capture runs at whatever
 * size the phone's profile chose, so mapping capture pixels to screen pixels is
 * the caller's job - one multiply - and this class never has to know the two
 * differ.
 */
public final class BoxOverlay {

    private final WindowManager wm;
    private final WindowManager.LayoutParams lp;
    private final Frames view;
    private boolean attached;

    public BoxOverlay(Context ctx, int screenW, int screenH) {
        this.wm = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
        this.view = new Frames(ctx);
        this.lp = new WindowManager.LayoutParams(
                screenW, screenH,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        this.lp.gravity = android.view.Gravity.TOP | android.view.Gravity.START;
    }

    public void attach() {
        if (attached) {
            return;
        }
        try {
            wm.addView(view, lp);
            attached = true;
        } catch (RuntimeException e) {
            attached = false;
        }
    }

    /**
     * Follows the display when it rotates.
     *
     * <p>The game is landscape and the app is portrait, so this happens on the
     * first second of every session. A window left at the old size draws every
     * box in the wrong place, which looks exactly like a broken detector.
     */
    public void resize(int screenW, int screenH) {
        if (lp.width == screenW && lp.height == screenH) {
            return;
        }
        lp.width = screenW;
        lp.height = screenH;
        if (!attached) {
            return;
        }
        try {
            wm.updateViewLayout(view, lp);
        } catch (RuntimeException ignored) {
        }
    }

    /** @param xyxy flat {left, top, right, bottom} per box, in screen pixels. */
    public void updateRects(int[] xyxy) {
        view.set(xyxy);
    }

    public void clear() {
        view.set(new int[0]);
    }

    public void detach() {
        if (!attached) {
            return;
        }
        try {
            wm.removeView(view);
        } catch (RuntimeException ignored) {
        }
        attached = false;
    }

    private static final class Frames extends View {
        private final Paint stroke = new Paint();
        private volatile int[] boxes = new int[0];

        Frames(Context ctx) {
            super(ctx);
            stroke.setStyle(Paint.Style.STROKE);
            stroke.setStrokeWidth(3f);
            stroke.setAntiAlias(true);
            stroke.setColor(0xFF7EE787);
        }

        void set(int[] b) {
            boxes = b;
            postInvalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int[] b = boxes;
            for (int i = 0; i + 3 < b.length; i += 4) {
                canvas.drawRect(b[i], b[i + 1], b[i + 2], b[i + 3], stroke);
            }
        }
    }
}
