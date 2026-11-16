// ESP.m
//
// One CADisplayLink drives two things: the overlay redraw and the aimbot. They
// are independent switches - turning ESP off must NOT turn aim off, which is
// what the previous version did by returning before `[USModel tick]`.

#import "ESP.h"
#import "FFState.h"
#import "FFMenu.h"
#import "Compat.h"
#import "Obf.h"

@interface USOverlay ()
@property (nonatomic) CADisplayLink *link;
@property (nonatomic) NSArray<USEntity *> *snapshot;
@property (nonatomic) NSTimeInterval lastAim;
@property (nonatomic) UITextField *maskField;
@end

@implementation USOverlay {
    UIWindow *_win;
}

+ (instancetype)shared {
    static USOverlay *v;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        // UIView init is main-thread only. The menu and the boot path both call
        // this from main, but a proxy-driven toggle does not, so the first
        // construction is forced onto the main queue.
        void (^make)(void) = ^{
            v = [[USOverlay alloc] initWithFrame:[UIScreen mainScreen].bounds];
            v.backgroundColor = UIColor.clearColor;
            v.userInteractionEnabled = NO;          // never steal touches from the game
            v.opaque = NO;
            v.multipleTouchEnabled = NO;
            v.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        };
        if ([NSThread isMainThread]) make();
        else dispatch_sync(dispatch_get_main_queue(), make);
    });
    return v;
}

- (void)start {
    if (self.link) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (self.link) return;
        UIWindow *w = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
        w.windowLevel = UIWindowLevelNormal + 128;   // above the game, below alerts
        w.backgroundColor = UIColor.clearColor;
        w.rootViewController = [UIViewController new];
        w.rootViewController.view.backgroundColor = UIColor.clearColor;
        w.userInteractionEnabled = NO;
        [w.rootViewController.view addSubview:self];
        w.hidden = NO;
        self->_win = w;
        self.frame = w.bounds;
        [self setHiddenFromCapture:[USConfig.shared isOn:kFFShadow]];

        self.link = [CADisplayLink displayLinkWithTarget:self selector:@selector(tick:)];
        self.link.preferredFramesPerSecond = 60;
        [self.link addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
        FFLOG(@"esp overlay started");
    });
}

- (void)stop {
    dispatch_async(dispatch_get_main_queue(), ^{
        [self.link invalidate];
        self.link = nil;
        self->_win.hidden = YES;
        self->_win = nil;
        self.snapshot = @[];
        [self setNeedsDisplay];
        FFLOG(@"esp overlay stopped");
    });
}

- (void)setHiddenFromCapture:(BOOL)hidden {
    // A secure UITextField's layer is excluded from screen recordings and most
    // screenshot paths. The mask layer used to have no frame, i.e. a 0x0 mask,
    // which hides the entire overlay instead of just excluding it from capture.
    dispatch_async(dispatch_get_main_queue(), ^{
        if (hidden) {
            if (!self.maskField) {
                UITextField *f = [UITextField new];
                f.secureTextEntry = YES;
                f.userInteractionEnabled = NO;
                f.frame = self.bounds;
                f.autoresizingMask = UIViewAutoresizingFlexibleWidth |
                                     UIViewAutoresizingFlexibleHeight;
                self.maskField = f;
            }
            self.maskField.frame = self.bounds;
            self.layer.mask = self.maskField.layer;
        } else {
            self.layer.mask = nil;
            self.maskField = nil;
        }
    });
}

- (void)tick:(CADisplayLink *)link {
    BOOL wantEsp = [USConfig.shared isOn:kFFEspEnabled];
    BOOL wantAim = [USConfig.shared isOn:kFFAimEnabled];

    if (!wantEsp && !wantAim) {
        if (self.snapshot.count) { self.snapshot = @[]; [self setNeedsDisplay]; }
        // Keep the world-to-screen probe alive only while something needs it.
        return;
    }
    if (![USModel ready]) return;

    NSArray<USEntity *> *list = [USModel players];

    if (wantEsp) {
        double maxDist = [USConfig.shared doubleValue:kFFEspMaxDist];
        NSMutableArray *vis = [NSMutableArray array];
        for (USEntity *p in list) {
            if (maxDist > 0 && p.distance > maxDist) continue;
            if (!p.onScreen) continue;
            [vis addObject:p];
        }
        self.snapshot = vis;
        [self setNeedsDisplay];
    } else if (self.snapshot.count) {
        self.snapshot = @[];
        [self setNeedsDisplay];
    }

    [USModel tick];
    if (wantAim) [self runAim:list link:link];
}

// ------------------------------------------------------------------ aimbot
- (void)runAim:(NSArray<USEntity *> *)list link:(CADisplayLink *)link {
    if (![USConfig.shared isOn:kFFAimEnabled]) return;
    // 20 Hz max: faster than this looks like a snap to any observer
    NSTimeInterval now = link.timestamp;
    if (now - self.lastAim < 0.05) return;
    self.lastAim = now;

    CGFloat w = self.bounds.size.width, h = self.bounds.size.height;
    CGPoint cross = CGPointMake(w * 0.5, h * 0.5);
    double fov = [USConfig.shared doubleValue:kFFAimFov];
    double best = fov * fov;
    USEntity *target = nil;

    for (USEntity *p in list) {
        if (!p.onScreen) continue;
        if ([USConfig.shared isOn:kFFAimVisible] && p.depth <= 0) continue;
        CGFloat dx = p.screen.x - cross.x, dy = p.screen.y - cross.y;
        double d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; target = p; }
    }
    if (!target) return;
    [USModel aimAt:target
             bone:[USConfig.shared stringValue:kFFAimBone]
           smooth:(int)[USConfig.shared intValue:kFFAimSmooth]];
}

// ------------------------------------------------------------------ drawing
- (void)drawRect:(CGRect)rect {
    CGContextRef ctx = UIGraphicsGetCurrentContext();
    if (!ctx) return;
    CGContextClearRect(ctx, rect);

    // Nothing to draw when both switches are off; the layer would otherwise
    // keep an opaque backing store alive.
    if (![USConfig.shared isOn:kFFEspEnabled] && ![USConfig.shared isOn:kFFAimEnabled]) return;

    CGColorRef accent = [UIColor colorWithRed:0.71 green:1.0 blue:0.24 alpha:0.95].CGColor;
    CGContextSetStrokeColorWithColor(ctx, accent);
    CGContextSetLineWidth(ctx, 1.4);

    // FOV circle for the aimbot
    if ([USConfig.shared isOn:kFFEspFovCircle] || [USConfig.shared isOn:kFFAimEnabled]) {
        double r = [USConfig.shared doubleValue:kFFAimFov];
        CGFloat cx = rect.size.width * 0.5, cy = rect.size.height * 0.5;
        CGContextSetAlpha(ctx, 0.35);
        CGContextStrokeEllipseInRect(ctx, CGRectMake(cx - r, cy - r, r * 2, r * 2));
        CGContextSetAlpha(ctx, 1.0);
    }

    CGFloat w = rect.size.width, h = rect.size.height;
    CGPoint cross = CGPointMake(w * 0.5, h * 0.5);
    BOOL drawBox  = [USConfig.shared isOn:kFFEspBox];
    BOOL drawLine = [USConfig.shared isOn:kFFEspLine];
    BOOL drawName = [USConfig.shared isOn:kFFEspName];
    BOOL drawHp   = [USConfig.shared isOn:kFFEspHp];
    BOOL drawDist = [USConfig.shared isOn:kFFEspDist];

    for (USEntity *p in self.snapshot) {
        float distance = p.distance;
        // scale a 0.72 m wide body by the projected depth
        CGFloat depth = p.depth > 0.1f ? p.depth : 1.0f;
        CGFloat boxH = (CGFloat)(0.72 * 1.8 / depth) * h * 0.5;
        if (boxH < 8) boxH = 8;
        if (boxH > h) boxH = h;
        CGFloat boxW = boxH * 0.42;
        CGRect b = CGRectMake(p.screen.x - boxW * 0.5,
                              p.screen.y - boxH,
                              boxW, boxH);

        if (drawLine) {
            CGContextMoveToPoint(ctx, cross.x, cross.y);
            CGContextAddLineToPoint(ctx, p.screen.x, b.origin.y + boxH);
            CGContextStrokePath(ctx);
        }
        if (drawBox) {
            CGContextStrokeRect(ctx, b);
            // corner ticks
            CGFloat t = boxW * 0.28;
            CGContextSetLineWidth(ctx, 2.2);
            CGContextStrokeRect(ctx, CGRectMake(b.origin.x, b.origin.y, t, t * 0.35));
            CGContextStrokeRect(ctx, CGRectMake(CGRectGetMaxX(b) - t, b.origin.y, t, t * 0.35));
            CGContextStrokeRect(ctx, CGRectMake(b.origin.x, CGRectGetMaxY(b) - t * 0.35, t, t * 0.35));
            CGContextStrokeRect(ctx, CGRectMake(CGRectGetMaxX(b) - t, CGRectGetMaxY(b) - t * 0.35, t, t * 0.35));
            CGContextSetLineWidth(ctx, 1.4);
        }
        if (drawHp && p.maxHp > 0) {
            CGFloat frac = MAX(0.0, MIN(1.0, p.hp / p.maxHp));
            CGFloat barH = MAX(boxH * 0.6, 20);
            CGRect bg = CGRectMake(b.origin.x - 6, b.origin.y, 3, barH);
            CGRect fg = CGRectMake(b.origin.x - 6, b.origin.y + barH * (1 - frac), 3, barH * frac);
            CGContextSetFillColorWithColor(ctx, [UIColor colorWithWhite:0 alpha:0.45].CGColor);
            CGContextFillRect(ctx, bg);
            CGContextSetFillColorWithColor(ctx, accent);
            CGContextFillRect(ctx, fg);
        }
        if (drawName || drawDist) {
            NSMutableString *label = [NSMutableString string];
            if (drawName && p.name.length) [label appendString:p.name];
            if (drawDist) {
                if (label.length) [label appendString:@"  "];
                [label appendFormat:@"%.0fm", distance];
            }
            if (label.length) {
                NSDictionary *attrs = @{
                    NSFontAttributeName: FFMono(10, YES),
                    NSForegroundColorAttributeName: [UIColor colorWithRed:0.71 green:1 blue:0.24 alpha:1]
                };
                [label drawAtPoint:CGPointMake(b.origin.x, b.origin.y - 13)
                    withAttributes:attrs];
            }
        }
    }
}

@end
