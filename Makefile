# rtlsim - build and test. `make -j` builds; `make test` runs the tests.
CXX ?= g++
CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++20 -Wall -Wno-unused-parameter -MMD -MP
CPPFLAGS += -Isrc/shim -Ithird_party/mame/mcs51 -Isrc -I$(BUILD)
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -MMD -MP

# RTLPlayground checkout: register names for traces come from its headers,
# and its patched SDCC builds the ISA/bank tests.
RTLPG ?= $(HOME)/src/RTLPlayground
SDCC_DIR ?= $(firstword $(wildcard $(RTLPG)/toolchain/sdcc-*/bin))

BUILD = build
MAME_SRC = $(addprefix third_party/mame/mcs51/,i8051.cpp mcs51ops.cpp i8052.cpp dw8051.cpp mcs51dasm.cpp)
SIM_SRC = $(wildcard src/cpu/*.cpp) $(wildcard src/sys/*.cpp) $(wildcard src/net/*.cpp) $(wildcard src/fabric/*.cpp) $(wildcard src/ctl/*.cpp)
C_SRC = src/sys/flash_dev.c
MAIN_SRC = src/main.cpp

MAME_OBJ = $(MAME_SRC:%.cpp=$(BUILD)/%.o)
SIM_OBJ = $(SIM_SRC:%.cpp=$(BUILD)/%.o)
MAIN_OBJ = $(MAIN_SRC:%.cpp=$(BUILD)/%.o)
C_OBJ = $(C_SRC:%.c=$(BUILD)/%.o)

all: $(BUILD)/rtlsim

$(BUILD)/rtlsim: $(MAIN_OBJ) $(SIM_OBJ) $(MAME_OBJ) $(C_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/regnames.inc: tools/regnames.py $(wildcard $(RTLPG)/rtl837x_regs.h)
	@mkdir -p $(dir $@)
	python3 tools/regnames.py $(RTLPG)/rtl837x_regs.h > $@
$(BUILD)/src/sys/switch_core.o: $(BUILD)/regnames.inc

# A firmware image for tests/fw: the newest RTLPlayground build for the bench board,
# or another machine's with its board file, e.g. the 8x:
#   make test FW_MACHINE=KEEPLINK_KP_9000_9XHPML_V1_2 FW_BOARD=boards/keeplink_kp_9000_9xhpml_v1_2.json
FW_MACHINE ?= SWGT024_V2_0_UNMANAGED
FW_IMAGE ?= $(firstword $(shell ls -t $(RTLPG)/output/$(FW_MACHINE)-compact/rtlplayground-v*-compact.bin 2>/dev/null))
FW_BOARD ?=
FWB = $(if $(FW_BOARD),--board $(FW_BOARD))

# ... and its boot image (the loader and the application), for test_early_reset
FW_BOOT_IMAGE ?= $(firstword $(shell ls -t $(RTLPG)/output/$(FW_MACHINE)-compact/rtlplayground-boot-v*-compact.bin 2>/dev/null))

# the flash model's own test, on the host
$(BUILD)/test_flash_dev: tests/flash/test_flash_dev.c src/sys/flash_dev.c src/sys/flash_dev.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -fsanitize=address,undefined -Isrc -o $@ tests/flash/test_flash_dev.c src/sys/flash_dev.c

test: $(BUILD)/rtlsim $(BUILD)/test_flash_dev
	$(BUILD)/test_flash_dev
	$(MAKE) -C tests/isa RTLSIM=$(abspath $(BUILD)/rtlsim) SDCC_DIR=$(SDCC_DIR)
	$(MAKE) -C tests/bank RTLSIM=$(abspath $(BUILD)/rtlsim) SDCC_DIR=$(SDCC_DIR)
	$(MAKE) -C tests/flashbusy RTLSIM=$(abspath $(BUILD)/rtlsim) SDCC_DIR=$(SDCC_DIR)
	python3 tests/flashbusy/test_busy_control.py --rtlsim $(BUILD)/rtlsim
	python3 tests/cov/test_cov.py --rtlsim $(BUILD)/rtlsim
ifneq ($(FW_IMAGE),)
	python3 tests/fw/test_control.py $(FW_IMAGE) $(FWB) --rtlsim $(BUILD)/rtlsim
ifneq ($(FW_BOOT_IMAGE),)
	python3 tests/fw/test_early_reset.py $(FW_BOOT_IMAGE) $(FWB) --rtlsim $(BUILD)/rtlsim
endif
else
	@echo "fw: no firmware image (set FW_IMAGE or build RTLPlayground), skipped"
endif

# rtlsim --control on another image and board (the map next to the image):
#   make test-control CTL_IMAGE=.../rtlplayground-boot-...-KEEPLINK_KP_9000_9XHPML_V1_2-compact.bin \
#        CTL_BOARD=boards/keeplink_kp_9000_9xhpml_v1_2.json
CTL_IMAGE ?= $(FW_IMAGE)
CTL_BOARD ?=
test-control: $(BUILD)/rtlsim
	python3 tests/fw/test_control.py $(CTL_IMAGE) $(if $(CTL_BOARD),--board $(CTL_BOARD)) --rtlsim $(BUILD)/rtlsim

# The early-reset hang (rtlsim --control) on another boot image and board: ER_EXPECT
# boot for a firmware that does the switch init before its main loop, hang for
# one that gets to its prompt without it (^R ^R there hangs the chip):
#   make test-early-reset ER_IMAGE=... ER_BOARD=boards/keeplink_kp_9000_9xhpml_v1_2.json ER_EXPECT=hang
ER_IMAGE ?= $(FW_BOOT_IMAGE)
ER_BOARD ?=
ER_EXPECT ?= boot
test-early-reset: $(BUILD)/rtlsim
	python3 tests/fw/test_early_reset.py $(ER_IMAGE) $(if $(ER_BOARD),--board $(ER_BOARD)) --expect $(ER_EXPECT) --rtlsim $(BUILD)/rtlsim

# The firmware's own tests (old vs new, the network, the flash matrix, web
# updates, power cuts, the VM booting the image) are in rtlpg-tests
# (github.com/sirmick/rtlpg-tests), beside this checkout: make -C ../rtlpg-tests core.

# The switch as a small VM (tools/vm/README.md): build/vm/rtlsw.qcow2
# (rtlpg-tests' make vm boots it under QEMU).
vm:
	tools/vm/mkvm.sh $(FW_IMAGE)

# The bank check over a firmware boot (slower: every instruction is watched).
FW_MAP ?= $(dir $(FW_IMAGE))rtlplayground.map
check-banks: $(BUILD)/rtlsim
	$(BUILD)/rtlsim --image $(FW_IMAGE) --check-banks $(FW_MAP) --cycles 600000000 --quiet < /dev/null > /dev/null; \
		test $$? -ne 4

clean:
	rm -rf $(BUILD)
	$(MAKE) -C tests/isa clean
	$(MAKE) -C tests/bank clean
	$(MAKE) -C tests/flashbusy clean

-include $(shell find $(BUILD) -name '*.d' 2>/dev/null)

.PHONY: all test test-control test-early-reset vm check-banks clean
