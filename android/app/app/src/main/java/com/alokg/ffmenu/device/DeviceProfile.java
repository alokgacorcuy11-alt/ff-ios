package com.alokg.ffmenu.device;

import android.app.ActivityManager;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.DisplayMetrics;
import android.view.Display;
import android.view.WindowManager;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.InputStreamReader;

/**
 * Everything about the phone the app has to adapt to, read once.
 *
 * <p>Values come from two places and both are checked: {@link Build} for the
 * things Android guarantees, and {@code getprop} for the things only the ROM
 * knows - the MIUI version, the ColorOS version, whether the bootloader is
 * open. A property that is missing comes back as "" and is treated as unknown,
 * never as a no.
 *
 * <p>Nothing here throws. A phone with a broken ROM must still get a working
 * app, and every read is wrapped because on some of those ROMs even
 * {@code getprop} is not where it should be.
 */
public final class DeviceProfile {

    public final String manufacturer;
    public final String model;
    public final String brand;
    public final String android;
    public final int api;
    public final String abi;
    public final int ramMb;
    public final int cores;
    public final int maxKhz;
    public final int screenW;
    public final int screenH;
    public final int densityDpi;
    /** "" when the ROM does not use that overlay. */
    public final String romName;
    public final String romVersion;
    public final boolean debuggable;
    /** Free Fire as installed, or null. */
    public final String gameVersion;
    public final int gameTargetSdk;
    public final Tier tier;

    private DeviceProfile(String manufacturer, String model, String brand, String android,
                          int api, String abi, int ramMb, int cores, int maxKhz,
                          int screenW, int screenH, int densityDpi,
                          String romName, String romVersion, boolean debuggable,
                          String gameVersion, int gameTargetSdk, Tier tier) {
        this.manufacturer = manufacturer;
        this.model = model;
        this.brand = brand;
        this.android = android;
        this.api = api;
        this.abi = abi;
        this.ramMb = ramMb;
        this.cores = cores;
        this.maxKhz = maxKhz;
        this.screenW = screenW;
        this.screenH = screenH;
        this.densityDpi = densityDpi;
        this.romName = romName;
        this.romVersion = romVersion;
        this.debuggable = debuggable;
        this.gameVersion = gameVersion;
        this.gameTargetSdk = gameTargetSdk;
        this.tier = tier;
    }

    public static DeviceProfile read(Context ctx) {
        String man = safe(Build.MANUFACTURER);
        String brandProp = safe(prop("ro.product.brand"));
        String brand = Plan.brandKey(man, brandProp);

        String romName = "";
        String romVersion = "";
        String miui = prop("ro.miui.ui.version.name");
        String coloros = prop("ro.build.version.opporom");
        String oplus = prop("ro.build.version.oplusrom");
        String funtouch = prop("ro.vivo.os.version");
        String emui = prop("ro.build.version.emui");
        String oneui = prop("ro.build.version.oneui");
        String harmony = prop("hw_sc.build.platform.version");

        if (!miui.isEmpty()) {
            romName = "MIUI";
            romVersion = miui;
        } else if (!coloros.isEmpty()) {
            romName = "ColorOS";
            romVersion = coloros;
        } else if (!oplus.isEmpty()) {
            romName = "ColorOS";
            romVersion = oplus;
        } else if (!funtouch.isEmpty()) {
            romName = "Funtouch";
            romVersion = funtouch;
        } else if (!emui.isEmpty()) {
            romName = "EMUI";
            romVersion = emui;
        } else if (!harmony.isEmpty()) {
            romName = "HarmonyOS";
            romVersion = harmony;
        } else if (!oneui.isEmpty()) {
            romName = "One UI";
            romVersion = oneui;
        }

        int ramMb = 0;
        try {
            ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
            if (am != null) {
                ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
                am.getMemoryInfo(mi);
                ramMb = (int) (mi.totalMem / (1024L * 1024L));
            }
        } catch (RuntimeException ignored) {
        }

        int[] screen = screenSize(ctx);
        int dpi = 0;
        try {
            dpi = ctx.getResources().getDisplayMetrics().densityDpi;
        } catch (RuntimeException ignored) {
        }

        String gameVersion = "";
        int gameTarget = 0;
        try {
            PackageInfo pi = ctx.getPackageManager()
                    .getPackageInfo(GAME, 0);
            gameVersion = safe(pi.versionName);
            gameTarget = pi.applicationInfo == null ? 0 : pi.applicationInfo.targetSdkVersion;
        } catch (PackageManager.NameNotFoundException | RuntimeException ignored) {
            // not installed, or hidden by package visibility on API 30+
        }

        int cores = Math.max(1, Runtime.getRuntime().availableProcessors());
        int khz = maxCpuKhz();
        int api = Build.VERSION.SDK_INT;

        return new DeviceProfile(
                man, safe(Build.MODEL), brand, safe(Build.VERSION.RELEASE), api,
                safe(prop("ro.product.cpu.abilist")), ramMb, cores, khz,
                screen[0], screen[1], dpi, romName, romVersion,
                "1".equals(prop("ro.debuggable")),
                gameVersion, gameTarget,
                Tier.of(api, ramMb, cores, khz));
    }

    public static final String GAME = "com.dts.freefireth";

    /** The screen in its current orientation, from the display rather than the app. */
    public static int[] screenSize(Context ctx) {
        int w = 0;
        int h = 0;
        try {
            WindowManager wm = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
            if (wm != null) {
                Display d = wm.getDefaultDisplay();
                DisplayMetrics m = new DisplayMetrics();
                d.getRealMetrics(m);
                w = m.widthPixels;
                h = m.heightPixels;
            }
        } catch (RuntimeException ignored) {
        }
        if (w <= 0 || h <= 0) {
            try {
                DisplayMetrics m = ctx.getResources().getDisplayMetrics();
                w = m.widthPixels;
                h = m.heightPixels;
            } catch (RuntimeException ignored) {
            }
        }
        return new int[]{Math.max(1, w), Math.max(1, h)};
    }

    /** Highest core frequency the kernel will admit to, or 0. */
    private static int maxCpuKhz() {
        int best = 0;
        for (int cpu = 0; cpu < 8; cpu++) {
            File f = new File("/sys/devices/system/cpu/cpu" + cpu + "/cpufreq/cpuinfo_max_freq");
            if (!f.canRead()) {
                continue;
            }
            BufferedReader r = null;
            try {
                r = new BufferedReader(new FileReader(f));
                String line = r.readLine();
                if (line != null) {
                    best = Math.max(best, Integer.parseInt(line.trim()));
                }
            } catch (Exception ignored) {
            } finally {
                close(r);
            }
        }
        return best;
    }

    /** A property, or "" when the ROM does not define it. */
    public static String prop(String name) {
        Process p = null;
        try {
            p = new ProcessBuilder("getprop", name).redirectErrorStream(true).start();
            BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line);
            }
            p.waitFor();
            return sb.toString().trim();
        } catch (Exception e) {
            return "";
        } finally {
            if (p != null) {
                p.destroy();
            }
        }
    }

    private static void close(BufferedReader r) {
        if (r != null) {
            try {
                r.close();
            } catch (Exception ignored) {
            }
        }
    }

    private static String safe(String s) {
        return s == null ? "" : s.trim();
    }

    /** The ROM line, or "" when this is a stock build. */
    public String rom() {
        return romName.isEmpty() ? "" : romName + " " + romVersion;
    }

    public String report() {
        StringBuilder sb = new StringBuilder();
        sb.append("hp      : ").append(dash(manufacturer)).append(' ')
                .append(dash(model)).append(" (").append(brand).append(")\n");
        sb.append("android : ").append(dash(android)).append(" / api ").append(api).append('\n');
        if (!romName.isEmpty()) {
            sb.append("rom     : ").append(rom()).append('\n');
        }
        sb.append("cpu     : ").append(cores).append(" inti")
                .append(maxKhz > 0 ? ", maks " + (maxKhz / 1000) + " MHz" : "").append('\n');
        sb.append("ram     : ").append(ramMb).append(" MB\n");
        sb.append("layar   : ").append(screenW).append('x').append(screenH)
                .append(" @ ").append(densityDpi).append(" dpi\n");
        sb.append("abi     : ").append(dash(abi)).append('\n');
        sb.append("game    : ").append(gameVersion.isEmpty()
                ? "TIDAK terpasang" : gameVersion + " (targetSdk " + gameTargetSdk + ")").append('\n');
        sb.append("profil  : ").append(tier.name());
        int[] s = tier.captureSize(screenW, screenH);
        if (tier.level != Tier.UNSUPPORTED) {
            sb.append(" - rekam ").append(s[0]).append('x').append(s[1])
                    .append(" @ ").append(tier.fps).append(" fps");
        }
        sb.append('\n');
        return sb.toString();
    }

    private static String dash(String s) {
        return s == null || s.isEmpty() ? "-" : s;
    }
}
