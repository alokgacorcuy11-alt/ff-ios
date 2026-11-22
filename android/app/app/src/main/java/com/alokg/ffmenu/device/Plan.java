package com.alokg.ffmenu.device;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * The setup a phone needs before the capture can survive a game session, and
 * which of those steps this particular brand actually has.
 *
 * <p>This is the part that decides whether "works on every brand" is true. The
 * code is the same everywhere - MediaProjection, an overlay window, a
 * foreground service - but every vendor ships a different screen that decides
 * whether a background service is allowed to keep running, and several of them
 * kill it by default. None of that is guesswork: the steps are the ones the
 * vendors themselves expose, and the app opens the right one.
 *
 * <p>Android-free on purpose. The list of steps for a brand and an API level is
 * a decision worth testing, and it is tested in tools/tests/test_tier.py.
 */
public final class Plan {

    public static final String OVERLAY = "overlay";
    public static final String NOTIF = "notif";
    public static final String BATTERY = "battery";
    public static final String AUTOSTART = "autostart";
    public static final String ACCESS = "access";

    /** Brands whose ROM ships a separate "allow this app to start itself" list. */
    private static final List<String> AUTOSTART_BRANDS = Collections.unmodifiableList(
            Arrays.asList("xiaomi", "oppo", "realme", "vivo", "samsung", "huawei",
                    "honor", "transsion", "oneplus", "motorola", "asus", "lenovo",
                    "nokia", "sony", "lg", "other"));

    public static final class Step {
        public final String id;
        public final String title;
        public final String detail;
        /** Optional steps do not block the boxes; they unlock aim and survival. */
        public final boolean optional;

        Step(String id, String title, String detail, boolean optional) {
            this.id = id;
            this.title = title;
            this.detail = detail;
            this.optional = optional;
        }
    }

    private Plan() {
    }

    /**
     * Which set of vendor instructions applies.
     *
     * <p>Reads both the manufacturer and the brand property, because they do
     * not always agree: a Redmi reports manufacturer "Xiaomi" and brand "Redmi",
     * a POCO reports "Xiaomi" and "POCO", and an iQOO reports "vivo". Matching
     * on the manufacturer alone puts two of those on the wrong guide.
     */
    public static String brandKey(String manufacturer, String brandProp) {
        String m = (manufacturer == null ? "" : manufacturer)
                + " " + (brandProp == null ? "" : brandProp);
        m = m.toLowerCase(Locale.US);
        if (m.contains("xiaomi") || m.contains("redmi") || m.contains("poco")) return "xiaomi";
        if (m.contains("realme")) return "realme";
        if (m.contains("oneplus")) return "oneplus";
        if (m.contains("oppo") || m.contains("oplus")) return "oppo";
        if (m.contains("iqoo")) return "vivo";
        if (m.contains("vivo")) return "vivo";
        if (m.contains("samsung")) return "samsung";
        if (m.contains("huawei")) return "huawei";
        if (m.contains("honor") || m.contains("hihonor")) return "honor";
        if (m.contains("google")) return "google";
        if (m.contains("infinix") || m.contains("tecno") || m.contains("itel")
                || m.contains("transsion")) return "transsion";
        if (m.contains("motorola") || m.contains("moto")) return "motorola";
        if (m.contains("nokia")) return "nokia";
        if (m.contains("asus")) return "asus";
        if (m.contains("lenovo")) return "lenovo";
        if (m.contains("sony")) return "sony";
        if (m.contains("lg ")) return "lg";
        return "other";
    }

    /**
     * The ordered checklist for one phone.
     *
     * @param brand a key from DeviceProfile.brandKey
     * @param api   Build.VERSION.SDK_INT
     */
    public static List<Step> forDevice(String brand, int api) {
        List<Step> out = new ArrayList<>();

        out.add(new Step(OVERLAY, "IZIN GAMBAR DI ATAS APP",
                "Kotak musuh digambar sebagai jendela di atas game. Tanpa ini "
                        + "tidak ada yang bisa tampil.", false));

        if (api >= 33) {
            out.add(new Step(NOTIF, "IZIN NOTIFIKASI",
                    "Android 13 ke atas menyembunyikan notifikasi kalau izin ini "
                            + "tidak diberi. Rekam layar tetap jalan, tapi tidak ada "
                            + "tanda bahwa ia jalan.", false));
        }

        out.add(new Step(BATTERY, "MATIKAN PEMBATAS BATERAI",
                "Tanpa ini sistem mematikan rekam layar saat game dibuka. Ini "
                        + "penyebab paling sering dari \"ESP mati sendiri\".", false));

        if (needsAutostart(brand)) {
            out.add(new Step(AUTOSTART, "IZINKAN MULAI SENDIRI",
                    autostartWhy(brand), false));
        }

        out.add(new Step(ACCESS, "LAYANAN SENTUH (opsional)",
                api >= 33
                        ? "Untuk bantuan bidik. Di Android 13 ke atas app dari luar "
                        + "Play Store terkunci: buka App info -> titik tiga -> "
                        + "\"Allow restricted settings\" dulu, baru layanan ini bisa "
                        + "dinyalakan. Tanpa ini kotak tetap jalan."
                        : "Untuk bantuan bidik. Tanpa ini kotak tetap jalan.",
                true));

        return out;
    }

    public static boolean needsAutostart(String brand) {
        return AUTOSTART_BRANDS.contains(brand);
    }

    private static String autostartWhy(String brand) {
        if ("xiaomi".equals(brand)) {
            return "MIUI/HyperOS mematikan app latar belakang secara default. "
                    + "Nyalakan Autostart DAN \"Display pop-up windows while "
                    + "running in background\".";
        }
        if ("oppo".equals(brand) || "realme".equals(brand)) {
            return "ColorOS/realme UI punya \"Auto Startup\" dan \"App Quick "
                    + "Freeze\". Keduanya harus dilewati untuk app ini.";
        }
        if ("vivo".equals(brand)) {
            return "Funtouch/OriginOS memakai \"Auto-start\" dan \"High background "
                    + "power consumption\". Nyalakan keduanya.";
        }
        if ("samsung".equals(brand)) {
            return "One UI menidurkan app yang tidak dipakai. Masukkan app ini ke "
                    + "\"Never sleeping apps\".";
        }
        if ("huawei".equals(brand) || "honor".equals(brand)) {
            return "EMUI/HarmonyOS punya \"App launch\" yang harus diatur manual "
                    + "ke \"Manage manually\" dengan ketiga sakelar nyala.";
        }
        if ("transsion".equals(brand)) {
            return "Infinix/Tecno/itel memakai \"Power Marathon\". Matikan untuk "
                    + "app ini.";
        }
        return "Kalau merek ini punya pengaturan \"autostart\" atau \"izin mulai "
                + "sendiri\", nyalakan untuk app ini.";
    }
}
