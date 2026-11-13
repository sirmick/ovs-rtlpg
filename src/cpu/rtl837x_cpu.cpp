// rtl837x_cpu.cpp - see rtl837x_cpu.h.
#include "cpu/rtl837x_cpu.h"

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
}

void rtl837x_cpu::device_reset()
{
	dw8051_device::device_reset();
	m_mpage = m_psbank = m_eie = m_eip = m_exif = 0;
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
		NAME([this](u8 d) { if (uart_tx) uart_tx(d); set_ti(1); }));
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
	return true;
}

// mcs51_cpu_device::execute_run() with the extended interrupt poll and the
// idle fast-forward added.
void rtl837x_cpu::execute_run()
{
	do {
		bool const inhibit = m_irq_delayed == 0xff;	// RETI or an IE/IP write
		u8 const active = m_irq_active;
		check_irqs();
		if (!inhibit && m_irq_active == active)
			take_ext_irq();
		if (idle_skip())
			continue;

		if (instruction_enabled()) {
			m_ppc = m_pc;
			debugger_instruction_hook(m_pc);
			u8 op = m_program.read_byte(m_pc++);
			prepare_instruction(op);
			execute_op(op);
			busy_cycles += m_inst_cycles;
		} else {
			m_inst_cycles++;
		}
		burn_cycles(m_inst_cycles);
		handle_ta_window();
		m_inst_cycles = 0;
	} while (m_icount > 0);
}
