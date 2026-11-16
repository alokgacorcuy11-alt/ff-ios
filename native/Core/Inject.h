// Inject.h - synthetic touch, delivered through the HID event system.
//
// Why this and not a fabricated UITouch: touches that arrive through the HID
// system are processed by SpringBoard/backboardd exactly like a real finger, so
// the game sees a normal touch stream. Nothing in the network layer or in
// movement validation can tell it apart from a human, which is the whole point.
//
// Every symbol here comes from IOKit's IOHIDEventSystemClient / IOHIDEvent API,
// resolved with dlsym. Nothing is invented. `+selfTest` lets you confirm the
// path works on your device before aiming a single shot.

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

@interface USInput : NSObject

/// Resolve the IOKit symbols. YES if touch injection is available.
+ (BOOL)available;
+ (NSString *)report;

/// Post a tap at a point in the main screen's coordinate space.
+ (BOOL)tap:(CGPoint)p duration:(NSTimeInterval)seconds;

/// Begin a drag. Follow it with -drag:, end it with -lift.
+ (BOOL)touchDown:(CGPoint)p;
+ (BOOL)drag:(CGPoint)p;
+ (BOOL)lift;

/// True while a synthetic finger is down.
+ (BOOL)isDown;

/// Guard so a second feature cannot hijack a finger that is already down.
+ (BOOL)acquire:(NSString *)owner;
+ (void)release_:(NSString *)owner;

/// Optional override for the two tail field indices of the digitizer page,
/// e.g. `schema["input"] = @{ @"display_coords_index": @0x0B0018 }`. Without it
/// they are discovered at runtime (see Inject.m). An empty / nil dictionary
/// clears the override and re-runs discovery.
+ (void)configure:(NSDictionary *)inputSection;

/// The override currently in effect, or nil. Read-only.
+ (NSDictionary *)fieldOverrides;

/// Dispatch a touch at the screen centre and lift it. Returns NO if the HID
/// client could not be created or the event was rejected.
+ (BOOL)selfTest;

@end