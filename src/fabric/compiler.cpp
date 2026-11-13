// compiler.cpp - see compiler.h.
#include "fabric/compiler.h"

#include <algorithm>
#include <cstdio>
#include <map>

enum : u16 {
	REG_INGRESS = 0x4e10, VLAN_CTRL = 0x4e14, VLAN_IGR_FLTR = 0x4e18, PVID_BASE = 0x4e1c,
	PORT_ISOLATION = 0x50c0,
};
enum : u32 { VLAN_VALID = 1u << 25, VLAN_CVLAN_FILTER = 0x4 };
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
	auto isolation = [&](unsigned p) -> u64 { return core.reg(PORT_ISOLATION + 4 * p) & 0x3ff; };

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
				f.write_meta = isolation(p);
				f.write_meta_mask = 0x3ff;
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
		if (mac[0] & 1)
			continue;	// multicast entries: IGMP, later
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
		if (q != CPU_PORT && untagged(tables.vlan(vid), q))
			d.actions.push_back({of_action::POP_VLAN});
		d.actions.push_back(out(q));
		fl.push_back(d);
	}
	flow learn = make(TABLE_LEARN, 0);
	learn.actions.push_back({of_action::CONTROLLER});
	learn.goto_table = TABLE_L2;
	fl.push_back(learn);
	flow miss = make(TABLE_L2, 0);
	miss.goto_table = TABLE_FLOOD;
	fl.push_back(miss);

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
