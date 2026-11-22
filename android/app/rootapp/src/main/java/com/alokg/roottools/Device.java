package com.alokg.roottools;

import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * What the phone says about itself.
 *
 * <p>Every field comes from {@code getprop}, including the ones that are also
 * available as Java constants. One mechanism, one place to look when a value
 * is wrong, and no dependency on an API level for the answer.
 *
 * <p>A missing property is reported as missing, never as "no". Whether a
 * vendor allows its bootloader to be unlocked is not something to guess at:
 * some ROMs do not define these properties at all, and telling a buyer their
 * phone cannot be unlocked when the truth is "this ROM does not say" would
 * cost them a device they already own.
 */
public final class Device {

    public final String manufacturer;
    public final String model;
    public final String android;
    public final String abis;
    /** "1" the vendor allows it, "0" the vendor locks it, "" this ROM is silent. */
    public final String oemUnlockSupported;
    /** "1" the user has ticked Developer options -> OEM unlocking, "0" not, "" unknown. */
    public final String oemUnlockAllowed;
    /** "1" bootloader locked, "0" unlocked, "" unknown. */
    public final String flashLocked;
    /** green = stock and locked, orange = unlocked, yellow = unlocked, custom key. */
    public final String verifiedBoot;
    /** "1" the build is debuggable - an engineering build, not a retail one. */
    public final String debuggable;

    private Device(String manufacturer, String model, String android, String abis,
                   String oemUnlockSupported, String oemUnlockAllowed,
                   String flashLocked, String verifiedBoot, String debuggable) {
        this.manufacturer = manufacturer;
        this.model = model;
        this.android = android;
        this.abis = abis;
        this.oemUnlockSupported = oemUnlockSupported;
        this.oemUnlockAllowed = oemUnlockAllowed;
        this.flashLocked = flashLocked;
        this.verifiedBoot = verifiedBoot;
        this.debuggable = debuggable;
    }

    public static Device read() {
        return of(
                prop("ro.product.manufacturer"),
                prop("ro.product.model"),
                prop("ro.build.version.release"),
                prop("ro.product.cpu.abilist"),
                prop("ro.oem_unlock_supported"),
                prop("sys.oem_unlock_allowed"),
                prop("ro.boot.flash.locked"),
                prop("ro.boot.verifiedbootstate"),
                prop("ro.debuggable"));
    }

    /**
     * A Device from values that are already known.
     *
     * <p>Exists so the verdict can be tested on a desktop against every
     * combination a real ROM produces, instead of being trusted because it
     * looked right on one phone. Getting this wrong has a specific cost: a
     * buyer told "not possible" stops, and a buyer told "possible" spends an
     * evening on a phone that never could.
     */
    public static Device of(String manufacturer, String model, String android, String abis,
                            String oemUnlockSupported, String oemUnlockAllowed,
                            String flashLocked, String verifiedBoot, String debuggable) {
        return new Device(manufacturer, model, android, abis, oemUnlockSupported,
                oemUnlockAllowed, flashLocked, verifiedBoot, debuggable);
    }

    /** A single property, or "" when the ROM does not define it. */
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

    /** True when the bootloader is already open - the only case that needs no steps. */
    public boolean bootloaderUnlocked() {
        if ("0".equals(flashLocked)) {
            return true;
        }
        return "orange".equalsIgnoreCase(verifiedBoot) || "yellow".equalsIgnoreCase(verifiedBoot);
    }

    /** Which brand's instructions apply. */
    public String brand() {
        String m = manufacturer.toLowerCase();
        if (m.contains("xiaomi") || m.contains("redmi") || m.contains("poco")) return "xiaomi";
        if (m.contains("samsung")) return "samsung";
        if (m.contains("google")) return "google";
        if (m.contains("oneplus")) return "oneplus";
        if (m.contains("oppo") || m.contains("realme") || m.contains("vivo")
                || m.contains("oneplus") || m.contains("huawei") || m.contains("honor")) return "locked";
        return "other";
    }

    /**
     * One line the buyer can act on, and nothing more optimistic than the facts.
     */
    public String verdict() {
        if (bootloaderUnlocked()) {
            return "bootloader SUDAH TERBUKA - langsung ke langkah Magisk";
        }
        if ("1".equals(oemUnlockSupported)) {
            return "vendor MENGIZINKAN buka kunci - jalur root terbuka";
        }
        if ("0".equals(oemUnlockSupported)) {
            return "vendor MENGUNCI - jalur root tertutup untuk model ini";
        }
        return "ROM ini tidak menyebutkan - periksa Developer options -> OEM unlocking";
    }

    public String report() {
        StringBuilder sb = new StringBuilder();
        sb.append("merek   : ").append(dash(manufacturer)).append('\n');
        sb.append("model   : ").append(dash(model)).append('\n');
        sb.append("android : ").append(dash(android)).append('\n');
        sb.append("abi     : ").append(dash(abis)).append('\n');
        sb.append('\n');
        sb.append("oem_unlock_supported : ").append(dash(oemUnlockSupported)).append('\n');
        sb.append("sys.oem_unlock_allowed: ").append(dash(oemUnlockAllowed)).append('\n');
        sb.append("boot.flash.locked    : ").append(dash(flashLocked)).append('\n');
        sb.append("verifiedbootstate    : ").append(dash(verifiedBoot)).append('\n');
        sb.append("debuggable           : ").append(dash(debuggable)).append('\n');
        return sb.toString();
    }

    private static String dash(String s) {
        return s == null || s.isEmpty() ? "(tidak disebutkan)" : s;
    }
}
