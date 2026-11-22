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
    test_memory();
    test_metadata();
    test_math();
    test_player_reads();
    std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
