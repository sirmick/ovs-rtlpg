// compiler.cpp - see compiler.h.
#include "fabric/compiler.h"

#include <algorithm>
#include <cstdio>
#include <map>

enum : u16 {
	REG_INGRESS = 0x4e10, VLAN_CTRL = 0x4e14, VLAN_IGR_FLTR = 0x4e18, PVID_BASE = 0x4e1c,
	PORT_ISOLATION = 0x50c0, IGMP_PORT_CFG = 0x52a0, IPV4_PORT_MC_LM_ACT = 0x4f78,
	MSTP_STATES = 0x5310, RLDP_RLPP_CTRL = 0x106c,
};
enum : u32 { VLAN_VALID = 1u << 25, VLAN_CVLAN_FILTER = 0x4 };
// RTL837X_IGMP_PORT_CFG: igmp on sets these trap bits per port (igmp off: flood 0x15).
enum : u32 { IGMP_TRAP = 0x2a, ETH_P_IP = 0x0800, IPPROTO_IGMP = 2 };
// RLDP probes (Realtek's RRCP EtherType) and the control register's enable bit
enum : u32 { ETH_P_RRCP = 0x8899, RLDP_EN = 0x01 };
// Metadata: bits 9:0 the ingress port's isolation mask; bit 10 set by table 0
// when the frame came in untagged (table 0 pushes a tag on every frame).
enum : u64 { META_ISOLATION = 0x3ff, META_UNTAGGED = 0x400 };
static constexpr unsigned CPU_PORT = 9;

static u16 pvid(const switch_core &core, unsigned port)
{
	u32 v = core.reg(PVID_BASE + (port >> 1) * 4);
	return (port & 1 ? v >> 12 : v) & 0xfff;
}

static bool member(u32 vlan, unsigned port) { return (vlan & VLAN_VALID) && (vlan >> port & 1); }
static bool untagged(u32 vlan, unsigned port) { return vlan >> (10 + port) & 1; }

static flow make(u8 table, u16 priority)
{
	flow f;
	f.table = table;
	f.priority = priority;
	return f;
}

// To `port` in a VLAN whose entry is `vlan`, the tag on (the frame has one).
static of_action out(unsigned port)
{
	return port == CPU_PORT ? of_action{of_action::CONTROLLER} : of_action{of_action::OUTPUT, port + 1};
}

std::vector<flow> compile_flows(const switch_core &core, const switch_tables &tables, const board &b,
				const fabric_config &cfg)
{
	std::vector<flow> fl;
	bool const filtering = core.reg(VLAN_CTRL) & VLAN_CVLAN_FILTER;
	std::vector<unsigned> ports;
	for (auto &p : b.ports)
		ports.push_back(p.phys);
	// MSTP_STATES, 2 bits a port: 11 forwarding; 01 blocking, 10 learning and
	// 00 disabled all forward nothing (learning would learn: not modelled).
	// The CPU port always forwards.
	auto forwarding = [&](unsigned p) { return p == CPU_PORT || (core.reg(MSTP_STATES) >> (2 * p) & 3) == 3; };
	auto isolation = [&](unsigned p) -> u64 {
		u64 m = core.reg(PORT_ISOLATION + 4 * p) & 0x3ff;
		for (unsigned q : ports)
			if (!forwarding(q))
				m &= ~(1ull << q);	// nothing leaves through a port that isn't forwarding
		return m;
	};

	// 0: a port that isn't forwarding takes in only BPDUs, which reach the CPU
	// (silicon: "BPDUs reach the CPU even on a blocked port"; LACP doesn't).
	for (unsigned p : ports) {
		if (forwarding(p))
			continue;
		flow drop = make(TABLE_CLASSIFY, 250);
		drop.in_port = p + 1;
		fl.push_back(drop);
		flow bpdu = make(TABLE_CLASSIFY, 260);
		bpdu.in_port = p + 1;
		bpdu.has_dst = true;
		bpdu.dst = {0x01, 0x80, 0xc2, 0x00, 0x00, 0x00};
		bpdu.actions.push_back({of_action::CONTROLLER});
		fl.push_back(bpdu);
	}
	// 0: with loop detection on, every RLDP probe goes to the chip's RLDP
	// engine (fabric.cpp), from any port, a blocked one too: the engine keeps
	// probing through a port the firmware blocked.
	if (core.reg(RLDP_RLPP_CTRL) & RLDP_EN) {
		flow r = make(TABLE_CLASSIFY, 400);
		r.eth_type = ETH_P_RRCP;
		r.actions.push_back({of_action::CONTROLLER});
		fl.push_back(r);
	}

	// 0: classify
	for (unsigned p : ports) {
		unsigned const mode = (core.reg(REG_INGRESS) >> (2 * p)) & 3;
		bool const filter = filtering && (core.reg(VLAN_IGR_FLTR) >> p & 1);
		u16 const pv = pvid(core, p);
		if (mode != 1 && (!filter || member(tables.vlan(pv), p))) {
			for (int tagged = 0; tagged < 2; tagged++) {	// untagged, priority-tagged
				flow f = make(TABLE_CLASSIFY, 200);
				f.in_port = p + 1;
				f.vlan_vid = tagged ? flow::VID_PRESENT : 0;
				if (!tagged)
					f.actions.push_back({of_action::PUSH_VLAN, 0x8100});
				f.actions.push_back({of_action::SET_VID, pv});
				f.write_meta = isolation(p) | (tagged ? 0 : META_UNTAGGED);
				f.write_meta_mask = META_ISOLATION | META_UNTAGGED;
				f.goto_table = TABLE_LEARN;
				fl.push_back(f);
			}
		}
		if (mode == 2)
			continue;
		if (!filter) {
			flow f = make(TABLE_CLASSIFY, 100);
			f.in_port = p + 1;
			f.vlan_vid = flow::VID_PRESENT;
			f.vlan_mask = flow::VID_PRESENT;
			f.write_meta = isolation(p);
			f.write_meta_mask = 0x3ff;
			f.goto_table = TABLE_LEARN;
			fl.push_back(f);
			continue;
		}
		for (unsigned v = 1; v < 4095; v++) {
			if (!member(tables.vlan(v), p))
				continue;
			flow f = make(TABLE_CLASSIFY, 100);
			f.in_port = p + 1;
			f.vlan_vid = flow::VID_PRESENT | v;
			f.write_meta = isolation(p);
			f.write_meta_mask = 0x3ff;
			f.goto_table = TABLE_LEARN;
			fl.push_back(f);
		}
	}

	// 1: learn, 2: L2
	for (auto &e : tables.l2()) {
		if (!e.present)
			continue;
		u8 mac[6];
		u16 vid;
		switch_tables::key(e, mac, &vid);
		if (mac[0] & 1 || switch_tables::is_l3mc(e))
			continue;	// multicast entries (L3MC): IGMP forwarding is phase 4
		unsigned const q = switch_tables::port(e);
		flow l = make(TABLE_LEARN, 100);
		l.vlan_vid = flow::VID_PRESENT | vid;
		l.has_src = true;
		std::copy(mac, mac + 6, l.src.begin());
		if (q != CPU_PORT)
			l.in_port = q + 1;	// the CPU's own: from no front port
		if (!switch_tables::is_static(e)) {
			l.idle_timeout = cfg.ageing_s;
			l.send_removed = true;
		}
		l.goto_table = TABLE_L2;
		fl.push_back(l);

		flow d = make(TABLE_L2, 100);
		d.vlan_vid = flow::VID_PRESENT | vid;
		d.has_dst = true;
		std::copy(mac, mac + 6, d.dst.begin());
		flow drop = d;
		drop.priority = 90;	// isolated from the ingress port
		fl.push_back(drop);
		d.meta = d.meta_mask = 1ull << q;
		if (forwarding(q)) {
			if (q != CPU_PORT && untagged(tables.vlan(vid), q))
				d.actions.push_back({of_action::POP_VLAN});
			d.actions.push_back(out(q));
		}
		fl.push_back(d);
	}
	flow learn = make(TABLE_LEARN, 0);
	learn.actions.push_back({of_action::CONTROLLER});
	learn.goto_table = TABLE_L2;
	fl.push_back(learn);
	flow miss = make(TABLE_L2, 0);
	miss.goto_table = TABLE_FLOOD;
	fl.push_back(miss);

	// IGMP snoop: with `igmp on` the chip traps IGMP to the CPU, which forwards
	// it (RTLPlayground #904). Trap IGMP (IP proto 2) to the controller above
	// the unicast entries, so a multicast IGMP frame reaches the firmware with
	// its ingress port (the CPU tag cpu_nic::receive() adds) instead of being
	// flooded. On silicon a trapped frame keeps its ingress tagging: one that
	// came in untagged reaches the CPU with no 802.1Q tag (switch-bench: the
	// pre-#904 handler, which takes only untagged frames, learns from an
	// untagged report and not from a VID 1 or VID 0 tagged one), unlike every
	// other frame to the CPU. So pop the tag table 0 pushed; fabric.cpp hands
	// an untagged frame to the NIC as it is.
	// Any port carrying the trap bits means snooping is on. Scan all ports, not
	// just this board's, since the firmware programs its own MIN..MAX range and
	// IGMP_TRAP_CFG is not cleared on `igmp off`. (A flood/off port reads 0x15,
	// an unprogrammed one 0, neither of which match.)
	bool igmp_on = false;
	for (unsigned p = 0; p <= CPU_PORT; p++)
		if ((core.reg(IGMP_PORT_CFG + 4 * p) & IGMP_TRAP) == IGMP_TRAP) {
			igmp_on = true;
			break;
		}
	if (igmp_on) {
		flow t = make(TABLE_L2, 150);
		t.eth_type = ETH_P_IP;
		t.ip_proto = IPPROTO_IGMP;
		t.actions.push_back({of_action::CONTROLLER});
		fl.push_back(t);
		t.priority = 151;
		t.meta = t.meta_mask = META_UNTAGGED;
		t.vlan_vid = t.vlan_mask = flow::VID_PRESENT;	// pop_vlan needs it in the match
		t.actions.insert(t.actions.begin(), {of_action::POP_VLAN});
		fl.push_back(t);
	}

	// 3: flood, once per VLAN and isolation mask that some port has
	std::vector<u64> masks;
	for (unsigned p : ports)
		if (std::find(masks.begin(), masks.end(), isolation(p)) == masks.end())
			masks.push_back(isolation(p));
	for (unsigned v = 1; v < 4095; v++) {
		u32 const vl = tables.vlan(v);
		if (!(vl & VLAN_VALID))
			continue;
		for (u64 m : masks) {
			flow f = make(TABLE_FLOOD, 100);
			f.vlan_vid = flow::VID_PRESENT | v;
			f.meta = m;
			f.meta_mask = 0x3ff;
			std::vector<unsigned> tag, untag;
			for (unsigned p : ports)
				if (member(vl, p) && (m >> p & 1))
					(untagged(vl, p) ? untag : tag).push_back(p);
			if (member(vl, CPU_PORT) && (m >> CPU_PORT & 1))
				tag.push_back(CPU_PORT);
			for (unsigned p : tag)
				f.actions.push_back(out(p));
			if (!untag.empty())
				f.actions.push_back({of_action::POP_VLAN});
			for (unsigned p : untag)
				f.actions.push_back(out(p));
			if (!f.actions.empty())
				fl.push_back(f);
		}
	}

	// 2: IP multicast to a group with an L2MC entry - (group MAC, VID) and a
	// member mask (RTLPlayground's non-IPMC_USES_L3MC path, entry_to_l2mc;
	// LUT_IPMC_HASH clear) - goes to the entry's members in that VLAN only, tagged and
	// untagged as table 3 does, within the isolation mask; an empty set drops.
	// Only 01:00:5e (IPv4 multicast): the static 01:80:c2 link-local entries
	// RTLPlayground installs are not modelled yet.
	for (auto &e : tables.l2()) {
		if (!e.present || switch_tables::is_l3mc(e))
			continue;
		u8 mac[6];
		u16 vid;
		switch_tables::key(e, mac, &vid);
		if (mac[0] != 0x01 || mac[1] != 0x00 || mac[2] != 0x5e)
			continue;
		u32 const vl = tables.vlan(vid);
		if (!(vl & VLAN_VALID))
			continue;
		unsigned const mm = switch_tables::l3mc_mask(e);	// same mask layout
		for (u64 m : masks) {
			flow f = make(TABLE_L2, 120);
			f.vlan_vid = flow::VID_PRESENT | vid;
			f.meta = m;
			f.meta_mask = 0x3ff;
			f.has_dst = true;
			std::copy(mac, mac + 6, f.dst.begin());
			std::vector<unsigned> tag, untag;
			for (unsigned p : ports)
				if ((mm >> p & 1) && member(vl, p) && (m >> p & 1))
					(untagged(vl, p) ? untag : tag).push_back(p);
			if ((mm >> CPU_PORT & 1) && member(vl, CPU_PORT) && (m >> CPU_PORT & 1))
				tag.push_back(CPU_PORT);
			for (unsigned p : tag)
				f.actions.push_back(out(p));
			if (!untag.empty())
				f.actions.push_back({of_action::POP_VLAN});
			for (unsigned p : untag)
				f.actions.push_back(out(p));
			fl.push_back(f);
		}
	}

	// 2: IP multicast to a snooped group - an L3MC entry (IPMC_USES_L3MC) - goes
	// only to the entry's member ports, never flooded. The entry has no VLAN, so
	// per VLAN the members that are also VLAN members get it, tagged and
	// untagged as table 3 does, within the isolation mask; an empty set drops.
	// Below the IGMP trap (150), so IGMP itself still goes to the CPU.
	// Forwarding is (*,G)-only: the chip keys the lookup on (SIP=0, DIP), so a
	// source-specific entry (SIP!=0) sits in the table but never forwards -
	// measured on switch-bench: with both a (*,G) and an (S,G) entry for a
	// group, a stream matching the (S,G) source still goes to the (*,G)
	// members. The firmware only ever programs SIP=0, so this matches it.
	for (auto &e : tables.l2()) {
		if (!e.present || !switch_tables::is_l3mc(e))
			continue;
		u8 dip[4], sip[4];
		switch_tables::l3mc_dip(e, dip);
		switch_tables::l3mc_sip(e, sip);
		u32 const d = (u32(dip[0]) << 24) | (u32(dip[1]) << 16) | (u32(dip[2]) << 8) | dip[3];
		if (sip[0] || sip[1] || sip[2] || sip[3])
			continue;		// (S,G): in the table but never forwards on this chip
		unsigned const mm = switch_tables::l3mc_mask(e);
		for (unsigned v = 1; v < 4095; v++) {
			u32 const vl = tables.vlan(v);
			if (!(vl & VLAN_VALID))
				continue;
			for (u64 m : masks) {
				flow f = make(TABLE_L2, 120);
				f.vlan_vid = flow::VID_PRESENT | v;
				f.meta = m;
				f.meta_mask = 0x3ff;
				f.eth_type = ETH_P_IP;
				f.has_ipv4_dst = true;
				f.ipv4_dst = d;
				std::vector<unsigned> tag, untag;
				for (unsigned p : ports)
					if ((mm >> p & 1) && member(vl, p) && (m >> p & 1))
						(untagged(vl, p) ? untag : tag).push_back(p);
				if ((mm >> CPU_PORT & 1) && member(vl, CPU_PORT) && (m >> CPU_PORT & 1))
					tag.push_back(CPU_PORT);
				for (unsigned p : tag)
					f.actions.push_back(out(p));
				if (!untag.empty())
					f.actions.push_back({of_action::POP_VLAN});
				for (unsigned p : untag)
					f.actions.push_back(out(p));
				fl.push_back(f);	// no actions: a known group with no member here drops
			}
		}
	}

	// 2: IPv4 multicast no L3MC entry matched (the lookup missed), per ingress
	// port by RTL837X_IPV4_PORT_MC_LM_ACT, 2 bits a port: 00 flood (table 3;
	// IPV4_UNKN_MC_FLD_PMSK, the firmware's default all ports, isn't
	// modelled), 01 drop, 10 trap, 11 to the router ports (not modelled:
	// flooded). `igmp on` sets drop. Silicon still floods 224.0.0.0/24
	// (switch-bench: 224.0.0.251 reaches every port while 239.x drops).
	u32 const lm = core.reg(IPV4_PORT_MC_LM_ACT);
	bool miss_any = false;
	for (unsigned p : ports) {
		unsigned const act = lm >> (2 * p) & 3;
		if (act != 1 && act != 2)
			continue;
		flow f = make(TABLE_L2, 110);
		f.in_port = p + 1;
		f.eth_type = ETH_P_IP;
		f.has_ipv4_dst = true;
		f.ipv4_dst = 0xe0000000;
		f.ipv4_dst_mask = 0xf0000000;
		if (act == 2)
			f.actions.push_back({of_action::CONTROLLER});
		fl.push_back(f);
		miss_any = true;
	}
	if (miss_any) {
		flow f = make(TABLE_L2, 115);
		f.eth_type = ETH_P_IP;
		f.has_ipv4_dst = true;
		f.ipv4_dst = 0xe0000000;
		f.ipv4_dst_mask = 0xffffff00;
		f.goto_table = TABLE_FLOOD;
		fl.push_back(f);
	}
	return fl;
}

std::string flows_text(const std::vector<flow> &flows)
{
	std::vector<std::pair<std::string, std::string>> rows;
	for (auto &f : flows) {
		char k[16];
		snprintf(k, sizeof k, "%u %05u ", f.table, 65535 - f.priority);
		rows.emplace_back(k + f.match_text(), f.text());
	}
	std::sort(rows.begin(), rows.end());
	std::string s;
	for (auto &r : rows)
		s += r.second + "\n";
	return s;
}
