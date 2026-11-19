package com.alokg.ffmenu.vision;

import android.content.Context;
import android.graphics.PixelFormat;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.os.Handler;
import android.os.HandlerThread;

import java.nio.ByteBuffer;

/**
 * Turns a MediaProjection into ARGB frames.
 *
 * <p>MediaProjection is the only way an ordinary app can look at another app's
 * output: the user grants it once, the system composites the display, and what
 * arrives is pixels. No memory, no process, no permission the game can revoke.
 *
 * <p>Row stride is not always width*4 - the producer pads rows - so the copy is
 * per row instead of a bulk arraycopy. Getting that wrong produces a sheared
 * image that still looks plausible, which is the worst kind of bug.
 */
public final class Capture {

    public interface Sink {
        void onFrame(int[] argb, int width, int height);
    }

    private final ImageReader reader;
    private final VirtualDisplay display;
    private final HandlerThread thread;
    private final int width, height;
    private volatile boolean closed;

    public Capture(Context ctx, MediaProjection projection, int width, int height,
                   int densityDpi, Sink sink) {
        this.width = width;
        this.height = height;

        thread = new HandlerThread("vision-capture");
        thread.start();
        Handler handler = new Handler(thread.getLooper());

        reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
        reader.setOnImageAvailableListener(r -> {
            Image img = null;
            try {
                img = r.acquireLatestImage();
                if (img == null || closed) {
                    return;
                }
                int[] out = new int[this.width * this.height];
                if (!copy(img, out)) {
                    return;
                }
                sink.onFrame(out, this.width, this.height);
            } catch (RuntimeException ignored) {
                // a frame dropped during rotation is not worth a crash
            } finally {
                if (img != null) {
                    try {
                        img.close();
                    } catch (RuntimeException ignored) {
                    }
                }
            }
        }, handler);

        display = projection.createVirtualDisplay("vision", width, height, densityDpi,
                android.hardware.display.DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.getSurface(), null, handler);
    }

    private boolean copy(Image img, int[] out) {
        Image.Plane[] planes = img.getPlanes();
        if (planes.length == 0) {
            return false;
        }
        Image.Plane plane = planes[0];
        ByteBuffer buf = plane.getBuffer();
        int rowStride = plane.getRowStride();
        int pixelStride = plane.getPixelStride();
        if (rowStride <= 0 || pixelStride != 4) {
            return false;
        }
        int rowBytes = width * 4;
        byte[] row = new byte[rowBytes];
        for (int y = 0; y < height; y++) {
            int at = y * rowStride;
            if (at + rowBytes > buf.limit()) {
                return false;
            }
            buf.position(at);
            buf.get(row, 0, rowBytes);
            int o = y * width;
            for (int x = 0, i = 0; x < width; x++, i += 4) {
                int r = row[i] & 0xff;
                int g = row[i + 1] & 0xff;
                int b = row[i + 2] & 0xff;
                out[o + x] = 0xFF000000 | (r << 16) | (g << 8) | b;
            }
        }
        return true;
    }

    public void stop() {
        closed = true;
        try {
            reader.setOnImageAvailableListener(null, null);
        } catch (RuntimeException ignored) {
        }
        try {
            display.release();
        } catch (RuntimeException ignored) {
        }
        try {
            reader.close();
        } catch (RuntimeException ignored) {
        }
        thread.quitSafely();
    }
}