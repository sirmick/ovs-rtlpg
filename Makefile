# rtlsim - build and test. `make -j` builds; `make test` runs the tests.
CXX ?= g++
CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++20 -Wall -Wno-unused-parameter -MMD -MP
CPPFLAGS += -Isrc/shim -Ithird_party/mame/mcs51 -Isrc

BUILD = build
MAME_SRC = $(addprefix third_party/mame/mcs51/,i8051.cpp mcs51ops.cpp i8052.cpp dw8051.cpp mcs51dasm.cpp)
SIM_SRC = $(wildcard src/cpu/*.cpp) $(wildcard src/sys/*.cpp)
MAIN_SRC = src/main.cpp

MAME_OBJ = $(MAME_SRC:%.cpp=$(BUILD)/%.o)
SIM_OBJ = $(SIM_SRC:%.cpp=$(BUILD)/%.o)
MAIN_OBJ = $(MAIN_SRC:%.cpp=$(BUILD)/%.o)

all: $(BUILD)/rtlsim

$(BUILD)/rtlsim: $(MAIN_OBJ) $(SIM_OBJ) $(MAME_OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^

$(BUILD)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

test: $(BUILD)/rtlsim
	$(MAKE) -C tests/isa RTLSIM=$(abspath $(BUILD)/rtlsim)
	$(MAKE) -C tests/bank RTLSIM=$(abspath $(BUILD)/rtlsim)

clean:
	rm -rf $(BUILD)
	$(MAKE) -C tests/isa clean
	$(MAKE) -C tests/bank clean

-include $(shell find $(BUILD) -name '*.d' 2>/dev/null)

.PHONY: all test clean
