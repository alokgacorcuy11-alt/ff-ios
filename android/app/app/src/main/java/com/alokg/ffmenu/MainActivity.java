package com.alokg.ffmenu;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
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

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private TextView out;
    private Button connect, stop;
    private MemClient client;

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

        row.addView(connect, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(stop, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        root.addView(row);

        setContentView(root);
        say("root: checking...");
        io.execute(() -> {
            boolean r = Root.available();
            say("root: " + (r ? "ok (uid=0)" : "NOT granted"));
        });
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

        // 2. restart it, so a rebuilt binary always wins over a running one
        Root.run("pkill -f .ffmemd 2>/dev/null", 5);
        Root.Result st = Root.startDetached(DAEMON_TMP, getApplicationInfo().uid);
        say("start: " + (st.ok() ? "spawned" : "FAILED " + st.out));

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
        c.ping();
        say("socket: connected");

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
            if (client != null) {
                client.close();
                client = null;
            }
            Root.run("pkill -f .ffmemd 2>/dev/null; rm -f " + DAEMON_TMP, 8);
            say("stopped: daemon killed, socket closed");
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (client != null) client.close();
        io.shutdown();
    }
}
