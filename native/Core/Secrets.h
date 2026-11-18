// Secrets.h - the strings this project must not leave lying in the binary.
//
// Every one of them is a fingerprint. A release dylib that spells out the
// profile filename is a dylib that hides a provisioning profile; one that names
// the development entitlement is one that lies about its own signature; one that
// contains the App Store identifier is pretending to be the App Store build.
// `strings` needs no skill, and a scanner only needs one of them.
//
// So they are stored XOR-encoded (Obf.h) and decoded once at runtime. Nothing
// here is security - it is the removal of a signature that costs nothing to
// remove. The match is case-insensitive, so the stored form is lowercase.
//
// Core/Secrets.m holds the encoded blobs and the decoders.

#import <Foundation/Foundation.h>
#include <CoreFoundation/CoreFoundation.h>

typedef NS_ENUM(NSInteger, FFSecret) {
    FFSecretProfile = 0,   // provisioning profile filename inside the .app
    FFSecretFramework,     // the directory our own image is loaded from
    FFSecretSchemaFile,    // optional map override file, with extension
    FFSecretSchemaBase,    // the same name without the extension
    FFSecretEntitlement,   // the development-signing entitlement key
    FFSecretBundleID,      // the App Store identifier
    FFSecretEntAPI,        // SecTaskCopyValueForEntitlement
    FFSecretEntCreate,     // SecTaskCreateFromSelf
    FFSecretEntSign,       // SecTaskCopySigningIdentifier
    FFSecretCount
};

/// Decoded, NUL-terminated, lowercase. Valid for the life of the process.
const char *FFSecretNeedle(FFSecret s);

/// The same bytes as an NSString (cached; safe to call from any thread).
NSString *FFSecretText(FFSecret s);

/// The same bytes as a CFString (cached; +1 lives forever by design, so the
/// caller must not release it).
CFStringRef FFSecretCF(FFSecret s);
