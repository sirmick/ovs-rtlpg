// flow.cpp - see flow.h.
#include "fabric/flow.h"

#include <cstdio>

static std::string mac_text(const std::array<u8, 6> &m)
{
	char b[18];
	snprintf(b, sizeof b, "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]);
	return b;
}

std::string flow::match_text() const
{
	char b[64];
	std::string s;
	snprintf(b, sizeof b, "table=%u,priority=%u", table, priority);
	s = b;
	if (in_port) {
		snprintf(b, sizeof b, ",in_port=%u", in_port);
		s += b;
	}
	if (vlan_vid >= 0) {
		if (vlan_mask == 0x1fff)
			snprintf(b, sizeof b, ",vlan_vid=0x%04x", vlan_vid);
		else
			snprintf(b, sizeof b, ",vlan_vid=0x%04x/0x%04x", vlan_vid, vlan_mask);
		s += b;
	}
	if (has_src)
		s += ",dl_src=" + mac_text(src);
	if (has_dst)
		s += ",dl_dst=" + mac_text(dst);
	if (meta_mask) {
		snprintf(b, sizeof b, ",metadata=0x%llx/0x%llx", (unsigned long long)meta, (unsigned long long)meta_mask);
		s += b;
	}
	return s;
}

std::string flow::text() const
{
	char b[64];
	std::string s = match_text();
	if (idle_timeout) {
		snprintf(b, sizeof b, ",idle_timeout=%u", idle_timeout);
		s += b;
	}
	if (send_removed)
		s += ",send_flow_rem";
	s += " actions=";
	std::string a;
	auto add = [&a](const std::string &x) { a += (a.empty() ? "" : ",") + x; };
	for (auto &act : actions) {
		switch (act.type) {
		case of_action::OUTPUT: snprintf(b, sizeof b, "output:%u", act.arg); add(b); break;
		case of_action::CONTROLLER: add("CONTROLLER:65535"); break;
		case of_action::PUSH_VLAN: snprintf(b, sizeof b, "push_vlan:0x%04x", act.arg); add(b); break;
		case of_action::POP_VLAN: add("pop_vlan"); break;
		case of_action::SET_VID: snprintf(b, sizeof b, "set_field:%u->vlan_vid", VID_PRESENT | act.arg); add(b); break;
		}
	}
	if (write_meta_mask) {
		snprintf(b, sizeof b, "write_metadata:0x%llx/0x%llx", (unsigned long long)write_meta,
			 (unsigned long long)write_meta_mask);
		add(b);
	}
	if (goto_table >= 0) {
		snprintf(b, sizeof b, "goto_table:%d", goto_table);
		add(b);
	}
	return s + (a.empty() ? "drop" : a);
}
