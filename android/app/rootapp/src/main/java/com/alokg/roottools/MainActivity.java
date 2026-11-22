package com.alokg.roottools;

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
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * One screen: what this phone is, whether it can be unlocked, and what the
 * root side looks like once it is.
 *
 * <p>It does not root anything. Nothing can - bootloader unlocking happens
 * before Android boots, from a computer, and no installed app can reach that.
 * What this screen removes is the other half of the problem: the part where
 * the buyer has to know which of thirty menus to open, whether their model is
 * even eligible, and what to type on a computer.
 *
 * <p>Everything it prints is read from the phone or from `su`. When something
 * is unknown it says unknown.
 */
public class MainActivity extends Activity {

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final StringBuilder log = new StringBuilder();

    private TextView out;
    private Button check, guide, root, denylist;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);

        int pad = dp(12);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setBackgroundColor(Color.parseColor("#0d1117"));
        column.setPadding(pad, pad, pad, pad);

        ScrollView scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        scroller.setBackgroundColor(Color.parseColor("#0d1117"));

        TextView title = new TextView(this);
        title.setText("root tools");
        title.setTextColor(Color.parseColor("#7ee787"));
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(18);
        column.addView(title);

        out = new TextView(this);
        out.setTextColor(Color.parseColor("#c9d1d9"));
        out.setTypeface(Typeface.MONOSPACE);
        out.setTextSize(11);
        out.setMovementMethod(new ScrollingMovementMethod());
        out.setTextIsSelectable(true);
        out.setMinimumHeight(dp(140));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        lp.topMargin = pad;
        column.addView(out, lp);

        check = new Button(this);
        check.setText("PERIKSA");
        check.setOnClickListener(v -> doCheck());

        guide = new Button(this);
        guide.setText("PANDUAN");
        guide.setOnClickListener(v -> doGuide());

        root = new Button(this);
        root.setText("CEK ROOT");
        root.setOnClickListener(v -> doRoot());

        denylist = new Button(this);
        denylist.setText("DENYLIST");
        denylist.setOnClickListener(v -> doDenylist());

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.addView(check, cell());
        row.addView(guide, cell());
        column.addView(row, spaced(dp(6)));

        LinearLayout row2 = new LinearLayout(this);
        row2.setOrientation(LinearLayout.HORIZONTAL);
        row2.addView(root, cell());
        row2.addView(denylist, cell());
        column.addView(row2, spaced(dp(6)));

        for (Button btn : new Button[]{check, guide, root, denylist}) {
            btn.setTextSize(12);
            btn.setMinWidth(0);
            btn.setMinimumWidth(0);
            btn.setPadding(dp(4), dp(2), dp(4), dp(2));
        }

        TextView hint = new TextView(this);
        hint.setText("PERIKSA dulu, lalu PANDUAN. App ini tidak me-root apa pun.");
        hint.setTextColor(Color.parseColor("#8b949e"));
        hint.setTypeface(Typeface.MONOSPACE);
        hint.setTextSize(10);
        hint.setPadding(dp(2), dp(8), dp(2), 0);
        column.addView(hint);

        scroller.addView(column);
        setContentView(scroller);

        say("app ini tidak menyentuh HP sampai lo tekan tombol.");
        doCheck();
    }

    // ---- actions -----------------------------------------------------------

    private void doCheck() {
        io.execute(() -> {
            Device d = Device.read();
            say("=== HP INI ===");
            for (String line : d.report().split("\n")) {
                say("  " + line);
            }
            say("");
            say(">>> " + d.verdict());
            say("");
            if (d.bootloaderUnlocked()) {
                say("tekan CEK ROOT untuk melihat apakah Magisk sudah terpasang.");
            } else {
                say("tekan PANDUAN untuk langkah merek ini ("
                        + d.manufacturer.toLowerCase() + ").");
            }
        });
    }

    private void doGuide() {
        io.execute(() -> {
            Device d = Device.read();
            say("=== PANDUAN: " + (d.manufacturer.isEmpty() ? "merek tidak terbaca"
                    : d.manufacturer) + " ===");
            for (String line : Guide.forBrand(d.brand()).split("\n")) {
                say("  " + line);
            }
            for (String line : Guide.afterRoot().split("\n")) {
                say("  " + line);
            }
        });
    }

    private void doRoot() {
        io.execute(() -> {
            say("=== ROOT ===");
            Root.Result id = Root.su("id");
            say("  su -c id  -> " + id.summary());
            if (!id.ok() || !id.out.contains("uid=0")) {
                say("  belum ada root. Kalau superuser bertanya, jawab izinkan,");
                say("  lalu tekan CEK ROOT lagi.");
                return;
            }
            say("  root: AKTIF (uid=0)");
            Root.Result ver = Root.su("magisk -V");
            say("  magisk -V        -> " + ver.summary());
            Root.Result den = Root.su("magisk --denylist ls");
            String s = den.out.trim();
            say("  denylist         -> " + (s.isEmpty() ? "(kosong)" : s.replace('\n', ' ')));
        });
    }

    private void doDenylist() {
        io.execute(() -> {
            say("=== DENYLIST ===");
            if (!Root.available()) {
                say("  butuh root dulu - tekan CEK ROOT");
                return;
            }
            Root.Result add = Root.su("magisk --denylist add com.dts.freefireth");
            say("  tambah game -> " + add.summary());
            Root.Result ls = Root.su("magisk --denylist ls");
            boolean there = ls.out.contains("com.dts.freefireth");
            say("  terdaftar   -> " + (there ? "ya" : "TIDAK"));
            if (!there) {
                say("  kalau gagal: Zygisk harus ON di Magisk -> Settings, dan");
                say("  Magisk harus versi 24 ke atas (perintah --denylist).");
            }
        });
    }

    // ---- plumbing ----------------------------------------------------------

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
    protected void onDestroy() {
        super.onDestroy();
        io.shutdown();
    }
}
