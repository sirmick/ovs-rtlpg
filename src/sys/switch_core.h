// switch_core.h - the RTL837x switch core as the 8051 reaches it: the
// register handshake, SerDes and SMI (PHY) access, all through SFRs.
//
// From RTLPlayground's rtlplayground.c:
//   EXEC_GO (0xa0) starts a command, EXEC_STATUS (0xa1) reads 0 when done
//   (at once, here). Registers: 16-bit address in 0xa2/0xa3, 32-bit data in
//   0xa4 (bits 31-24) .. 0xa7 (bits 7-0). Commands: 1/3 read/write register,
//   5/7 read/write SerDes (reg 0x93, page << 1 | id 0x94, data 0xa6/0xa7),
//   9/11 read/write PHY over SMI (reg 0xc2/0xc3, MMD in 0xc4 bits 3-7; read:
//   PHY in 0xa5; write: a 9-bit PHY mask in 0xc5 + 0xc4 bit 0).
//
// Registers are plain storage unless a behaviour is installed for them;
// every access can be traced, and the first access to each register that has
// no behaviour is logged as "unmodelled".
#ifndef RTLSIM_SWITCH_CORE_H
#define RTLSIM_SWITCH_CORE_H

#include "emu.h"

#include <functional>
#include <map>
#include <set>
#include <unordered_map>

class switch_core
{
public:
	switch_core();

	static bool owns(u8 sfr);
	u8 sfr_r(u8 sfr);
	void sfr_w(u8 sfr, u8 d);

	u32 reg(u16 addr) const;
	void set_reg(u16 addr, u32 v) { m_reg[addr] = v; }
	u16 phy(u8 phy, u8 mmd, u16 reg) const;
	void set_phy(u8 phy, u8 mmd, u16 reg, u16 v) { m_phy[key(phy, mmd, reg)] = v; }

	// Behaviours: a read hook returns the value to read; a write hook gets
	// the value written and returns what to store.
	void on_read(u16 addr, std::function<u32(u32 stored)> f) { m_rhook[addr] = std::move(f); }
	void on_write(u16 addr, std::function<u32(u32 v)> f) { m_whook[addr] = std::move(f); }

	std::function<void()> soc_reset;	// RESET_SOC_BIT written
	bool trace = false;
	bool log_unmodelled = true;
	std::function<u16()> pc;		// for traces

	static const char *name(u16 addr);

private:
	static u32 key(u8 a, u8 b, u16 c) { return (u32(a) << 24) | (u32(b) << 16) | c; }
	void execute(u8 cmd);
	u32 read_reg(u16 addr);
	void write_reg(u16 addr, u32 v);
	void note(u16 addr, bool write, u32 v);

	u8 m_sfr[256] = {};
	std::unordered_map<u16, u32> m_reg;
	std::unordered_map<u32, u16> m_sds, m_phy;
	std::unordered_map<u16, std::function<u32(u32)>> m_rhook, m_whook;
	std::set<u16> m_seen;
};

#endif
