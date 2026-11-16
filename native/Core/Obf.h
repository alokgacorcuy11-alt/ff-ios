// Obf.h - keep the dylib from identifying itself.
//
// Three small things that each remove a fingerprint:
//
//   1. the log tag and the preference keys are XOR-decoded at runtime, so
//      `strings` on the dylib has no product name to grep for, and the app's
//      preferences plist carries nothing readable either
//   2. logging is OFF by default. Console.app is where naive detection starts,
//      and a release build that chats about "esp"/"aim" is handing itself over
//   3. the offsets of what we read are never compiled in (see Resolve.m), so
//      there is nothing to pattern-match against a known cheat either
//
// Not obfuscation theatre - it is the subset that shows up in a
// `strings`/`nm` diff against a stock build.

#ifndef FF_OBF_H
#define FF_OBF_H

#import <Foundation/Foundation.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define FF_XOR_KEY 0x5B

static inline void FFDecode(const uint8_t *src, size_t n, char *out) {
    for (size_t i = 0; i < n; i++) out[i] = (char)(src[i] ^ FF_XOR_KEY);
    out[n] = 0;
}

#define FF_OBF_DECL(name, ...) static const uint8_t name[] = { __VA_ARGS__ }

// Encoded blobs, generated once and checked in:
//   "us.core"           ^ 0x5B
//   "runtime"           ^ 0x5B
//   "us.core.prefs.3"   ^ 0x5B   NSUserDefaults keys
//   "us.core.map.3"     ^ 0x5B
FF_OBF_DECL(FF_TAG_BYTES,   0x2e, 0x28, 0x75, 0x38, 0x34, 0x29, 0x3e);
FF_OBF_DECL(FF_SELF_BYTES,  0x29, 0x2e, 0x35, 0x2f, 0x32, 0x36, 0x3e);
FF_OBF_DECL(FF_PREFS_BYTES, 0x2e, 0x28, 0x75, 0x38, 0x34, 0x29, 0x3e,
                            0x75, 0x2b, 0x29, 0x3e, 0x3d, 0x28, 0x75, 0x68);
FF_OBF_DECL(FF_MAP_BYTES,   0x2e, 0x28, 0x75, 0x38, 0x34, 0x29, 0x3e,
                            0x75, 0x36, 0x3a, 0x2b, 0x75, 0x68);

static inline NSString *FFTag(void) {
    char buf[sizeof(FF_TAG_BYTES) + 1];
    FFDecode(FF_TAG_BYTES, sizeof(FF_TAG_BYTES), buf);
    return @(buf);
}

static inline NSString *FFSubsystem(void) {
    char buf[sizeof(FF_SELF_BYTES) + 1];
    FFDecode(FF_SELF_BYTES, sizeof(FF_SELF_BYTES), buf);
    return @(buf);
}

// NSUserDefaults keys land in Library/Preferences/<bundle>.plist, readable by
// anything on the device. Keep them uninformative, and not grep-able either.
static inline NSString *FFPrefsKey(void) {
    char buf[sizeof(FF_PREFS_BYTES) + 1];
    FFDecode(FF_PREFS_BYTES, sizeof(FF_PREFS_BYTES), buf);
    return @(buf);
}

static inline NSString *FFMapKey(void) {
    char buf[sizeof(FF_MAP_BYTES) + 1];
    FFDecode(FF_MAP_BYTES, sizeof(FF_MAP_BYTES), buf);
    return @(buf);
}

// ---------------------------------------------------------------- logging
// One flag for the whole dylib, so a toggle in the menu actually silences
// every translation unit (a static inside an inline function would not).
extern int FFVerboseFlag;
BOOL FFVerbose(void);
void FFSetVerbose(BOOL on);

// Drop-in for NSLog(@"[ffmenu] ...", ...) -> FFLOG(@"...", ...)
// String-literal concatenation keeps the format intact and the arguments line
// up, because the tag becomes the first argument.
//
// With FF_QUIET_BUILD the macro expands to nothing at all: the format strings
// are not in the binary, so `strings` on a release dylib shows no messages,
// no feature names in prose, nothing to pattern-match. Build with
// `make RELEASE=1`.
#if defined(FF_QUIET_BUILD) && !defined(FF_DEBUG)

#define FFLOG(fmt, ...)          do { } while (0)
#define FFLOG_ALWAYS(fmt, ...)   do { } while (0)

#else

#define FFLOG(fmt, ...)                                                      \
    do {                                                                     \
        if (FFVerbose()) NSLog(@"[%@] " fmt, FFTag(), ##__VA_ARGS__);        \
    } while (0)

#define FFLOG_ALWAYS(fmt, ...) NSLog(@"[%@] " fmt, FFTag(), ##__VA_ARGS__)

#endif

#endif  // FF_OBF_H