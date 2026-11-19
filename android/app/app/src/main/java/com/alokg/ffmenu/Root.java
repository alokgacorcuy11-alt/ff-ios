package com.alokg.ffmenu;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

/** Thin wrapper around `su`. Every call is explicit and returns its output. */
final class Root {

    private Root() {}

    static final class Result {
        final int code;
        final String out;
        Result(int code, String out) { this.code = code; this.out = out; }
        boolean ok() { return code == 0; }
    }

    /** Runs a command as root and returns stdout+stderr. Blocks. */
    static Result run(String cmd, int timeoutSec) {
        Process p = null;
        try {
            p = new ProcessBuilder("su", "-c", cmd)
                    .redirectErrorStream(true)
                    .start();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(
                    new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) sb.append(line).append('\n');
            }
            if (!p.waitFor(timeoutSec, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                return new Result(-1, sb + "\n[timeout after " + timeoutSec + "s]");
            }
            return new Result(p.exitValue(), sb.toString().trim());
        } catch (IOException e) {
            return new Result(-2, "su not available: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Result(-3, "interrupted");
        } finally {
            if (p != null) p.destroy();
        }
    }

    static boolean available() {
        Result r = run("id", 5);
        return r.ok() && r.out.contains("uid=0");
    }

    /**
     * Starts the daemon detached. `su -c` exits immediately; the daemon is
     * reparented and keeps running, which is what we want - it must outlive the
     * activity.
     */
    static Result startDetached(String binPath, int uid) {
        String cmd = "cd /data/local/tmp && "
                + "nohup " + binPath + " " + uid
                + " </dev/null >/dev/null 2>&1 & "
                // Nothing left on disk: the process keeps running from the
                // inode, but there is no path to find, stat or copy.
                + "sleep 0.4; rm -f " + binPath;
        return run(cmd, 10);
    }
}
