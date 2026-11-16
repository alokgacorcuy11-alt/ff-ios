// Game.h - Free Fire reader.
//
// Two ways to bind:
//
//   [USModel autoBind]                 <- the default. USMap walks the live
//                                        runtime, works out the schema, and
//                                        binds. No file, no device dump, no
//                                        offsets, survives a game patch.
//   [USModel bindWithSchema:dict]      <- optional override, e.g. a hand-tuned
//                                        ff_native.json served by the proxy.
//
// Nothing in here hardcodes a class name, a field name or an offset. Even the
// array header layout is read from il2cpp_offset_of_array_length_in_array_object_header.

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import "IL2CPP.h"

@interface USEntity : NSObject
@property (nonatomic) Il2CppObject *obj;      // borrowed, live during the frame
@property (nonatomic) Vec3 world;
@property (nonatomic) CGPoint screen;         // filled by worldToScreen
@property (nonatomic) BOOL onScreen;
@property (nonatomic) float depth;            // metres in front of the camera
@property (nonatomic) float hp;
@property (nonatomic) float maxHp;
@property (nonatomic) int32_t team;
@property (nonatomic) BOOL isLocal;
@property (nonatomic) BOOL isBot;
@property (nonatomic, copy) NSString *name;
@property (nonatomic) float distance;
@end

@interface USModel : NSObject

// --- binding
+ (BOOL)autoBind;                              // resolve + bind, on any thread
+ (BOOL)bindWithSchema:(NSDictionary *)schema;
+ (BOOL)ready;
+ (NSDictionary *)activeMap;
+ (NSString *)resolveReport;                   // what autoBind worked out
+ (NSArray<NSString *> *)bindErrors;

// --- read
+ (USEntity *)localPlayer;
+ (NSArray<USEntity *> *)players;              // excludes the local player
+ (BOOL)worldToScreen:(Vec3)world out:(CGPoint *)out depth:(float *)depth;
+ (BOOL)w2sReady;
+ (NSString *)w2sMode;                         // "icall" | "matrix" | "none"
+ (NSString *)w2sReport;                       // which candidate resolved, and why

// --- act
/// Aim at `target`. Needs a measured gain: see +calibrateAim. Returns NO and
/// logs once if the aimbot has never been calibrated on this device.
+ (BOOL)aimAt:(USEntity *)target bone:(NSString *)bone smooth:(int)smooth;
+ (void)tick;                                  // per frame: w2s probing + aim watchdog
+ (void)releaseAim;                            // drop any injected touch

/// Measure the camera's rotation per point of finger travel, with sign. Moves
/// the real camera, so call it in a match and not from the display link.
+ (BOOL)calibrateAim;
+ (BOOL)aimCalibrated;

+ (NSString *)diagnostics;
@end