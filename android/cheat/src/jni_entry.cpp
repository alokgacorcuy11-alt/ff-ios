// jni_entry.cpp - the only surface the host container talks to.
//
// Four calls. Nothing runs until start() is called, nothing keeps running after
// stop(), and status() is the honest answer to "what did you find" rather than
// a success flag.
#include <jni.h>

#include <android/log.h>
#include <cstdarg>
#include <cstdio>
#include <dlfcn.h>
#include <link.h>

#include <cstring>
#include <string>

#include "ff_mem.h"
#include "ff_meta.h"
#include "ff_scan.h"
#include "ff_stealth.h"

#define LOG(...) __android_log_print(ANDROID_LOG_INFO, "ffcheat", __VA_ARGS__)

namespace {

ff::LiveMemory* g_mem = nullptr;
ff::Meta* g_meta = nullptr;
uintptr_t g_il2cpp_base = 0;
uintptr_t g_metadata_base = 0;
std::string g_status = "not started";

uintptr_t module_base(const char* substring) {
    struct Ctx {
        const char* want;
        uintptr_t base;
    } ctx{substring, 0};

    dl_iterate_phdr([](struct dl_phdr_info* info, size_t, void* data) -> int {
        Ctx* c = static_cast<Ctx*>(data);
        if (info->dlpi_name != nullptr && std::strstr(info->dlpi_name, c->want) != nullptr) {
            c->base = static_cast<uintptr_t>(info->dlpi_addr);
            return 1;
        }
        return 0;
    }, &ctx);
    return ctx.base;
}

void append(std::string& s, const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    s += buf;
}

jstring to_jstring(JNIEnv* env, const std::string& s) {
    return env->NewStringUTF(s.c_str());
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM*, void*) {
    return JNI_VERSION_1_6;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_alokg_ffcheat_Native_start(JNIEnv* env, jclass) {
    if (g_meta != nullptr) {
        return to_jstring(env, g_status);
    }

    g_mem = new ff::LiveMemory();
    g_il2cpp_base = module_base("libil2cpp.so");

    uintptr_t scan_begin = 0;
    uintptr_t scan_end = 0;
    g_metadata_base = ff::Meta::find_metadata_base(*g_mem, scan_begin, scan_end);

    g_status.clear();
    append(g_status, "il2cpp base %p\n", reinterpret_cast<void*>(g_il2cpp_base));
    append(g_status, "metadata base %p (scan %p..%p)\n",
           reinterpret_cast<void*>(g_metadata_base),
           reinterpret_cast<void*>(scan_begin), reinterpret_cast<void*>(scan_end));

    if (g_il2cpp_base == 0 || g_metadata_base == 0) {
        append(g_status, "STOP: metadata or libil2cpp not mapped yet");
        LOG("%s", g_status.c_str());
        return to_jstring(env, g_status);
    }

    g_meta = new ff::Meta(*g_mem, ff::kLayoutFF132, g_metadata_base, g_il2cpp_base);
    append(g_status, "types %d\n", g_meta->type_count());
    append(g_status, "field offsets reachable: %s\n",
           g_meta->offsets_ready() ? "yes" : "NO");

    // A class has to resolve before anything else is worth doing. This is the
    // one call that proves the whole chain: metadata found, string addressed,
    // class located, namespace matched.
    const int32_t idx = g_meta->find_type_any(ff::kPlayerName);
    append(g_status, "Player type index: %d\n", idx);
    if (idx >= 0) {
        ff::TypeInfo type;
        if (g_meta->type_info(idx, type)) {
            ff::ClassFinder finder(*g_mem, *g_meta);
            ff::ClassHit hit;
            const bool found = finder.find(type, hit);
            append(g_status, "Il2CppClass: %s (scanned %zu bytes)\n",
                   found ? "FOUND" : "not found", finder.last_scanned_bytes());
            if (found) {
                append(g_status, "  klass %p at name offset %#x\n",
                       reinterpret_cast<void*>(hit.klass), hit.name_offset);
            }
        }
    }

    const ff::StealthReport stealth = ff::inspect_self();
    append(g_status, "stealth: %s\n", stealth.note.c_str());

    LOG("%s", g_status.c_str());
    return to_jstring(env, g_status);
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_alokg_ffcheat_Native_status(JNIEnv* env, jclass) {
    return to_jstring(env, g_status);
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_alokg_ffcheat_Native_stop(JNIEnv* env, jclass) {
    delete g_meta;
    g_meta = nullptr;
    delete g_mem;
    g_mem = nullptr;
    g_status = "stopped";
    return to_jstring(env, g_status);
}

// Field offsets for one class, printed by name. Used during research: the
// answer tells you instantly whether the offsets still match after a patch.
extern "C" JNIEXPORT jstring JNICALL
Java_com_alokg_ffcheat_Native_dumpClass(JNIEnv* env, jclass, jstring name) {
    if (g_meta == nullptr) {
        return to_jstring(env, "not started");
    }
    const char* raw = env->GetStringUTFChars(name, nullptr);
    std::string wanted(raw == nullptr ? "" : raw);
    if (raw != nullptr) {
        env->ReleaseStringUTFChars(name, raw);
    }

    const int32_t idx = g_meta->find_type_any(wanted);
    if (idx < 0) {
        return to_jstring(env, wanted + ": not found");
    }
    ff::TypeInfo type;
    if (!g_meta->type_info(idx, type)) {
        return to_jstring(env, wanted + ": unreadable");
    }

    std::string out;
    append(out, "%s  (%d fields)\n", type.full().c_str(), type.field_count);
    for (int32_t i = 0; i < type.field_count; ++i) {
        std::string field;
        if (!g_meta->field_name(type, i, field)) {
            continue;
        }
        const int32_t offset = g_meta->field_offset(type, i);
        if (offset < 0) {
            continue;
        }
        append(out, "  +%#06x  %s\n", offset, field.c_str());
    }
    return to_jstring(env, out);
}