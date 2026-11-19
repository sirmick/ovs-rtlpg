// rldp.cpp - see rldp.h.
#include "sys/rldp.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>

enum : u16 { RLDP_CTRL = 0x106c, RLDP_RETRY = 0x1070, RLDP_PERIOD = 0x1074, RLDP_TX_PMSK = 0x1078,
	     RLDP_LOOP = 0x108c, RLDP_LOOPED = 0x1090, RLDP_LEAVE = 0x1094, RLDP_PAIR = 0x1098 };
enum : u32 { RLDP_EN = 0x01, RLDP_PERIODIC = 0x20 };
static constexpr unsigned PROBE_PORT_BYTE = 22;		// model-only: the sending port

u64 rldp_engine::now_ms() const
{
	return m_sys.cycles / 31250;		// 125 MHz / 4 machine cycles a second
}

void rldp_engine::set_pair(unsigned port, unsigned partner)
{
	u16 const reg = RLDP_PAIR + ((port >> 3) << 2);
	unsigned const sh = (port & 7) << 2;
	switch_core &c = m_sys.core();
	c.set_reg(reg, (c.reg(reg) & ~(0xfu << sh)) | (u32(partner & 0xf) << sh));
}

void rldp_engine::tick()
{
	switch_core &c = m_sys.core();
	u32 const ctrl = c.reg(RLDP_CTRL);
	bool const on = (ctrl & RLDP_EN) && (ctrl & RLDP_PERIODIC);	// "SA moving" sends nothing here
	u64 const now = now_ms();
	if (on != m_on) {
		m_on = on;
		if (on) {
			for (u8 &b : m_id)
				b = u8(rand());
			m_next = now;
		} else {
			m_looped = 0;
			c.set_reg(RLDP_LOOPED, 0);
		}
	}
	if (!on)
		return;
	u32 const period = c.reg(RLDP_PERIOD);
	u64 const chk = std::max<u32>(period & 0xffff, 1), loop = std::max<u32>(period >> 16, 1);
	u64 const retry = (c.reg(RLDP_RETRY) >> 8) & 0xff;

	// a looped port that has heard no probe for its retries leaves the loop
	u16 left = 0;
	for (unsigned p = 0; p < 10; p++)
		if ((m_looped >> p & 1) && now - m_heard[p] > (retry + 1) * loop)
			left |= 1u << p;
	if (left) {
		m_looped &= ~left;
		c.set_reg(RLDP_LOOPED, m_looped);
		c.set_reg(RLDP_LEAVE, c.reg(RLDP_LEAVE) | left);
		if (trace)
			fprintf(stderr, "FAB: RLDP left loop 0x%03x\n", left);
	}

	if (now < m_next)
		return;
	m_next = now + chk;
	u32 const pmsk = c.reg(RLDP_TX_PMSK);
	for (auto &bp : m_sys.board_info().ports) {
		unsigned const p = bp.phys;
		if (!(pmsk >> p & 1) || m_sys.link(p) == rtl837x_system::LINK_DOWN)
			continue;
		std::vector<u8> f(60, 0);
		std::fill(f.begin(), f.begin() + 6, 0xff);
		u8 const src[6] = {0x00, 0xe0, 0x4c, 0x00, 0x00, 0x00};	// Realtek OUI, magic 0
		std::copy(src, src + 6, f.begin() + 6);
		f[12] = 0x88; f[13] = 0x99; f[14] = 0x23;
		std::copy(m_id, m_id + 6, f.begin() + 15);
		f[PROBE_PORT_BYTE] = u8(p + 1);
		out(p, f);
		probes++;
	}
}

void rldp_engine::rx(unsigned port, const u8 *f, size_t n)
{
	if (n < 60 || f[14] != 0x23)
		return;
	if (!std::equal(m_id, m_id + 6, f + 15) || !f[PROBE_PORT_BYTE]) {
		forward(port, f, n);	// another chip's probe
		return;
	}
	unsigned const from = f[PROBE_PORT_BYTE] - 1;
	if (from >= 10)
		return;
	returns++;
	u16 const both = u16((1u << from) | (1u << port));
	u64 const now = now_ms();
	m_heard[from] = m_heard[port] = now;
	switch_core &c = m_sys.core();
	if ((m_looped & both) != both && trace)
		fprintf(stderr, "FAB: RLDP loop: probe from port %u back on port %u\n", from, port);
	m_looped |= both;
	set_pair(from, port);
	set_pair(port, from);
	c.set_reg(RLDP_LOOPED, m_looped);
	c.set_reg(RLDP_LOOP, c.reg(RLDP_LOOP) | both);
}

// Another chip's probe: flooded in the ingress port's PVID VLAN, as a
// broadcast is, to the forwarding member ports its isolation mask allows.
void rldp_engine::forward(unsigned port, const u8 *f, size_t n)
{
	switch_core &c = m_sys.core();
	if ((c.reg(0x5310) >> (2 * port) & 3) != 3)
		return;			// came in on a port that doesn't forward
	u32 const pv = (c.reg(0x4e1c + (port >> 1) * 4) >> ((port & 1) ? 12 : 0)) & 0xfff;
	u32 const vl = m_sys.tables().vlan(pv);
	u32 const iso = c.reg(0x50c0 + 4 * port);
	if (!(vl >> 25 & 1))
		return;
	for (auto &bp : m_sys.board_info().ports) {
		unsigned const q = bp.phys;
		if (q == port || !(vl >> q & 1) || !(iso >> q & 1) || (c.reg(0x5310) >> (2 * q) & 3) != 3)
			continue;
		std::vector<u8> fr(f, f + n);
		if (!(vl >> (10 + q) & 1)) {	// a tagged member: insert the PVID's tag
			u8 const tag[4] = {0x81, 0x00, u8(pv >> 8), u8(pv)};
			fr.insert(fr.begin() + 12, tag, tag + 4);
		}
		out(q, fr);
	}
}
