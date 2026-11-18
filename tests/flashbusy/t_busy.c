/* t_busy.c - the flash busy rule: start a sector erase, then call into
 * BANK1. Built twice: as is, the call runs from the bank window while the
 * chip erases (rtlsim --check-flash-busy flags it, exit 6); with
 * -DWAIT_IN_COMMON, the home bank polls the status register until the erase
 * is over first, as RTLPlayground's flash_sector_erase() does (clean). */
#include "../isa/common.h"

__sfr __at(0x96) PSBANK;
__sfr __at(0x80) FL_EXEC;
__sbit __at(0x82) FL_GO;
__sbit __at(0x80) FL_BUSY;
__sfr __at(0x9e) FL_TCONF;
__sfr __at(0xb1) FL_CMD_R;
__sfr __at(0xb2) FL_CMD;
__sfr __at(0xab) FL_ADDR16;
__sfr __at(0xaa) FL_ADDR8;
__sfr __at(0xa9) FL_ADDR0;
__sfr __at(0xac) FL_DATA0;

uint8_t b1_value(uint8_t x) __banked;

/* the sector to erase: one nothing runs from (a test may poke another) */
__xdata uint32_t erase_addr = 0x70000;

static void go(void)
{
	FL_GO = 1;
	while (FL_BUSY)
		;
}

static uint8_t status(void)
{
	FL_TCONF = 0x11;	/* read, no address, 1 byte */
	FL_CMD_R = 0x05;
	go();
	return FL_DATA0;
}

void erase_start(void)
{
	FL_TCONF = 0x18;	/* write enable: write, no address */
	FL_CMD = 0x06;
	go();
	FL_TCONF = 0x08;	/* sector erase: write, address */
	FL_CMD = 0x20;
	FL_ADDR16 = erase_addr >> 16;
	FL_ADDR8 = erase_addr >> 8;
	FL_ADDR0 = erase_addr;
	go();
}

void wait_idle(void)
{
	while (status() & 1)
		;
}

int main(void)
{
	out_s("erase\n");
	erase_start();
#ifdef WAIT_IN_COMMON
	wait_idle();
#endif
	out_kv("b1", b1_value(7));
	out_kv("psbank", PSBANK);
	out_s("done\n");
	halt();
	return 0;
}
