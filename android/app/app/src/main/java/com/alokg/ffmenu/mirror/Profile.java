package com.alokg.ffmenu.mirror;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The panel's settings, kept on the phone.
 *
 * <p>Without this, "set it up once" is a lie: the panel is a window, and a
 * window that is dismissed forgets everything. The buyer would re-tap eleven
 * switches every single session, which is exactly the kind of work nobody
 * asked for. So the state lives in a file, and the panel puts it back on the
 * module the first time the module answers.
 *
 * <p>The format is one line of plain text:
 *
 * <pre>
 *   v1 fov=60 on=1,2,5,9
 * </pre>
 *
 * <p>Plain text on purpose. {@code org.json} would work, but this class has no
 * dependencies at all, so the same arithmetic can be compiled and run on the
 * desktop by the test suite - and a settings file is not a place to discover a
 * parser bug on a phone.
 *
 * <p>Everything about the parsing is tolerant: an unknown version, a stray
 * token, a field from an older build - all ignored, and the rest is kept. A
 * settings file that cannot be read must never stop the app from starting.
 */
public final class Profile {

    public static final int DEFAULT_FOV = 60;
    public static final int FOV_MIN = 10;
    public static final int FOV_MAX = 90;

    private static final String VERSION = "v1";

    private final Map<Integer, Boolean> on = new LinkedHashMap<>();
    private int fov = DEFAULT_FOV;

    public Profile() {
    }

    // ---- reading and writing ------------------------------------------------

    /** Parses whatever is in the file, keeping every field it can trust. */
    public static Profile of(String text) {
        Profile p = new Profile();        if (text == null) {
            return p;
        }
        for (String token : text.trim().split("\\s+")) {
            int eq = token.indexOf('=');
            if (eq <= 0 || eq == token.length() - 1) {
                continue;
            }
            String key = token.substring(0, eq);
            String value = token.substring(eq + 1);
            if ("fov".equals(key)) {
                p.fov(parseInt(value, DEFAULT_FOV));
            } else if ("on".equals(key)) {
                for (String n : value.split(",")) {
                    int item = parseInt(n, -1);
                    if (item > 0) {
                        p.on.put(item, Boolean.TRUE);
                    }
                }
            }
            // The version token (if any) is deliberately not checked: a file
            // written by an older build is read for what it does contain
            // rather than thrown away for what it does not.
        }
        return p;
    }

    public String text() {
        StringBuilder sb = new StringBuilder(VERSION);
        sb.append(" fov=").append(fov);
        sb.append(" on=");
        boolean first = true;
        for (Map.Entry<Integer, Boolean> e : on.entrySet()) {
            if (!Boolean.TRUE.equals(e.getValue())) {
                continue;
            }
            if (!first) {
                sb.append(',');
            }
            sb.append(e.getKey());
            first = false;
        }
        return sb.toString();
    }

    // ---- the settings -------------------------------------------------------

    public boolean isOn(int item) {
        return Boolean.TRUE.equals(on.get(item));
    }

    public void set(int item, boolean value) {
        if (item <= 0) {
            return;
        }
        on.put(item, value);
    }

    /** Only the items that are switched on, in the order they were added. */
    public List<Integer> enabled() {
        List<Integer> out = new ArrayList<>();
        for (Map.Entry<Integer, Boolean> e : on.entrySet()) {
            if (Boolean.TRUE.equals(e.getValue())) {
                out.add(e.getKey());
            }
        }
        return out;
    }

    public int fov() {
        return fov;
    }

    public void fov(int value) {
        fov = Math.max(FOV_MIN, Math.min(FOV_MAX, value));
    }

    // ---- helpers ------------------------------------------------------------

    private static int parseInt(String s, int fallback) {
        try {
            return Integer.parseInt(s.trim());
        } catch (Exception e) {
            return fallback;
        }
    }
}
