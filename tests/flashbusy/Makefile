# The flash busy rule (rtlsim --check-flash-busy): t_busy.c starts a sector
# erase and calls into BANK1. busy_bad calls at once, from the bank window
# while the chip erases: flagged, exit 6. busy_good waits in the home bank
# for the erase to end first: clean, and its answer is the same.
RTLSIM ?= ../../build/rtlsim
SDCC_DIR ?= $(firstword $(wildcard $(HOME)/src/RTLPlayground/toolchain/sdcc-*/bin))
SDCC ?= $(SDCC_DIR)/sdcc
SDAS ?= $(SDCC_DIR)/sdas8051
OUT = out
LINK = -Wl-bHOME=0x00000 -Wl-bBANK1=0x14000 -Wl-r
RUN = $(RTLSIM) --check-flash-busy --cycles 50000000

all: $(OUT)/busy_bad.ok $(OUT)/busy_good.ok
	@echo "flashbusy: a call into a bank during an erase is flagged; waiting in the home bank first is clean"

$(OUT)/b1_busy.rel: b1_busy.c | $(OUT)
	$(SDCC) -mmcs51 --xram-size 49151 -c -o $@ $< > /dev/null
$(OUT)/bad.rel: t_busy.c ../isa/common.h | $(OUT)
	$(SDCC) -mmcs51 --xram-size 49151 -c -o $@ $< > /dev/null
$(OUT)/good.rel: t_busy.c ../isa/common.h | $(OUT)
	$(SDCC) -mmcs51 --xram-size 49151 -DWAIT_IN_COMMON -c -o $@ $< > /dev/null
$(OUT)/crtbank.rel: ../bank/crtbank.asm | $(OUT)
	$(SDAS) -plosgff -o $@ $<
$(OUT)/busy_%.ihx: $(OUT)/%.rel $(OUT)/b1_busy.rel $(OUT)/crtbank.rel
	$(SDCC) -mmcs51 --xram-size 49151 $(LINK) -o $@ $^ > /dev/null
$(OUT)/%.bin: $(OUT)/%.ihx
	python3 ../../tools/mkimage.py $< $@

$(OUT)/busy_bad.ok: $(OUT)/busy_bad.bin $(RTLSIM)
	$(RUN) --image $< > $(OUT)/busy_bad.txt 2> $(OUT)/busy_bad.err; \
		test $$? -eq 6 || { cat $(OUT)/busy_bad.err; echo "flashbusy: the call into BANK1 during the erase was not flagged"; exit 1; }
	grep -q 'flash busy (sector erase 070000.*instruction fetch.*psbank=01' $(OUT)/busy_bad.err
	@touch $@
$(OUT)/busy_good.ok: $(OUT)/busy_good.bin $(RTLSIM)
	$(RUN) --image $< > $(OUT)/busy_good.txt 2> $(OUT)/busy_good.err || { cat $(OUT)/busy_good.err; exit 1; }
	! grep -q 'flash busy' $(OUT)/busy_good.err
	printf 'erase\nb1=00000031\npsbank=00000000\ndone\n' | diff -u - $(OUT)/busy_good.txt
	@touch $@
$(OUT):
	mkdir -p $@
clean:
	rm -rf $(OUT)
.PHONY: all clean
