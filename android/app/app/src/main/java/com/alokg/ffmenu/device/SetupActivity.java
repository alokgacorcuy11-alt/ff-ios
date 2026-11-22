package com.alokg.ffmenu.device;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/**
 * The whole setup, on one screen, for a buyer who will not read a README.
 *
 * <p>It exists because "works on every brand" is a lie without it: the app is
 * identical everywhere, but MIUI, ColorOS, Funtouch, One UI and EMUI each ship
 * a different switch that decides whether a background service survives, and
 * every one of them defaults to killing it. The buyer cannot be asked to find
 * five screens in five different ROMs. This screen finds them.
 *
 * <p>Each row is one step: what it is, why it matters, and a button that opens
 * the right panel. The tick re-reads the real state when the screen comes back
 * to the front, so it is a measurement, not a checkbox the user can lie to.
 */
public class SetupActivity extends Activity {

    private static final int BG = 0xFF0D1117;
    private static final int FG = 0xFFC9D1D9;
    private static final int DIM = 0xFF8B949E;
    private static final int OK = 0xFF7EE787;
    private static final int WARN = 0xFFD29922;

    private final List<Row> rows = new ArrayList<>();
    private DeviceProfile device;
    private TextView summary;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);

        device = DeviceProfile.read(this);

        int pad = dp(12);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setBackgroundColor(BG);
        column.setPadding(pad, pad, pad, pad);

        ScrollView scroller = new ScrollView(this);
        scroller.setFillViewport(true);
        scroller.setBackgroundColor(BG);

        TextView title = new TextView(this);
        title.setText("setup otomatis");
        title.setTextColor(OK);
        title.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        title.setTextSize(18);
        column.addView(title);

        TextView phone = new TextView(this);
        phone.setText(device.report());
        phone.setTextColor(DIM);
        phone.setTypeface(Typeface.MONOSPACE);
        phone.setTextSize(10);
        phone.setPadding(0, dp(6), 0, dp(10));
        column.addView(phone);

        summary = new TextView(this);
        summary.setTextColor(FG);
        summary.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        summary.setTextSize(12);
        summary.setPadding(0, 0, 0, dp(8));
        column.addView(summary);

        List<Plan.Step> steps = Plan.forDevice(device.brand, device.api);
        for (Plan.Step step : steps) {
            column.addView(rowView(step), spaced(dp(10)));
        }

        Button done = new Button(this);
        done.setText("SELESAI");
        done.setTextSize(12);
        done.setOnClickListener(v -> finish());
        column.addView(done, spaced(dp(14)));

        TextView foot = new TextView(this);
        foot.setText("Yang bertanda [ok] sudah benar-benar aktif - dibaca dari "
                + "sistem, bukan dari tombol. [?] artinya Android tidak memberi "
                + "cara memeriksanya, jadi pastikan sendiri.");
        foot.setTextColor(DIM);
        foot.setTypeface(Typeface.MONOSPACE);
        foot.setTextSize(10);
        foot.setPadding(0, dp(8), 0, 0);
        column.addView(foot);

        scroller.addView(column);
        setContentView(scroller);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    private View rowView(Plan.Step step) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.HORIZONTAL);

        TextView mark = new TextView(this);
        mark.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        mark.setTextSize(12);
        mark.setPadding(0, dp(3), dp(8), 0);
        mark.setMinWidth(dp(44));
        box.addView(mark);

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);

        TextView name = new TextView(this);
        name.setText(step.title + (step.optional ? "  (opsional)" : ""));
        name.setTextColor(FG);
        name.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        name.setTextSize(12);
        text.addView(name);

        TextView detail = new TextView(this);
        detail.setText(step.detail);
        detail.setTextColor(DIM);
        detail.setTypeface(Typeface.MONOSPACE);
        detail.setTextSize(10);
        detail.setPadding(0, dp(2), 0, 0);
        text.addView(detail);

        box.addView(text, new LinearLayout.LayoutParams(0,
                LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        Button open = new Button(this);
        open.setText("BUKA");
        open.setTextSize(11);
        open.setMinWidth(0);
        open.setMinimumWidth(0);
        open.setPadding(dp(6), dp(2), dp(6), dp(2));
        open.setOnClickListener(v -> {
            // Nothing to explain, nothing to confirm: the screen it opens is the
            // explanation. The tick is re-read on the way back.
            String said = Setup.open(this, step.id, device.brand);
            summary.setText(said);
        });
        box.addView(open, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        Row row = new Row(step, mark, open);
        rows.add(row);
        return box;
    }

    private void refresh() {
        int requiredLeft = 0;
        int optionalLeft = 0;

        for (Row row : rows) {
            int s = Setup.state(this, row.step.id);
            if (s == Setup.DONE) {
                row.mark.setText("[ok]");
                row.mark.setTextColor(OK);
                row.open.setText("UBAH");
            } else if (s == Setup.UNKNOWN) {
                row.mark.setText("[?]");
                row.mark.setTextColor(WARN);
                row.open.setText("BUKA");
                if (!row.step.optional) {
                    requiredLeft++;
                }
            } else {
                row.mark.setText("[  ]");
                row.mark.setTextColor(FG);
                row.open.setText("BUKA");
                if (row.step.optional) {
                    optionalLeft++;
                } else {
                    requiredLeft++;
                }
            }
        }

        if (requiredLeft == 0) {
            summary.setText("SIAP - semua izin wajib sudah aktif. Buka game, lalu "
                    + "tekan ESP.");
            summary.setTextColor(OK);
        } else {
            summary.setText(requiredLeft + " langkah wajib belum beres"
                    + (optionalLeft > 0 ? ", " + optionalLeft + " opsional" : ""));
            summary.setTextColor(FG);
        }
    }

    private static final class Row {
        final Plan.Step step;
        final TextView mark;
        final Button open;

        Row(Plan.Step step, TextView mark, Button open) {
            this.step = step;
            this.mark = mark;
            this.open = open;
        }
    }

    private LinearLayout.LayoutParams spaced(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = top;
        return lp;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
