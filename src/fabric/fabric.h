// fabric.h - the switch core reproduced on an OVS bridge: keeps the bridge's
// flows compiled from the chip's state (fabric/compiler.h), and does in the
// chip model what the hardware does for the flows: learning into the L2
// table, ageing it out, and the CPU's own frames.
//
// - Recompiles when the VLAN or L2 table changes, or a register the compiler
//   reads does, and hands the set to the controller, which installs the
//   difference.
// - A packet-in from the learn table is a frame from an unknown source: its
//   (VID, MAC) is learned on its ingress port. Any other packet-in is for the
//   CPU port's NIC.
// - A learned entry's flow that idles out ages the entry out.
#ifndef RTLSIM_FABRIC_H
#define RTLSIM_FABRIC_H

#include "fabric/compiler.h"
#include "net/openflow.h"
#include "sys/system.h"

class fabric
{
public:
	fabric(rtl837x_system &sys, of_controller &of, const fabric_config &cfg = {});
	void update();		// recompile if anything changed
	bool trace = false;
	u64 learned = 0, aged = 0, compiles = 0;

private:
	std::vector<u32> inputs() const;

	rtl837x_system &m_sys;
	of_controller &m_of;
	fabric_config m_cfg;
	std::vector<u32> m_last;
	u64 m_version = ~0ull;
};

#endif
