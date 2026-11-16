// Stealth.m - three layers, no external dependencies.
//
//   TIER 1  camouflage      the image loads from a vendor-looking framework
//                           path and carries no identifying strings.
//                           Defeats `image_name.contains("cheat")` and any
//                           scan for loose .dylib load commands.
//
//   TIER 2  dyld hiding     USLink overwrites the symbol-pointer slots the
//                           GAME calls through, so its own dyld enumeration
//                           never sees us. Verified offline on this build
//                           (tools/got_scan.py), 9 slots in 6 of 7 targets:
//
//                             __DATA,__got          [925] _dyld_image_count
//                             __DATA,__la_symbol_ptr[1174] _dyld_image_count
//                             __DATA,__got          [924] _dyld_get_image_name
//                             __DATA,__la_symbol_ptr[1172] _dyld_get_image_name
//                             __DATA,__got          [923] _dyld_get_image_header
//                             __DATA,__la_symbol_ptr[1171] _dyld_get_image_header
//                             __DATA,__la_symbol_ptr[1173] _dyld_get_image_vmaddr_slide
//                             __DATA,__la_symbol_ptr[1487] _objc_copyClassNamesForImage
//                             __DATA,__la_symbol_ptr[1488] _objc_copyImageNames
//
//                           Both sections matter: a check that binds at launch
//                           goes through __got and never touches the lazy ones.
//
//                           No Dobby, no substrate, no inline hooking. Nothing
//                           is patched inside libdyld itself - only the slots
//                           the game calls through, so an integrity hash of
//                           dyld still matches.
//
//   TIER 3  behavioural     handled in the menu (smooth / fov / bone / jitter),
//                           and it is the layer that actually matters.

#import "Stealth.h"
#import "Rebind.h"
#import "Dyld.h"
#import "Obf.h"
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Logging goes through Obf.h: the tag is decoded at runtime, so neither the
// product name nor the os_log subsystem string ends up in the binary.

// ------------------------------------------------------------------ state
typedef uint32_t (*fn_count)(void);
typedef const char *(*fn_name)(uint32_t);
typedef const struct mach_header *(*fn_header)(uint32_t);
typedef intptr_t (*fn_slide)(uint32_t);
typedef const char **(*fn_copyImageNames)(unsigned int *);

static fn_count  real_count;
static fn_name   real_name;
static fn_header real_header;
static fn_slide  real_slide;
static fn_copyImageNames real_copyImageNames;

static int gOurIndex = -1;
static int gPatched = 0;
static BOOL gInstalled = NO;
static BOOL gHidden = NO;
static char gOurImagePath[512];

static uint32_t rc(void) { return real_count ? real_count() : 0; }

static void findSelf(void) {
    Dl_info info;
    if (!dladdr((const void *)&findSelf, &info) || !info.dli_fbase) return;
    const struct mach_header *self = (const struct mach_header *)info.dli_fbase;
    if (info.dli_fname) {
        strncpy(gOurImagePath, info.dli_fname, sizeof(gOurImagePath) - 1);
    }
    uint32_t n = rc();
    for (uint32_t i = 0; i < n; i++) {
        const struct mach_header *h = real_header ? real_header(i) : NULL;
        if ((const void *)h == (const void *)self) { gOurIndex = (int)i; return; }
    }
}

static int mapIndex(uint32_t fake) {
    if (gOurIndex < 0) return -1;
    int r = (int)fake;
    if (r >= gOurIndex) r += 1;
    if (r >= (int)rc()) return -1;
    return r;
}

// ------------------------------------------------------------------ shims
static uint32_t shim_count(void) {
    uint32_t n = rc();
    if (gOurIndex < 0) findSelf();
    return (gOurIndex >= 0 && n > 0) ? n - 1 : n;
}

static const char *shim_name(uint32_t i) {
    int r = mapIndex(i);
    if (r < 0 || !real_name) return "";
    return real_name((uint32_t)r);
}

static const struct mach_header *shim_header(uint32_t i) {
    int r = mapIndex(i);
    if (r < 0 || !real_header) return NULL;
    return real_header((uint32_t)r);
}

static intptr_t shim_slide(uint32_t i) {
    int r = mapIndex(i);
    if (r < 0 || !real_slide) return 0;
    return real_slide((uint32_t)r);
}

// objc_copyImageNames returns a malloc'd array the caller frees, so we must
// hand back an equally freeable array with our entry removed.
static const char **shim_copyImageNames(unsigned int *outCount) {
    if (!real_copyImageNames) { if (outCount) *outCount = 0; return NULL; }
    unsigned int n = 0;
    const char **all = real_copyImageNames(&n);
    if (!all) { if (outCount) *outCount = 0; return NULL; }
    const char **out = calloc(n ? n : 1, sizeof(char *));
    unsigned int k = 0;
    for (unsigned int i = 0; i < n; i++) {
        if (gOurImagePath[0] && all[i] && strstr(all[i], gOurImagePath)) continue;
        out[k++] = all[i];
    }
    free((void *)all);
    if (outCount) *outCount = k;
    return out;
}

// ------------------------------------------------------------------ install
@implementation USCloak

+ (BOOL)isHidden { return gHidden; }

+ (void)captureOriginals {
    // FFDlsym tries the C name and the underscored Mach-O name. A bare
    // `dlsym(..., "dyld_image_count")` returns NULL on this platform, which
    // would leave every shim below reporting an empty image list.
    real_count  = (fn_count)FFDlsym("_dyld_image_count");
    real_name   = (fn_name)FFDlsym("_dyld_get_image_name");
    real_header = (fn_header)FFDlsym("_dyld_get_image_header");
    real_slide  = (fn_slide)FFDlsym("_dyld_get_image_vmaddr_slide");
    real_copyImageNames = (fn_copyImageNames)FFDlsym("_objc_copyImageNames");
}

+ (USCloakTier)install {
    if (gInstalled) return gHidden ? USCloakHidden : USCloakCamouflage;
    gInstalled = YES;

    [self captureOriginals];
    findSelf();
    FFLOG(@"image index=%d path=%s", gOurIndex, gOurImagePath);

    // TIER 2: rewrite the slots the game itself calls through.
    static const char *targets[] = {
        "dyld_image_count", "dyld_get_image_name",
        "dyld_get_image_header", "dyld_get_image_vmaddr_slide",
    };
    FFImp repl[4] = {
        (FFImp)shim_count, (FFImp)shim_name,
        (FFImp)shim_header, (FFImp)shim_slide,
    };
    for (int i = 0; i < 4; i++) {
        gPatched += [USLink rebindEverywhere:targets[i] to:repl[i]];
    }
    if (real_copyImageNames) {
        gPatched += [USLink rebindEverywhere:"objc_copyImageNames"
                                            to:(FFImp)shim_copyImageNames];
    }

    // Deliberately no inline hook engine here. Rewriting the calling image's
    // own slots is invisible to a hash check of libdyld; patching the first
    // bytes of dyld_image_count in place is not, and it is a process-wide
    // change that any other dylib can trip over. GOT rebinding already covers
    // the path the game actually takes.

    gHidden = (gPatched > 0);
    FFLOG(@"stealth: %d slot(s) rebound, hidden=%s",
          gPatched, gHidden ? "yes" : "no");
    return gHidden ? USCloakHidden : USCloakCamouflage;
}

+ (NSString *)report {
    if (gHidden)
        return [NSString stringWithFormat:
                @"stealth tier 2 (dyld hidden, %d slots, idx %d)", gPatched, gOurIndex];
    if (gOurIndex >= 0)
        return [NSString stringWithFormat:
                @"stealth tier 1 (camouflaged, visible at idx %d)", gOurIndex];
    return @"stealth tier 1 (camouflaged)";
}

@end