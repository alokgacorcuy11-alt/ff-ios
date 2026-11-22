package com.alokg.roottools;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.concurrent.TimeUnit;

/**
 * The only privileged thing this app does: ask the superuser app to run one
 * command, and report exactly what came back.
 *
 * <p>No daemon, no background process, no shell held open. Each call is one
 * `su -c`, which the user sees in their superuser app and can refuse or revoke.
 * That is the whole contract.
 */
public final class Root {

    public static final class Result {
        public final String command;
        public final int code;
        public final String out;

        Result(String command, int code, String out) {
            this.command = command;
            this.code = code;
            this.out = out;
        }

        public boolean ok() {
            return code == 0;
        }

        /** What to put on screen: the first line, trimmed, or the exit code. */
        public String summary() {
            String s = out.trim();
            if (s.isEmpty()) {
                return ok() ? "selesai (exit 0)" : "gagal (exit " + code + ")";
            }
            int nl = s.indexOf('\n');
            return nl > 0 ? s.substring(0, nl) : s;
        }

        @Override
        public String toString() {
            return summary();
        }
    }

    private Root() {
    }

    /**
     * Runs {@code su -c command}.
     *
     * @param timeoutSec how long to wait before giving up. A superuser prompt
     *     the user has not answered looks exactly like a hang, so this must
     *     never be allowed to wait forever.
     */
    public static Result su(String command, int timeoutSec) {
        Process p = null;
        try {
            p = new ProcessBuilder("su", "-c", command).redirectErrorStream(true).start();
            StringBuilder sb = new StringBuilder();
            BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
            String line;
            boolean finished = p.waitFor(timeoutSec, TimeUnit.SECONDS);
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
            }
            if (!finished) {
                p.destroy();
                return new Result(command, -1, sb + "waktu habis (" + timeoutSec
                        + "s) - mungkin prompt superuser belum dijawab");
            }
            return new Result(command, p.exitValue(), sb.toString());
        } catch (Exception e) {
            return new Result(command, -1,
                    e.getClass().getSimpleName() + ": " + e.getMessage());
        } finally {
            if (p != null) {
                p.destroy();
            }
        }
    }

    public static Result su(String command) {
        return su(command, 20);
    }

    /** True when `su -c id` answers as uid 0. That is the whole test. */
    public static boolean available() {
        Result r = su("id", 15);
        return r.ok() && r.out.contains("uid=0");
    }
}
