// USConfig.h - feature state + remote schema, shared by menu, ESP and aimbot.

#import <Foundation/Foundation.h>
#import "IL2CPP.h"

#define FF_FEATURE_CHANGED @"FFFeatureChanged"

// --- native feature keys. Keep them identical to menu/features.json so the
// --- proxy panel and the in-game menu drive the same switches.
extern NSString *kFFAimEnabled;      // aim.enabled
extern NSString *kFFAimSmooth;       // aim.smooth      (range 1..20)
extern NSString *kFFAimFov;          // aim.fov         (range 10..400)
extern NSString *kFFAimBone;         // aim.bone        (head|chest)
extern NSString *kFFAimVisible;      // aim.visible_only
extern NSString *kFFEspEnabled;      // esp.enabled
extern NSString *kFFEspBox;          // esp.box
extern NSString *kFFEspLine;         // esp.line
extern NSString *kFFEspName;         // esp.name
extern NSString *kFFEspHp;           // esp.hp
extern NSString *kFFEspDist;         // esp.distance
extern NSString *kFFEspFovCircle;    // esp.fov_circle
extern NSString *kFFEspMaxDist;      // esp.max_dist    (range 10..800)
extern NSString *kFFShadow;          // ui.hide_from_capture
extern NSString *kFFVerbose;         // debug.verbose
// Measured, not switched: degrees of camera rotation per point of finger
// travel, with sign, written by +[USModel calibrateAim] and read at bind so the
// aim keeps working after a relaunch.
extern NSString *kFFGainX;           // cal.gain_x
extern NSString *kFFGainY;           // cal.gain_y

@interface USConfig : NSObject

+ (instancetype)shared;

// features
- (BOOL)isOn:(NSString *)key;
- (NSInteger)intValue:(NSString *)key;
- (double)doubleValue:(NSString *)key;
- (NSString *)stringValue:(NSString *)key;
- (void)setOn:(BOOL)on forKey:(NSString *)key;
- (void)setRaw:(id)v forKey:(NSString *)key;

// schema / offsets (state/ff_native.json served by the proxy)
- (NSDictionary *)schema;
- (BOOL)schemaReady;
- (void)reloadSchema;

// True only for a map that can actually be bound: player.container_class,
// container_static, list_field and position_field present, and none of them a
// template value ("CHANGE_ME", "??", ...). A schema file is an override and has
// to earn it.
FOUNDATION_EXPORT BOOL FFSchemaUsable(NSDictionary *s);

// where the active map came from: "none" | "bundle" | "file" | "proxy"
- (NSString *)schemaSource;

// Delete a map that failed to bind, so it cannot poison the next launch.
- (void)rejectSchema;

// remote control: poll the proxy panel so toggles made in the WebView menu
// (or on the laptop) apply here without relaunching the game
- (void)startSyncWith:(NSString *)baseURL;
- (NSString *)proxyBase;

// persistence
- (void)save;
- (void)loadPersisted;                // NOT `load`: that name reads like the
                                     // +load hook dyld calls at image init
@end