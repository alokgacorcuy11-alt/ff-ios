package com.alokg.roottools;

/**
 * The steps for the brand the phone says it is.
 *
 * <p>Written as instructions, not as promises. Two things are true of every
 * brand and are said first: unlocking wipes the phone, and the boot image that
 * gets flashed has to come from the exact firmware build the phone is running -
 * a boot image from a different build does not "probably work", it can leave a
 * device that does not start.
 *
 * <p>Menu names differ between ROM versions. Where a name is given it is the
 * common one, and the fallback is described rather than assumed.
 */
public final class Guide {

    private Guide() {
    }

    public static String forBrand(String brand) {
        switch (brand) {
            case "xiaomi":
                return xiaomi();
            case "samsung":
                return samsung();
            case "google":
                return google();
            case "oneplus":
                return oneplus();
            case "locked":
                return locked();
            default:
                return other();
        }
    }

    private static String head() {
        return "SEBELUM MULAI - dua hal ini tidak bisa dibatalkan:\n"
                + "  1. Membuka bootloader MENGHAPUS SEMUA DATA. Cadangkan dulu.\n"
                + "  2. boot.img yang di-flash harus dari firmware yang PERSIS SAMA\n"
                + "     dengan build yang sedang jalan di HP ini. Beda build = HP\n"
                + "     bisa tidak menyala. Catat nomor build-nya dulu\n"
                + "     (Settings -> About phone).\n\n"
                + "Siapkan di komputer: adb + fastboot, dan firmware untuk model\n"
                + "yang persis sama.\n\n";
    }

    private static String xiaomi() {
        return head()
                + "XIAOMI / REDMI / POCO\n"
                + "  1. Settings -> About phone -> ketuk \"MIUI version\" 7x\n"
                + "     (HyperOS: ketuk \"OS version\") -> Developer options nyala\n"
                + "  2. Settings -> Additional settings -> Developer options ->\n"
                + "     \"Mi Unlock status\" -> masukkan akun Mi + SIM aktif\n"
                + "  3. Tunggu masa tunggu vendor (biasanya 7 hari / 168 jam)\n"
                + "  4. Di komputer:\n"
                + "       adb reboot bootloader\n"
                + "       fastboot oem unlock       (HyperOS: fastboot flashing unlock)\n"
                + "  5. Unduh firmware model yang sama, ambil boot.img di dalamnya\n"
                + "  6. Pasang app Magisk di HP -> Install -> \"Select and Patch a File\"\n"
                + "     -> pilih boot.img -> hasilnya magisk_patched-xxxxx.img\n"
                + "  7. Salin hasilnya ke komputer:\n"
                + "       adb reboot bootloader\n"
                + "       fastboot flash boot magisk_patched-xxxxx.img\n"
                + "       fastboot reboot\n"
                + "  8. Buka app Magisk -> status harus \"Installed\"\n";
    }

    private static String samsung() {
        return head()
                + "SAMSUNG\n"
                + "  1. Settings -> About phone -> Software information -> ketuk\n"
                + "     \"Build number\" 7x -> Developer options\n"
                + "  2. Nyalakan \"OEM unlocking\" dan \"USB debugging\"\n"
                + "  3. Matikan HP, lalu masuk download mode:\n"
                + "     tahan Vol Up + Vol Down, colok kabel USB, lepas saat ada prompt,\n"
                + "     lalu tahan Vol Up untuk konfirmasi unlock\n"
                + "  4. Unduh firmware model yang sama, ambil boot.img dari dalam AP\n"
                + "  5. Patch dengan app Magisk, lalu flash lewat Odin:\n"
                + "     taruh hasil patch di slot AP, flash, HP restart sendiri\n"
                + "  6. PERINGATAN Samsung: membuka bootloader memicu Knox permanen.\n"
                + "     Samsung Pay, Secure Folder dan beberapa fitur bank mati dan\n"
                + "     tidak bisa dikembalikan.\n";
    }

    private static String google() {
        return head()
                + "GOOGLE / PIXEL\n"
                + "  1. Settings -> About phone -> ketuk \"Build number\" 7x\n"
                + "  2. Developer options -> nyalakan \"OEM unlocking\" + \"USB debugging\"\n"
                + "  3. adb reboot bootloader\n"
                + "     fastboot flashing unlock\n"
                + "  4. Ambil boot.img dari factory image yang build-nya sama\n"
                + "  5. Patch dengan app Magisk -> fastboot flash boot <hasil>\n"
                + "  6. Setiap kali menyala akan ada peringatan bootloader terbuka.\n"
                + "     Itu normal dan tidak hilang.\n";
    }

    private static String oneplus() {
        return head()
                + "ONEPLUS\n"
                + "  1. Developer options -> \"OEM unlocking\" + \"USB debugging\"\n"
                + "  2. adb reboot bootloader\n"
                + "     fastboot flashing unlock      (model lama: fastboot oem unlock)\n"
                + "  3. Ambil boot.img dari paket firmware build yang sama\n"
                + "  4. Patch dengan app Magisk -> fastboot flash boot <hasil>\n"
                + "  5. Beberapa model OnePlus baru memakai paket \"init_boot.img\",\n"
                + "     bukan boot.img - cek isi firmware, yang ada itulah yang dipakai.\n";
    }

    private static String locked() {
        return "OPPO / REALME / VIVO / HUAWEI / HONOR\n\n"
                + "Jujur di depan: sebagian besar model dari merek ini TIDAK punya\n"
                + "jalan resmi untuk membuka bootloader. Kalau di Developer options\n"
                + "tidak ada pilihan \"OEM unlocking\", atau pilihannya abu-abu dan\n"
                + "tidak bisa dinyalakan, maka memang tidak ada jalan resmi.\n\n"
                + "Cara memastikannya, 30 detik:\n"
                + "  1. Settings -> About phone -> ketuk \"Build number\" 7x\n"
                + "  2. Kembali -> Additional settings -> Developer options\n"
                + "  3. Cari \"OEM unlocking\"\n"
                + "       ada dan bisa dinyalakan  -> jalur terbuka, lanjut ke bagian\n"
                + "                                   Magisk di panduan merek lain\n"
                + "       tidak ada / abu-abu      -> tertutup untuk model ini\n\n"
                + "Beberapa model realme punya program \"In-Depth Test\" sendiri; itu\n"
                + "perlu diperiksa per model. Untuk OPPO seri A, umumnya tidak ada.\n\n"
                + "Kalau tertutup: satu-satunya jalan adalah alat pihak ketiga yang\n"
                + "berbayar dan tidak resmi - dan itu di luar yang bisa gue pandu\n"
                + "dengan jujur, karena hasilnya berbeda-beda per unit dan bisa\n"
                + "membuat HP tidak menyala.\n";
    }

    private static String other() {
        return head()
                + "MEREK LAIN (panduan umum)\n"
                + "  1. Buka Developer options (ketuk \"Build number\" 7x di\n"
                + "     Settings -> About phone)\n"
                + "  2. Nyalakan \"OEM unlocking\" kalau ada. Kalau tidak ada atau\n"
                + "     abu-abu, merek ini mengunci - berhenti di sini\n"
                + "  3. Nyalakan \"USB debugging\", lalu: adb reboot bootloader\n"
                + "  4. fastboot flashing unlock   (atau: fastboot oem unlock)\n"
                + "  5. Ambil boot.img dari firmware dengan build yang sama\n"
                + "  6. Patch dengan app Magisk, flash: fastboot flash boot <hasil>\n";
    }

    /** What to do once the phone answers `su` as uid 0. */
    public static String afterRoot() {
        return "\nSETELAH ROOT - ini yang app ini urus sendiri:\n"
                + "  1. Cek root                su -c id\n"
                + "  2. Versi Magisk            su -c magisk -V\n"
                + "  3. Lihat DenyList          su -c magisk --denylist ls\n"
                + "  4. Tambah game ke DenyList su -c magisk --denylist add com.dts.freefireth\n"
                + "  5. Zygisk harus ON (Magisk -> Settings -> Zygisk)\n"
                + "  6. Modul penyembunyi dipasang dari Magisk, lalu HP restart\n";
    }
}
