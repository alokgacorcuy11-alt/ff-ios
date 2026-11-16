// Inject.m

#import "Inject.h"
#import "Dyld.h"
#import "Obf.h"
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <stdbool.h>
#include <unistd.h>

// IOHIDEvent digitizer field page.
//
// IOHIDEventFieldBase(type) = (type << 16), and kIOHIDEventTypeDigitizer = 0x0B,
// so every digitizer field is 0x000B0000 + n. The X..Pressure prefix is stable
// across every IOHIDEventField.h Apple has shipped:
//
//   0 X, 1 Y, 2 Z, 3 ButtonMask, 4 Type, 5 Index, 6 Identity, 7 EventMask,
//   8 Range, 9 Touch, 0xA Pressure
//
// The TAIL is not stable: Apple inserted and removed fields (AuxiliaryPressure,
// TiltX/Y, Altitude, Azimuth, Quality, Density, Irregularity, Major/Minor
// Radius) between releases, so IsDisplayCoordinates has been 0x0B0018, then
// 0x0B0016, then 0x0B0014 on different iOS versions. Hardcoding any of them
// silently puts the tap in the wrong place on the versions where it is wrong -
// which is exactly what the previous version of this file did.
//
// So the tail is DISCOVERED at runtime, not guessed. See `resolveTailFields`.
enum {
    kDigitizerPage                 = 0x0B0000,
    kFieldX                        = kDigitizerPage + 0x00,
    kFieldY                        = kDigitizerPage + 0x01,
    kFieldZ                        = kDigitizerPage + 0x02,
    kFieldButtonMask               = kDigitizerPage + 0x03,
    kFieldType                     = kDigitizerPage + 0x04,
    kFieldIndex                    = kDigitizerPage + 0x05,
    kFieldIdentity                 = kDigitizerPage + 0x06,
    kFieldEventMask                = kDigitizerPage + 0x07,
    kFieldRange                    = kDigitizerPage + 0x08,
    kFieldTouch                    = kDigitizerPage + 0x09,
    kFieldPressure                 = kDigitizerPage + 0x0A,

    // digitizer event masks
    kMaskRange                     = 1 << 0,
    kMaskTouch                     = 1 << 1,
    kMaskPosition                  = 1 << 2,
    kMaskStop                      = 1 << 3,
    kMaskCancel                    = 1 << 7,
    kMaskStart                     = 1 << 8,

    kTransducerFinger              = 2,
};

typedef struct __IOHIDEvent      *IOHIDEventRef;
typedef struct __IOHIDEventSystemClient *IOHIDEventSystemClientRef;

typedef IOHIDEventSystemClientRef (*fn_clientCreate)(CFAllocatorRef);
typedef void     (*fn_clientDispatch)(IOHIDEventSystemClientRef, IOHIDEventRef);
typedef IOHIDEventRef (*fn_fingerEvent)(CFAllocatorRef, uint64_t, uint32_t, uint32_t,
                                        uint32_t, double, double, double, double, double,
                                        Boolean, Boolean, uint32_t);
typedef IOHIDEventRef (*fn_digitizerEvent)(CFAllocatorRef, uint64_t, uint32_t, uint32_t,
                                           uint32_t, uint32_t, uint32_t, double, double,
                                           double, double, double, Boolean, Boolean, uint32_t);
typedef void     (*fn_setInteger)(IOHIDEventRef, uint32_t, int64_t);
typedef int64_t  (*fn_getInteger)(IOHIDEventRef, uint32_t);
typedef void     (*fn_setFloat)(IOHIDEventRef, uint32_t, double);
typedef void     (*fn_append)(IOHIDEventRef, IOHIDEventRef);

static fn_clientCreate     pClientCreate;
static fn_clientDispatch   pClientDispatch;
static fn_fingerEvent      pFingerEvent;
static fn_digitizerEvent   pDigitizerEvent;
static fn_setInteger       pSetInteger;
static fn_getInteger       pGetInteger;
static fn_setFloat         pSetFloat;
static fn_append           pAppend;

static IOHIDEventSystemClientRef gClient;
static BOOL gDown = NO;
static uint32_t gIdentity = 1;
static CGPoint gLast;
static NSString *gOwner;
static BOOL gTried;

// Resolved tail fields. 0 means "not present in this build".
static uint32_t gFieldIsDisplayCoords = 0;
static uint32_t gFieldIsIntegrated    = 0;
// YES when the event carries points; NO when it carries device pixels.
static BOOL gPointSpace = NO;
static BOOL gTailResolved = NO;

static NSDictionary *gOverrides;

// Private: the probe below needs it before the @implementation is reached.
@interface USInput (Probe)
+ (NSDictionary *)fieldOverrides;
@end

@implementation USInput

// ------------------------------------------------------------------ helpers
static CGFloat screenScale(void) {
    static CGFloat s;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        __block CGFloat v = 0;          // __block: the block assigns it
        if ([NSThread isMainThread]) {
            v = [UIScreen mainScreen].scale;
        } else {
            dispatch_sync(dispatch_get_main_queue(), ^{
                v = [UIScreen mainScreen].scale;
            });
        }
        s = (v > 0) ? v : 1.0;
    });
    return s;
}

// Turn a point in the main screen's coordinate space into whatever the event
// expects. `gPointSpace` is decided by `resolveTailFields`, never assumed.
static CGPoint eventSpace(CGPoint p) {
    if (gPointSpace) return p;
    CGFloat s = screenScale();
    return CGPointMake(p.x * s, p.y * s);
}

// A throwaway event with the same shape as the real ones, used only to inspect
// field defaults.
static IOHIDEventRef probeEvent(void) {
    if (!pFingerEvent) return NULL;
    return pFingerEvent(kCFAllocatorDefault, mach_absolute_time(),
                        /*index*/ 1,
                        /*identity*/ 0x4242,
                        /*eventMask*/ (uint32_t)(kMaskRange | kMaskTouch | kMaskPosition),
                        /*x*/ 100.0, /*y*/ 200.0, /*z*/ 0.0,
                        /*tipPressure*/ 1.0, /*twist*/ 0.0,
                        /*range*/ true, /*touch*/ true, /*options*/ 0);
}

// Work out where the tail flags live on THIS build.
//
// A physical finger is by definition integrated with the display, and the
// constructor sets that flag - so exactly one index above Pressure reads back
// non-zero on a fresh event. IsDisplayCoordinates is the index immediately
// before it: the two are adjacent in every version of the enum, only their
// absolute position moved. If the probe finds nothing unambiguous, no flag is
// set and coordinates go out in device pixels - the constructor's own default,
// which is a correct space and not a guess.
static void resolveTailFields(void) {
    if (gTailResolved) return;

    // explicit override wins: schema["input"] { display_coords_index, integrated_index }
    NSDictionary *ov = [USInput fieldOverrides];
    if (ov) {
        id d = ov[@"display_coords_index"];
        id i = ov[@"integrated_index"];
        if ([d respondsToSelector:@selector(intValue)]) gFieldIsDisplayCoords = (uint32_t)[d intValue];
        if ([i respondsToSelector:@selector(intValue)]) gFieldIsIntegrated    = (uint32_t)[i intValue];
        if (gFieldIsDisplayCoords) {
            gTailResolved = YES;
            gPointSpace = YES;
            FFLOG(@"inject: tail fields from config (display=0x%X integrated=0x%X)",
                  gFieldIsDisplayCoords, gFieldIsIntegrated);
            return;
        }
    }

    // Not resolved yet: the symbols may simply not be loaded. Do not mark this
    // as done, or a configure: call before the first available call would
    // permanently skip discovery.
    if (!pFingerEvent || !pGetInteger || !pSetInteger) return;

    gTailResolved = YES;
    IOHIDEventRef e = probeEvent();
    if (!e) return;

    int found = -1, count = 0;
    // 0x0B000B .. 0x0B0030 covers every layout Apple has shipped, with slack.
    for (uint32_t f = kDigitizerPage + 0x0B; f <= kDigitizerPage + 0x30; f++) {
        if (pGetInteger(e, f) != 0) { found = (int)f; count++; }
    }

    if (count == 1 && found > (int)(kDigitizerPage + 0x0B)) {
        gFieldIsIntegrated    = (uint32_t)found;
        gFieldIsDisplayCoords = (uint32_t)found - 1;
        // prove it: setting the flag must round-trip, or we do not use it
        pSetInteger(e, gFieldIsDisplayCoords, 1);
        BOOL ok = pGetInteger(e, gFieldIsDisplayCoords) == 1;
        if (ok) {
            gPointSpace = YES;
            FFLOG(@"inject: tail fields discovered (display=0x%X integrated=0x%X) - points",
                  gFieldIsDisplayCoords, gFieldIsIntegrated);
        } else {
            gFieldIsDisplayCoords = 0;
            gFieldIsIntegrated = 0;
            FFLOG(@"inject: display flag found but not settable - device pixels");
        }
    } else {
        FFLOG(@"inject: tail flags ambiguous (%d candidates) - device pixels", count);
    }
    CFRelease((CFTypeRef)e);
}

+ (NSDictionary *)fieldOverrides { return gOverrides; }

+ (void)configure:(NSDictionary *)inputSection {
    if (![inputSection isKindOfClass:NSDictionary.class]) return;
    gOverrides = [inputSection copy];
    gTailResolved = NO;              // re-resolve with the new override
    resolveTailFields();
}

// ------------------------------------------------------------------ public
+ (BOOL)available {
    if (!gTried) {
        gTried = YES;
        void *h = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY);
        if (!h) h = dlopen("/usr/lib/libIOKit.dylib", RTLD_LAZY);
        #define SYM(local, name) local = (typeof(local))FFDlsymIn(h ? h : RTLD_DEFAULT, name)
        SYM(pClientCreate,   "IOHIDEventSystemClientCreate");
        SYM(pClientDispatch, "IOHIDEventSystemClientDispatchEvent");
        SYM(pFingerEvent,    "IOHIDEventCreateDigitizerFingerEvent");
        SYM(pDigitizerEvent, "IOHIDEventCreateDigitizerEvent");
        SYM(pSetInteger,     "IOHIDEventSetIntegerValue");
        SYM(pGetInteger,     "IOHIDEventGetIntegerValue");
        SYM(pSetFloat,       "IOHIDEventSetFloatValue");
        SYM(pAppend,         "IOHIDEventAppendEvent");
        #undef SYM
        if (pClientCreate) gClient = pClientCreate(kCFAllocatorDefault);
        resolveTailFields();
        FFLOG(@"inject: client=%s finger=%s get=%s",
              gClient ? "yes" : "no", pFingerEvent ? "yes" : "no",
              pGetInteger ? "yes" : "no");
    }
    return gClient != NULL && (pFingerEvent || pDigitizerEvent) && pClientDispatch;
}

+ (NSString *)report {
    if (!gTried) [self available];
    return [NSString stringWithFormat:@"inject: client=%s finger=%s space=%s%s",
            gClient ? "yes" : "no", pFingerEvent ? "yes" : "no",
            gPointSpace ? "points" : "pixels",
            gFieldIsDisplayCoords ? "" : " (no display flag)"];
}

// One digitizer event. `mask` is a digitizer event mask, `touch` is the
// down/up state, `p` is in the main screen's coordinate space.
+ (IOHIDEventRef)newEvent:(int)mask touch:(BOOL)touch at:(CGPoint)p pressure:(double)pr {
    if (!pFingerEvent) return NULL;
    CGPoint q = eventSpace(p);
    uint64_t ts = mach_absolute_time();
    IOHIDEventRef e = pFingerEvent(kCFAllocatorDefault, ts,
                                   /*index*/    0,
                                   /*identity*/ gIdentity,
                                   /*eventMask*/ (uint32_t)mask,
                                   /*x*/ q.x, /*y*/ q.y, /*z*/ 0.0,
                                   /*tipPressure*/ pr,
                                   /*twist*/ 0.0,
                                   /*range*/ true,
                                   /*touch*/ touch ? true : false,
                                   /*options*/ 0);
    if (!e) return NULL;
    if (pSetInteger) {
        pSetInteger(e, kFieldType, kTransducerFinger);
        pSetInteger(e, kFieldIdentity, (int64_t)gIdentity);
        // The constructor leaves this at 0 unless it is told otherwise; it must
        // match the space `eventSpace` produced.
        if (gFieldIsDisplayCoords) pSetInteger(e, gFieldIsDisplayCoords, gPointSpace ? 1 : 0);
        if (gFieldIsIntegrated)    pSetInteger(e, gFieldIsIntegrated, 1);
        pSetInteger(e, kFieldRange, 1);
        pSetInteger(e, kFieldTouch, touch ? 1 : 0);
    }
    return e;
}

+ (BOOL)dispatch:(IOHIDEventRef)e {
    if (!e || !gClient || !pClientDispatch) return NO;
    pClientDispatch(gClient, e);
    CFRelease((CFTypeRef)e);
    return YES;
}

+ (BOOL)touchDown:(CGPoint)p {
    if (![self available]) return NO;
    if (gDown) return NO;
    gIdentity = (gIdentity % 0xFFFF) + 1;
    gLast = p;
    // A real start event carries range + touch + position + start in one go.
    IOHIDEventRef e = [self newEvent:(kMaskRange | kMaskTouch | kMaskPosition | kMaskStart)
                               touch:YES at:p pressure:1.0];
    BOOL ok = [self dispatch:e];
    // A second, position-only event makes the stream look like a real finger
    // that moved a fraction of a pixel - cheap, and it keeps continuity.
    IOHIDEventRef e2 = [self newEvent:(kMaskTouch | kMaskPosition) touch:YES at:p pressure:1.0];
    ok = [self dispatch:e2] && ok;
    gDown = ok;
    return ok;
}

+ (BOOL)drag:(CGPoint)p {
    if (!gDown || !gClient) return NO;
    gLast = p;
    IOHIDEventRef e = [self newEvent:(kMaskTouch | kMaskPosition) touch:YES at:p pressure:1.0];
    return [self dispatch:e];
}

+ (BOOL)lift {
    if (!gDown) return NO;
    IOHIDEventRef e = [self newEvent:(kMaskRange | kMaskTouch | kMaskPosition | kMaskStop)
                               touch:NO at:gLast pressure:0.0];
    BOOL ok = [self dispatch:e];
    gDown = NO;
    return ok;
}

+ (BOOL)isDown { return gDown; }

+ (BOOL)tap:(CGPoint)p duration:(NSTimeInterval)seconds {
    if (![self touchDown:p]) return NO;
    if (seconds > 0) {
        // caller runs on a background queue; a short usleep is fine there
        usleep((useconds_t)(seconds * 1e6));
    }
    return [self lift];
}

+ (BOOL)acquire:(NSString *)owner {
    if (gOwner && ![gOwner isEqualToString:owner]) return NO;
    gOwner = owner;
    return YES;
}

+ (void)release_:(NSString *)owner {
    if (![gOwner isEqualToString:owner]) return;
    gOwner = nil;
    [self lift];
}

+ (BOOL)selfTest {
    if (![self available]) {
        FFLOG(@"inject selfTest: IOKit symbols unavailable");
        return NO;
    }
    CGSize s = [UIScreen mainScreen].bounds.size;
    CGPoint mid = CGPointMake(s.width * 0.5, s.height * 0.5);
    BOOL down = [self touchDown:mid];
    BOOL moved = [self drag:CGPointMake(mid.x + 4, mid.y + 4)];
    BOOL up = [self lift];
    FFLOG(@"inject selfTest: down=%d move=%d up=%d space=%s",
          down, moved, up, gPointSpace ? "points" : "pixels");
    return down && up;
}

@end
