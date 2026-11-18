// Secrets.m - the encoded blobs behind Secrets.h.
//
// XOR 0x5B, lowercase, decoded once on first use. The point is not secrecy -
// anybody who wants the strings can call the accessor. The point is that a
// `strings` pass over the built dylib finds no such word, and that is the check
// that actually happens.
//
// Lowercase because every match in Integrity.m is case-insensitive: a checker
// is free to spell the path however it likes, and strcasestr() folds both
// sides.

#import "Secrets.h"
#import "Obf.h"

typedef struct { const uint8_t *bytes; size_t len; } FFBlob;

// provisioning profile in the .app  (24 bytes)
static const uint8_t kProfile[] = {
    0x3e, 0x36, 0x39, 0x3e, 0x3f, 0x3f, 0x3e, 0x3f, 0x75, 0x36,
    0x34, 0x39, 0x32, 0x37, 0x3e, 0x2b, 0x29, 0x34, 0x2d, 0x32,
    0x28, 0x32, 0x34, 0x35,
};

// the directory our image loads from  (23 bytes)
static const uint8_t kFramework[] = {
    0x2e, 0x35, 0x32, 0x2f, 0x22, 0x28, 0x3e, 0x29, 0x2d, 0x32,
    0x38, 0x3e, 0x28, 0x75, 0x3d, 0x29, 0x3a, 0x36, 0x3e, 0x2c,
    0x34, 0x29, 0x30,
};

// optional map override, with extension  (14 bytes)
static const uint8_t kSchemaFile[] = {
    0x3d, 0x3d, 0x04, 0x35, 0x3a, 0x2f, 0x32, 0x2d, 0x3e, 0x75,
    0x31, 0x28, 0x34, 0x35,
};

// the same, without the extension  (9 bytes)
static const uint8_t kSchemaBase[] = {
    0x3d, 0x3d, 0x04, 0x35, 0x3a, 0x2f, 0x32, 0x2d, 0x3e,
};

// the development-signing entitlement  (14 bytes)
static const uint8_t kEntitlement[] = {
    0x3c, 0x3e, 0x2f, 0x76, 0x2f, 0x3a, 0x28, 0x30, 0x76, 0x3a,
    0x37, 0x37, 0x34, 0x2c,
};

// the App Store identifier  (18 bytes)
static const uint8_t kBundleID[] = {
    0x38, 0x34, 0x36, 0x75, 0x3f, 0x2f, 0x28, 0x75, 0x3d, 0x29,
    0x3e, 0x3e, 0x3d, 0x32, 0x29, 0x3e, 0x2f, 0x33,
};

// SecTaskCopyValueForEntitlement  (30 bytes)
static const uint8_t kEntAPI[] = {
    0x28, 0x3e, 0x38, 0x2f, 0x3a, 0x28, 0x30, 0x38, 0x34, 0x2b,
    0x22, 0x2d, 0x3a, 0x37, 0x2e, 0x3e, 0x3d, 0x34, 0x29, 0x3e,
    0x35, 0x2f, 0x32, 0x2f, 0x37, 0x3e, 0x36, 0x3e, 0x35, 0x2f,
};

// SecTaskCreateFromSelf  (21 bytes)
static const uint8_t kEntCreate[] = {
    0x28, 0x3e, 0x38, 0x2f, 0x3a, 0x28, 0x30, 0x38, 0x29, 0x3e,
    0x3a, 0x2f, 0x3e, 0x3d, 0x29, 0x34, 0x36, 0x28, 0x3e, 0x37,
    0x3d,
};

// SecTaskCopySigningIdentifier  (28 bytes)
static const uint8_t kEntSign[] = {
    0x28, 0x3e, 0x38, 0x2f, 0x3a, 0x28, 0x30, 0x38, 0x34, 0x2b,
    0x22, 0x28, 0x32, 0x3c, 0x35, 0x32, 0x35, 0x3c, 0x32, 0x3f,
    0x3e, 0x35, 0x2f, 0x32, 0x3d, 0x32, 0x3e, 0x29,
};

// Order must match FFSecret in Secrets.h.
static const FFBlob kBlobs[FFSecretCount] = {
    { kProfile,      sizeof(kProfile)      },
    { kFramework,    sizeof(kFramework)    },
    { kSchemaFile,   sizeof(kSchemaFile)   },
    { kSchemaBase,   sizeof(kSchemaBase)   },
    { kEntitlement,  sizeof(kEntitlement)  },
    { kBundleID,     sizeof(kBundleID)     },
    { kEntAPI,       sizeof(kEntAPI)       },
    { kEntCreate,    sizeof(kEntCreate)    },
    { kEntSign,      sizeof(kEntSign)      },
};

// Longest blob is 30 bytes; leave a byte for the terminator and some slack so a
// future entry cannot silently overflow.
#define FF_SECRET_MAX 64

static char gCStr[FFSecretCount][FF_SECRET_MAX];
static NSString *gText[FFSecretCount];
static CFStringRef gCF[FFSecretCount];

static void FFSecretInit(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        for (NSInteger i = 0; i < FFSecretCount; i++) {
            FFDecode(kBlobs[i].bytes, kBlobs[i].len, gCStr[i]);
            gText[i] = @(gCStr[i]);
            gCF[i] = CFStringCreateWithCString(kCFAllocatorDefault, gCStr[i],
                                               kCFStringEncodingUTF8);
        }
    });
}

const char *FFSecretNeedle(FFSecret s) {
    if (s < 0 || s >= FFSecretCount) return "";
    FFSecretInit();
    return gCStr[s];
}

NSString *FFSecretText(FFSecret s) {
    if (s < 0 || s >= FFSecretCount) return @"";
    FFSecretInit();
    return gText[s];
}

CFStringRef FFSecretCF(FFSecret s) {
    if (s < 0 || s >= FFSecretCount) return NULL;
    FFSecretInit();
    return gCF[s];
}
