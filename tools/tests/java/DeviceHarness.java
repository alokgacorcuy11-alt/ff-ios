import com.alokg.roottools.Device;
import com.alokg.roottools.Guide;

/**
 * Drives Device and Guide from the command line, so the decision a buyer sees
 * can be tested here instead of trusted.
 *
 * <p>Usage:
 *   DeviceHarness verdict &lt;unlock_supported&gt; &lt;flash_locked&gt; &lt;verifiedboot&gt;
 *   DeviceHarness brand &lt;manufacturer&gt;
 *   DeviceHarness guide &lt;brand&gt;
 *   DeviceHarness afterroot
 */
public final class DeviceHarness {

    public static void main(String[] args) {
        if (args.length == 0) {
            System.out.println("usage: verdict|brand|guide|afterroot ...");
            return;
        }
        switch (args[0]) {
            case "verdict": {
                Device d = Device.of(
                        args.length > 4 ? args[4] : "Xiaomi",
                        "model", "9", "arm64-v8a",
                        arg(args, 1), "", arg(args, 2), arg(args, 3), "0");
                System.out.println(d.verdict());
                System.out.println(d.bootloaderUnlocked() ? "UNLOCKED" : "LOCKED");
                break;
            }
            case "brand":
                System.out.println(Device.of(arg(args, 1), "m", "9", "arm64-v8a",
                        "", "", "", "", "0").brand());
                break;
            case "guide":
                System.out.println(Guide.forBrand(arg(args, 1)).length() > 200 ? "LONG" : "SHORT");
                break;
            case "guidebody":
                System.out.println(Guide.forBrand(arg(args, 1)));
                break;
            case "afterroot":
                System.out.println(Guide.afterRoot());
                break;
            default:
                System.out.println("?");
        }
    }

    private static String arg(String[] a, int i) {
        return i < a.length ? a[i] : "";
    }
}
