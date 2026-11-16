// Stealth.h - reduce the client-side fingerprint.
//
// Be clear about what this does and does not do:
//
//   TIER 1  camouflage   free, always on. The image loads with a vendor-looking
//                        path and contains no identifying strings. Defeats
//                        `_dyld_get_image_name(x).contains("cheat")` and any
//                        scanner that only looks for loose .dylib load commands.
//
//   TIER 2  image hiding  real dyld enumeration interception. USLink rewrites
//                        the symbol-pointer slots the game itself calls
//                        through, so no inline hook engine is required. If
//                        Dobby happens to be linked in, that path is taken as
//                        well; if not, nothing is lost.
//
//   TIER 3  behavioural  smooth/fov/bone choices in the menu. Nothing in code
//                        can fix a session that looks like software.
//
// Run [USCloak install] first thing in the constructor.

#import <Foundation/Foundation.h>

typedef NS_ENUM(NSInteger, USCloakTier) {
    USCloakCamouflage = 1,
    USCloakHidden = 2,
    USCloakBehavioural = 3,
};

@interface USCloak : NSObject
+ (USCloakTier)install;          // returns the level actually achieved
+ (NSString *)report;              // one line, for the diagnostics panel
+ (BOOL)isHidden;                  // YES if dyld interception is live
@end