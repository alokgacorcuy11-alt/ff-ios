#include "ff_meta.h"

#include <cstring>

namespace ff {

namespace {

// The metadata table length in the header is a byte count, not an entry count.
// Everything is derived from it so a regenerated Layout stays consistent.
inline uintptr_t at(uintptr_t base, int32_t offset) {
    return base + static_cast<uintptr_t>(static_cast<uint32_t>(offset));
}

}  // namespace

uintptr_t Meta::find_metadata_base(const Memory& mem,
                                   uintptr_t& scan_begin, uintptr_t& scan_end) {
    // The file starts with the sanity value then the version, both little
    // endian. Searching for that pair finds the mapped copy without needing
    // the game to hand it over.
    const uint8_t needle[8] = {
            static_cast<uint8_t>(kMetadataSanity & 0xff),
            static_cast<uint8_t>((kMetadataSanity >> 8) & 0xff),
            static_cast<uint8_t>((kMetadataSanity >> 16) & 0xff),
            static_cast<uint8_t>((kMetadataSanity >> 24) & 0xff),
            static_cast<uint8_t>(kMetadataVersion & 0xff), 0, 0, 0,
    };
    for (const Range& r : mem.ranges()) {
        // The metadata is mapped read-only and is tens of megabytes. Ranges
        // smaller than a megabyte cannot be it, and skipping them turns a scan
        // of every mapping in the process into a scan of a handful.
        if (r.writable || r.end - r.begin < (1u << 20)) {
            continue;
        }
        const uintptr_t hit = find_pattern(mem, r.begin, r.end, needle, sizeof(needle));
        if (hit != 0) {
            scan_begin = r.begin;
            scan_end = r.end;
            return hit;
        }
    }
    return 0;
}

Meta::Meta(const Memory& mem, const Layout& layout, uintptr_t metadata_base,
           uintptr_t il2cpp_base, uintptr_t offsets_array_override)
        : mem_(mem), layout_(layout), base_(metadata_base), type_count_(0),
          offsets_array_(offsets_array_override) {
    if (base_ == 0) {
        return;
    }
    type_count_ = layout_.type_capacity / layout_.type_stride;

    // The field-offset array is the one piece that lives in the binary rather
    // than the metadata. Reading it through MetadataRegistration rather than
    // hardcoding the array address means a game patch that moves one and not
    // the other still works.
    //
    // The override exists for tests: fabricating a 175 MB image just to put a
    // registration struct at RVA 0xa5c7f60 would make the test untestable.
    if (offsets_array_ == 0 && il2cpp_base != 0) {
        uintptr_t offsets_ptr = 0;
        if (mem_.get_ptr(at(il2cpp_base, static_cast<int32_t>(kMetadataRegistrationRVA))
                         + kMetadataRegistrationFieldOffsets, offsets_ptr)) {
            if (mem_.contains(offsets_ptr, sizeof(uintptr_t) * 4)) {
                offsets_array_ = offsets_ptr;
            }
        }
    }
}

std::string Meta::string_at(int32_t index) const {
    if (index < 0 || index >= layout_.string_length) {
        return std::string();
    }
    char buf[256];
    if (!mem_.get_cstr(at(base_, layout_.string_offset + index), buf, sizeof(buf))) {
        return std::string();
    }
    return std::string(buf);
}

bool Meta::type_info(int32_t index, TypeInfo& out) const {
    if (index < 0 || index >= type_count_) {
        return false;
    }
    const uintptr_t t = at(base_, layout_.type_offset + index * layout_.type_stride);
    int32_t name_i = 0;
    int32_t ns_i = 0;
    int32_t fstart = 0;
    int32_t mstart = 0;
    uint16_t fcount = 0;
    uint16_t mcount = 0;
    if (!mem_.get_i32(t, name_i) || !mem_.get_i32(t + 4, ns_i)
            || !mem_.get_i32(t + layout_.type_field_start, fstart)
            || !mem_.get_i32(t + layout_.type_method_start, mstart)
            || !mem_.get_u16(t + layout_.type_field_count, fcount)
            || !mem_.get_u16(t + layout_.type_method_count, mcount)) {
        return false;
    }
    out.index = index;
    out.name_index = name_i;
    out.name = string_at(name_i);
    out.namespaze = string_at(ns_i);
    out.field_start = fstart;
    out.field_count = fcount;
    out.method_start = mstart;
    out.method_count = mcount;
    return !out.name.empty();
}

int32_t Meta::find_type(const std::string& name, const std::string& namespaze) const {
    for (int32_t i = 0; i < type_count_; ++i) {
        TypeInfo t;
        if (!type_info(i, t)) {
            continue;
        }
        if (t.name == name && t.namespaze == namespaze) {
            return i;
        }
    }
    return -1;
}

int32_t Meta::find_type_any(const std::string& full_name) const {
    const size_t dot = full_name.rfind('.');
    if (dot == std::string::npos) {
        return find_type(full_name, std::string());
    }
    return find_type(full_name.substr(dot + 1), full_name.substr(0, dot));
}

int32_t Meta::field_offset(const TypeInfo& type, int32_t field_index) const {
    if (offsets_array_ == 0 || field_index < 0 || field_index >= type.field_count) {
        return -1;
    }
    uintptr_t per_type = 0;
    if (!mem_.get_ptr(offsets_array_ + sizeof(uintptr_t) * type.index, per_type)) {
        return -1;
    }
    if (per_type == 0) {
        return -1;
    }
    int32_t value = -1;
    if (!mem_.get_i32(per_type + sizeof(uint32_t) * field_index, value)) {
        return -1;
    }
    return value;
}

bool Meta::field_name(const TypeInfo& type, int32_t field_index, std::string& out) const {
    if (field_index < 0 || field_index >= type.field_count) {
        return false;
    }
    const uintptr_t f = at(base_, layout_.field_offset
                                   + (type.field_start + field_index) * layout_.field_stride);
    int32_t name_i = 0;
    if (!mem_.get_i32(f, name_i)) {
        return false;
    }
    out = string_at(name_i);
    return !out.empty();
}

int32_t Meta::field_index_by_name(const TypeInfo& type, const std::string& name) const {
    for (int32_t i = 0; i < type.field_count; ++i) {
        std::string candidate;
        if (field_name(type, i, candidate) && candidate == name) {
            return i;
        }
    }
    return -1;
}

bool Meta::method_name(const TypeInfo& type, int32_t index, std::string& out) const {
    if (index < 0 || index >= type.method_count) {
        return false;
    }
    const uintptr_t m = at(base_, layout_.method_offset
                                   + (type.method_start + index) * layout_.method_stride);
    int32_t name_i = 0;
    if (!mem_.get_i32(m, name_i)) {
        return false;
    }
    out = string_at(name_i);
    return !out.empty();
}

}  // namespace ff
