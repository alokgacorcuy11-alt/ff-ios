package com.alokg.ffmenu;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

import com.alokg.ffmenu.feed.FeedChannel;
import com.alokg.ffmenu.feed.FeedServer;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.Socket;

/**
 * Holds the feed open for as long as the panel is in the background.
 *
 * <p>It reads {@code container.bin} and {@code manifest.json} from assets,
 * binds the loopback port the client was built with, hands the container over
 * once, and then keeps the socket for commands. Nothing is written to disk at
 * any point - the container came out of the APK, the module goes straight into
 * the client's memory.
 *
 * <p>A foreground service, because the panel is backgrounded the moment Free
 * Fire comes up and a plain background service would be reaped.
 */
public final class FeedService extends Service {

    public static final String CHANNEL_ID = "feed";
    private static final int NOTIFICATION_ID = 0x66656564;

    private static volatile FeedService live;

    private volatile FeedServer server;
    private volatile FeedChannel channel;
    private volatile String state = "idle";
    private Thread worker;

    public static boolean push(String json) {
        FeedService s = live;
        if (s == null || s.channel == null) {
            return false;
        }
        try {
            s.channel.send(json);
            return true;
        } catch (IOException e) {
            s.state = "send failed: " + e.getMessage();
            return false;
        }
    }

    public static String status() {
        FeedService s = live;
        if (s == null) {
            return "feed: not running";
        }
        int port = s.server == null ? 0 : s.server.port();
        return "feed: " + s.state + (port != 0 ? " on 127.0.0.1:" + port : "");
    }

    /** True once a client has taken the container and the channel is live. */
    public static boolean connected() {
        FeedService s = live;
        return s != null && s.channel != null;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        live = this;
        startForeground(NOTIFICATION_ID, notification());
        if (worker == null) {
            worker = new Thread(this::run, "feed");
            worker.setDaemon(true);
            worker.start();
        }
        return START_NOT_STICKY;
    }

    private Notification notification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                    new NotificationChannel(CHANNEL_ID, "feed",
                            NotificationManager.IMPORTANCE_LOW));
        }
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("feed")
                .setContentText(state)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .build();
    }

    private void run() {
        try {
            byte[] container = asset("container.bin");
            JSONObject man = new JSONObject(new String(asset("manifest.json"), "UTF-8"));
            String key = man.optString("key", "");
            int port = man.optInt("port", 0);
            if (container.length == 0 || key.length() != 64 || port == 0) {
                state = "no feed built in (assets/container.bin + manifest.json)";
                return;
            }

            byte[] master = hex(key);
            server = FeedServer.start(container, master, port);
            state = "waiting for the client";

            Socket sock = server.serveAndReturn(120000);
            if (sock == null) {
                state = "client did not connect";
                return;
            }
            channel = FeedChannel.attach(sock, master);
            state = "delivered";

            // Keep reading so a dead client is noticed and acks are consumed.
            while (true) {
                String line = channel.receive(60000);
                if (line == null) {
                    continue;
                }
                state = "client: " + (line.length() > 40 ? line.substring(0, 40) : line);
            }
        } catch (IOException e) {
            state = "stopped: " + e.getMessage();
        } catch (Exception e) {
            state = "stopped: " + e.getClass().getSimpleName();
        }
    }

    private byte[] asset(String name) throws IOException {
        try (InputStream in = getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            return out.toByteArray();
        } catch (IOException e) {
            return new byte[0];
        }
    }

    private static byte[] hex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    @Override
    public void onDestroy() {
        if (channel != null) {
            channel.close();
        }
        if (server != null) {
            server.close();
        }
        live = null;
        super.onDestroy();
    }
}