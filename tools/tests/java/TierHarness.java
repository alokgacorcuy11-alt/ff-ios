import com.alokg.ffmenu.device.Plan;
import com.alokg.ffmenu.device.Tier;

/**
 * Drives the compatibility engine from a command line, so the profile a phone
 * gets and the checklist a brand gets can be tested here instead of trusted.
 *
 * <p>Usage:
 *   TierHarness tier    &lt;api&gt; &lt;ramMb&gt; &lt;cores&gt; &lt;maxKhz&gt;
 *   TierHarness capture &lt;api&gt; &lt;ramMb&gt; &lt;cores&gt; &lt;maxKhz&gt; &lt;w&gt; &lt;h&gt;
 *   TierHarness plan    &lt;brand&gt; &lt;api&gt;
 *   TierHarness brand   &lt;manufacturer&gt; &lt;brandProp&gt;
 */
public final class TierHarness {

    public static void main(String[] args) {
        if (args.length < 2) {
            System.out.println("usage: tier|capture|plan|brand ...");
            return;
        }
        switch (args[0]) {
            case "tier": {
                Tier t = tier(args);
                System.out.println(t.name() + " " + t.capturePct + " " + t.fps
                        + " " + t.stride + " " + t.assist);
                break;
            }
            case "capture": {
                Tier t = tier(args);
                int[] s = t.captureSize(num(args, 5), num(args, 6));
                System.out.println(s[0] + " " + s[1]);
                break;
            }
            case "plan": {
                StringBuilder sb = new StringBuilder();
                for (Plan.Step s : Plan.forDevice(args[1], num(args, 2))) {
                    if (sb.length() > 0) {
                        sb.append(',');
                    }
                    sb.append(s.id).append(s.optional ? "(opt)" : "");
                }
                System.out.println(sb);
                break;
            }
            case "brand":
                System.out.println(Plan.brandKey(args[1], args.length > 2 ? args[2] : ""));
                break;
            default:
                System.out.println("?");
        }
    }

    private static Tier tier(String[] a) {
        return Tier.of(num(a, 1), num(a, 2), num(a, 3), num(a, 4));
    }

    private static int num(String[] a, int i) {
        return i < a.length ? Integer.parseInt(a[i]) : 0;
    }
}
