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
#include <functional>
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
	u8 cmd_r() const { return m_sfr[0xb1]; }	// the read command (CMD_R)
	bool trace = false;
	// Write protect (control mode's flash wp): write enable (0x06) is
	// ignored, so WEL stays clear and no program or erase happens.
	bool write_protect = false;
	FILE *op_log = nullptr;		// each erase (E addr) and program (P addr len) done
	// The security registers (read command 0x48 at addr reads secreg[addr]),
	// erased (0xff) past its end. A genuine vendor-programmed chip holds data
	// its stock firmware checks; --flash-secreg loads it from a file.
	std::vector<u8> secreg;

	// Timing (timing = true; else everything is done at once), in machine
	// cycles (31.25 M a second), from the bench SWTG024AS's W25Q40: a page
	// program command keeps the chip BUSY ~1.9 ms, a 4K sector erase ~45 ms
	// (datasheet typical), a 64K block ~150 ms; a transfer keeps the
	// controller's EXEC busy ~8 us (reads ~500 KiB/s). `now` is the clock.
	//
	// The 1.9 ms is not per command: the controller programs at most 4
	// bytes a command, and RTLPlayground's flash_write_bytes() sends write
	// enable + page program per 4 bytes, waiting for BUSY each time; the
	// 1.9 ms was one 256-byte write, 64 commands. A program of n bytes takes
	// tBP1 + (n - 1) tBP2 (W25Q BV/DV/JV datasheet: 30 + 2.5 us typical, 50
	// + 12 max); tPP (0.4 ms typical, 0.7 max) is for a whole page, which
	// this firmware never sends. With program_first_ns set, a program is
	// timed so (capped at page_max_ns); else it takes `program`, whatever its
	// length. Measured on the 8x (doc/sched/silicon.md): a 4-byte
	// flash_write_bytes() 49-55 us, a 256-byte one 1.71 ms, ~27 us a command
	// all in - faster than the datasheet's tBP1 alone. Control mode fits it
	// with a 1 us command and a 10.5 + (n - 1) 1 us program: 43 us and
	// 1.64 ms for schedtest's flashtime (its fixed part is short of the 8x's).
	// A transfer that reads data (0x03, 0x0b, 0x3b, 0xbb) keeps EXEC busy
	// `transfer`; every other command (status, write enable, program,
	// erase, ids) `command`, the same unless set.
	struct timings {
		u64 program = 59375, sector_erase = 1406250, block_erase = 4687500, transfer = 250, command = 250;
		u64 program_first_ns = 0, program_byte_ns = 0, page_max_ns = 0;
		u64 program_cycles(unsigned n) const
		{
			if (!program_first_ns)
				return program;
			u64 ns = program_first_ns + (n ? n - 1 : 0) * program_byte_ns;
			if (page_max_ns && ns > page_max_ns)
				ns = page_max_ns;
			return ns / 32;		// 31.25 cycles a µs: 32 ns a cycle
		}
	} t;
	bool timing = false;
	// The chip's JEDEC id (manufacturer, memory type, capacity: 9Fh's bytes).
	u32 jedec() const { return u32(m_mfr) << 16 | m_type << 8 | m_cap; }
	// The datasheet's typical times for a part we know by its JEDEC id, into
	// t (control mode's, sys/flashctl.cpp's parts[]); its name, or null and t
	// unchanged.
	const char *part_timings();
	std::function<u64()> now;

	// Power cuts: let n more erase/program operations through, then drop
	// the next one and every later one; power_cut() says it happened.
	void power_cut_after(long n) { fd_fail_after(&m_dev, int(n)); }
	bool power_cut() const { return m_dev.powercut; }
	unsigned ops() const { return m_dev.erases + m_dev.writes; }
	// The chip is busy with an erase or a program (with timing): its array
	// can't be read. What it is doing, for reports.
	bool busy() const { return timing && now && now() < m_busy_until; }
	struct operation {
		char kind = 0;		// 'E' sector erase, 'B' block erase, 'P' page program
		u32 addr = 0, len = 0;
		u64 start = 0, end = 0;
		std::vector<u8> before;	// the bytes it changes, as they were (keep_before)
	};
	const operation &last_op() const { return m_op; }
	bool keep_before = false;	// keep them, for a partial power cut
	std::function<void()> on_busy;	// an erase or program has started (with timing)

	// Power off at firmware time `cut` and on again: the controller's
	// registers clear and the chip forgets its write enable. An operation in
	// flight is done only in part: the model does it at once, so with
	// keep_before the share of it its time hadn't reached is put back as it
	// was (an erase erased its first part, a program wrote its first bytes);
	// without, it counts as done.
	void power_on(u64 cut);
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
	u64 m_busy_until = 0, m_exec_until = 0;
	operation m_op;
	void begin_op(char kind, u32 addr, u32 len, u64 cycles);
	unsigned m_synced_ops = 0;
	u8 m_status = 0;
};

#endif
