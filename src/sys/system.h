// system.h - the RTL837x around its 8051: flash image, code banking, xdata,
// and the SFR bus to the chip's peripherals.
//
// Code memory, as tools/imagebuilder.c in RTLPlayground lays out the image:
// the flash starts with a 2-byte header, then the common code (CPU
// 0x0000-0x3ffd at flash 0x0002). At reset it is copied to code RAM, as the
// boot ROM does. CPU 0x4000-0xffff is a window into the flash chosen by
// PSBANK: bank b (1..) at flash (b-1)*0xc000 + addr.
#ifndef RTLSIM_SYSTEM_H
#define RTLSIM_SYSTEM_H

#include "cpu/rtl837x_cpu.h"

#include <cstdio>
#include <string>
#include <vector>

class rtl837x_system
{
public:
	explicit rtl837x_system(std::vector<u8> flash);

	void reset();
	// Run about `cycles` machine cycles. Returns false once halted.
	bool run(int cycles);
	bool halted() const { return m_halted; }

	rtl837x_cpu &cpu() { return m_cpu; }
	u8 code_read(u16 addr);			// as the CPU sees it now
	u32 code_flash_addr(u16 addr, u8 bank) const;

	FILE *uart_out = stdout;
	bool trace_insn = false;
	bool trace_sfr = false;
	bool stop_on_halt = true;		// "sjmp ." stops the run (tests)
	u64 instructions = 0;			// counted only while tracing
	u64 cycles = 0;

private:
	u8 sfr_r(u8 a);
	void sfr_w(u8 a, u8 d);
	u8 xdata_r(offs_t a);
	void xdata_w(offs_t a, u8 d);
	void on_insn(offs_t pc);
	void map_code_pages();
	bool at_halt();

	rtl837x_cpu m_cpu;
	std::vector<u8> m_flash;
	std::vector<u8> m_code_ram;		// the common code, 0x0000-0x3fff
	std::vector<u8> m_xdata;
	u8 m_sfr[256] = {};			// SFRs nobody models yet
	bool m_sfr_seen[256] = {};
	bool m_halted = false;
	std::unique_ptr<util::disasm_interface> m_dasm;
};

#endif
