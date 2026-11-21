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
 * The buyer's screen, and nothing else: a key, an install, a panel.
 *
 * <p>The earlier build also carried the research harness - a root daemon, a
 * UDP config listener, a screen-capture aim path and an accessibility service.
 * None of it was reachable from the product, but all of it was declared, and
 * that is what an installer's scanner reads. Accessibility plus screen capture
 * is the exact pair that makes a sideloaded APK look like malware, so both are
 * gone. What is left asks for two permissions: draw over other apps, and the
 * network (a loopback socket needs it).
 *
 * <p>The flow is three taps and survives a restart:
 *
 * <pre>
 *   paste key  ->  key is written to files/key and verified offline
 *              ->  module missing? the install starts by itself
 *              ->  open Free Fire, press MIRROR
 * </pre>
 *
 * <p>Nothing here reaches the network except 127.0.0.1:7755, where the module
 * answers. No account, no server, no telemetry.
 */
public class MainActivity extends Activity {

    /** Where the key is kept between launches. */
    private static final String KEY_FILE = "key";
    private static final int REQ_TREE = 0x52;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private TextView out;
    private EditText keyIn;
    private Button activate, install, mirror;
    /** Install mode: true appends the random tail, false writes the reference bytes. */
    private boolean installTail = true;

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
        mirror.setOnClickListener(v -> openMirror());

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
        for (Button btn : new Button[]{install, mirror, activate}) {
            btn.setTextSize(12);
            btn.setMinWidth(0);
            btn.setMinimumWidth(0);
            btn.setPadding(dp(4), dp(2), dp(4), dp(2));
        }

        TextView hint = new TextView(this);
        hint.setText("1) tempel key   2) INSTALL   3) buka game, tekan MIRROR");
        hint.setTextColor(Color.parseColor("#8b949e"));
        hint.setTypeface(Typeface.MONOSPACE);
        hint.setTextSize(10);
        hint.setPadding(dp(2), dp(6), dp(2), 0);
        column.addView(hint);

        scroller.addView(column);
        setContentView(scroller);

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
        say("modul: " + (Module.present()
                ? "terpasang (" + Module.installedSize() + " byte)"
                : "belum terpasang"));
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
        if (!Module.present()) {
            say("modul belum ada - memasang sekarang");
            installModule(true);
        }
    }

    private String savedKey() {
        File f = new File(getFilesDir(), KEY_FILE);
        if (!f.isFile()) {
            return "";
        }
        try {
            FileInputStream in = new FileInputStream(f);
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0) {
                bos.write(buf, 0, n);
            }
            in.close();
            return new String(bos.toByteArray(), "UTF-8").trim();
        } catch (IOException e) {
            return "";
        }
    }

    private void saveKey(String key) {
        try {
            FileOutputStream f = new FileOutputStream(new File(getFilesDir(), KEY_FILE));
            f.write(key.getBytes("UTF-8"));
            f.flush();
            f.close();
        } catch (IOException e) {
            say("key: tidak bisa disimpan (" + e.getMessage() + ")");
        }
    }

    // ---- install -----------------------------------------------------------

    /**
     * Two ways in, tried in the order that asks least of the user: a plain
     * write first, and only when Android refuses, the system folder picker.
     */
    private void installModule(boolean withTail) {
        installTail = withTail;
        if (!withTail) {
            say("INSTALL: mode tanpa tail (byte referensi)");
        }
        // Say which game and which folder, so a test cannot be run against the
        // wrong target: Free Fire MAX (com.dts.freefiremax) has its own folder
        // and this module does not belong in it.
        say("game: " + Module.GAME + "  folder "
                + (new File(Module.dir()).isDirectory() ? "ada" : "BELUM ada")
                + ": " + Module.dir());
        install.setEnabled(false);
        io.execute(() -> {
            try {
                say(Module.installDirect(this, installTail));
                installed();
            } catch (Exception e) {
                say("direct: " + e.getMessage());
                say("direct ditolak - pilih folder lewat picker");
                ui.post(this::pickTree);
            } finally {
                ui.post(() -> install.setEnabled(true));
            }
        });
    }

    private void installed() {
        say("selesai. buka Free Fire, lalu tekan MIRROR.");
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
            }
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        io.shutdown();
    }
}
