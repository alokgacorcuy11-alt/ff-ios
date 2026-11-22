#include "ff_stealth.h"
#include "ff_mem.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

#if defined(__ANDROID__)
#include <pthread.h>
#include <sys/prctl.h>
#endif

namespace ff {

void name_thread(const char* name) {
#if defined(__ANDROID__)
    if (name == nullptr) {
        return;
    }
    // 15 characters is the kernel's limit; longer names are truncated silently.
    ::prctl(PR_SET_NAME, name, 0, 0, 0);
#else
    (void)name;
#endif
}

bool unlink_after_open(const char* path) {
    if (path == nullptr) {
        return false;
    }
    return ::unlink(path) == 0;
}

StealthReport inspect_self() {
    StealthReport r;

    // Walk our own mappings and collect the file-backed ones. Anything with a
    // path is something a scan can name, and this reports them rather than
    // claiming there are none.
    FILE* f = std::fopen("/proc/self/maps", "r");
    if (f != nullptr) {
        char line[512];
        while (std::fgets(line, sizeof(line), f) != nullptr) {
            // The path is whatever follows the inode and device fields.
            const char* p = std::strchr(line, '/');
            if (p == nullptr) {
                continue;
            }
            std::string path(p);
            while (!path.empty() && (path.back() == '\n' || path.back() == ' ')) {
                path.pop_back();
            }
            if (path.find("libffcheat") == std::string::npos
                    && path.find("libcheat") == std::string::npos) {
                continue;
            }
            r.mapped_from_file = true;
            r.self_path = path;
            break;
        }
        std::fclose(f);
    }

    // TracerPid is the kernel's own answer to "is anyone attached". Reading it
    // rather than asserting it keeps this report from drifting away from truth.
    FILE* s = std::fopen("/proc/self/status", "r");
    if (s != nullptr) {
        char line[256];
        while (std::fgets(line, sizeof(line), s) != nullptr) {
            if (std::strncmp(line, "TracerPid:", 10) == 0) {
                const int pid = std::atoi(line + 10);
                r.uses_ptrace = pid != 0;
                break;
            }
        }
        std::fclose(s);
    }

    r.created_threads = false;   // this build starts none
    r.uses_frida = false;        // and links nothing that would
    r.reads_only = true;

    if (r.mapped_from_file) {
        r.note = "payload is file-backed: its path is visible in /proc/self/maps. "
                 "A manual loader is what changes this.";
    } else {
        r.note = "payload is not file-backed: no path to find.";
    }
    return r;
}

}  // namespace ff
