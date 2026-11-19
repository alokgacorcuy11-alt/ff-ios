package com.alokg.ffmenu.feed;

import java.io.Closeable;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.security.MessageDigest;
import java.util.Arrays;

/**
 * The command session that follows a delivery, on the same socket.
 *
 * <p>The container is delivered once; the panel and the module then talk over
 * what is already open. That matters for the same reason the container does:
 * a command file in the game's data dir would be another artefact, and a
 * second listener would be another port.
 *
 * <pre>
 * frame = type(1) | seq(u32le) | len(u32le) | payload | mac(16)
 * mac   = HMAC-SHA256(macKey, type | seq | len | payload)[0:16]
 * </pre>
 *
 * <p>{@code seq} is strictly increasing in each direction. A frame captured by
 * another app on loopback fails the counter even if it somehow had the key,
 * so a toggle cannot be replayed.
 */
public final class FeedChannel implements Closeable {

    public static final int TYPE_CMD = 1;
    public static final int TYPE_ACK = 2;

    private static final int MAC_LEN = 16;
    private static final int HEAD_LEN = 9;
    private static final int MAX_PAYLOAD = 64 * 1024;

    private final Socket socket;
    private final DataInputStream in;
    private final OutputStream out;
    private final byte[] macKey;

    private int sendSeq = 0;
    private int lastRecv = 0;

    private FeedChannel(Socket socket, byte[] macKey) throws IOException {
        this.socket = socket;
        this.macKey = macKey;
        this.in = new DataInputStream(socket.getInputStream());
        this.out = socket.getOutputStream();
    }

    public static FeedChannel attach(Socket socket, byte[] master) throws IOException {
        if (socket == null || master == null) {
            throw new IOException("feed: channel needs a socket and a key");
        }
        return new FeedChannel(socket, FeedFormat.deriveMac(master));
    }

    /** Sends one command. The frame is authenticated and sequence-numbered. */
    public void send(String json) throws IOException {
        send(TYPE_CMD, json.getBytes("UTF-8"));
    }

    public void ack(String note) throws IOException {
        send(TYPE_ACK, (note == null ? "" : note).getBytes("UTF-8"));
    }

    private void send(int type, byte[] payload) throws IOException {
        if (payload.length > MAX_PAYLOAD) {
            throw new IOException("feed: frame too large");
        }
        int seq = ++sendSeq;
        byte[] head = header(type, seq, payload.length);
        byte[] mac = FeedFormat.hmac(macKey, head, payload);
        out.write(head);
        out.write(payload);
        out.write(mac, 0, MAC_LEN);
        out.flush();
    }

    /** Blocks for the next frame. Returns null on timeout. */
    public String receive(int timeoutMs) throws IOException {
        socket.setSoTimeout(Math.max(1, timeoutMs));
        byte[] head = new byte[HEAD_LEN];
        try {
            in.readFully(head);
        } catch (java.net.SocketTimeoutException e) {
            return null;
        }

        int type = head[0] & 0xff;
        int seq = FeedFormat.u32(head, 1);
        int len = FeedFormat.u32(head, 5);
        if (len < 0 || len > MAX_PAYLOAD) {
            throw new IOException("feed: implausible frame length " + len);
        }

        byte[] payload = new byte[len];
        in.readFully(payload);
        byte[] mac = new byte[MAC_LEN];
        in.readFully(mac);

        byte[] want = FeedFormat.hmac(macKey, head, payload);
        if (!MessageDigest.isEqual(Arrays.copyOf(want, MAC_LEN), mac)) {
            throw new IOException("feed: frame failed authentication");
        }
        if (Integer.compareUnsigned(seq, lastRecv) <= 0) {
            throw new IOException("feed: replayed frame (seq " + seq + ")");
        }
        lastRecv = seq;

        if (type != TYPE_CMD && type != TYPE_ACK) {
            throw new IOException("feed: unknown frame type " + type);
        }
        return new String(payload, "UTF-8");
    }

    private static byte[] header(int type, int seq, int len) {
        byte[] head = new byte[HEAD_LEN];
        head[0] = (byte) type;
        FeedFormat.putU32(head, 1, seq);
        FeedFormat.putU32(head, 5, len);
        return head;
    }

    @Override
    public void close() {
        try {
            socket.close();
        } catch (IOException ignored) {
        }
    }
}