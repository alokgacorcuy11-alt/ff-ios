// IL2CPP.h - Unity IL2CPP runtime bridge.
//
// Every symbol below is a real export of Unity's il2cpp runtime. Verified on
// this exact build (UnityFramework 1.132.1) with tools/il2cpp_symbols.py:
//
//   256 / 256 il2cpp_* symbols present, all defined in __TEXT,__text,
//   all external (exported), so Core/Dyld.h finds every one of them through
//   FFDlsym (which tries both the bare and the underscore-prefixed name).
//
// That is why nothing here is hardcoded: class names, field names, field
// offsets and field *types* are all read from the live runtime on the device.
// A game patch that re-randomises every obfuscated member name changes none of
// this, because we never look at the obfuscated names in the first place - we
// look at field types and at the ~40% of names Garena leaves readable.

#import <Foundation/Foundation.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct Il2CppDomain    Il2CppDomain;
typedef struct Il2CppAssembly  Il2CppAssembly;
typedef struct Il2CppImage     Il2CppImage;
typedef struct Il2CppClass     Il2CppClass;
typedef struct Il2CppObject    Il2CppObject;
typedef struct Il2CppArray     Il2CppArray;
typedef struct Il2CppString    Il2CppString;
typedef struct Il2CppType      Il2CppType;
typedef struct MethodInfo      MethodInfo;
typedef struct FieldInfo       FieldInfo;

typedef struct { float x, y, z; }            Vec3;   // Unity Vector3
typedef struct { float x, y; }               Vec2;   // Unity Vector2
typedef struct { float x, y, z, w; }         Quat;   // Unity Quaternion
typedef struct { float m[16]; }              Mat4;   // Unity Matrix4x4

// ------------------------------------------------------------------ api table
typedef struct {
    Il2CppDomain*      (*domain_get)(void);
    const Il2CppAssembly** (*domain_get_assemblies)(const Il2CppDomain*, size_t*);
    const Il2CppImage* (*assembly_get_image)(const Il2CppAssembly*);

    const char*        (*image_get_name)(const Il2CppImage*);
    size_t             (*image_get_class_count)(const Il2CppImage*);
    Il2CppClass*       (*image_get_class)(const Il2CppImage*, size_t);

    Il2CppClass*       (*class_from_name)(const Il2CppImage*, const char*, const char*);
    const char*        (*class_get_name)(Il2CppClass*);
    const char*        (*class_get_namespace)(Il2CppClass*);
    Il2CppClass*       (*class_get_parent)(Il2CppClass*);
    Il2CppClass*       (*class_get_declaring_type)(Il2CppClass*);
    Il2CppClass*       (*class_from_type)(const Il2CppType*);
    const Il2CppType*  (*class_get_type)(Il2CppClass*);
    BOOL               (*class_is_valuetype)(Il2CppClass*);
    BOOL               (*class_is_enum)(Il2CppClass*);
    Il2CppClass*       (*class_get_element_class)(Il2CppClass*);
    BOOL               (*class_has_parent)(Il2CppClass*, Il2CppClass*);
    uint32_t           (*class_num_fields)(Il2CppClass*);
    const Il2CppClass* (*class_get_nested_types)(Il2CppClass*, void**);

    uint32_t           (*class_get_type_token)(Il2CppClass*);
    Il2CppClass*       (*object_get_class)(Il2CppObject*);
    Il2CppObject*      (*object_new)(Il2CppClass*);
    void*              (*object_unbox)(Il2CppObject*);
    Il2CppObject*      (*value_box)(Il2CppClass*, void*);

    MethodInfo*        (*class_get_method_from_name)(Il2CppClass*, const char*, int);
    const MethodInfo*  (*class_get_methods)(Il2CppClass*, void**);
    const char*        (*method_get_name)(const MethodInfo*);
    uint32_t           (*method_get_param_count)(const MethodInfo*);
    const Il2CppType*  (*method_get_return_type)(const MethodInfo*);
    Il2CppObject*      (*runtime_invoke)(const MethodInfo*, void*, void**, Il2CppObject**);

    FieldInfo*         (*class_get_field_from_name)(Il2CppClass*, const char*);
    const FieldInfo*   (*class_get_fields)(Il2CppClass*, void**);
    const char*        (*field_get_name)(FieldInfo*);
    const Il2CppType*  (*field_get_type)(FieldInfo*);
    size_t             (*field_get_offset)(FieldInfo*);
    uint32_t           (*field_get_flags)(FieldInfo*);
    void               (*field_get_value)(Il2CppObject*, FieldInfo*, void*);
    void               (*field_set_value)(Il2CppObject*, FieldInfo*, void*);
    void               (*field_static_get_value)(FieldInfo*, void*);
    void               (*field_static_set_value)(FieldInfo*, void*);

    const char*        (*type_get_name)(const Il2CppType*);
    int32_t            (*type_get_type)(const Il2CppType*);
    BOOL               (*type_is_static)(const Il2CppType*);

    int32_t            (*array_length)(Il2CppArray*);
    int32_t            (*offset_of_array_length)(void);      // no guesswork
    int32_t            (*array_object_header_size)(void);

    int32_t            (*string_length)(Il2CppString*);
    uint16_t*          (*string_chars)(Il2CppString*);
    Il2CppString*      (*string_new)(const char*);

    void*              (*resolve_icall)(const char*);
    void               (*free)(void*);
    void*              (*thread_attach)(Il2CppDomain*);
    void               (*thread_detach)(void*);
    void               (*runtime_class_init)(Il2CppClass*);
} Il2CppApi;

// Field attribute bits we care about (ECMA-335 FieldAttributes).
enum {
    FFFieldStatic  = 0x0010,
    FFFieldLiteral = 0x0040,
};

@interface IL2CPP : NSObject

+ (BOOL)load;                        // resolve every symbol; YES if usable
+ (Il2CppApi *)api;
+ (NSArray<NSString *> *)missingSymbols;
+ (NSString *)report;                // one line for the diagnostics panel

+ (BOOL)hasImage:(NSString *)name;
+ (const Il2CppImage *)image:(NSString *)name;
+ (NSArray<NSString *> *)imageNames;

+ (Il2CppClass *)cls:(NSString *)name inImage:(NSString *)image ns:(NSString *)ns;
+ (Il2CppClass *)clsAnywhere:(NSString *)name;
+ (Il2CppClass *)clsAnywhere:(NSString *)name ns:(NSString *)ns;
+ (Il2CppClass *)clsByFullName:(NSString *)full;   // "COW.GamePlay.Player"

+ (void)attachThread;
+ (NSString *)classNameOf:(Il2CppObject *)obj;
+ (NSString *)fullNameOf:(Il2CppClass *)cls;

@end

// ---- convenience wrappers. All of them are null-safe.
NSString *FFTypeName(const Il2CppType *t);          // autoreleased copy
NSString *FFFieldTypeName(FieldInfo *f);
BOOL      FFFieldIsStatic(FieldInfo *f);
BOOL      FFFieldIsLiteral(FieldInfo *f);
size_t    FFFieldOffset(FieldInfo *f);
NSArray<NSString *> *FFFieldNames(Il2CppClass *cls);
Il2CppClass *FFClassFromTypeName(NSString *typeName);   // strips <..>, [] suffixes

// Read a reference-typed field straight out of the object image.
static inline Il2CppObject *FFReadObj(Il2CppObject *o, size_t off) {
    if (!o) return NULL;
    return *(Il2CppObject **)((uint8_t *)o + off);
}
static inline int32_t FFReadI32(Il2CppObject *o, size_t off) {
    if (!o) return 0;
    return *(int32_t *)((uint8_t *)o + off);
}
static inline float FFReadF32(Il2CppObject *o, size_t off) {
    if (!o) return 0.0f;
    return *(float *)((uint8_t *)o + off);
}
static inline Vec3 FFReadVec3(Il2CppObject *o, size_t off) {
    Vec3 v = {0, 0, 0};
    if (!o) return v;
    v = *(Vec3 *)((uint8_t *)o + off);
    return v;
}

#ifdef __cplusplus
}
#endif