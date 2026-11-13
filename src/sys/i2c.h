// i2c.h - the RTL837x I2C master and what hangs off its buses (SFP EEPROMs).
//
// From RTLPlayground's rtl837x_pins.c sfp_read_block() and machine_defs.h:
//   I2C_IN (0x0420) bits 7:0: the register to start at.
//   I2C_CTRL (0x0418): bit 0 starts a transfer and reads 1 while it runs
//   (here it is done at once), bit 1 reads 1 if it failed (no device acked);
//   bits 9:3 the 7-bit device address, bits 12:10 the SDA bus, bits 15:13
//   the SCL bus, bits 19:16 the byte count - 1, bit 20 a one-byte register
//   address.
//   I2C_OUT (0x0424..0x0430): the bytes read, byte i in register 0x0424 +
//   (i & ~3), bits 8*(i & 3) + 7..8*(i & 3).
// Buses are told apart by their SDA line (boards share SCL between slots).
// Only reads are modelled; a write fails as if nobody acked.
#ifndef RTLSIM_I2C_H
#define RTLSIM_I2C_H

#include "sys/switch_core.h"

#include <map>
#include <vector>

class i2c_controller
{
public:
	explicit i2c_controller(switch_core &core);

	// A device answering at 7-bit address `dev` on SDA bus `bus` (0-4), with
	// `mem` as its register space (wrapping at its size). Empty: detach.
	void attach(unsigned bus, u8 dev, std::vector<u8> mem);
	bool trace = false;

private:
	u32 execute(u32 ctrl);

	switch_core &m_core;
	std::map<std::pair<unsigned, u8>, std::vector<u8>> m_dev;
};

#endif
