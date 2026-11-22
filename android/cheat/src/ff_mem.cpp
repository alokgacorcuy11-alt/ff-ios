#include "ff_mem.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace ff {

void parse_maps(std::vector<Range>& out) {
    out.clear();
    FILE* f = std::fopen("/proc/self/maps", "r");
    if (f == nullptr) {
        return;
    }
    char line[512];
    while (std::fgets(line, sizeof(line), f) != nullptr) {
        unsigned long long begin = 0;
        unsigned long long end = 0;
        char perms[8] = {0};
        if (std::sscanf(line, "%llx-%llx %7s", &begin, &end, perms) != 3) {
            continue;
        }
        // The whole line is needed to know the permissions, and the first field
        // of perms is all that matters: r for readable, w for writable.
        Range r;
        r.begin = static_cast<uintptr_t>(begin);
        r.end = static_cast<uintptr_t>(end);
        r.writable = perms[1] == 'w';
        if (r.end > r.begin) {
            out.push_back(r);
        }
    }
    std::fclose(f);
}

bool Memory::get_cstr(uintptr_t addr, char* out, size_t cap) const {
    if (cap == 0) {
        return false;
    }
    out[0] = '\0';
    size_t written = 0;
    while (written + 1 < cap) {
        char c = 0;
        if (!read(addr + written, &c, 1)) {
            break;
        }
        if (c == '\0') {
            out[written] = '\0';
            return written > 0;
        }
        if (static_cast<unsigned char>(c) < 0x20 || static_cast<unsigned char>(c) > 0x7e) {
            break;
        }
        out[written++] = c;
        if (written > 512) {
            break;
        }
    }
    out[written] = '\0';
    return written > 0;
}

bool Memory::looks_like_cstr(uintptr_t addr) const {
    if (addr == 0 || !contains(addr, 1)) {
        return false;
    }
    char buf[64] = {0};
    if (!get_cstr(addr, buf, sizeof(buf))) {
        return false;
    }
    size_t n = std::strlen(buf);
    return n >= 1 && n < sizeof(buf) - 1;
}

LiveMemory::LiveMemory() {
    refresh();
}

void LiveMemory::refresh() {
    parse_maps(ranges_);
}

bool LiveMemory::read(uintptr_t addr, void* out, size_t n) const {
    if (out == nullptr || n == 0) {
        return false;
    }
    if (!contains(addr, n)) {
        return false;
    }
    // Mapped and readable according to maps. A concurrent munmap between the
    // check and the copy is still possible; it is rare enough that guarding it
    // would cost more than it saves, and the caller treats a crash-inducing
    // read as a bug, not a condition.
    std::memcpy(out, reinterpret_cast<const void*>(addr), n);
    return true;
}

BlobMemory::BlobMemory(std::vector<uint8_t> bytes, uintptr_t base) {
    add_segment(std::move(bytes), base);
}

void BlobMemory::add_segment(std::vector<uint8_t> bytes, uintptr_t base) {
    if (segments_.empty()) {
        base_ = base;
    }
    Range r;
    r.begin = base;
    r.end = base + bytes.size();
    r.writable = false;
    ranges_.push_back(r);

    Segment seg;
    seg.bytes = std::move(bytes);
    segments_.push_back(std::move(seg));
}

bool BlobMemory::read(uintptr_t addr, void* out, size_t n) const {
    if (out == nullptr || n == 0) {
        return false;
    }
    for (size_t i = 0; i < segments_.size(); ++i) {
        // One rule, in one place. The first version of this repeated the
        // containment test inline, and the repeat did not get the fix that the
        // shared one did - which is what made a two-region read walk past the
        // end of the first region and die.
        const Range& r = ranges_[i];
        if (!r.contains(addr, n)) {
            continue;
        }
        std::memcpy(out, segments_[i].bytes.data() + (addr - r.begin), n);
        return true;
    }
    return false;
}

uintptr_t find_pattern(const Memory& mem, uintptr_t begin, uintptr_t end,
                       const uint8_t* needle, size_t needle_len) {
    if (needle == nullptr || needle_len == 0 || end <= begin + needle_len) {
        return 0;
    }
    // Step in whole bytes over the range, copying in 4 KB blocks so a search of
    // a 100 MB .so costs seconds rather than minutes.
    const size_t kBlock = 1 << 16;
    std::vector<uint8_t> buf(kBlock + needle_len);
    for (uintptr_t at = begin; at < end; at += kBlock) {
        const size_t want = static_cast<size_t>(
                (end - at) < kBlock + needle_len ? (end - at) : kBlock + needle_len);
        if (!mem.read(at, buf.data(), want)) {
            continue;
        }
        if (want < needle_len) {
            break;
        }
        for (size_t i = 0; i + needle_len <= want; ++i) {
            if (std::memcmp(buf.data() + i, needle, needle_len) == 0) {
                return at + i;
            }
        }
    }
    return 0;
}

MappedMemory::~MappedMemory() {
    for (const Raw& r : raw_) {
        if (r.ptr != nullptr) {
            ::munmap(r.ptr, r.size);
        }
    }
}

bool MappedMemory::map_raw(const char* path, uintptr_t& out_ptr, size_t& out_size) {
    const int fd = ::open(path, O_RDONLY);
    if (fd < 0) {
        return false;
    }
    struct stat st {};
    if (::fstat(fd, &st) != 0 || st.st_size <= 0) {
        ::close(fd);
        return false;
    }
    const size_t size = static_cast<size_t>(st.st_size);
    void* p = ::mmap(nullptr, size, PROT_READ, MAP_PRIVATE | MAP_NORESERVE, fd, 0);
    ::close(fd);
    if (p == MAP_FAILED) {
        return false;
    }
    raw_.push_back(Raw{p, size});
    out_ptr = reinterpret_cast<uintptr_t>(p);
    out_size = size;
    mapped_ += size;
    return true;
}

bool MappedMemory::add_file(const char* path, uintptr_t pretend_base) {
    uintptr_t raw = 0;
    size_t size = 0;
    if (!map_raw(path, raw, size)) {
        return false;
    }
    Region r;
    r.range.begin = pretend_base;
    r.range.end = pretend_base + size;
    r.range.writable = false;
    r.data = reinterpret_cast<const uint8_t*>(raw);
    regions_.push_back(r);
    ranges_.push_back(r.range);
    return true;
}

bool MappedMemory::add_elf(const char* path, uintptr_t pretend_base) {
    uintptr_t raw = 0;
    size_t size = 0;
    if (!map_raw(path, raw, size)) {
        return false;
    }
    if (size < 64) {
        return false;
    }
    const uint8_t* file = reinterpret_cast<const uint8_t*>(raw);
    if (std::memcmp(file, "\x7f""ELF", 4) != 0 || file[4] != 2 || file[5] != 1) {
        return false;   // not a 64-bit little-endian ELF
    }

    uint64_t phoff = 0;
    uint16_t phentsize = 0;
    uint16_t phnum = 0;
    std::memcpy(&phoff, file + 0x20, 8);
    std::memcpy(&phentsize, file + 0x36, 2);
    std::memcpy(&phnum, file + 0x3A, 2);
    if (phoff == 0 || phentsize < 56) {
        return false;
    }

    for (uint16_t i = 0; i < phnum; ++i) {
        const uint64_t at = phoff + static_cast<uint64_t>(i) * phentsize;
        if (at + 56 > size) {
            break;
        }
        uint32_t p_type = 0;
        std::memcpy(&p_type, file + at, 4);
        if (p_type != 1) {                        // PT_LOAD
            continue;
        }
        uint64_t p_offset = 0;
        uint64_t p_vaddr = 0;
        uint64_t p_filesz = 0;
        std::memcpy(&p_offset, file + at + 0x08, 8);
        std::memcpy(&p_vaddr, file + at + 0x10, 8);
        std::memcpy(&p_filesz, file + at + 0x20, 8);
        if (p_filesz == 0 || p_offset + p_filesz > size) {
            continue;
        }
        Region r;
        r.range.begin = pretend_base + static_cast<uintptr_t>(p_vaddr);
        r.range.end = r.range.begin + static_cast<size_t>(p_filesz);
        r.range.writable = false;
        r.data = file + p_offset;
        regions_.push_back(r);
        ranges_.push_back(r.range);
    }
    return !regions_.empty();
}

bool MappedMemory::read(uintptr_t addr, void* out, size_t n) const {
    if (out == nullptr || n == 0) {
        return false;
    }
    for (const Region& r : regions_) {
        if (!r.range.contains(addr, n)) {
            continue;
        }
        std::memcpy(out, r.data + (addr - r.range.begin), n);
        return true;
    }
    return false;
}

}  // namespace ff