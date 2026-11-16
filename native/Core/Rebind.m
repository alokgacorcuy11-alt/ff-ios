// Rebind.m - generalised fishhook.
//
// The one rule that matters here: NEVER walk the image list through the same
// GOT slots this file rewrites. `_dyld_image_count` / `_dyld_get_image_header`
// / `_dyld_get_image_vmaddr_slide` are themselves rebindable symbols, and
// `+rebindEverywhere:` patches them in our own image - after which every later
// loop iteration goes through the hiding shim (which reports n-1 images and
// remaps indices), so indices silently drift and images get skipped.
//
// So the real functions are resolved once, from libdyld's own handle, before
// any patching happens, and the image list is snapshotted up front.

#import "Rebind.h"
#import "Dyld.h"
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <dlfcn.h>
#include <string.h>
#include <stdlib.h>

static int gPatched = 0;

// ------------------------------------------------ real dyld, resolved once
typedef uint32_t (*fn_count)(void);
typedef const struct mach_header *(*fn_header)(uint32_t);
typedef intptr_t (*fn_slide)(uint32_t);

static fn_count  real_count;
static fn_header real_header;
static fn_slide  real_slide;
static BOOL gDyldResolved = NO;

static void resolveDyld(void) {
    if (gDyldResolved) return;
    gDyldResolved = YES;

    void *h = dlopen("/usr/lib/libdyld.dylib", RTLD_LAZY);
    if (!h) h = dlopen("/usr/lib/system/libdyld.dylib", RTLD_LAZY);
    void *src = h ? h : RTLD_DEFAULT;

    real_count  = (fn_count)FFDlsymIn(src, "_dyld_image_count");
    real_header = (fn_header)FFDlsymIn(src, "_dyld_get_image_header");
    real_slide  = (fn_slide)FFDlsymIn(src, "_dyld_get_image_vmaddr_slide");

    // Fall back to the imports. Correct as long as we are called before we
    // patch them - which is exactly what `+install` ordering guarantees.
    if (!real_count)  real_count  = &_dyld_image_count;
    if (!real_header) real_header = &_dyld_get_image_header;
    if (!real_slide)  real_slide  = &_dyld_get_image_vmaddr_slide;
}

@implementation USLink

+ (int)patchCount { return gPatched; }

static void makeWritable(void *addr) {
    vm_address_t page = (vm_address_t)addr & ~(vm_page_size - 1);
    vm_protect(mach_task_self(), page, vm_page_size * 2, FALSE,
               VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
}

static const char *stripUnderscores(const char *s) {
    while (s && *s == '_') s++;
    return s ? s : "";
}

static BOOL nameMatches(const char *sym, NSArray<NSString *> *wanted) {
    if (!sym) return NO;
    const char *s = stripUnderscores(sym);
    for (NSString *w in wanted) {
        if (strcmp(s, stripUnderscores(w.UTF8String)) == 0) return YES;
    }
    return NO;
}

+ (int)patchImage:(const struct mach_header_64 *)h
          symbols:(NSArray<NSString *> *)symbols
             into:(FFImp)replacement
           origin:(FFImp *)outOrigin
{
    resolveDyld();
    if (!h || h->magic != MH_MAGIC_64 || symbols.count == 0) return 0;

    intptr_t slide = 0;
    uint32_t nimg = real_count();
    for (uint32_t i = 0; i < nimg; i++) {
        if ((const struct mach_header_64 *)real_header(i) == h) {
            slide = real_slide(i);
            break;
        }
    }

    const struct symtab_command *symtab = NULL;
    const struct dysymtab_command *dysym = NULL;
    uint64_t linkeditVm = 0, linkeditFileoff = 0;
    BOOL haveLinkedit = NO;

    struct { uint64_t addr; uint64_t size; uint32_t reserved1; } secs[64];
    int nsecs = 0;

    const uint8_t *p = (const uint8_t *)(h + 1);
    for (uint32_t c = 0; c < h->ncmds; c++) {
        const struct load_command *lc = (const struct load_command *)p;
        if (lc->cmd == LC_SYMTAB) {
            symtab = (const struct symtab_command *)lc;
        } else if (lc->cmd == LC_DYSYMTAB) {
            dysym = (const struct dysymtab_command *)lc;
        } else if (lc->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *sg = (const struct segment_command_64 *)lc;
            if (strcmp(sg->segname, SEG_LINKEDIT) == 0 && !haveLinkedit) {
                linkeditVm = sg->vmaddr;
                linkeditFileoff = sg->fileoff;
                haveLinkedit = YES;
            }
            const struct section_64 *s = (const struct section_64 *)(sg + 1);
            for (uint32_t k = 0; k < sg->nsects && nsecs < 64; k++) {
                uint8_t t = s[k].flags & SECTION_TYPE;
                if (t == S_LAZY_SYMBOL_POINTERS || t == S_NON_LAZY_SYMBOL_POINTERS) {
                    secs[nsecs].addr = s[k].addr;
                    secs[nsecs].size = s[k].size;
                    secs[nsecs].reserved1 = s[k].reserved1;
                    nsecs++;
                }
            }
        }
        p += lc->cmdsize;
    }

    if (!symtab || !dysym || nsecs == 0 || !haveLinkedit) return 0;

    // __LINKEDIT is not always 1:1 with the file, so resolve it through its
    // vmaddr and convert the command's file offsets by subtracting fileoff.
    const uint8_t *le = (const uint8_t *)(uintptr_t)(linkeditVm + (uint64_t)slide);
    #define LE_AT(fo) ((const void *)(le + ((uint64_t)(fo) - linkeditFileoff)))

    const struct nlist_64 *syms = (const struct nlist_64 *)LE_AT(symtab->symoff);
    const char *strtab = (const char *)LE_AT(symtab->stroff);
    const uint32_t *indirect = (const uint32_t *)LE_AT(dysym->indirectsymoff);

    int patched = 0;
    for (int s = 0; s < nsecs; s++) {
        uint32_t nslots = (uint32_t)(secs[s].size / sizeof(void *));
        void **slots = (void **)(uintptr_t)(secs[s].addr + (uint64_t)slide);
        for (uint32_t i = 0; i < nslots; i++) {
            uint32_t idx = indirect[secs[s].reserved1 + i];
            if (idx & (INDIRECT_SYMBOL_LOCAL | INDIRECT_SYMBOL_ABS)) continue;
            if (idx >= symtab->nsyms) continue;
            const char *nm = strtab + syms[idx].n_un.n_strx;
            if (!nameMatches(nm, symbols)) continue;

            if (outOrigin && *outOrigin == NULL) *outOrigin = (FFImp)slots[i];
            makeWritable(&slots[i]);
            slots[i] = (void *)replacement;
            patched++;
            gPatched++;
        }
    }
    #undef LE_AT
    return patched;
}

+ (FFImp)rebind:(const char *)symbol
         inImage:(const struct mach_header_64 *)header
              to:(FFImp)replacement
{
    FFImp origin = NULL;
    [self patchImage:header symbols:@[ @(symbol) ] into:replacement origin:&origin];
    return origin;
}

+ (FFImp)rebindAny:(NSArray<NSString *> *)symbols
           inImage:(const struct mach_header_64 *)header
                to:(FFImp)replacement
{
    FFImp origin = NULL;
    [self patchImage:header symbols:symbols into:replacement origin:&origin];
    return origin;
}

+ (int)rebindEverywhere:(const char *)symbol to:(FFImp)replacement {
    resolveDyld();
    // Snapshot first: the list must not change shape while we are patching the
    // very symbols that produce it.
    uint32_t n = real_count();
    if (n == 0 || n > 4096) return 0;
    const struct mach_header_64 **imgs = calloc(n, sizeof(*imgs));
    if (!imgs) return 0;
    for (uint32_t i = 0; i < n; i++) {
        imgs[i] = (const struct mach_header_64 *)real_header(i);
    }

    int total = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (!imgs[i]) continue;
        total += [self patchImage:imgs[i] symbols:@[ @(symbol) ] into:replacement origin:NULL];
    }
    free(imgs);
    return total;
}

@end
