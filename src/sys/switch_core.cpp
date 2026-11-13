// switch_core.cpp - see switch_core.h.
#include "sys/switch_core.h"

#include <cstdio>
#include <random>

enum : u8 {
	EXEC_GO = 0xa0, EXEC_STATUS = 0xa1, ADDR_H = 0xa2, ADDR_L = 0xa3,
	D24 = 0xa4, D16 = 0xa5, D8 = 0xa6, D0 = 0xa7,
	SDS_REG = 0x93, SDS_PAGE_ID = 0x94,
	SMI_REG_H = 0xc2, SMI_REG_L = 0xc3, SMI_DEV = 0xc4, SMI_PHYMASK = 0xc5,
};
enum : u8 { CMD_READ_REG = 1, CMD_WRITE_REG = 3, CMD_READ_SDS = 5, CMD_WRITE_SDS = 7, CMD_READ_SMI = 9, CMD_WRITE_SMI = 11 };

// RTL837X_* names from RTLPlayground's rtl837x_regs.h (tools/regnames.py)
static const std::map<u16, const char *> s_names = {
#include "regnames.inc"
};

const char *switch_core::name(u16 addr)
{
	auto it = s_names.find(addr);
	return it == s_names.end() ? nullptr : it->second;
}

switch_core::switch_core()
{
	static std::mt19937 rng(1);
	// RESET: the NIC reset bit clears when the reset is done (at once);
	// the SoC reset bit resets the chip.
	on_write(0x0024, [this](u32 v) {
		if ((v & 1) && soc_reset)
			soc_reset();
		return v & ~0x05u;
	});
	on_read(0x107c, [](u32) { return u32(rng()); });	// RAND_NUM0
	on_read(0x1080, [](u32) { return u32(rng()); });	// RAND_NUM1
}

bool switch_core::owns(u8 s)
{
	return (s >= EXEC_GO && s <= D0) || s == SDS_REG || s == SDS_PAGE_ID || (s >= SMI_REG_H && s <= SMI_PHYMASK);
}

u8 switch_core::sfr_r(u8 s)
{
	if (s == EXEC_STATUS)
		return 0;
	return m_sfr[s];
}

void switch_core::sfr_w(u8 s, u8 d)
{
	m_sfr[s] = d;
	if (s == EXEC_GO)
		execute(d);
}

u32 switch_core::reg(u16 addr) const
{
	auto it = m_reg.find(addr);
	return it == m_reg.end() ? 0 : it->second;
}

u16 switch_core::phy(u8 p, u8 mmd, u16 r) const
{
	auto it = m_phy.find(key(p, mmd, r));
	return it == m_phy.end() ? 0 : it->second;
}

void switch_core::note(u16 addr, bool write, u32 v)
{
	if (trace) {
		const char *n = name(addr);
		fprintf(stderr, "%s %04x %s %08x  %s  pc=%04x\n", write ? "W" : "R", addr, write ? "=" : "->", v, n ? n : "", pc ? pc() : 0);
	}
	if (log_unmodelled && !m_rhook.count(addr) && !m_whook.count(addr) && m_seen.insert(addr).second && !trace) {
		const char *n = name(addr);
		fprintf(stderr, "unmodelled reg %04x %s (%s %08x) pc=%04x\n", addr, n ? n : "", write ? "W" : "R", v, pc ? pc() : 0);
	}
}

u32 switch_core::read_reg(u16 addr)
{
	u32 v = reg(addr);
	if (auto it = m_rhook.find(addr); it != m_rhook.end())
		v = it->second(v);
	note(addr, false, v);
	return v;
}

void switch_core::write_reg(u16 addr, u32 v)
{
	note(addr, true, v);
	if (auto it = m_whook.find(addr); it != m_whook.end())
		v = it->second(v);
	m_reg[addr] = v;
}

void switch_core::execute(u8 cmd)
{
	u16 const addr = (m_sfr[ADDR_H] << 8) | m_sfr[ADDR_L];
	switch (cmd) {
	case CMD_READ_REG: {
		u32 v = read_reg(addr);
		m_sfr[D24] = v >> 24; m_sfr[D16] = v >> 16; m_sfr[D8] = v >> 8; m_sfr[D0] = v;
		break;
	}
	case CMD_WRITE_REG:
		write_reg(addr, (u32(m_sfr[D24]) << 24) | (m_sfr[D16] << 16) | (m_sfr[D8] << 8) | m_sfr[D0]);
		break;
	case CMD_READ_SDS: {
		u16 v = m_sds[key(m_sfr[SDS_PAGE_ID] & 1, m_sfr[SDS_PAGE_ID] >> 1, m_sfr[SDS_REG])];
		m_sfr[D8] = v >> 8; m_sfr[D0] = v;
		if (trace)
			fprintf(stderr, "SDS R %d.%02x.%02x -> %04x\n", m_sfr[SDS_PAGE_ID] & 1, m_sfr[SDS_PAGE_ID] >> 1, m_sfr[SDS_REG], v);
		break;
	}
	case CMD_WRITE_SDS: {
		u16 v = (m_sfr[D8] << 8) | m_sfr[D0];
		m_sds[key(m_sfr[SDS_PAGE_ID] & 1, m_sfr[SDS_PAGE_ID] >> 1, m_sfr[SDS_REG])] = v;
		if (trace)
			fprintf(stderr, "SDS W %d.%02x.%02x = %04x\n", m_sfr[SDS_PAGE_ID] & 1, m_sfr[SDS_PAGE_ID] >> 1, m_sfr[SDS_REG], v);
		break;
	}
	case CMD_READ_SMI: {
		u8 p = m_sfr[D16], mmd = m_sfr[SMI_DEV] >> 3;
		u16 r = (m_sfr[SMI_REG_H] << 8) | m_sfr[SMI_REG_L];
		u16 v = phy(p, mmd, r);
		m_sfr[D8] = v >> 8; m_sfr[D0] = v;
		if (trace)
			fprintf(stderr, "PHY R %d.%d.%04x -> %04x\n", p, mmd, r, v);
		break;
	}
	case CMD_WRITE_SMI: {
		u16 mask = m_sfr[SMI_PHYMASK] | ((m_sfr[SMI_DEV] & 1) << 8);
		u8 mmd = m_sfr[SMI_DEV] >> 3;
		u16 r = (m_sfr[SMI_REG_H] << 8) | m_sfr[SMI_REG_L];
		u16 v = (m_sfr[D8] << 8) | m_sfr[D0];
		for (u8 p = 0; p < 9; p++)
			if (mask & (1 << p))
				set_phy(p, mmd, r, v);
		if (trace)
			fprintf(stderr, "PHY W mask=%03x %d.%04x = %04x\n", mask, mmd, r, v);
		break;
	}
	default:
		fprintf(stderr, "switch: unknown command %02x\n", cmd);
	}
}
