// Integrity.m - the runtime half of the client-integrity answer.
//
// Read ANTIDETECT 0.1 first: the ban is a launch-time verdict on the app, not
// on what the app does. The pieces split cleanly:
//
//   decided at signing   signature, Team ID, profile contents, App Attest
//                        -> not reachable from inside the process
//   decided at runtime   "is there a provisioning profile in the bundle"
//                        "is this task debuggable"
//                        "what does the code signature call this app"
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
//   * C entry points are rebound in the calling image's symbol-pointer slots by
//     USLink - the same machinery that hides us from dyld. Nothing is patched
//     inside libsystem, so a hash of libsystem still matches.
//   * Objective-C methods (NSFileManager, NSBundle) are patched by IMP, which
//     is the only way to catch a caller that never goes through PLT.
//
// No name in here is written as a literal. Core/Secrets.m holds them XOR-coded,
// so `strings` on the release dylib has nothing to grep for. That matters more
// than it sounds: a dylib that spells out the profile filename is a dylib that
// is hiding a profile, and a scanner only needs one such word.

#import "Integrity.h"
#import "Rebind.h"
#import "Dyld.h"
#import "Stealth.h"
#import "Secrets.h"
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
// same way. Case-insensitive because a checker may spell it differently, which
// is also why the stored needles are lowercase.
static BOOL FFSecretPath(const char *p) {
    if (!p || !*p) return NO;
    static const FFSecret which[] = {
        FFSecretProfile, FFSecretFramework, FFSecretSchemaFile,
    };
    for (size_t i = 0; i < sizeof(which) / sizeof(which[0]); i++) {
        const char *needle = FFSecretNeedle(which[i]);
        if (needle && *needle && strcasestr(p, needle)) return YES;
    }
    return NO;
}

static BOOL FFSecretString(NSString *s) {
    return [s isKindOfClass:NSString.class] && FFSecretPath(s.UTF8String);
}

// ---------------------------------------------------------------- C entry points
typedef int (*fn_stat)(const char *, struct stat *);
typedef int (*fn_access)(const char *, int);
typedef int (*fn_faccessat)(int, const char *, int, int);
typedef int (*fn_open)(const char *, int, int);
typedef int (*fn_openat)(int, const char *, int, int);
typedef CFTypeRef (*fn_copyEnt)(void *, CFStringRef, CFErrorRef *);
typedef CFStringRef (*fn_copySign)(void *, CFErrorRef *);

static fn_stat real_stat;
static fn_stat real_lstat;
static fn_access real_access;
static fn_faccessat real_faccessat;
static fn_open real_open;
static fn_openat real_openat;
static fn_copyEnt real_copyEnt;
static fn_copySign real_copySign;
static BOOL gEntHooked;

static int shim_stat(const char *p, struct stat *b) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_stat(p, b);
}

// Darwin exports the 64-bit-inode variants under `stat$INODE64` and calls them
// through that name, so both spellings are rebound below. They are the same
// function; forwarding through whichever pointer was found keeps the shim
// honest when only one of the two names resolves.
static int shim_lstat(const char *p, struct stat *b) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    if (real_lstat) return real_lstat(p, b);
    return real_stat(p, b);
}

static int shim_access(const char *p, int mode) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_access(p, mode);
}

static int shim_faccessat(int fd, const char *p, int mode, int flag) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_faccessat(fd, p, mode, flag);
}

static int shim_open(const char *p, int flags, int mode) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_open(p, flags, mode);
}

static int shim_openat(int fd, const char *p, int flags, int mode) {
    if (FFSecretPath(p)) { errno = ENOENT; return -1; }
    return real_openat(fd, p, flags, mode);
}

// The kernel answers "does this task hold this entitlement" from the code
// signature, which we cannot change. But the API is still a function returning
// a value, and a bare NULL is that API's own way of saying "no such
// entitlement". A checker asking for the development entitlement is asking "was
// this app signed for development"; the answer stops being the signature's.
static CFTypeRef shim_copyEnt(void *task, CFStringRef key, CFErrorRef *err) {
    CFStringRef want = FFSecretCF(FFSecretEntitlement);
    if (key && want && CFGetTypeID(key) == CFStringGetTypeID() &&
        CFStringCompare(key, want, 0) == kCFCompareEqualTo) {
        if (err) *err = NULL;
        return NULL;
    }
    return real_copyEnt(task, key, err);
}

// And the identifier the signature itself carries: on a re-signed build it is
// the signer's, not the store's. Same reasoning - the value is an answer, and
// the answer the App Store build gives is the identifier of the app.
static CFStringRef shim_copySign(void *task, CFErrorRef *err) {
    CFStringRef real = real_copySign ? real_copySign(task, err) : NULL;
    CFStringRef want = FFSecretCF(FFSecretBundleID);
    if (!want) return real;
    if (real && CFEqual(real, want)) return real;
    if (real) CFRelease(real);
    if (err) *err = NULL;
    return CFStringCreateCopy(kCFAllocatorDefault, want);
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
    return FFSecretText(FFSecretBundleID);
}

static NSDictionary *r_infoDictionary(id self, SEL _cmd) {
    NSDictionary *d = o_infoDictionary ? o_infoDictionary(self, _cmd) : nil;
    if (!d || self != [NSBundle mainBundle]) return d;
    id ident = d[@"CFBundleIdentifier"];
    if (![ident isKindOfClass:NSString.class]) return d;
    NSString *want = FFSecretText(FFSecretBundleID);
    if ([ident isEqualToString:want]) return d;
    NSMutableDictionary *c = [d mutableCopy];
    c[@"CFBundleIdentifier"] = want;
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

// One rebind attempt: resolve the original first and only then patch the slots.
// A shim with no original would answer ENOENT for the whole system, so a name
// that does not resolve is simply skipped. `orig` keeps the first real pointer
// found, so a second spelling of the same function does not overwrite it.
static int bindOne(const char *name, FFImp rep, void **orig) {
    if (!name || !*name) return 0;
    void *p = FFDlsym(name);
    if (!p) return 0;
    if (orig && !*orig) *orig = p;
    return [USLink rebindEverywhere:name to:rep];
}

@implementation USIntegrity

+ (void)install {
    static BOOL done = NO;
    if (done) return;
    done = YES;
    gPatched = [NSMutableSet set];

    // ---- C entry points.
    //
    //   stat / lstat        both spellings: Darwin calls the $INODE64 variants
    //   openat / faccessat  Swift and parts of CF never call plain open/access
    //   entitlement API     the answer a checker reads for "is this debuggable"
    //
    // Nothing is patched inside libsystem: every one of these is a rewrite of a
    // symbol-pointer slot in the image that calls through it.
    // SecTask* lives in Security.framework. It is loaded by almost everything
    // that touches the keychain, but not guaranteed this early, and dlsym
    // against an unloaded image returns NULL. One dlopen removes the doubt.
    dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY);

    void *o[7] = { NULL, NULL, NULL, NULL, NULL, NULL, NULL };
    gSlots += bindOne("stat",           (FFImp)shim_stat,      &o[0]);
    gSlots += bindOne("stat$INODE64",   (FFImp)shim_stat,      &o[0]);
    gSlots += bindOne("lstat",          (FFImp)shim_lstat,     &o[1]);
    gSlots += bindOne("lstat$INODE64",  (FFImp)shim_lstat,     &o[1]);
    gSlots += bindOne("access",         (FFImp)shim_access,    &o[2]);
    gSlots += bindOne("faccessat",      (FFImp)shim_faccessat, &o[3]);
    gSlots += bindOne("open",           (FFImp)shim_open,      &o[4]);
    gSlots += bindOne("openat",         (FFImp)shim_openat,    &o[5]);
    gSlots += bindOne(FFSecretNeedle(FFSecretEntAPI),
                      (FFImp)shim_copyEnt, &o[6]);

    real_stat      = (fn_stat)o[0];
    real_lstat     = (fn_stat)o[1];
    real_access    = (fn_access)o[2];
    real_faccessat = (fn_faccessat)o[3];
    real_open      = (fn_open)o[4];
    real_openat    = (fn_openat)o[5];
    real_copyEnt   = (fn_copyEnt)o[6];
    gEntHooked     = (o[6] != NULL);

    const char *signName = FFSecretNeedle(FFSecretEntSign);
    if (signName && *signName) {
        void *p = FFDlsym(signName);
        if (p) {
            gSlots += [USLink rebindEverywhere:signName to:(FFImp)shim_copySign];
            real_copySign = (fn_copySign)p;
        }
    }

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

    FFLOG(@"%d slot(s), %d method(s), entitlement %@, identifier %@",
          gSlots, gMethods, gEntHooked ? @"covered" : @"NOT covered",
          real_copySign ? @"covered" : @"NOT covered");
}

// ---------------------------------------------------------------- way back in
+ (NSData *)dataAtPath:(NSString *)path {
    if (!path.length) return nil;
    fn_open ro = real_open ?: (fn_open)FFDlsym("open");
    if (!ro) return nil;
    int fd = ro(path.UTF8String, O_RDONLY, 0);
    if (fd < 0) return nil;
    NSMutableData *out = [NSMutableData data];
    uint8_t buf[8192];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        [out appendBytes:buf length:(NSUInteger)n];
    }
    close(fd);
    return n < 0 ? nil : out;
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
    NSString *prof = [app stringByAppendingPathComponent:FFSecretText(FFSecretProfile)];

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
    static fn_copyEnt rawEnt;
    static BOOL looked;
    if (!looked) {
        looked = YES;
        const char *cn = FFSecretNeedle(FFSecretEntCreate);
        const char *en = FFSecretNeedle(FFSecretEntAPI);
        createSelf = (void *(*)(CFAllocatorRef))(cn && *cn ? FFDlsym(cn) : NULL);
        rawEnt = (fn_copyEnt)(en && *en ? FFDlsym(en) : NULL);
    }
    if (createSelf && rawEnt) {
        void *task = createSelf(kCFAllocatorDefault);
        if (task) {
            CFTypeRef v = rawEnt(task, FFSecretCF(FFSecretEntitlement), NULL);
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
            @"  debuggable  : %@\n"
            @"  bundle id   : %@\n"
            @"  path        : %@",
            profileOnDisk ? @"PRESENT" : @"absent", gtask, diskID, prof];
}

+ (NSString *)report {
    NSString *app = [self appPath] ?: @"";
    NSString *prof = [app stringByAppendingPathComponent:FFSecretText(FFSecretProfile)];

    // Live probes: both go through the hooks that a checker would go through,
    // so these are end-to-end answers and not a claim about intent.
    BOOL profileSeen = [[NSFileManager defaultManager] fileExistsAtPath:prof];
    NSString *bid = [[NSBundle mainBundle] bundleIdentifier] ?: @"?";

    return [NSString stringWithFormat:
            @"visible (what a checker sees)\n"
            @"  profile     : %@\n"
            @"  debuggable  : %@\n"
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
         fileExistsAtPath:[app stringByAppendingPathComponent:FFSecretText(FFSecretProfile)]]) n++;
    if (![[[NSBundle mainBundle] bundleIdentifier]
          isEqualToString:FFSecretText(FFSecretBundleID)]) n++;
    if (![USCloak isHidden]) n++;
    if (!gEntHooked) n++;
    return n;
}

@end
