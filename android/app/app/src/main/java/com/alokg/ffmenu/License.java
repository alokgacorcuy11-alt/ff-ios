package com.alokg.ffmenu;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Activation keys, checked on the device with no server round trip.
 *
 * <p>A key is 24 characters in six groups. The layout and the arithmetic are
 * duplicated in tools/keygen.py and site/functions/_lib/key.js, and the tests
 * issue keys in Python and in JavaScript and verify them here, so the three
 * cannot drift apart silently.
 *
 * <pre>
 *   alphabet   23456789ABCDEFGHJKLMNPQRSTUVWXYZ   (no 0/O/1/I)
 *   payload    16 chars = 10 bytes
 *              b0       version (high nibble) | tier (low nibble)
 *              b1..b2   expiry in days since 2026-01-01, LE. 0 = never
 *              b3..b6   device hash, LE. 0 = any device
 *              b7..b9   nonce, LE. makes every issued key a different string
 *   mac        8 chars = 5 bytes = HMAC-SHA256(secret, payload)[0:5]
 * </pre>
 *
 * <p>Honest about what this is: the secret is in the APK, so anyone who
 * decompiles it can mint keys. Offline licensing cannot be stronger than that.
 * What it does buy is that a leaked key can be narrowed to one device, and an
 * expiry can be set, without asking the network for permission mid-match.
 */
public final class License {

    // 48 chars of [A-Z2-9] = ~240 bits, no quoting or escaping hazard in Java,
    // TOML or an env var. Rotated before the first sale, so there is no build
    // in the wild signed with the old placeholder.
    //
    // This must equal LICENSE_SECRET on the Pages project. The site mints keys
    // with it; the phone verifies with it. Drift = every key sold is dead.
    public static final String SECRET = "YPZ6SPRGEEJXHF7CWXNQURYLTDY8NU487U3RP27C67QDSUXM";

    private static final String ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";
    private static final int VERSION = 2;
    private static final int PAYLOAD_CHARS = 16;
    private static final int PAYLOAD_BYTES = 10;
    private static final int MAC_CHARS = 8;
    /** 2026-01-01T00:00:00Z, the same day keygen.py counts from. */
    private static final long EPOCH_MS = 1767225600000L;

    /** The last checked key, so the panel can gate itself without re-checking. */
    private static volatile Status current = null;

    private License() {
    }

    public static final class Status {
        public final boolean ok;
        public final String reason;
        public final int tier;
        public final long expiresInDays;

        Status(boolean ok, String reason, int tier, long expiresInDays) {
            this.ok = ok;
            this.reason = reason;
            this.tier = tier;
            this.expiresInDays = expiresInDays;
        }

        @Override
        public String toString() {
            return (ok ? "key ok" : "key: " + reason)
                    + (ok && tier > 0 ? "  (tier " + tier + ")" : "")
                    + (ok && expiresInDays >= 0 ? "  " + expiresInDays + "d left" : "");
        }
    }

    public static boolean unlocked() {
        Status s = current;
        return s != null && s.ok;
    }

    public static Status current() {
        return current;
    }

    /** Once true, stays true: a key that expires mid-session does not lock the panel. */
    public static Status check(String key, String deviceId) {
        Status s = verify(key, deviceId, System.currentTimeMillis());
        if (s.ok) {
            current = s;
        }
        return s;
    }

    public static Status verify(String key, String deviceId, long nowMs) {
        if (key == null) {
            return new Status(false, "no key", 0, -1);
        }
        String body = key.replace("-", "").replace(" ", "").toUpperCase();
        if (body.length() != PAYLOAD_CHARS + MAC_CHARS) {
            return new Status(false, "a key is " + (PAYLOAD_CHARS + MAC_CHARS)
                    + " characters, this one is " + body.length(), 0, -1);
        }

        byte[] payload;
        byte[] mac;
        try {
            payload = b32(body.substring(0, PAYLOAD_CHARS), PAYLOAD_BYTES);
            mac = b32(body.substring(PAYLOAD_CHARS), 5);
        } catch (IllegalArgumentException e) {
            return new Status(false, e.getMessage(), 0, -1);
        }

        int version = (payload[0] >> 4) & 0xF;
        int tier = payload[0] & 0xF;
        if (version != VERSION) {
            return new Status(false, "unsupported version " + version, 0, -1);
        }

        byte[] want = hmac(SECRET, payload);
        if (!MessageDigest.isEqual(slice(want, 5), mac)) {
            return new Status(false, "the signature does not match", 0, -1);
        }

        // bytes 7..9 are the nonce: what makes every issued key a different
        // string. It is covered by the signature, so it cannot be edited, and
        // nothing here needs to read it - it is carried, not interpreted.
        int device = (payload[3] & 0xff)
                | ((payload[4] & 0xff) << 8)
                | ((payload[5] & 0xff) << 16)
                | ((payload[6] & 0xff) << 24);
        if (device != 0) {
            if (deviceId == null || deviceId.isEmpty()) {
                return new Status(false, "this key is bound to a device", 0, -1);
            }
            if (deviceHash(SECRET, deviceId) != device) {
                return new Status(false, "this key belongs to another device", tier, -1);
            }
        }

        int exp = (payload[1] & 0xff) | ((payload[2] & 0xff) << 8);
        long left = -1;
        if (exp != 0) {
            long days = (nowMs - EPOCH_MS) / 86400000L;
            left = exp - days;
            if (left < 0) {
                return new Status(false, "expired " + (-left) + " days ago", tier, left);
            }
        }
        return new Status(true, "ok", tier, left);
    }

    public static int deviceHash(String secret, String deviceId) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            md.update(secret.getBytes(StandardCharsets.UTF_8));
            md.update((byte) '|');
            md.update(deviceId.getBytes(StandardCharsets.UTF_8));
            byte[] d = md.digest();
            return ((d[0] & 0xff) << 24) | ((d[1] & 0xff) << 16)
                    | ((d[2] & 0xff) << 8) | (d[3] & 0xff);
        } catch (Exception e) {
            return 0;
        }
    }

    private static byte[] hmac(String secret, byte[] data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(data);
        } catch (Exception e) {
            throw new IllegalStateException("HmacSHA256 unavailable", e);
        }
    }

    private static byte[] slice(byte[] src, int n) {
        byte[] out = new byte[n];
        System.arraycopy(src, 0, out, 0, n);
        return out;
    }

    /**
     * Fixed-width base32, five bits per character, MSB first, trailing bits
     * dropped.
     *
     * <p>Deliberately not a {@code long} accumulator: the payload is 16
     * characters = 80 bits, which does not fit in a long, and a shift-based
     * version silently loses the high bits and decodes the version nibble as 0.
     * This fills the output byte by byte instead.
     */
    static byte[] b32(String text, int outLen) {
        byte[] out = new byte[outLen];
        int limit = outLen * 8;
        int bitPos = 0;
        for (int i = 0; i < text.length(); i++) {
            int idx = ALPHABET.indexOf(text.charAt(i));
            if (idx < 0) {
                throw new IllegalArgumentException("not in the alphabet: " + text.charAt(i));
            }
            for (int b = 4; b >= 0; b--) {
                int bit = (idx >>> b) & 1;
                if (bitPos < limit && bit != 0) {
                    out[bitPos >> 3] |= (byte) (1 << (7 - (bitPos & 7)));
                }
                bitPos++;
            }
        }
        return out;
    }
}