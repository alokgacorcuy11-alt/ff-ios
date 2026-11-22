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

BlobMemory::BlobMemory(std::vector<uint8_t> bytes, uintptr_t base)
        : bytes_(std::move(bytes)), base_(base) {
    Range r;
    r.begin = base_;
    r.end = base_ + bytes_.size();
    r.writable = false;
    ranges_.push_back(r);
}

bool BlobMemory::read(uintptr_t addr, void* out, size_t n) const {
    if (out == nullptr || n == 0) {
        return false;
    }
    if (addr < base_) {
        return false;
    }
    const uintptr_t off = addr - base_;
    if (off + n > bytes_.size()) {
        return false;
    }
    std::memcpy(out, bytes_.data() + off, n);
    return true;
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
    for (const Region& r : regions_) {
        if (r.data != nullptr) {
            ::munmap(const_cast<uint8_t*>(r.data), r.end - r.begin);
        }
    }
}

bool MappedMemory::add_file(const char* path, uintptr_t pretend_base) {
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
    Region r;
    r.begin = pretend_base;
    r.end = pretend_base + size;
    r.data = static_cast<const uint8_t*>(p);
    regions_.push_back(r);

    Range range;
    range.begin = r.begin;
    range.end = r.end;
    range.writable = false;
    ranges_.push_back(range);

    mapped_ += size;
    return true;
}

bool MappedMemory::read(uintptr_t addr, void* out, size_t n) const {
    if (out == nullptr || n == 0) {
        return false;
    }
    for (const Region& r : regions_) {
        if (addr >= r.begin && n <= r.end - addr) {
            std::memcpy(out, r.data + (addr - r.begin), n);
            return true;
        }
    }
    return false;
}

}  // namespace ff