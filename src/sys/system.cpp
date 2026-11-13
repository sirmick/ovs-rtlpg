// system.cpp - see system.h.
#include "sys/system.h"

#include <sstream>

static constexpr u32 COMMON_LEN = 0x3ffe;	// imagebuilder: bank 0 is 0x0000-0x3ffd
static constexpr u32 XRAM_LEN = 0xc000;		// --xram-size 49151

rtl837x_system::rtl837x_system(std::vector<u8> flash)
	: m_cpu(125000000), m_flash(std::move(flash)), m_code_ram(0x4000, 0xff), m_xdata(0x10000, 0)
{
	m_cpu.start();
	m_cpu.space(AS_PROGRAM).set_external([this](offs_t a) { return code_read(a); }, [](offs_t, u8) {});
	m_cpu.space(AS_DATA).set_external([this](offs_t a) { return xdata_r(a); }, [this](offs_t a, u8 d) { xdata_w(a, d); });
	m_cpu.space(rtl837x_cpu::SPACE_SFR).set_external([this](offs_t a) { return sfr_r(a); }, [this](offs_t a, u8 d) { sfr_w(a, d); });
	m_cpu.sfr_read = [this](u8 a) { return sfr_r(a); };
	m_cpu.sfr_write = [this](u8 a, u8 d) {
		if (a == rtl837x_cpu::SFR_PSBANK)
			map_code_pages();
		sfr_w(a, d);
	};
	m_cpu.uart_tx = [this](u8 c) { fputc(c, uart_out); fflush(uart_out); };
	m_dasm = m_cpu.disassembler();
	reset();
}

void rtl837x_system::reset()
{
	std::fill(m_code_ram.begin(), m_code_ram.end(), 0xff);
	for (u32 i = 0; i < COMMON_LEN && 2 + i < m_flash.size(); i++)
		m_code_ram[i] = m_flash[2 + i];
	m_cpu.reset();
	m_halted = false;
	map_code_pages();
}

// Code fetches go straight to memory: the common code RAM, and the flash
// window of the current bank (re-mapped on every PSBANK write).
void rtl837x_system::map_code_pages()
{
	auto &prog = m_cpu.space(AS_PROGRAM);
	for (u32 page = 0; page < 0x40; page++)
		prog.set_fast_page(page, &m_code_ram[page << 8]);
	for (u32 page = 0x40; page < 0x100; page++) {
		u32 f = code_flash_addr(page << 8, m_cpu.psbank());
		prog.set_fast_page(page, f + 0x100 <= m_flash.size() ? &m_flash[f] : nullptr);
	}
}

u32 rtl837x_system::code_flash_addr(u16 addr, u8 bank) const
{
	bank &= 0x1f;
	if (bank == 0)
		bank = 1;	// TODO: what PSBANK=0 maps above 0x4000 (open question)
	return (bank - 1) * 0xc000u + addr;
}

u8 rtl837x_system::code_read(u16 addr)
{
	if (addr < 0x4000)
		return m_code_ram[addr];
	u32 f = code_flash_addr(addr, m_cpu.psbank());
	return f < m_flash.size() ? m_flash[f] : 0xff;
}

u8 rtl837x_system::xdata_r(offs_t a)
{
	a &= 0xffff;
	return a < XRAM_LEN ? m_xdata[a] : 0xff;
}

void rtl837x_system::xdata_w(offs_t a, u8 d)
{
	a &= 0xffff;
	if (a < XRAM_LEN)
		m_xdata[a] = d;
}

u8 rtl837x_system::sfr_r(u8 a)
{
	if (trace_sfr)
		fprintf(stderr, "SFR R %02x -> %02x  pc=%04x\n", a, m_sfr[a], m_cpu.pc());
	return m_sfr[a];
}

void rtl837x_system::sfr_w(u8 a, u8 d)
{
	if (trace_sfr)
		fprintf(stderr, "SFR W %02x = %02x  pc=%04x\n", a, d, m_cpu.pc());
	if (!m_sfr_seen[a] && a != rtl837x_cpu::SFR_PSBANK) {
		m_sfr_seen[a] = true;
		if (!trace_sfr)
			fprintf(stderr, "unmodelled SFR %02x written (%02x) at pc=%04x\n", a, d, m_cpu.pc());
	}
	m_sfr[a] = d;
}

void rtl837x_system::on_insn(offs_t pc)
{
	instructions++;
	if (trace_insn && m_dasm) {
		std::ostringstream s;
		util::disasm_interface::data_buffer buf([this](offs_t a) { return code_read(a); });
		m_dasm->disassemble(s, pc, buf, buf);
		fprintf(stderr, "%02x:%04x  %s\n", m_cpu.psbank(), pc, s.str().c_str());
	}
}

bool rtl837x_system::at_halt()
{
	u16 pc = m_cpu.pc();
	return code_read(pc) == 0x80 && code_read(pc + 1) == 0xfe;
}

bool rtl837x_system::run(int cycles)
{
	if (m_halted)
		return false;
	m_cpu.m_insn_hook = trace_insn ? std::function<void(offs_t)>([this](offs_t pc) { on_insn(pc); }) : nullptr;
	int left = cycles;
	while (left > 0 && !m_halted) {
		int n = m_cpu.run(std::min(left, 256));
		left -= n;
		this->cycles += n;
		if (stop_on_halt && at_halt())
			m_halted = true;
	}
	return !m_halted;
}
