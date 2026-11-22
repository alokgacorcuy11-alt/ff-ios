// test_core.cpp - the cheat's own logic, run on a desktop against a fixture.
//
// The parts worth testing are the ones where being wrong is silent: reading a
// field at an offset that moved, or projecting a world point with a matrix
// convention that is off by a transpose. Both produce output that looks
// plausible and is wrong, which is exactly the class of bug that survives to
// the buyer.
//
// The fixture is a metadata image built by hand with the v31 layout and small
// offsets, so the decoder is exercised end to end without shipping a 57 MB file
// into CI. The real layout constants are checked separately, by the Python
// dumper, against the real binary.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "ff_esp.h"
#include "ff_math.h"
#include "ff_mem.h"
#include "ff_meta.h"

static int g_fail = 0;
static int g_pass = 0;

static void check(bool cond, const char* what, const std::string& extra = "") {
    if (cond) {
        ++g_pass;
        std::printf("[ok  ] %s %s\n", what, extra.c_str());
    } else {
        ++g_fail;
        std::printf("[FAIL] %s %s\n", what, extra.c_str());
    }
}

// ---------------------------------------------------------------- fixture --

namespace {

constexpr uintptr_t kBase = 0x10000000;
constexpr int32_t kStrings = 0x100;
constexpr int32_t kTypes = 0x400;
constexpr int32_t kFields = 0x600;
constexpr int32_t kMethods = 0x700;
constexpr int32_t kOffsetsArray = 0x800;
constexpr int32_t kOffsetsType0 = 0x900;
constexpr int32_t kOffsetsType1 = 0x920;
constexpr int32_t kOffsetsType2 = 0x940;
constexpr int32_t kBlobSize = 2 * 1024 * 1024;   // must clear the 1 MB filter

constexpr int32_t kStrPlayer = 0;
constexpr int32_t kStrNamespace = 16;
constexpr int32_t kStrHealth = 32;
constexpr int32_t kStrSpeed = 48;
constexpr int32_t kStrGetSpeed = 64;

const ff::Layout kTestLayout = {
        kTypes, 88, 88 * 3,
        kStrings, 0x200,
        kFields, 12,
        kMethods, 40,
        32, 36, 64, 68,
};

void put32(std::vector<uint8_t>& b, int32_t at, uint32_t v) {
    std::memcpy(b.data() + at, &v, 4);
}
void put16(std::vector<uint8_t>& b, int32_t at, uint16_t v) {
    std::memcpy(b.data() + at, &v, 2);
}
void put64(std::vector<uint8_t>& b, int32_t at, uint64_t v) {
    std::memcpy(b.data() + at, &v, 8);
}
void putstr(std::vector<uint8_t>& b, int32_t at, const char* s) {
    std::memcpy(b.data() + at, s, std::strlen(s) + 1);
}

void type_def(std::vector<uint8_t>& b, int32_t index, int32_t name, int32_t ns,
              int32_t field_start, uint16_t field_count,
              int32_t method_start, uint16_t method_count) {
    const int32_t t = kTypes + index * 88;
    put32(b, t + 0, static_cast<uint32_t>(name));
    put32(b, t + 4, static_cast<uint32_t>(ns));
    put32(b, t + kTestLayout.type_field_start, static_cast<uint32_t>(field_start));
    put32(b, t + kTestLayout.type_method_start, static_cast<uint32_t>(method_start));
    put16(b, t + kTestLayout.type_method_count, method_count);
    put16(b, t + kTestLayout.type_field_count, field_count);
}

void field_def(std::vector<uint8_t>& b, int32_t index, int32_t name) {
    const int32_t f = kFields + index * 12;
    put32(b, f + 0, static_cast<uint32_t>(name));
    put32(b, f + 4, 1);
    put32(b, f + 8, 0x04000001u + static_cast<uint32_t>(index));
}

std::vector<uint8_t> build_fixture() {
    std::vector<uint8_t> b(kBlobSize, 0);

    put32(b, 0, ff::kMetadataSanity);
    put32(b, 4, static_cast<uint32_t>(ff::kMetadataVersion));

    putstr(b, kStrings + kStrPlayer, "Player");
    putstr(b, kStrings + kStrNamespace, "COW.GamePlay");
    putstr(b, kStrings + kStrHealth, "m_Health");
    putstr(b, kStrings + kStrSpeed, "Speed");
    putstr(b, kStrings + kStrGetSpeed, "get_Speed");

    type_def(b, 0, kStrPlayer, kStrNamespace, 0, 2, 0, 1);
    type_def(b, 1, kStrHealth, kStrNamespace, 2, 1, 1, 0);
    type_def(b, 2, kStrSpeed, kStrNamespace, 3, 0, 1, 0);

    field_def(b, 0, kStrHealth);
    field_def(b, 1, kStrSpeed);
    field_def(b, 2, kStrGetSpeed);

    put32(b, kMethods + 0, static_cast<uint32_t>(kStrGetSpeed));

    // The offset array is an array of pointers, one per type, each pointing at
    // that type's per-field offsets - the shape the real binary uses.
    put64(b, kOffsetsArray + 0, kBase + kOffsetsType0);
    put64(b, kOffsetsArray + 8, kBase + kOffsetsType1);
    put64(b, kOffsetsArray + 16, kBase + kOffsetsType2);
    put32(b, kOffsetsType0 + 0, 0x20);
    put32(b, kOffsetsType0 + 4, 0x24);
    put32(b, kOffsetsType1 + 0, 0x30);

    return b;
}

}  // namespace

// ------------------------------------------------------------------ tests --

// The regression test for the bug that cost a device round trip.
//
// Range::contains used to read `addr >= begin && n <= end - addr`, which accepts
// any address above the end, because the subtraction underflows and every size
// is smaller than a wrapped-around value. With one region mapped, every address
// tested was inside it, so nothing noticed. With two, a read aimed at the second
// region was served by the first at an offset 8 GB past its end, and the process
// died before it printed a line.
static void test_range() {
    const ff::Range r{0x1000, 0x2000, false};

    check(r.contains(0x1000, 0x1000), "a range contains its first byte");
    check(r.contains(0x1F00, 0x100), "a range contains its last byte");
    check(!r.contains(0x2000, 1), "a range does not contain its end");
    check(!r.contains(0xFFF, 1), "a range does not contain an address below it");
    check(!r.contains(0x5000, 1), "a range does not contain an address above it");

    // The exact shape of the original failure: an address 0x3000 past the end,
    // with a size small enough that the wrapped subtraction still looked bigger.
    check(!r.contains(0x5000, 8),
          "and the size check does not wrap around to accept it");
    check(!r.contains(UINTPTR_MAX - 8, 8),
          "an address near the top of the space is refused too");
}

// The regression test for the failure that cost two device round trips.
//
// Two regions. A read aimed at the second must be served by the second. The
// first version of the guard accepted any address above a region's start, so
// the first region served it from an offset far past its end - and on a device,
// with a 57 MB region and a read 8 GB past its end, that is a segfault.
//
// The host test had one region, so it could never have caught this.
static void test_two_regions() {
    std::vector<uint8_t> low(64, 0xAA);
    std::vector<uint8_t> high(64, 0xBB);

    ff::BlobMemory mem;
    mem.add_segment(low, 0x1000);
    mem.add_segment(high, 0x9000);

    uint8_t v = 0;
    check(mem.read(0x1000, &v, 1) && v == 0xAA, "a read in the first region is served by it");
    check(mem.read(0x9000, &v, 1) && v == 0xBB,
          "a read in the second region is served by the second, not by the first");
    check(mem.read(0x9000 + 63, &v, 1) && v == 0xBB,
          "the last byte of the second region is still the second region");
    check(!mem.read(0x9040, &v, 1), "past the end of the second region is refused");
    check(!mem.read(0x5000, &v, 1), "the gap between them is refused");
    check(!mem.read(0x9030, &v, 32), "a read that straddles the end is refused");
}

// The regression test for the second device failure, and the more interesting
// of the two.
//
// A shared object is not a linear image. In libil2cpp.so, .data.rel.ro has file
// offset 0xa4140f0 and virtual address 0xa41c0f0 - the same bytes, 0x8000
// apart. Serving the file byte for byte at an address looks right, works for
// every offset below the first such segment, and returns the wrong number for
// every offset above it. The pointer to the field-offset table read back as
// 0x150000 instead of 0xa8bf168, and nothing about it looked broken.
//
// The fixture states the problem in the smallest possible form: one PT_LOAD
// whose file offset and virtual address differ.
static void test_elf_segments() {
    std::vector<uint8_t> elf(0x300, 0);
    elf[0] = 0x7f;
    elf[1] = 'E';
    elf[2] = 'L';
    elf[3] = 'F';
    elf[4] = 2;   // 64-bit
    elf[5] = 1;   // little endian

    auto put16 = [&elf](size_t at, uint16_t v) { std::memcpy(elf.data() + at, &v, 2); };
    auto put32 = [&elf](size_t at, uint32_t v) { std::memcpy(elf.data() + at, &v, 4); };
    auto put64 = [&elf](size_t at, uint64_t v) { std::memcpy(elf.data() + at, &v, 8); };

    put64(0x20, 0x40);            // e_phoff
    put16(0x36, 56);              // e_phentsize
    put16(0x38, 1);               // e_phnum
    put32(0x40 + 0x00, 1);        // p_type = PT_LOAD
    put64(0x40 + 0x08, 0x200);    // p_offset
    put64(0x40 + 0x10, 0x1000);   // p_vaddr
    put64(0x40 + 0x20, 0x100);    // p_filesz
    put64(0x200 + 0x40, 0xCAFEBABEULL);

    const char* path = "/tmp/ffcheat-elf-fixture";
    FILE* f = std::fopen(path, "wb");
    if (f == nullptr) {
        check(false, "could not write the elf fixture");
        return;
    }
    std::fwrite(elf.data(), 1, elf.size(), f);
    std::fclose(f);

    ff::MappedMemory mem;
    check(mem.add_elf(path, 0x5000), "an ELF is mapped by its segments");

    uintptr_t v = 0;
    const uintptr_t inside = 0x5000 + 0x1000 + 0x40;
    check(mem.get_ptr(inside, v) && v == 0xCAFEBABEULL,
          "a read at the segment's virtual address returns the bytes at its own "
          "file offset",
          v == 0xCAFEBABEULL ? "" : "got " + std::to_string(v));

    check(!mem.contains(0x5000 + 0x200, 8),
          "a file offset is not treated as a virtual address");
    check(!mem.contains(0x5000 + 0x1000 + 0x200, 8),
          "and nothing is mapped past the end of the segment");

    std::remove(path);
}

static void test_memory() {
    std::vector<uint8_t> bytes = {'h', 'i', 0, 'x'};
    ff::BlobMemory mem(bytes, kBase);

    char buf[8] = {0};
    check(mem.get_cstr(kBase, buf, sizeof(buf)) && std::strcmp(buf, "hi") == 0,
          "reads a NUL-terminated string");
    check(!mem.contains(kBase + 4, 1), "refuses an address past the end");
    uint32_t past = 0;
    check(!mem.get_u32(kBase + 4, past), "refuses a read past the end");

    bytes = {0};
    ff::BlobMemory empty(bytes, kBase);
    check(!empty.get_cstr(kBase, buf, sizeof(buf)), "an empty string is not a name");
}

static void test_metadata() {
    ff::BlobMemory mem(build_fixture(), kBase);

    uintptr_t scan_begin = 0;
    uintptr_t scan_end = 0;
    const uintptr_t found = ff::Meta::find_metadata_base(mem, scan_begin, scan_end);
    check(found == kBase, "finds the metadata header in a mapped region",
          found == kBase ? "" : "got " + std::to_string(found));

    ff::Meta meta(mem, kTestLayout, kBase, 0, kBase + kOffsetsArray);
    check(meta.type_count() == 3, "reads the type count from the table length",
          std::to_string(meta.type_count()));
    check(meta.offsets_ready(), "links the field-offset array");

    const int32_t player = meta.find_type("Player", "COW.GamePlay");
    check(player == 0, "finds a type by name and namespace", std::to_string(player));
    check(meta.find_type_any("COW.GamePlay.Player") == 0, "finds it by full name");
    check(meta.find_type_any("COW.GamePlay.Enemy") == -1, "a missing type is -1");

    ff::TypeInfo type;
    check(meta.type_info(0, type) && type.field_count == 2 && type.method_count == 1,
          "reads the field and method counts");
    check(type.full() == "COW.GamePlay.Player", "builds the full name", type.full());
    check(type.name_index == kStrPlayer,
          "remembers where the name lives in the string blob, which is what the "
          "class scan searches for");

    std::string field;
    check(meta.field_name(type, 0, field) && field == "m_Health",
          "reads a field name", field);
    check(meta.field_offset(type, 0) == 0x20, "reads the field offset",
          std::to_string(meta.field_offset(type, 0)));
    check(meta.field_offset(type, 1) == 0x24, "and the second one",
          std::to_string(meta.field_offset(type, 1)));
    check(meta.field_index_by_name(type, "Speed") == 1, "finds a field by name");
    check(meta.field_index_by_name(type, "Nothing") == -1, "a missing field is -1");

    std::string method;
    check(meta.method_name(type, 0, method) && method == "get_Speed",
          "reads a method name", method);
    check(meta.field_offset(type, 9) == -1, "an out-of-range field is -1, not a guess");
}

static void test_math() {
    const ff::Mat4 identity = ff::Mat4::identity();
    ff::Vec2 out;

    check(ff::world_to_screen(identity, {0, 0, 5}, 1000, 500, out)
                  && std::fabs(out.x - 500.f) < 0.01f
                  && std::fabs(out.y - 250.f) < 0.01f,
          "the origin projects to the centre of the screen");

    check(!ff::world_to_screen(identity, {0, 0, -5}, 1000, 500, out),
          "a point behind the camera does not project");
    check(!ff::world_to_screen(identity, {0, 0, 0}, 1000, 500, out),
          "a point on the near plane does not project");
    check(!ff::world_to_screen(identity, {500, 0, 5}, 1000, 500, out),
          "a point far off axis does not project");

    // Screen y grows downward: a point above centre lands in the upper half.
    check(ff::world_to_screen(identity, {0, 1, 5}, 1000, 500, out) && out.y < 250.f,
          "up in the world is up on the screen");

    // A camera 10 m back looking down +z, so an enemy 5 m ahead is 15 m away.
    ff::Mat4 vp = ff::Mat4::identity();
    vp.m[14] = 10.f;
    ff::Box box;
    check(ff::player_box(vp, {0, 0, 5}, ff::Silhouette{}, 1000, 500, box),
          "builds a box for a player in front");
    check(box.valid() && box.height() > 0, "the box has a positive height",
          std::to_string(box.height()));

    ff::Box near_box;
    ff::player_box(vp, {0, 0, 1}, ff::Silhouette{}, 1000, 500, near_box);
    check(near_box.height() > box.height(),
          "a nearer player draws a taller box",
          std::to_string(near_box.height()) + " > " + std::to_string(box.height()));

    check(!ff::player_box(vp, {0, 0, -20}, ff::Silhouette{}, 1000, 500, box),
          "no box for a player behind the camera");

    check(std::fabs(ff::distance_m({0, 0, 0}, {3, 4, 0}) - 5.f) < 0.001f,
          "distance is Euclidean");
}

static void test_player_reads() {
    // A player object with the two fields the ESP reads, at their real offsets.
    std::vector<uint8_t> bytes(kBlobSize, 0);
    const uintptr_t obj = 0x1000;
    const float pos[3] = {12.5f, 3.25f, -40.0f};
    std::memcpy(bytes.data() + obj + ff::player::m_GroundHitPos, pos, sizeof(pos));
    put32(bytes, static_cast<int32_t>(obj + ff::player::TeamModeID), 3);

    ff::BlobMemory mem(bytes, kBase);
    ff::PlayerView view;
    check(ff::read_player(mem, kBase + obj, view), "reads a player");
    check(view.has_position, "the player has a position");
    check(std::fabs(view.ground.x - 12.5f) < 0.01f
                  && std::fabs(view.ground.y - 3.25f) < 0.01f
                  && std::fabs(view.ground.z + 40.0f) < 0.01f,
          "the position is the one that was written");
    check(view.team == 3, "reads the team", std::to_string(view.team));

    view.local_team = 3;
    check(!view.enemy(), "the same team is not an enemy");
    view.local_team = 1;
    check(view.enemy(), "a different team is an enemy");

    ff::PlayerView missing;
    check(!ff::read_player(mem, kBase + 0x400000, missing),
          "a player outside the image is rejected instead of boxed at the origin");
}

int main() {
    std::printf("================================================\n");
    std::printf(" ffcheat core (host, no android)\n");
    std::printf("================================================\n");
    test_range();
    test_two_regions();
    test_elf_segments();
    test_memory();
    test_metadata();
    test_math();
    test_player_reads();
    std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
