package com.alokg.ffmenu;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Receives configuration from the laptop over the hotspot.
 *
 * One-way by default: the phone opens a UDP port and never sends anything, so
 * there is no connection, no DNS lookup, no TLS handshake, and nothing on the
 * wire that points back at this process. The laptop broadcasts; every phone on
 * the hotspot that knows the key can hear it.
 *
 * Packet, little-endian:
 *
 *   0..3    "FFC1"
 *   4..7    u32 seq            must increase
 *   8..15   u64 timestamp ms
 *   16..17  u16 json length    <= 1400
 *   18..    json
 *   +32     HMAC-SHA256(key, bytes[0 .. 18+len))
 *
 * The key is a shared secret, not a password: without it the packet is
 * rejected before it is parsed, so a stranger on the same WiFi cannot move
 * your settings, and a replayed packet cannot be re-applied because the
 * sequence number must increase.
 */
final class LanConfig extends Thread {

    private static final byte[] MAGIC = {'F', 'F', 'C', '1'};
    private static final int HDR = 18;
    private static final int MAC = 32;
    private static final int MAX_JSON = 1400;

    interface Sink {
        void onConfig(JSONObject o, int seq, int watches);
        void onStatus(String line);
    }

    /** Reads memory for the watch list. Supplied by the activity. */
    interface Mem {
        byte[] read(long addr, int len) throws IOException;
    }

    private final Config cfg;
    private final Sink sink;
    private volatile Mem mem;
    private volatile boolean running = true;
    private DatagramSocket sock;

    LanConfig(Config cfg, Sink sink) {
        this.cfg = cfg;
        this.sink = sink;
        setDaemon(true);
        setName("cfg");
    }

    void setMem(Mem m) {
        this.mem = m;
    }

    @Override
    public void run() {
        try {
            sock = new DatagramSocket(null);
            sock.setReuseAddress(true);
            sock.bind(new InetSocketAddress(Config.PORT));
            sink.onStatus("config: listening udp/" + Config.PORT);
        } catch (IOException e) {
            sink.onStatus("config: cannot bind udp/" + Config.PORT + ": " + e.getMessage());
            return;
        }

        byte[] buf = new byte[2048];
        while (running) {
            DatagramPacket p = new DatagramPacket(buf, buf.length);
            try {
                sock.receive(p);
            } catch (IOException e) {
                if (running) sink.onStatus("config: " + e.getMessage());
                break;
            }
            try {
                handle(p);
            } catch (Exception e) {
                sink.onStatus("config: dropped packet (" + e.getMessage() + ")");
            }
        }
        if (sock != null) sock.close();
    }

    private void handle(DatagramPacket p) throws Exception {
        byte[] d = p.getData();
        int n = p.getLength();
        if (n < HDR + MAC) throw new Exception("short");

        for (int i = 0; i < 4; i++) {
            if (d[i] != MAGIC[i]) throw new Exception("magic");
        }

        ByteBuffer bb = ByteBuffer.wrap(d, 0, HDR).order(ByteOrder.LITTLE_ENDIAN);
        bb.position(4);
        int seq = bb.getInt();
        long ts = bb.getLong();
        int len = bb.getShort() & 0xFFFF;

        if (len > MAX_JSON || HDR + len + MAC > n) throw new Exception("length");
        if (Integer.compareUnsigned(seq, cfg.lastSeq) <= 0) {
            throw new Exception("replay seq " + Integer.toUnsignedString(seq));
        }

        byte[] expect = mac(d, HDR + len);
        int diff = 0;
        for (int i = 0; i < MAC; i++) diff |= expect[i] ^ d[HDR + len + i];
        if (diff != 0) throw new Exception("bad key");

        String json = new String(d, HDR, len, StandardCharsets.UTF_8);
        JSONObject o = new JSONObject(json);

        int watches = cfg.apply(o);
        cfg.lastSeq = seq;
        cfg.lastPacketAt = System.currentTimeMillis();
        sink.onConfig(o, seq, watches);

        if (cfg.reply && o.has("reply_to")) {
            sendReply(o.getJSONObject("reply_to"), seq, ts);
        }
    }

    /** Only used while hunting offsets; off by default. */
    private void sendReply(JSONObject to, int seq, long ts) throws Exception {
        Mem m = mem;
        if (m == null) return;

        JSONArray arr = new JSONArray();
        for (Config.Watch w : cfg.watch) {
            int size = sizeOf(w.type);
            try {
                byte[] b = m.read(w.addr, size);
                w.value = format(b, w.type);
            } catch (IOException e) {
                w.value = "err";
            }
            JSONObject e = new JSONObject();
            e.put("label", w.label);
            e.put("addr", "0x" + Long.toHexString(w.addr));
            e.put("type", w.type);
            e.put("value", w.value);
            arr.put(e);
        }

        JSONObject body = new JSONObject();
        body.put("seq", seq);
        body.put("ts", ts);
        body.put("watch", arr);

        byte[] json = body.toString().getBytes(StandardCharsets.UTF_8);
        if (json.length > MAX_JSON) return;

        ByteBuffer out = ByteBuffer.allocate(HDR + json.length + MAC)
                .order(ByteOrder.LITTLE_ENDIAN);
        out.put(MAGIC).putInt(seq).putLong(ts).putShort((short) json.length).put(json);
        byte[] mac = mac(out.array(), HDR + json.length);
        out.put(mac);

        String host = to.optString("host", "");
        int port = to.optInt("port", Config.PORT);
        if (host.isEmpty()) return;
        SocketAddress dst = new InetSocketAddress(host, port);
        sock.send(new DatagramPacket(out.array(), out.array().length, dst));
    }

    private static byte[] mac(byte[] data, int len) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(Config.KEY, "HmacSHA256"));
        mac.update(data, 0, len);
        return mac.doFinal();
    }

    static int sizeOf(String type) {
        switch (type) {
            case "u8":   return 1;
            case "u16":  return 2;
            case "u64":  return 8;
            case "vec3": return 12;
            case "f32":
            case "u32":
            default:     return 4;
        }
    }

    static String format(byte[] b, String type) {
        ByteBuffer bb = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);
        switch (type) {
            case "u8":  return Integer.toString(bb.get() & 0xFF);
            case "u16": return Integer.toString(bb.getShort() & 0xFFFF);
            case "u64": return Long.toUnsignedString(bb.getLong());
            case "f32": return Float.toString(bb.getFloat());
            case "vec3": {
                float x = bb.getFloat(), y = bb.getFloat(), z = bb.getFloat();
                return String.format(java.util.Locale.US, "%.2f,%.2f,%.2f", x, y, z);
            }
            case "u32":
            default: {
                int v = bb.getInt();
                return Integer.toUnsignedString(v) + " (0x" + Integer.toHexString(v) + ")";
            }
        }
    }

    void shutdown() {
        running = false;
        if (sock != null) sock.close();
    }
}
