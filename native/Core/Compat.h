// Compat.h - keep the dylib at iOS 12.0 minimum.
//
// The App Store build of Free Fire still supports old devices, and a dylib that
// calls iOS 13+ API unconditionally would raise the IPA's effective minimum or
// crash on iOS 12. Everything version-sensitive goes through here.

#import <UIKit/UIKit.h>

#ifndef FF_MIN_IOS
#define FF_MIN_IOS 12.0
#endif

// UIFont monospacedSystemFontOfSize:weight: is 13.0+. Fall back to Menlo on 12.
static inline UIFont *FFMono(CGFloat size, BOOL bold) {
    if (@available(iOS 13.0, *)) {
        return [UIFont monospacedSystemFontOfSize:size
                                           weight:bold ? UIFontWeightBold : UIFontWeightRegular];
    }
    return [UIFont fontWithName:(bold ? @"Menlo-Bold" : @"Menlo") size:size]
           ?: [UIFont boldSystemFontOfSize:size];
}

// UISegmentedControl.selectedSegmentTintColor is 13.0+.
static inline void FFSetSegmentTint(UISegmentedControl *seg, UIColor *color) {
    if (!seg) return;
    if (@available(iOS 13.0, *)) {
        seg.selectedSegmentTintColor = color;
    } else {
        seg.tintColor = color;
    }
}

// UIWindowScene exists from 13.0; on 12 we must use the legacy UIWindow path.
static inline UIWindow *FFMakeWindow(void) {
    UIWindow *w = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    return w;
}

// traitCollectionDidChange / safe area are fine on 12; nothing else needed.