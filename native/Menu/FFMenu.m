// USPanel.m

#import "FFMenu.h"
#import "FFState.h"
#import "Game.h"
#import "ESP.h"
#import "Compat.h"
#import "Stealth.h"
#import "Inject.h"
#import "Obf.h"
#include <math.h>

#define ACC  [UIColor colorWithRed:0.71 green:1.00 blue:0.24 alpha:1.0]
#define BG   [UIColor colorWithRed:0.031 green:0.039 blue:0.031 alpha:0.97]
#define CARD [UIColor colorWithRed:0.063 green:0.075 blue:0.059 alpha:1.0]
#define LINE [UIColor colorWithRed:0.137 green:0.165 blue:0.129 alpha:1.0]
#define DIM  [UIColor colorWithRed:0.467 green:0.514 blue:0.435 alpha:1.0]
#define MONO(sz) FFMono((sz), YES)

typedef NS_ENUM(NSInteger, RowKind) { RowToggle, RowRange, RowSegment, RowInfo };

// ---------------------------------------------------------------- passthrough
//
// A clear window above the game must not eat touches.
//
// UIView's -hitTest:withEvent: returns the view ITSELF when no subview matches,
// and a UIWindow does the same - so an empty, fully transparent window still
// becomes the touch target for every tap on screen. The game keeps rendering
// normally (it is just a window below ours) but nothing responds: exactly the
// "the app is up, login does nothing" symptom. A full-screen overlay needs both
// halves to decline the hit - the window and its root view - because the window
// only consults subviews and then falls back to itself.
@interface USPassthroughWindow : UIWindow
@end

@implementation USPassthroughWindow
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *v = [super hitTest:point withEvent:event];
    return (v == self) ? nil : v;      // not ours -> let the window below have it
}
@end

@interface USPassthroughView : UIView
@end

@implementation USPassthroughView
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    UIView *v = [super hitTest:point withEvent:event];
    return (v == self) ? nil : v;      // bare background -> not a target
}
@end

// ---------------------------------------------------------------- rows
@interface USRow : NSObject
@property (nonatomic, copy) NSString *group, *label, *key, *unit, *hint;
@property (nonatomic) RowKind kind;
@property (nonatomic) double min, max, step;
@property (nonatomic, copy) NSArray<NSString *> *options;
@end
@implementation USRow @end

@interface USPanel () <UIScrollViewDelegate>
@property (nonatomic) UIWindow *win;
@property (nonatomic) UIButton *orb;
@property (nonatomic) UIView *panel;
@property (nonatomic) NSMutableArray<USRow *> *rows;
@property (nonatomic) NSMutableDictionary<NSString *, UILabel *> *valueLabels;
@property (nonatomic) NSMutableArray<UISlider *> *sliders;
@end

@implementation USPanel {
    CGPoint _orbHome;
    BOOL _open;
}

+ (instancetype)shared {
    static USPanel *m;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ m = [USPanel new]; });
    return m;
}

- (instancetype)init {
    if ((self = [super init])) {
        _valueLabels = [NSMutableDictionary dictionary];
        _sliders = [NSMutableArray array];
        [self buildRows];
    }
    return self;
}

- (void)buildRows {
    self.rows = [NSMutableArray array];
    // The last parameter must NOT be called `kind`: the preprocessor replaces
    // every occurrence of a parameter name, including the token after the dot in
    // `r.kind`, so `r.kind = kind` became `r.RowToggle = RowToggle` and clang
    // reported "property 'RowToggle' not found on object of type 'USRow *'" nine
    // times. A parameter name that collides with a member name is a trap that
    // reads as correct.
    #define R(grp, lbl, keyName, kindValue) do {                            \
        USRow *row = [USRow new];                                           \
        row.group = grp; row.label = lbl; row.key = keyName;                \
        row.kind = kindValue; [self.rows addObject:row];                    \
    } while (0)

    R(@"TARGETING", @"Enable",      kFFAimEnabled,  RowToggle);
    R(@"TARGETING", @"Smooth",      kFFAimSmooth,   RowRange);
    R(@"TARGETING", @"FOV",         kFFAimFov,      RowRange);
    R(@"TARGETING", @"Target Bone", kFFAimBone,     RowSegment);
    R(@"TARGETING", @"On Screen Only", kFFAimVisible, RowToggle);

    R(@"VISUALS", @"Enable",        kFFEspEnabled,  RowToggle);
    R(@"VISUALS", @"Box",           kFFEspBox,      RowToggle);
    R(@"VISUALS", @"Snapline",      kFFEspLine,     RowToggle);
    R(@"VISUALS", @"Name",          kFFEspName,     RowToggle);
    R(@"VISUALS", @"Health Bar",    kFFEspHp,       RowToggle);
    R(@"VISUALS", @"Distance",      kFFEspDist,     RowToggle);
    R(@"VISUALS", @"FOV Circle",    kFFEspFovCircle,RowToggle);
    R(@"VISUALS", @"Max Distance",  kFFEspMaxDist,  RowRange);

    R(@"SYSTEM",  @"Hide From Recording", kFFShadow,   RowToggle);
    R(@"SYSTEM",  @"Verbose Logs",        kFFVerbose,  RowToggle);
    #undef R

    for (USRow *r in self.rows) {
        if ([r.key isEqualToString:kFFAimSmooth])  { r.min = 2;  r.max = 20;  r.step = 1; }
        if ([r.key isEqualToString:kFFAimFov])     { r.min = 10; r.max = 400; r.step = 10; }
        if ([r.key isEqualToString:kFFEspMaxDist]) { r.min = 10; r.max = 800; r.step = 10; r.unit = @"m"; }
        if ([r.key isEqualToString:kFFAimBone])    { r.options = @[@"head", @"chest"]; }
        r.hint = [self hintFor:r.key];
    }
}

- (NSString *)hintFor:(NSString *)key {
    if ([key isEqualToString:kFFAimEnabled]) return @"Lock onto the closest target to the crosshair";
    if ([key isEqualToString:kFFAimSmooth])  return @"Higher = slower, less visible";
    if ([key isEqualToString:kFFAimFov])     return @"Pixel radius from screen centre";
    if ([key isEqualToString:kFFEspEnabled]) return @"Draw over the render surface";
    if ([key isEqualToString:kFFEspMaxDist]) return @"Cull players beyond this range";
    if ([key isEqualToString:kFFShadow])     return @"Excludes the overlay from captures";
    return nil;
}

// ---------------------------------------------------------------- install
- (void)install {
    dispatch_async(dispatch_get_main_queue(), ^{
        USPassthroughWindow *w =
            [[USPassthroughWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
        w.windowLevel = UIWindowLevelNormal + 256;
        w.backgroundColor = UIColor.clearColor;
        // The root view must be pass-through too: a plain UIView would return
        // itself from hitTest for every point outside the orb, which is the
        // whole screen. Set the view before installing the controller so the
        // window never hosts a root view of the wrong class.
        UIViewController *host = [UIViewController new];
        host.view = [[USPassthroughView alloc] initWithFrame:w.bounds];
        host.view.backgroundColor = UIColor.clearColor;
        host.view.autoresizingMask = UIViewAutoresizingFlexibleWidth |
                                     UIViewAutoresizingFlexibleHeight;
        w.rootViewController = host;
        w.hidden = NO;

        CGFloat s = 52;
        self->_orbHome = CGPointMake(14, w.bounds.size.height - s - 90);
        self.orb = [UIButton buttonWithType:UIButtonTypeCustom];
        self.orb.frame = CGRectMake(self->_orbHome.x, self->_orbHome.y, s, s);
        self.orb.backgroundColor = ACC;
        self.orb.layer.cornerRadius = s / 2;
        self.orb.layer.borderWidth = 1;
        self.orb.layer.borderColor = [UIColor colorWithWhite:0 alpha:0.35].CGColor;
        self.orb.layer.shadowColor = ACC.CGColor;
        self.orb.layer.shadowRadius = 12;
        self.orb.layer.shadowOpacity = 0.55;
        self.orb.layer.shadowOffset = CGSizeZero;
        self.orb.titleLabel.font = MONO(15);
        [self.orb setTitle:@"US" forState:UIControlStateNormal];
        [self.orb setTitleColor:[UIColor colorWithWhite:0.04 alpha:1] forState:UIControlStateNormal];
        [self.orb addTarget:self action:@selector(togglePanel) forControlEvents:UIControlEventTouchUpInside];

        UIPanGestureRecognizer *pan = [[UIPanGestureRecognizer alloc] initWithTarget:self
                                                                            action:@selector(drag:)];
        [self.orb addGestureRecognizer:pan];

        [w.rootViewController.view addSubview:self.orb];
        self->_win = w;
        FFLOG(@"menu installed");
    });
}

- (void)drag:(UIPanGestureRecognizer *)g {
    CGPoint t = [g translationInView:self.orb.superview];
    self.orb.center = CGPointMake(self.orb.center.x + t.x, self.orb.center.y + t.y);
    [g setTranslation:CGPointZero inView:self.orb.superview];
    if (g.state == UIGestureRecognizerStateEnded) {
        CGRect b = self.orb.superview.bounds;
        CGFloat x = MAX(8, MIN(b.size.width - self.orb.bounds.size.width - 8, self.orb.frame.origin.x));
        CGFloat y = MAX(48, MIN(b.size.height - self.orb.bounds.size.height - 24, self.orb.frame.origin.y));
        [UIView animateWithDuration:0.18 animations:^{
            self.orb.frame = CGRectMake(x, y, self.orb.bounds.size.width, self.orb.bounds.size.height);
        }];
    }
}

// ---------------------------------------------------------------- panel
- (void)togglePanel {
    if (_open) { [self closePanel]; return; }
    _open = YES;

    CGRect screen = self.win.bounds;
    CGFloat w = MIN(screen.size.width - 24, 400);
    CGFloat h = MIN(screen.size.height - 120, 620);
    self.panel = [[UIView alloc] initWithFrame:CGRectMake((screen.size.width - w) / 2,
                                                        (screen.size.height - h) / 2, w, h)];
    self.panel.backgroundColor = BG;
    self.panel.layer.cornerRadius = 16;
    self.panel.layer.borderWidth = 1;
    self.panel.layer.borderColor = LINE.CGColor;
    self.panel.clipsToBounds = YES;
    self.panel.alpha = 0;
    self.panel.transform = CGAffineTransformMakeScale(0.94, 0.94);

    [self buildPanelContents:self.panel.bounds];
    [self.win.rootViewController.view addSubview:self.panel];

    [UIView animateWithDuration:0.18 animations:^{
        self.panel.alpha = 1;
        self.panel.transform = CGAffineTransformIdentity;
    }];
}

- (void)closePanel {
    _open = NO;
    UIView *p = self.panel;
    [UIView animateWithDuration:0.14 animations:^{
        p.alpha = 0;
        p.transform = CGAffineTransformMakeScale(0.94, 0.94);
    } completion:^(BOOL f) { [p removeFromSuperview]; }];
    self.panel = nil;
}

- (void)buildPanelContents:(CGRect)bounds {
    CGFloat pad = 14, y = 0;
    CGFloat w = bounds.size.width;

    // header
    UIView *hdr = [[UIView alloc] initWithFrame:CGRectMake(0, 0, w, 56)];
    hdr.backgroundColor = [UIColor colorWithWhite:0.02 alpha:1];
    UILabel *title = [[UILabel alloc] initWithFrame:CGRectMake(pad, 14, w - 120, 18)];
    title.text = @"SERVICES";
    title.font = MONO(14);
    title.textColor = [UIColor colorWithWhite:0.91 alpha:1];
    UILabel *sub = [[UILabel alloc] initWithFrame:CGRectMake(pad, 32, w - 120, 12)];
    sub.text = @"NATIVE LAYER";
    sub.font = MONO(8);
    sub.textColor = DIM;
    [hdr addSubview:title];
    [hdr addSubview:sub];

    UIButton *x = [UIButton buttonWithType:UIButtonTypeCustom];
    x.frame = CGRectMake(w - 44, 12, 32, 32);
    x.layer.cornerRadius = 8;
    x.layer.borderWidth = 1;
    x.layer.borderColor = LINE.CGColor;
    [x setTitle:@"x" forState:UIControlStateNormal];
    [x setTitleColor:DIM forState:UIControlStateNormal];
    x.titleLabel.font = MONO(13);
    [x addTarget:self action:@selector(closePanel) forControlEvents:UIControlEventTouchUpInside];
    [hdr addSubview:x];
    [self.panel addSubview:hdr];
    y = 56;

    UIScrollView *sv = [[UIScrollView alloc] initWithFrame:CGRectMake(0, y, w, bounds.size.height - y - 104)];
    sv.showsVerticalScrollIndicator = NO;
    sv.alwaysBounceVertical = YES;
    [self.panel addSubview:sv];
    CGFloat sy = 6;
    NSString *currentGroup = nil;

    for (USRow *r in self.rows) {
        if (![r.group isEqualToString:currentGroup ?: @""]) {
            currentGroup = r.group;
            UILabel *g = [[UILabel alloc] initWithFrame:CGRectMake(pad, sy + 8, w - pad * 2, 14)];
            g.text = currentGroup;
            g.font = MONO(9);
            g.textColor = DIM;
            [sv addSubview:g];
            sy += 26;
        }
        UIView *card = [self cardFor:r width:w - pad * 2];
        card.frame = CGRectMake(pad, sy, w - pad * 2, card.frame.size.height);
        [sv addSubview:card];
        sy += card.frame.size.height + 6;
    }
    sv.contentSize = CGSizeMake(w, sy + 10);

    // footer: two rows now, because the aim needs a calibration action that
    // moves the camera - it cannot be a silent default.
    const CGFloat footerH = 104;
    UIView *ft = [[UIView alloc] initWithFrame:CGRectMake(0, bounds.size.height - footerH, w, footerH)];
    ft.backgroundColor = [UIColor colorWithWhite:0.02 alpha:1];

    CGFloat halfW = (w - pad * 3) / 2;
    UIButton *save = [UIButton buttonWithType:UIButtonTypeCustom];
    save.frame = CGRectMake(pad, 8, halfW, 36);
    save.layer.cornerRadius = 10;
    save.layer.borderWidth = 1;
    save.layer.borderColor = ACC.CGColor;
    [save setTitle:@"SAVE" forState:UIControlStateNormal];
    [save setTitleColor:ACC forState:UIControlStateNormal];
    save.titleLabel.font = MONO(11);
    [save addTarget:self action:@selector(doSave) forControlEvents:UIControlEventTouchUpInside];

    UIButton *cal = [UIButton buttonWithType:UIButtonTypeCustom];
    cal.frame = CGRectMake(pad * 2 + halfW, 8, halfW, 36);
    cal.layer.cornerRadius = 10;
    cal.layer.borderWidth = 1;
    cal.layer.borderColor = LINE.CGColor;
    [cal setTitle:@"CALIBRATE" forState:UIControlStateNormal];
    [cal setTitleColor:DIM forState:UIControlStateNormal];
    cal.titleLabel.font = MONO(11);
    [cal addTarget:self action:@selector(doCalibrate) forControlEvents:UIControlEventTouchUpInside];

    UIButton *touch = [UIButton buttonWithType:UIButtonTypeCustom];
    touch.frame = CGRectMake(pad, 52, halfW, 36);
    touch.layer.cornerRadius = 10;
    touch.layer.borderWidth = 1;
    touch.layer.borderColor = LINE.CGColor;
    [touch setTitle:@"TEST TOUCH" forState:UIControlStateNormal];
    [touch setTitleColor:DIM forState:UIControlStateNormal];
    touch.titleLabel.font = MONO(11);
    [touch addTarget:self action:@selector(doTouchTest) forControlEvents:UIControlEventTouchUpInside];

    UIButton *diag = [UIButton buttonWithType:UIButtonTypeCustom];
    diag.frame = CGRectMake(pad * 2 + halfW, 52, halfW, 36);
    diag.layer.cornerRadius = 10;
    diag.layer.borderWidth = 1;
    diag.layer.borderColor = LINE.CGColor;
    [diag setTitle:@"DIAGNOSTICS" forState:UIControlStateNormal];
    [diag setTitleColor:DIM forState:UIControlStateNormal];
    diag.titleLabel.font = MONO(11);
    [diag addTarget:self action:@selector(showDiag) forControlEvents:UIControlEventTouchUpInside];

    [ft addSubview:save];
    [ft addSubview:cal];
    [ft addSubview:touch];
    [ft addSubview:diag];
    [self.panel addSubview:ft];
}

- (UIView *)cardFor:(USRow *)r width:(CGFloat)w {
    CGFloat h = 58;
    UIView *card = [[UIView alloc] initWithFrame:CGRectMake(0, 0, w, h)];
    card.backgroundColor = CARD;
    card.layer.cornerRadius = 12;
    card.layer.borderWidth = 1;
    card.layer.borderColor = LINE.CGColor;

    UILabel *lbl = [[UILabel alloc] initWithFrame:CGRectMake(13, 12, w - 110, 16)];
    lbl.text = r.label;
    lbl.font = MONO(12.5);
    lbl.textColor = [UIColor colorWithWhite:0.88 alpha:1];
    [card addSubview:lbl];

    if (r.hint.length) {
        UILabel *h = [[UILabel alloc] initWithFrame:CGRectMake(13, 30, w - 110, 22)];
        h.text = r.hint;
        h.font = MONO(9);
        h.textColor = DIM;
        h.numberOfLines = 2;
        [card addSubview:h];
    }

    if (r.kind == RowToggle) {
        UISwitch *sw = [[UISwitch alloc] initWithFrame:CGRectMake(w - 64, 14, 51, 31)];
        sw.onTintColor = ACC;
        sw.transform = CGAffineTransformMakeScale(0.86, 0.86);
        sw.on = [USConfig.shared isOn:r.key];
        sw.tag = [self.rows indexOfObject:r];
        [sw addTarget:self action:@selector(toggled:) forControlEvents:UIControlEventValueChanged];
        [card addSubview:sw];
    } else if (r.kind == RowRange) {
        UISlider *s = [[UISlider alloc] initWithFrame:CGRectMake(w * 0.42, 20, w * 0.42, 20)];
        s.minimumValue = (float)r.min;
        s.maximumValue = (float)r.max;
        s.value = (float)[USConfig.shared doubleValue:r.key];
        s.minimumTrackTintColor = ACC;
        s.maximumTrackTintColor = LINE;
        s.tag = [self.rows indexOfObject:r];
        [s addTarget:self action:@selector(slid:) forControlEvents:UIControlEventValueChanged];
        [card addSubview:s];

        UILabel *v = [[UILabel alloc] initWithFrame:CGRectMake(w - 56, 20, 44, 16)];
        v.font = MONO(10);
        v.textColor = ACC;
        v.textAlignment = NSTextAlignmentRight;
        v.text = [NSString stringWithFormat:@"%.0f%@", s.value, r.unit ?: @""];
        self.valueLabels[r.key] = v;
        [card addSubview:v];
    } else if (r.kind == RowSegment) {
        UISegmentedControl *seg = [[UISegmentedControl alloc] initWithItems:r.options];
        seg.frame = CGRectMake(w - 140, 15, 126, 30);
        FFSetSegmentTint(seg, ACC);
        NSString *cur = [USConfig.shared stringValue:r.key];
        NSUInteger idx = [r.options indexOfObject:cur];
        seg.selectedSegmentIndex = (idx == NSNotFound) ? 0 : (NSInteger)idx;
        seg.tag = [self.rows indexOfObject:r];
        [seg addTarget:self action:@selector(segged:) forControlEvents:UIControlEventValueChanged];
        [card addSubview:seg];
    }
    return card;
}

// ---------------------------------------------------------------- actions
- (void)toggled:(UISwitch *)sw {
    USRow *r = self.rows[sw.tag];
    [USConfig.shared setOn:sw.isOn forKey:r.key];
    if ([r.key isEqualToString:kFFEspEnabled] || [r.key isEqualToString:kFFAimEnabled]) {
        // The overlay hosts both: stopping it because ESP went off would kill
        // the aimbot too.
        USConfig *s = USConfig.shared;
        if ([s isOn:kFFEspEnabled] || [s isOn:kFFAimEnabled]) [[USOverlay shared] start];
        else [[USOverlay shared] stop];
    }
    if ([r.key isEqualToString:kFFShadow]) {
        [[USOverlay shared] setHiddenFromCapture:sw.isOn];
    }
    if ([r.key isEqualToString:kFFVerbose]) {
        FFSetVerbose(sw.isOn);
    }
}

- (void)slid:(UISlider *)s {
    USRow *r = self.rows[s.tag];
    double step = r.step > 0 ? r.step : 1;
    double val = round(s.value / step) * step;
    [USConfig.shared setRaw:@(val) forKey:r.key];
    self.valueLabels[r.key].text = [NSString stringWithFormat:@"%.0f%@", val, r.unit ?: @""];
}

- (void)segged:(UISegmentedControl *)seg {
    USRow *r = self.rows[seg.tag];
    NSString *v = r.options[seg.selectedSegmentIndex];
    [USConfig.shared setRaw:v forKey:r.key];
}

// The touch path has to be proved on the device before anything depends on it:
// if the tap lands in the wrong place, saying "space=pixels" tells you why,
// but only a real tap tells you it happened. Do this in the lobby or a training
// range - it is a genuine tap at the centre of the screen.
- (void)doTouchTest {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        BOOL ok = [USInput selfTest];
        NSString *r = [USInput report];
        dispatch_async(dispatch_get_main_queue(), ^{
            UIAlertController *a = [UIAlertController
                alertControllerWithTitle:(ok ? @"TOUCH OK" : @"TOUCH FAILED")
                                 message:r
                          preferredStyle:UIAlertControllerStyleAlert];
            [a addAction:[UIAlertAction actionWithTitle:@"OK"
                                                  style:UIAlertActionStyleDefault
                                                handler:nil]];
            [self.win.rootViewController presentViewController:a animated:YES completion:nil];
        });
    });
}

- (void)doSave {
    [USConfig.shared save];
    [self toast:@"SAVED"];
}

- (void)showDiag {
    NSString *d = [NSString stringWithFormat:@"%@\n%@",
                   [USModel diagnostics], [USCloak report]];
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"BIND STATE"
                                                              message:d
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [self.win.rootViewController presentViewController:a animated:YES completion:nil];
}

// Calibration moves the real camera, so it runs off the main thread (the
// routine sleeps between drags) and reports the measured value afterwards. It
// cannot run on the display link: ~450 ms of usleep there would freeze the UI.
- (void)doCalibrate {
    if (![USModel ready]) {
        [self toast:@"NOT BOUND"];
        return;
    }
    [self toast:@"MEASURING"];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        BOOL ok = [USModel calibrateAim];
        dispatch_async(dispatch_get_main_queue(), ^{
            [self toast:ok ? @"CALIBRATED" : @"FAILED - SEE LOGS"];
        });
    });
}

- (void)toast:(NSString *)msg {
    UILabel *t = [[UILabel alloc] initWithFrame:CGRectMake((self.panel.bounds.size.width - 120) / 2,
                                                          self.panel.bounds.size.height - 96, 120, 30)];
    t.text = msg;
    t.font = MONO(10);
    t.textAlignment = NSTextAlignmentCenter;
    t.textColor = [UIColor colorWithWhite:0.04 alpha:1];
    t.backgroundColor = ACC;
    t.layer.cornerRadius = 15;
    t.clipsToBounds = YES;
    [self.panel addSubview:t];
    [UIView animateWithDuration:1.1 animations:^{ t.alpha = 0; }
                     completion:^(BOOL f) { [t removeFromSuperview]; }];
}

- (void)refresh {
    dispatch_async(dispatch_get_main_queue(), ^{
        UIView *p = self.panel;
        if (!p) return;
        [p.subviews makeObjectsPerformSelector:@selector(removeFromSuperview)];
        [self.valueLabels removeAllObjects];
        [self buildPanelContents:p.bounds];
    });
}

@end