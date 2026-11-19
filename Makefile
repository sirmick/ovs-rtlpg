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

# A firmware image for tests/fw: the newest RTLPlayground build for the bench board
FW_IMAGE ?= $(firstword $(shell ls -t $(RTLPG)/output/SWGT024_V2_0_UNMANAGED-compact/rtlplayground-v*-compact.bin 2>/dev/null))

# ... and its boot image (the loader and the application), for test_early_reset
FW_BOOT_IMAGE ?= $(firstword $(shell ls -t $(RTLPG)/output/SWGT024_V2_0_UNMANAGED-compact/rtlplayground-boot-v*-compact.bin 2>/dev/null))

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
	python3 tests/oldnew/test_known.py
ifneq ($(FW_IMAGE),)
	python3 tests/fw/test_boot.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/net/test_nic.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/fabric/test_flows.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/flash/test_boot0.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/fw/test_control.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/fw/test_nic_ring.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/fw/test_lacp.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
ifneq ($(FW_BOOT_IMAGE),)
	python3 tests/fw/test_early_reset.py $(FW_BOOT_IMAGE) --rtlsim $(BUILD)/rtlsim
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

# Old vs new (tests/oldnew/README.md): the reference scenarios on two boot
# images in rtlsim --control, compared on results; < 1 min in parallel.
#   make oldnew OLD=... NEW=... [BOARD=boards/....json] [EXPECT=file] [ONLY="boot cli"]
# OLD defaults to the reference build of fork main for the 8x in
# ~/src/rtlpg-ref (tests/oldnew/README.md), NEW to OLD: old against old, which
# must match on every scenario.
OLD ?= $(firstword $(wildcard $(HOME)/src/rtlpg-ref/output/KEEPLINK_KP_9000_9XHPML_V1_2-compact/rtlplayground-boot-v*.bin))
NEW ?= $(OLD)
BOARD ?= $(if $(findstring KEEPLINK_KP_9000_9XHPML,$(OLD)),boards/keeplink_kp_9000_9xhpml_v1_2.json)
oldnew: $(BUILD)/rtlsim
	python3 tests/oldnew/oldnew.py --old $(OLD) --new $(NEW) $(if $(BOARD),--board $(BOARD)) \
		$(if $(EXPECT),--expect $(EXPECT)) $(if $(ONLY),--only $(ONLY)) \
		$(if $(NEW_PAYLOAD),--new-payload $(NEW_PAYLOAD)) $(if $(CHECKS),--checks) \
		$(if $(KNOWN),--known $(KNOWN)) --rtlsim $(BUILD)/rtlsim

# The bench's HTTP client against a scripted switch (no rtlsim): it honours
# the switch's TCP window (tests/oldnew/test_bench.py).
test-bench:
	python3 tests/oldnew/test_bench.py

# The new-only acceptance checks (tests/oldnew/checks.py) on one image, IMAGE
# (default NEW): LACPDU gaps, BPDU hello spacing, long steps (`health tasks`),
# the flash after an upload. KNOWN=tests/oldnew/known-failures marks today's.
IMAGE ?= $(NEW)
CHECKS_BOARD = $(if $(filter command line environment,$(origin BOARD)),$(BOARD),$(if $(findstring KEEPLINK_KP_9000_9XHPML,$(IMAGE)),boards/keeplink_kp_9000_9xhpml_v1_2.json))
oldnew-checks: $(BUILD)/rtlsim
	python3 tests/oldnew/checks.py --image $(IMAGE) $(if $(CHECKS_BOARD),--board $(CHECKS_BOARD)) \
		$(if $(ONLY),--only $(ONLY)) $(if $(KNOWN),--known $(KNOWN)) --rtlsim $(BUILD)/rtlsim

# The firmware against Linux's TCP/IP in a network namespace (needs sudo;
# real time, so a few seconds): ping, the web UI, and what each frame costs.
# Once over a tap on the --nic socket, once through a private OVS bridge;
# then hosts on three front ports, switched as the firmware configures it;
# then two switches cabled together; then IGMP snooping against silicon.
test-net: $(BUILD)/rtlsim
	python3 tests/net/test_net.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim --via tap
	python3 tests/net/test_net.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim --via ovs
	python3 tests/net/test_switching.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/net/test_topo.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/net/test_igmp.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim

# A firmware update through the web UI on a persistent flash, with a power
# cut in the middle of the install (needs sudo; real time, ~1.5 min).
test-update: $(BUILD)/rtlsim
	python3 tests/net/test_update.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim

# Flash and board tests that take a few seconds more, and an image matrix
# ($RTLSIM_IMAGES, ~/rtlsim/images): the PoE board loading its RTL8238B,
# standard -> compact on the 2 and 4 MiB boards (--sweep for power cuts), and
# a web update through the loader on every board of the matrix.
test-flash: $(BUILD)/rtlsim
	python3 tests/flash/test_pse.py --rtlsim $(BUILD)/rtlsim
	python3 tests/flash/test_migrate.py --rtlsim $(BUILD)/rtlsim
	python3 tests/flash/test_loader.py --rtlsim $(BUILD)/rtlsim
	python3 tests/flash/test_loader_update.py --rtlsim $(BUILD)/rtlsim

# A power cut at every step of a firmware install (rtlsim --powercut-after;
# no sudo; minutes, in parallel). FW_OLD is what the switch runs before.
FW_OLD ?= $(FW_IMAGE)
test-powercut: $(BUILD)/rtlsim
	python3 tests/flash/test_powercut.py $(FW_IMAGE) --old $(FW_OLD) --rtlsim $(BUILD)/rtlsim

# The switch as a small VM (tools/vm/README.md): build/vm/rtlsw.qcow2, and
# a test of it under QEMU that needs no root.
vm:
	tools/vm/mkvm.sh $(FW_IMAGE)
test-vm:
	python3 tests/vm/test_vm.py

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

.PHONY: all test test-control test-early-reset oldnew oldnew-checks test-bench test-net test-update test-flash test-powercut vm test-vm check-banks clean
