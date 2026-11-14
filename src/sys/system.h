// system.h - the RTL837x around its 8051: flash, code banking, xdata, and
// the SFR bus to the flash controller, the switch core and the CPU port's NIC.
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
#include "sys/nic.h"
#include "sys/i2c.h"
#include "sys/board.h"
#include "sys/rtl8238b.h"
#include "sys/bankcheck.h"

#include <cstdio>
#include <deque>
#include <memory>
#include <string>
#include <vector>

class rtl837x_system
{
public:
	explicit rtl837x_system(std::unique_ptr<flash_controller> flash, const board &b = board_swtg024as());
	rtl837x_system(const std::vector<u8> &image, u32 flash_size, const board &b = board_swtg024as())
		: rtl837x_system(std::make_unique<flash_controller>(image, flash_size), b) {}

	void reset(bool chip_reset = false);
	bool flash_reset_hang() const { return m_flash_reset_hang; }
	// Run about `cycles` machine cycles. Returns false once halted.
	bool run(int cycles);
	bool halted() const { return m_halted; }

	rtl837x_cpu &cpu() { return m_cpu; }
	flash_controller &flash() { return *m_flash; }
	switch_core &core() { return m_core; }
	switch_tables &tables() { return m_tables; }
	cpu_nic &nic() { return m_nic; }
	i2c_controller &i2c() { return m_i2c; }
	u8 code_read(u16 addr);			// as the CPU sees it now
	u32 code_flash_addr(u16 addr, u8 psbank) const;

	// Link state of a physical port (0-9): a speed code as REG_LINKS has it
	// (0 10M, 1 100M, 2 1G, 4 10G, 5 2.5G, 6 5G), or LINK_DOWN. A change
	// pulses INT0, the firmware's link interrupt.
	static constexpr int LINK_DOWN = -1;
	void set_link(unsigned phys_port, int speed);
	int link(unsigned phys_port) const { return phys_port < 10 ? m_link[phys_port] : LINK_DOWN; }

	// What drives a GPIO input pin (0-63) from outside; pins nobody drives
	// read 1 (pulled up), so an empty SFP cage reads "no module".
	void set_gpio(unsigned pin, bool level) { if (pin < 64) m_gpio_ext = (m_gpio_ext & ~(1ull << pin)) | (u64(level) << pin); }

	// An SFP module in cage `slot` (the firmware's, 1-based): its EEPROM, 256 bytes at I2C
	// 0x50 and, if there are 512, the diagnostics at 0x51. Empty: pull it.
	// False if the board has no such cage.
	bool set_sfp(unsigned slot, const std::vector<u8> &eeprom);
	const board &board_info() const { return m_board; }
	rtl8238b *pse() { return m_pse.get(); }	// the board's PoE controller, if any

	void uart_input(const std::string &s) { m_rx.insert(m_rx.end(), s.begin(), s.end()); }
	bool uart_idle() const { return m_rx.empty(); }

	FILE *uart_out = stdout;
	bool trace_insn = false;
	std::unique_ptr<bank_checker> bankcheck;	// set to check banked constant reads
	bool trace_sfr = false;
	bool stop_on_halt = true;		// "sjmp ." stops the run (tests)
	u64 instructions = 0;			// counted only while tracing
	u64 cycles = 0;
	u64 resets = 0;
	bool trace_reset = false;		// log each chip reset to stderr
	u64 uart_lines = 0;			// sent so far, to place a reset in the console

private:
	u8 sfr_r(u8 a);
	void sfr_w(u8 a, u8 d);
	u8 xdata_r(offs_t a);
	void xdata_w(offs_t a, u8 d);
	void on_insn(offs_t pc);
	void map_code_pages();
	bool at_halt();
	void seed_chip();
	void model_links();
	void model_gpio();
	void model_los();
	void model_pse();
	void model_code_ram_load();

	rtl837x_cpu m_cpu;
	std::unique_ptr<flash_controller> m_flash;
	switch_core m_core;
	switch_tables m_tables{m_core};
	cpu_nic m_nic{m_core, m_tables};
	i2c_controller m_i2c{m_core};
	board m_board;
	std::vector<bool> m_sfp_in;	// by index into m_board.sfp
	std::unique_ptr<rtl8238b> m_pse;
	std::vector<u8> m_code_ram;		// the common code, 0x0000-0x3fff
	std::vector<u8> m_xdata;
	u8 m_sfr[256] = {};			// SFRs nobody models yet
	bool m_sfr_seen[256] = {};
	u8 m_map_flash = 1;			// SFR 0x97: xdata 0x4000-0xbfff reads the flash
	bool m_halted = false;
	bool m_flash_reset_hang = false;
	bool m_reset_pending = false;
	std::deque<u8> m_rx;
	std::unique_ptr<util::disasm_interface> m_dasm;
	chip_identity const &m_id = m_board.id;
	u8 m_len[256] = {};
	int m_link[10] = {-1, -1, -1, -1, -1, -1, -1, -1, -1, -1};
	bool m_int0_pulse = false;
	u64 m_gpio_ext = ~0ull;		// instruction lengths, from the disassembler
};

#endif
