package com.alokg.ffmenu;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/**
 * Client for the root-side daemon (android/daemon/memd.c).
 *
 * Wire format, little-endian, packed:
 *   req { u32 cmd; u32 pid; u32 len; u32 nameLen; u64 addr; char name[64]; }
 *   rsp { u32 status; u32 len; } + len bytes
 */
final class MemClient implements Closeable {

    static final String SOCKET = "ffs.memd.v1";

    static final int CMD_PING  = 1;
    static final int CMD_READ  = 2;
    static final int CMD_WRITE = 3;
    static final int CMD_BASE  = 4;
    static final int CMD_FIND  = 5;
    static final int CMD_MAPS  = 6;
    static final int CMD_SHUTDOWN = 7;

    private static final int REQ_SIZE = 88;   // 4+4+4+4+8+64, packed

    private final LocalSocket sock;
    private final DataInputStream in;
    private final OutputStream out;
    private final ByteBuffer req = ByteBuffer.allocate(REQ_SIZE).order(ByteOrder.LITTLE_ENDIAN);

    MemClient() throws IOException {
        sock = new LocalSocket();
        sock.connect(new LocalSocketAddress(SOCKET, LocalSocketAddress.Namespace.ABSTRACT));
        in = new DataInputStream(new BufferedInputStream(sock.getInputStream()));
        out = new BufferedOutputStream(sock.getOutputStream());
    }

    private synchronized byte[] call(int cmd, int pid, long addr, int len, String name)
            throws IOException {
        byte[] nb = name == null ? new byte[0]
                : name.getBytes(StandardCharsets.US_ASCII);
        int nl = Math.min(nb.length, 63);

        req.clear();
        req.putInt(cmd);
        req.putInt(pid);
        req.putInt(len);
        req.putInt(nl);
        req.putLong(addr);
        req.put(nb, 0, nl);
        for (int i = nl; i < 64; i++) req.put((byte) 0);

        out.write(req.array(), 0, REQ_SIZE);
        out.flush();

        int status = readIntLE();
        int rlen = readIntLE();
        if (status != 0) throw new IOException("daemon status " + status);
        if (rlen < 0 || rlen > (1 << 20)) throw new IOException("bad length " + rlen);

        byte[] data = new byte[rlen];
        in.readFully(data);
        return data;
    }

    /** The daemon is little-endian; DataInputStream.readInt() is not. */
    private int readIntLE() throws IOException {
        byte[] b = new byte[4];
        in.readFully(b);
        return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    /** Returns the daemon's pid, so it can be killed without matching a cmdline. */
    int ping() throws IOException {
        byte[] d = call(CMD_PING, 0, 0, 0, null);
        if (d.length < 4) return 0;
        return ByteBuffer.wrap(d).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    /** Asks the daemon to exit. The socket is the only handle: no pid file. */
    void shutdown() throws IOException {
        call(CMD_SHUTDOWN, 0, 0, 0, null);
    }

    int find(String cmdlineNeedle) throws IOException {
        byte[] d = call(CMD_FIND, 0, 0, 0, cmdlineNeedle);
        if (d.length < 4) return 0;
        return ByteBuffer.wrap(d).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    long base(int pid, String module) throws IOException {
        byte[] d = call(CMD_BASE, pid, 0, 0, module);
        if (d.length < 8) return 0;
        return ByteBuffer.wrap(d).order(ByteOrder.LITTLE_ENDIAN).getLong();
    }

    byte[] read(int pid, long addr, int len) throws IOException {
        return call(CMD_READ, pid, addr, len, null);
    }

    String maps(int pid) throws IOException {
        byte[] d = call(CMD_MAPS, pid, 0, 1 << 20, null);
        return new String(d, StandardCharsets.UTF_8);
    }

    synchronized void write(int pid, long addr, byte[] data) throws IOException {
        byte[] nb = new byte[0];
        req.clear();
        req.putInt(CMD_WRITE);
        req.putInt(pid);
        req.putInt(data.length);
        req.putInt(0);
        req.putLong(addr);
        req.put(nb);
        for (int i = 0; i < 64; i++) req.put((byte) 0);

        out.write(req.array(), 0, REQ_SIZE);
        out.write(data);
        out.flush();

        int status = readIntLE();
        int rlen = readIntLE();
        if (status != 0) throw new IOException("write status " + status);
        if (rlen > 0) in.skipBytes(rlen);
    }

    /** Convenience: little-endian u32 at an address. */
    int u32(int pid, long addr) throws IOException {
        byte[] b = read(pid, addr, 4);
        if (b.length != 4) throw new EOFException("short read at 0x" + Long.toHexString(addr));
        return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    long u64(int pid, long addr) throws IOException {
        byte[] b = read(pid, addr, 8);
        if (b.length != 8) throw new EOFException("short read at 0x" + Long.toHexString(addr));
        return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getLong();
    }

    @Override
    public void close() {
        try { sock.close(); } catch (IOException ignored) { }
    }
}
