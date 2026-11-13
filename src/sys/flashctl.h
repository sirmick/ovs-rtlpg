// flashctl.h - the RTL837x SPI flash controller (SFRs), over a modelled
// SPI-NOR chip (sys/flash_dev.h).
//
// From RTLPlayground's rtl837x_flash.c: a transaction is set up in TCONF
// (0x9e) and started with the GO bit (0x80.2); BUSY (0x80.0) reads clear
// at once. TCONF bit 3: a write, the command is in CMD (0xb2), else a read
// with the command in CMD_R (0xb1); bit 4: no address phase; bits 0-2: data
// bytes, in DATA0 (0xac), DATA8, DATA16, DATA24 (0xaf). Address in ADDR16
// (0xab), ADDR8, ADDR0 (0xa9).
#ifndef RTLSIM_FLASHCTL_H
#define RTLSIM_FLASHCTL_H

#include "emu.h"

#include <cstdio>
#include <string>
#include <vector>
extern "C" {
#include "sys/flash_dev.h"
}

class flash_controller
{
public:
	// The chip in memory, loaded with `image`.
	flash_controller(const std::vector<u8> &image, u32 chip_size, u8 jedec_mfr = 0xef, u8 jedec_type = 0x40);
	// The chip is the file at `path`, mapped shared: every program and erase
	// lands in the file at once, as it would survive a power cut. A new file
	// is created with `chip_size` bytes of erased flash and `image` programmed
	// at 0; an existing one is used as it is (its size is the chip size).
	flash_controller(const std::string &path, const std::vector<u8> &image, u32 chip_size,
			 u8 jedec_mfr = 0xef, u8 jedec_type = 0x40);
	~flash_controller();

	static bool owns(u8 sfr);
	u8 sfr_r(u8 sfr);
	void sfr_w(u8 sfr, u8 d);

	flashdev_t &dev() { return m_dev; }
	u32 size() const { return m_dev.size; }
	u8 byte(u32 addr) const { return m_dev.mem[addr & m_dev.addr_mask]; }
	bool trace = false;
	FILE *op_log = nullptr;		// each erase (E addr) and program (P addr len) done

	// Power cuts: let n more erase/program operations through, then drop
	// the next one and every later one; power_cut() says it happened.
	void power_cut_after(long n) { fd_fail_after(&m_dev, int(n)); }
	bool power_cut() const { return m_dev.powercut; }
	unsigned ops() const { return m_dev.erases + m_dev.writes; }
	// A flash file: write what changed since the last call back to it (msync),
	// so a crash or a VM killed without a shutdown can't lose it.
	void sync();

private:
	void execute();
	void set_capacity();

	flashdev_t m_dev;
	u8 m_mfr, m_type, m_cap;
	u8 m_sfr[256] = {};
	bool m_wel = false;
	bool m_mapped = false;
	unsigned m_synced_ops = 0;
	u8 m_status = 0;
};

#endif
