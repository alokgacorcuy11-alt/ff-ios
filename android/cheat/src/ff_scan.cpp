#include "ff_scan.h"

#include <cstring>

namespace ff {

namespace {

// Writable mappings worth searching. Skipping the small ones and the enormous
// ones keeps a search of a multi-gigabyte process down to the handful of
// regions where objects actually live.
bool worth_scanning(const Range& r) {
    const size_t size = r.end - r.begin;
    return r.writable && size >= 4096 && size <= (size_t)1024 * 1024 * 1024;
}

}  // namespace

ClassFinder::ClassFinder(const Memory& mem, const Meta& meta) : mem_(mem), meta_(meta) {}

bool ClassFinder::validate(const TypeInfo& type, uintptr_t candidate,
                           int32_t name_offset, ClassHit& out) const {
    if (candidate == 0 || (candidate & 0x7) != 0) {
        return false;
    }
    if (!mem_.contains(candidate, 0x100)) {
        return false;
    }

    // The name field must hold the address we searched for.
    uintptr_t name = 0;
    if (!mem_.get_ptr(candidate + name_offset, name)) {
        return false;
    }
    const uintptr_t expected = meta_.string_address(type.name_index);
    if (name != expected) {
        return false;
    }

    // And the field beside it must hold the namespace. This is the check that
    // separates a real class from a pointer that happens to equal the address.
    uintptr_t ns_ptr = 0;
    if (mem_.get_ptr(candidate + name_offset + kKlassNameToNamespace, ns_ptr)) {
        char buf[128] = {0};
        if (mem_.get_cstr(ns_ptr, buf, sizeof(buf))) {
            if (!type.namespaze.empty() && type.namespaze != buf) {
                return false;
            }
        } else if (!type.namespaze.empty()) {
            // No readable namespace where the namespace belongs. A real class
            // always has one, even if it is the empty string.
            return false;
        }
    } else {
        return false;
    }

    uintptr_t image = 0;
    if (name_offset >= 0x10) {
        mem_.get_ptr(candidate + name_offset - 0x10, image);
    }

    out.klass = candidate;
    out.name_addr = name;
    out.name_offset = name_offset;
    out.image = image;
    return true;
}

void ClassFinder::find_all(const TypeInfo& type, std::vector<ClassHit>& out) const {
    out.clear();
    scanned_ = 0;
    if (type.name_index <= 0) {
        return;
    }
    const uintptr_t needle = meta_.string_address(type.name_index);

    // Search for the pointer value, not for a struct. Any object holding a
    // reference to this name is a hit, and validation filters them.
    //
    // The widen is not cosmetic: uintptr_t is 32 bits on armeabi-v7a, and
    // shifting it by 32 is undefined. Building the pattern from a known 64-bit
    // value makes the upper bytes zero on 32-bit instead of whatever the
    // compiler produced, which is what a 32-bit address looks like anyway.
    const uint64_t wide = static_cast<uint64_t>(needle);
    const uint8_t bytes[8] = {
            static_cast<uint8_t>(wide & 0xff),
            static_cast<uint8_t>((wide >> 8) & 0xff),
            static_cast<uint8_t>((wide >> 16) & 0xff),
            static_cast<uint8_t>((wide >> 24) & 0xff),
            static_cast<uint8_t>((wide >> 32) & 0xff),
            static_cast<uint8_t>((wide >> 40) & 0xff),
            static_cast<uint8_t>((wide >> 48) & 0xff),
            static_cast<uint8_t>((wide >> 56) & 0xff),
    };

    std::vector<uint8_t> block;
    for (const Range& r : mem_.ranges()) {
        if (!worth_scanning(r)) {
            continue;
        }
        const size_t kChunk = 1 << 18;
        block.resize(kChunk + sizeof(bytes));
        for (uintptr_t at = r.begin; at + sizeof(bytes) < r.end; at += kChunk) {
            size_t want = static_cast<size_t>(r.end - at);
            if (want > block.size()) {
                want = block.size();
            }
            if (!mem_.read(at, block.data(), want)) {
                continue;
            }
            scanned_ += want;
            for (size_t i = 0; i + sizeof(bytes) <= want; i += 8) {
                if (std::memcmp(block.data() + i, bytes, sizeof(bytes)) != 0) {
                    continue;
                }
                const uintptr_t where = at + i;
                for (int32_t off : kKlassNameOffsets) {
                    if (where < static_cast<uintptr_t>(off)) {
                        continue;
                    }
                    ClassHit hit;
                    if (validate(type, where - off, off, hit)) {
                        out.push_back(hit);
                        break;
                    }
                }
            }
        }
    }
}

bool ClassFinder::find(const TypeInfo& type, ClassHit& out) const {
    std::vector<ClassHit> all;
    find_all(type, all);
    if (all.empty()) {
        return false;
    }
    out = all.front();
    return true;
}

}  // namespace ff
