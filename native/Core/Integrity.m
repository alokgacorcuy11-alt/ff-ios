// Integrity.m - the runtime half of the client-integrity answer.
//
// Read ANTIDETECT 0.1 first: the ban is a launch-time verdict on the app, not
// on what the app does. The pieces split cleanly:
//
//   decided at signing   signature, Team ID, profile contents, App Attest
//                        -> not reachable from inside the process
//   decided at runtime   "is there a provisioning profile in the bundle"
//                        "is this task debuggable"
//                        "what does the bundle say its identifier is"
//                        "is there a framework the App Store build lacks"
//                        "which images are loaded"
//                        -> these are API answers, and API answers are ours
//
// This file handles exactly the last group, and exactly the artefacts OUR
// install introduced - no jailbreak hiding, no Frida hiding, no syscall
// interception, nothing that the App Store build would not also answer.
//
// Two mechanisms, because the callers differ:
//   * C entry points (stat / access / open / SecTaskCopyValueForEntitlement)
//     are rebound in the calling image's symbol-pointer slots by USLink - the
//     same machinery that hides us from dyld. Nothing is patched inside
//     libsystem, so a hash of libsystem still matches.
//   * Objective-C methods (NSFileManager, NSBundle) are patched by IMP, which
//     is the only way to catch a caller that never goes through PLT.

#import "Integrity.h"
#import "Rebind.h"
#import "Dyld.h"
#import "Stealth.h"
#import "Obf.h"

#import <objc/runtime.h>
#include <sys/stat.h>
#include <sys/syslimits.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>

// ---------------------------------------------------------------- what is secret
//
// Only artefacts that exist because the app was re-signed. Matching is on the
// path, so a directory listing, a stat, an access and an open all answer the
// same way. Case-insensitive because a checker may spell it differently.
static const char *kSecret[] = {
    "embedded.mobileprovision",
    "UnityServices.framework",
    "ff_native.json",
};

static BOOL FFSecretPath(const char *p) {
    if (!p || !*p) return NO;
    for (size_t i = 0; i < sizeof(kSecret) / sizeof(kSecret[0]); i++) {
        if (strcasestr(p, kSecret[i])) return YES;
    }
    return NO;
}

static BOOL FFSecretString(NSString *s) {
    return [s isKindOfClass:NSString.class] && FFSecretPath(s.UTF8String);
}

// ---------------------------------------------------------------- C entry points
typedef int (*fn_stat)(const char *, struct stat *);
typedef int (*fn_access)(const char *, int);
typedef int (*fn_open)(const char *, int, int);
typedef CFTypeRef (*fn_secTaskCopyEnt)(void *, CFStringRef, CFErrorRef *);

static fn_stat real_stat;
static fn_access real_access;
static fn_open real_open;
static BOOL gEntHooked;

static int shim_stat(const char *p, struct stat *b) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_stat(p, b);
}

static int shim_access(const char *p, int mode) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_access(p, mode);
}

static int shim_open(const char *p, int flags, int mode) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_open(p, flags, mode);
}

// The kernel answers "does this task hold this entitlement" from the code
// signature, which we cannot change. But the API is still a function returning
// a value, and a bare NULL is that API's own way of saying "no such
// entitlement". A checker asking for get-task-allow is asking "was this app
// signed for development"; the answer stops being the signature's.
static fn_secTaskCopyEnt real_copyEnt;

static CFTypeRef shim_copyEnt(void *task, CFStringRef key, CFErrorRef *err) {
    if (key && CFGetTypeID(key) == CFStringGetTypeID() &&
        CFStringCompare(key, CFSTR("get-task-allow"), 0) == kCFCompareEqualTo) {
        if (err) *err = NULL;
        return NULL;
    }
    return real_copyEnt(task, key, err);
}

// ---------------------------------------------------------------- ObjC methods
static BOOL (*o_fileExists)(id, SEL, NSString *);
static BOOL (*o_fileExistsDir)(id, SEL, NSString *, BOOL *);
static NSArray *(*o_contentsOfDir)(id, SEL, NSString *, NSError **);
static NSDictionary *(*o_attributesOfItem)(id, SEL, NSString *, NSError **);
static NSString *(*o_bundleIdentifier)(id, SEL);
static NSDictionary *(*o_infoDictionary)(id, SEL);

static BOOL r_fileExists(id self, SEL _cmd, NSString *path) {
    if (FFSecretString(path)) return NO;
    return o_fileExists ? o_fileExists(self, _cmd, path) : NO;
}

static BOOL r_fileExistsDir(id self, SEL _cmd, NSString *path, BOOL *isDir) {
    if (FFSecretString(path)) {
        if (isDir) *isDir = NO;
        return NO;
    }
    return o_fileExistsDir ? o_fileExistsDir(self, _cmd, path, isDir) : NO;
}

static NSArray *r_contentsOfDir(id self, SEL _cmd, NSString *path, NSError **err) {
    NSArray *all = o_contentsOfDir ? o_contentsOfDir(self, _cmd, path, err) : nil;
    if (!all.count) return all;
    NSMutableArray *out = [NSMutableArray arrayWithCapacity:all.count];
    for (NSString *name in all) {
        if (![name isKindOfClass:NSString.class]) continue;
        NSString *full = [path stringByAppendingPathComponent:name];
        if (FFSecretString(full)) continue;
        [out addObject:name];
    }
    return out;
}

static NSDictionary *r_attributesOfItem(id self, SEL _cmd, NSString *path, NSError **err) {
    if (FFSecretString(path)) {
        if (err) {
            *err = [NSError errorWithDomain:NSCocoaErrorDomain
                                       code:NSFileNoSuchFileError
                                   userInfo:nil];
        }
        return nil;
    }
    return o_attributesOfItem ? o_attributesOfItem(self, _cmd, path, err) : nil;
}

// The App Store identifier. FF compares this against its own constant and a
// sideloaded build always differs - Sideloadly even appends the Team ID. The
// value is a getter, not a capability: nothing in this process needs the
// foreign string to work. Scoped to the main bundle, so every framework still
// answers with its own identifier.
static NSString *r_bundleIdentifier(id self, SEL _cmd) {
    NSString *real = o_bundleIdentifier ? o_bundleIdentifier(self, _cmd) : nil;
    if (!real.length) return real;
    if (self != [NSBundle mainBundle]) return real;
    return @"com.dts.freefireth";
}

static NSDictionary *r_infoDictionary(id self, SEL _cmd) {
    NSDictionary *d = o_infoDictionary ? o_infoDictionary(self, _cmd) : nil;
    if (!d || self != [NSBundle mainBundle]) return d;
    id ident = d[@"CFBundleIdentifier"];
    if (![ident isKindOfClass:NSString.class]) return d;
    if ([ident isEqualToString:@"com.dts.freefireth"]) return d;
    NSMutableDictionary *c = [d mutableCopy];
    c[@"CFBundleIdentifier"] = @"com.dts.freefireth";
    return c;
}

// ---------------------------------------------------------------- install
static NSMutableSet *gPatched;
static int gSlots, gMethods;

static void *hook(Class c, SEL sel, void *rep) {
    if (!c) return NULL;
    Method m = class_getInstanceMethod(c, sel);
    if (!m) return NULL;
    NSValue *key = [NSValue valueWithPointer:(void *)m];
    if ([gPatched containsObject:key]) return NULL;      // already patched via a parent
    [gPatched addObject:key];
    void *orig = (void *)method_getImplementation(m);
    method_setImplementation(m, (IMP)rep);
    gMethods++;
    return orig;
}

@implementation USIntegrity

+ (void)install {
    static BOOL done = NO;
    if (done) return;
    done = YES;
    gPatched = [NSMutableSet set];

    // ---- C entry points. A shim is only bound when the real function was
    // found: one with no original would answer ENOENT for the whole system.
    static const char *names[] = {
        "stat", "access", "open", "SecTaskCopyValueForEntitlement",
    };
    static FFImp reps[4];
    reps[0] = (FFImp)shim_stat;
    reps[1] = (FFImp)shim_access;
    reps[2] = (FFImp)shim_open;
    reps[3] = (FFImp)shim_copyEnt;
    void *orig[4] = { NULL, NULL, NULL, NULL };

    for (int i = 0; i < 4; i++) {
        orig[i] = FFDlsym(names[i]);
        if (!orig[i]) continue;
        int n = [USLink rebindEverywhere:names[i] to:reps[i]];
        gSlots += n;
        if (i == 3 && n > 0) gEntHooked = YES;
    }
    real_stat = (fn_stat)orig[0];
    real_access = (fn_access)orig[1];
    real_open = (fn_open)orig[2];
    real_copyEnt = (fn_secTaskCopyEnt)orig[3];

    // ---- ObjC. `[NSFileManager defaultManager]` is a private CF subclass, so
    // patch its class first and NSFileManager second; hook() refuses to patch
    // the same Method twice, which keeps the originals real when the subclass
    // simply inherits them.
    Class fm = object_getClass([NSFileManager defaultManager]);
    o_fileExists       = (BOOL (*)(id, SEL, NSString *))
        hook(fm, @selector(fileExistsAtPath:), (void *)r_fileExists);
    o_fileExistsDir    = (BOOL (*)(id, SEL, NSString *, BOOL *))
        hook(fm, @selector(fileExistsAtPath:isDirectory:), (void *)r_fileExistsDir);
    o_contentsOfDir    = (NSArray *(*)(id, SEL, NSString *, NSError **))
        hook(fm, @selector(contentsOfDirectoryAtPath:error:), (void *)r_contentsOfDir);
    o_attributesOfItem = (NSDictionary *(*)(id, SEL, NSString *, NSError **))
        hook(fm, @selector(attributesOfItemAtPath:error:), (void *)r_attributesOfItem);

    if (!o_fileExists)
        o_fileExists = (BOOL (*)(id, SEL, NSString *))
            hook(NSFileManager.class, @selector(fileExistsAtPath:), (void *)r_fileExists);
    if (!o_fileExistsDir)
        o_fileExistsDir = (BOOL (*)(id, SEL, NSString *, BOOL *))
            hook(NSFileManager.class, @selector(fileExistsAtPath:isDirectory:), (void *)r_fileExistsDir);
    if (!o_contentsOfDir)
        o_contentsOfDir = (NSArray *(*)(id, SEL, NSString *, NSError **))
            hook(NSFileManager.class, @selector(contentsOfDirectoryAtPath:error:), (void *)r_contentsOfDir);
    if (!o_attributesOfItem)
        o_attributesOfItem = (NSDictionary *(*)(id, SEL, NSString *, NSError **))
            hook(NSFileManager.class, @selector(attributesOfItemAtPath:error:), (void *)r_attributesOfItem);

    o_bundleIdentifier = (NSString *(*)(id, SEL))
        hook(NSBundle.class, @selector(bundleIdentifier), (void *)r_bundleIdentifier);
    o_infoDictionary   = (NSDictionary *(*)(id, SEL))
        hook(NSBundle.class, @selector(infoDictionary), (void *)r_infoDictionary);

    FFLOG(@"integrity: %d slot(s), %d method(s), entitlement %@",
          gSlots, gMethods, gEntHooked ? @"covered" : @"NOT covered");
}

// ---------------------------------------------------------------- audit
+ (NSString *)appPath {
    char buf[PATH_MAX];
    uint32_t sz = sizeof(buf);
    if (_NSGetExecutablePath(buf, &sz) != 0) return nil;
    char *slash = strrchr(buf, '/');
    if (!slash) return nil;
    *slash = 0;
    return @(buf);
}

+ (NSString *)rawReport {
    NSString *app = [self appPath] ?: @"?";
    NSString *prof = [app stringByAppendingPathComponent:@"embedded.mobileprovision"];

    fn_stat rs = real_stat ?: (fn_stat)FFDlsym("stat");
    struct stat st;
    BOOL profileOnDisk = rs && rs(prof.UTF8String, &st) == 0;

    // Parsed from disk, not through NSBundle: this is the file's own answer,
    // not the hooked one.
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:
                          [app stringByAppendingPathComponent:@"Info.plist"]];
    NSString *diskID = [info[@"CFBundleIdentifier"] isKindOfClass:NSString.class]
                     ? info[@"CFBundleIdentifier"] : @"?";

    NSString *gtask = @"unknown";
    static void *(*createSelf)(CFAllocatorRef);
    static fn_secTaskCopyEnt rawEnt;
    static BOOL looked;
    if (!looked) {
        looked = YES;
        createSelf = (void *(*)(CFAllocatorRef))FFDlsym("SecTaskCreateFromSelf");
        rawEnt = (fn_secTaskCopyEnt)FFDlsym("SecTaskCopyValueForEntitlement");
    }
    if (createSelf && rawEnt) {
        void *task = createSelf(kCFAllocatorDefault);
        if (task) {
            CFTypeRef v = rawEnt(task, CFSTR("get-task-allow"), NULL);
            if (v) {
                gtask = (CFGetTypeID(v) == CFBooleanGetTypeID() &&
                         CFBooleanGetValue((CFBooleanRef)v)) ? @"true" : @"false";
                CFRelease(v);
            } else {
                gtask = @"absent";
            }
            CFRelease((CFTypeRef)task);
        }
    }

    return [NSString stringWithFormat:
            @"raw (what is really there)\n"
            @"  profile     : %@\n"
            @"  get-task-all: %@\n"
            @"  bundle id   : %@\n"
            @"  profile path: %@",
            profileOnDisk ? @"PRESENT" : @"absent", gtask, diskID, prof];
}

+ (NSString *)report {
    NSString *app = [self appPath] ?: @"";
    NSString *prof = [app stringByAppendingPathComponent:@"embedded.mobileprovision"];

    // Live probes: both go through the hooks that a checker would go through,
    // so these are end-to-end answers and not a claim about intent.
    BOOL profileSeen = [[NSFileManager defaultManager] fileExistsAtPath:prof];
    NSString *bid = [[NSBundle mainBundle] bundleIdentifier] ?: @"?";

    return [NSString stringWithFormat:
            @"visible (what a checker sees)\n"
            @"  profile     : %@\n"
            @"  get-task-all: %@\n"
            @"  bundle id   : %@\n"
            @"  hooks       : %d slot(s), %d method(s)\n"
            @"  dyld        : %@",
            profileSeen ? @"VISIBLE" : @"hidden",
            gEntHooked ? @"hidden" : @"VISIBLE",
            bid, gSlots, gMethods, [USCloak report]];
}

+ (NSInteger)exposureCount {
    NSInteger n = 0;
    NSString *app = [self appPath] ?: @"";
    if ([[NSFileManager defaultManager]
         fileExistsAtPath:[app stringByAppendingPathComponent:@"embedded.mobileprovision"]]) n++;
    if (![[[NSBundle mainBundle] bundleIdentifier] isEqualToString:@"com.dts.freefireth"]) n++;
    if (![USCloak isHidden]) n++;
    if (!gEntHooked) n++;
    return n;
}

@end
