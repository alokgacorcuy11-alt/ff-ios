// ff_stealth.h - what can actually be hidden, and what cannot.
//
// Written as capabilities, not as promises, because the honest answer matters
// more than the comfortable one.
//
// WHAT THIS LAYER REALLY DOES
//
//   reads only          the cheat never writes to the game's code or data, so
//                       whatever integrity check hashes .text still passes. This
//                       is not a trick, it is the design: ESP needs to read.
//   no ptrace           we are inside the process, so TracerPid stays 0 and
//                       there is no attach to notice.
//   no frida            no gadget, no port, no agent thread, nothing to find.
//   thread hygiene      no thread of our own is created; work happens on a
//                       thread the game already has.
//
// WHAT IT CANNOT DO, AND NO ONE ELSE CAN EITHER
//
//   the library path in /proc/self/maps   a mapped file has a path and the
//                                         kernel owns it. Only never mapping a
//                                         file hides it, which means a manual
//                                         loader - see below.
//   behavioural detection                 an aim that snaps in 8 ms every time is
//                                         visible in the statistics of the match,
//                                         not in the memory image.
//   server-side validation                a speed the server did not compute gets
//                                         rolled back, and a rollback is a flag.
//   future detections                     an anticheat update can add a check
//                                         tomorrow that did not exist today.
//
// On the maps path: the only approach that removes it is loading the payload
// from an anonymous mapping with a manual ELF loader. That is a real technique
// and it is the next step - mapped_from_file() below reports honestly whether
// this build is there yet, so nobody assumes it is.
#pragma once

#include <cstdint>
#include <string>

namespace ff {

struct StealthReport {
    bool reads_only = true;
    bool uses_ptrace = false;
    bool uses_frida = false;
    bool created_threads = false;
    bool mapped_from_file = true;   // true = the path is visible in maps
    std::string self_path;
    std::string note;
};

// Best-effort: names the calling thread something unremarkable, because a
// thread called "cheat" is the cheapest possible detection.
void name_thread(const char* name);

// Unlinks a file after it has been mapped. The mapping survives, the name is
// gone from the filesystem - so a check that looks for the file finds nothing,
// even though a check that reads maps still sees "(deleted)".
bool unlink_after_open(const char* path);

// Fills in what this build actually does. Called at startup and reported to the
// host, so the answer to "is it hidden" is measured rather than assumed.
StealthReport inspect_self();

}  // namespace ff