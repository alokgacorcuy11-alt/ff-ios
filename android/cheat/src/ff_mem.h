// ff_mem.h - reading another process's memory when you are inside it.
//
// The cheat runs inside the game's process, so "reading memory" is a copy from
// a pointer. That is the part that looks trivial and is not: a bad address does
// not return garbage, it takes the game down, and a cheat that crashes the game
// is a cheat that gets the buyer banned and the product reported.
//
// So nothing is read without first checking the address is inside a mapped
// range. The ranges come from /proc/self/maps and are refreshed on demand -
// the game maps and unmaps constantly, and a stale range list is worse than
// none.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace ff {

struct Range {
    uintptr_t begin = 0;
    uintptr_t end = 0;
    bool writable = false;

    bool contains(uintptr_t addr, size_t n) const {
        return addr >= begin && n <= end - addr;
    }
};

void parse_maps(std::vector<Range>& out);

class Memory {
public:
    virtual ~Memory() = default;
    virtual bool read(uintptr_t addr, void* out, size_t n) const = 0;
    virtual const std::vector<Range>& ranges() const = 0;

    bool contains(uintptr_t addr, size_t n) const {
        for (const Range& r : ranges()) {
            if (r.contains(addr, n)) {
                return true;
            }
        }
        return false;
    }

    bool get_ptr(uintptr_t addr, uintptr_t& out) const {
        return read(addr, &out, sizeof(out));
    }
    bool get_i32(uintptr_t addr, int32_t& out) const {
        return read(addr, &out, sizeof(out));
    }
    bool get_u32(uintptr_t addr, uint32_t& out) const {
        return read(addr, &out, sizeof(out));
    }
    bool get_u16(uintptr_t addr, uint16_t& out) const {
        return read(addr, &out, sizeof(out));
    }
    bool get_f32(uintptr_t addr, float& out) const {
        return read(addr, &out, sizeof(out));
    }

    // Copies a NUL-terminated string, always terminated in the buffer.
    bool get_cstr(uintptr_t addr, char* out, size_t cap) const;

    // True when the pointer is inside a mapped range and the bytes there look
    // like a short printable string. Used where a value has to be validated
    // before it is trusted as a name.
    bool looks_like_cstr(uintptr_t addr) const;
};

// The live process. Bound to /proc/self/maps.
class LiveMemory final : public Memory {
public:
    LiveMemory();

    void refresh();

    bool read(uintptr_t addr, void* out, size_t n) const override;
    const std::vector<Range>& ranges() const override { return ranges_; }

private:
    std::vector<Range> ranges_;
};

// A byte image held in memory, so the same code that reads the game can be run
// against a fixture whose correct answer is known in advance.
class BlobMemory final : public Memory {
public:
    BlobMemory(std::vector<uint8_t> bytes, uintptr_t base);

    bool read(uintptr_t addr, void* out, size_t n) const override;
    const std::vector<Range>& ranges() const override { return ranges_; }

    uintptr_t base() const { return base_; }

private:
    std::vector<uint8_t> bytes_;
    std::vector<Range> ranges_;
    uintptr_t base_;
};

// Scans a memory image for a byte pattern, returning the first address.
// Returns 0 when not found.
uintptr_t find_pattern(const Memory& mem, uintptr_t begin, uintptr_t end,
                       const uint8_t* needle, size_t needle_len);

// Files mapped as if they lived at an address of the caller's choosing.
//
// This exists so the decoder can be run against the real global-metadata.dat and
// the real libil2cpp.so without a game, a container or a rooted phone. The two
// files are the whole input the decoder needs, and pretending they sit at a
// fixed address turns them into the same shape it sees at runtime.
//
// The addresses are fiction and that is the point: nothing is allocated for
// them, the pages are mapped once and read through.
class MappedMemory final : public Memory {
public:
    MappedMemory() = default;
    ~MappedMemory() override;

    bool add_file(const char* path, uintptr_t pretend_base);

    bool read(uintptr_t addr, void* out, size_t n) const override;
    const std::vector<Range>& ranges() const override { return ranges_; }

    size_t mapped_bytes() const { return mapped_; }

private:
    struct Region {
        uintptr_t begin;
        uintptr_t end;
        const uint8_t* data;
    };
    std::vector<Region> regions_;
    std::vector<Range> ranges_;
    size_t mapped_ = 0;
};

}  // namespace ff
