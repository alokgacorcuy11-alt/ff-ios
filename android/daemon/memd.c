// memd.c - root-side memory service for the Android build.
//
// Why a daemon and not an injected library:
//
//   An in-process cheat (Zygisk / LSPosed / patched APK) has to put code inside
//   Free Fire. That changes what the game can see about itself: an extra
//   mapping, an extra thread, a modified APK signature, a module in the
//   package list. On iOS we had no choice - a sandboxed process cannot be read
//   from outside. Android has no such rule: a root process can read
//   /proc/<pid>/mem directly.
//
//   So nothing of ours ever enters the game. The game's maps, threads, file
//   hashes and signature stay exactly as Garena shipped them. What remains
//   visible is a process with a boring name holding a file descriptor - which
//   is what every profiling tool, backup agent and crash reporter looks like.
//
// Protocol (little-endian, arm64):
//
//   req  { u32 cmd; u32 pid; u32 len; u32 name_len; u64 addr; char name[64]; }
//   rsp  { u32 status; u32 len; }  followed by len bytes
//
//   cmd 1 PING   -> status 0, len 0
//   cmd 2 READ   -> bytes from [addr, addr+len)
//   cmd 3 WRITE  -> consumes len bytes after the request, status 0 on success
//   cmd 4 BASE   -> writes the load base of `name` (e.g. libil2cpp.so) as u64
//   cmd 5 FIND   -> writes the pid of the process whose cmdline contains `name`
//   cmd 6 MAPS   -> writes up to len bytes of /proc/<pid>/maps
//
// Only the uid passed as argv[1] may talk to it: the socket lives in the
// abstract namespace, which is not protected by file permissions, so the peer
// credential check is the access control.
//
// Build: see build.sh (NDK clang, static, arm64).

#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <stdint.h>
#include <dirent.h>
#include <signal.h>

#define SOCK_ABSTRACT "ffs.memd.v1"
#define MAX_REQ_LEN   (1u << 20)   /* 1 MiB per read, enough for a player array */

enum {
    CMD_PING  = 1,
    CMD_READ  = 2,
    CMD_WRITE = 3,
    CMD_BASE  = 4,
    CMD_FIND  = 5,
    CMD_MAPS  = 6,
};

typedef struct __attribute__((packed)) {
    uint32_t cmd;
    uint32_t pid;
    uint32_t len;
    uint32_t name_len;
    uint64_t addr;
    char     name[64];
} req_t;

typedef struct __attribute__((packed)) {
    uint32_t status;
    uint32_t len;
} rsp_t;

static uid_t g_uid;

static int write_all(int fd, const void *buf, size_t n) {
    const uint8_t *p = (const uint8_t *)buf;
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w <= 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

static int read_all(int fd, void *buf, size_t n) {
    uint8_t *p = (uint8_t *)buf;
    while (n) {
        ssize_t r = read(fd, p, n);
        if (r <= 0) {
            if (r < 0 && errno == EINTR) continue;
            return -1;
        }
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

/* One /proc/<pid>/maps parse, used by both BASE and MAPS. */
static int read_maps(int pid, char **out, size_t *out_len) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;

    size_t cap = 1 << 16, used = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) { close(fd); return -1; }

    for (;;) {
        if (used + 4096 > cap) {
            cap *= 2;
            char *nb = (char *)realloc(buf, cap);
            if (!nb) { free(buf); close(fd); return -1; }
            buf = nb;
        }
        ssize_t r = read(fd, buf + used, cap - used - 1);
        if (r < 0) {
            if (errno == EINTR) continue;
            free(buf); close(fd); return -1;
        }
        if (r == 0) break;
        used += (size_t)r;
        if (used > (8u << 20)) break;   /* a maps file is ~100 KB; stop runaway */
    }
    buf[used] = 0;
    close(fd);
    *out = buf;
    *out_len = used;
    return 0;
}

/* Load base of a named module: its lowest mapping, which is the ELF header. */
static uint64_t module_base(int pid, const char *needle) {
    char *maps = NULL;
    size_t len = 0;
    if (read_maps(pid, &maps, &len) != 0) return 0;

    uint64_t base = 0;
    char *save = NULL;
    for (char *line = strtok_r(maps, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        if (!strstr(line, needle)) continue;
        /* 7c00d00000-7c00d01000 r--p 00000000 ... /data/app/.../libil2cpp.so
         *
         * maps is sorted by address, so the first line that names the module
         * is its base. Taking the first *executable* mapping instead would
         * return the text segment - 0x1000+ into the file - and every offset
         * would be wrong by that much. */
        unsigned long long start = 0;
        if (sscanf(line, "%llx-", &start) != 1) continue;
        base = (uint64_t)start;
        break;
    }
    free(maps);
    return base;
}

static int find_pid(const char *needle) {
    DIR *d = opendir("/proc");
    if (!d) return 0;
    struct dirent *e;
    int found = 0;
    char path[64], buf[256];

    while ((e = readdir(d))) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        snprintf(path, sizeof(path), "/proc/%.10s/cmdline", e->d_name);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        close(fd);
        if (n <= 0) continue;
        buf[n] = 0;
        if (strstr(buf, needle)) { found = atoi(e->d_name); break; }
    }
    closedir(d);
    return found;
}

static void handle(int fd) {
    req_t q;
    rsp_t s;
    int memfd = -1, mempid = -1;

    while (read_all(fd, &q, sizeof(q)) == 0) {
        s.status = 0;
        s.len = 0;

        if (q.cmd == CMD_PING) {
            if (write_all(fd, &s, sizeof(s)) != 0) break;
            continue;
        }

        if (q.cmd == CMD_FIND) {
            char name[65] = {0};
            memcpy(name, q.name, q.name_len < 64 ? q.name_len : 64);
            int pid = find_pid(name);
            uint32_t out = (uint32_t)pid;
            s.len = sizeof(out);
            if (write_all(fd, &s, sizeof(s)) != 0) break;
            if (write_all(fd, &out, sizeof(out)) != 0) break;
            continue;
        }

        if (q.pid == 0 || q.pid != (uint32_t)mempid) {
            if (memfd >= 0) close(memfd);
            char path[64];
            snprintf(path, sizeof(path), "/proc/%u/mem", q.pid);
            memfd = open(path, O_RDWR | O_CLOEXEC);
            mempid = (int)q.pid;
        }

        if (q.cmd == CMD_BASE) {
            char name[65] = {0};
            memcpy(name, q.name, q.name_len < 64 ? q.name_len : 64);
            uint64_t base = module_base((int)q.pid, name);
            s.len = sizeof(base);
            if (write_all(fd, &s, sizeof(s)) != 0) break;
            if (write_all(fd, &base, sizeof(base)) != 0) break;
            continue;
        }

        if (q.cmd == CMD_MAPS) {
            char *maps = NULL;
            size_t len = 0;
            if (read_maps((int)q.pid, &maps, &len) != 0) {
                s.status = (uint32_t)errno;
                if (write_all(fd, &s, sizeof(s)) != 0) { free(maps); break; }
                free(maps);
                continue;
            }
            if (q.len && len > q.len) len = q.len;
            s.len = (uint32_t)len;
            if (write_all(fd, &s, sizeof(s)) != 0 ||
                write_all(fd, maps, len) != 0) { free(maps); break; }
            free(maps);
            continue;
        }

        if (memfd < 0) {
            s.status = (uint32_t)errno;
            if (write_all(fd, &s, sizeof(s)) != 0) break;
            continue;
        }

        if (q.cmd == CMD_READ) {
            if (q.len == 0 || q.len > MAX_REQ_LEN) {
                s.status = (uint32_t)EINVAL;
                if (write_all(fd, &s, sizeof(s)) != 0) break;
                continue;
            }
            uint8_t *buf = (uint8_t *)malloc(q.len);
            if (!buf) { s.status = (uint32_t)ENOMEM; write_all(fd, &s, sizeof(s)); break; }
            ssize_t got = pread(memfd, buf, q.len, (off_t)q.addr);
            if (got < 0) {
                s.status = (uint32_t)errno;
                if (write_all(fd, &s, sizeof(s)) != 0) { free(buf); break; }
                free(buf);
                continue;
            }
            s.len = (uint32_t)got;
            if (write_all(fd, &s, sizeof(s)) != 0 ||
                write_all(fd, buf, (size_t)got) != 0) { free(buf); break; }
            free(buf);
            continue;
        }

        if (q.cmd == CMD_WRITE) {
            if (q.len == 0 || q.len > MAX_REQ_LEN) {
                s.status = (uint32_t)EINVAL;
                if (write_all(fd, &s, sizeof(s)) != 0) break;
                continue;
            }
            uint8_t *buf = (uint8_t *)malloc(q.len);
            if (!buf) { s.status = (uint32_t)ENOMEM; write_all(fd, &s, sizeof(s)); break; }
            if (read_all(fd, buf, q.len) != 0) { free(buf); break; }
            ssize_t put = pwrite(memfd, buf, q.len, (off_t)q.addr);
            s.status = put == (ssize_t)q.len ? 0 : (uint32_t)errno;
            free(buf);
            if (write_all(fd, &s, sizeof(s)) != 0) break;
            continue;
        }

        s.status = (uint32_t)EINVAL;
        if (write_all(fd, &s, sizeof(s)) != 0) break;
    }

    if (memfd >= 0) close(memfd);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: memd <client-uid>\n");
        return 2;
    }
    g_uid = (uid_t)strtoul(argv[1], NULL, 10);

    /* The game's own /proc scan would see this name. Make it unremarkable. */
    prctl(PR_SET_NAME, "logd.writer", 0, 0, 0);
    umask(077);
    signal(SIGCHLD, SIG_IGN);   /* per-client children, reaped by the kernel */

    int srv = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (srv < 0) return 1;

    struct sockaddr_un sa;
    memset(&sa, 0, sizeof(sa));
    sa.sun_family = AF_UNIX;
    memcpy(sa.sun_path + 1, SOCK_ABSTRACT, strlen(SOCK_ABSTRACT));
    socklen_t slen = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(SOCK_ABSTRACT));

    /* abstract namespace: no filesystem entry to clean up */
    if (bind(srv, (struct sockaddr *)&sa, slen) != 0) return 1;
    if (listen(srv, 4) != 0) return 1;

    for (;;) {
        int fd = accept(srv, NULL, NULL);
        if (fd < 0) {
            if (errno == EINTR) continue;
            break;
        }

        struct ucred cred;
        socklen_t clen = sizeof(cred);
        if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &clen) != 0 ||
            cred.uid != g_uid) {
            close(fd);
            continue;
        }

        /* One process per client. A single-threaded accept loop would serve
         * exactly one connection until it closed, which is fine for a test but
         * wrong for the app: it reconnects after every screen-off, and the old
         * socket is not always closed first. */
        pid_t child = fork();
        if (child == 0) {
            close(srv);
            handle(fd);
            close(fd);
            _exit(0);
        }
        close(fd);
    }

    close(srv);
    return 0;
}
