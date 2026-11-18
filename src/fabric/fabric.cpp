// fabric.cpp - see fabric.h.
#include "fabric/fabric.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>

// The registers compile_flows() reads: ingress frame types, VLAN control,
// ingress filter, PVIDs (0x4e1c-0x4e2c), isolation (0x50c0-0x50e4), and the
// per-port IGMP config (0x52a0-0x52c4, ports 0-9) that `igmp on/off` toggles.
static const u16 s_inputs[] = {
	0x4e10, 0x4e14, 0x4e18, 0x4e1c, 0x4e20, 0x4e24, 0x4e28, 0x4e2c,
	0x50c0, 0x50c4, 0x50c8, 0x50cc, 0x50d0, 0x50d4, 0x50d8, 0x50dc, 0x50e0, 0x50e4,
	0x52a0, 0x52a4, 0x52a8, 0x52ac, 0x52b0, 0x52b4, 0x52b8, 0x52bc, 0x52c0, 0x52c4,
	0x4f78,
	0x5310, 0x106c,		// MSTP_STATES, RLDP on/off
};

fabric::fabric(rtl837x_system &sys, of_controller &of, const fabric_config &cfg)
	: m_sys(sys), m_of(of), m_cfg(cfg)
{
	m_of.packet_in = [this](unsigned port, u8 table, const u8 *f, size_t n) {
		if (table == TABLE_CLASSIFY && n >= 14 && f[12] == 0x88 && f[13] == 0x99) {
			rldp_rx(port, f, n);
			return;
		}
		if (table != TABLE_LEARN) {
			// Table 0 tags every frame, so an untagged one is a trap that
			// popped the tag to keep the ingress format (compiler.cpp).
			bool const as_is = n < 14 || f[12] != 0x81 || f[13] != 0x00;
			m_sys.nic().receive(port, f, n, as_is);
			return;
		}
		if (n < 18 || f[12] != 0x81 || f[13] != 0x00)
			return;		// table 0 tags every frame it passes on
		u16 const vid = ((f[14] << 8) | f[15]) & 0xfff;
		if (m_sys.tables().learn(f + 6, vid, port)) {
			learned++;
			if (trace)
				fprintf(stderr, "FAB: learned %02x:%02x:%02x:%02x:%02x:%02x VLAN %u on port %u\n",
					f[6], f[7], f[8], f[9], f[10], f[11], vid, port);
		}
		update();
	};
	m_of.flow_removed = [this](const flow &fl) {
		if (fl.table != TABLE_LEARN || !fl.has_src || fl.in_port < 1)
			return;
		if (m_sys.tables().age_out(fl.src.data(), fl.vlan_vid & 0xfff, fl.in_port - 1)) {
			aged++;
			if (trace)
				fprintf(stderr, "FAB: aged out %02x:%02x:%02x:%02x:%02x:%02x VLAN %u on port %u\n",
					fl.src[0], fl.src[1], fl.src[2], fl.src[3], fl.src[4], fl.src[5],
					fl.vlan_vid & 0xfff, fl.in_port - 1);
		}
		update();
	};
}

std::vector<u32> fabric::inputs() const
{
	std::vector<u32> v;
	for (u16 r : s_inputs)
		v.push_back(m_sys.core().reg(r));
	return v;
}

void fabric::update()
{
	std::vector<u32> in = inputs();
	if (in == m_last && m_sys.tables().version == m_version)
		return;
	m_last = std::move(in);
	m_version = m_sys.tables().version;
	m_of.set_flows(compile_flows(m_sys.core(), m_sys.tables(), m_sys.board_info(), m_cfg));
	compiles++;
}


enum : u16 { RLDP_CTRL = 0x106c, RLDP_RETRY = 0x1070, RLDP_PERIOD = 0x1074, RLDP_TX_PMSK = 0x1078,
	     RLDP_LOOP = 0x108c, RLDP_LOOPED = 0x1090, RLDP_LEAVE = 0x1094, RLDP_PAIR = 0x1098 };
enum : u32 { RLDP_EN = 0x01, RLDP_PERIODIC = 0x20 };
static constexpr unsigned PROBE_PORT_BYTE = 22;		// model-only: the sending port

u64 fabric::now_ms() const
{
	return m_sys.cycles / 31250;		// 125 MHz / 4 machine cycles a second
}

void fabric::rldp_set_pair(unsigned port, unsigned partner)
{
	u16 const reg = RLDP_PAIR + ((port >> 3) << 2);
	unsigned const sh = (port & 7) << 2;
	switch_core &c = m_sys.core();
	c.set_reg(reg, (c.reg(reg) & ~(0xfu << sh)) | (u32(partner & 0xf) << sh));
}

void fabric::tick()
{
	switch_core &c = m_sys.core();
	u32 const ctrl = c.reg(RLDP_CTRL);
	bool const on = (ctrl & RLDP_EN) && (ctrl & RLDP_PERIODIC);	// "SA moving" sends nothing here
	u64 const now = now_ms();
	if (on != m_rldp_on) {
		m_rldp_on = on;
		if (on) {
			for (u8 &b : m_rldp_id)
				b = u8(rand());
			m_rldp_next = now;
		} else {
			m_rldp_looped = 0;
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
		if ((m_rldp_looped >> p & 1) && now - m_rldp_heard[p] > (retry + 1) * loop)
			left |= 1u << p;
	if (left) {
		m_rldp_looped &= ~left;
		c.set_reg(RLDP_LOOPED, m_rldp_looped);
		c.set_reg(RLDP_LEAVE, c.reg(RLDP_LEAVE) | left);
		if (trace)
			fprintf(stderr, "FAB: RLDP left loop 0x%03x\n", left);
	}

	if (now < m_rldp_next)
		return;
	m_rldp_next = now + chk;
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
		std::copy(m_rldp_id, m_rldp_id + 6, f.begin() + 15);
		f[PROBE_PORT_BYTE] = u8(p + 1);
		m_of.packet_out(p, f);
		rldp_probes++;
	}
}

void fabric::rldp_rx(unsigned port, const u8 *f, size_t n)
{
	if (n < 60 || f[14] != 0x23)
		return;
	if (!std::equal(m_rldp_id, m_rldp_id + 6, f + 15) || !f[PROBE_PORT_BYTE]) {
		rldp_forward(port, f, n);	// another chip's probe
		return;
	}
	unsigned const from = f[PROBE_PORT_BYTE] - 1;
	if (from >= 10)
		return;
	rldp_returns++;
	u16 const both = u16((1u << from) | (1u << port));
	u64 const now = now_ms();
	m_rldp_heard[from] = m_rldp_heard[port] = now;
	switch_core &c = m_sys.core();
	if ((m_rldp_looped & both) != both && trace)
		fprintf(stderr, "FAB: RLDP loop: probe from port %u back on port %u\n", from, port);
	m_rldp_looped |= both;
	rldp_set_pair(from, port);
	rldp_set_pair(port, from);
	c.set_reg(RLDP_LOOPED, m_rldp_looped);
	c.set_reg(RLDP_LOOP, c.reg(RLDP_LOOP) | both);
}

// Another chip's probe: flooded in the ingress port's PVID VLAN, as a
// broadcast is, to the forwarding member ports its isolation mask allows.
void fabric::rldp_forward(unsigned port, const u8 *f, size_t n)
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
		std::vector<u8> out(f, f + n);
		if (!(vl >> (10 + q) & 1)) {	// a tagged member: insert the PVID's tag
			u8 const tag[4] = {0x81, 0x00, u8(pv >> 8), u8(pv)};
			out.insert(out.begin() + 12, tag, tag + 4);
		}
		m_of.packet_out(q, out);
	}
}
