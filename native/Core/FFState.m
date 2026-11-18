// USConfig.m

#import "FFState.h"
#import "Obf.h"
#import "Secrets.h"
#import "Integrity.h"

#include <unistd.h>

// The feature keys are stored encoded and decoded in +load (below), so the
// binary carries no plaintext "esp.*" / "aim.*" strings to grep for. The blob
// is the keys joined by \n, each byte XOR 0x5B, in exactly this order:
//
//   aim.enabled, aim.smooth, aim.fov, aim.bone, aim.visible_only,
//   esp.enabled, esp.box, esp.line, esp.name, esp.hp, esp.distance,
//   esp.fov_circle, esp.max_dist, ui.hide_from_capture, debug.verbose,
//   cal.gain_x, cal.gain_y
//
// The last two are not switches: they hold the measured rotation gain the aim
// calibration produces, so the values survive a relaunch. They are encoded for
// the same reason as the rest - a plaintext "cal.gain" is a fingerprint.
//
// tools/tests/test_sources.py decodes this blob and checks it against the
// header, so a key can never drift out of sync silently.
#define FF_KEY_XOR 0x5B
static const uint8_t kKeyBlob[] = {
    0x3a, 0x32, 0x36, 0x75, 0x3e, 0x35, 0x3a, 0x39, 0x37, 0x3e, 0x3f, 0x51,
    0x3a, 0x32, 0x36, 0x75, 0x28, 0x36, 0x34, 0x34, 0x2f, 0x33, 0x51, 0x3a,
    0x32, 0x36, 0x75, 0x3d, 0x34, 0x2d, 0x51, 0x3a, 0x32, 0x36, 0x75, 0x39,
    0x34, 0x35, 0x3e, 0x51, 0x3a, 0x32, 0x36, 0x75, 0x2d, 0x32, 0x28, 0x32,
    0x39, 0x37, 0x3e, 0x04, 0x34, 0x35, 0x37, 0x22, 0x51, 0x3e, 0x28, 0x2b,
    0x75, 0x3e, 0x35, 0x3a, 0x39, 0x37, 0x3e, 0x3f, 0x51, 0x3e, 0x28, 0x2b,
    0x75, 0x39, 0x34, 0x23, 0x51, 0x3e, 0x28, 0x2b, 0x75, 0x37, 0x32, 0x35,
    0x3e, 0x51, 0x3e, 0x28, 0x2b, 0x75, 0x35, 0x3a, 0x36, 0x3e, 0x51, 0x3e,
    0x28, 0x2b, 0x75, 0x33, 0x2b, 0x51, 0x3e, 0x28, 0x2b, 0x75, 0x3f, 0x32,
    0x28, 0x2f, 0x3a, 0x35, 0x38, 0x3e, 0x51, 0x3e, 0x28, 0x2b, 0x75, 0x3d,
    0x34, 0x2d, 0x04, 0x38, 0x32, 0x29, 0x38, 0x37, 0x3e, 0x51, 0x3e, 0x28,
    0x2b, 0x75, 0x36, 0x3a, 0x23, 0x04, 0x3f, 0x32, 0x28, 0x2f, 0x51, 0x2e,
    0x32, 0x75, 0x33, 0x32, 0x3f, 0x3e, 0x04, 0x3d, 0x29, 0x34, 0x36, 0x04,
    0x38, 0x3a, 0x2b, 0x2f, 0x2e, 0x29, 0x3e, 0x51, 0x3f, 0x3e, 0x39, 0x2e,
    0x3c, 0x75, 0x2d, 0x3e, 0x29, 0x39, 0x34, 0x28, 0x3e, 0x51, 0x38, 0x3a,
    0x37, 0x75, 0x3c, 0x3a, 0x32, 0x35, 0x04, 0x23, 0x51, 0x38, 0x3a, 0x37,
    0x75, 0x3c, 0x3a, 0x32, 0x35, 0x04, 0x22,
};

NSString *kFFAimEnabled;
NSString *kFFAimSmooth;
NSString *kFFAimFov;
NSString *kFFAimBone;
NSString *kFFAimVisible;
NSString *kFFEspEnabled;
NSString *kFFEspBox;
NSString *kFFEspLine;
NSString *kFFEspName;
NSString *kFFEspHp;
NSString *kFFEspDist;
NSString *kFFEspFovCircle;
NSString *kFFEspMaxDist;
NSString *kFFShadow;
NSString *kFFVerbose;
NSString *kFFGainX;
NSString *kFFGainY;

// Persisted under the app's own preferences plist, so keep the key names
// uninformative and out of the binary's string table (see Core/Obf.h).
static NSString *kPrefsKey(void)  { return FFPrefsKey(); }
static NSString *kSchemaKey(void) { return FFMapKey(); }

// ---------------------------------------------------------------- validation
//
// A schema file is an OVERRIDE, so it has to earn its place. The failure mode
// this exists to kill: `cp native/ff_native.example.json state/ff_native.json`
// (which README suggests) ships a template whose every value is "CHANGE_ME".
// The old boot path only asked `container_class.length > 0`, that value is
// non-empty, so the dylib bound against a template map, every field lookup
// returned NULL, and ESP/aim did nothing - silently, forever, because the proxy
// also wrote that map into Documents. Bind now refuses templates.
static const char *kTemplateValues[] = {
    "CHANGE_ME", "??", "?", "TBD", "FILL_ME"
};

static BOOL FFIsTemplateValue(NSString *s) {
    if (!s.length) return YES;
    NSString *t = [s stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
    if (!t.length) return YES;
    for (size_t i = 0; i < sizeof(kTemplateValues) / sizeof(kTemplateValues[0]); i++) {
        if ([t caseInsensitiveCompare:@(kTemplateValues[i])] == NSOrderedSame) return YES;
    }
    // catches "CHANGE_ME_2", "x_CHANGE_ME" and the like
    return [t rangeOfString:@"CHANGE_ME" options:NSCaseInsensitiveSearch].location != NSNotFound;
}

BOOL FFSchemaUsable(NSDictionary *s) {
    if (![s isKindOfClass:NSDictionary.class] || s.count == 0) return NO;
    NSDictionary *p = s[@"player"];
    if (![p isKindOfClass:NSDictionary.class]) return NO;
    // The four without which USModel cannot read anything at all. These are
    // exactly the keys whose absence shows up as "bound, but zero players".
    NSArray<NSString *> *required = @[ @"container_class", @"container_static",
                                       @"list_field", @"position_field" ];
    for (NSString *k in required) {
        id v = p[k];
        if (![v isKindOfClass:NSString.class]) return NO;
        if (FFIsTemplateValue(v)) return NO;
    }
    return YES;
}

@implementation USConfig {
    NSMutableDictionary<NSString *, id> *_values;
    NSDictionary *_schema;
    NSString *_schemaSource;
    NSString *_proxyBase;
    NSTimer *_pollTimer;
    NSTimeInterval _lastFetch;
}

// Decode the feature keys. Runs before anything can ask for one: +load happens
// during image initialisation, long before the game reaches the point where the
// dylib is constructed.
+ (void)load {
    static char buf[sizeof(kKeyBlob) + 1];
    for (size_t i = 0; i < sizeof(kKeyBlob); i++)
        buf[i] = (char)(kKeyBlob[i] ^ FF_KEY_XOR);
    buf[sizeof(kKeyBlob)] = 0;
    NSArray<NSString *> *p = [@(buf) componentsSeparatedByString:@"\n"];
    NSUInteger i = 0;
    #define K(var) var = (i < p.count) ? p[i++] : @""
    K(kFFAimEnabled);
    K(kFFAimSmooth);
    K(kFFAimFov);
    K(kFFAimBone);
    K(kFFAimVisible);
    K(kFFEspEnabled);
    K(kFFEspBox);
    K(kFFEspLine);
    K(kFFEspName);
    K(kFFEspHp);
    K(kFFEspDist);
    K(kFFEspFovCircle);
    K(kFFEspMaxDist);
    K(kFFShadow);
    K(kFFVerbose);
    K(kFFGainX);
    K(kFFGainY);
    #undef K
}

+ (instancetype)shared {
    static USConfig *s;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ s = [USConfig new]; });
    return s;
}

- (instancetype)init {
    if ((self = [super init])) {
        _values = [NSMutableDictionary dictionary];
        [self defaults];
        [self loadPersisted];
        [self reloadSchema];
    }
    return self;
}

- (void)defaults {
    _values[kFFAimEnabled]   = @NO;
    _values[kFFAimSmooth]    = @(4);
    _values[kFFAimFov]       = @(120);
    _values[kFFAimBone]      = @"head";
    _values[kFFAimVisible]   = @NO;
    _values[kFFEspEnabled]   = @NO;
    _values[kFFEspBox]       = @YES;
    _values[kFFEspLine]      = @YES;
    _values[kFFEspName]      = @YES;
    _values[kFFEspHp]        = @YES;
    _values[kFFEspDist]      = @YES;
    _values[kFFEspFovCircle] = @NO;
    _values[kFFEspMaxDist]   = @(400);
    _values[kFFShadow]       = @NO;
    _values[kFFVerbose]      = @NO;
}

// ------------------------------------------------------------------ values
- (BOOL)isOn:(NSString *)key {
    @synchronized (self) { return [_values[key] boolValue]; }
}

- (NSInteger)intValue:(NSString *)key {
    @synchronized (self) { return [_values[key] integerValue]; }
}

- (double)doubleValue:(NSString *)key {
    @synchronized (self) { return [_values[key] doubleValue]; }
}

- (NSString *)stringValue:(NSString *)key {
    @synchronized (self) { return [_values[key] isKindOfClass:NSString.class] ? _values[key] : @""; }
}

- (void)setOn:(BOOL)on forKey:(NSString *)key {
    @synchronized (self) { _values[key] = @(on); }
    [[NSNotificationCenter defaultCenter] postNotificationName:FF_FEATURE_CHANGED
                                                        object:key];
    [self save];
}

- (void)setRaw:(id)v forKey:(NSString *)key {
    if (!key) return;
    @synchronized (self) { _values[key] = v; }
    [[NSNotificationCenter defaultCenter] postNotificationName:FF_FEATURE_CHANGED
                                                        object:key];
}

// ------------------------------------------------------------------ schema
- (NSDictionary *)schema {
    @synchronized (self) { return _schema; }
}

- (BOOL)schemaReady {
    // "There is a dict with a player key in it" is not ready - a template has
    // both. Same test the boot path binds with, so the two cannot disagree.
    return FFSchemaUsable([self schema]);
}

// Defined before its first use: reloadSchema (below) calls it, and ObjC
// compiles top-down, so a later definition is an implicit-declaration error.
static NSString *DocumentsDir(void) {
    return NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
}

- (void)reloadSchema {
    // 1. bundled copy next to the dylib (always available, offline safe)
    NSDictionary *s = [self loadJSON:FFSecretText(FFSecretSchemaBase) dir:nil];
    NSString *src = s ? @"bundle" : @"none";
    if (s && !FFSchemaUsable(s)) {
        FFLOG(@"bundled schema is a template - ignoring it");
        s = nil;
        src = @"none";
    }

    // 2. proxy copy overrides it - lets you fix offsets after a game patch
    //    without rebuilding or re-signing the IPA. Read WITHOUT the bundle
    //    fallback, so `src` tells the truth about where the map came from.
    NSString *remotePath = [DocumentsDir() stringByAppendingPathComponent:FFSecretText(FFSecretSchemaFile)];
    NSDictionary *remote = [self loadJSONFile:remotePath];
    if (remote) {
        if (FFSchemaUsable(remote)) {
            s = remote;
            src = @"file";
        } else {
            // A template left here by a previous run survives reinstall (the
            // Documents dir is only wiped when the app is deleted, and iTunes
            // backups restore it). Delete it now or it poisons every launch.
            //
            // unlink, not -removeItemAtPath:. Our own hooks answer ENOENT for
            // this filename, and NSFileManager stats before it removes, so the
            // high-level call would fail and leave the file in place.
            unlink(remotePath.fileSystemRepresentation);
            FFLOG(@"schema file rejected (template values) - deleted %@", remotePath);
        }
    }
    @synchronized (self) {
        _schema = s ?: @{};
        _schemaSource = src;
    }
    FFLOG(@"schema %@ from %@ (%lu top keys)",
          s ? @"loaded" : @"MISSING", src, (unsigned long)_schema.count);
}

// Documents-only read. `loadJSON:dir:` falls back to the bundle when the file
// is absent, which made "did the operator supply a map?" unanswerable.
//
// Both readers go through USIntegrity's real-syscall path: our own open/stat
// hooks hide this filename process-wide, ourselves included, so the plain
// NSData/NSFileManager route would answer "no such file" to us as well.
- (NSDictionary *)loadJSONFile:(NSString *)path {
    if (!path.length) return nil;
    NSData *d = [USIntegrity dataAtPath:path];
    if (!d) return nil;
    id o = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
    return [o isKindOfClass:NSDictionary.class] ? o : nil;
}

- (NSString *)schemaSource {
    @synchronized (self) { return _schemaSource ?: @"none"; }
}

// Bind failed against this map: it is not an answer for this build. Drop it and
// fall back to the live resolver, which is the path that always works.
- (void)rejectSchema {
    NSString *p = [DocumentsDir() stringByAppendingPathComponent:FFSecretText(FFSecretSchemaFile)];
    unlink(p.fileSystemRepresentation);   // see reloadSchema: removeItemAtPath would stat first
    @synchronized (self) {
        _schema = @{};
        _schemaSource = @"none";
    }
    FFLOG(@"schema rejected - deleted %@", p);
}

- (NSDictionary *)loadJSON:(NSString *)name dir:(NSString *)dir {
    NSString *path = nil;
    if (dir) path = [dir stringByAppendingPathComponent:[name stringByAppendingString:@".json"]];
    NSData *d = path ? [USIntegrity dataAtPath:path] : nil;
    if (!d) {
        path = [[NSBundle mainBundle] pathForResource:name ofType:@"json"];
        d = path ? [USIntegrity dataAtPath:path] : nil;
    }
    if (!d) return nil;
    id o = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
    return [o isKindOfClass:NSDictionary.class] ? o : nil;
}

// ------------------------------------------------------------------ sync
- (NSString *)proxyBase {
    @synchronized (self) { return _proxyBase; }
}

- (void)startSyncWith:(NSString *)baseURL {
    if (!baseURL.length) return;
    @synchronized (self) { _proxyBase = [baseURL copy]; }
    dispatch_async(dispatch_get_main_queue(), ^{
        [self->_pollTimer invalidate];
        self->_pollTimer = [NSTimer scheduledTimerWithTimeInterval:2.0
                                                           target:self
                                                         selector:@selector(poll)
                                                         userInfo:nil
                                                          repeats:YES];
        [self poll];
    });
}

- (void)poll {
    NSString *base = [self proxyBase];
    if (!base.length) return;

    // 1. feature state from the panel (so the WebView menu drives these too)
    NSString *stateURL = [base stringByAppendingString:@"/api/state"];
    [self GET:stateURL completion:^(NSDictionary *json) {
        NSDictionary *values = json[@"values"];
        if (![values isKindOfClass:NSDictionary.class]) return;
        for (NSString *k in values) {
            // only adopt keys this dylib knows, never invent behaviour
            if (self->_values[k] != nil) [self setRaw:values[k] forKey:k];
        }
    }];

    // 2. native schema, refreshed at most every 30s
    if ([[NSDate date] timeIntervalSince1970] - _lastFetch > 30) {
        _lastFetch = [[NSDate date] timeIntervalSince1970];
        NSString *schemaURL = [base stringByAppendingString:@"/api/native"];
        [self GET:schemaURL completion:^(NSDictionary *json) {
            if (![json isKindOfClass:NSDictionary.class] || json.count == 0) return;
            // The proxy answers this route with a diagnostic wrapper
            // ({"_missing": ..., "example": {...}}) when it has no map, and with
            // a template if the operator copied the example. Neither is an
            // answer: adopting it pointed every field lookup at "CHANGE_ME",
            // and writing it to Documents made the damage outlive the proxy.
            if (!FFSchemaUsable(json)) {
                FFLOG(@"proxy schema ignored (no usable map served)");
                return;
            }
            @synchronized (self) {
                self->_schema = json;
                self->_schemaSource = @"proxy";
            }
            // Not written to disk. The map is an override that the proxy can
            // re-serve at any time, and a 1.4 KB JSON full of FF class names in
            // Documents is an artefact a client scan can read. Keeping it in
            // memory removes the artefact and changes nothing else.
            FFLOG(@"schema refreshed from proxy");
        }];
    }
}

- (void)GET:(NSString *)url completion:(void (^)(NSDictionary *))done {
    NSURL *u = [NSURL URLWithString:url];
    if (!u) return;
    NSMutableURLRequest *r = [NSMutableURLRequest requestWithURL:u];
    r.timeoutInterval = 4.0;
    // One session for the life of the process. `ephemeralSessionConfiguration`
    // used to be applied to a brand-new session on every 2 s poll, which leaks
    // a session + its queue + its cache each time and eventually trips the
    // app's file-descriptor limit.
    static NSURLSession *sSession;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSURLSessionConfiguration *c = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        c.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        c.HTTPMaximumConnectionsPerHost = 2;
        c.timeoutIntervalForRequest = 4.0;
        sSession = [NSURLSession sessionWithConfiguration:c];
    });
    [[sSession dataTaskWithRequest:r
        completionHandler:^(NSData *d, NSURLResponse *resp, NSError *e) {
            if (!d || e) return;
            id o = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
            if ([o isKindOfClass:NSDictionary.class]) {
                dispatch_async(dispatch_get_main_queue(), ^{ done(o); });
            }
        }] resume];
}

// ------------------------------------------------------------------ persist
- (void)save {
    NSDictionary *snap;
    @synchronized (self) { snap = [_values copy]; }
    [[NSUserDefaults standardUserDefaults] setObject:snap forKey:kPrefsKey()];
    [[NSUserDefaults standardUserDefaults] synchronize];
}

// Not named `load`, for the same reason the resolver is not: a method called
// `load` reads like the runtime hook it is not, and the next person to touch
// this file should not have to remember which kind is invoked by dyld.
- (void)loadPersisted {
    NSDictionary *d = [[NSUserDefaults standardUserDefaults] dictionaryForKey:kPrefsKey()];
    if (!d) return;
    @synchronized (self) {
        for (NSString *k in d) if (_values[k] != nil) _values[k] = d[k];
    }
}

@end