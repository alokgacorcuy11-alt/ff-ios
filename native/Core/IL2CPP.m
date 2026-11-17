// IL2CPP.m - resolve the Unity runtime by name, at runtime.
//
// Two-stage lookup:
//   1. dlsym(RTLD_DEFAULT) - the normal path. All 256 il2cpp_* symbols in this
//      build are exported, so this works.
//   2. Mach-O symbol table walk - only if stage 1 misses. The walk has to map
//      __LINKEDIT's file offsets correctly: in UnityFramework 1.132.1
//      __LINKEDIT has vmaddr 0xc388000 but fileoff 0xbf44000, so
//      `header + symoff` is WRONG by 0x434000. That mistake is why the old
//      version of this file silently returned NULL on some builds.

#import "IL2CPP.h"
#import "Dyld.h"
#import "Obf.h"
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <stdlib.h>
#include <string.h>

static Il2CppApi gApi;
static BOOL gLoaded = NO;
static NSMutableArray<NSString *> *gMissing;

@implementation IL2CPP

+ (Il2CppApi *)api { return &gApi; }
+ (NSArray<NSString *> *)missingSymbols { return gMissing ?: @[]; }

// ------------------------------------------------------------------ lookup
// Map a file range of a loaded image to its in-memory address.
//
// A symbol table is untrusted input. n_strx is a 32-bit offset whose only
// meaning is "within strsize", and an image can hand back a stroff/symoff whose
// segment is only partly mapped - the dyld shared cache maps __LINKEDIT lazily,
// and an image caught mid-initialisation has a header but no settled mappings.
// Reading a byte past the end is a SIGBUS that kills the app before it draws a
// frame, so every access below goes through a range check that requires the
// WHOLE run to sit inside one segment.
static void *rangeInImage(const struct mach_header_64 *h, intptr_t slide,
                          uint64_t fileoff, uint64_t size) {
    if (size == 0) return NULL;
    const uint8_t *p = (const uint8_t *)(h + 1);
    const uint8_t *limit = p + h->sizeofcmds;
    for (uint32_t c = 0; c < h->ncmds; c++) {
        if (p + sizeof(struct load_command) > limit) return NULL;
        const struct load_command *lc = (const struct load_command *)p;
        if (lc->cmdsize < sizeof(struct load_command)) return NULL;
        if (lc->cmd == LC_SEGMENT_64 &&
            lc->cmdsize >= sizeof(struct segment_command_64)) {
            const struct segment_command_64 *sg = (const struct segment_command_64 *)lc;
            if (fileoff >= sg->fileoff && fileoff - sg->fileoff <= sg->filesize &&
                size <= sg->filesize - (fileoff - sg->fileoff)) {
                return (void *)((intptr_t)sg->vmaddr + slide +
                                (intptr_t)(fileoff - sg->fileoff));
            }
        }
        p += lc->cmdsize;
    }
    return NULL;
}

static void *symFromMachO(const char *name) {
    uint32_t n = _dyld_image_count();
    for (uint32_t i = 0; i < n; i++) {
        const struct mach_header *mh = _dyld_get_image_header(i);
        if (!mh || mh->magic != MH_MAGIC_64) continue;
        const struct mach_header_64 *h = (const struct mach_header_64 *)mh;
        if (h->ncmds == 0 || h->sizeofcmds < sizeof(struct mach_header_64)) continue;
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);

        const struct symtab_command *st = NULL;
        const uint8_t *p = (const uint8_t *)(h + 1);
        const uint8_t *limit = p + h->sizeofcmds;
        for (uint32_t c = 0; c < h->ncmds; c++) {
            if (p + sizeof(struct load_command) > limit) break;
            const struct load_command *lc = (const struct load_command *)p;
            if (lc->cmdsize < sizeof(struct load_command)) break;
            if (lc->cmd == LC_SYMTAB &&
                lc->cmdsize >= sizeof(struct symtab_command)) {
                st = (const struct symtab_command *)lc;
                break;
            }
            p += lc->cmdsize;
        }
        if (!st || st->nsyms == 0 || st->strsize == 0) continue;

        uint64_t symbytes = (uint64_t)st->nsyms * sizeof(struct nlist_64);
        if (symbytes > (1ull << 28)) continue;        // 256 MB of symbols is a lie
        const struct nlist_64 *syms =
            (const struct nlist_64 *)rangeInImage(h, slide, st->symoff, symbytes);
        const char *strtab =
            (const char *)rangeInImage(h, slide, st->stroff, st->strsize);
        if (!syms || !strtab) continue;

        for (uint32_t s = 0; s < st->nsyms; s++) {
            // n_strx beyond the string table is the exact read that killed the
            // app: the old code checked only != 0, so a stale entry walked off
            // __LINKEDIT into unmapped memory. Skip before touching the name.
            uint32_t strx = syms[s].n_un.n_strx;
            if (strx == 0 || strx >= st->strsize) continue;
            if (syms[s].n_type & N_STAB) continue;
            // the name must be terminated inside the table, not after it
            if (!memchr(strtab + strx, 0, st->strsize - strx)) continue;
            const char *sn = strtab + strx;
            if (sn[0] == '_') sn++;
            if (strcmp(sn, name) != 0) continue;
            uint8_t t = syms[s].n_type & N_TYPE;
            if (t != N_SECT && t != N_ABS) continue;
            return (void *)(intptr_t)(syms[s].n_value + (uint64_t)slide);
        }
    }
    return NULL;
}

static void *findSym(const char *name) {
    void *p = FFDlsym(name);
    if (p) return p;
    return symFromMachO(name);
}

// ------------------------------------------------------------------ load
#define R(field, sym, required)                                          \
    do {                                                                 \
        gApi.field = (void *)findSym(sym);                               \
        if (!gApi.field) {                                               \
            [gMissing addObject:@(sym)];                                 \
            if (required) missing++;                                     \
        }                                                                \
    } while (0)

// NOT named `load`, on purpose.
//
// In Objective-C any class method whose selector is `load` - whatever its
// signature or return type - is invoked by the runtime from load_images(), i.e.
// during dyld image initialisation, before the app's own initialisers run. This
// method walks _dyld_image_count() and parses other images' symbol tables, which
// is exactly what must not happen there: the image list is still being built and
// a half-initialised image reports mappings that are not settled yet. That is
// what crashed on launch - the symbol walk faulted on a byte read inside
// load_images, before the splash screen, with the game never reaching main().
//
// It is called explicitly from FFBind() instead, on the main queue, after
// UIApplication exists and after the game has had time to bring Unity up.
+ (BOOL)loadAPI {
    if (gLoaded) return YES;
    memset(&gApi, 0, sizeof(gApi));
    gMissing = [NSMutableArray array];
    int missing = 0;

    R(domain_get,                  "il2cpp_domain_get", 1);
    R(domain_get_assemblies,       "il2cpp_domain_get_assemblies", 1);
    R(assembly_get_image,          "il2cpp_assembly_get_image", 1);

    R(image_get_name,              "il2cpp_image_get_name", 0);
    R(image_get_class_count,       "il2cpp_image_get_class_count", 0);
    R(image_get_class,             "il2cpp_image_get_class", 0);

    R(class_from_name,             "il2cpp_class_from_name", 1);
    R(class_get_name,              "il2cpp_class_get_name", 1);
    R(class_get_namespace,         "il2cpp_class_get_namespace", 1);
    R(class_get_parent,            "il2cpp_class_get_parent", 0);
    R(class_get_declaring_type,    "il2cpp_class_get_declaring_type", 0);
    R(class_from_type,             "il2cpp_class_from_type", 0);
    R(class_get_type,              "il2cpp_class_get_type", 0);
    R(class_is_valuetype,          "il2cpp_class_is_valuetype", 0);
    R(class_is_enum,               "il2cpp_class_is_enum", 0);
    R(class_get_element_class,     "il2cpp_class_get_element_class", 0);
    R(class_has_parent,            "il2cpp_class_has_parent", 0);
    R(class_num_fields,            "il2cpp_class_num_fields", 0);
    R(class_get_nested_types,      "il2cpp_class_get_nested_types", 0);
    R(class_get_type_token,        "il2cpp_class_get_type_token", 0);

    R(object_get_class,            "il2cpp_object_get_class", 1);
    R(object_new,                  "il2cpp_object_new", 0);
    R(object_unbox,                "il2cpp_object_unbox", 1);
    R(value_box,                   "il2cpp_value_box", 0);

    R(class_get_method_from_name,  "il2cpp_class_get_method_from_name", 0);
    R(class_get_methods,           "il2cpp_class_get_methods", 0);
    R(method_get_name,             "il2cpp_method_get_name", 0);
    R(method_get_param_count,      "il2cpp_method_get_param_count", 0);
    R(method_get_return_type,      "il2cpp_method_get_return_type", 0);
    R(runtime_invoke,              "il2cpp_runtime_invoke", 1);

    R(class_get_field_from_name,   "il2cpp_class_get_field_from_name", 1);
    R(class_get_fields,            "il2cpp_class_get_fields", 1);
    R(field_get_name,              "il2cpp_field_get_name", 1);
    R(field_get_type,              "il2cpp_field_get_type", 1);
    R(field_get_offset,            "il2cpp_field_get_offset", 1);
    R(field_get_flags,             "il2cpp_field_get_flags", 1);
    R(field_get_value,             "il2cpp_field_get_value", 1);
    R(field_set_value,             "il2cpp_field_set_value", 1);
    R(field_static_get_value,      "il2cpp_field_static_get_value", 1);
    R(field_static_set_value,      "il2cpp_field_static_set_value", 0);

    R(type_get_name,               "il2cpp_type_get_name", 1);
    R(type_get_type,               "il2cpp_type_get_type", 0);
    R(type_is_static,              "il2cpp_type_is_static", 0);

    R(array_length,                "il2cpp_array_length", 0);
    R(offset_of_array_length,      "il2cpp_offset_of_array_length_in_array_object_header", 0);
    R(array_object_header_size,    "il2cpp_array_object_header_size", 0);

    R(string_length,               "il2cpp_string_length", 1);
    R(string_chars,                "il2cpp_string_chars", 0);
    R(string_new,                  "il2cpp_string_new", 0);

    R(resolve_icall,               "il2cpp_resolve_icall", 1);
    R(free,                        "il2cpp_free", 0);
    R(thread_attach,               "il2cpp_thread_attach", 1);
    R(thread_detach,               "il2cpp_thread_detach", 0);
    R(runtime_class_init,          "il2cpp_runtime_class_init", 0);

    if (missing) {
        FFLOG(@"%d required il2cpp symbols missing: %@",
              missing, [gMissing componentsJoinedByString:@", "]);
        return NO;
    }
    [self attachThread];
    gLoaded = YES;
    FFLOG(@"il2cpp ok (%lu symbols, %lu optional missing)",
          (unsigned long)(sizeof(gApi) / sizeof(void *)), (unsigned long)gMissing.count);
    return YES;
}

+ (NSString *)report {
    if (!gLoaded) return @"il2cpp: not loaded";
    return [NSString stringWithFormat:@"il2cpp: %lu optional symbols missing%@",
            (unsigned long)gMissing.count,
            gMissing.count ? [NSString stringWithFormat:@" (%@)",
                              [gMissing componentsJoinedByString:@","]] : @""];
}

+ (void)attachThread {
    if (!gApi.domain_get || !gApi.thread_attach) return;
    static BOOL once = NO;
    if (once) return;
    once = YES;
    gApi.thread_attach(gApi.domain_get());
}

// ------------------------------------------------------------------ images
+ (NSArray<NSString *> *)imageNames {
    NSMutableArray *out = [NSMutableArray array];
    if (!gApi.domain_get || !gApi.domain_get_assemblies) return out;
    size_t n = 0;
    const Il2CppAssembly **as = gApi.domain_get_assemblies(gApi.domain_get(), &n);
    for (size_t i = 0; i < n; i++) {
        const Il2CppImage *img = gApi.assembly_get_image(as[i]);
        if (!img || !gApi.image_get_name) continue;
        const char *nm = gApi.image_get_name(img);
        if (nm) [out addObject:@(nm)];
    }
    return out;
}

+ (BOOL)hasImage:(NSString *)name { return [self image:name] != NULL; }

+ (const Il2CppImage *)image:(NSString *)name {
    if (!gApi.domain_get || !gApi.domain_get_assemblies) return NULL;
    size_t n = 0;
    const Il2CppAssembly **as = gApi.domain_get_assemblies(gApi.domain_get(), &n);
    for (size_t i = 0; i < n; i++) {
        const Il2CppImage *img = gApi.assembly_get_image(as[i]);
        if (!img || !gApi.image_get_name) continue;
        const char *nm = gApi.image_get_name(img);
        if (nm && [name isEqualToString:@(nm)]) return img;
    }
    return NULL;
}

// ------------------------------------------------------------------ classes
+ (Il2CppClass *)cls:(NSString *)name inImage:(NSString *)image ns:(NSString *)ns {
    if (!gLoaded) return NULL;
    const Il2CppImage *img = image ? [self image:image] : NULL;
    if (image && !img) return NULL;
    if (img) return gApi.class_from_name(img, ns ? ns.UTF8String : "", name.UTF8String);

    size_t n = 0;
    const Il2CppAssembly **as = gApi.domain_get_assemblies(gApi.domain_get(), &n);
    for (size_t i = 0; i < n; i++) {
        const Il2CppImage *im = gApi.assembly_get_image(as[i]);
        if (!im) continue;
        Il2CppClass *c = gApi.class_from_name(im, ns ? ns.UTF8String : "", name.UTF8String);
        if (c) return c;
    }
    return NULL;
}

+ (Il2CppClass *)clsAnywhere:(NSString *)name { return [self clsAnywhere:name ns:@""]; }

+ (Il2CppClass *)clsAnywhere:(NSString *)name ns:(NSString *)ns {
    if (!gLoaded) return NULL;
    Il2CppClass *c = [self cls:name inImage:nil ns:ns];
    if (c) return c;
    // namespace miss is common in obfuscated builds: retry with an empty ns
    if (ns.length) return [self cls:name inImage:nil ns:@""];
    return NULL;
}

+ (Il2CppClass *)clsByFullName:(NSString *)full {
    if (!gLoaded || full.length == 0) return NULL;
    NSRange dot = [full rangeOfString:@"." options:NSBackwardsSearch];
    NSString *ns = dot.location == NSNotFound ? @"" : [full substringToIndex:dot.location];
    NSString *nm = dot.location == NSNotFound ? full : [full substringFromIndex:dot.location + 1];
    Il2CppClass *c = [self clsAnywhere:nm ns:ns];
    if (c) return c;
    // nested types: Namespace.Outer.Inner -> walk the outer class's nested list
    if (dot.location != NSNotFound) {
        NSString *tail = [full substringFromIndex:dot.location + 1];
        NSRange d2 = [tail rangeOfString:@"." options:NSBackwardsSearch];
        if (d2.location != NSNotFound && gApi.class_get_nested_types) {
            NSString *outer = [tail substringToIndex:d2.location];
            NSString *inner = [tail substringFromIndex:d2.location + 1];
            Il2CppClass *o = [self clsAnywhere:outer ns:ns];
            if (o) {
                void *it = NULL;
                const Il2CppClass *k;
                while ((k = gApi.class_get_nested_types(o, &it))) {
                    const char *n = gApi.class_get_name((Il2CppClass *)k);
                    if (n && [inner isEqualToString:@(n)]) return (Il2CppClass *)k;
                }
            }
        }
    }
    return NULL;
}

+ (NSString *)classNameOf:(Il2CppObject *)obj {
    if (!obj || !gApi.object_get_class || !gApi.class_get_name) return @"?";
    return @(gApi.class_get_name(gApi.object_get_class(obj)));
}

+ (NSString *)fullNameOf:(Il2CppClass *)cls {
    if (!cls || !gApi.class_get_name) return @"?";
    const char *n = gApi.class_get_name(cls);
    const char *s = gApi.class_get_namespace ? gApi.class_get_namespace(cls) : "";
    if (s && s[0]) return [NSString stringWithFormat:@"%s.%s", s, n];
    return @(n ? n : "?");
}

@end

// ------------------------------------------------------------------ helpers
// il2cpp_type_get_name allocates in some runtime versions and returns an
// interned string in others. Freeing an interned pointer is an instant crash,
// so only free what lives outside every mapped image.
//
// The range table is built once per image-set: the old version re-parsed every
// load command of every image on every call, and the resolver calls this for
// every field of every class (~1.7k fields on the player class alone), which
// made startup visibly slow.
typedef struct { const uint8_t *lo, *hi; } FFRange;

static FFRange *gRanges;
static uint32_t gRangeCount;
static uint32_t gRangeImages;

static void buildImageRanges(void) {
    uint32_t n = _dyld_image_count();
    if (n > 4096) n = 0;
    uint32_t cap = n * 8 + 16;
    FFRange *r = calloc(cap, sizeof(FFRange));
    if (!r) return;

    uint32_t k = 0;
    for (uint32_t i = 0; i < n; i++) {
        const struct mach_header_64 *h =
            (const struct mach_header_64 *)_dyld_get_image_header(i);
        if (!h || h->magic != MH_MAGIC_64) continue;
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        const uint8_t *q = (const uint8_t *)(h + 1);
        for (uint32_t c = 0; c < h->ncmds && k + 1 < cap; c++) {
            const struct load_command *lc = (const struct load_command *)q;
            if (lc->cmd == LC_SEGMENT_64) {
                const struct segment_command_64 *sg =
                    (const struct segment_command_64 *)lc;
                if (sg->vmsize) {
                    r[k].lo = (const uint8_t *)(sg->vmaddr + (uint64_t)slide);
                    r[k].hi = r[k].lo + sg->vmsize;
                    k++;
                }
            }
            q += lc->cmdsize;
        }
    }

    free(gRanges);
    gRanges = r;
    gRangeCount = k;
    gRangeImages = n;
}

static BOOL FFIsImagePointer(const void *p) {
    if (!p) return NO;
    // Rebuild only when the image set actually changed (the count is what the
    // dyld API exposes cheaply; during il2cpp binding it is stable).
    if (gRangeCount == 0 || gRangeImages != _dyld_image_count()) buildImageRanges();
    const uint8_t *u = (const uint8_t *)p;
    for (uint32_t i = 0; i < gRangeCount; i++) {
        if (u >= gRanges[i].lo && u < gRanges[i].hi) return YES;
    }
    return NO;
}

NSString *FFTypeName(const Il2CppType *t) {
    Il2CppApi *api = [IL2CPP api];
    if (!t || !api->type_get_name) return @"";
    const char *n = api->type_get_name(t);
    if (!n) return @"";
    NSString *s = @(n);
    if (api->free && !FFIsImagePointer(n)) api->free((void *)n);
    return s;
}

NSString *FFFieldTypeName(FieldInfo *f) {
    Il2CppApi *api = [IL2CPP api];
    if (!f || !api->field_get_type) return @"";
    return FFTypeName(api->field_get_type(f));
}

BOOL FFFieldIsStatic(FieldInfo *f) {
    Il2CppApi *api = [IL2CPP api];
    if (!f || !api->field_get_flags) return NO;
    return (api->field_get_flags(f) & FFFieldStatic) != 0;
}

BOOL FFFieldIsLiteral(FieldInfo *f) {
    Il2CppApi *api = [IL2CPP api];
    if (!f || !api->field_get_flags) return NO;
    return (api->field_get_flags(f) & FFFieldLiteral) != 0;
}

size_t FFFieldOffset(FieldInfo *f) {
    Il2CppApi *api = [IL2CPP api];
    if (!f || !api->field_get_offset) return 0;
    return api->field_get_offset(f);
}

NSArray<NSString *> *FFFieldNames(Il2CppClass *cls) {
    NSMutableArray *out = [NSMutableArray array];
    Il2CppApi *api = [IL2CPP api];
    if (!cls || !api->class_get_fields || !api->field_get_name) return out;
    void *it = NULL;
    const FieldInfo *f;
    while ((f = api->class_get_fields(cls, &it))) {
        const char *n = api->field_get_name((FieldInfo *)f);
        if (n) [out addObject:@(n)];
    }
    return out;
}

// "System.Collections.Generic.List`1<COW.GamePlay.Player>" -> class for the
// *element* only when asked; here we return the class of the type itself,
// stripping array suffixes so X[] yields X.
Il2CppClass *FFClassFromTypeName(NSString *typeName) {
    if (!typeName.length) return NULL;
    NSString *t = typeName;
    while ([t hasSuffix:@"[]"]) t = [t substringToIndex:t.length - 2];
    if ([t hasSuffix:@"[,]"]) t = [t substringToIndex:t.length - 3];
    NSRange lt = [t rangeOfString:@"<"];
    if (lt.location != NSNotFound) t = [t substringToIndex:lt.location];
    if ([t hasSuffix:@"&"]) t = [t substringToIndex:t.length - 1];
    return [IL2CPP clsByFullName:t];
}