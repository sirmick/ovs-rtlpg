// board.h - the hardware around the chip that the firmware image doesn't
// describe: what is wired to the SFP cages. (DESIGN.md's board JSON; for
// now the boards are built in.)
#ifndef RTLSIM_BOARD_H
#define RTLSIM_BOARD_H

#include "emu.h"

#include <vector>

struct board_sfp {
	unsigned phys;		// physical port of the cage
	u8 detect_gpio;		// low: a module is in
	u8 los_gpio;		// high: no light (NO_GPIO: not wired)
	unsigned i2c_bus;	// SDA bus of its EEPROM
};

struct board {
	static constexpr u8 NO_GPIO = 0xff;
	const char *name;
	std::vector<board_sfp> sfp;	// slot 1, 2, ...
};

// The bench SWTG024AS (machine/pcb/PCB_SWTG024AS_V2_0_17452.c, is_sfp in
// machine/SWGT024_V2_0_UNMANAGED.c): slot 1 is the left cage (J4), physical
// port 8 on SerDes 1; slot 2 the right one (J2), physical port 3 on SerDes 0.
inline board board_swtg024as()
{
	return { "swtg024as", {
		{ 8, 30, board::NO_GPIO, 4 },
		{ 3, 50, board::NO_GPIO, 3 },
	} };
}

#endif
