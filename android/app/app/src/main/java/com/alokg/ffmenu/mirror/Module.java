package com.alokg.ffmenu.mirror;

import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
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
    /** Where the picked folder grant is remembered between launches. */
    private static final String TREE_FILE = "tree";
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

    // ---- the folder grant, remembered ---------------------------------------

    /**
     * The tree the buyer picked once, so it never has to be picked again.
     *
     * <p>Android 11 closed {@code Android/data} to ordinary writes and the
     * system picker is the only door. That would be a tap on every install and
     * another on every removal - which is exactly the "ribet" the buyer
     * complains about. The picker hands out a <i>persistable</i> grant, so it
     * is written down here once and reused by both.
     */
    public static void rememberTree(Context ctx, Uri tree) {
        writeText(new File(ctx.getFilesDir(), TREE_FILE), tree.toString());
    }

    /** The remembered grant, or null when the picker has never been used. */
    public static Uri rememberedTree(Context ctx) {
        String s = readText(new File(ctx.getFilesDir(), TREE_FILE));
        return s.isEmpty() ? null : Uri.parse(s);
    }

    /** Drops the record and hands the grant back to the system. */
    public static void releaseTree(Context ctx) {
        Uri tree = rememberedTree(ctx);
        //noinspection ResultOfMethodCallIgnored
        new File(ctx.getFilesDir(), TREE_FILE).delete();
        if (tree == null) {
            return;
        }
        try {
            ctx.getContentResolver().releasePersistableUriPermission(tree,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION
                            | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        } catch (Exception ignored) {
            // already gone - not a failure worth reporting
        }
    }

    // ---- payload -----------------------------------------------------------

    /**
     * Decrypts the container from assets and appends the per-install tail.
     * Nothing is written; the caller decides where it lands.
     */
    public static byte[] payload(Context ctx) throws IOException {
        return payload(ctx, true);
    }

    /**
     * @param withTail append the random per-install tail. {@code false} writes
     *     exactly the bytes the reference build writes - the compatibility mode,
     *     for the case where the loader turns out to mind trailing bytes. The
     *     patch format is length-delimited, so it should not, but "should" is not
     *     the same as "tested on a phone", and a buyer who cannot install has no
     *     second option.
     */
    public static byte[] payload(Context ctx, boolean withTail) throws IOException {
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
        int tail = withTail ? o.optInt("tail", DEFAULT_TAIL) : 0;
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
        return installDirect(ctx, true);
    }

    /** @param withTail see {@link #payload(Context, boolean)} */
    public static String installDirect(Context ctx, boolean withTail) throws IOException {
        byte[] bytes = payload(ctx, withTail);
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
        return "Modul terpasang (" + bytes.length + " byte"
                + (withTail ? "" : ", mode tanpa tail") + "). Tutup game, lalu buka lagi.";
    }

    // ---- install: system picker -------------------------------------------

    /**
     * Writes through a persisted tree grant. The grant must be on exactly the
     * game's files directory; anything else is refused rather than silently
     * writing to a folder the game never reads.
     */
    public static String installTree(Context ctx, Uri tree) throws IOException {
        return installTree(ctx, tree, true);
    }

    /** @param withTail see {@link #payload(Context, boolean)} */
    public static String installTree(Context ctx, Uri tree, boolean withTail) throws IOException {
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

        byte[] bytes = payload(ctx, withTail);
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
        // Written down so that MIRROR can report the truth about it, and so
        // that turning it off later needs no second trip through the picker.
        rememberTree(ctx, tree);
        return "Modul terpasang (" + bytes.length + " byte"
                + (withTail ? "" : ", mode tanpa tail") + "). Tutup game, lalu buka lagi.";
    }

    // ---- is it there? ------------------------------------------------------

    /**
     * Whether the module file is in the folder - asked the way that still
     * works on Android 11 and later.
     *
     * <p>A plain {@code File} check is a lie there: {@code Android/data} of
     * another package is closed to path-based access, so an installed module
     * and a missing one look exactly the same. If a folder grant has ever been
     * given, this asks through it instead, which is an answer rather than a
     * guess. Only when there is no grant does it fall back to the path, which
     * is the correct answer on Android 10 and below.
     */
    public static boolean exists(Context ctx) {
        Uri tree = rememberedTree(ctx);
        if (tree != null) {
            try {
                InputStream in = ctx.getContentResolver().openInputStream(docUri(tree));
                if (in != null) {
                    in.close();
                    return true;
                }
            } catch (Exception ignored) {
                return false;
            }
        }
        return new File(dir(), FILE).isFile();
    }

    /** The size of the installed module, or -1 when it cannot be read. */
    public static long size(Context ctx) {
        Uri tree = rememberedTree(ctx);
        if (tree != null) {
            try (android.database.Cursor c = ctx.getContentResolver().query(docUri(tree),
                    new String[]{DocumentsContract.Document.COLUMN_SIZE},
                    null, null, null)) {
                if (c != null && c.moveToFirst() && !c.isNull(0)) {
                    return c.getLong(0);
                }
            } catch (Exception ignored) {
                // fall through to the path
            }
        }
        File f = new File(dir(), FILE);
        return f.isFile() ? f.length() : -1;
    }

    private static Uri docUri(Uri tree) {
        String treeId = DocumentsContract.getTreeDocumentId(tree);
        return DocumentsContract.buildDocumentUriUsingTree(tree, treeId + "/" + FILE);
    }

    // ---- removal: the other half of "install" ------------------------------

    /**
     * Takes the module back out, and gives the folder grant back with it.
     *
     * <p>This is what "turn it off and the phone is as it was" means in
     * practice: one file deleted, verified deleted, and the one permission the
     * app had been holding released. Nothing else was ever changed - there is
     * no install step, no system setting, no background process - so nothing
     * else has to be undone.
     *
     * <p>Tried through the remembered grant first, because that is the door
     * that is still open on Android 11 and later, and it needs no taps. The
     * path is the fallback for older Androids.
     */
    public static String remove(Context ctx) throws IOException {
        IOException viaTree = null;
        Uri tree = rememberedTree(ctx);
        if (tree != null) {
            try {
                String r = removeTree(ctx, tree);
                releaseTree(ctx);
                return r;
            } catch (IOException e) {
                viaTree = e;
            }
        }
        try {
            String r = removeDirect(ctx);
            releaseTree(ctx);
            return r;
        } catch (IOException e) {
            if (viaTree != null) {
                throw new IOException(e.getMessage()
                        + " (lewat folder juga gagal: " + viaTree.getMessage() + ")");
            }
            throw e;
        }
    }

    /** Deletes straight through the path. Throws when Android refuses. */
    public static String removeDirect(Context ctx) throws IOException {
        File target = new File(dir(), FILE);
        File staging = new File(dir(), STAGING);
        //noinspection ResultOfMethodCallIgnored
        staging.delete();
        if (!target.exists()) {
            return "tidak ada modul - HP sudah bersih";
        }
        if (!target.delete()) {
            throw new IOException("file modul tidak bisa dihapus");
        }
        if (target.exists()) {
            throw new IOException("file masih ada setelah dihapus");
        }
        return "modul dihapus (" + FILE + ")";
    }

    /**
     * Deletes through the remembered folder grant. A delete that reports
     * success but leaves the file is not a delete, so the absence is confirmed
     * by re-reading it.
     */
    public static String removeTree(Context ctx, Uri tree) throws IOException {
        String treeId;
        try {
            treeId = DocumentsContract.getTreeDocumentId(tree);
        } catch (Exception e) {
            throw new IOException("Izin folder tidak valid");
        }
        if (!TREE_DOC.equals(treeId)) {
            throw new IOException("Izin folder bukan folder game");
        }
        ContentResolver r = ctx.getContentResolver();
        Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, treeId + "/" + FILE);
        boolean there;
        try {
            InputStream in = r.openInputStream(doc);
            there = in != null;
            if (in != null) {
                in.close();
            }
        } catch (Exception e) {
            there = false;
        }
        if (!there) {
            return "tidak ada modul - HP sudah bersih";
        }
        boolean gone;
        try {
            gone = DocumentsContract.deleteDocument(r, doc);
        } catch (Exception e) {
            throw new IOException("tidak bisa menghapus di folder itu: " + e.getMessage());
        }
        if (!gone) {
            throw new IOException("folder menolak menghapus file modul");
        }
        try {
            InputStream in = r.openInputStream(doc);
            if (in != null) {
                in.close();
                throw new IOException("file masih ada setelah dihapus");
            }
        } catch (java.io.FileNotFoundException expected) {
            // what a successful delete looks like
        }
        return "modul dihapus (" + FILE + ")";
    }

    // ---- helpers -----------------------------------------------------------

    private static void writeFile(File f, byte[] data) throws IOException {        FileOutputStream out = new FileOutputStream(f);
        try {
            out.write(data);
            out.flush();
            out.getFD().sync();
        } finally {
            out.close();
        }
    }

    /** A small text file in the app's own storage. Never throws. */
    static void writeText(File f, String s) {
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(s.getBytes("UTF-8"));
            out.flush();
        } catch (IOException ignored) {
            // a lost record means one extra trip through the picker, not a failure
        }
    }

    static String readText(File f) {
        if (!f.isFile()) {
            return "";
        }
        try (FileInputStream in = new FileInputStream(f)) {
            return new String(readAll(in), "UTF-8").trim();
        } catch (IOException e) {
            return "";
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
