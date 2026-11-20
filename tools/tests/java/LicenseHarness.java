import com.alokg.ffmenu.License;

/**
 * Test harness for offline key verification, not shipped in the APK.
 *
 * <pre>
 *   LicenseHarness secret              print the embedded secret
 *   LicenseHarness device &lt;id&gt;         print the device hash for an id
 *   LicenseHarness verify &lt;key&gt; &lt;id&gt;    OK / BAD with the reason
 * </pre>
 *
 * <p>The secret is printed deliberately: the Python side issues keys with it, so
 * if the constant here ever changes, tools/tests/test_license.py stops agreeing
 * with it instead of shipping keys that no build accepts.
 */
public final class LicenseHarness {

    public static void main(String[] args) {
        String mode = args[0];
        if ("secret".equals(mode)) {
            System.out.println(License.SECRET);
            return;
        }
        if ("device".equals(mode)) {
            System.out.println(License.deviceHash(License.SECRET, args[1]));
            return;
        }
        if ("verify".equals(mode)) {
            String key = args[1];
            String device = args.length > 2 ? args[2] : "";
            License.Status s = License.verify(key, device, System.currentTimeMillis());
            if (s.ok) {
                System.out.println("OK " + s.tier + " " + s.expiresInDays);
            } else {
                System.out.println("BAD " + s.reason);
            }
            return;
        }
        System.out.println("UNKNOWN " + mode);
        System.exit(2);
    }
}