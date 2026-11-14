// system.cpp - see system.h.
#include "sys/system.h"

#include <sstream>

static constexpr u32 COMMON_LEN = 0x3ffe;	// imagebuilder: bank 0 is 0x0000-0x3ffd
static constexpr u32 XRAM_LEN = 0xc000;		// --xram-size 49151
static constexpr u8 SFR_MAP_FLASH = 0x97;	// MAP_FLASH_PAGE_TO_XDATA

rtl837x_system::rtl837x_system(std::unique_ptr<flash_controller> flash, const board &b)
	: m_cpu(125000000), m_flash(std::move(flash)), m_board(b),
	  m_code_ram(0x4000, 0xff), m_xdata(0x10000, 0)
{
	seed_chip();
	model_links();
	model_gpio();
	model_pse();
	model_code_ram_load();
	m_cpu.start();
	m_cpu.space(AS_PROGRAM).set_external([this](offs_t a) { return code_read(a); }, [](offs_t, u8) {});
	m_cpu.space(AS_DATA).set_external([this](offs_t a) { return xdata_r(a); }, [this](offs_t a, u8 d) { xdata_w(a, d); });
	m_cpu.space(rtl837x_cpu::SPACE_SFR).set_external([this](offs_t a) { return sfr_r(a); }, [this](offs_t a, u8 d) { sfr_w(a, d); });
	m_cpu.sfr_read = [this](u8 a) { return sfr_r(a); };
	m_cpu.sfr_write = [this](u8 a, u8 d) {
		if (a == rtl837x_cpu::SFR_PSBANK)
			map_code_pages();
		else
			sfr_w(a, d);
	};
	m_cpu.uart_tx = [this](u8 c) { fputc(c, uart_out); if (c == '\n') fflush(uart_out); };
	m_core.soc_reset = [this]() { m_reset_pending = true; };
	m_flash->now = [this]() { return m_cpu.cycles_total; };
	m_core.pc = [this]() { return m_cpu.pc(); };
	m_nic.xdata_r = [this](u16 a) { return xdata_r(a); };
	m_nic.xdata_w = [this](u16 a, u8 d) { xdata_w(a, d); };
	m_nic.irq = [this](bool level) { m_cpu.set_int1(level); };
	m_nic.busy = [this]() { return m_cpu.busy_cycles; };
	m_dasm = m_cpu.disassembler();
	for (int op = 0; op < 256; op++) {
		std::ostringstream o;
		util::disasm_interface::data_buffer b([op](offs_t a) -> u8 { return a == 0 ? op : 0; });
		m_len[op] = m_dasm->disassemble(o, 0, b, b) & util::disasm_interface::LENGTHMASK;
	}
	reset();
}

// A reg-0x24 chip reset (chip_reset) doesn't reset the flash controller, so
// the boot ROM refetches the common code through it as the firmware left it.
// If that isn't a read setup (boot0 leaves CMD_R on a status read), the fetch
// is garbage and the chip hangs until a power cycle - as seen on a
// POE-2G040210GSM after a boot0 install. A cold power-on (chip_reset false)
// resets the controller, so it always boots.
void rtl837x_system::reset(bool chip_reset)
{
	if (chip_reset && !m_flash->code_readable()) {
		if (!m_flash_reset_hang)
			fprintf(stderr, "rtlsim: chip reset with the flash controller in a non-read command "
				"(CMD_R=0x%02x); the boot prefetch reads garbage and the switch hangs until a "
				"power cycle, as on silicon\n", m_flash->cmd_r());
		m_flash_reset_hang = true;
		m_halted = true;
		return;
	}
	std::fill(m_code_ram.begin(), m_code_ram.end(), 0xff);
	for (u32 i = 0; i < COMMON_LEN && 2 + i < m_flash->size(); i++)
		m_code_ram[i] = m_flash->byte(2 + i);
	m_map_flash = 1;
	m_nic.reset();
	m_cpu.reset();
	m_halted = false;
	map_code_pages();
}

// Identity registers (rtlplayground.c main(), boot.c rtl8373_revision()):
// CHIP_ID bits 23:16 0x73 for an RTL8373, bits 15:8 0x70 for an N part;
// CHIP_INFO bits 15:8 read the revision while bits 0x000a0000 are set.
void rtl837x_system::seed_chip()
{
	m_core.set_reg(0x0004, 0x83000000 | ((m_id.rtl8373 ? 0x73u : 0x72u) << 16) | (m_id.n_variant ? 0x7000 : 0));
	m_core.set_reg(0x0010, m_id.uuid);
	m_core.set_reg(0x0014, m_id.lot_no);
	u8 rev = m_id.revision;
	m_core.on_read(0x000c, [rev](u32 v) { return (v & 0xa0000) == 0xa0000 ? (v & ~0xff00u) | (u32(rev) << 8) : v; });
}

// Link registers (rtl837x_port.c, rtlplayground.c check_links()):
// LINKS_STS 0x63e8 has link-up bits, physical ports 0-7 at bits 16-23 and
// 8-9 at bits 8-9; LINKS 0x63f0 a 4-bit speed code per port 0-7 (port i at
// bits 4i+3..4i), LINKS_89 0x63f4 the same for ports 8-9.
void rtl837x_system::model_links()
{
	m_core.on_read(0x63e8, [this](u32 v) {
		u32 up = 0;
		for (unsigned i = 0; i < 10; i++)
			if (m_link[i] != LINK_DOWN)
				up |= i < 8 ? 1u << (16 + i) : 1u << (i - 8 + 8);
		return (v & ~0x00ff0300u) | up;
	});
	m_core.on_read(0x63f0, [this](u32) {
		u32 v = 0;
		for (unsigned i = 0; i < 8; i++)
			v |= u32(m_link[i] == LINK_DOWN ? 0 : m_link[i] & 0xf) << (4 * i);
		return v;
	});
	m_core.on_read(0x63f4, [this](u32) {
		u32 v = 0;
		for (unsigned i = 8; i < 10; i++)
			v |= u32(m_link[i] == LINK_DOWN ? 0 : m_link[i] & 0xf) << (4 * (i - 8));
		return v;
	});
}

// GPIO (rtl837x_pins.c): DIRECTION bit set = output. The INPUT registers
// read the output latch for outputs and the outside level for inputs.
void rtl837x_system::model_gpio()
{
	for (int half = 0; half < 2; half++) {
		u16 const in = half ? 0x48 : 0x44, out = half ? 0x40 : 0x3c, dir = half ? 0x50 : 0x4c;
		m_core.on_read(in, [this, half, out, dir](u32) {
			u32 const d = m_core.reg(dir);
			u32 const ext = u32(m_gpio_ext >> (32 * half));
			return (m_core.reg(out) & d) | (ext & ~d);
		});
	}
}

// The PoE controller on two GPIO pins, open drain: a pin is low while it is
// an output (DIRECTION bit set) with its latch 0, else pulled up. A write to
// the pins' OUTPUT or DIRECTION register gives the stub the lines; what it
// does to SDA is that pin's outside level.
void rtl837x_system::model_pse()
{
	if (!m_board.pse.present)
		return;
	m_pse = std::make_unique<rtl8238b>(m_board.pse.addr);
	u8 const sda = m_board.pse.sda, scl = m_board.pse.scl;
	for (int half = 0; half < 2; half++) {
		u16 const out = half ? 0x40 : 0x3c, dir = half ? 0x50 : 0x4c;
		if ((sda >> 5) != half && (scl >> 5) != half)
			continue;
		auto level = [this, half, out, dir](u8 pin, u16 reg, u32 v) {
			if ((pin >> 5) != half)
				return true;
			u32 const o = reg == out ? v : m_core.reg(out), d = reg == dir ? v : m_core.reg(dir);
			return !((d >> (pin & 31)) & 1) || ((o >> (pin & 31)) & 1);
		};
		for (u16 reg : {out, dir})
			m_core.on_write(reg, [this, reg, level, sda, scl](u32 v) {
				set_gpio(sda, m_pse->lines(level(scl, reg, v), level(sda, reg, v)));
				return v;
			});
	}
}

// Loading the common code RAM (0x0000-0x3fff) from software, as RTLPlayground's
// loader does (loader/loader_alias.c app_start()) to start an application:
// with HW_CONF (0x6040) bit 1 set, bits 3:2 pick a 4 KiB chunk, and a write
// to register 0x80b0 + 4 * i puts its data's low byte at chunk * 0x1000 + i.
// The loader reads it back with movc and jumps to 0, so it runs at once.
void rtl837x_system::model_code_ram_load()
{
	for (u32 i = 0; i < 0x1000; i++)
		m_core.on_write(u16(0x80b0 + 4 * i), [this, i](u32 v) {
			u32 const conf = m_core.reg(0x6040);
			if (conf & 0x02)
				m_code_ram[((conf >> 2) & 3) * 0x1000 + i] = u8(v);
			return v;
		});
}

void rtl837x_system::set_link(unsigned phys_port, int speed)
{
	if (phys_port >= 10 || m_link[phys_port] == speed)
		return;
	m_link[phys_port] = speed;
	model_los();
	m_cpu.set_int0(true);		// the falling edge IT0 waits for; released after a slice
	m_int0_pulse = true;
}

bool rtl837x_system::set_sfp(unsigned slot, const std::vector<u8> &eeprom)
{
	board_sfp const *cp = m_board.cage(slot);
	if (!cp)
		return false;
	board_sfp const &c = *cp;
	bool const in = !eeprom.empty();
	std::vector<u8> a0, a2;
	if (in) {
		a0.assign(eeprom.begin(), eeprom.begin() + std::min<size_t>(eeprom.size(), 256));
		a0.resize(256, 0xff);
		if (eeprom.size() > 256)
			a2.assign(eeprom.begin() + 256, eeprom.begin() + std::min<size_t>(eeprom.size(), 512));
	}
	m_i2c.attach(c.i2c_bus, 0x50, a0);
	m_i2c.attach(c.i2c_bus, 0x51, a2);
	set_gpio(c.detect_gpio, !in);
	m_sfp_in.resize(m_board.sfp.size());
	m_sfp_in[cp - m_board.sfp.data()] = in;
	model_los();
	return true;
}

// LOS is high without light: no module, or no link on its port.
void rtl837x_system::model_los()
{
	for (size_t i = 0; i < m_board.sfp.size(); i++) {
		board_sfp const &c = m_board.sfp[i];
		if (c.los_gpio != board::NO_GPIO)
			set_gpio(c.los_gpio, !(i < m_sfp_in.size() && m_sfp_in[i]) || link(c.phys) == LINK_DOWN);
	}
}

u32 rtl837x_system::code_flash_addr(u16 addr, u8 psbank) const
{
	u32 b = (psbank & 0x40) ? 1 : (psbank & 0x3f);
	return ((b - 1) * 0xc000u + addr) & (m_flash->size() - 1);
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
		prog.set_fast_page(page, f + 0x100 <= m_flash->size() ? &m_flash->dev().mem[f] : nullptr);
	}
}

u8 rtl837x_system::code_read(u16 addr)
{
	if (addr < 0x4000)
		return m_code_ram[addr];
	return m_flash->byte(code_flash_addr(addr, m_cpu.psbank()));
}

u8 rtl837x_system::xdata_r(offs_t a)
{
	a &= 0xffff;
	if (m_map_flash && a >= 0x4000 && a < 0xc000)
		return m_flash->byte(a);	// TODO: which flash address (open question)
	return a < XRAM_LEN ? m_xdata[a] : 0xff;
}

void rtl837x_system::xdata_w(offs_t a, u8 d)
{
	a &= 0xffff;
	if (m_map_flash && a >= 0x4000 && a < 0xc000)
		return;
	if (a < XRAM_LEN)
		m_xdata[a] = d;
}

u8 rtl837x_system::sfr_r(u8 a)
{
	u8 v;
	if (flash_controller::owns(a))
		v = m_flash->sfr_r(a);
	else if (switch_core::owns(a))
		v = m_core.sfr_r(a);
	else if (cpu_nic::owns(a))
		v = m_nic.sfr_r(a);
	else if (a == SFR_MAP_FLASH)
		v = m_map_flash;
	else
		v = m_sfr[a];
	if (trace_sfr)
		fprintf(stderr, "SFR R %02x -> %02x  pc=%04x\n", a, v, m_cpu.pc());
	return v;
}

void rtl837x_system::sfr_w(u8 a, u8 d)
{
	if (trace_sfr)
		fprintf(stderr, "SFR W %02x = %02x  pc=%04x\n", a, d, m_cpu.pc());
	if (flash_controller::owns(a)) {
		m_flash->sfr_w(a, d);
		return;
	}
	if (switch_core::owns(a)) {
		m_core.sfr_w(a, d);
		return;
	}
	if (cpu_nic::owns(a)) {
		m_nic.sfr_w(a, d);
		return;
	}
	if (a == SFR_MAP_FLASH) {
		m_map_flash = d & 1;
		return;
	}
	if (!m_sfr_seen[a]) {
		m_sfr_seen[a] = true;
		if (!trace_sfr)
			fprintf(stderr, "unmodelled SFR %02x written (%02x) at pc=%04x\n", a, d, m_cpu.pc());
	}
	m_sfr[a] = d;
}

void rtl837x_system::on_insn(offs_t pc)
{
	instructions++;
	if (bankcheck) {
		u8 const op = code_read(pc);
		u8 const bank = m_cpu.psbank();
		if (pc >= 0x4000)
			bankcheck->banked_pc(bank, pc);
		if (op == 0x93)		// movc a,@a+dptr
			bankcheck->movc_read(bank, pc, bank, u16(m_cpu.acc() + m_cpu.dptr()));
		else if (op == 0x83)	// movc a,@a+pc
			bankcheck->movc_read(bank, pc, bank, u16(m_cpu.acc() + pc + 1));
	}
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
	m_cpu.m_insn_hook = (trace_insn || bankcheck) ? std::function<void(offs_t)>([this](offs_t pc) { on_insn(pc); }) : nullptr;
	int left = cycles;
	while (left > 0 && !m_halted) {
		int n = m_cpu.run(std::min(left, 256));
		left -= n;
		this->cycles += n;
		if (!m_rx.empty() && m_cpu.uart_rx(m_rx.front()))
			m_rx.pop_front();
		if (m_int0_pulse) {
			m_int0_pulse = false;
			m_cpu.set_int0(false);
		}
		if (m_reset_pending) {
			m_reset_pending = false;
			resets++;
			reset(true);
		}
		if (stop_on_halt && at_halt())
			m_halted = true;
	}
	return !m_halted;
}
