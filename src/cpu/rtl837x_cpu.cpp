// rtl837x_cpu.cpp - see rtl837x_cpu.h.
#include "cpu/rtl837x_cpu.h"
#include <cstdlib>

static const machine_config s_mconfig;

rtl837x_cpu::rtl837x_cpu(u32 clock)
	: dw8051_device(s_mconfig, "cpu", nullptr, clock)
{
	// the chip has no ports where a stock 8051 has P0-P3
	port_out_cb<0>().set([](u8) {});
	port_out_cb<1>().set([](u8) {});
	port_out_cb<2>().set([](u8) {});
	port_out_cb<3>().set([](u8) {});
	port_in_cb<0>().set([]() -> u8 { return 0xff; });	// the core samples RXD on P3.0
	port_in_cb<1>().set([]() -> u8 { return 0xff; });
	port_in_cb<2>().set([]() -> u8 { return 0xff; });
	port_in_cb<3>().set([]() -> u8 { return 0xff; });
	if (char const *x = getenv("RTLSIM_BANK_X16"))
		bank_x16 = strtoul(x, nullptr, 0);
}

void rtl837x_cpu::device_reset()
{
	dw8051_device::device_reset();
	m_mpage = m_psbank = m_eie = m_eip = m_exif = 0;
	m_tx_busy = false;		// what is on the wire keeps coming (m_rxq)
}

void rtl837x_cpu::sfr_map(address_map &map)
{
	dw8051_device::sfr_map(map);

	// chip SFRs where a stock 8051 has its ports
	for (u8 a : {0x80, 0x90, 0xa0, 0xb0}) {
		map(a, a).lr8([this, a]() -> u8 { return sfr_read ? sfr_read(a) : 0; }, "chip_r")
			.lw8([this, a](u8 d) { if (sfr_write) sfr_write(a, d); }, "chip_w");
	}
	map(SFR_MPAGE, SFR_MPAGE).lrw8(NAME([this]() -> u8 { return m_mpage; }), NAME([this](u8 d) { m_mpage = d; }));
	map(SFR_PSBANK, SFR_PSBANK).lrw8(NAME([this]() -> u8 { return m_psbank; }),
		NAME([this](u8 d) { m_psbank = d; if (sfr_write) sfr_write(SFR_PSBANK, d); }));
	map(SFR_EIE, SFR_EIE).lrw8(NAME([this]() -> u8 { return m_eie; }), NAME([this](u8 d) { m_eie = d; }));
	map(SFR_EIP, SFR_EIP).lrw8(NAME([this]() -> u8 { return m_eip; }), NAME([this](u8 d) { m_eip = d; }));
	map(SFR_EXIF, SFR_EXIF).lrw8(NAME([this]() -> u8 { return m_exif; }), NAME([this](u8 d) { m_exif = d; }));
	map(SFR_SBUF, SFR_SBUF).lrw8(NAME([this]() -> u8 { return m_sbuf; }),
		NAME([this](u8 d) {
			if (uart_tx)
				uart_tx(d);
			if (!uart_timing)
				set_ti(1);
			else {
				m_tx_busy = true;
				m_tx_done = cycles_total + char_cycles();
			}
		}));
}

offs_t rtl837x_cpu::external_ram_iaddr(offs_t offset, offs_t mem_mask)
{
	if (mem_mask == 0x00ff)
		return (offset & 0x00ff) | (m_mpage << 8);
	return offset;
}

bool rtl837x_cpu::uart_rx(u8 c)
{
	if (BIT(m_scon, SCON_RI))
		return false;
	m_sbuf = c;
	set_ri(1);
	return true;
}

u32 rtl837x_cpu::char_cycles()
{
	u32 bit;
	if (BIT(m_t2con, T2CON_RCLK) || BIT(m_t2con, T2CON_TCLK))
		bit = 16 * std::max<u32>(1, (0x10000 - m_rcap2) / 2);	// T2 counts 2 a cycle as the baud generator
	else if (BIT(m_tcon, TCON_TR1) && ((m_tmod >> 4) & 3) == 2) {
		u32 const per = BIT(m_sfr.read_byte(0x8e), 4) ? 1 : 3;
		bit = (BIT(m_pcon, PCON_SMOD) ? 16 : 32) * (0x100 - m_th1) * per;
	} else
		return 2713;	// 115200 baud
	return 10 * bit;
}

void rtl837x_cpu::uart_rx_queue(const std::string &bytes)
{
	u64 t = std::max(m_rx_last, cycles_total);
	u32 const c = char_cycles();
	for (u8 b : bytes)
		m_rxq.emplace_back(t += c, b);
	m_rx_last = t;
}

// TI and RI as their time comes, between instructions.
void rtl837x_cpu::uart_events()
{
	if (m_tx_busy && cycles_total >= m_tx_done) {
		m_tx_busy = false;
		set_ti(1);
	}
	while (!m_rxq.empty() && m_rxq.front().first <= cycles_total) {
		u8 const c = m_rxq.front().second;
		m_rxq.pop_front();
		if (BIT(m_scon, SCON_RI) || !BIT(m_scon, SCON_REN)) {
			if (uart_lost)
				uart_lost(c, BIT(m_scon, SCON_RI));
		} else {
			m_sbuf = c;
			set_ri(1);
		}
	}
}

// When the UART next does something, or ~0.
u64 rtl837x_cpu::uart_next() const
{
	u64 t = ~0ull;
	if (m_tx_busy)
		t = m_tx_done;
	if (!m_rxq.empty())
		t = std::min(t, m_rxq.front().first);
	return t;
}

void rtl837x_cpu::ext_irq(int n)
{
	if (n >= 2 && n <= 5)
		m_exif |= 1 << (n + 2);
}

// The extended sources rank below the standard ones at the same priority
// level, so they are polled after check_irqs() declined to take one.
bool rtl837x_cpu::take_ext_irq()
{
	if (!BIT(m_ie, IE_A))
		return false;
	u8 const pend = (m_exif >> 4) & m_eie & 0x0f;
	if (!pend)
		return false;
	int src = -1, prio = -1;
	for (int i = 0; i < 4; i++) {
		if (BIT(pend, i) && int(BIT(m_eip, i)) > prio) {
			prio = BIT(m_eip, i);
			src = i;
		}
	}
	if (m_irq_active && prio <= m_cur_irq_prio)
		return false;
	m_program.read_byte(m_pc);
	push_pc();
	m_pc = 0x43 + 8 * src;
	m_cur_irq_prio = prio;
	m_irq_active |= 1 << prio;
	interrupt_taken();
	return true;
}

// In IDLE (PCON.0) with nothing pending, nothing happens until a timer
// overflows: jump to just before the nearest overflow of a timer whose
// interrupt is enabled, and leave the overflow itself to the cycle-by-cycle
// path. Timers count every machine cycle, or every third (CKCON T0M/T1M/T2M
// clear: clk/12 on the four-clock core). Returns true if it skipped.
bool rtl837x_cpu::idle_skip()
{
	if (!fast_idle || instruction_enabled() || pending_irq_flags(interrupt_mask()))
		return false;
	if (BIT(m_ie, IE_A) && ((m_exif >> 4) & m_eie & 0x0f))
		return false;
	u8 const ckcon = m_sfr.read_byte(0x8e);
	auto per = [ckcon](int bit) -> u32 { return BIT(ckcon, bit) ? 1 : 3; };
	u32 dist = m_icount;	// in machine cycles
	auto limit = [&](u32 counts, u32 cpc) { if (counts > 1) dist = std::min(dist, (counts - 1) * cpc); else dist = 0; };
	auto t01_left = [](u8 mode, u8 th, u8 tl) -> u32 {
		switch (mode) {
		case 0: return 0x2000 - (((th << 5) | (tl & 0x1f)) & 0x1fff);
		case 1: return 0x10000 - ((th << 8) | tl);
		case 2: return 0x100 - tl;
		default: return 0x100 - tl;
		}
	};
	bool const t0 = BIT(m_tcon, TCON_TR0) && !BIT(m_tmod, TMOD_CT0);
	bool const t1 = BIT(m_tcon, TCON_TR1) && !BIT(m_tmod, TMOD_CT1);
	bool const t2 = BIT(m_t2con, T2CON_TR2) && !BIT(m_t2con, T2CON_CT2);
	if (t0 && BIT(m_ie, IE_T0))
		limit(t01_left(m_tmod & 3, m_th0, m_tl0), per(3));
	if (t1 && BIT(m_ie, IE_T1))
		limit(t01_left((m_tmod >> 4) & 3, m_th1, m_tl1), per(4));
	if (t2 && BIT(m_ie, 5))	// ET2 is IE.5 (the core's IE_T2 enum says 6)
		limit(0x10000 - m_t2, per(5));
	if (!t0 && !t1 && !t2)
		dist = m_icount;	// asleep for good: nothing but an external line wakes it
	if (uart_timing) {		// wake for the UART's next event
		u64 const next = uart_next();
		if (next <= cycles_total)
			return false;
		if (next - cycles_total < dist)
			dist = u32(next - cycles_total);
	}
	if (exact_time) {
		// the timers' /3 prescaler is where it was after a multiple of 3
		dist -= dist % 3;
		if (dist < 16)
			return false;
		bool const baud = BIT(m_t2con, T2CON_RCLK) || BIT(m_t2con, T2CON_TCLK);
		if (t0 && (m_tmod & 3) == 2)
			skip_reload(m_tl0, m_th0, dist / per(3), 0);
		else if (t0)
			update_timer_t0(dist / per(3));
		if (t1 && ((m_tmod >> 4) & 3) == 2)
			skip_reload(m_tl1, m_th1, dist / per(4), 1);
		else if (t1)
			update_timer_t1(dist / per(4));
		if (t2)
			update_timer_t2(baud ? 2 * dist : dist / per(5));
		m_icount -= dist;
		idle_skipped += dist;
		cycles_total += dist;
		return true;
	}
	if (dist < 16)
		return false;
	if (t0)
		update_timer_t0(dist / per(3));
	if (t1)
		update_timer_t1(dist / per(4));
	if (t2)
		m_t2 += dist / per(5);
	m_icount -= dist;
	idle_skipped += dist;
	cycles_total += dist;
	return true;
}

// The core burns the 4 cycles of an interrupt's entry itself.
void rtl837x_cpu::interrupt_taken()
{
	dw8051_device::interrupt_taken();
	if (exact_time)
		cycles_total += 4;
}

// An 8-bit auto-reload timer (mode 2) counted on by n at once, as n single
// counts would: it may wrap many times (Timer 1 as the baud generator
// reloads every 17 cycles), each wrap reloading TH, setting TF and, for
// Timer 1, clocking the serial port. The core's mode 2 reloads once.
void rtl837x_cpu::skip_reload(u8 &tl, u8 th, u32 n, int timer)
{
	u32 const to_wrap = 0x100 - tl;
	if (n < to_wrap) {
		tl += n;
		return;
	}
	u32 const period = 0x100 - th, rest = n - to_wrap;
	u32 const wraps = 1 + rest / period;
	tl = u8(th + rest % period);
	if (timer == 0) {
		set_tf0(1);
		return;
	}
	if (((m_tmod & 3) != 3))
		set_tf1(1);
	for (u32 i = 0; i < wraps; i++)
		transmit_receive(1);
}

// mcs51_cpu_device::execute_run() with the extended interrupt poll and the
// idle fast-forward added.
void rtl837x_cpu::execute_run()
{
	do {
		if (uart_timing)
			uart_events();
		bool const inhibit = m_irq_delayed == 0xff;	// RETI or an IE/IP write
		u8 const active = m_irq_active;
		check_irqs();
		if (!inhibit && m_irq_active == active)
			take_ext_irq();
		if (idle_skip())
			continue;

		bool const ran = instruction_enabled();
		if (ran) {
			m_ppc = m_pc;
			if (cov_load_pending && m_pc == 0) {
				cov_region = 1;
				cov_load_pending = false;
			}
			if (cov)
				cov->hit(cov_region, m_psbank, m_pc);
			debugger_instruction_hook(m_pc);
			u8 op = m_program.read_byte(m_pc++);
			prepare_instruction(op);
			execute_op(op);
			if (bank_x16 != 16 && m_ppc >= 0x4000) {
				u32 const x = m_inst_cycles * bank_x16 + bank_rem;
				m_inst_cycles = x >> 4;
				bank_rem = x & 15;
			}
			busy_cycles += m_inst_cycles;
		} else {
			m_inst_cycles++;
		}
		burn_cycles(m_inst_cycles);
		cycles_total += m_inst_cycles;
		handle_ta_window();
		m_inst_cycles = 0;
		if (stop_request || (ran && int(m_pc) == break_pc && (break_bank < 0 || m_psbank == break_bank))) {
			stop_request = false;
			stopped = true;
			break;
		}
	} while (m_icount > 0);
}
