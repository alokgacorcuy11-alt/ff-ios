// ESP.h - overlay renderer. Draws into its own UIWindow above the game's
// render surface, driven by CADisplayLink. No hooking of the renderer needed.

#import <UIKit/UIKit.h>
#import "Game.h"

@interface USOverlay : UIView
+ (instancetype)shared;
- (void)start;
- (void)stop;
- (void)setHiddenFromCapture:(BOOL)hidden;
@end