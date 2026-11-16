// USPanel.h - native floating panel. Same visual language as menu/menu.html so
// the WebView menu and this one read as one product.

#import <UIKit/UIKit.h>

@interface USPanel : NSObject
+ (instancetype)shared;
- (void)install;      // creates the overlay window + floating button
- (void)refresh;      // re-reads USConfig (called after a proxy sync)
@end