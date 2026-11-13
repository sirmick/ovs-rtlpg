# Differential ISA tests: each t_*.c built with SDCC (one image per memory
# model) and run on rtlsim, and built for the host; the outputs must match.
#
#   make -C tests/isa RTLSIM=.../rtlsim [SDCC=...]
RTLSIM ?= ../../build/rtlsim
SDCC_DIR ?= $(firstword $(wildcard $(HOME)/src/RTLPlayground/toolchain/sdcc-*/bin))
SDCC ?= $(SDCC_DIR)/sdcc
HOSTCC ?= cc
MKIMAGE = ../../tools/mkimage.py

TESTS = $(basename $(wildcard t_*.c))
MODELS = small medium large stackauto
FLAGS_small = --model-small
FLAGS_medium = --model-medium
FLAGS_large = --model-large
FLAGS_stackauto = --model-large --stack-auto

OUT = out
RESULTS = $(foreach t,$(TESTS),$(foreach m,$(MODELS),$(OUT)/$(t).$(m).ok))

all: $(RESULTS)
	@echo "isa: $(words $(RESULTS)) runs, all match the host"

$(OUT)/%.host.txt: %.c common.h | $(OUT)
	$(HOSTCC) -O1 -DHOST -o $(OUT)/$*.host $<
	$(OUT)/$*.host > $@

define model_rules
$(OUT)/%.$(1).ihx: %.c common.h | $(OUT)
	$(SDCC) -mmcs51 $(FLAGS_$(1)) --xram-size 49151 -o $$@ $$< > /dev/null

$(OUT)/%.$(1).bin: $(OUT)/%.$(1).ihx
	python3 $(MKIMAGE) $$< $$@

$(OUT)/%.$(1).ok: $(OUT)/%.$(1).bin $(OUT)/%.host.txt $(RTLSIM)
	$(RTLSIM) --image $$< --cycles 200000000 > $(OUT)/$$*.$(1).txt
	diff -u $(OUT)/$$*.host.txt $(OUT)/$$*.$(1).txt
	@touch $$@
endef
$(foreach m,$(MODELS),$(eval $(call model_rules,$(m))))

$(OUT):
	mkdir -p $@

clean:
	rm -rf $(OUT)

.PHONY: all clean
.SECONDARY:
