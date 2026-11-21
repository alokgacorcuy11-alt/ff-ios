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

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * One screen, and it owns the whole thing.
 *
 * <pre>
 *   tempel key   ->  satu kali, disimpan
 *   AKTIFKAN     ->  modul dipasang
 *   buka game    ->  MIRROR, setelan yang dulu tersimpan dipasang sendiri
 *   MATIKAN / tutup app  ->  modul dihapus, izin folder dilepas
 * </pre>
 *
 * <p>The last line is the point of this screen. What the app changes on the
 * phone is exactly one file in the game's folder, so "off" is not a mode, a
 * system setting or a background process - it is that file being deleted,
 * verified deleted, and the one folder permission the app had been holding
 * handed back to the system. Nothing else was altered, so nothing else has to
 * be undone.
 *
 * <p>Auto-clean is on by default and there are two triggers: leaving the app,
 * and starting it. Starting as well, because a process can be killed without
 * ever getting the chance to tidy up - and a phone that is clean at the start
 * of every session is the same promise kept, whichever way the last session
 * ended.
 */
public class MainActivity extends Activity {

    /** Where the key is kept between launches. */
    private static final String KEY_FILE = "key";
    /** Whether closing the app should put the phone back as it was. */
    private static final String CLEAN_FILE = "auto-bersih";
    private static final int REQ_TREE = 0x52;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private TextView out;
    private EditText keyIn;
    private Button activate, toggle, mirror, autoclean, diag;
    /** Install mode: true appends the random tail, false writes the reference bytes. */
    private boolean installTail = true;
    /** Put the phone back as it was when the app is left. */
    private boolean autoClean = true;

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
        // fillViewport keeps the log elastic when there IS room.
        ScrollView scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        scroller.setBackgroundColor(Color.parseColor("#0d1117"));

        TextView title = new TextView(this);
        title.setText("toolbox");
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

        keyIn = new EditText(this);
        keyIn.setHint("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX");
        keyIn.setTypeface(Typeface.MONOSPACE);
        keyIn.setTextSize(12);
        keyIn.setTextColor(Color.parseColor("#c9d1d9"));

        activate = new Button(this);
        activate.setText("KEY");
        activate.setOnClickListener(v -> activateKey(keyIn.getText().toString().trim(), true));

        LinearLayout keyRow = new LinearLayout(this);
        keyRow.setOrientation(LinearLayout.HORIZONTAL);
        keyRow.addView(keyIn, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        keyRow.addView(activate, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        toggle = new Button(this);
        toggle.setText("AKTIFKAN");
        toggle.setOnClickListener(v -> toggleModule());
        // Long-press is the panic button: everything this app ever wrote goes,
        // including the key, so a phone can be handed over without a trace.
        toggle.setOnLongClickListener(v -> {
            wipe();
            return true;
        });

        mirror = new Button(this);
        mirror.setText("MIRROR");
        mirror.setOnClickListener(v -> openMirror());

        autoclean = new Button(this);
        autoclean.setOnClickListener(v -> setAutoClean(!autoClean));

        diag = new Button(this);
        diag.setText("LOG");
        diag.setOnClickListener(v -> showGameLog());

        // ---- the key, then the two buttons a buyer actually needs -----------
        LinearLayout.LayoutParams kr = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        kr.topMargin = pad;
        column.addView(keyRow, kr);

        LinearLayout primary = new LinearLayout(this);
        primary.setOrientation(LinearLayout.HORIZONTAL);
        primary.addView(toggle, cell());
        primary.addView(mirror, cell());
        column.addView(primary, spaced(dp(6)));

        LinearLayout row2 = new LinearLayout(this);
        row2.setOrientation(LinearLayout.HORIZONTAL);
        row2.addView(autoclean, cell());
        row2.addView(diag, cell());
        column.addView(row2, spaced(dp(6)));

        // One place to style every control. The material button style insists
        // on an ~88dp minimum width; five of those do not fit across a phone, so
        // labels came out as "CONNEC..." instead of being readable.
        for (Button btn : new Button[]{toggle, mirror, activate, autoclean, diag}) {
            btn.setTextSize(12);
            btn.setMinWidth(0);
            btn.setMinimumWidth(0);
            btn.setPadding(dp(4), dp(2), dp(4), dp(2));
        }

        TextView hint = new TextView(this);
        hint.setText("1) tempel key   2) AKTIFKAN   3) buka game, tekan MIRROR");
        hint.setTextColor(Color.parseColor("#8b949e"));
        hint.setTypeface(Typeface.MONOSPACE);
        hint.setTextSize(10);
        hint.setPadding(dp(2), dp(8), dp(2), 0);
        column.addView(hint);

        scroller.addView(column);
        setContentView(scroller);

        autoClean = !"0".equals(readOwn(CLEAN_FILE));
        paintAutoClean();

        // A key already on the phone is checked again on every launch, not
        // trusted: the expiry moves with the calendar, and a key that was valid
        // last month has to stop being valid without the user doing anything.
        String saved = savedKey();
        if (!saved.isEmpty()) {
            keyIn.setText(saved);
            activateKey(saved, false);
        } else {
            say("belum ada key - tempel key yang kamu beli, lalu tekan KEY");
        }

        if (autoClean && Module.exists(this)) {
            say("auto-bersih: sisa sesi sebelumnya dihapus");
            removeModule(false);
        } else {
            refreshState();
        }
    }

    // ---- state -------------------------------------------------------------

    /** Says, on the screen, what is actually on the phone right now. */
    private void refreshState() {
        io.execute(() -> {
            boolean there = Module.exists(this);
            long size = Module.size(this);
            ui.post(() -> {
                toggle.setText(there ? "MATIKAN" : "AKTIFKAN");
                if (there) {
                    say("modul: AKTIF" + (size > 0 ? " (" + size + " byte)" : ""));
                } else {
                    say("modul: BERSIH - tidak ada yang tertinggal di HP");
                }
            });
        });
    }

    // ---- key ---------------------------------------------------------------

    /**
     * @param announce print the whole status; a silent restore only prints when
     *     something is wrong, so a normal launch is two lines instead of five.
     */
    private void activateKey(String key, boolean announce) {
        if (key.isEmpty()) {
            say("key: kosong");
            return;
        }
        String device = android.provider.Settings.Secure.getString(
                getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
        License.Status s = License.check(key, device == null ? "" : device);
        if (announce || !s.ok) {
            say(s.toString());
        }
        if (!s.ok) {
            return;
        }
        saveKey(key);
        say("key: aktif dan tersimpan - tidak perlu tempel lagi");
        MirrorPanel.refresh();
        // The one step a buyer forgets. If the module is not on the phone yet,
        // the install starts on its own instead of waiting to be asked.
        if (!Module.exists(this)) {
            say("modul belum ada - memasang sekarang");
            installModule(true);
        }
    }

    private String savedKey() {
        return readOwn(KEY_FILE);
    }

    private void saveKey(String key) {
        writeOwn(KEY_FILE, key);
    }

    // ---- install / remove --------------------------------------------------

    private void toggleModule() {
        if (!License.unlocked()) {
            say("masukkan key dulu");
            return;
        }
        io.execute(() -> {
            if (Module.exists(this)) {
                removeModule(true);
            } else {
                installModule(true);
            }
        });
    }

    /**
     * Two ways in, tried in the order that asks least of the user: a plain
     * write first, and only when Android refuses, the system folder picker -
     * once, because the grant is written down.
     */
    private void installModule(boolean withTail) {
        installTail = withTail;
        if (!withTail) {
            say("INSTALL: mode tanpa tail (byte referensi)");
        }
        say("game: " + Module.GAME + ": " + Module.dir());
        toggle.setEnabled(false);
        io.execute(() -> {
            try {
                say(Module.installDirect(this, installTail));
                installed();
            } catch (Exception e) {
                say("direct: " + e.getMessage());
                say("direct ditolak - pilih folder lewat picker");
                ui.post(this::pickTree);
            } finally {
                ui.post(() -> {
                    toggle.setEnabled(true);
                    refreshState();
                });
            }
        });
    }

    private void installed() {
        say("selesai. buka Free Fire, lalu tekan MIRROR.");
    }

    /**
     * @param announce say it out loud, or stay quiet during the automatic clean
     *     at startup that the buyer did not ask for.
     */
    private void removeModule(boolean announce) {
        try {
            String r = Module.remove(this);
            if (announce) {
                say(r);
                say("GPS/izin folder dilepas - HP kembali seperti semula");
            }
        } catch (Exception e) {
            say("gagal menghapus: " + e.getMessage());
        } finally {
            ui.post(() -> {
                refreshState();
            });
        }
    }

    /** Everything this app ever wrote, gone. For handing the phone to someone. */
    private void wipe() {
        say("bersih total...");
        io.execute(() -> {
            try {
                say(Module.remove(this));
            } catch (Exception e) {
                say("modul: " + e.getMessage());
            }
            Module.releaseTree(this);
            //noinspection ResultOfMethodCallIgnored
            new File(getFilesDir(), KEY_FILE).delete();
            //noinspection ResultOfMethodCallIgnored
            new File(getFilesDir(), "panel").delete();
            ui.post(() -> {
                keyIn.setText("");
                say("key dan setelan dihapus. HP bersih.");
                refreshState();
            });
        });
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

    // ---- panel -------------------------------------------------------------

    private void openMirror() {
        if (!License.unlocked()) {
            say("mirror: masukkan key dulu");
            return;
        }
        if (!Module.exists(this)) {
            say("mirror: modul belum terpasang - tekan AKTIFKAN dulu");
            return;
        }
        if (MirrorPanel.needsPermission(this)) {
            say("mirror: izin overlay belum ada - buka izinnya, lalu tekan MIRROR lagi");
            startActivity(new Intent(
                    android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION));
            return;
        }
        io.execute(() -> say(Bridge.verdict(true, Module.size(this), new Bridge().alive())));
        MirrorPanel.toggle(this);
        say(MirrorPanel.visible() ? "mirror: tampil" : "mirror: disembunyikan");
    }

    // ---- the game's own log ------------------------------------------------

    /**
     * Reads the game's folder and shows the game's own words about the patch.
     *
     * <p>Free Fire carries a code-patch loader whose log line is
     * {@code CodePath load failed!} - and it writes that, and everything else
     * it decided, into its own directory. Reading it beats guessing at paths:
     * either the file is there and the loader refused it, or it is not there.
     */
    private void showGameLog() {
        say("--- isi folder game ---");
        io.execute(() -> {
            java.util.List<Module.Entry> entries;
            try {
                entries = Module.list(this);
            } catch (Exception e) {
                say("folder: " + e.getMessage());
                return;
            }
            say("folder: " + entries.size() + " entri");
            java.util.List<String> logs = new java.util.ArrayList<>();
            int shown = 0;
            for (Module.Entry e : entries) {
                if (shown < 20) {
                    say("  " + e);
                    shown++;
                }
                String lower = e.name.toLowerCase(java.util.Locale.US);
                if (!e.dir && (lower.endsWith(".log") || lower.contains("debug")
                        || lower.contains("patch") || lower.contains("hotfix"))) {
                    logs.add(e.name);
                }
            }
            if (entries.size() > shown) {
                say("  ... dan " + (entries.size() - shown) + " entri lain");
            }
            if (logs.isEmpty()) {
                say("tidak ada berkas .log / patch di folder itu");
                return;
            }
            int read = 0;
            for (String name : logs) {
                if (read >= 3) {
                    break;
                }
                read++;
                say("--- " + name);
                String text;
                try {
                    text = Module.readChild(this, name, 200000);
                } catch (Exception e) {
                    say("  gagal dibaca: " + e.getMessage());
                    continue;
                }
                String[] lines = text.split("\n");
                int printed = 0;
                for (int i = lines.length - 1; i >= 0 && printed < 12; i--) {
                    String line = lines[i].trim();
                    if (line.isEmpty()) {
                        continue;
                    }
                    say("  " + cut(line, 100));
                    printed++;
                }
                if (printed == 0) {
                    say("  (kosong)");
                }
            }
            say("--- akhir log. Salin layar ini dan kirim ke admin. ---");
        });
    }

    private static String cut(String s, int max) {
        String t = s.replace('\t', ' ');
        return t.length() <= max ? t : t.substring(0, max) + "...";
    }

    // ---- auto clean --------------------------------------------------------

    private void setAutoClean(boolean on) {
        autoClean = on;
        writeOwn(CLEAN_FILE, on ? "1" : "0");
        paintAutoClean();
        say(on ? "auto-bersih: ON - app ditutup, modul dihapus"
                : "auto-bersih: OFF - modul tetap terpasang sampai MATIKAN");
    }

    private void paintAutoClean() {
        autoclean.setText(autoClean ? "auto-bersih: ON" : "auto-bersih: OFF");
        autoclean.setTextColor(autoClean ? Color.parseColor("#7ee787")
                : Color.parseColor("#8b949e"));
    }

    // ---- own files ---------------------------------------------------------

    /** The app's own storage - no permission needed, and gone on uninstall. */
    private String readOwn(String name) {
        File f = new File(getFilesDir(), name);
        if (!f.isFile()) {
            return "";
        }
        try (FileInputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0) {
                bos.write(buf, 0, n);
            }
            return new String(bos.toByteArray(), "UTF-8").trim();
        } catch (IOException e) {
            return "";
        }
    }

    private void writeOwn(String name, String value) {
        try (FileOutputStream f = new FileOutputStream(new File(getFilesDir(), name))) {
            f.write(value.getBytes("UTF-8"));
            f.flush();
        } catch (IOException e) {
            say(name + ": tidak bisa disimpan (" + e.getMessage() + ")");
        }
    }

    // ---- layout helpers ----------------------------------------------------

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

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != REQ_TREE) {
            return;
        }
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
                installed();
            } catch (Exception e) {
                say("picker: " + e.getMessage());
            } finally {
                ui.post(this::refreshState);
            }
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // isFinishing: a rebuild or a low-memory teardown is not the user
        // leaving, and must not silently remove the module.
        if (autoClean && isFinishing() && Module.exists(this)) {
            try {
                Module.remove(this);
            } catch (Exception ignored) {
                // the next launch cleans up instead; that is why it cleans there too
            }
        }
        io.shutdown();
    }
}
