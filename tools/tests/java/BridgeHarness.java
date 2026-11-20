import com.alokg.ffmenu.mirror.Bridge;

/**
 * Drives {@link Bridge} against a real socket, for tools/tests/test_bridge.py.
 * Not shipped in the APK.
 *
 * <pre>
 *   BridgeHarness labels             item numbers and their panel labels
 *   BridgeHarness origin             the header the module insists on
 *   BridgeHarness alive  &lt;port&gt;      true / false
 *   BridgeHarness cmd    &lt;port&gt; &lt;n&gt; &lt;on|off&gt;
 *   BridgeHarness fov    &lt;port&gt; &lt;n&gt;
 *   BridgeHarness status &lt;port&gt;      the parsed line
 * </pre>
 *
 * <p>On an IOException it prints {@code ERR <message>} and exits 3, so the
 * Python side can tell a refusal from a crash.
 */
public final class BridgeHarness {

    public static void main(String[] args) {
        if (args.length == 0) {
            System.out.println("ERR no mode");
            System.exit(2);
        }
        String mode = args[0];

        if ("labels".equals(mode)) {
            for (int item : Bridge.ITEMS) {
                System.out.println(item + " " + Bridge.label(item));
            }
            return;
        }
        if ("origin".equals(mode)) {
            System.out.println(Bridge.ORIGIN);
            return;
        }
        if ("verdict".equals(mode)) {
            // verdict <fileThere 0|1> <size> <alive 0|1>
            System.out.println(Bridge.verdict("1".equals(args[1]),
                    Long.parseLong(args[2]), "1".equals(args[3])));
            return;
        }

        Bridge b = new Bridge("127.0.0.1", Integer.parseInt(args[1]), 2000);
        try {
            switch (mode) {
                case "alive":
                    System.out.println(b.alive());
                    return;
                case "cmd":
                    System.out.println(b.command(Integer.parseInt(args[2]),
                            "on".equals(args[3])));
                    return;
                case "fov":
                    System.out.println(b.fov(Integer.parseInt(args[2])));
                    return;
                case "status":
                    System.out.println(b.status());
                    return;
                default:
                    System.out.println("ERR unknown mode " + mode);
                    System.exit(2);
            }
        } catch (IllegalArgumentException e) {
            System.out.println("BADARG " + e.getMessage());
            System.exit(4);
        } catch (Exception e) {
            System.out.println("ERR " + e.getMessage());
            System.exit(3);
        }
    }
}
