# Code banking: t_bank.c in HOME, b1-b3.c in BANK1-3, linked and packed as the
# firmware is (RTLPlayground's crtbank.asm, imagebuilder layout via
# tools/mkimage.py); the output must match the host build.
RTLSIM ?= ../../build/rtlsim
SDCC_DIR ?= $(firstword $(wildcard $(HOME)/src/RTLPlayground/toolchain/sdcc-*/bin))
SDCC ?= $(SDCC_DIR)/sdcc
SDAS ?= $(SDCC_DIR)/sdas8051
HOSTCC ?= cc
OUT = out
SRC = t_bank.c b1.c b2.c b3.c
LINK = -Wl-bHOME=0x00000 -Wl-bBANK1=0x14000 -Wl-bBANK2=0x24000 -Wl-bBANK3=0x34000 -Wl-r

all: $(OUT)/bank.ok
	@echo "bank: 3 banks, cross-bank calls and constants match the host"

$(OUT)/%.rel: %.c bank.h ../isa/common.h | $(OUT)
	$(SDCC) -mmcs51 --xram-size 49151 -c -o $@ $< > /dev/null
$(OUT)/crtbank.rel: crtbank.asm | $(OUT)
	$(SDAS) -plosgff -o $@ $<
$(OUT)/bank.ihx: $(SRC:%.c=$(OUT)/%.rel) $(OUT)/crtbank.rel
	$(SDCC) -mmcs51 --xram-size 49151 $(LINK) -o $@ $^ > /dev/null
$(OUT)/bank.bin: $(OUT)/bank.ihx
	python3 ../../tools/mkimage.py $< $@
$(OUT)/bank.host.txt: $(SRC) bank.h | $(OUT)
	$(HOSTCC) -O1 -DHOST -o $(OUT)/bank.host $(SRC)
	$(OUT)/bank.host > $@
$(OUT)/bank.ok: $(OUT)/bank.bin $(OUT)/bank.host.txt $(RTLSIM)
	$(RTLSIM) --image $< --cycles 200000000 > $(OUT)/bank.txt
	diff -u $(OUT)/bank.host.txt $(OUT)/bank.txt
	@touch $@
$(OUT):
	mkdir -p $@
clean:
	rm -rf $(OUT)
.PHONY: all clean
