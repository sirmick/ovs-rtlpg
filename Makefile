# rtlsim - build and test. `make -j` builds; `make test` runs the tests.
CXX ?= g++
CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++20 -Wall -Wno-unused-parameter -MMD -MP
CPPFLAGS += -Isrc/shim -Ithird_party/mame/mcs51 -Ithird_party/rtlplayground -Isrc -I$(BUILD)
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -MMD -MP

# RTLPlayground checkout: register names for traces come from its headers
RTLPG ?= $(HOME)/src/RTLPlayground

BUILD = build
MAME_SRC = $(addprefix third_party/mame/mcs51/,i8051.cpp mcs51ops.cpp i8052.cpp dw8051.cpp mcs51dasm.cpp)
SIM_SRC = $(wildcard src/cpu/*.cpp) $(wildcard src/sys/*.cpp) $(wildcard src/net/*.cpp) $(wildcard src/fabric/*.cpp)
C_SRC = third_party/rtlplayground/flash_dev.c
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

test: $(BUILD)/rtlsim
	$(MAKE) -C tests/isa RTLSIM=$(abspath $(BUILD)/rtlsim)
	$(MAKE) -C tests/bank RTLSIM=$(abspath $(BUILD)/rtlsim)
ifneq ($(FW_IMAGE),)
	python3 tests/fw/test_boot.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
	python3 tests/net/test_nic.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim
else
	@echo "fw: no firmware image (set FW_IMAGE or build RTLPlayground), skipped"
endif

# The firmware against Linux's TCP/IP in a network namespace (needs sudo;
# real time, so a few seconds): ping, the web UI, and what each frame costs.
# Once over a tap on the --nic socket, once through a private OVS bridge.
test-net: $(BUILD)/rtlsim
	python3 tests/net/test_net.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim --via tap
	python3 tests/net/test_net.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim --via ovs

# A firmware update through the web UI on a persistent flash, with a power
# cut in the middle of the install (needs sudo; real time, ~1.5 min).
test-update: $(BUILD)/rtlsim
	python3 tests/net/test_update.py $(FW_IMAGE) --rtlsim $(BUILD)/rtlsim

# The bank check over a firmware boot (slower: every instruction is watched).
FW_MAP ?= $(dir $(FW_IMAGE))rtlplayground.map
check-banks: $(BUILD)/rtlsim
	$(BUILD)/rtlsim --image $(FW_IMAGE) --check-banks $(FW_MAP) --cycles 600000000 --quiet < /dev/null > /dev/null; \
		test $$? -ne 4

clean:
	rm -rf $(BUILD)
	$(MAKE) -C tests/isa clean
	$(MAKE) -C tests/bank clean

-include $(shell find $(BUILD) -name '*.d' 2>/dev/null)

.PHONY: all test test-net test-update check-banks clean
