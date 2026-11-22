// ff_scan.h - finding the live Il2CppClass for a metadata type.
//
// The obvious way is il2cpp_class_from_name. It is unreachable: the name is in
// .dynstr but no dynamic symbol points at it (verified - index 9,360 has zero
// referencing symbols), so there is nothing to resolve by name and nothing to
// dlsym.
//
// What is left is the reason IL2CPP is easy anyway: Il2CppClass.name does not
// hold a copy of the name, it holds the address of the name inside the mapped
// metadata. That address is a known number. So the class is found by searching
// writable memory for it, and then checking that the struct around the hit
// really is a class - because a pointer to that address could also be a
// coincidence, a cache entry, or a debugger's table.
//
// The check is the namespace pointer. If the candidate is really an Il2CppClass,
// the field beside its name points at the namespace string, and that is a second
// independent match on a second address. Two addresses agreeing by accident is
// not a thing that happens.
#pragma once

#include <cstdint>
#include <vector>

#include "ff_mem.h"
#include "ff_meta.h"

namespace ff {

// Offsets inside Il2CppClass that are tried, in order. The layout moves between
// IL2CPP versions and one build may pad differently, so the offset is not
// assumed - it is confirmed by the namespace check below.
constexpr int32_t kKlassNameOffsets[] = {0x10, 0x18, 0x08, 0x20};
constexpr int32_t kKlassNameToNamespace = 0x08;

struct ClassHit {
    uintptr_t klass = 0;          // Il2CppClass*
    uintptr_t name_addr = 0;      // the pointer that matched
    int32_t name_offset = 0;      // which candidate offset was correct
    uintptr_t image = 0;          // whatever sits before the name
};

class ClassFinder {
public:
    ClassFinder(const Memory& mem, const Meta& meta);

    // Locates the class, or returns false. Expensive: the first call walks every
    // writable mapping in the process, so it happens once and the result is kept.
    bool find(const TypeInfo& type, ClassHit& out) const;

    // Every candidate that passed validation, for when the first one behaves
    // oddly and the second is the real class.
    void find_all(const TypeInfo& type, std::vector<ClassHit>& out) const;

    // How many writable bytes the last search covered. Reported so a search that
    // finds nothing can be told apart from a search that never ran.
    size_t last_scanned_bytes() const { return scanned_; }

private:
    bool validate(const TypeInfo& type, uintptr_t candidate, int32_t name_offset,
                  ClassHit& out) const;

    const Memory& mem_;
    const Meta& meta_;
    mutable size_t scanned_ = 0;
};

}  // namespace ff
