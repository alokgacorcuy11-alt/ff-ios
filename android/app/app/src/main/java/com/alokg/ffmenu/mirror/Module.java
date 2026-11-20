package com.alokg.ffmenu.mirror;

import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.provider.DocumentsContract;

import com.alokg.ffmenu.feed.FeedFormat;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;

/**
 * Puts the module where the game's loader looks for it.
 *
 * <p>One file, one place:
 *
 * <pre>
 *   /storage/emulated/0/Android/data/com.dts.freefireth/files/Assembly-CSharp-patch.bytes
 * </pre>
 *
 * <p>Two ways in, because Android made the second one necessary:
 *
 * <ul>
 *   <li><b>direct</b> - a plain file write. Works on Android 10 and below, and
 *       on any build that still has legacy external storage.</li>
 *   <li><b>tree</b> - the system folder picker, pointed at exactly that
 *       directory. Android 11 closed {@code Android/data} to ordinary writes;
 *       the picker is the sanctioned door and it is still open. The reference
 *       build does the same thing and refuses any other folder, because a
 *       grant on the wrong tree writes a file nobody reads.</li>
 * </ul>
 *
 * <p>Both paths write to a staging name, read it back, compare the digest and
 * only then move it into place. A half-written patch is worse than none: the
 * game loads whatever is there, so a truncated file looks like a broken module
 * rather than a failed install.
 *
 * <p>The bytes come from {@code assets/container.bin}, sealed per install by
 * {@code tools/payload_forge.py}, and are decrypted in memory. The tail is
 * random bytes appended after the patch: the patch itself is the same for every
 * buyer, so without it every install would carry one identical file hash - the
 * exact mistake that put the reference build on a list.
 */
public final class Module {

    public static final String GAME = "com.dts.freefireth";
    public static final String FILE = "Assembly-CSharp-patch.bytes";
    private static final String STAGING = ".ffmenu.tmp";
    /** The document id the picker has to return; also the initial folder shown. */
    public static final String TREE_DOC = "primary:Android/data/" + GAME + "/files";
    private static final String CONTAINER = "container.bin";
    private static final String MANIFEST = "manifest.json";
    private static final int DEFAULT_TAIL = 2048;

    private Module() {
    }

    /** The game's own external files directory - what the loader scans. */
    public static String dir() {
        return Environment.getExternalStorageDirectory().getAbsolutePath()
                + "/Android/data/" + GAME + "/files";
    }

    public static boolean present() {
        return new File(dir(), FILE).isFile();
    }

    public static long installedSize() {
        File f = new File(dir(), FILE);
        return f.isFile() ? f.length() : -1;
    }

    // ---- payload -----------------------------------------------------------

    /**
     * Decrypts the container from assets and appends the per-install tail.
     * Nothing is written; the caller decides where it lands.
     */
    public static byte[] payload(Context ctx) throws IOException {
        byte[] container;
        try {
            container = readAll(ctx.getAssets().open(CONTAINER));
        } catch (IOException e) {
            throw new IOException("build ini belum punya payload ("
                    + CONTAINER + " tidak ada di assets)");
        }
        String manifest;
        try {
            manifest = new String(readAll(ctx.getAssets().open(MANIFEST)), "UTF-8");
        } catch (IOException e) {
            throw new IOException("build ini belum punya payload ("
                    + MANIFEST + " tidak ada di assets)");
        }
        JSONObject o;
        try {
            o = new JSONObject(manifest);
        } catch (Exception e) {
            throw new IOException("manifest.json rusak: " + e.getMessage());
        }
        String name = o.optString("name", "");
        String key = o.optString("key", "");
        int tail = o.optInt("tail", DEFAULT_TAIL);
        if (name.isEmpty() || key.length() != 64) {
            throw new IOException("manifest.json tidak lengkap (name/key)");
        }

        byte[] patch = FeedFormat.open(container, hex(key), name);

        byte[] out = new byte[patch.length + Math.max(0, tail)];
        System.arraycopy(patch, 0, out, 0, patch.length);
        if (tail > 0) {
            byte[] pad = new byte[tail];
            new SecureRandom().nextBytes(pad);
            System.arraycopy(pad, 0, out, patch.length, tail);
        }
        return out;
    }

    // ---- install: direct ---------------------------------------------------

    /** Writes straight into the directory. Throws when Android refuses. */
    public static String installDirect(Context ctx) throws IOException {
        byte[] bytes = payload(ctx);
        File dir = new File(dir());
        if (!dir.isDirectory()) {
            throw new IOException("Folder game tidak ditemukan. Buka Free Fire dulu sekali.");
        }
        File target = new File(dir, FILE);
        File staging = new File(dir, STAGING);

        writeFile(staging, bytes);
        if (!Arrays.equals(bytes, readAll(new FileInputStream(staging)))) {
            //noinspection ResultOfMethodCallIgnored
            staging.delete();
            throw new IOException("File yang ditulis tidak sesuai dengan aslinya");
        }
        if (!staging.renameTo(target)) {
            //noinspection ResultOfMethodCallIgnored
            target.delete();
            if (!staging.renameTo(target)) {
                //noinspection ResultOfMethodCallIgnored
                staging.delete();
                throw new IOException("Tidak bisa memindahkan file ke folder game");
            }
        }
        return "Modul terpasang (" + bytes.length + " byte). Tutup game, lalu buka lagi.";
    }

    // ---- install: system picker -------------------------------------------

    /**
     * Writes through a persisted tree grant. The grant must be on exactly the
     * game's files directory; anything else is refused rather than silently
     * writing to a folder the game never reads.
     */
    public static String installTree(Context ctx, Uri tree) throws IOException {
        ContentResolver r = ctx.getContentResolver();
        String treeId;
        try {
            treeId = DocumentsContract.getTreeDocumentId(tree);
        } catch (Exception e) {
            throw new IOException("Izin folder tidak valid");
        }
        if (!TREE_DOC.equals(treeId)) {
            throw new IOException("Pilih tepat Android/data/" + GAME + "/files");
        }

        byte[] bytes = payload(ctx);
        Uri parent = DocumentsContract.buildDocumentUriUsingTree(tree, treeId);

        // A leftover copy would make createDocument pick a suffixed name, and
        // the loader reads one exact name. Delete first, then create.
        Uri existing = DocumentsContract.buildDocumentUriUsingTree(tree, treeId + "/" + FILE);
        try {
            DocumentsContract.deleteDocument(r, existing);
        } catch (Exception ignored) {
            // not there - the normal case on a first install
        }

        Uri doc = DocumentsContract.createDocument(r, parent, "application/octet-stream", FILE);
        if (doc == null) {
            throw new IOException("Tidak bisa membuat file di folder itu");
        }
        OutputStream os = r.openOutputStream(doc, "wt");
        if (os == null) {
            throw new IOException("Tidak bisa menulis ke folder itu");
        }
        try {
            os.write(bytes);
            os.flush();
        } finally {
            os.close();
        }

        byte[] back = readAll(r.openInputStream(doc));
        if (!Arrays.equals(bytes, back)) {
            try {
                DocumentsContract.deleteDocument(r, doc);
            } catch (Exception ignored) {
            }
            throw new IOException("File yang ditulis tidak sesuai dengan aslinya");
        }
        return "Modul terpasang (" + bytes.length + " byte). Tutup game, lalu buka lagi.";
    }

    // ---- helpers -----------------------------------------------------------

    private static void writeFile(File f, byte[] data) throws IOException {
        FileOutputStream out = new FileOutputStream(f);
        try {
            out.write(data);
            out.flush();
            out.getFD().sync();
        } finally {
            out.close();
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream(1 << 16);
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            return out.toByteArray();
        } finally {
            try {
                in.close();
            } catch (IOException ignored) {
            }
        }
    }

    static byte[] hex(String s) throws IOException {
        if (s.length() % 2 != 0) {
            throw new IOException("key bukan hex");
        }
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(s.charAt(i * 2), 16);
            int lo = Character.digit(s.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) {
                throw new IOException("key bukan hex");
            }
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    static String sha256(byte[] data) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder s = new StringBuilder();
            for (byte b : d) {
                s.append(String.format("%02x", b));
            }
            return s.toString();
        } catch (Exception e) {
            return "";
        }
    }
}
