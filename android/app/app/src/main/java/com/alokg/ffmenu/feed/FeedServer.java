package com.alokg.ffmenu.feed;

import java.io.Closeable;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * Serves one container over loopback, on an ephemeral port, from memory.
 *
 * <p>Why this and not a file: the XYZ build dropped its payload into
 * {@code /Android/data/com.dts.freefireth/files}, which is the one directory
 * the game can list without asking anyone, and used the same bytes for every
 * user of the build. A single hash match then retired all of them. Here the
 * container exists only in this process's heap, is different for every
 * install, and is handed straight to the client.
 *
 * <p>Nothing binds to a public interface: {@link InetAddress#getLoopbackAddress()}
 * keeps it unreachable from the network, and the response is authenticated
 * with a key only this install's client knows, so a squatter on the port can
 * neither read nor answer for us.
 */
public final class FeedServer implements Closeable {

    public static final int MAX_CONTAINER = 64 << 20;

    private final ServerSocket server;
    private final byte[] container;
    private final byte[] macKey;
    private volatile boolean closed;

    private FeedServer(ServerSocket server, byte[] container, byte[] master) {
        this.server = server;
        this.container = container;
        this.macKey = FeedFormat.deriveMac(master);
    }

    /** Binds loopback on a free port. Nothing is sent until {@link #serveOnce}. */
    public static FeedServer start(byte[] container, byte[] master) throws IOException {
        return start(container, master, 0);
    }

    /**
     * Binds loopback on a specific port. The client is built with the port
     * baked in, so the panel has to meet it exactly; port 0 means "any".
     */
    public static FeedServer start(byte[] container, byte[] master, int port)
            throws IOException {
        if (container == null || container.length < FeedFormat.HEADER_LEN + FeedFormat.TAG_LEN) {
            throw new IOException("feed: container too short");
        }
        if (container.length > MAX_CONTAINER) {
            throw new IOException("feed: container too large");
        }
        ServerSocket ss = new ServerSocket();
        ss.setReuseAddress(true);
        try {
            ss.bind(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 4);
        } catch (IOException e) {
            ss.close();
            throw new IOException("feed: cannot bind loopback:" + port
                    + " (" + e.getMessage() + ")", e);
        }
        return new FeedServer(ss, container, master);
    }

    public int port() {
        return server.getLocalPort();
    }

    public boolean isClosed() {
        return closed;
    }

    /**
     * Accepts one client and hands over the container, leaving the socket open
     * so the caller can keep talking on it. Returns null if nobody connected
     * inside the timeout.
     */
    public Socket serveAndReturn(int timeoutMs) throws IOException {
        if (closed) {
            throw new IOException("feed: server closed");
        }
        server.setSoTimeout(Math.max(1, timeoutMs));
        Socket s;
        try {
            s = server.accept();
        } catch (java.net.SocketTimeoutException e) {
            return null;
        }
        try {
            s.setSoTimeout(Math.max(1, timeoutMs));
            DataInputStream in = new DataInputStream(s.getInputStream());
            OutputStream out = s.getOutputStream();

            byte[] nonce = new byte[16];
            in.readFully(nonce);

            byte[] tag = FeedFormat.hmac(macKey, nonce, container);
            byte[] head = new byte[9];
            System.arraycopy(FeedFormat.MAGIC, 0, head, 0, 4);
            head[4] = (byte) FeedFormat.VERSION;
            FeedFormat.putU32(head, 5, container.length);

            out.write(head);
            out.write(tag);
            out.write(container);
            out.flush();
            return s;
        } catch (IOException e) {
            try {
                s.close();
            } catch (IOException ignored) {
            }
            throw e;
        }
    }

    /**
     * Accepts one client, answers its challenge, and returns whether the
     * container was handed over. A client that does not speak the protocol, or
     * connects and says nothing, is dropped without a reply.
     */
    public boolean serveOnce(int timeoutMs) throws IOException {
        Socket s = serveAndReturn(timeoutMs);
        if (s == null) {
            return false;
        }
        try {
            s.close();
        } catch (IOException ignored) {
        }
        return true;
    }

    @Override
    public void close() {
        closed = true;
        try {
            server.close();
        } catch (IOException ignored) {
        }
    }

    /** Convenience for the service: serve until one client is satisfied. */
    public boolean serveUntilDelivered(long deadlineMs) throws IOException {
        while (!closed && System.currentTimeMillis() < deadlineMs) {
            long left = deadlineMs - System.currentTimeMillis();
            try {
                if (serveOnce((int) Math.min(left, 4000))) {
                    return true;
                }
            } catch (java.net.SocketTimeoutException e) {
                // keep waiting for the client to start
            }
        }
        return false;
    }
}