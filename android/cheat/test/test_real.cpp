// test_real.cpp - the decoder against Free Fire's own files, on a real Android
// kernel, with no game running.
//
// The desktop test proves the logic against a fixture whose answer is known.
// This one proves it against the real thing: the shipped
// global-metadata.dat (57 MB) and the shipped libil2cpp.so (184 MB), mapped
// read-only and read through exactly the way the injected library reads them.
//
// What it cannot test: the live class scan. There is no IL2CPP runtime in this
// process, so no Il2CppClass exists to find, and the scan is expected to report
// zero hits. What it does test is everything before that: the header search,
// the type table, the string table, the field table, and the offset tables in
// the binary - which is to say every number the cheat depends on.
//
// It runs natively. On an x86_64 host it should be built for x86_64, because
// running it under ARM translation would be testing the translator.
//
//   test_real <libil2cpp.so> <global-metadata.dat> [ClassName] [FieldName]

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "ff_mem.h"
#include "ff_meta.h"
#include "ff_offsets.h"
#include "ff_scan.h"

namespace {

int g_fail = 0;
int g_pass = 0;

void ok(bool cond, const char* what, const std::string& extra = "") {
    if (cond) {
        std::printf("[ok  ] %s %s\n", what, extra.c_str());
        ++g_pass;
    } else {
        std::printf("[FAIL] %s %s\n", what, extra.c_str());
        ++g_fail;
    }
}

// The two files are mapped at invented addresses. Nothing is allocated for
// them; the pages are mapped once and served.
constexpr uintptr_t kMetaBase = 0x200000000ULL;
constexpr uintptr_t kIl2cppBase = 0x400000000ULL;

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::printf("usage: %s <libil2cpp.so> <global-metadata.dat> "
                    "[ClassName] [FieldName]\n", argv[0]);
        return 2;
    }

    std::printf("==============================================================\n");
    std::printf(" ffcheat real-data decode\n");
    std::printf("==============================================================\n");

    ff::MappedMemory mem;
    if (!mem.add_file(argv[2], kMetaBase)) {
        std::printf("[FAIL] cannot map %s\n", argv[2]);
        return 1;
    }
    if (!mem.add_file(argv[1], kIl2cppBase)) {
        std::printf("[FAIL] cannot map %s\n", argv[1]);
        return 1;
    }
    std::printf("  mapped %zu bytes over %zu regions\n\n", mem.mapped_bytes(),
                mem.ranges().size());

    // --- the header has to be found the same way the cheat finds it --------
    uintptr_t scan_begin = 0;
    uintptr_t scan_end = 0;
    const uintptr_t meta_base = ff::Meta::find_metadata_base(mem, scan_begin, scan_end);
    ok(meta_base == kMetaBase, "finds the metadata header by its sanity and version",
       meta_base == kMetaBase ? "" : "got " + std::to_string(meta_base));

    ff::Meta meta(mem, ff::kLayoutFF132, meta_base, kIl2cppBase);

    // 43,627 is the number the Python dumper read from this same file, with no
    // C++ involved. Agreement between two independent readers is the point.
    ok(meta.type_count() == 43627, "type count matches the python dump",
       std::to_string(meta.type_count()));
    ok(meta.offsets_ready(),
       "reaches the field-offset table in libil2cpp.so through "
       "Il2CppMetadataRegistration");

    // --- the class the ESP is written against -----------------------------
    const int32_t player = meta.find_type_any(ff::kPlayerName);
    ok(player == 31821, "COW.GamePlay.Player is at type index 31821",
       std::to_string(player));

    ff::TypeInfo type;
    ok(meta.type_info(player, type), "reads the type definition");
    ok(type.field_count == 1689, "Player has 1,689 fields",
       std::to_string(type.field_count));
    ok(type.method_count == 4136, "and 4,136 methods",
       std::to_string(type.method_count));

    // --- the offsets the ESP uses, re-read from the real binary -----------
    struct Want {
        const char* name;
        int32_t expected;
    };
    const Want wants[] = {
            {"m_GroundHitPos", 0x1db8},
            {"m_TransformObject", 0x1288},
            {"TeamModeID", 0x0434},
            {"TeamColorStr", 0x0818},
            {"Speed", 0x0560},
            {"AimCount", 0x054c},
            {"m_AimAssist", 0x0638},
    };
    for (const Want& w : wants) {
        const int32_t index = meta.field_index_by_name(type, w.name);
        const int32_t offset = index < 0 ? -1 : meta.field_offset(type, index);
        ok(offset == w.expected,
           "field offset matches the generated header",
           std::string(w.name) + " = " + std::to_string(offset) + " (header says "
                   + std::to_string(w.expected) + ")");
    }

    // --- a class the argument names, if one was given ---------------------
    if (argc > 3) {
        const int32_t k = meta.find_type_any(argv[3]);
        std::printf("\n  %s -> type %d\n", argv[3], k);
        if (k >= 0) {
            ff::TypeInfo t;
            meta.type_info(k, t);
            std::printf("  %s  (%d fields, %d methods)\n", t.full().c_str(),
                        t.field_count, t.method_count);
            const char* filter = argc > 4 ? argv[4] : nullptr;
            int shown = 0;
            for (int32_t i = 0; i < t.field_count && shown < 60; ++i) {
                std::string name;
                if (!meta.field_name(t, i, name)) {
                    continue;
                }
                if (filter != nullptr && name.find(filter) == std::string::npos) {
                    continue;
                }
                const int32_t off = meta.field_offset(t, i);
                if (off <= 0) {
                    continue;
                }
                std::printf("    +%#06x  %s\n", off, name.c_str());
                ++shown;
            }
        }
    }

    // --- the live scan, which is expected to find nothing here ------------
    ff::ClassFinder finder(mem, meta);
    ff::ClassHit hit;
    const bool found = finder.find(type, hit);
    std::printf("\n  live class scan: %s, %zu bytes searched\n",
                found ? "FOUND" : "no hit (expected: no IL2CPP runtime here)",
                finder.last_scanned_bytes());
    ok(!found || hit.klass != 0,
       "the class scan runs and does not crash on a 241 MB image");

    std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
