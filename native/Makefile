TARGET := iphone:clang:latest:12.0

# Free Fire 1.132.1 ships arm64 only (ipa_probe: archs=arm64). Building an
# arm64e slice adds nothing to the product and only adds a second chance to
# fail at link/sign time.
ARCHS = arm64

# Build a plain injectable dylib - no substrate, no jailbreak runtime needed.
# FFMenu is only the build artefact name; nothing carries it into the binary.
LIBRARY_NAME = FFMenu
FFMenu_FILES = Tweak.x \
	Core/IL2CPP.m \
	Core/Resolve.m \
	Core/FFState.m \
	Core/Game.m \
	Core/ESP.m \
	Core/Inject.m \
	Core/Obf.m \
	Core/Stealth.m \
	Core/Rebind.m \
	Core/Secrets.m \
	Core/Integrity.m \
	Menu/FFMenu.m

# -fvisibility=hidden keeps our symbols out of the dynamic symbol table, which
# makes `nm`/`strings` on the final dylib a lot less informative.
FFMenu_CFLAGS = -fobjc-arc -O2 -fvisibility=hidden -Wall \
	-DFFLOGTAG='"us.core"' \
	-I$(THEOS_PROJECT_DIR) \
	-I$(THEOS_PROJECT_DIR)/Core \
	-I$(THEOS_PROJECT_DIR)/Menu \
	-Wno-unused-variable -Wno-unused-function

# make RELEASE=1  ->  every log format string is compiled out, so `strings` on
#                     the dylib contains no messages at all. See Core/Obf.h.
ifeq ($(RELEASE),1)
	FFMenu_CFLAGS += -DFF_QUIET_BUILD
endif

FFMenu_FRAMEWORKS = Foundation UIKit CoreGraphics QuartzCore
FFMenu_LIBRARIES = objc

# The install name is what a `strings` on the dylib would show for LC_ID_DYLIB.
# Point it at the camouflage path so the binary agrees with where it is placed.
FFMenu_LDFLAGS += -Wl,-install_name,@executable_path/Frameworks/UnityServices.framework/UnityServices

# Strip local symbols. Without this, nm on the dylib lists every private
# function and global by name, which is a road map.
FFMenu_LDFLAGS += -Wl,-x

FFMenu_INSTALL_PATH = /Library/MobileSubstrate/DynamicLibraries

ifeq ($(ROOTLESS),1)
	THEOS_PACKAGE_SCHEME = rootless
	FFMenu_INSTALL_PATH = /var/jb/Library/MobileSubstrate/DynamicLibraries
endif

include $(THEOS)/makefiles/common.mk
include $(THEOS_MAKE_PATH)/library.mk

# The only artifact we need:
#   make clean && make
#   -> .theos/obj/arm64/FFMenu.dylib
#
# Then:  python tools/package_ipa.py <decrypted.ipa> <FFMenu.dylib>
after-install::
	install.exec "killall -9 FreeFire || true"