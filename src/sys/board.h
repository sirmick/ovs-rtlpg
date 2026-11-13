// board.h - the hardware around the chip that the firmware image doesn't
// describe: the chip's identity, the flash chip, the front ports and what
// is wired to the SFP cages. Built in for the bench board, or read from a
// board file (boards/*.json, DESIGN.md "Board JSON"):
//
//   { "name": "swgt024_v2_0_unmanaged", "chip": "RTL8372", "revision": "0x68",
//     "uuid": "0xc8540700", "lot": "0x0f000000",
//     "flash": { "size": "512K", "jedec": "ef40" },
//     "ports": [ { "phys": 4, "front": "1" }, ...,
//       { "phys": 8, "front": "5", "sfp": { "slot": 1, "detect_gpio": 30,
//         "los_gpio": 37, "i2c": [39, 40], "module": "sfp-10g.bin" } } ] }
//
// tools/mkboard.py drafts one from RTLPlayground's machine sources; boards/
// has a draft for every machine there. Fields rtlsim doesn't use yet
// ("model", "factory_mac_at", "reset_gpio", "chips") are ignored.
//
// "i2c" is [SDA pin, SCL pin], as RTLPlayground's machine files give them
// (I2CBUS() in machine/machine_defs.h); the SDA pin picks the bus. A
// module path is relative to the board file.
#ifndef RTLSIM_BOARD_H
#define RTLSIM_BOARD_H

#include "emu.h"

#include <string>
#include <vector>

// What the chip reports about itself: the type, the revision, and the UUID
// and lot number the firmware makes its default MAC from.
struct chip_identity {
	bool rtl8373 = false;
	bool n_variant = false;
	u8 revision = 0x68;
	u32 uuid = 0xc8540700;
	u32 lot_no = 0x0f000000;
};

struct board_sfp {
	unsigned slot;		// the firmware's slot number, 1-based
	unsigned phys;		// physical port of the cage
	u8 detect_gpio;		// low: a module is in
	u8 los_gpio;		// high: no light (NO_GPIO: not wired)
	unsigned i2c_bus;	// SDA bus of its EEPROM
	std::string module;	// EEPROM file of the module in it at power-on
};

struct board_port {
	unsigned phys;
	std::string front;	// the label on the case
};

struct board {
	static constexpr u8 NO_GPIO = 0xff;
	std::string name;
	chip_identity id;
	u32 flash_size = 0x80000;
	u8 jedec_mfr = 0xef, jedec_type = 0x40;
	std::vector<board_port> ports;
	std::vector<board_sfp> sfp;

	const board_sfp *cage(unsigned slot) const
	{
		for (auto &c : sfp)
			if (c.slot == slot)
				return &c;
		return nullptr;
	}
};

// The bench SWTG024AS (RTL8372 rev 0x68, MAC 06:0f:00:c8:54:07), as
// tools/mkboard.py drafts it from RTLPlayground's
// machine/SWGT024_V2_0_UNMANAGED.c (pcb/PCB_SWTG024AS_V2_0_17452__UNMANAGED.c):
// cage 1, front port 5, is physical port 8 (detect GPIO30, LOS GPIO37,
// I2C SDA4); cage 2, front port 6, is physical port 3 (GPIO50, GPIO51, SDA3).
// Same as boards/swgt024_v2_0_unmanaged.json.
board board_swtg024as();

// Reads a board file; throws std::runtime_error saying what is wrong.
board load_board(const std::string &path);

#endif
