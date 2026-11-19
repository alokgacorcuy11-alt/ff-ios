package com.alokg.ffmenu;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Live configuration, pushed from the laptop over the hotspot.
 *
 * Everything the reader and the overlay will consume lives here, so a setting
 * change never needs a rebuild or even a reconnect: the packet arrives, the
 * fields are replaced, the next frame reads the new values.
 *
 * The watch list is the exception that makes this useful before the overlay
 * exists: point it at an address, and the app reads it at 10 Hz and shows the
 * value. That is how offsets get confirmed from the laptop instead of by
 * rebuilding the app for every guess.
 */
final class Config {

    /**
     * Shared secret. Must be the same bytes in tools/lan_config.py.
     * Change it - anyone on the same hotspot who knows it can drive the app.
     */
    static final byte[] KEY =
            "ganti-ini-32-byte-rahasia-anda!!".getBytes(java.nio.charset.StandardCharsets.UTF_8);

    static final int PORT = 47811;

    static final class Watch {
        final long addr;
        final String type;      // u8 u16 u32 u64 f32 vec3
        final String label;
        volatile String value = "-";

        Watch(long addr, String type, String label) {
            this.addr = addr;
            this.type = type;
            this.label = label;
        }
    }

    // --- features (consumed by the reader/overlay, stage 2+)
    volatile boolean espBox = true;
    volatile boolean espLine = true;
    volatile boolean espName = true;
    volatile boolean espDistance = true;
    volatile boolean espHealth = true;
    volatile boolean espSkeleton = false;
    volatile float espMaxDist = 200f;
    volatile int espColor = 0xFF00FF7E;

    volatile boolean aimEnabled = false;
    volatile String aimBone = "chest";
    volatile int aimSmooth = 8;
    volatile int aimFov = 90;
    volatile float aimMaxDist = 150f;
    volatile boolean aimHideFov = false;
    volatile boolean dragHeadshot = false;

    volatile boolean noRecoil = false;
    volatile boolean noSpread = false;
    volatile boolean fastSwitch = false;
    volatile boolean reloadX2 = false;

    // --- live watch
    volatile List<Watch> watch = new ArrayList<>();
    volatile long lastPacketAt = 0;
    volatile int lastSeq = 0;
    volatile boolean reply = false;

    /** Returns the number of watch entries accepted. */
    int apply(JSONObject o) {
        espBox = o.optBoolean("esp_box", espBox);
        espLine = o.optBoolean("esp_line", espLine);
        espName = o.optBoolean("esp_name", espName);
        espDistance = o.optBoolean("esp_distance", espDistance);
        espHealth = o.optBoolean("esp_health", espHealth);
        espSkeleton = o.optBoolean("esp_skeleton", espSkeleton);
        espMaxDist = (float) o.optDouble("esp_max_dist", espMaxDist);
        espColor = parseColor(o.optString("esp_color", ""), espColor);

        aimEnabled = o.optBoolean("aim_enabled", aimEnabled);
        aimBone = o.optString("aim_bone", aimBone);
        aimSmooth = o.optInt("aim_smooth", aimSmooth);
        aimFov = o.optInt("aim_fov", aimFov);
        aimMaxDist = (float) o.optDouble("aim_max_dist", aimMaxDist);
        aimHideFov = o.optBoolean("aim_hide_fov", aimHideFov);
        dragHeadshot = o.optBoolean("drag_hs", dragHeadshot);

        noRecoil = o.optBoolean("no_recoil", noRecoil);
        noSpread = o.optBoolean("no_spread", noSpread);
        fastSwitch = o.optBoolean("fast_switch", fastSwitch);
        reloadX2 = o.optBoolean("reload_x2", reloadX2);

        reply = o.optBoolean("reply", false);

        JSONArray w = o.optJSONArray("watch");
        if (w != null) {
            List<Watch> next = new ArrayList<>();
            for (int i = 0; i < w.length() && i < 16; i++) {
                JSONObject e = w.optJSONObject(i);
                if (e == null) continue;
                String a = e.optString("addr", "");
                long addr = parseAddr(a);
                if (addr == 0) continue;
                String type = e.optString("type", "u32");
                String label = e.optString("label", a);
                next.add(new Watch(addr, type, label));
            }
            watch = next;
        }
        return watch.size();
    }

    /**
     * The reverse of {@link #apply}. Same key names, deliberately: a toggle in
     * the panel and a packet from the laptop have to look identical to the
     * module, or there would be two vocabularies to keep in sync.
     */
    JSONObject toJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("esp_box", espBox);
            o.put("esp_line", espLine);
            o.put("esp_name", espName);
            o.put("esp_distance", espDistance);
            o.put("esp_health", espHealth);
            o.put("esp_skeleton", espSkeleton);
            o.put("esp_max_dist", (double) espMaxDist);

            o.put("aim_enabled", aimEnabled);
            o.put("aim_bone", aimBone);
            o.put("aim_smooth", aimSmooth);
            o.put("aim_fov", aimFov);
            o.put("aim_max_dist", (double) aimMaxDist);
            o.put("aim_hide_fov", aimHideFov);
            o.put("drag_hs", dragHeadshot);

            o.put("no_recoil", noRecoil);
            o.put("no_spread", noSpread);
            o.put("fast_switch", fastSwitch);
            o.put("reload_x2", reloadX2);
        } catch (JSONException e) {
            // a put on a fresh object cannot fail; if it ever does, the module
            // sees the previous packet and nothing changes mid-match
        }
        return o;
    }

    static long parseAddr(String s) {
        try {
            String t = s.trim();
            if (t.startsWith("0x") || t.startsWith("0X")) {
                return Long.parseUnsignedLong(t.substring(2), 16);
            }
            return Long.parseUnsignedLong(t);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    static int parseColor(String s, int dflt) {
        if (s == null || s.isEmpty()) return dflt;
        try {
            String t = s.startsWith("#") ? s.substring(1) : s;
            long v = Long.parseLong(t, 16);
            if (t.length() == 6) v |= 0xFF000000L;
            return (int) v;
        } catch (NumberFormatException e) {
            return dflt;
        }
    }
}
