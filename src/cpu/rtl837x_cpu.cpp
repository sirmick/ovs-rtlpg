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

// mcs51_cpu_device::execute_run() with the extended interrupt poll added.
void rtl837x_cpu::execute_run()
{
	do {
		bool const inhibit = m_irq_delayed == 0xff;	// RETI or an IE/IP write
		u8 const active = m_irq_active;
		check_irqs();
		if (!inhibit && m_irq_active == active)
			take_ext_irq();

		if (instruction_enabled()) {
			m_ppc = m_pc;
			debugger_instruction_hook(m_pc);
			u8 op = m_program.read_byte(m_pc++);
			prepare_instruction(op);
			execute_op(op);
		} else {
			m_inst_cycles++;
		}
		burn_cycles(m_inst_cycles);
		handle_ta_window();
		m_inst_cycles = 0;
	} while (m_icount > 0);
}
