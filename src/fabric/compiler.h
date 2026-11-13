// compiler.h - the switch core's forwarding state, as an OVS flow set.
//
// A pure function of the register file, the VLAN and L2 tables and the
// board's ports (DESIGN.md "Compiler"). Tables:
//   0 classify: per ingress port, its accepted frame types (RTL837x_REG_INGRESS
//     0x4e10, 2 bits a port: 0 all, 1 tagged only, 2 untagged only), its PVID
//     for untagged and priority-tagged frames (pushed as a tag), the ingress
//     VLAN filter (VLAN_PORT_IGR_FLTR 0x4e18 bit per port, with VLAN_CTRL
//     0x4e14 bit 2): a tagged frame only for VLANs the port is a member of.
//     The port's isolation mask (PORT_ISOLATION 0x50c0 + 4 * port: where its
//     frames may go) goes into metadata.
//   1 learn: a frame whose (VID, source MAC) the L2 table has on its ingress
//     port goes on; any other is also copied to the controller, which learns
//     it into the L2 table. A dynamic entry's flow has the ageing time as
//     idle timeout, and its removal ages the entry out.
//   2 L2: a known unicast destination goes to its port if the isolation mask
//     allows it, else nowhere; the CPU's entries go to the controller.
//   3 flood: everything else goes to the VLAN's members the isolation mask
//     allows, tagged first, then with the tag popped to the untagged ones
//     (the VLAN entry's untag mask). The CPU's copy goes to the controller.
// The frame keeps an 802.1Q tag from table 0 on; the CPU gets it tagged, as
// the chip's CPU tag carries the VLAN.
// OpenFlow port numbers are physical port + 1; the CPU is port 9, the
// controller.
#ifndef RTLSIM_COMPILER_H
#define RTLSIM_COMPILER_H

#include "fabric/flow.h"
#include "sys/board.h"
#include "sys/switch_core.h"
#include "sys/tables.h"

#include <vector>

struct fabric_config {
	u16 ageing_s = 300;		// L2 ageing time
};

enum : u8 { TABLE_CLASSIFY = 0, TABLE_LEARN = 1, TABLE_L2 = 2, TABLE_FLOOD = 3 };

std::vector<flow> compile_flows(const switch_core &core, const switch_tables &tables, const board &b,
				const fabric_config &cfg = {});

// The flow set, one flow a line, in a stable order.
std::string flows_text(const std::vector<flow> &flows);

#endif
