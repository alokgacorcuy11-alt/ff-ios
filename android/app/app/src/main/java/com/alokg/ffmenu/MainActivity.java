package com.alokg.ffmenu;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.alokg.ffmenu.mirror.Bridge;
import com.alokg.ffmenu.mirror.MirrorPanel;
import com.alokg.ffmenu.mirror.Module;

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
    private static final int REQ_TREE = 0x52;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private final Config cfg = Config.SHARED;
    private LanConfig lan;

    private TextView out;
    private Button connect, stop, feed, panel, vision, install, mirror;
    private EditText keyIn;
    private Button activate;
    /** Install mode: true appends the random tail, false writes the reference bytes. */
    private boolean installTail = true;
    /** The root-only tools, folded away so they cannot crowd out the two a buyer needs. */
    private LinearLayout rootTools;
    private Button rootToggle;
    private MemClient client;
    private volatile int gamePid;
    private volatile int daemonPid;
    private volatile long lastCfgSay;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);

        int pad = dp(12);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setBackgroundColor(Color.parseColor("#0d1117"));
        column.setPadding(pad, pad, pad, pad);

        // Everything lives inside a scrolling viewport. A fixed column pushed
        // its last row off the screen on a short phone, or as soon as the system
        // font was turned up - which is what "the buttons are cut off" was.
        // fillViewport keeps the log elastic when there IS room: it stretches
        // into the leftover space instead of leaving a gap above the controls.
        ScrollView scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        scroller.setBackgroundColor(Color.parseColor("#0d1117"));

        TextView title = new TextView(this);
        title.setText("FF · mem bridge");
        title.setTextColor(Color.parseColor("#7ee787"));
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(18);
        column.addView(title);

        out = new TextView(this);
        out.setTextColor(Color.parseColor("#c9d1d9"));
        out.setTypeface(Typeface.MONOSPACE);
        out.setTextSize(12);
        out.setMovementMethod(new ScrollingMovementMethod());
        out.setTextIsSelectable(true);
        // height 0 + weight 1: the log gives up its space before anything else
        // does, and scrolls inside itself rather than growing and pushing the
        // controls down. The minimum keeps it readable when space is tight.
        out.setMinimumHeight(dp(140));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        lp.topMargin = pad;
        column.addView(out, lp);

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

        install = new Button(this);
        install.setText("INSTALL");
        install.setOnClickListener(v -> installModule(true));
        // Long-press is the escape hatch: if the loader minds trailing bytes, the
        // exact bytes the reference build writes are one press away.
        install.setOnLongClickListener(v -> {
            installModule(false);
            return true;
        });

        mirror = new Button(this);
        mirror.setText("MIRROR");
        mirror.setOnClickListener(v -> {
            if (!License.unlocked()) {
                say("mirror: masukkan key dulu");
                return;
            }
            if (MirrorPanel.needsPermission(this)) {
                say("mirror: izin overlay belum ada - buka izinnya, lalu tekan MIRROR lagi");
                startActivity(new Intent(
                        android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION));
                return;
            }
            io.execute(() -> say(Bridge.verdict(Module.present(),
                    Module.installedSize(), new Bridge().alive())));
            MirrorPanel.toggle(this);
            say(MirrorPanel.visible() ? "mirror: tampil" : "mirror: disembunyikan");
        });

        keyIn = new EditText(this);
        keyIn.setHint("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX");
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

        // ---- the key, then the two buttons a buyer actually needs -----------
        LinearLayout.LayoutParams kr = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        kr.topMargin = pad;
        column.addView(keyRow, kr);

        LinearLayout primary = new LinearLayout(this);
        primary.setOrientation(LinearLayout.HORIZONTAL);
        primary.addView(install, cell());
        primary.addView(mirror, cell());
        column.addView(primary, spaced(dp(6)));

        // One place to style every control. The material button style insists
        // on an ~88dp minimum width; five of those do not fit across a phone, so
        // labels came out as "CONNEC..." instead of being readable.
        for (Button btn : new Button[]{connect, stop, feed, panel, vision,
                install, mirror, activate}) {
            btn.setTextSize(12);
            btn.setMinWidth(0);
            btn.setMinimumWidth(0);
            btn.setPadding(dp(4), dp(2), dp(4), dp(2));
        }

        // ---- the root-only tools, folded away ------------------------------
        row.addView(connect, cell());
        row.addView(stop, cell());
        row.addView(feed, cell());

        LinearLayout row2 = new LinearLayout(this);
        row2.setOrientation(LinearLayout.HORIZONTAL);
        row2.addView(panel, cell());
        row2.addView(vision, cell());

        rootTools = new LinearLayout(this);
        rootTools.setOrientation(LinearLayout.VERTICAL);
        rootTools.setVisibility(View.GONE);
        rootTools.addView(row);
        rootTools.addView(row2);

        rootToggle = new Button(this);
        rootToggle.setText("root tools  (butuh root)");
        rootToggle.setTextSize(11);
        rootToggle.setMinWidth(0);
        rootToggle.setMinimumWidth(0);
        rootToggle.setPadding(dp(4), dp(2), dp(4), dp(2));
        rootToggle.setOnClickListener(v -> toggleRootTools());
        column.addView(rootToggle, spaced(dp(6)));
        column.addView(rootTools);

        scroller.addView(column);
        setContentView(scroller);
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

    /**
     * Two ways in, tried in the order that asks least of the user: a plain
     * write first, and only when Android refuses, the system folder picker.
     */
    private void installModule(boolean withTail) {
        installTail = withTail;
        if (!withTail) {
            say("INSTALL: mode tanpa tail (byte referensi)");
        }
        install.setEnabled(false);
        io.execute(() -> {
            try {
                say(Module.installDirect(this, installTail));
            } catch (Exception e) {
                say("direct: " + e.getMessage());
                say("direct ditolak - pilih folder lewat picker");
                ui.post(this::pickTree);
            } finally {
                ui.post(() -> install.setEnabled(true));
            }
        });
    }

    // ---- layout helpers ---------------------------------------------------

    /**
     * One equal share of a row, with a small gutter.
     *
     * <p>minWidth 0 is set separately, on the buttons: the material style's
     * ~88dp floor is what turned "CONNECT" into "CONNEC..." in a five-across
     * row.
     */
    private LinearLayout.LayoutParams cell() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        lp.rightMargin = dp(4);
        return lp;
    }

    private LinearLayout.LayoutParams spaced(int topMargin) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = topMargin;
        return lp;
    }

    /** Shows or hides the root-only tools, and says which state it is in. */
    private void toggleRootTools() {
        boolean show = rootTools.getVisibility() != View.VISIBLE;
        rootTools.setVisibility(show ? View.VISIBLE : View.GONE);
        rootToggle.setText(show ? "root tools  (butuh root)  \u25b4"
                : "root tools  (butuh root)  \u25be");
    }

    private void pickTree() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            i.putExtra(DocumentsContract.EXTRA_INITIAL_URI,
                    DocumentsContract.buildDocumentUri(
                            "com.android.externalstorage.documents", Module.TREE_DOC));
        } catch (Exception ignored) {
            // older pickers ignore the hint and open at the root
        }
        try {
            startActivityForResult(i, REQ_TREE);
        } catch (Exception e) {
            say("picker: " + e.getMessage());
        }
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
        MirrorPanel.refresh();
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
        if (request == REQ_TREE) {
            if (result != RESULT_OK || data == null || data.getData() == null) {
                say("picker: dibatalkan");
                return;
            }
            Uri tree = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(tree,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION
                                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            } catch (Exception e) {
                say("picker: izin tidak bisa disimpan (" + e.getMessage() + ")");
            }
            say("picker: " + tree);
            io.execute(() -> {
                try {
                    say(Module.installTree(this, tree, installTail));
                } catch (Exception e) {
                    say("picker: " + e.getMessage());
                }
            });
            return;
        }
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
