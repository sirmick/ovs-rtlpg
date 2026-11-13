// system.h - the RTL837x around its 8051: flash, code banking, xdata, and
// the SFR bus to the flash controller and the switch core.
//
// Code memory, as RTLPlayground's imagebuilder lays out the image: the flash
// starts with a 2-byte header, then the common code (CPU 0x0000-0x3ffd at
// flash 0x0002), which reset copies to code RAM as the boot ROM does. CPU
// 0x4000-0xffff is a window into the flash chosen by PSBANK, by the rule in
// RTLPlayground's test/test_loader.c: bank b = PSBANK (1 if bit 6 is set),
// flash address ((b-1)*0xc000 + addr) modulo the chip size; PSBANK 0 wraps
// to the top of the chip.
#ifndef RTLSIM_SYSTEM_H
#define RTLSIM_SYSTEM_H

#include "cpu/rtl837x_cpu.h"
#include "sys/flashctl.h"
#include "sys/switch_core.h"
#include "sys/tables.h"

#include <cstdio>
#include <deque>
#include <memory>
#include <string>
#include <vector>

// What the chip reports about itself. Defaults: the SWTG024AS on the bench
// (RTL8372, revision 0x68, MAC 06:0f:00:c8:54:07 from its UUID and lot
// number). TODO: from the board JSON.
struct chip_identity {
	bool rtl8373 = false;
	bool n_variant = false;
	u8 revision = 0x68;
	u32 uuid = 0xc8540700;
	u32 lot_no = 0x0f000000;
};

class rtl837x_system
{
public:
	rtl837x_system(const std::vector<u8> &image, u32 flash_size, const chip_identity &id = {});

	void reset();
	// Run about `cycles` machine cycles. Returns false once halted.
	bool run(int cycles);
	bool halted() const { return m_halted; }

	rtl837x_cpu &cpu() { return m_cpu; }
	flash_controller &flash() { return *m_flash; }
	switch_core &core() { return m_core; }
	switch_tables &tables() { return m_tables; }
	u8 code_read(u16 addr);			// as the CPU sees it now
	u32 code_flash_addr(u16 addr, u8 psbank) const;

	void uart_input(const std::string &s) { m_rx.insert(m_rx.end(), s.begin(), s.end()); }
	bool uart_idle() const { return m_rx.empty(); }

	FILE *uart_out = stdout;
	bool trace_insn = false;
	bool trace_sfr = false;
	bool stop_on_halt = true;		// "sjmp ." stops the run (tests)
	u64 instructions = 0;			// counted only while tracing
	u64 cycles = 0;
	u64 resets = 0;

private:
	u8 sfr_r(u8 a);
	void sfr_w(u8 a, u8 d);
	u8 xdata_r(offs_t a);
	void xdata_w(offs_t a, u8 d);
	void on_insn(offs_t pc);
	void map_code_pages();
	bool at_halt();
	void seed_chip();

	rtl837x_cpu m_cpu;
	std::unique_ptr<flash_controller> m_flash;
	switch_core m_core;
	switch_tables m_tables{m_core};
	std::vector<u8> m_code_ram;		// the common code, 0x0000-0x3fff
	std::vector<u8> m_xdata;
	u8 m_sfr[256] = {};			// SFRs nobody models yet
	bool m_sfr_seen[256] = {};
	u8 m_map_flash = 1;			// SFR 0x97: xdata 0x4000-0xbfff reads the flash
	bool m_halted = false;
	bool m_reset_pending = false;
	std::deque<u8> m_rx;
	std::unique_ptr<util::disasm_interface> m_dasm;
	chip_identity m_id;
};

#endif
