cmake_minimum_required(VERSION 3.18)
project(ffcheat CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wno-unused-parameter)

set(CORE_SOURCES
    src/ff_mem.cpp
    src/ff_meta.cpp
    src/ff_scan.cpp
    src/ff_math.cpp
    src/ff_esp.cpp
    src/ff_stealth.cpp)

# Two things are built from one source tree. The host test is the same core
# without the JNI entry, so it compiles and runs on a desktop runner where the
# answer to every question is checkable in seconds instead of on a phone.
option(FFCHEAT_HOST_TEST "build the desktop test instead of the android library" OFF)

# The same core, run against the real Free Fire files on a real Android kernel.
# Built for whatever ABI the test device is, so nothing is translated.
option(FFCHEAT_REAL_TEST "build the real-data device test" OFF)

if(FFCHEAT_HOST_TEST)
    add_executable(test_core test/test_core.cpp ${CORE_SOURCES})
    target_include_directories(test_core PRIVATE src)
    enable_testing()
    add_test(NAME core COMMAND test_core)
elseif(FFCHEAT_REAL_TEST)
    add_executable(test_real test/test_real.cpp ${CORE_SOURCES})
    target_include_directories(test_real PRIVATE src)
else()
    add_library(ffcheat SHARED ${CORE_SOURCES} src/jni_entry.cpp)
    target_include_directories(ffcheat PRIVATE src)
    find_library(log-lib log)
    target_link_libraries(ffcheat ${log-lib})
    # The cheat is injected into an existing process, so it is never the entry
    # point of anything and needs no visibility beyond what the host asks for.
    set_target_properties(ffcheat PROPERTIES
        PREFIX "lib"
        OUTPUT_NAME "ffcheat"
        CXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON)
endif()
