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
#import "Core/Integrity.h"
#import "Core/Inject.h"
#import "Menu/FFMenu.h"
#import "Obf.h"

static int gTries = 0;
static BOOL gBound = NO;

// ---------------------------------------------------------------- boot
static void FFBind(void) {
    // "Bound" is not a latch - it is a claim that has to stay true. A map can
    // bind and still read nothing (wrong container, empty list), and the old
    // `if (gBound) return;` meant the dylib never tried again.
    if (gBound && [USModel ready]) return;

    // A proxy map takes precedence if one was shipped or served; it is the
    // operator's override. Otherwise resolve everything from the live runtime,
    // which is the normal path and needs no files at all.
    NSDictionary *map = USConfig.shared.schema;
    BOOL fromProxy = FFSchemaUsable(map);

    // Optional digitizer field override. Without it the two tail indices are
    // discovered at runtime; with it, the operator's measured values win.
    if ([map[@"input"] isKindOfClass:NSDictionary.class]) [USInput configure:map[@"input"]];

    gBound = NO;
    if (fromProxy) {
        FFLOG(@"binding from the %@ schema", [USConfig.shared schemaSource]);
        gBound = [USModel bindWithSchema:map];
        if (!gBound) {
            // The map did not describe this build. Keeping it would repeat the
            // same failure every three seconds forever, so drop it and let the
            // live resolver - the path that needs no files - take over.
            FFLOG(@"schema failed to bind, falling back to the live runtime:\n%@",
                  [USModel diagnostics]);
            [USConfig.shared rejectSchema];
            [USPanel.shared refresh];
        }
    }
    if (!gBound) {
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

// Re-resolve until it works, for as long as the process lives.
//
// il2cpp can only walk the player class once an actual match exists, and the
// entity classes are loaded lazily - so a bind at launch is *expected* to fail
// in the lobby. The previous version gave up after 60 s, which is exactly when
// the player finishes loading into a match.
static void FFArmBindRetry(int n) {
    // 3 s while a match is probably starting, then back off to 10 s. autoBind
    // walks every class in every image, so it is not free.
    NSTimeInterval gap = (n < 20) ? 3.0 : 10.0;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(gap * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (![USModel ready]) FFBind();
        FFArmBindRetry(n + 1);
    });
}

static void FFBoot(void) {
    USConfig *st = USConfig.shared;
    if (![IL2CPP loadAPI]) {
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

// ---------------------------------------------------------------- entry
//
// Nothing below may touch UIKit or dyld at LOAD time.
//
// This dylib is pulled in by an LC_LOAD_DYLIB in the app's main executable, so
// its initialisers run during dyld's own image initialisation - before the
// app's initialisers, before main(), and while dyld is still building the very
// image list we exist to hide from. Doing real work here was wrong twice over:
//
//   * UIKit: there is no UIApplication yet, so creating a window or registering
//     a notification observer is undefined (and the app dies before its first
//     frame - a crash on tap, before any splash screen)
//   * dyld: _dyld_image_count / _dyld_get_image_header from an image initialiser
//     walk a list that is still being built, and rewriting another image's GOT
//     slots with vm_protect races the loader that is about to fill them in
//
// So the initialiser only schedules. The real work happens on the main queue,
// as soon as UIApplication exists - which is still before Unity starts, so the
// stealth layer is installed long before the game looks at anything.

static void USBootWhenReady(int tries);

__attribute__((constructor))
static void USInit(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        USBootWhenReady(0);
    });
}

static void USBootWhenReady(int tries) {
    // 600 x 0.05 s = 30 s. UIApplication exists within a few run-loop turns of
    // UIApplicationMain, so this normally succeeds on the first attempt.
    if (!UIApplication.sharedApplication) {
        if (tries < 600) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                         (int64_t)(0.05 * NSEC_PER_SEC)),
                           dispatch_get_main_queue(), ^{
                USBootWhenReady(tries + 1);
            });
        } else {
            FFLOG(@"gave up waiting for UIApplication");
        }
        return;
    }

    // verbosity first, so the lines below respect it. Silent unless asked.
    FFSetVerbose([USConfig.shared isOn:kFFVerbose]);
    USCloakTier tier = [USCloak install];   // stealth first, before anything
    // Then the client-integrity answers: a checker that asks what this app is
    // gets the App Store answer for the artefacts that only exist because the
    // build was re-signed. See Core/Integrity.m and ANTIDETECT 0.1.
    [USIntegrity install];
    FFLOG(@"loaded (pid %d) %@", getpid(), [USCloak report]);
    (void)tier;

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        FFBoot();
        FFArmBindRetry(0);
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