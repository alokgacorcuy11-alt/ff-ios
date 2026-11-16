// Dyld.h - symbol lookup that does not care about the underscore.
//
// `dlsym` on Darwin has one trap: the C name and the Mach-O name differ by a
// leading underscore (`_dyld_image_count` in the symbol table, `dyld_image_count`
// in C). Whether `dlsym` accepts the bare name has changed across dyld versions
// and differs between RTLD_DEFAULT and a dlopen handle.
//
// Guessing wrong is not a graceful failure here: in Stealth.m a missed lookup
// leaves `real_count` NULL, the dyld shims then report zero images, and the
// game's own dyld enumeration gets an empty list. So try both spellings.

#ifndef FF_DYLD_H
#define FF_DYLD_H

#include <dlfcn.h>
#include <stddef.h>
#include <string.h>

static inline void *FFDlsymIn(void *handle, const char *name) {
    if (!name || !name[0]) return NULL;
    void *p = dlsym(handle, name);
    if (p) return p;
    if (name[0] == '_') return dlsym(handle, name + 1);

    char buf[256];
    size_t n = strlen(name);
    if (n + 2 > sizeof(buf)) return NULL;
    buf[0] = '_';
    memcpy(buf + 1, name, n + 1);
    return dlsym(handle, buf);
}

static inline void *FFDlsym(const char *name) {
    return FFDlsymIn(RTLD_DEFAULT, name);
}

#endif
