package com.alokg.ffmenu.device;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.accessibility.AccessibilityManager;

import java.util.ArrayList;
import java.util.List;

/**
 * Reads the state of each setup step, and opens the screen that changes it.
 *
 * <p>Two rules, both learned the hard way:
 *
 * <p>1. Every vendor screen is tried by launching it, never by asking whether it
 * exists. {@code resolveActivity} answers null for packages the app cannot see
 * (Android 11 hides them) and for activities that are present but disabled -
 * both of which are common for these settings panels. Launching and catching
 * {@code ActivityNotFoundException} is the only answer that is always true.
 *
 * <p>2. Every chain ends at App info, which exists on every Android ever made.
 * A step that cannot find its vendor screen still gets the user somewhere
 * useful instead of nowhere.
 */
public final class Setup {

    public static final int TODO = 0;
    public static final int DONE = 1;
    /** The system gives no way to read this back. */
    public static final int UNKNOWN = 2;

    private Setup() {
    }

    // ---- state --------------------------------------------------------------

    public static int state(Context ctx, String id) {
        try {
            switch (id) {
                case Plan.OVERLAY:
                    return Settings.canDrawOverlays(ctx) ? DONE : TODO;

                case Plan.NOTIF:
                    if (Build.VERSION.SDK_INT < 33) {
                        return DONE;
                    }
                    return ctx.checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                            == PackageManager.PERMISSION_GRANTED ? DONE : TODO;

                case Plan.BATTERY: {
                    PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
                    if (pm == null) {
                        return UNKNOWN;
                    }
                    return pm.isIgnoringBatteryOptimizations(ctx.getPackageName()) ? DONE : TODO;
                }

                case Plan.ACCESS:
                    return accessEnabled(ctx) ? DONE : TODO;

                default:
                    // Autostart lives in a vendor database with no public reader.
                    return UNKNOWN;
            }
        } catch (RuntimeException e) {
            return UNKNOWN;
        }
    }

    /** True when this app is in the system's list of enabled accessibility services. */
    public static boolean accessEnabled(Context ctx) {
        try {
            AccessibilityManager am = (AccessibilityManager)
                    ctx.getSystemService(Context.ACCESSIBILITY_SERVICE);
            if (am == null) {
                return false;
            }
            String pkg = ctx.getPackageName();
            for (AccessibilityServiceInfo info :
                    am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)) {
                String id = info.getId();
                if (id != null && id.startsWith(pkg + "/")) {
                    return true;
                }
            }
        } catch (RuntimeException ignored) {
        }
        return false;
    }

    // ---- opening ------------------------------------------------------------

    /**
     * Opens the screen for one step.
     *
     * @return a short line saying what was opened, for the log. Never null.
     */
    public static String open(Context ctx, String id, String brand) {
        List<Intent> chain = intents(ctx, id, brand);
        for (Intent i : chain) {
            try {
                if (!(ctx instanceof android.app.Activity)) {
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                }
                ctx.startActivity(i);
                return "dibuka: " + label(id);
            } catch (Exception ignored) {
                // next candidate
            }
        }
        return "tidak ada layar yang bisa dibuka untuk " + label(id)
                + " - buka manual dari Settings";
    }

    private static String label(String id) {
        switch (id) {
            case Plan.OVERLAY:
                return "izin gambar di atas app";
            case Plan.NOTIF:
                return "izin notifikasi";
            case Plan.BATTERY:
                return "pembatas baterai";
            case Plan.AUTOSTART:
                return "izin mulai sendiri";
            case Plan.ACCESS:
                return "layanan sentuh";
            default:
                return id;
        }
    }

    /** Every intent worth trying for a step, most specific first. */
    public static List<Intent> intents(Context ctx, String id, String brand) {
        List<Intent> out = new ArrayList<>();
        String pkg = ctx.getPackageName();
        Uri self = Uri.parse("package:" + pkg);

        switch (id) {
            case Plan.OVERLAY:
                out.add(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, self));
                out.add(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION));
                break;

            case Plan.NOTIF:
                out.add(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, pkg));
                break;

            case Plan.BATTERY:
                out.add(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, self));
                out.add(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                break;

            case Plan.ACCESS:
                out.add(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
                break;

            case Plan.AUTOSTART:
                for (String[] c : autostartComponents(brand)) {
                    out.add(new Intent().setComponent(new ComponentName(c[0], c[1])));
                }
                break;

            default:
                break;
        }

        // Always last, always present.
        out.add(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, self));
        return out;
    }

    /**
     * The vendor's own "start on boot / keep running" screen, where one exists.
     *
     * <p>These are internal activities. They are not documented, they move
     * between ROM versions, and some are removed outright. That is exactly why
     * the list is a chain: the first one that launches wins, and the fallback is
     * App info. None of them is assumed to exist.
     */
    public static List<String[]> autostartComponents(String brand) {
        List<String[]> out = new ArrayList<>();
        switch (brand) {
            case "xiaomi":
                out.add(new String[]{"com.miui.securitycenter",
                        "com.miui.permcenter.autostart.AutoStartManagementActivity"});
                break;

            case "oppo":
            case "realme":
                out.add(new String[]{"com.coloros.safecenter",
                        "com.coloros.safecenter.permission.startup.StartupAppListActivity"});
                out.add(new String[]{"com.coloros.safecenter",
                        "com.coloros.safecenter.startupapp.StartupAppListActivity"});
                out.add(new String[]{"com.oplus.safecenter",
                        "com.oplus.safecenter.startupapp.StartupAppListActivity"});
                out.add(new String[]{"com.oppo.safe",
                        "com.oppo.safe.permission.startup.StartupAppListActivity"});
                break;

            case "vivo":
                out.add(new String[]{"com.vivo.permissionmanager",
                        "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"});
                out.add(new String[]{"com.iqoo.secure",
                        "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"});
                break;

            case "huawei":
                out.add(new String[]{"com.huawei.systemmanager",
                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"});
                out.add(new String[]{"com.huawei.systemmanager",
                        "com.huawei.systemmanager.optimize.process.ProtectActivity"});
                break;

            case "honor":
                out.add(new String[]{"com.hihonor.systemmanager",
                        "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity"});
                out.add(new String[]{"com.huawei.systemmanager",
                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"});
                break;

            case "samsung":
                out.add(new String[]{"com.samsung.android.lool",
                        "com.samsung.android.sm.ui.battery.BatteryActivity"});
                out.add(new String[]{"com.samsung.android.lool",
                        "com.samsung.android.sm.battery.ui.BatteryActivity"});
                out.add(new String[]{"com.samsung.android.sm",
                        "com.samsung.android.sm.ui.battery.BatteryActivity"});
                break;

            case "oneplus":
                out.add(new String[]{"com.oplus.battery",
                        "com.oplus.powermanager.fuelgaue.PowerUsageModelActivity"});
                break;

            case "transsion":
                out.add(new String[]{"com.transsion.phonemaster",
                        "com.transsion.phonemaster.ui.autostart.AutoStartActivity"});
                break;

            default:
                break;
        }
        return out;
    }
}
