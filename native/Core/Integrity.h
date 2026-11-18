// Integrity.h - what the client says about itself, and what a checker sees.
//
// The ban verdict described in ANTIDETECT 0.1 is a client-integrity verdict: it
// does not need a feature to fire, because it inspects the app that is running.
// Half of those signals are decided at signing time and cannot be touched from
// inside the process (the signature, the provisioning profile's contents, the
// Team ID in the code directory). The other half are *API answers* - and those
// can be answered truthfully for everything, and carefully for the few paths
// and keys that only exist because the app was re-signed.
//
// This layer is deliberately narrow. It hides exactly the artefacts our own
// install introduced, and it changes nothing else: no jailbreak hiding, no
// Frida hiding, no syscall interception. A checker that asks "is there an extra
// framework" gets "no"; a checker that asks "does this app have a file it does
// not expect" gets "no". Everything that is genuinely true stays true.

#import <Foundation/Foundation.h>

@interface USIntegrity : NSObject

/// Install the hooks. Idempotent; safe to call before UIApplication exists.
+ (void)install;

/// What a checker sees NOW (after the hooks). One line per signal.
+ (NSString *)report;

/// What the kernel/filesystem actually says (bypasses our own hooks).
/// This is the number that matters: it is the gap between the two that a
/// client-integrity check is looking for.
+ (NSString *)rawReport;

/// How many artefacts are still visible without our hooks. 0 is impossible for
/// a re-signed build; the point is to know the number instead of guessing.
+ (NSInteger)exposureCount;

@end
