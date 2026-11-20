package com.alokg.ffmenu.mirror;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * The panel side of the module's control surface, exactly as the reference
 * build defines it.
 *
 * <p>The module runs inside the game and answers on loopback:
 *
 * <pre>
 *   GET /                     is anything there
 *   GET /itemN                turn feature N on
 *   GET /itemN/desativar      turn feature N off
 *   GET /fov/N                aim FOV size
 *   GET /xyz/fov3-status      one line of state
 * </pre>
 *
 * <p>Three details that are not guessable and are load-bearing:
 *
 * <ul>
 *   <li>The module only answers requests carrying {@code Origin:
 *       https://patoteam.shop}. Send anything else and every call comes back
 *       non-200 while the module is running perfectly.</li>
 *   <li>{@code HttpURLConnection} silently drops that header - it is on the JDK
 *       and Android restricted list - so the request is written over a plain
 *       socket instead. A header that disappears without an error is not
 *       something to build a control surface on.</li>
 *   <li>The numbering has holes. 3 and 4 do not exist in 3.0; 5 is the drag
 *       headshot, not the fifth row of a list. The table below is the mapping
 *       read out of the reference panel's own if/else chain, and
 *       {@code tools/tests/test_bridge.py} pins it so a "tidy" renumbering
 *       cannot silently move a switch onto the wrong feature.</li>
 * </ul>
 *
 * <p>No Android imports: this compiles on a desktop JDK and is tested against a
 * real socket, because a control surface that is only exercised by hand on a
 * phone is a control surface nobody has tested.
 */
public final class Bridge {

    public static final String HOST = "127.0.0.1";
    public static final int PORT = 7755;
    public static final int TIMEOUT_MS = 3000;
    /** The module refuses anything else; see the class comment. */
    public static final String ORIGIN = "https://patoteam.shop";
    private static final int MAX_BODY = 64 * 1024;

    /** Every item the 3.0 module implements, in panel order. */
    public static final int[] ITEMS = {1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};

    private static final String[] MODE = {
            "FOV mati",
            "pilih mode aim",
            "pemain belum ada",
            "kamera belum ada",
            "snapshot belum ada",
            "mencari target",
            "target siap",
            "tembak terbaca",
            "perintah dikirim",
            "kesalahan modul",
    };

    private final String host;
    private final int port;
    private final int timeoutMs;

    public Bridge() {
        this(HOST, PORT, TIMEOUT_MS);
    }

    public Bridge(String host, int port, int timeoutMs) {
        this.host = host;
        this.port = port;
        this.timeoutMs = timeoutMs;
    }

    /** The panel label for an item number, from the reference build. */
    public static String label(int item) {
        switch (item) {
            case 1:
                return "LOCK HEAD";
            case 2:
                return "ESP LINE";
            case 5:
                return "DRAG HS 85%";
            case 6:
                return "ESP NAME";
            case 7:
                return "ESP HEALTH";
            case 8:
                return "LOCK BADAN";
            case 9:
                return "ESP SKELETON";
            case 10:
                return "ESP DISTANCE";
            case 11:
                return "AIM FOV";
            case 12:
                return "NO RECOIL";
            case 13:
                return "HIDE AIM FOV";
            case 14:
                return "FAST SWITCH";
            case 15:
                return "NO SPREAD";
            case 16:
                return "FAST RELOAD";
            default:
                return "ITEM " + item;
        }
    }

    // ---- requests ----------------------------------------------------------

    /** One GET, body as text. Non-2xx is an exception, not a silent empty. */
    public String get(String path) throws IOException {
        if (path == null || !path.startsWith("/")) {
            throw new IllegalArgumentException("path must start with /: " + path);
        }
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), timeoutMs);
            s.setSoTimeout(timeoutMs);

            String req = "GET " + path + " HTTP/1.1\r\n"
                    + "Host: " + host + ":" + port + "\r\n"
                    + "Origin: " + ORIGIN + "\r\n"
                    + "Accept: */*\r\n"
                    + "Connection: close\r\n"
                    + "\r\n";
            OutputStream out = s.getOutputStream();
            out.write(req.getBytes(StandardCharsets.US_ASCII));
            out.flush();

            return readResponse(new BufferedInputStream(s.getInputStream()));
        } finally {
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
    }

    /** Turns one feature on or off. Returns the state that was requested. */
    public boolean command(int item, boolean on) throws IOException {
        if (item < 1 || item > 16) {
            throw new IllegalArgumentException("item " + item + " is outside 1..16");
        }
        get("/item" + item + (on ? "" : "/desativar"));
        return on;
    }

    /** Sets the aim FOV size. The reference panel allows 10..90. */
    public int fov(int size) throws IOException {
        if (size < 1 || size > 200) {
            throw new IllegalArgumentException("fov " + size + " is out of range");
        }
        get("/fov/" + size);
        return size;
    }

    /** True if the module is up and answering. Never throws. */
    public boolean alive() {
        try {
            get("/");
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    /**
     * One line that says which layer is missing.
     *
     * <p>Pulled out of the activity so it can be tested without a phone: the
     * whole point is to tell a buyer where it stopped, and a message that is only
     * assembled inside a click handler is a message nobody has read.
     *
     * @param fileThere whether the patch is in the game's folder
     * @param size      its size in bytes, or -1
     * @param alive     whether the module answered on loopback
     */
    public static String verdict(boolean fileThere, long size, boolean alive) {
        if (!fileThere) {
            return "cek: patch BELUM ada di folder game - tekan INSTALL dulu";
        }
        if (size <= 0) {
            return "cek: patch ada tapi kosong - tekan INSTALL lagi";
        }
        if (alive) {
            return "cek: patch ada (" + size + " B) | modul MENJAWAB";
        }
        return "cek: patch ada (" + size + " B) | modul TIDAK menjawab"
                + " - tutup lalu buka ulang game."
                + " kalau tetap: tekan LAMA tombol INSTALL (mode tanpa tail), buka game lagi";
    }

    public Status status() throws IOException {
        return parse(get("/xyz/fov3-status"));
    }

    // ---- response ----------------------------------------------------------

    private String readResponse(InputStream in) throws IOException {
        String status = line(in);
        if (status == null || !status.startsWith("HTTP/")) {
            throw new IOException("respons bukan HTTP");
        }
        String[] parts = status.split(" ");
        if (parts.length < 2) {
            throw new IOException("status line rusak: " + status);
        }
        int code;
        try {
            code = Integer.parseInt(parts[1]);
        } catch (NumberFormatException e) {
            throw new IOException("status line rusak: " + status);
        }

        int len = -1;
        boolean chunked = false;
        for (String h = line(in); h != null && !h.isEmpty(); h = line(in)) {
            int c = h.indexOf(':');
            if (c < 0) {
                continue;
            }
            String k = h.substring(0, c).trim().toLowerCase(Locale.US);
            String v = h.substring(c + 1).trim();
            if ("content-length".equals(k)) {
                try {
                    len = Integer.parseInt(v);
                } catch (NumberFormatException ignored) {
                    len = -1;
                }
            } else if ("transfer-encoding".equals(k)
                    && v.toLowerCase(Locale.US).contains("chunked")) {
                chunked = true;
            }
        }

        if (code < 200 || code >= 300) {
            throw new IOException("modul menolak perintah (HTTP " + code + ")");
        }

        ByteArrayOutputStream body = new ByteArrayOutputStream(256);
        if (chunked) {
            while (true) {
                String size = line(in);
                if (size == null) {
                    break;
                }
                int semi = size.indexOf(';');
                if (semi >= 0) {
                    size = size.substring(0, semi);
                }
                int n;
                try {
                    n = Integer.parseInt(size.trim(), 16);
                } catch (NumberFormatException e) {
                    throw new IOException("chunk rusak: " + size);
                }
                if (n == 0) {
                    break;
                }
                copy(in, body, n);
                line(in);
            }
        } else if (len >= 0) {
            copy(in, body, len);
        } else {
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0) {
                if (body.size() + n > MAX_BODY) {
                    throw new IOException("respons terlalu panjang");
                }
                body.write(buf, 0, n);
            }
        }
        return new String(body.toByteArray(), StandardCharsets.UTF_8);
    }

    private static void copy(InputStream in, ByteArrayOutputStream out, int n)
            throws IOException {
        if (n < 0 || out.size() + n > MAX_BODY) {
            throw new IOException("respons terlalu panjang");
        }
        byte[] buf = new byte[Math.min(n, 4096)];
        int left = n;
        while (left > 0) {
            int got = in.read(buf, 0, Math.min(left, buf.length));
            if (got < 0) {
                throw new IOException("respons terpotong");
            }
            out.write(buf, 0, got);
            left -= got;
        }
    }

    /** One header or status line, CR stripped. Null at EOF. */
    private static String line(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream(64);
        int c;
        while ((c = in.read()) >= 0) {
            if (c == '\n') {
                break;
            }
            if (out.size() > 4096) {
                throw new IOException("baris terlalu panjang");
            }
            out.write(c);
        }
        if (c < 0 && out.size() == 0) {
            return null;
        }
        byte[] b = out.toByteArray();
        int n = b.length;
        if (n > 0 && b[n - 1] == '\r') {
            n--;
        }
        return new String(b, 0, n, StandardCharsets.US_ASCII);
    }

    // ---- status line -------------------------------------------------------

    /** One sample of the module's own state, `XYZFOV3|...`. */
    public static final class Status {
        public final int mode;
        public final int players;
        public final int valid;
        public final int inFov;
        public final boolean shot;
        public final boolean sent;
        public final boolean error;

        Status(int mode, int players, int valid, int inFov,
               boolean shot, boolean sent, boolean error) {
            this.mode = mode;
            this.players = players;
            this.valid = valid;
            this.inFov = inFov;
            this.shot = shot;
            this.sent = sent;
            this.error = error;
        }

        public String modeName() {
            return mode >= 0 && mode < MODE.length ? MODE[mode] : "mode " + mode;
        }

        @Override
        public String toString() {
            return "FOV: " + modeName()
                    + " | pemain " + players
                    + ", valid " + valid
                    + ", dalam FOV " + inFov
                    + " | tembak " + (shot ? "YA" : "TIDAK")
                    + " | kamera " + (sent ? "YA" : "TIDAK")
                    + " | error " + (error ? "YA" : "TIDAK");
        }
    }

    public static Status parse(String raw) throws IOException {
        if (raw == null) {
            throw new IOException("status: kosong");
        }
        String[] f = raw.trim().split("\\|", -1);
        if (f.length != 8 || !"XYZFOV3".equals(f[0])) {
            throw new IOException("status tidak valid: " + raw);
        }
        int mode = num(f[1], 9, raw);
        int players = num(f[2], 4096, raw);
        int valid = num(f[3], 4096, raw);
        int inFov = num(f[4], 4096, raw);
        return new Status(mode, players, valid, inFov,
                "1".equals(f[5]), "1".equals(f[6]), "1".equals(f[7]));
    }

    private static int num(String s, int max, String raw) throws IOException {
        int v;
        try {
            v = Integer.parseInt(s);
        } catch (NumberFormatException e) {
            throw new IOException("status tidak valid: " + raw);
        }
        if (v < 0 || v > max) {
            throw new IOException("status tidak valid: " + raw);
        }
        return v;
    }
}
