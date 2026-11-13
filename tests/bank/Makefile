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

all: $(OUT)/bank.ok $(OUT)/constbug.ok
	@echo "bank: 3 banks, cross-bank calls and constants match the host; the bank check catches a misplaced constseg"

$(OUT)/%.rel: %.c bank.h ../isa/common.h | $(OUT)
	$(SDCC) -mmcs51 --xram-size 49151 -c -o $@ $< > /dev/null
$(OUT)/crtbank.rel: crtbank.asm | $(OUT)
	$(SDAS) -plosgff -o $@ $<
$(OUT)/bank.ihx: $(SRC:%.c=$(OUT)/%.rel) $(OUT)/crtbank.rel
	$(SDCC) -mmcs51 --xram-size 49151 $(LINK) -o $@ $^ > /dev/null
$(OUT)/%.bin: $(OUT)/%.ihx
	python3 ../../tools/mkimage.py $< $@
$(OUT)/bank.host.txt: $(SRC) bank.h | $(OUT)
	$(HOSTCC) -O1 -DHOST -o $(OUT)/bank.host $(SRC)
	$(OUT)/bank.host > $@
$(OUT)/bank.ok: $(OUT)/bank.bin $(OUT)/bank.host.txt $(RTLSIM)
	$(RTLSIM) --image $< --cycles 200000000 --check-banks $(OUT)/bank.map > $(OUT)/bank.txt
	diff -u $(OUT)/bank.host.txt $(OUT)/bank.txt
	@touch $@

# constbug.c has codeseg BANK3 but constseg BANK2: the checker must say so
# (exit 4) and name the reading function.
$(OUT)/constbug.ihx: $(OUT)/t_constbug.rel $(OUT)/b1.rel $(OUT)/b2.rel $(OUT)/b3.rel $(OUT)/constbug.rel $(OUT)/crtbank.rel
	$(SDCC) -mmcs51 --xram-size 49151 $(LINK) -o $@ $^ > /dev/null
$(OUT)/constbug.ok: $(OUT)/constbug.bin $(RTLSIM)
	$(RTLSIM) --image $< --cycles 200000000 --check-banks $(OUT)/constbug.map > /dev/null 2> $(OUT)/constbug.err; \
		test $$? -eq 4 || { cat $(OUT)/constbug.err; echo "bank check missed the misplaced constseg"; exit 1; }
	grep -q '_constbug_sum' $(OUT)/constbug.err
	@touch $@
$(OUT):
	mkdir -p $@
clean:
	rm -rf $(OUT)
.PHONY: all clean
