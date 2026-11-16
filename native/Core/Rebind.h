// Rebind.h - rebind another image's symbol-pointer slots at runtime.
//
// fishhook, generalised: instead of only rewriting our own __got / __la_symbol_ptr,
// this walks ANY loaded image's Mach-O, finds the indirect symbol table
// (LC_DYSYMTAB) and overwrites the slot that image calls through.
//
// Verified offline against this build with tools/got_scan.py:
//
//   __la_symbol_ptr[1174] -> __dyld_image_count
//   __la_symbol_ptr[1171] -> __dyld_get_image_header
//   __la_symbol_ptr[1172] -> __dyld_get_image_name
//   __la_symbol_ptr[1173] -> __dyld_get_image_vmaddr_slide
//   __la_symbol_ptr[1487] -> _objc_copyClassNamesForImage
//   __la_symbol_ptr[1488] -> _objc_copyImageNames
//
// All six live in __DATA,__la_symbol_ptr, which is writable. No inline hook
// engine, no Dobby, no substrate.

#import <Foundation/Foundation.h>
#include <mach-o/dyld.h>

typedef void *(*FFImp)(void);

@interface USLink : NSObject

/// Rebind one symbol in one image. Returns the previous implementation, or
/// NULL if the symbol is not called through a pointer slot in that image.
+ (FFImp)rebind:(const char *)symbol
        inImage:(const struct mach_header_64 *)header
             to:(FFImp)replacement;

/// Rebind in every loaded image. Returns how many slots were patched.
+ (int)rebindEverywhere:(const char *)symbol to:(FFImp)replacement;

/// Rebind using a list of accepted spellings (IL2CPP builds differ on the
/// leading underscores). Returns the first original found.
+ (FFImp)rebindAny:(NSArray<NSString *> *)symbols
           inImage:(const struct mach_header_64 *)header
                to:(FFImp)replacement;

/// Total slots patched by rebindAny:inImage:to: so far.
+ (int)patchCount;
@end