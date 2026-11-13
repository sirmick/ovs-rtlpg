// fabric.cpp - see fabric.h.
#include "fabric/fabric.h"

#include <cstdio>

// The registers compile_flows() reads: ingress frame types, VLAN control,
// ingress filter, PVIDs (0x4e1c-0x4e2c), isolation (0x50c0-0x50e4).
static const u16 s_inputs[] = {
	0x4e10, 0x4e14, 0x4e18, 0x4e1c, 0x4e20, 0x4e24, 0x4e28, 0x4e2c,
	0x50c0, 0x50c4, 0x50c8, 0x50cc, 0x50d0, 0x50d4, 0x50d8, 0x50dc, 0x50e0, 0x50e4,
};

fabric::fabric(rtl837x_system &sys, of_controller &of, const fabric_config &cfg)
	: m_sys(sys), m_of(of), m_cfg(cfg)
{
	m_of.packet_in = [this](unsigned port, u8 table, const u8 *f, size_t n) {
		if (table != TABLE_LEARN) {
			m_sys.nic().receive(port, f, n);
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
