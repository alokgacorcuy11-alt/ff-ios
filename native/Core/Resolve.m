// Resolve.m

#import "Resolve.h"
#include <math.h>

// Namespaces that never contain the in-match gameplay layer. Skipping them is
// what keeps the container scan to well under a second.
static NSArray<NSString *> *skipPrefixes(void) {
    static NSArray *p;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        p = @[@"UnityEngine", @"Unity", @"System", @"Mono", @"Microsoft",
              @"Internal", @"JetBrains", @"Newtonsoft", @"TMPro", @"Spine",
              @"DG", @"SF", @"Beebyte", @"Firebase", @"Newtonsoft", @"ProtoBuf",
              @"Cysharp", @"Google"];
    });
    return p;
}

static BOOL skipNamespace(NSString *ns) {
    if (!ns.length) return NO;
    for (NSString *p in skipPrefixes()) {
        if ([ns isEqualToString:p]) return YES;
        if ([ns hasPrefix:[p stringByAppendingString:@"."]]) return YES;
    }
    return NO;
}

// ---------------------------------------------------------------- name match
// "=exact" | "^prefix" | "suffix$" | plain substring (case-insensitive)
static BOOL nameHit(NSString *name, NSString *pref) {
    if ([pref hasPrefix:@"="]) return [name isEqualToString:[pref substringFromIndex:1]];
    if ([pref hasPrefix:@"^"]) return [name hasPrefix:[pref substringFromIndex:1]];
    if ([pref hasSuffix:@"$"]) return [name hasSuffix:[pref substringToIndex:pref.length - 1]];
    return [name.lowercaseString rangeOfString:pref.lowercaseString].location != NSNotFound;
}

// A block type gets a name so that "a function returning a block" does not have
// to be spelled out twice. The first attempt at this was written as
// `static int (^rankByName)(NSArray *) = ^int(NSArray *){ return ^int(NSString*){...}; }`
// which declares a *block variable* and then returns a block from it - not a
// thing a block can do. That is what broke `make`.
typedef int (^FFNameScore)(NSString *name);

// The element type of an array or a generic List, or nil when the type is
// neither. Used to require an EXACT element match: a substring test also
// accepts List<PlayerView> or PlayerData[], and a collection of the wrong type
// resolves cleanly and draws nothing - there is no crash to tell you which
// mistake it was.
static NSString *elementOf(NSString *t) {
    if (!t.length) return nil;
    if ([t hasSuffix:@"[]"]) return [t substringToIndex:t.length - 2];
    NSRange lt = [t rangeOfString:@"<" options:NSBackwardsSearch];
    if (lt.location == NSNotFound || ![t hasSuffix:@">"]) return nil;
    if (lt.location + 2 > t.length) return nil;
    NSString *inner = [t substringWithRange:NSMakeRange(lt.location + 1,
                                                        t.length - lt.location - 2)];
    if ([inner rangeOfString:@"<"].location != NSNotFound) return nil;   // nested
    return inner;
}

static BOOL isListType(NSString *t) {
    return [t hasPrefix:@"System.Collections.Generic.List`1<"]
        || [t hasPrefix:@"System.Collections.Generic.List<"];
}

// Earlier entries in `prefs` beat later ones. Always returns a heap block.
static FFNameScore rankFrom(NSArray<NSString *> *prefs) {
    NSArray<NSString *> *owned = [prefs copy];
    FFNameScore scorer = ^int(NSString *name) {
        int best = 0;
        for (NSUInteger i = 0; i < owned.count; i++) {
            if (!nameHit(name, owned[i])) continue;
            int s = (int)(1000 - (int)i * 17);
            if (s > best) best = s;
        }
        return best;
    };
    // -copy moves the block off this stack frame; returning the literal as-is
    // would hand the caller a stack block that dies on return.
    return (FFNameScore)[scorer copy];
}

@implementation USMap

// ---------------------------------------------------------------- snapshot
// Every field of a class, as plain dictionaries, so the selection logic reads
// like a query instead of pointer soup.
static NSArray<NSDictionary *> *fieldSnapshot(Il2CppClass *cls) {
    NSMutableArray *out = [NSMutableArray array];
    Il2CppApi *api = [IL2CPP api];
    if (!cls || !api->class_get_fields) return out;
    void *it = NULL;
    const FieldInfo *f;
    while ((f = api->class_get_fields(cls, &it))) {
        FieldInfo *fi = (FieldInfo *)f;
        if (FFFieldIsLiteral(fi)) continue;
        const char *cn = api->field_get_name(fi);
        NSString *name = cn ? @(cn) : @"";
        NSString *type = FFFieldTypeName(fi);
        if (!name.length || !type.length) continue;
        [out addObject:@{
            @"name": name,
            @"type": type,
            @"offset": @(FFFieldOffset(fi)),
            @"static": @(FFFieldIsStatic(fi)),
        }];
    }
    return out;
}

static NSDictionary *pick(NSArray<NSDictionary *> *fields,
                          BOOL wantStatic,
                          BOOL (^typeOK)(NSString *type),
                          FFNameScore nameScore) {
    NSDictionary *best = nil;
    int bestScore = -1;
    for (NSDictionary *f in fields) {
        if ([f[@"static"] boolValue] != wantStatic) continue;
        if (typeOK && !typeOK(f[@"type"])) continue;
        int s = nameScore ? nameScore(f[@"name"]) : 0;
        if (s < 0) continue;
        if (s > bestScore) { bestScore = s; best = f; }
    }
    return best;
}

// ---------------------------------------------------------------- measuring
// Do not assume the Il2CppString layout: create a known string through the
// runtime and find where its characters actually are. The layout is then a
// measured fact for this build.
//
// The old version created "aA9z" but searched for 'F','F','P','R', so the loop
// could never match and the defaults were silently kept. Now the probe and the
// search pattern are the same four UTF-16 units, and the length field is
// verified to be 4 as well - so a coincidental byte match cannot win.
static void measureStringLayout(NSInteger *lenOff, NSInteger *dataOff) {
    Il2CppApi *api = [IL2CPP api];
    if (!api->string_new || !api->string_chars) return;
    static const uint16_t probe[4] = { 'a', 'A', '9', 'z' };
    Il2CppString *s = api->string_new("aA9z");
    if (!s) return;
    const uint8_t *base = (const uint8_t *)s;

    for (NSInteger off = 12; off <= 36; off += 2) {
        const uint16_t *w = (const uint16_t *)(base + off);
        if (w[0] != probe[0] || w[1] != probe[1] ||
            w[2] != probe[2] || w[3] != probe[3]) continue;

        // length sits in the 16 bytes before the character data, 4-byte aligned
        NSInteger lo = -1;
        for (NSInteger cand = off - 4; cand >= 8 && cand >= off - 16; cand -= 4) {
            if (*(const int32_t *)(base + cand) == 4) { lo = cand; break; }
        }
        if (lo < 0) continue;

        if (dataOff) *dataOff = off;
        if (lenOff)  *lenOff = lo;
        return;
    }
}

// ---------------------------------------------------------------- player
static Il2CppClass *findPlayerClass(NSString **imageName) {
    NSArray<NSString *> *images = [IL2CPP imageNames];
    // Class names are not obfuscated: COW.GamePlay.Player carries 393 readable
    // member names in 1.132.1. Anchoring on it is deliberate, not a guess.
    for (NSString *img in images) {
        Il2CppClass *c = [IL2CPP cls:@"Player" inImage:img ns:@"COW.GamePlay"];
        if (c) { if (imageName) *imageName = img; return c; }
    }
    for (NSString *img in images) {
        Il2CppClass *c = [IL2CPP cls:@"Player" inImage:img ns:@"COW."];
        if (c) { if (imageName) *imageName = img; return c; }
    }
    return NULL;
}

// ---------------------------------------------------------------- container
typedef struct {
    BOOL found;
    Il2CppClass *cls;
    NSString *className;
    NSString *namespace;
    NSString *fieldName;
    BOOL isArray;
    NSUInteger fieldCount;
} ContainerHit;

+ (NSDictionary *)autoMap { return [self autoMapWithLog:nil]; }

+ (NSDictionary *)autoMapWithLog:(void (^)(NSString *))log {
    void (^say)(NSString *, ...) = ^(NSString *fmt, ...) {
        if (!log) return;
        va_list a; va_start(a, fmt);
        NSString *s = [[NSString alloc] initWithFormat:fmt arguments:a];
        va_end(a);
        log(s);
    };

    if (![IL2CPP load]) { say(@"il2cpp not ready"); return nil; }

    Il2CppApi *api = [IL2CPP api];
    NSMutableDictionary *map = [NSMutableDictionary dictionary];
    map[@"auto"] = @YES;

    // ---- 1. the player class
    NSString *image = nil;
    Il2CppClass *player = findPlayerClass(&image);
    if (!player) { say(@"player class not found - not in a match?"); return nil; }
    NSString *playerFull = [IL2CPP fullNameOf:player];
    if (image) map[@"image"] = image;
    say(@"player class: %@  (image: %@)", playerFull, image ?: @"?");

    NSArray<NSDictionary *> *pf = fieldSnapshot(player);
    say(@"player fields: %lu", (unsigned long)pf.count);
    if (pf.count < 20) { say(@"player class looks empty - aborting"); return nil; }

    NSMutableDictionary *p = [NSMutableDictionary dictionary];
    NSRange dot = [playerFull rangeOfString:@"." options:NSBackwardsSearch];
    p[@"class"] = dot.location == NSNotFound ? playerFull : [playerFull substringFromIndex:dot.location + 1];
    p[@"namespace"] = dot.location == NSNotFound ? @"" : [playerFull substringToIndex:dot.location];

    BOOL (^isString)(NSString *) = ^BOOL(NSString *t) { return [t isEqualToString:@"System.String"]; };
    BOOL (^isInt)(NSString *) = ^BOOL(NSString *t) {
        return [t isEqualToString:@"System.Int32"] || [t isEqualToString:@"System.UInt32"] ||
               [t isEqualToString:@"System.Int16"] || [t isEqualToString:@"System.Byte"];
    };
    BOOL (^isNum)(NSString *) = ^BOOL(NSString *t) {
        return [t isEqualToString:@"System.Single"] || [t isEqualToString:@"System.Double"] ||
               [t isEqualToString:@"System.Int32"] || [t isEqualToString:@"System.UInt32"];
    };
    BOOL (^isBool)(NSString *) = ^BOOL(NSString *t) { return [t isEqualToString:@"System.Boolean"]; };
    BOOL (^isVec3)(NSString *) = ^BOOL(NSString *t) { return [t isEqualToString:@"UnityEngine.Vector3"]; };

    // ---- 2. identity / stats, by type + whichever names survived
    NSDictionary *fName = pick(pf, NO, isString, rankFrom(@[
        @"=OriginalNickName", @"=NickName", @"nick", @"playerName", @"=name", @"Name"]));
    if (fName) p[@"name_field"] = fName[@"name"];

    NSDictionary *fHp = pick(pf, NO, isNum, rankFrom(@[
        @"=m_Hp", @"=Hp", @"=hp", @"=m_HP", @"=HP", @"Health", @"=m_Health", @"Life"]));
    if (fHp) p[@"hp_field"] = fHp[@"name"];

    NSDictionary *fMax = pick(pf, NO, isNum, rankFrom(@[
        @"MaxHp", @"MaxHP", @"HpMax", @"=m_MaxHp", @"MaxHealth"]));
    if (fMax) p[@"max_hp_field"] = fMax[@"name"];

    NSDictionary *fTeam = pick(pf, NO, isInt, rankFrom(@[
        @"=TeamModeID", @"=TeamID", @"^Team", @"team"]));
    if (fTeam) p[@"team_field"] = fTeam[@"name"];

    NSDictionary *fLocal = pick(pf, NO, isBool, rankFrom(@[
        @"=IsLocalPlayer", @"=IsLocal", @"=LocalPlayer", @"^IsLocal", @"=IsMe"]));
    if (fLocal) p[@"is_local_field"] = fLocal[@"name"];

    // Only the local player owns the main camera transform - a strong signal.
    // Field types cannot be resolved offline on this build (REPORT section 5),
    // so the name is a CANDIDATE, not an answer: emit every field whose type
    // name mentions Transform, best name first, and let Game.m confirm the
    // choice by using it.
    NSMutableArray<NSString *> *camCands = [NSMutableArray array];
    for (NSDictionary *f in pf) {
        if ([f[@"static"] boolValue]) continue;
        if ([f[@"type"] rangeOfString:@"Transform"].location == NSNotFound) continue;
        [camCands addObject:f[@"name"]];
    }
    {
        NSArray *pref = @[@"=MainCameraTransform", @"CameraTransform", @"^m_Camera", @"Camera"];
        [camCands sortUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
            int ra = 99, rb = 99;
            for (NSUInteger i = 0; i < pref.count; i++) {
                if (nameHit(a, pref[i]) && ra == 99) ra = (int)i;
                if (nameHit(b, pref[i]) && rb == 99) rb = (int)i;
            }
            return ra < rb ? NSOrderedAscending : (ra > rb ? NSOrderedDescending : NSOrderedSame);
        }];
    }
    if (camCands.count) {
        p[@"camera_transform_candidates"] = camCands;
        p[@"camera_transform_field"] = camCands.firstObject;
        say(@"camera transform candidates: %lu (first: %@)",
            (unsigned long)camCands.count, camCands.firstObject);
    } else {
        say(@"no Transform-typed field on the player - aim and the matrix "
            @"world-to-screen will have nothing to read");
    }

    NSDictionary *fBot = pick(pf, NO, isBool, rankFrom(@[@"=IsClientBot", @"IsBot"]));
    if (fBot) p[@"bot_field"] = fBot[@"name"];

    // ---- 3. position candidates (calibrated by Game.m, not guessed here)
    NSMutableArray *vec3 = [NSMutableArray array];
    for (NSDictionary *f in pf) {
        if ([f[@"static"] boolValue]) continue;
        if (!isVec3(f[@"type"])) continue;
        [vec3 addObject:f[@"name"]];
    }
    NSArray *pref = @[@"position", @"Position", @"^m_Pos", @"^Pos", @"Location", @"^m_Location"];
    [vec3 sortUsingComparator:^NSComparisonResult(NSString *a, NSString *b) {
        int ra = 99, rb = 99;
        for (NSUInteger i = 0; i < pref.count; i++) {
            if (nameHit(a, pref[i]) && ra == 99) ra = (int)i;
            if (nameHit(b, pref[i]) && rb == 99) rb = (int)i;
        }
        return ra < rb ? NSOrderedAscending : (ra > rb ? NSOrderedDescending : NSOrderedSame);
    }];
    if (vec3.count) {
        p[@"position_candidates"] = vec3;
        p[@"position_field"] = vec3.firstObject;
        say(@"position candidates: %lu (first: %@)",
            (unsigned long)vec3.count, vec3.firstObject);
    } else {
        say(@"no Vector3 on the player class - position must come from Transform");
    }

    // ---- 4. the collection that holds the players
    ContainerHit best = {0};
    NSUInteger scanned = 0, hits = 0;
    NSArray<NSString *> *images = [IL2CPP imageNames];

    for (NSString *imgName in images) {
        const Il2CppImage *img = [IL2CPP image:imgName];
        if (!img || !api->image_get_class_count || !api->image_get_class) continue;
        size_t count = api->image_get_class_count(img);
        for (size_t i = 0; i < count; i++) {
            Il2CppClass *c = api->image_get_class(img, i);
            if (!c || !api->class_get_fields) continue;
            const char *nsC = api->class_get_namespace(c);
            NSString *ns = nsC ? @(nsC) : @"";
            if (skipNamespace(ns)) continue;
            uint32_t nf = api->class_num_fields ? api->class_num_fields(c) : 0;
            if (nf == 0 || nf > 400) continue;
            scanned++;

            void *it = NULL;
            const FieldInfo *fi;
            while ((fi = api->class_get_fields(c, &it))) {
                FieldInfo *f = (FieldInfo *)fi;
                if (FFFieldIsLiteral(f) || FFFieldIsStatic(f)) continue;
                NSString *t = FFFieldTypeName(f);
                NSString *elem = elementOf(t);
                if (!elem || ![elem isEqualToString:playerFull]) continue;
                BOOL isArr = [t hasSuffix:@"[]"];
                BOOL isList = !isArr && isListType(t);
                if (!isArr && !isList) continue;      // HashSet / Dictionary / Queue

                hits++;
                BOOL better = NO;
                if (!best.found) {
                    better = YES;
                } else {
                    int curRank = best.isArray ? 1 : 0;
                    int newRank = isArr ? 1 : 0;
                    if (newRank < curRank) better = YES;
                    else if (newRank == curRank && nf < best.fieldCount) better = YES;
                }
                if (!better) continue;

                const char *cn = api->class_get_name(c);
                const char *fn = api->field_get_name(f);
                best.found = YES;
                best.cls = c;
                best.className = cn ? @(cn) : @"";
                best.namespace = ns;
                best.fieldName = fn ? @(fn) : @"";
                best.isArray = isArr;
                best.fieldCount = nf;
                if (isList && ns.length) break;
            }
        }
    }
    say(@"classes scanned: %lu, collection hits: %lu", (unsigned long)scanned, (unsigned long)hits);

    if (best.found) {
        p[@"container_class"] = best.className;
        p[@"container_namespace"] = best.namespace;
        p[@"list_field"] = best.fieldName;
        p[@"list_kind"] = best.isArray ? @"array" : @"list";
        say(@"container: %@.%@ -> %@ (%@, %lu fields)",
            best.namespace, best.className, best.fieldName,
            best.isArray ? @"array" : @"list", (unsigned long)best.fieldCount);

        // ---- 5. the singleton that hands out that collection
        NSString *containerFull = best.namespace.length
            ? [NSString stringWithFormat:@"%@.%@", best.namespace, best.className]
            : best.className;
        NSString *owner = nil, *staticName = nil;
        Il2CppClass *ownerCls = NULL;

        for (NSDictionary *f in fieldSnapshot(best.cls)) {
            if (![f[@"static"] boolValue]) continue;
            NSString *t = f[@"type"];
            if ([t isEqualToString:containerFull] || [t hasSuffix:[@"." stringByAppendingString:best.className]]) {
                owner = best.className; staticName = f[@"name"]; ownerCls = best.cls;
                break;
            }
        }

        if (!staticName) {
            for (NSString *imgName in images) {
                const Il2CppImage *img = [IL2CPP image:imgName];
                if (!img || !api->image_get_class_count || !api->image_get_class) continue;
                size_t count = api->image_get_class_count(img);
                for (size_t i = 0; i < count && !staticName; i++) {
                    Il2CppClass *c = api->image_get_class(img, i);
                    if (!c || !api->class_get_fields) continue;
                    const char *nsC = api->class_get_namespace(c);
                    NSString *ns = nsC ? @(nsC) : @"";
                    if (skipNamespace(ns)) continue;
                    uint32_t nf = api->class_num_fields ? api->class_num_fields(c) : 0;
                    if (nf == 0 || nf > 400) continue;
                    void *it = NULL;
                    const FieldInfo *fi;
                    while ((fi = api->class_get_fields(c, &it))) {
                        FieldInfo *f = (FieldInfo *)fi;
                        if (!FFFieldIsStatic(f) || FFFieldIsLiteral(f)) continue;
                        if (![FFFieldTypeName(f) isEqualToString:containerFull]) continue;
                        const char *cn = api->class_get_name(c);
                        const char *fn = api->field_get_name(f);
                        owner = cn ? @(cn) : @"";
                        staticName = fn ? @(fn) : @"";
                        ownerCls = c;
                        break;
                    }
                    if (staticName) break;
                }
                if (staticName) break;
            }
        }

        if (staticName) {
            p[@"container_static"] = staticName;
            p[@"container_static_owner"] = owner;
            say(@"singleton: %@.%@", owner, staticName);
            if (ownerCls && api->runtime_class_init) api->runtime_class_init(ownerCls);
        } else {
            say(@"no static field holds the collection - will scan for the instance");
        }
        if (api->runtime_class_init) api->runtime_class_init(best.cls);
    } else {
        say(@"player collection not found");
    }

    map[@"player"] = p;

    // ---- 6. camera + transform icalls.
    // Candidates only: which spelling exists depends on the Unity version, so
    // Game.m probes them and reports the winner. They are grouped by CALLING
    // CONVENTION, because that is not a detail you can hand-wave:
    //
    //   *_Injected   value types are returned through an out pointer:
    //                void f(void *self, T *in, T *out)
    //   value-returning  the engine returns the struct in registers:
    //                T f(void *self, T in)
    //
    // Calling one with the other's prototype does not fail loudly, it returns
    // garbage - so they can never share a candidate list.
    map[@"camera"] = @{
        @"class": @"Camera",
        @"namespace": @"UnityEngine",
        @"icall_get_main": @"UnityEngine.Camera::get_main",
        @"icall_get_main_candidates": @[
            @"UnityEngine.Camera::get_main",
            @"UnityEngine.Camera::get_current",
        ],
        @"icall_w2s_injected": @"UnityEngine.Camera::WorldToScreenPoint_Injected",
        @"icall_w2s_injected_candidates": @[
            @"UnityEngine.Camera::WorldToScreenPoint_Injected",
        ],
        @"icall_w2s_value": @"UnityEngine.Camera::WorldToScreenPoint",
        @"icall_w2s_value_candidates": @[
            @"UnityEngine.Camera::WorldToScreenPoint",
        ],
        @"icall_fov": @"UnityEngine.Camera::get_fieldOfView",
        @"icall_fov_candidates": @[
            @"UnityEngine.Camera::get_fieldOfView",
        ],
    };
    map[@"transform"] = @{
        @"icall_position_injected": @"UnityEngine.Transform::get_position_Injected",
        @"icall_position_candidates": @[
            @"UnityEngine.Transform::get_position_Injected",
        ],
        @"icall_rotation_injected": @"UnityEngine.Transform::get_rotation_Injected",
        @"icall_rotation_candidates": @[
            @"UnityEngine.Transform::get_rotation_Injected",
        ],
        @"icall_euler_injected": @"UnityEngine.Transform::get_eulerAngles_Injected",
        @"icall_euler_candidates": @[
            @"UnityEngine.Transform::get_eulerAngles_Injected",
        ],
        @"icall_matrix_injected": @"UnityEngine.Transform::get_localToWorldMatrix_Injected",
        @"icall_matrix_candidates": @[
            @"UnityEngine.Transform::get_localToWorldMatrix_Injected",
        ],
    };

    // Aim actuation defaults to the input layer: byte-identical to a finger,
    // so no server-side movement check can distinguish it.
    map[@"aim"] = @{ @"apply": @"touch" };

    // ---- 7. measured, not assumed
    NSInteger lenOff = 0, dataOff = 0;
    measureStringLayout(&lenOff, &dataOff);
    if (dataOff) {
        p[@"string_len_offset"] = @(lenOff);
        p[@"string_data_offset"] = @(dataOff);
        say(@"string layout measured: length@+%ld chars@+%ld", (long)lenOff, (long)dataOff);
    } else {
        say(@"string layout not measured - will use il2cpp_string_chars");
    }
    if (api->offset_of_array_length) {
        int32_t lo = api->offset_of_array_length();
        int32_t hdr = api->array_object_header_size ? api->array_object_header_size() : 0;
        p[@"array_len_offset"] = @(lo);
        p[@"array_data_offset"] = @(hdr);
        say(@"array layout from runtime: length@+%d data@+%d", lo, hdr);
    }

    say(@"map resolved");
    return map;
}

+ (NSString *)describe:(NSDictionary *)map {
    NSDictionary *p = map[@"player"] ?: @{};
    return [NSString stringWithFormat:
            @"%@.%@ | %@.%@ -> %@ (%@) | static %@.%@ | pos %@ | hp %@ | name %@",
            p[@"namespace"] ?: @"?", p[@"class"] ?: @"?",
            p[@"container_namespace"] ?: @"?", p[@"container_class"] ?: @"?",
            p[@"list_field"] ?: @"-", p[@"list_kind"] ?: @"?",
            p[@"container_static_owner"] ?: @"-", p[@"container_static"] ?: @"-",
            p[@"position_field"] ?: @"-",
            p[@"hp_field"] ?: @"-",
            p[@"name_field"] ?: @"-"];
}

@end