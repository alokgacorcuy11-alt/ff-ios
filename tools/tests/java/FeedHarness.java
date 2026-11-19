import com.alokg.ffmenu.feed.FeedFormat;
import com.alokg.ffmenu.feed.FeedServer;

import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.MessageDigest;

/**
 * Test harness for the feed protocol, not shipped in the APK.
 *
 * <pre>
 *   serve &lt;container&gt; &lt;keyHex&gt; &lt;name&gt; [timeoutMs]   -> PORT n / SERVED
 *   open  &lt;container&gt; &lt;keyHex&gt; &lt;name&gt;               -> SHA / LEN
 *   fetch &lt;container&gt; &lt;keyHex&gt; &lt;name&gt; &lt;port&gt;        -> SHA / LEN
 * </pre>
 */
public final class FeedHarness {

    public static void main(String[] args) throws Exception {
        String mode = args[0];
        byte[] container = Files.readAllBytes(Paths.get(args[1]));
        byte[] master = hex(args[2]);
        String name = args[3];

        if ("serve".equals(mode)) {
            FeedServer s = FeedServer.start(container, master);
            System.out.println("PORT " + s.port());
            System.out.flush();
            int timeout = args.length > 4 ? Integer.parseInt(args[4]) : 20000;
            boolean ok = s.serveUntilDelivered(System.currentTimeMillis() + timeout);
            System.out.println(ok ? "SERVED" : "TIMEOUT");
            s.close();
            System.exit(ok ? 0 : 3);

        } else if ("open".equals(mode)) {
            report(FeedFormat.open(container, master, name));

        } else if ("fetch".equals(mode)) {
            report(FeedFormat.fetch("127.0.0.1", Integer.parseInt(args[4]), master, name, 8000));

        } else if ("session".equals(mode)) {
            int count = Integer.parseInt(args[4]);
            FeedServer s = FeedServer.start(container, master, 0);
            System.out.println("PORT " + s.port());
            System.out.flush();
            java.net.Socket sock = s.serveAndReturn(15000);
            if (sock == null) {
                System.out.println("TIMEOUT");
                System.exit(3);
            }
            System.out.println("SERVED");
            System.out.flush();
            com.alokg.ffmenu.feed.FeedChannel ch =
                    com.alokg.ffmenu.feed.FeedChannel.attach(sock, master);
            try {
                for (int i = 0; i < count; i++) {
                    String cmd = ch.receive(8000);
                    if (cmd == null) {
                        System.out.println("TIMEOUT");
                        System.exit(3);
                    }
                    System.out.println("CMD " + cmd);
                    System.out.flush();
                    ch.ack("ok");
                }
            } catch (Exception e) {
                System.out.println("ERR " + e.getMessage());
                System.out.flush();
                ch.close();
                s.close();
                System.exit(4);
            }
            ch.close();
            s.close();
            System.out.println("DONE");

        } else {
            System.out.println("UNKNOWN " + mode);
            System.exit(2);
        }
    }

    private static void report(byte[] module) throws Exception {
        System.out.println("SHA " + hex(MessageDigest.getInstance("SHA-256").digest(module)));
        System.out.println("LEN " + module.length);
    }

    private static byte[] hex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    private static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder(b.length * 2);
        for (byte x : b) {
            sb.append(String.format("%02x", x & 0xff));
        }
        return sb.toString();
    }
}