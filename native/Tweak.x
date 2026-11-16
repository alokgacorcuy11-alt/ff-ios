// Tweak.x - dylib entry point.
//
// A plain injected dylib, not a substrate tweak: no %hook, no libsubstrate at
// runtime. Everything goes through the IL2CPP API (read/write managed fields,
// invoke managed methods, resolve icalls), which is why it works on a re-signed
// Free Fire IPA with no jailbreak.

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#include <unistd.h>
#import "Core/IL2CPP.h"
#import "Core/FFState.h"
#import "Core/Game.h"
#import "Core/ESP.h"
#import "Core/Compat.h"
#import "Core/Stealth.h"
#import "Core/Inject.h"
#import "Menu/FFMenu.h"
#import "Obf.h"

static int gTries = 0;
static BOOL gBound = NO;

// ---------------------------------------------------------------- boot
static void FFBind(void) {
    if (gBound) return;

    // A proxy map takes precedence if one was shipped or served; it is the
    // operator's override. Otherwise resolve everything from the live runtime,
    // which is the normal path and needs no files at all.
    NSDictionary *map = USConfig.shared.schema;
    BOOL fromProxy = [map[@"player"][@"container_class"] length] > 0;

    // Optional digitizer field override. Without it the two tail indices are
    // discovered at runtime; with it, the operator's measured values win.
    if ([map[@"input"] isKindOfClass:NSDictionary.class]) [USInput configure:map[@"input"]];

    if (fromProxy) {
        FFLOG(@"binding from the proxy schema");
        gBound = [USModel bindWithSchema:map];
    } else {
        FFLOG(@"resolving from the live runtime");
        gBound = [USModel autoBind];
    }

    if (gBound) {
        FFLOG(@"game bound: %@", [USModel resolveReport]);
    } else {
        FFLOG(@"bind failed:\n%@", [USModel diagnostics]);
    }
    [USPanel.shared refresh];
}

static void FFBoot(void) {
    USConfig *st = USConfig.shared;
    if (![IL2CPP load]) {
        if (++gTries < 120) {                 // 120 x 0.5 s = 60 s
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                           dispatch_get_main_queue(), ^{ FFBoot(); });
        } else {
            FFLOG(@"gave up waiting for the il2cpp runtime");
        }
        return;
    }
    gTries = 0;

    NSString *base = st.schema[@"proxy"];
    if ([base isKindOfClass:NSString.class] && base.length) {
        [st startSyncWith:base];
        FFLOG(@"syncing with %@", base);
    }
    FFBind();
    [USPanel.shared install];
    if ([st isOn:kFFEspEnabled] || [st isOn:kFFAimEnabled]) [[USOverlay shared] start];
}

// Re-resolve when the match changes: il2cpp can only walk the player class once
// an actual match exists, and entity classes are loaded lazily.
static void FFArmBindRetry(void) {
    static int n = 0;
    if (gBound || n++ > 20) return;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        FFBind();
        FFArmBindRetry();
    });
}

__attribute__((constructor))
static void USInit(void) {
    // verbosity first, so the lines below respect it. Silent unless asked.
    FFSetVerbose([USConfig.shared isOn:kFFVerbose]);
    USCloakTier tier = [USCloak install];   // stealth first, before anything
    FFLOG(@"loaded (pid %d) %@", getpid(), [USCloak report]);
    (void)tier;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        FFBoot();
        FFArmBindRetry();
    });
    // touch injection is independent of il2cpp; get the symbols early so the
    // diagnostics panel can report the truth even before a match exists
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_BACKGROUND, 0), ^{
        [USInput available];
    });

    // The display link is the only thing that runs the aimbot, so it must come
    // up whenever EITHER switch turns on - not only at boot, and not only for
    // ESP. This is what makes enabling aim from the proxy panel work.
    [[NSNotificationCenter defaultCenter] addObserverForName:FF_FEATURE_CHANGED
                                                      object:nil
                                                       queue:[NSOperationQueue mainQueue]
                                                  usingBlock:^(NSNotification *n) {
        NSString *key = [n.object isKindOfClass:NSString.class] ? n.object : nil;
        if (!key) return;
        if (![key isEqualToString:kFFEspEnabled] && ![key isEqualToString:kFFAimEnabled]) return;
        USConfig *s = USConfig.shared;
        if ([s isOn:kFFEspEnabled] || [s isOn:kFFAimEnabled]) [[USOverlay shared] start];
    }];
}