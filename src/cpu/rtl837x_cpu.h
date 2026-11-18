// rtl837x_cpu.h - the RTL837x's 8051: MAME's DW8051 with the chip's changes.
//
// - 0x80 and 0xa0 are the flash controller and the switch-register command,
//   not ports P0/P2. They and every SFR the core doesn't own go to the system
//   (sfr_read/sfr_write).
// - movx @Ri takes its page from MPAGE (0x92), not P2.
// - The UART works on whole bytes: a write to SBUF goes to uart_tx and sets TI,
//   uart_rx() puts a byte in SBUF and sets RI.
// - DS80C320-style extended interrupts: enables EIE (0xe8), priorities EIP
//   (0xf8), flags IE2..IE5 in EXIF (0x91) bits 4..7, which software clears;
//   vectors 0x43, 0x4b, 0x53, 0x5b.
// - Code banking (PSBANK, 0x96) is the system's: it maps program space.
#ifndef RTLSIM_RTL837X_CPU_H
#define RTLSIM_RTL837X_CPU_H

#include "emu.h"
#include "dw8051.h"

class rtl837x_cpu : public dw8051_device
{
public:
	explicit rtl837x_cpu(u32 clock);

	static constexpr int SPACE_SFR = AS_SFR;
	enum : u8 { SFR_EXIF = 0x91, SFR_MPAGE = 0x92, SFR_PSBANK = 0x96, SFR_SBUF = 0x99, SFR_EIE = 0xe8, SFR_EIP = 0xf8 };

	// The system's side of the SFR bus (chip SFRs, unknown SFRs).
	std::function<u8(u8)> sfr_read;
	std::function<void(u8, u8)> sfr_write;
	std::function<void(u8)> uart_tx;

	// Feed a received byte; false while the previous one is still unread (RI set).
	bool uart_rx(u8 c);
	// Raise an extended interrupt request: n = 2..5 (EXIF IE2..IE5).
	void ext_irq(int n);
	void set_int0(bool asserted) { set_input_line(MCS51_INT0_LINE, asserted ? ASSERT_LINE : CLEAR_LINE); }
	void set_int1(bool asserted) { set_input_line(MCS51_INT1_LINE, asserted ? ASSERT_LINE : CLEAR_LINE); }

	u16 pc() const { return m_pc; }
	u64 idle_skipped = 0;		// machine cycles fast-forwarded while idle
	u64 busy_cycles = 0;		// machine cycles spent running instructions
	u64 cycles_total = 0;		// every machine cycle, the skipped idle ones too
	bool fast_idle = true;
	// Time kept exactly (the control socket), so that a run done in one call or
	// in several ends in the same state, and the timers keep step with
	// cycles_total: idle fast-forwards only by whole multiples of 3 cycles
	// (the /3 prescaler stays put), an auto-reload timer wraps as often as
	// it would cycle by cycle, T2 as a baud generator counts 2 a cycle, and
	// an interrupt's entry (4 cycles, burnt by the core) counts in
	// cycles_total. Off, as it was.
	bool exact_time = false;
	u8 psbank() const { return m_psbank; }
	u8 sp() const { return m_sp; }
	u8 acc() const { return m_acc; }
	u16 dptr() const { return m_dptr; }
	u8 iram(u8 a) const { return m_internal_ram[a]; }
	void iram_w(u8 a, u8 d) { m_internal_ram[a] = d; }

	// Stopping between instructions (the control socket's breakpoints): run()
	// returns early, with `stopped` set, once an instruction leaves PC at
	// break_pc (with PSBANK break_bank, unless that is -1), or after the
	// instruction during which stop_request was set. Unset (-1, false), they
	// cost one compare per instruction.
	int break_pc = -1, break_bank = -1;
	bool stop_request = false;
	bool stopped = false;

protected:
	virtual void device_reset() override;
	virtual void sfr_map(address_map &map) override;
	virtual offs_t external_ram_iaddr(offs_t offset, offs_t mem_mask) override;
	virtual void execute_run() override;
	virtual void interrupt_taken() override;

private:
	bool take_ext_irq();
	bool idle_skip();
	void skip_reload(u8 &tl, u8 th, u32 n, int timer);

	u8 m_mpage = 0, m_psbank = 0, m_eie = 0, m_eip = 0, m_exif = 0;
};

#endif
