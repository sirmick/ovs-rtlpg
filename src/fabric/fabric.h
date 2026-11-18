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
// - The RLDP engine (loop detection, 0x106c-0x109c): with it on and periodic,
//   tick() sends a probe out of every probing port each check period, by
//   packet-out, so a port the firmware blocked still sends it. A probe that
//   comes back with this chip's ID is a loop: LOOPED (live), LOOP (latched)
//   and the pair nibbles are set for both ports; a looped port that hears no
//   probe for its retry count of loop periods leaves the loop (LEAVE latched).
//   Another chip's probe is forwarded like a broadcast. Silicon's probe has
//   no port in it (measured on the 8x); the model puts the sending port in a
//   pad byte, so it knows the pair without the chip's timing.
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
	void tick();		// the RLDP engine, from the main loop
	bool trace = false;
	u64 learned = 0, aged = 0, compiles = 0;

private:
	std::vector<u32> inputs() const;
	void rldp_rx(unsigned port, const u8 *f, size_t n);
	void rldp_set_pair(unsigned port, unsigned partner);
	void rldp_forward(unsigned port, const u8 *f, size_t n);
	u64 now_ms() const;

	rtl837x_system &m_sys;
	of_controller &m_of;
	fabric_config m_cfg;
	std::vector<u32> m_last;
	u64 m_version = ~0ull;
	bool m_rldp_on = false;
	u8 m_rldp_id[6] = {};
	u64 m_rldp_next = 0;		// ms: the next probe round
	u64 m_rldp_heard[10] = {};	// ms: a looped port's last returned probe
	u16 m_rldp_looped = 0;
public:
	u64 rldp_probes = 0, rldp_returns = 0;
};

#endif
