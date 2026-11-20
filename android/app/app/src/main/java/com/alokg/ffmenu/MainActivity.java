package com.alokg.ffmenu;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Stage 1: prove the link end to end.
 *
 *   root -> daemon -> socket -> Free Fire's pid -> libil2cpp.so base -> read
 *   the ELF header out of the game's own memory.
 *
 * If the last line says "ELF ok", everything after this (offsets, ESP, aim) is
 * ordinary work on top of a working channel. If it does not, the failure is in
 * one of four places and each one prints its own line, so there is nothing to
 * guess.
 */
public class MainActivity extends Activity {

    private static final String GAME = "com.dts.freefireth";
    private static final String GAME_MAX = "com.dts.freefiremax";
    private static final String DAEMON_TMP = "/data/local/tmp/.ffmemd";
    private static final int REQ_CAPTURE = 0x51;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private final Config cfg = Config.SHARED;
    private LanConfig lan;

    private TextView out;
    private Button connect, stop, feed, panel, vision;
    private EditText keyIn;
    private Button activate;
    private MemClient client;
    private volatile int gamePid;
    private volatile int daemonPid;
    private volatile long lastCfgSay;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#0d1117"));
        int pad = dp(14);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("FF · mem bridge");
        title.setTextColor(Color.parseColor("#7ee787"));
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(18);
        root.addView(title);

        out = new TextView(this);
        out.setTextColor(Color.parseColor("#c9d1d9"));
        out.setTypeface(Typeface.MONOSPACE);
        out.setTextSize(12);
        out.setMovementMethod(new ScrollingMovementMethod());
        out.setTextIsSelectable(true);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        lp.topMargin = pad;
        root.addView(out, lp);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        connect = new Button(this);
        connect.setText("CONNECT");
        connect.setOnClickListener(v -> start());

        stop = new Button(this);
        stop.setText("STOP");
        stop.setOnClickListener(v -> stopAll());

        feed = new Button(this);
        feed.setText("FEED");
        feed.setOnClickListener(v -> {
            startService(new Intent(this, FeedService.class));
            say(FeedService.status());
        });

        panel = new Button(this);
        panel.setText("PANEL");
        panel.setOnClickListener(v -> {
            if (!android.provider.Settings.canDrawOverlays(this)) {
                say("panel: izin overlay belum ada - buka izinnya, lalu tekan PANEL lagi");
                startActivity(new Intent(
                        android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION));
                return;
            }
            Panel.toggle(this, cfg);
            say(Panel.visible() ? "panel: tampil" : "panel: disembunyikan");
        });

        vision = new Button(this);
        vision.setText("VISION");
        vision.setOnClickListener(v -> {
            if (Vision.running()) {
                stopService(new Intent(this, VisionService.class));
                Vision.stop();
                say(Vision.status());
                return;
            }
            android.media.projection.MediaProjectionManager mpm =
                    (android.media.projection.MediaProjectionManager)
                            getSystemService(MEDIA_PROJECTION_SERVICE);
            if (mpm == null) {
                say("vision: service unavailable");
                return;
            }
            say("vision: minta izin capture...");
            startActivityForResult(mpm.createScreenCaptureIntent(), REQ_CAPTURE);
        });

        keyIn = new EditText(this);
        keyIn.setHint("XXXX-XXXX-XXXX-XXXX-XXXX");
        keyIn.setTypeface(Typeface.MONOSPACE);
        keyIn.setTextSize(12);
        keyIn.setTextColor(Color.parseColor("#c9d1d9"));

        activate = new Button(this);
        activate.setText("KEY");
        activate.setOnClickListener(v -> activateKey());

        LinearLayout keyRow = new LinearLayout(this);
        keyRow.setOrientation(LinearLayout.HORIZONTAL);
        keyRow.addView(keyIn, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        keyRow.addView(activate, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        LinearLayout.LayoutParams kr = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        kr.topMargin = pad;
        root.addView(keyRow, kr);

        row.addView(connect, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(stop, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(feed, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(panel, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(vision, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        root.addView(row);

        setContentView(root);
        say("root: checking...");
        io.execute(() -> {
            boolean r = Root.available();
            say("root: " + (r ? "ok (uid=0)" : "NOT granted"));
        });

        lan = new LanConfig(cfg, new LanConfig.Sink() {
            @Override
            public void onConfig(org.json.JSONObject o, int seq, int watches) {
                long now = System.currentTimeMillis();
                if (now - lastCfgSay < 1000) return;
                lastCfgSay = now;
                say("config: seq " + seq + ", esp " + (cfg.espBox ? "on" : "off")
                        + " aim " + (cfg.aimEnabled ? cfg.aimBone + "/" + cfg.aimSmooth : "off")
                        + ", watch " + watches);
            }

            @Override
            public void onStatus(String line) {
                say(line);
            }
        });
        lan.setMem((addr, len) -> {
            MemClient c = client;
            int pid = gamePid;
            if (c == null || pid == 0) throw new java.io.IOException("not connected");
            return c.read(pid, addr, len);
        });
        lan.start();
    }

    private void activateKey() {
        String device = android.provider.Settings.Secure.getString(
                getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
        String key = keyIn.getText().toString().trim();
        License.Status s = License.check(key, device == null ? "" : device);
        say(s.toString());
        if (s.ok) {
            say("device id: " + (device == null ? "-" : device));
        }
        Panel.refresh();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void say(String s) {
        ui.post(() -> {
            log.append(s).append('\n');
            out.setText(log.toString());
            if (out.getLineCount() > 0) {
                int scroll = out.getLayout().getLineTop(out.getLineCount()) - out.getHeight();
                out.scrollTo(0, Math.max(scroll, 0));
            }
        });
    }

    private void start() {
        connect.setEnabled(false);
        io.execute(() -> {
            try {
                run();
            } catch (Exception e) {
                say("FAIL: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            } finally {
                ui.post(() -> connect.setEnabled(true));
            }
        });
    }

    private void run() throws Exception {
        say("--- " + new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US)
                .format(new java.util.Date()));

        if (!Root.available()) {
            say("root: not available, stop here");
            return;
        }

        // 1. daemon on disk
        File local = new File(getFilesDir(), "memd");
        if (!local.exists() || local.length() == 0) {
            say("asset: extracting memd...");
            extract("memd", local);
        }
        say("asset: " + local.length() + " bytes");

        Root.Result cp = Root.run(
                "mkdir -p /data/local/tmp && "
                        + "cp '" + local.getAbsolutePath() + "' " + DAEMON_TMP + " && "
                        + "chmod 700 " + DAEMON_TMP + " && "
                        + "ls -l " + DAEMON_TMP, 10);
        say("install: " + (cp.ok() ? cp.out.replace('\n', ' ') : "FAILED " + cp.out));
        if (!cp.ok()) return;

        // The copy in the app's own storage is not needed any more: it is an
        // asset in the APK and can be re-extracted on demand. Deleting it now
        // means the only artefact on the device is the APK itself.
        //noinspection ResultOfMethodCallIgnored
        local.delete();

        // 2. a daemon from a previous run must go first: it owns the abstract
        // socket, so a new one would fail to bind and the app would silently
        // talk to the old build. Its cmdline is scrubbed, so the socket is the
        // only handle - ask it to exit through the socket.
        try {
            MemClient stale = new MemClient();
            int old = stale.ping();
            stale.shutdown();
            stale.close();
            say("old: daemon pid " + old + " asked to exit");
            Thread.sleep(250);
        } catch (IOException | InterruptedException ignored) {
            // nothing running - the normal case
        }

        Root.Result st = Root.startDetached(DAEMON_TMP, getApplicationInfo().uid);
        say("start: " + (st.ok() ? "spawned, binary unlinked (no path left)"
                : "FAILED " + st.out));

        // 3. connect, with a short retry: the socket appears a few ms later
        MemClient c = null;
        for (int i = 0; i < 20 && c == null; i++) {
            try {
                c = new MemClient();
            } catch (IOException e) {
                try { Thread.sleep(150); } catch (InterruptedException ignored) { }
            }
        }
        if (c == null) {
            say("socket: " + MemClient.SOCKET + " unreachable (daemon died?)");
            return;
        }
        client = c;
        daemonPid = c.ping();
        say("socket: connected (daemon pid " + daemonPid + ")");

        // 4. the game
        int pid = c.find(GAME);
        String which = GAME;
        if (pid == 0) {
            pid = c.find(GAME_MAX);
            which = GAME_MAX;
        }
        if (pid == 0) {
            say("game: not running (start Free Fire, then CONNECT again)");
            return;
        }
        say("game: " + which + " pid " + pid);
        gamePid = pid;

        // 5. the runtime, and a read from it
        long base = c.base(pid, "libil2cpp.so");
        if (base == 0) {
            say("il2cpp: module not mapped yet (still loading?)");
            return;
        }
        say("il2cpp: base 0x" + Long.toHexString(base));

        byte[] hdr = c.read(pid, base, 4);
        boolean elf = hdr.length == 4 && hdr[0] == 0x7f && hdr[1] == 'E'
                && hdr[2] == 'L' && hdr[3] == 'F';
        say("mem: " + (elf ? "ELF ok, read path works"
                : "unexpected header " + hex(hdr)));
        if (elf) say("ready: offsets next (see android/README.md)");
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x));
        return s.length() == 0 ? "<empty>" : s.toString();
    }

    private void extract(String asset, File dst) throws IOException {
        try (InputStream in = getAssets().open(asset);
             FileOutputStream fos = new FileOutputStream(dst)) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) fos.write(buf, 0, n);
        }
        //noinspection ResultOfMethodCallIgnored
        dst.setExecutable(true, false);
    }

    private void stopAll() {
        io.execute(() -> {
            MemClient c = client;
            if (c != null) {
                try {
                    c.shutdown();
                } catch (IOException ignored) { }
                c.close();
                client = null;
            }
            if (daemonPid != 0) Root.run("kill -9 " + daemonPid + " 2>/dev/null", 5);
            Root.run("rm -f " + DAEMON_TMP, 5);
            gamePid = 0;
            daemonPid = 0;
            say("stopped: daemon gone, no file left");
        });
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != REQ_CAPTURE) {
            return;
        }
        if (result != RESULT_OK || data == null) {
            say("vision: izin capture ditolak");
            return;
        }
        Intent svc = new Intent(this, VisionService.class);
        svc.putExtra(VisionService.EXTRA_RESULT_CODE, result);
        svc.putExtra(VisionService.EXTRA_DATA, data);
        startForegroundService(svc);
        say("vision: starting - " + Vision.status());
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (lan != null) lan.shutdown();
        if (client != null) client.close();
        io.shutdown();
    }
}
