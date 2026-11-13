// flow.h - an OpenFlow 1.3 flow as the compiler makes them: the few match
// fields, instructions and actions the switch's pipeline needs, and its text
// (close to ovs-ofctl's) for golden tests.
#ifndef RTLSIM_FLOW_H
#define RTLSIM_FLOW_H

#include "emu.h"

#include <array>
#include <string>
#include <vector>

struct of_action {
	enum type_t { OUTPUT, CONTROLLER, PUSH_VLAN, POP_VLAN, SET_VID } type;
	u32 arg = 0;		// OUTPUT: OpenFlow port; SET_VID: the VID
};

struct flow {
	static constexpr u32 PORT_CONTROLLER = 0xfffffffd;
	static constexpr u16 VID_PRESENT = 0x1000;

	u8 table = 0;
	u16 priority = 0;
	// match; absent unless set
	u32 in_port = 0;			// 0: any
	int vlan_vid = -1;			// OXM vlan_vid: 0 no tag, VID_PRESENT | vid
	u16 vlan_mask = 0x1fff;
	bool has_src = false, has_dst = false;
	std::array<u8, 6> src{}, dst{};
	u64 meta = 0, meta_mask = 0;
	// instructions
	std::vector<of_action> actions;		// apply-actions
	u64 write_meta = 0, write_meta_mask = 0;
	int goto_table = -1;
	u16 idle_timeout = 0;
	bool send_removed = false;

	// the table, priority and match: what tells two flows apart
	std::string match_text() const;
	std::string text() const;
};

#endif
