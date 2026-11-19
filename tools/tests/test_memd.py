#!/usr/bin/env python3
"""test_memd.py - exercise the Android root daemon on a Linux host.

The daemon is plain POSIX (abstract unix socket, /proc, pread/pwrite), so it
compiles and runs unmodified on a Linux box. That makes the wire protocol and
the /proc parsing testable without a phone, a game or an NDK - which is exactly
the part that would otherwise only fail on the device.

What is verified:
  * the request struct the Java client writes (88 bytes, packed, little-endian)
  * FIND returns the pid of a process whose cmdline matches
  * BASE returns the load base of a mapped module, and the bytes at that base
    really are an ELF header
  * READ of a known buffer returns the known bytes
  * WRITE lands and is read back from the target's memory
  * MAPS is parseable text
  * a client with the wrong uid is refused (the SO_PEERCRED gate)

  python tools/tests/test_memd.py

Skipped with a clear message on Windows: no /proc, no abstract sockets.
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SRC = ROOT / "android" / "daemon" / "memd.c"
SOCK = "\0ffs.memd.v1"

REQ = struct.Struct("<IIIIQ64s")     # 88 bytes, packed - must match Java
RSP = struct.Struct("<II")

FAIL = 0
SKIP = 0


def check(name, cond, extra=""):
    global FAIL
    print(f"[{'ok  ' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAIL += 1


def skip(name, why):
    global SKIP
    SKIP += 1
    print(f"[skip] {name} ({why})")


class Client:
    def __init__(self):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(5)
        self.s.connect(SOCK)

    def call(self, cmd, pid=0, addr=0, length=0, name=b""):
        self.s.sendall(REQ.pack(cmd, pid, length, len(name), addr,
                                name.ljust(64, b"\0")[:64]))
        status, rlen = RSP.unpack(self._read(RSP.size))
        data = self._read(rlen) if rlen else b""
        return status, data

    def write(self, pid, addr, payload):
        self.s.sendall(REQ.pack(3, pid, len(payload), 0, addr, b"\0" * 64) + payload)
        status, rlen = RSP.unpack(self._read(RSP.size))
        if rlen:
            self._read(rlen)
        return status

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise EOFError("daemon closed")
            buf += chunk
        return buf

    def close(self):
        self.s.close()


VICTIM_C = r"""
#include <stdio.h>
#include <unistd.h>
#include <sys/prctl.h>
#ifndef PR_SET_PTRACER
#define PR_SET_PTRACER 0x59616d61
#endif
#ifndef PR_SET_PTRACER_ANY
#define PR_SET_PTRACER_ANY ((unsigned long)-1)
#endif
char slot[64] = "ORIGINAL";
int main(void) {
    /* Ubuntu ships yama ptrace_scope=1: a process may only be read by its own
     * descendants. The daemon is a sibling, so without this opt-in every read
     * fails with EACCES and the test would fail on CI while passing on a
     * machine with ptrace_scope=0. On Android this is not needed - root has
     * CAP_SYS_PTRACE, which yama always allows. */
    prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0);
    printf("%p\n", (void *)slot);
    fflush(stdout);
    sleep(120);
    return 0;
}
"""


def main() -> int:
    print("=" * 66)
    print(" android daemon (memd.c)")
    print("=" * 66)

    check("the request struct is 88 bytes", REQ.size == 88, str(REQ.size))

    if not Path("/proc/self/mem").exists():
        skip("all runtime checks", "not Linux")
        print("\nALL PASS (nothing to run here)")
        return 0

    cc = shutil.which("gcc") or shutil.which("clang")
    if not cc:
        skip("all runtime checks", "no C compiler")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="memd-test-"))
    binp = tmp / "memd"
    r = subprocess.run([cc, "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter",
                        "-std=gnu11", "-o", str(binp), str(SRC)],
                       capture_output=True, text=True)
    check("compiles clean", r.returncode == 0 and "warning" not in r.stderr.lower(),
          (r.stderr or "").strip().splitlines()[0] if r.stderr else "")

    # a target with a findable cmdline and a known global to read and write
    vsrc = tmp / "victim.c"
    vsrc.write_text(VICTIM_C)
    vbin = tmp / "ffvictim"
    subprocess.run([cc, "-O0", "-o", str(vbin), str(vsrc)], check=True)

    victim = subprocess.Popen([str(vbin)], stdout=subprocess.PIPE,
                              start_new_session=True)
    slot_addr = int(victim.stdout.readline().strip(), 16)
    check("the test target published its buffer address", slot_addr > 0x1000,
          hex(slot_addr))

    uid = os.getuid()
    daemon = subprocess.Popen([str(binp), str(uid)], start_new_session=True)
    time.sleep(0.4)

    try:
        c = Client()
        st, data = c.call(1)                                   # PING
        check("PING answers with the server pid",
              st == 0 and len(data) == 4 and
              struct.unpack("<I", data)[0] == daemon.pid,
              f"{data.hex()} vs {daemon.pid}")

        # the daemon rewrites its own argv, so neither the binary path nor the
        # uid is readable from /proc - the game's process scan sees nothing
        cmdline = Path(f"/proc/{daemon.pid}/cmdline").read_bytes()
        check("the cmdline is scrubbed", b"memd" not in cmdline
              and b"ffmemd" not in cmdline, cmdline)

        st, data = c.call(5, name=b"ffvictim")                 # FIND
        pid = struct.unpack("<I", data)[0] if len(data) == 4 else 0
        check("FIND returns the target pid", pid == victim.pid, f"{pid} vs {victim.pid}")

        st, data = c.call(2, pid=pid, addr=slot_addr, length=8)  # READ
        check("READ returns the buffer contents", data == b"ORIGINAL", data)

        st = c.write(pid, slot_addr, b"PATCHED\0")             # WRITE
        check("WRITE is accepted", st == 0, f"status {st}")
        st, data = c.call(2, pid=pid, addr=slot_addr, length=8)
        check("the write landed", data == b"PATCHED\0", data)

        st, data = c.call(4, pid=pid, name=b"libc")            # BASE
        base = struct.unpack("<Q", data)[0] if len(data) == 8 else 0
        check("BASE returns a plausible address", base > 0x1000, hex(base))
        st, data = c.call(2, pid=pid, addr=base, length=4)
        check("the module base really is an ELF header", data == b"\x7fELF", data.hex())

        st, data = c.call(6, pid=pid, length=8192)             # MAPS
        text = data.decode("utf-8", "replace")
        check("MAPS looks like a maps file", "libc" in text and "r-xp" in text)

        st, data = c.call(2, pid=pid, addr=slot_addr, length=0)  # bad length
        check("a zero-length read is refused", st != 0, f"status {st}")

        c2 = Client()
        st, _ = c2.call(1)
        check("a second client is served", st == 0)
        c2.close()

        # the service must be killable through the socket alone: the cmdline
        # and comm are scrubbed, so there is no name to pkill
        st, _ = c.call(7)                                      # SHUTDOWN
        check("SHUTDOWN answers", st == 0)
        c.close()
        try:
            daemon.wait(timeout=3)
            gone = daemon.poll() is not None
        except subprocess.TimeoutExpired:
            gone = False
        check("SHUTDOWN stops the service", gone)
    finally:
        daemon.terminate()
        victim.terminate()
        for p in (daemon, victim):
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    # the uid gate: a daemon started for a different uid must refuse us
    other = uid + 1 if uid != 65534 else 65533
    d2 = subprocess.Popen([str(binp), str(other)], start_new_session=True)
    time.sleep(0.4)
    try:
        c3 = Client()
        try:
            c3.call(1)
            refused = False
        except (EOFError, ConnectionResetError, BrokenPipeError, socket.timeout):
            refused = True
        c3.close()
        check("a client with the wrong uid is refused", refused)
    finally:
        d2.terminate()
        try:
            d2.wait(timeout=3)
        except subprocess.TimeoutExpired:
            d2.kill()

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f"{FAIL} check(s) FAILED")
        return 1
    print(f"ALL PASS ({SKIP} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
