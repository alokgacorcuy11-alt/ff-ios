// Game.m
//
// No class name, field name or offset is hardcoded here. They come from
// USMap (a live runtime walk) or from an optional schema the proxy serves.
// Even the array header comes from the runtime.

#import "Game.h"
#import "Resolve.h"
#import "Inject.h"
#import "FFState.h"
#import "Obf.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

@implementation USEntity
- (NSString *)description {
    return [NSString stringWithFormat:@"<%@%@ hp=%.0f %.0fm t=%d>",
            self.name ?: @"?", self.isBot ? @"(bot)" : @"",
            self.hp, self.distance, self.team];
}
@end

// ---------------------------------------------------------------- schema state
static NSDictionary *gMap;
static NSMutableArray<NSString *> *gErrors;
static NSMutableArray<NSString *> *gResolveLog;
static BOOL gReady = NO;
static BOOL gW2SReady = NO;
static NSString *gW2SMode = @"none";
static int gW2SFails = 0;

static Il2CppClass *gPlayerCls;
static FieldInfo *fName, *fHp, *fMaxHp, *fTeam, *fLocal, *fCamTr, *fBot, *fPos;
static size_t offName, offHp, offMaxHp, offTeam, offLocal, offCamTr, offBot, offPos;
static BOOL hpIsFloat = YES, maxIsFloat = YES;
static NSInteger strLenOff = 16, strDataOff = 20, arrLenOff = 24, arrDataOff = 32;
static NSMutableArray<NSString *> *gPosCandidates;

static Il2CppClass *gContainerCls, *gContainerStaticOwner, *gListCls;
static FieldInfo *fListField, *fContainerStatic, *fListItems, *fListSize;
static BOOL listIsArray = NO;
static size_t offListField, offItems, offSize;

static void *icW2SInj, *icW2SRet, *icGetMain, *icFov;
static void *icTrPosInj, *icTrRotInj, *icTrEulerInj, *icTrMatInj;
static void *icGetTransform;          // UnityEngine.Component::get_transform
static Il2CppObject *gMainCamera;
static NSTimeInterval gCameraStamp;
static Il2CppObject *gLocalObj;
static NSMutableArray<NSString *> *gIcallNotes;   // which candidate resolved
static NSMutableArray<NSString *> *gIcallTried;    // every name we probed
static NSTimeInterval gProbeStamp;

// calibration
static NSUInteger gCalibFrames;
static BOOL gCalibrated;
static double gCalibErr[32];

// aim
static NSTimeInterval gAimLast;
static CGPoint gAimCursor;

// Camera rotation per point of finger travel, WITH its sign, measured on this
// device in this game build by +calibrateAim. Zero means "never measured".
//
// The aimbot refuses to run without it. Moving a finger does not move the
// camera by a fixed amount - it depends on the player's in-game sensitivity,
// the screen size and whether look-Y is inverted - so a guessed gain does not
// aim, it spins. Stored under kFFGainX / kFFGainY so a measured value survives
// a relaunch.
static double gGainX, gGainY;
static BOOL gAimCalibrated;
static BOOL gAimWarned;
// A thumb that rests on the screen forever is not what a person does. Each
// engagement gets a randomised hold budget; on expiry the finger lifts and the
// next frame re-engages if the target is still there.
static NSTimeInterval gAimHoldUntil;

// Adaptive refinement. The calibration drag is 48 points, which can trigger the
// game's look-acceleration; the fine corrections the aimbot applies are a few
// points, where the small-signal gain is smaller. So the initial measurement is
// treated as a starting point, and every frame the gain is nudged toward what
// the camera actually did in response to the previous correction.
//
// Read the four checks in tools/tests/test_math.py before removing this: a
// measured gain that is 20x too small makes the loop diverge once the per-frame
// cap stops binding, and this is what recovers from that.
static double gLastDx, gLastDy;
static double gYawAtApply, gPitchAtApply;
static BOOL gHaveObservation;

// ---------------------------------------------------------------- errors
static void err(NSString *fmt, ...) {
    va_list a; va_start(a, fmt);
    NSString *s = [[NSString alloc] initWithFormat:fmt arguments:a];
    va_end(a);
    if (!gErrors) gErrors = [NSMutableArray array];
    [gErrors addObject:s];
    FFLOG(@"bind: %@", s);
}

// ---------------------------------------------------------------- primitives
static int32_t arrayLength(Il2CppObject *arr) {
    if (!arr) return 0;
    Il2CppApi *api = [IL2CPP api];
    if (api->array_length) return api->array_length((Il2CppArray *)arr);
    return FFReadI32(arr, (size_t)arrLenOff);
}

static Il2CppObject *arrayAt(Il2CppObject *arr, int32_t i) {
    if (!arr || i < 0 || i >= arrayLength(arr)) return NULL;
    return *(Il2CppObject **)((uint8_t *)arr + (size_t)arrDataOff + (size_t)i * 8);
}

static NSString *stringOf(Il2CppObject *s) {
    if (!s) return nil;
    Il2CppApi *api = [IL2CPP api];
    int32_t len = api->string_length ? api->string_length((Il2CppString *)s)
                                     : FFReadI32(s, (size_t)strLenOff);
    if (len <= 0 || len > 2048) return nil;
    const uint16_t *chars = api->string_chars ? api->string_chars((Il2CppString *)s) : NULL;
    if (!chars) chars = (const uint16_t *)((const uint8_t *)s + strDataOff);
    return [[NSString alloc] initWithCharacters:chars length:(NSUInteger)len];
}

static float numField(Il2CppObject *obj, FieldInfo *f, BOOL isFloat) {
    if (!obj || !f) return 0.0f;
    size_t o = FFFieldOffset(f);
    return isFloat ? FFReadF32(obj, o) : (float)FFReadI32(obj, o);
}

static Il2CppObject *readStaticObject(Il2CppClass *owner, FieldInfo *f) {
    if (!owner || !f) return NULL;
    Il2CppApi *api = [IL2CPP api];
    // Both are optional in the symbol table; calling a NULL icall is a jump to
    // address 0, which is the classic "works until the class is statically
    // initialised differently" crash.
    if (!api->field_static_get_value) return NULL;
    if (api->runtime_class_init) api->runtime_class_init(owner);
    Il2CppObject *v = NULL;
    api->field_static_get_value(f, &v);
    return v;
}

// ---------------------------------------------------------------- rotation
static Vec3 quatRotate(Quat q, Vec3 v) {
    Vec3 u = {q.x, q.y, q.z};
    Vec3 t = { 2.0f * (u.y * v.z - u.z * v.y),
               2.0f * (u.z * v.x - u.x * v.z),
               2.0f * (u.x * v.y - u.y * v.x) };
    Vec3 c = { u.y * t.z - u.z * t.y,
               u.z * t.x - u.x * t.z,
               u.x * t.y - u.y * t.x };
    Vec3 r = { v.x + q.w * t.x + c.x,
               v.y + q.w * t.y + c.y,
               v.z + q.w * t.z + c.z };
    return r;
}

// Inverse of quatRotate: rotate a world vector into the frame the quaternion
// describes. The input is normalised first, because readTransformRot only
// checks the length is within 0.5..1.5 - and the conjugate is the inverse only
// for a unit quaternion.
static Vec3 quatInvRotate(Quat q, Vec3 v) {
    float n = sqrtf(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
    if (!isfinite(n) || n < 1e-6f) return v;
    Quat inv = { -q.x / n, -q.y / n, -q.z / n, q.w / n };
    return quatRotate(inv, v);
}

static double wrapPi(double a) {
    while (a > M_PI) a -= 2.0 * M_PI;
    while (a < -M_PI) a += 2.0 * M_PI;
    return a;
}

// Camera forward in Unity is +Z in local space.
static Vec3 forwardOf(Quat q) {
    Vec3 fwd = { 0, 0, 1 };
    return quatRotate(q, fwd);
}

static double yawOfQuat(Quat q) {
    Vec3 f = forwardOf(q);
    return atan2((double)f.x, (double)f.z);
}

static double pitchOfQuat(Quat q) {
    Vec3 f = forwardOf(q);
    double y = (double)f.y;
    if (y > 1.0) y = 1.0;
    if (y < -1.0) y = -1.0;
    return asin(y);
}

// A gain outside this window cannot be a real sensitivity: one screen width of
// travel rotates a camera by roughly a quarter turn, i.e. about 0.005 rad per
// point. Anything near zero or near one means the rotation read was noise.
static BOOL gainPlausible(double g) {
    double a = fabs(g);
    return a >= 1e-4 && a <= 0.5;
}

static void loadGains(void) {
    double x = [USConfig.shared doubleValue:kFFGainX];
    double y = [USConfig.shared doubleValue:kFFGainY];
    if (gainPlausible(x) && gainPlausible(y)) {
        gGainX = x;
        gGainY = y;
        gAimCalibrated = YES;
    }
}

static BOOL readTransformPos(Il2CppObject *tr, Vec3 *out) {
    if (!tr || !out || !icTrPosInj) return NO;
    Vec3 v = {0, 0, 0};
    ((void (*)(void *, Vec3 *))icTrPosInj)(tr, &v);
    if (!isfinite(v.x) || !isfinite(v.y) || !isfinite(v.z)) return NO;
    if (fabsf(v.x) + fabsf(v.y) + fabsf(v.z) < 0.001f) return NO;
    *out = v;
    return YES;
}

// Unity's Quaternion.Euler is ZXY: Z first, then X, then Y. If we ever have to
// read euler angles instead of a quaternion, the order matters or the camera
// ends up rolled.
static Quat quatFromEuler(float xd, float yd, float zd) {
    const float h = (float)M_PI / 360.0f;
    float x = xd * h, y = yd * h, z = zd * h;
    float cx = cosf(x), sx = sinf(x);
    float cy = cosf(y), sy = sinf(y);
    float cz = cosf(z), sz = sinf(z);
    Quat q;
    q.x = sx * cy * cz + cx * sy * sz;
    q.y = cx * sy * cz - sx * cy * sz;
    q.z = cx * cy * sz - sx * sy * cz;
    q.w = cx * cy * cz + sx * sy * sz;
    return q;
}

// Rotation part of a column-major Unity Matrix4x4: element (row r, col c) is
// m[c * 4 + r]. The cross terms are (m21 - m12), (m02 - m20), (m10 - m01) -
// in that order. Writing them the other way round yields the INVERSE rotation,
// which does not crash and does not look obviously wrong; it just puts the ESP
// in the mirrored position. tools/tests/test_math.py pins this down.
static Quat quatFromMatrix(const Mat4 *m) {
    const float *e = m->m;
    float m00 = e[0],  m10 = e[1],  m20 = e[2];
    float m01 = e[4],  m11 = e[5],  m21 = e[6];
    float m02 = e[8],  m12 = e[9],  m22 = e[10];
    float tr = m00 + m11 + m22;
    Quat q = {0, 0, 0, 1};
    if (tr > 0.0f) {
        float s = sqrtf(tr + 1.0f) * 2.0f;
        q.w = 0.25f * s;
        q.x = (m21 - m12) / s;
        q.y = (m02 - m20) / s;
        q.z = (m10 - m01) / s;
    } else if (m00 > m11 && m00 > m22) {
        float s = sqrtf(1.0f + m00 - m11 - m22) * 2.0f;
        q.w = (m21 - m12) / s;
        q.x = 0.25f * s;
        q.y = (m01 + m10) / s;
        q.z = (m02 + m20) / s;
    } else if (m11 > m22) {
        float s = sqrtf(1.0f + m11 - m00 - m22) * 2.0f;
        q.w = (m02 - m20) / s;
        q.x = (m01 + m10) / s;
        q.y = 0.25f * s;
        q.z = (m12 + m21) / s;
    } else {
        float s = sqrtf(1.0f + m22 - m00 - m11) * 2.0f;
        q.w = (m10 - m01) / s;
        q.x = (m02 + m20) / s;
        q.y = (m12 + m21) / s;
        q.z = 0.25f * s;
    }
    return q;
}

// Three ways to get the camera's rotation, tried in order of directness. The
// icall that exists varies by Unity version, so nothing here is assumed - all
// three are probed at bind time and the report says which one answered.
static BOOL readTransformRot(Il2CppObject *tr, Quat *out) {
    if (!tr || !out) return NO;
    if (icTrRotInj) {
        Quat q = {0, 0, 0, 1};
        ((void (*)(void *, Quat *))icTrRotInj)(tr, &q);
        float len = sqrtf(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
        if (isfinite(len) && len > 0.5f && len < 1.5f) { *out = q; return YES; }
    }
    if (icTrEulerInj) {
        Vec3 e = {0, 0, 0};
        ((void (*)(void *, Vec3 *))icTrEulerInj)(tr, &e);
        if (isfinite(e.x) && isfinite(e.y) && isfinite(e.z)) {
            *out = quatFromEuler(e.x, e.y, e.z);
            return YES;
        }
    }
    if (icTrMatInj) {
        Mat4 m;
        memset(&m, 0, sizeof(m));
        ((void (*)(void *, Mat4 *))icTrMatInj)(tr, &m);
        Quat q = quatFromMatrix(&m);
        float len = sqrtf(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
        if (isfinite(len) && len > 0.5f && len < 1.5f) { *out = q; return YES; }
    }
    return NO;
}

// ---------------------------------------------------------------- camera
static Il2CppObject *mainCamera(void) {
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
    if (gMainCamera && now - gCameraStamp < 0.5) return gMainCamera;
    gCameraStamp = now;
    gMainCamera = NULL;
    if (icGetMain) {
        void *c = ((void *(*)(void))icGetMain)();
        if (c) gMainCamera = (Il2CppObject *)c;
    }
    return gMainCamera;
}

static float cameraFov(void) {
    Il2CppObject *c = mainCamera();
    if (!c || !icFov) return 60.0f;
    float f = ((float (*)(void *))icFov)(c);
    if (!isfinite(f) || f < 1.0f || f > 179.0f) return 60.0f;
    return f;
}

// Defined below; localCameraTransform needs it so the w2s matrix fallback can
// recover on its own if the schema's first guess was the wrong field.
static BOOL resolveCameraTransform(void);
static BOOL transformUsable(Il2CppObject *tr);
static Il2CppObject *cameraFromEngine(void);

// The camera pose, from the engine itself when possible.
//
// Camera.main is a Camera, and a Transform is a Component - so the engine's own
// Component::get_transform answers with the exact transform the renderer uses.
// That is the primary path now, because the player-field candidates can only be
// checked for "does this answer position and rotation like a Transform", and
// every bone on the player passes that test. A bone's pose projects the world
// through the wrong frame: boxes land nowhere and the aim points somewhere else,
// with no crash to point at. Player fields stay as the fallback.
static Il2CppObject *localCameraTransform(void) {
    Il2CppObject *engine = cameraFromEngine();
    if (engine) return engine;
    if (!gLocalObj) return NULL;
    if (!fCamTr) resolveCameraTransform();
    if (!fCamTr) return NULL;
    return FFReadObj(gLocalObj, offCamTr);
}

// ---------------------------------------------------------- camera transform
// Which field on the player is the main camera transform? The name is a
// candidate, and field types cannot be resolved offline on this build
// (REPORT-1.132.1 section 5), so the choice is CONFIRMED BY USE: the object has
// to answer the Transform position icall with a plausible vector, and - when a
// rotation icall exists - a unit-length rotation too.
//
// A wrong field here is silent: the matrix world-to-screen fallback returns
// nothing and the aim has no camera to read, with no crash to point at either.
static BOOL transformUsable(Il2CppObject *tr) {
    if (!tr) return NO;
    Vec3 pos;
    if (!readTransformPos(tr, &pos)) return NO;
    // If a rotation icall resolved, the aim depends on it. Requiring it here
    // means a field that only answers position is rejected now rather than
    // producing an aimbot that silently never fires.
    if (icTrRotInj || icTrEulerInj || icTrMatInj) {
        Quat q;
        if (!readTransformRot(tr, &q)) return NO;
    }
    return YES;
}

static NSString *gCamTrSource;
static BOOL gCamTrSwept;

// Camera.main -> Component::get_transform. Identity-checked: the returned
// object's class has to BE UnityEngine.Transform, not merely answer a couple of
// calls, because a bogus pointer dereferenced for a field read is a crash and
// this runs on the display link.
static Il2CppObject *cameraFromEngine(void) {
    if (!icGetTransform) return NULL;
    Il2CppObject *cam = mainCamera();
    if (!cam) return NULL;

    static Il2CppClass *transformCls;
    static BOOL looked;
    if (!looked) {
        looked = YES;
        transformCls = [IL2CPP clsAnywhere:@"Transform" ns:@"UnityEngine"];
    }

    Il2CppApi *api = [IL2CPP api];
    void *p = ((void *(*)(void *))icGetTransform)(cam);
    if (!p || ((uintptr_t)p & 7)) return NULL;
    Il2CppObject *tr = (Il2CppObject *)p;

    if (transformCls && api->object_get_class) {
        if (api->object_get_class(tr) != transformCls) return NULL;
    } else if (api->object_get_class) {
        // Transform is stripped from this build's metadata: fall back to a name
        // check, which is weaker but still rejects a foreign object.
        const char *n = api->class_get_name(api->object_get_class(tr));
        if (!n || strcmp(n, "Transform") != 0) return NULL;
    }

    if (!transformUsable(tr)) return NULL;
    gCamTrSource = @"Camera.main.transform";
    return tr;
}

static BOOL resolveCameraTransform(void) {
    if (!gLocalObj || !gPlayerCls) return NO;
    Il2CppApi *api = [IL2CPP api];

    // Fast path: the field already chosen still works.
    if (fCamTr && transformUsable(FFReadObj(gLocalObj, offCamTr))) return YES;

    NSArray<NSString *> *cands = gMap[@"player"][@"camera_transform_candidates"];
    if (![cands isKindOfClass:NSArray.class] || cands.count == 0) return NO;
    if (!api->class_get_field_from_name) return NO;

    int tried = 0;
    for (NSString *name in cands) {
        if (![name isKindOfClass:NSString.class] || !name.length) continue;
        FieldInfo *f = api->class_get_field_from_name(gPlayerCls, name.UTF8String);
        if (!f || FFFieldIsStatic(f)) continue;
        tried++;
        size_t off = FFFieldOffset(f);
        if (!transformUsable(FFReadObj(gLocalObj, off))) continue;
        fCamTr = f;
        offCamTr = off;
        gCamTrSource = name;
        FFLOG(@"camera transform: using %@ (%d candidate(s) tried)", name, tried);
        return YES;
    }

    if (!gCamTrSwept) {
        gCamTrSwept = YES;
        // Routed through err() rather than the log: this is the one place the
        // candidate count is read, and a value read only by FFLOG is a
        // write-only variable in a RELEASE build (-Werror). It also belongs in
        // DIAGNOSTICS anyway - it is the answer to "why is the aim dead".
        err(@"camera transform: none of %lu candidate(s) behaves like a Transform "
            @"(tried %d) - aim and the matrix w2s path stay off",
            (unsigned long)cands.count, tried);
    }
    return NO;
}

static void updateCamTrSource(void) {
    if (gCamTrSource) return;
    NSString *first = gMap[@"player"][@"camera_transform_field"];
    gCamTrSource = [first isKindOfClass:NSString.class] ? first : @"(unknown)";
}

// ---------------------------------------------------------------- w2s
// Three outcomes, and the difference matters: a missing camera is transient
// (between matches there is no main camera) and must not be counted as a
// failure, or a working icall path would demote itself to the matrix fallback
// for no reason.
enum { W2S_NO_CAMERA = 0, W2S_OK = 1, W2S_GARBAGE = 2 };

static int w2sTryICall(Vec3 world, CGPoint *out, float *depth) {
    Il2CppObject *cam = mainCamera();
    if ((!icW2SInj && !icW2SRet) || !cam || !out) return W2S_NO_CAMERA;
    Vec3 r = {0, 0, 0};
    if (icW2SInj) {
        // void f(void *self, Vec3 *in, Vec3 *out)
        ((void (*)(void *, Vec3 *, Vec3 *))icW2SInj)(cam, &world, &r);
    } else {
        // Vec3 f(void *self, Vec3 in) - the struct comes back in registers, so
        // this must NOT be called through the pointer prototype above
        r = ((Vec3 (*)(void *, Vec3))icW2SRet)(cam, world);
    }
    if (!isfinite(r.x) || !isfinite(r.y) || !isfinite(r.z)) return W2S_GARBAGE;
    // Note: a large but finite result is NOT garbage. A point 500 m off axis
    // projects to a huge pixel coordinate and that is correct behaviour; only
    // non-finite values mean the call did not work. On-screen-ness is decided
    // by the caller, from the returned coordinates.
    CGSize sc = [UIScreen mainScreen].bounds.size;
    out->x = r.x;
    out->y = sc.height - r.y;                 // Unity y is bottom-up
    if (depth) *depth = r.z;
    return W2S_OK;
}

static BOOL w2sMatrix(Vec3 world, CGPoint *out, float *depth) {
    Vec3 cp; Quat cq;
    Il2CppObject *tr = localCameraTransform();
    if (!tr || !readTransformPos(tr, &cp) || !readTransformRot(tr, &cq)) return NO;

    Vec3 d = { world.x - cp.x, world.y - cp.y, world.z - cp.z };
    Quat inv = { -cq.x, -cq.y, -cq.z, cq.w };
    Vec3 c = quatRotate(inv, d);
    if (c.z <= 0.05f) return NO;               // behind the camera

    float fovRad = cameraFov() * (float)M_PI / 180.0f;
    float f = 1.0f / tanf(fovRad * 0.5f);
    CGSize sc = [UIScreen mainScreen].bounds.size;
    float aspect = (float)(sc.width / MAX(sc.height, 1.0));
    float sx = (c.x * f / aspect) / c.z;
    float sy = (c.y * f) / c.z;
    out->x = (sx * 0.5f + 0.5f) * sc.width;
    out->y = (1.0f - (sy * 0.5f + 0.5f)) * sc.height;
    if (depth) *depth = c.z;
    return YES;
}

// ---------------------------------------------------------------- container
static NSArray<NSValue *> *livePlayers(void) {
    NSMutableArray *out = [NSMutableArray array];
    if (!fContainerStatic || !gContainerStaticOwner) return out;

    Il2CppObject *container = readStaticObject(gContainerStaticOwner, fContainerStatic);
    if (!container) return out;
    Il2CppObject *coll = FFReadObj(container, offListField);
    if (!coll) return out;

    if (listIsArray) {
        int32_t n = arrayLength(coll);
        for (int32_t i = 0; i < n; i++) {
            Il2CppObject *e = arrayAt(coll, i);
            if (e) [out addObject:[NSValue valueWithPointer:e]];
        }
        return out;
    }

    Il2CppObject *items = fListItems ? FFReadObj(coll, offItems) : coll;
    int32_t n = fListSize ? FFReadI32(coll, offSize) : arrayLength(items);
    if (n < 0 || n > 2000) n = arrayLength(items);
    for (int32_t i = 0; i < n; i++) {
        Il2CppObject *e = arrayAt(items, i);
        if (e) [out addObject:[NSValue valueWithPointer:e]];
    }
    return out;
}

static void fillPlayer(USEntity *p, Il2CppObject *obj) {
    p.obj = obj;
    if (!obj) return;
    if (fPos) p.world = FFReadVec3(obj, offPos);
    if (fName) p.name = stringOf(FFReadObj(obj, offName)) ?: @"";
    if (fHp) p.hp = numField(obj, fHp, hpIsFloat);
    if (fMaxHp) p.maxHp = numField(obj, fMaxHp, maxIsFloat);
    if (fTeam) p.team = FFReadI32(obj, offTeam);
    if (fLocal) p.isLocal = FFReadI32(obj, offLocal) != 0;
    if (fBot) p.isBot = FFReadI32(obj, offBot) != 0;
}

// ---------------------------------------------------------------- binding
@implementation USModel

+ (NSArray<NSString *> *)bindErrors { return gErrors ?: @[]; }
+ (BOOL)ready { return gReady; }
+ (NSDictionary *)activeMap { return gMap; }
+ (BOOL)w2sReady { return gW2SReady; }
+ (NSString *)w2sMode { return gW2SMode; }
+ (NSString *)resolveReport {
    return gResolveLog.count ? [gResolveLog componentsJoinedByString:@" | "] : @"not resolved";
}

+ (BOOL)autoBind {
    if (!gResolveLog) gResolveLog = [NSMutableArray array];
    [gResolveLog removeAllObjects];
    NSDictionary *map = [USMap autoMapWithLog:^(NSString *line) {
        [gResolveLog addObject:line];
        FFLOG(@"resolve: %@", line);
    }];
    if (!map) return NO;
    return [self bindWithSchema:map];
}

// Probe a list of candidate icall names and take the first that resolves.
// `il2cpp_resolve_icall` is a plain lookup: an unknown name returns NULL, it
// does not abort. So a candidate list costs one call per name and tells us
// exactly which spelling this Unity version uses.
static NSArray<NSString *> *candidatesOf(NSDictionary *d, NSString *single, NSString *plural) {
    NSMutableArray *out = [NSMutableArray array];
    id one = [d isKindOfClass:NSDictionary.class] ? d[single] : nil;
    if ([one isKindOfClass:NSString.class] && [one length]) [out addObject:one];
    id many = [d isKindOfClass:NSDictionary.class] ? d[plural] : nil;
    if ([many isKindOfClass:NSArray.class]) {
        for (id n in many)
            if ([n isKindOfClass:NSString.class] && [n length] && ![out containsObject:n])
                [out addObject:n];
    }
    return out;
}

static void *resolveFirst(NSArray<NSString *> *names, NSString **hit) {
    Il2CppApi *api = [IL2CPP api];
    if (!api->resolve_icall) return NULL;
    for (NSString *n in names) {
        if (gIcallTried && ![gIcallTried containsObject:n]) [gIcallTried addObject:n];
        void *p = api->resolve_icall(n.UTF8String);
        if (p) { if (hit) *hit = n; return p; }
    }
    return NULL;
}

static void *resolveGroup(NSDictionary *d, NSString *single, NSString *plural,
                          NSString *label) {
    NSString *hit = nil;
    void *p = resolveFirst(candidatesOf(d, single, plural), &hit);
    if (hit && gIcallNotes)
        [gIcallNotes addObject:[NSString stringWithFormat:@"%@ <- %@", label, hit]];
    else if (gIcallNotes)
        [gIcallNotes addObject:[NSString stringWithFormat:@"%@ <- none of %lu",
                                label, (unsigned long)candidatesOf(d, single, plural).count]];
    return p;
}

+ (BOOL)bindWithSchema:(NSDictionary *)s {
    gErrors = [NSMutableArray array];
    gReady = NO;
    gW2SReady = NO;
    gW2SMode = @"none";
    gW2SFails = 0;
    gMainCamera = NULL;
    gLocalObj = NULL;
    gCalibrated = NO;
    gProbeStamp = 0;
    if (!gIcallNotes) gIcallNotes = [NSMutableArray array];
    else [gIcallNotes removeAllObjects];
    if (!gIcallTried) gIcallTried = [NSMutableArray array];
    else [gIcallTried removeAllObjects];
    gCalibFrames = 0;
    memset(gCalibErr, 0, sizeof(gCalibErr));
    fName = fHp = fMaxHp = fTeam = fLocal = fCamTr = fBot = fPos = NULL;
    fListField = fContainerStatic = fListItems = fListSize = NULL;
    gContainerCls = gContainerStaticOwner = gListCls = NULL;
    offName = offHp = offMaxHp = offTeam = offLocal = offCamTr = offBot = offPos = 0;
    offListField = offItems = offSize = 0;

    if (![IL2CPP loadAPI]) { err(@"il2cpp runtime not resolved"); return NO; }
    gMap = s;
    if (![s isKindOfClass:NSDictionary.class] || s.count == 0) { err(@"empty schema"); return NO; }
    // Bind only against a map that has the keys and no template values. This
    // is the check that was missing: `container_class: "CHANGE_ME"` is a
    // non-empty string, so the old guard passed it, every field lookup then
    // returned NULL, and the symptom was "the menu works but nothing happens".
    if (!FFSchemaUsable(s)) { err(@"schema is a template / incomplete - not binding to it"); return NO; }

    // A gain measured in an earlier session still applies; without this the
    // aim would need re-calibrating on every launch.
    loadGains();

    Il2CppApi *api = [IL2CPP api];
    NSString *image = s[@"image"];
    NSDictionary *p = s[@"player"] ?: @{};
    NSDictionary *cam = s[@"camera"] ?: @{};
    NSDictionary *tr = s[@"transform"] ?: @{};

    // ---- struct offsets. Precedence: a usable schema value, then the runtime's
    // own answer, then the documented layout compiled in below.
    //
    // This used to be `if (!arrLenOff && api->offset_of_array_length)`, and
    // arrLenOff defaults to 24 - truthy - so the runtime was never asked. The
    // header and the report both claimed the array header comes from
    // il2cpp_offset_of_array_length_in_array_object_header. Now it does.
    //
    // A value <= 0 is never a real offset, so it means "absent", not "override
    // with 0" - a map that says array_data_offset: 0 would otherwise make every
    // element read land in the array header instead of on the elements.
    NSInteger v;

    v = [p[@"string_len_offset"] integerValue];
    if (v > 0) strLenOff = v;
    v = [p[@"string_data_offset"] integerValue];
    if (v > 0) strDataOff = v;

    v = [p[@"array_len_offset"] integerValue];
    if (v > 0) {
        arrLenOff = v;
    } else if (api->offset_of_array_length) {
        int32_t lo = api->offset_of_array_length();
        if (lo > 0) arrLenOff = lo;
    }

    v = [p[@"array_data_offset"] integerValue];
    if (v > 0) {
        arrDataOff = v;
    } else if (api->array_object_header_size) {
        int32_t hdr = api->array_object_header_size();
        if (hdr > 0) arrDataOff = hdr;
    }

    // The two string offsets cannot be queried, only measured - USMap does that
    // with a probe string and puts the result in the map. If a hand-written map
    // omits them, the compiled fallback (16 / 20) is the standard il2cpp string
    // layout and `il2cpp_string_chars` is used in preference to it anyway.

    // ---- player class
    NSString *pNs = p[@"namespace"] ?: @"";
    NSString *pCls = p[@"class"] ?: @"";
    gPlayerCls = [IL2CPP cls:pCls inImage:image ns:pNs] ?: [IL2CPP clsAnywhere:pCls ns:pNs];
    if (!gPlayerCls) { err(@"player class %@.%@ not found", pNs, pCls); return NO; }

    #define BIND(var, off, key)                                                  \
        do {                                                                     \
            NSString *nm = p[key];                                               \
            if ([nm isKindOfClass:NSString.class] && nm.length) {                 \
                var = api->class_get_field_from_name(gPlayerCls, nm.UTF8String);   \
                if (var) off = FFFieldOffset(var);                                \
                else err(@"field %@ missing on player", nm);                      \
            }                                                                    \
        } while (0)

    BIND(fName,  offName,  @"name_field");
    BIND(fHp,    offHp,    @"hp_field");
    BIND(fMaxHp, offMaxHp, @"max_hp_field");
    BIND(fTeam,  offTeam,  @"team_field");
    BIND(fLocal, offLocal, @"is_local_field");
    BIND(fCamTr, offCamTr, @"camera_transform_field");
    BIND(fBot,   offBot,   @"bot_field");
    BIND(fPos,   offPos,   @"position_field");
    #undef BIND

    // Record which name the schema offered, then make sure it actually works.
    // The confirmation may have to wait for a match (there is no local player in
    // the lobby), which is why calibrate: retries it too.
    gCamTrSource = nil;
    updateCamTrSource();
    resolveCameraTransform();

    if (fHp) {
        NSString *t = FFFieldTypeName(fHp);
        hpIsFloat = [t isEqualToString:@"System.Single"] || [t isEqualToString:@"System.Double"];
    }
    if (fMaxHp) {
        NSString *t = FFFieldTypeName(fMaxHp);
        maxIsFloat = [t isEqualToString:@"System.Single"] || [t isEqualToString:@"System.Double"];
    }

    gPosCandidates = [NSMutableArray array];
    for (NSString *nm in p[@"position_candidates"] ?: @[]) {
        if (![nm isKindOfClass:NSString.class]) continue;
        if (api->class_get_field_from_name(gPlayerCls, nm.UTF8String))
            [gPosCandidates addObject:nm];
    }

    // ---- container + singleton
    NSString *cNs = p[@"container_namespace"] ?: @"";
    NSString *cCls = p[@"container_class"] ?: @"";
    listIsArray = [p[@"list_kind"] isEqualToString:@"array"];
    gContainerCls = cCls.length
        ? ([IL2CPP cls:cCls inImage:image ns:cNs] ?: [IL2CPP clsAnywhere:cCls ns:cNs]) : NULL;
    if (!gContainerCls) err(@"container class %@.%@ not found", cNs, cCls);

    NSString *listFieldName = p[@"list_field"];
    if (gContainerCls && [listFieldName isKindOfClass:NSString.class] && listFieldName.length) {
        fListField = api->class_get_field_from_name(gContainerCls, listFieldName.UTF8String);
        if (fListField) offListField = FFFieldOffset(fListField);
        else err(@"list field %@ missing on container", listFieldName);
    }

    NSString *stName = p[@"container_static"];
    NSString *stOwner = p[@"container_static_owner"];
    if ([stName isKindOfClass:NSString.class] && stName.length) {
        gContainerStaticOwner = gContainerCls;
        if ([stOwner isKindOfClass:NSString.class] && stOwner.length &&
            ![stOwner isEqualToString:cCls]) {
            Il2CppClass *o = [IL2CPP clsAnywhere:stOwner ns:cNs] ?: [IL2CPP clsAnywhere:stOwner];
            if (o) gContainerStaticOwner = o;
        }
        if (gContainerStaticOwner)
            fContainerStatic = api->class_get_field_from_name(gContainerStaticOwner, stName.UTF8String);
        if (!fContainerStatic) err(@"static %@ not found", stName);
    } else {
        err(@"no static holder for the player collection");
    }

    // ---- List<T> internals from the inflated class the field points at
    if (fListField && !listIsArray && api->field_get_type && api->class_from_type) {
        const Il2CppType *t = api->field_get_type(fListField);
        gListCls = t ? api->class_from_type(t) : NULL;
        if (gListCls) {
            fListItems = api->class_get_field_from_name(gListCls, "_items");
            fListSize  = api->class_get_field_from_name(gListCls, "_size");
            if (fListItems) offItems = FFFieldOffset(fListItems);
            if (fListSize)  offSize  = FFFieldOffset(fListSize);
            if (!fListItems || !fListSize)
                err(@"_items/_size missing on %@", [IL2CPP fullNameOf:gListCls]);
        } else {
            err(@"could not inflate the List<> type");
        }
    }

    // ---- icalls, by candidate list, grouped by calling convention
    icW2SInj   = resolveGroup(cam, @"icall_w2s_injected", @"icall_w2s_injected_candidates", @"w2s.inj");
    icW2SRet   = resolveGroup(cam, @"icall_w2s_value", @"icall_w2s_value_candidates", @"w2s.val");
    icGetMain  = resolveGroup(cam, @"icall_get_main", @"icall_get_main_candidates", @"camera");
    icFov      = resolveGroup(cam, @"icall_fov", @"icall_fov_candidates", @"fov");
    icGetTransform = resolveGroup(cam, @"icall_component_transform",
                                  @"icall_component_transform_candidates", @"cam.tr");
    icTrPosInj = resolveGroup(tr, @"icall_position_injected", @"icall_position_candidates", @"pos");
    icTrRotInj = resolveGroup(tr, @"icall_rotation_injected", @"icall_rotation_candidates", @"rot");
    icTrEulerInj = resolveGroup(tr, @"icall_euler_injected", @"icall_euler_candidates", @"euler");
    icTrMatInj   = resolveGroup(tr, @"icall_matrix_injected", @"icall_matrix_candidates", @"matrix");

    // ---- readiness
    BOOL haveContainer = gContainerCls && fListField && fContainerStatic;
    if (!fPos) err(@"no position field on the player class");
    gReady = haveContainer && fPos && fListField;

    [self probeW2S];

    FFLOG(@"bind %@  w2s=%@  name=%s hp=%s team=%s pos=%s",
          gReady ? @"OK" : @"PARTIAL", gW2SMode,
          fName ? "y" : "n", fHp ? "y" : "n", fTeam ? "y" : "n", fPos ? "y" : "n");
    return gReady;
}

// The camera does not exist until a match does, so one probe at bind time is
// not enough: this runs again from +tick until it succeeds, and then
// periodically so a mode can recover if the game changes camera behaviour.
+ (void)probeW2S {
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
    if (now - gProbeStamp < 2.0) return;
    gProbeStamp = now;

    if (!fPos && !fCamTr) return;

    // 1. the icall is the engine's own projection: exact by definition.
    //    Several probe points, because a single world-space point can be
    //    legitimately off screen and that must not read as "broken".
    if ((icW2SInj || icW2SRet) && icGetMain && mainCamera()) {
        Vec3 probes[4];
        int np = 0;
        // a real subject: the local player, if we already have one
        if (gLocalObj && fPos) {
            Vec3 lp = FFReadVec3(gLocalObj, offPos);
            if (lp.x != 0.0f || lp.y != 0.0f || lp.z != 0.0f) probes[np++] = lp;
        }
        // otherwise: right in front of the camera, if the pose is readable
        Il2CppObject *tr = localCameraTransform();
        Vec3 cp; Quat cq;
        if (tr && readTransformPos(tr, &cp) && readTransformRot(tr, &cq)) {
            Vec3 f = {0, 0, 1};
            Vec3 fwd = quatRotate(cq, f);
            Vec3 p1 = {cp.x + fwd.x * 8.0f, cp.y + fwd.y * 8.0f, cp.z + fwd.z * 8.0f};
            probes[np++] = p1;
        }
        // last resort: a fixed point. A finite output is all we need.
        Vec3 p2 = {0, 10, 0};
        probes[np++] = p2;

        BOOL anyOK = NO, anyGarbage = NO;
        for (int i = 0; i < np; i++) {
            CGPoint o = CGPointZero; float d = 0;
            int r = w2sTryICall(probes[i], &o, &d);
            if (r == W2S_OK) { anyOK = YES; break; }
            if (r == W2S_GARBAGE) anyGarbage = YES;
        }
        if (anyOK) {
            if (!gW2SReady || ![gW2SMode isEqualToString:@"icall"]) {
                FFLOG(@"w2s: icall path confirmed - %@", [self w2sReport]);
            }
            gW2SReady = YES;
            gW2SMode = @"icall";
            gW2SFails = 0;
            return;
        }
        if (!anyGarbage && !gW2SReady) {
            FFLOG(@"w2s: camera present, no probe returned a finite projection yet");
        }
    }

    // 2. matrix fallback, if the camera pose is readable
    if (!gW2SReady && icTrPosInj && fCamTr &&
        (icTrRotInj || icTrEulerInj || icTrMatInj)) {
        gW2SReady = YES;
        gW2SMode = @"matrix";
        FFLOG(@"w2s: matrix path (no usable camera icall) - %@", [self w2sReport]);
        return;
    }

    if (!gW2SReady) {
        BOOL noCamIcall = (icGetMain == NULL) || (icW2SInj == NULL && icW2SRet == NULL);
        BOOL noPose = (icTrPosInj == NULL) ||
                      (!icTrRotInj && !icTrEulerInj && !icTrMatInj);
        if (noCamIcall && (noPose || !fCamTr))
            FFLOG(@"w2s: no path yet - camera icall %s, transform pose %s, camera transform %s",
                  noCamIcall ? "missing" : "ok",
                  noPose ? "missing" : "ok",
                  fCamTr ? "present" : "missing");
    }
}

+ (NSString *)w2sReport {
    NSMutableString *s = [NSMutableString stringWithFormat:@"mode=%@", gW2SMode];
    if (gIcallNotes.count)
        [s appendFormat:@"  [%@]", [gIcallNotes componentsJoinedByString:@", "]];
    if (!gW2SReady) {
        [s appendString:@"  no path yet"];
        if (gIcallTried.count)
            [s appendFormat:@"  tried: %@", [gIcallTried componentsJoinedByString:@", "]];
    }
    return s;
}

// ---------------------------------------------------------------- calibration
// Which Vector3 on the player class is the world position? Names are
// obfuscated, so decide by measurement: the local player's position must stay
// within a few metres of its own camera transform while both move.
+ (void)calibrate:(NSArray<USEntity *> *)all {
    if (gCalibrated || !fCamTr || !icTrPosInj) return;
    if (gPosCandidates.count == 0) { gCalibrated = YES; return; }

    USEntity *local = nil;
    for (USEntity *p in all) { if (p.isLocal) { local = p; break; } }
    if (!local) { for (USEntity *p in all) if (FFReadObj(p.obj, offCamTr)) { local = p; break; } }
    if (!local) return;

    Vec3 cp;
    Il2CppObject *camTr = FFReadObj(local.obj, offCamTr);
    if (!camTr || !readTransformPos(camTr, &cp)) return;

    Il2CppApi *api = [IL2CPP api];
    gCalibFrames++;
    NSUInteger n = MIN(gPosCandidates.count, (NSUInteger)32);
    for (NSUInteger i = 0; i < n; i++) {
        FieldInfo *f = api->class_get_field_from_name(gPlayerCls,
                                                      [gPosCandidates[i] UTF8String]);
        if (!f) { gCalibErr[i] += 50.0; continue; }
        Vec3 v = FFReadVec3(local.obj, FFFieldOffset(f));
        double dx = v.x - cp.x, dy = v.y - cp.y, dz = v.z - cp.z;
        double d = sqrt(dx * dx + dy * dy + dz * dz);
        gCalibErr[i] += (d > 0.3 && d < 8.0) ? d : 50.0;
    }

    if (gCalibFrames < 45) return;               // ~0.75 s at 60 fps

    int bestIdx = -1;
    double best = 1e18;
    for (NSUInteger i = 0; i < n; i++) {
        if (gCalibErr[i] < best) { best = gCalibErr[i]; bestIdx = (int)i; }
    }
    gCalibrated = YES;
    if (bestIdx < 0 || best > 45.0 * (double)gCalibFrames) {
        FFLOG(@"calibration: no Vector3 tracked the camera (best %.1f); keeping %@",
              best, gPosCandidates.firstObject);
        return;
    }
    FieldInfo *f = api->class_get_field_from_name(gPlayerCls, [gPosCandidates[bestIdx] UTF8String]);
    if (f) {
        fPos = f;
        offPos = FFFieldOffset(f);
        FFLOG(@"calibration: position field = %@ (avg %.2f m over %lu frames)",
              gPosCandidates[bestIdx], best / (double)gCalibFrames,
              (unsigned long)gCalibFrames);
    }
}

// ---------------------------------------------------------------- read
+ (USEntity *)localPlayer {
    if (!gReady) return nil;
    for (NSValue *v in livePlayers()) {
        USEntity *p = [USEntity new];
        fillPlayer(p, (Il2CppObject *)v.pointerValue);
        if (p.isLocal) return p;
    }
    if (!fCamTr) return nil;
    for (NSValue *v in livePlayers()) {
        Il2CppObject *o = (Il2CppObject *)v.pointerValue;
        if (FFReadObj(o, offCamTr)) {
            USEntity *p = [USEntity new];
            fillPlayer(p, o);
            p.isLocal = YES;
            return p;
        }
    }
    return nil;
}

+ (NSArray<USEntity *> *)players {
    if (!gReady) return @[];

    NSArray<NSValue *> *raw = livePlayers();
    if (raw.count == 0) return @[];

    NSMutableArray<USEntity *> *all = [NSMutableArray arrayWithCapacity:raw.count];
    for (NSValue *v in raw) {
        Il2CppObject *o = (Il2CppObject *)v.pointerValue;
        if (!o) continue;
        USEntity *p = [USEntity new];
        fillPlayer(p, o);
        [all addObject:p];
    }

    USEntity *local = nil;
    for (USEntity *p in all) { if (p.isLocal) { local = p; break; } }
    if (!local && fCamTr) {
        for (USEntity *p in all) {
            if (FFReadObj(p.obj, offCamTr)) { p.isLocal = YES; local = p; break; }
        }
    }
    gLocalObj = local.obj;

    [self calibrate:all];

    // Calibration can swap the position field, and `all` was filled before it
    // ran - so without this re-read the first frame after calibration (and
    // every frame if calibration picked a different field than the schema did)
    // is drawn at the wrong world position.
    if (fPos) {
        for (USEntity *p in all) p.world = FFReadVec3(p.obj, offPos);
    }

    Vec3 localPos = {0, 0, 0};
    if (local) localPos = FFReadVec3(local.obj, offPos);

    NSMutableArray<USEntity *> *out = [NSMutableArray arrayWithCapacity:all.count];
    for (USEntity *p in all) {
        if (p == local) continue;
        p.isLocal = NO;
        CGPoint sc = CGPointZero; float depth = 0;
        BOOL projected = [self worldToScreen:p.world out:&sc depth:&depth];
        p.screen = sc;
        p.depth = depth;
        // on-screen is decided here, not by worldToScreen: a finite projection
        // far outside the viewport is still a correct projection.
        CGSize scr = [UIScreen mainScreen].bounds.size;
        const CGFloat margin = 64.0;
        p.onScreen = projected
            && sc.x > -margin && sc.x < scr.width + margin
            && sc.y > -margin && sc.y < scr.height + margin;
        double dx = p.world.x - localPos.x;
        double dy = p.world.y - localPos.y;
        double dz = p.world.z - localPos.z;
        p.distance = (float)sqrt(dx * dx + dy * dy + dz * dz);
        [out addObject:p];
    }
    return out;
}

+ (BOOL)worldToScreen:(Vec3)world out:(CGPoint *)out depth:(float *)depth {
    if (!out) return NO;

    if ([gW2SMode isEqualToString:@"icall"]) {
        int r = w2sTryICall(world, out, depth);
        if (r == W2S_OK) { gW2SFails = 0; return YES; }
        if (r == W2S_GARBAGE) {
            // Answered, but with nonsense. After 60 of those in a row, stop
            // trusting it - and keep probing, so it can come back.
            if (++gW2SFails > 60) {
                gW2SMode = @"matrix";
                gW2SFails = 0;
                FFLOG(@"w2s: icall returned garbage 60x, demoting to matrix - %@",
                      [self w2sReport]);
            }
        }
        return w2sMatrix(world, out, depth);
    }

    if (w2sMatrix(world, out, depth)) return YES;
    // the matrix path can fail early in a match while the transform is still
    // loading; the icall may already work
    if (w2sTryICall(world, out, depth) == W2S_OK) {
        gW2SMode = @"icall";
        FFLOG(@"w2s: matrix failed but the icall works, switching back");
        return YES;
    }
    return NO;
}

// ---------------------------------------------------------------- aim
// Called once per frame by the ESP display link. Two jobs: keep the
// world-to-screen path probed, and never leave a synthetic finger on screen.
+ (void)tick {
    [self probeW2S];

    if (![USInput isDown]) return;
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
    // A finger that never lifts is the most obvious signature there is.
    if (now - gAimLast > 0.35) {
        [USInput release_:@"aim"];
        FFLOG(@"aim: released (idle)");
    }
}

+ (BOOL)aimCalibrated { return gAimCalibrated; }

// Measure the two gains by moving the camera a known distance and reading how
// far it turned. The alternative - assuming a sensitivity - produces an aimbot
// that drags the crosshair past everything.
//
// This moves the real camera, so the caller must be in a match and must accept
// the view swinging right then up by about a screen-width of travel. It runs
// ~450 ms of sleeps, so it must NOT be called from the display link.
+ (BOOL)calibrateAim {
    if (![USInput available]) {
        err(@"calibrate: touch injection unavailable");
        return NO;
    }
    // Retry the camera-transform choice: in the lobby the local player does not
    // exist yet, so the confirmation could not run at bind time.
    resolveCameraTransform();

    Il2CppObject *camTr = localCameraTransform();
    if (!camTr) {
        err(@"calibrate: no camera transform - start a match first");
        return NO;
    }
    Quat q0;
    if (!readTransformRot(camTr, &q0)) {
        err(@"calibrate: camera rotation unreadable - need the matrix icall");
        return NO;
    }
    gAimCalibrated = NO;
    // If the aimbot already holds the finger, take it back - the two cannot
    // share one touch stream, and the measurement has to own it.
    [USInput release_:@"aim"];
    if (![USInput acquire:@"cal"]) {
        // Someone else owns the touch stream; do not leave the aim disabled
        // because of a calibration that never ran.
        loadGains();
        return NO;
    }

    CGSize sc = [UIScreen mainScreen].bounds.size;
    CGPoint c = CGPointMake(sc.width * 0.5, sc.height * 0.5);
    const double N = 48.0;
    double gainX = 0.0, gainY = 0.0;
    BOOL measured = NO;

    if ([USInput touchDown:c]) {
        measured = YES;
        Quat a, b, e;

        // yaw: from the reference pose, drag right by N points
        [USInput drag:CGPointMake(c.x + (CGFloat)N, c.y)];
        usleep(150000);
        if (readTransformRot(camTr, &a)) {
            double dyaw = wrapPi(yawOfQuat(a) - yawOfQuat(q0));
            if (fabs(dyaw) > 1e-4) gainX = dyaw / N;
        }

        // pitch: come back to the centre first so the reference is the pose the
        // upward drag actually starts from, then drag up by N points. "up" is
        // -N in screen coordinates, which is why the divisor is negative: the
        // sign of the gain then already accounts for an inverted look axis.
        [USInput drag:c];
        usleep(150000);
        if (readTransformRot(camTr, &b)) {
            [USInput drag:CGPointMake(c.x, c.y - (CGFloat)N)];
            usleep(150000);
            if (readTransformRot(camTr, &e)) {
                double dpitch = pitchOfQuat(e) - pitchOfQuat(b);
                if (fabs(dpitch) > 1e-4) gainY = dpitch / (-N);
            }
        }

        [USInput drag:c];
    }
    [USInput release_:@"cal"];

    if (!measured || !gainPlausible(gainX) || !gainPlausible(gainY)) {
        err(@"calibrate: measured no usable rotation (camera static, or the drag was ignored)");
        // A failed attempt must not throw away a gain that worked before.
        loadGains();
        return NO;
    }

    gGainX = gainX;
    gGainY = gainY;
    gAimCalibrated = YES;
    gAimWarned = NO;
    gHaveObservation = NO;      // the calibration moved the camera; reference is stale
    [USConfig.shared setRaw:@(gGainX) forKey:kFFGainX];
    [USConfig.shared setRaw:@(gGainY) forKey:kFFGainY];
    [USConfig.shared save];
    FFLOG(@"calibrate: gain x=%.6f y=%.6f rad/pt", gGainX, gGainY);
    return YES;
}

+ (BOOL)aimAt:(USEntity *)target bone:(NSString *)bone smooth:(int)smooth {
    if (!target) return NO;
    Il2CppObject *camTr = localCameraTransform();
    if (!camTr) return NO;

    if (!gAimCalibrated) {
        if (!gAimWarned) {
            gAimWarned = YES;
            FFLOG(@"aim: no measured gain yet - run CALIBRATE in DIAGNOSTICS");
        }
        return NO;
    }
    if (![USInput available] || ![USInput acquire:@"aim"]) return NO;

    Quat q;
    Vec3 camPos;
    if (!readTransformRot(camTr, &q) || !readTransformPos(camTr, &camPos)) return NO;

    // Refine the gain from what the camera actually did in response to the last
    // correction, before trusting the current estimate for another frame.
    // An estimate is only accepted when it is plausible AND agrees with the
    // sign already measured - a second finger moving the view would otherwise
    // corrupt the gain in a handful of frames.
    if (gHaveObservation) {
        double nowYaw = yawOfQuat(q), nowPitch = pitchOfQuat(q);
        double dYaw = wrapPi(nowYaw - gYawAtApply);
        double dPitch = nowPitch - gPitchAtApply;
        // Thresholds matter in both directions. Too high and a gain that is far
        // too big never moves enough to be observed, so it can never correct
        // itself - and that is the case that crawls. Too low and an estimate is
        // taken from a frame where nothing actually moved, which drags the gain
        // toward zero. So: the finger had to move a measurable amount AND the
        // camera had to answer.
        if (fabs(gLastDx) > 0.25 && fabs(dYaw) > 1e-4) {
            double est = dYaw / gLastDx;
            if (gainPlausible(est) && est * gGainX > 0.0) {
                gGainX += (est - gGainX) * 0.25;
            }
        }
        if (fabs(gLastDy) > 0.25 && fabs(dPitch) > 1e-4) {
            double est = dPitch / gLastDy;
            if (gainPlausible(est) && est * gGainY > 0.0) {
                gGainY += (est - gGainY) * 0.25;
            }
        }
    }

    Vec3 aim = target.world;
    aim.y += [bone isEqualToString:@"chest"] ? 1.20f : 1.55f;

    Vec3 d = { aim.x - camPos.x, aim.y - camPos.y, aim.z - camPos.z };
    float len = sqrtf(d.x * d.x + d.y * d.y + d.z * d.z);
    if (len < 0.5f) return NO;                     // standing on top of the target
    d.x /= len; d.y /= len; d.z /= len;

    // Into camera space: +z forward, +x right, +y up.
    Vec3 dl = quatInvRotate(q, d);
    if (dl.z <= 0.0f) return NO;                   // behind the camera
    double horiz = sqrt((double)dl.x * dl.x + (double)dl.z * dl.z);
    double yawErr   = atan2((double)dl.x, (double)dl.z);
    double pitchErr = atan2((double)dl.y, horiz);

    // Below a third of a degree the correction is less than one point of travel;
    // chasing it just makes the crosshair jitter.
    const double dead = 0.0058;
    if (fabs(yawErr) < dead && fabs(pitchErr) < dead) {
        NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
        // On target: keep the finger where it is, but not indefinitely.
        if ([USInput isDown] && now > gAimHoldUntil) {
            [USInput release_:@"aim"];
            gHaveObservation = NO;
            FFLOG(@"aim: released after a full hold");
            return YES;
        }
        gAimLast = now;
        // No correction was applied this frame, so the pose reference from the
        // last one has been consumed: do not let the next call bill the same
        // rotation twice.
        gHaveObservation = NO;
        return YES;
    }

    // Finger travel that would cancel the error exactly, then take a fraction of
    // it. smooth is the number of frames to close the gap, so the slider finally
    // means what it says. The per-frame cap is what stops a snap on a fresh
    // target even at the fastest setting.
    double dxFull = yawErr   / gGainX;
    double dyFull = pitchErr / gGainY;
    int steps = MAX(2, MIN(smooth, 20));
    double k = 1.0 / (double)steps;
    const double maxStep = 24.0;
    double dx = MAX(-maxStep, MIN(maxStep, dxFull * k));
    double dy = MAX(-maxStep, MIN(maxStep, dyFull * k));

    CGSize sc = [UIScreen mainScreen].bounds.size;
    if (![USInput isDown]) {
        gAimCursor = CGPointMake(sc.width * 0.5, sc.height * 0.5);
        if (![USInput touchDown:gAimCursor]) return NO;
        // 2.0 - 3.2 s, fresh each time, so the lift pattern is not a metronome.
        gAimHoldUntil = [NSDate timeIntervalSinceReferenceDate] +
                        2.0 + (double)arc4random_uniform(120) / 100.0;
        // The pose reference is only valid while this finger stays down.
        gHaveObservation = NO;
        FFLOG(@"aim: engaged on %@ (%.0fm)", target.name ?: @"?", target.distance);
    }

    // Remember the pose this correction starts from, before the finger moves.
    gYawAtApply = yawOfQuat(q);
    gPitchAtApply = pitchOfQuat(q);
    gLastDx = dx;
    gLastDy = dy;
    gHaveObservation = YES;

    gAimCursor.x = (CGFloat)MAX(1.0, MIN((double)sc.width  - 1.0, (double)gAimCursor.x + dx));
    gAimCursor.y = (CGFloat)MAX(1.0, MIN((double)sc.height - 1.0, (double)gAimCursor.y + dy));

    gAimLast = [NSDate timeIntervalSinceReferenceDate];
    return [USInput drag:gAimCursor];
}

+ (void)releaseAim {
    [USInput release_:@"aim"];
}

// ---------------------------------------------------------------- diagnostics
+ (NSString *)diagnostics {
    NSMutableString *s = [NSMutableString string];
    [s appendFormat:@"%@\n", [IL2CPP report]];
    [s appendFormat:@"master: %@\n", gReady ? @"READY" : @"NOT BOUND"];
    [s appendFormat:@"schema source: %@\n", [USConfig.shared schemaSource]];
    [s appendFormat:@"w2s: %@\n", [self w2sReport]];
    [s appendFormat:@"camera transform: %@ (%@)\n",
        gCamTrSource ?: @"(none)",
        (fCamTr && gLocalObj) ? (transformUsable(FFReadObj(gLocalObj, offCamTr))
                                 ? @"verified" : @"UNVERIFIED") : @"not tested"];
    [s appendFormat:@"targeting: %@ (gain x=%.5f y=%.5f rad/pt)\n",
        gAimCalibrated ? @"calibrated" : @"NOT CALIBRATED - press CALIBRATE",
        gGainX, gGainY];
    [s appendFormat:@"%@\n", [USInput report]];
    [s appendFormat:@"%@\n", [USMap describe:gMap ?: @{}]];
    if (gResolveLog.count) [s appendFormat:@"%@\n", [gResolveLog componentsJoinedByString:@"\n"]];
    if (gErrors.count) [s appendFormat:@"errors:\n%@\n", [gErrors componentsJoinedByString:@"\n"]];
    return s;
}

@end