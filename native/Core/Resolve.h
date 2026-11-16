// Resolve.h - build the schema on the device, at runtime.
//
// The proxy can serve a hand-tuned ff_native.json, but it no longer has to.
// Because UnityFramework 1.132.1 exports all 256 il2cpp_* symbols, the dylib
// can walk the live object model itself and work out:
//
//   * which class is the player entity        (COW.GamePlay.Player)
//   * which of its 1689 fields is the nickname, the health, the team, the
//     position vector, the camera transform   (by field TYPE + the ~40% of
//     names Garena leaves readable, never by guessing an offset)
//   * which class holds the player collection (by looking for a field whose
//     TYPE is List<Player> / Player[])
//   * which static field hands you that collection (the singleton)
//
// Field TYPES are never obfuscated. That is the whole trick: after a patch
// re-randomises every member name, the shapes are identical.

#import <Foundation/Foundation.h>
#import "IL2CPP.h"

@interface USMap : NSObject

/// Walk the runtime and return a schema in the same shape as ff_native.json.
/// Returns nil if the game is not loaded yet.
+ (NSDictionary *)autoMap;

/// Same, but reports progress. `log` is called on the resolving thread.
+ (NSDictionary *)autoMapWithLog:(void (^)(NSString *))log;

/// Human-readable summary of a map produced by autoMap.
+ (NSString *)describe:(NSDictionary *)map;

@end
