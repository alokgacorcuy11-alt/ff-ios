// Obf.m - one place for the verbosity flag.
//
// Default is OFF: a release build must not narrate itself in Console.app.
// Turn it on with FF_VERBOSE=1 in the environment (dev builds via a wrapper or
// `simctl spawn booted launchctl setenv`) or with the "Verbose Logs" switch in
// the menu, which is stored like any other feature.

#import "Obf.h"

int FFVerboseFlag = -1;      // -1 = not decided yet

BOOL FFVerbose(void) {
    if (FFVerboseFlag >= 0) return FFVerboseFlag == 1;
    const char *e = getenv("FF_VERBOSE");
    FFVerboseFlag = (e && e[0]) ? (e[0] != '0' ? 1 : 0) : 0;
    return FFVerboseFlag == 1;
}

void FFSetVerbose(BOOL on) {
    FFVerboseFlag = on ? 1 : 0;
}