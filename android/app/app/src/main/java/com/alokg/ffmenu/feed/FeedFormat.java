package com.alokg.ffmenu.feed;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.security.MessageDigest;
import java.util.Arrays;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * The container format, and the client that pulls one off loopback.
 *
 * <p>Deliberately free of Android imports: it compiles on a desktop JDK, so the
 * wire format can be pinned against the Python forge in tools/payload_forge.py
 * instead of discovered in production.
 *
 * <pre>
 * container = MAGIC(4) | version(1) | nonce(12) | padlen(u16le) | moduleLen(u32le)
 *             | ciphertext(padlen + moduleLen) | tag(32)
 *
 * encKey       = HMAC-SHA256(master, "xyz-enc")
 * macKey       = HMAC-SHA256(master, "xyz-mac")
 * keystream[i] = HMAC-SHA256(encKey, nonce | u32le(i))
 * tag          = HMAC-SHA256(macKey, container[0 .. tagStart] | name)
 * </pre>
 *
 * <p>Encrypt-then-MAC, verified before anything is decrypted. The name is part
 * of the tag so a container cannot be replayed under a different install.
 */
public final class FeedFormat {

    public static final byte[] MAGIC = {'X', 'Y', 'Z', 'F'};
    public static final int VERSION = 1;
    public static final int NONCE_LEN = 12;
    public static final int TAG_LEN = 32;
    public static final int HEADER_LEN = 4 + 1 + NONCE_LEN + 2 + 4;

    private FeedFormat() {
    }

    // ---- primitive helpers -------------------------------------------------

    static byte[] hmac(byte[] key, byte[]... parts) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            for (byte[] p : parts) {
                mac.update(p);
            }
            return mac.doFinal();
        } catch (Exception e) {
            throw new IllegalStateException("HmacSHA256 unavailable", e);
        }
    }

    static byte[] concat(byte[]... parts) {
        int n = 0;
        for (byte[] p : parts) {
            n += p.length;
        }
        byte[] out = new byte[n];
        int o = 0;
        for (byte[] p : parts) {
            System.arraycopy(p, 0, out, o, p.length);
            o += p.length;
        }
        return out;
    }

    static void putU32(byte[] dst, int off, int v) {
        dst[off] = (byte) v;
        dst[off + 1] = (byte) (v >>> 8);
        dst[off + 2] = (byte) (v >>> 16);
        dst[off + 3] = (byte) (v >>> 24);
    }

    static void putU16(byte[] dst, int off, int v) {
        dst[off] = (byte) v;
        dst[off + 1] = (byte) (v >>> 8);
    }

    static int u32(byte[] src, int off) {
        return (src[off] & 0xff)
                | ((src[off + 1] & 0xff) << 8)
                | ((src[off + 2] & 0xff) << 16)
                | ((src[off + 3] & 0xff) << 24);
    }

    static int u16(byte[] src, int off) {
        return (src[off] & 0xff) | ((src[off + 1] & 0xff) << 8);
    }

    public static byte[] deriveEnc(byte[] master) {
        return hmac(master, "xyz-enc".getBytes());
    }

    public static byte[] deriveMac(byte[] master) {
        return hmac(master, "xyz-mac".getBytes());
    }

    public static byte[] keystream(byte[] encKey, byte[] nonce, int length) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(length + 32);
        int i = 0;
        while (out.size() < length) {
            byte[] ctr = new byte[4];
            putU32(ctr, 0, i);
            out.write(hmac(encKey, nonce, ctr), 0, 32);
            i++;
        }
        byte[] all = out.toByteArray();
        return Arrays.copyOf(all, length);
    }

    // ---- container ---------------------------------------------------------

    /** Verifies the tag first, then decrypts, then strips the padding. */
    public static byte[] open(byte[] container, byte[] master, String name)
            throws IOException {
        if (container == null || master == null || name == null) {
            throw new IOException("feed: missing input");
        }
        if (container.length < HEADER_LEN + TAG_LEN) {
            throw new IOException("feed: container too short");
        }
        if (!Arrays.equals(Arrays.copyOfRange(container, 0, 4), MAGIC)) {
            throw new IOException("feed: bad magic");
        }
        if ((container[4] & 0xff) != VERSION) {
            throw new IOException("feed: unsupported version " + container[4]);
        }

        int bodyLen = container.length - TAG_LEN;
        byte[] body = Arrays.copyOfRange(container, 0, bodyLen);
        byte[] tag = Arrays.copyOfRange(container, bodyLen, container.length);
        byte[] macKey = deriveMac(master);
        byte[] want = hmac(macKey, body, name.getBytes());
        if (!MessageDigest.isEqual(want, tag)) {
            throw new IOException("feed: authentication failed");
        }

        int padLen = u16(container, 17);
        int moduleLen = u32(container, 19);
        byte[] nonce = Arrays.copyOfRange(container, 5, 17);
        byte[] ct = Arrays.copyOfRange(container, HEADER_LEN, bodyLen);
        if (ct.length != padLen + moduleLen) {
            throw new IOException("feed: length mismatch");
        }

        byte[] ks = keystream(deriveEnc(master), nonce, ct.length);
        byte[] plain = new byte[ct.length];
        for (int i = 0; i < ct.length; i++) {
            plain[i] = (byte) (ct[i] ^ ks[i]);
        }
        Arrays.fill(ks, (byte) 0);
        return Arrays.copyOfRange(plain, padLen, padLen + moduleLen);
    }

    // ---- client ------------------------------------------------------------

    /**
     * One round trip against a {@link FeedServer}: challenge, response, verify,
     * decrypt. Returns the module bytes; nothing is written to disk.
     */
    public static byte[] fetch(String host, int port, byte[] master, String name,
                               int timeoutMs) throws IOException {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), timeoutMs);
            s.setSoTimeout(timeoutMs);
            InputStream in = s.getInputStream();
            OutputStream out = s.getOutputStream();

            byte[] nonce = new byte[16];
            new java.security.SecureRandom().nextBytes(nonce);
            out.write(nonce);
            out.flush();

            DataInputStream din = new DataInputStream(in);
            byte[] magic = new byte[4];
            din.readFully(magic);
            if (!Arrays.equals(magic, MAGIC)) {
                throw new IOException("feed: server did not answer in this protocol");
            }
            int ver = din.readUnsignedByte();
            if (ver != VERSION) {
                throw new IOException("feed: server version " + ver);
            }
            int size = Integer.reverseBytes(din.readInt());
            if (size < HEADER_LEN + TAG_LEN || size > (64 << 20)) {
                throw new IOException("feed: implausible size " + size);
            }
            byte[] tag = new byte[32];
            din.readFully(tag);
            byte[] container = new byte[size];
            din.readFully(container);

            byte[] want = hmac(deriveMac(master), nonce, container);
            if (!MessageDigest.isEqual(want, tag)) {
                throw new IOException("feed: response is not from this install");
            }
            return open(container, master, name);
        } finally {
            try {
                s.close();
            } catch (IOException ignored) {
            }
        }
    }
}