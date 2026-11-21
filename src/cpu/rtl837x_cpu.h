// rtl837x_cpu.h - the RTL837x's 8051: MAME's DW8051 with the chip's changes.
//
// - 0x80 and 0xa0 are the flash controller and the switch-register command,
//   not ports P0/P2. They and every SFR the core doesn't own go to the system
//   (sfr_read/sfr_write).
// - movx @Ri takes its page from MPAGE (0x92), not P2.
// - The UART works on whole bytes: a write to SBUF goes to uart_tx and sets TI,
//   uart_rx() puts a byte in SBUF and sets RI. With uart_timing (the control
//   socket) it takes its time instead: TI sets a character time after the
//   write, and bytes queued with uart_rx_queue() arrive one a character time.
// - DS80C320-style extended interrupts: enables EIE (0xe8), priorities EIP
//   (0xf8), flags IE2..IE5 in EXIF (0x91) bits 4..7, which software clears;
//   vectors 0x43, 0x4b, 0x53, 0x5b.
// - Code banking (PSBANK, 0x96) is the system's: it maps program space.
#ifndef RTLSIM_RTL837X_CPU_H
#define RTLSIM_RTL837X_CPU_H

#include "emu.h"
#include "dw8051.h"
#include "sys/coverage.h"

#include <deque>
#include <string>

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

	// The UART at its baud rate. A character is 10 bit-times (start, 8 data,
	// stop) at the rate the firmware programmed: Timer 2 as the baud generator
	// (RCLK/TCLK: 16 of its overflows a bit), else Timer 1 in mode 2 (16 of
	// its overflows a bit with SMOD, else 32); 115200 baud before either runs.
	// uart_rx_queue() puts bytes on the wire: each arrives a character time
	// after the one before (the first, after now), at an exact cycle, and is
	// lost, as on an 8051, if RI is still set or reception is off (REN):
	// uart_lost says so.
	bool uart_timing = false;
	u32 char_cycles();
	void uart_rx_queue(const std::string &bytes);
	size_t uart_rx_pending() const { return m_rxq.size(); }
	std::function<void(u8 c, bool ri)> uart_lost;
	// Raise an extended interrupt request: n = 2..5 (EXIF IE2..IE5).
	void ext_irq(int n);
	void set_int0(bool asserted) { set_input_line(MCS51_INT0_LINE, asserted ? ASSERT_LINE : CLEAR_LINE); }
	void set_int1(bool asserted) { set_input_line(MCS51_INT1_LINE, asserted ? ASSERT_LINE : CLEAR_LINE); }

	u16 pc() const { return m_pc; }
	u16 ppc() const { return m_ppc; }	// the current instruction's
	u64 idle_skipped = 0;		// machine cycles fast-forwarded while idle
	u64 busy_cycles = 0;		// machine cycles spent running instructions
	u64 cycles_total = 0;		// every machine cycle, the skipped idle ones too
	bool fast_idle = true;
	// Silicon fetches the bank window (0x4000-0xffff) from the SPI flash: on
	// the 4+2 (SWGT024, 2026-11-21) banked code ran ~2x rtlsim's (console
	// get 1.96-2.04x, one ARP 1.85x, httpd's per-byte header scan 2.6x;
	// rtlpg-bench object-model/tools/calib.py). An instruction there costs
	// bank_x16/16 of its cycles (16: as before; env RTLSIM_BANK_X16, 32 for
	// the 4+2), the remainder carried. A per-MOVC and a per-bank-switch cost
	// were tried and explained nothing.
	u32 bank_x16 = 16;
	u32 bank_rem = 0;
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
	// Software loaded the common code RAM (code_load_pending, set by the
	// system's model of the load) and PC then reached 0: code_loaded, the
	// code running is what software put there (on a loader image, the
	// application). Coverage (sys/coverage.h) marks each instruction run in
	// region cov_region: 0 before, 1 once code_loaded. Unset, one compare per
	// instruction.
	coverage *cov = nullptr;
	int cov_region = 0;
	bool code_load_pending = false;
	bool code_loaded = false;
	bool stop_request = false;
	bool stopped = false;

protected:
	virtual void device_reset() override;
	virtual void sfr_map(address_map &map) override;
	virtual offs_t external_ram_iaddr(offs_t offset, offs_t mem_mask) override;
	virtual void execute_run() override;
	virtual void interrupt_taken() override;

private:
	void uart_events();
	u64 uart_next() const;
	bool take_ext_irq();
	bool idle_skip();
	void skip_reload(u8 &tl, u8 th, u32 n, int timer);

	u8 m_mpage = 0, m_psbank = 0, m_eie = 0, m_eip = 0, m_exif = 0;
	bool m_tx_busy = false;
	u64 m_tx_done = 0;			// cycles_total when TI sets
	std::deque<std::pair<u64, u8>> m_rxq;	// bytes on the wire, by arrival
	u64 m_rx_last = 0;			// when the last queued byte arrives
};

#endif
