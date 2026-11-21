import com.alokg.ffmenu.mirror.Profile;

/**
 * Drives Profile.java from the command line so the test suite can check it.
 *
 * <p>Profile has no Android imports and no dependencies, which is the whole
 * reason it can be run here instead of only compiled: a parser that is only
 * exercised on a phone is a parser nobody has tested.
 */
public final class ProfileHarness {

    public static void main(String[] args) {
        if (args.length == 0) {
            System.out.println("usage: text|on|enabled|fov|set|clamp ...");
            return;
        }
        String cmd = args[0];
        String text = args.length > 1 ? args[1] : "";

        if ("clamp".equals(cmd)) {
            Profile p = new Profile();
            p.fov(Integer.parseInt(text));
            System.out.println(p.fov());
            return;
        }

        Profile p = Profile.of(text);

        switch (cmd) {
            case "text":
                System.out.println(p.text());
                break;
            case "on":
                System.out.println(p.isOn(Integer.parseInt(args[2])) ? "1" : "0");
                break;
            case "enabled": {
                StringBuilder sb = new StringBuilder();
                for (int item : p.enabled()) {
                    if (sb.length() > 0) {
                        sb.append(',');
                    }
                    sb.append(item);
                }
                System.out.println(sb);
                break;
            }
            case "fov":
                System.out.println(p.fov());
                break;
            case "set":
                p.set(Integer.parseInt(args[2]), "1".equals(args[3]));
                System.out.println(p.text());
                break;
            default:
                System.out.println("?");
        }
    }
}
