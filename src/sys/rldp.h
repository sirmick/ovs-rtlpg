// rldp.h - the switch core's RLDP engine, which the fabric and control mode
// share.
//
// The RLDP engine (loop detection, 0x106c-0x109c): with it on and periodic,
// tick() sends a probe out of every probing port each check period, through
// out() - out of a port the firmware blocked too. A probe that comes back
// with this chip's ID is a loop: LOOPED (live), LOOP (latched)
// and the pair nibbles are set for both ports; a looped port that hears no
// probe for its retry count of loop periods leaves the loop (LEAVE latched).
// Another chip's probe is forwarded like a broadcast. Silicon's probe has
// no port in it (measured on the 8x); the model puts the sending port in a
// pad byte, so it knows the pair without the chip's timing.
//
// rx() takes a probe that came in on a port (the caller tells them from
// other frames: ethertype 0x8899, protocol byte 0x23); it is the engine's,
// never the CPU's.
#ifndef RTLSIM_RLDP_H
#define RTLSIM_RLDP_H

#include "sys/system.h"

#include <functional>
#include <vector>

class rldp_engine
{
public:
	explicit rldp_engine(rtl837x_system &sys) : m_sys(sys) {}
	void tick();
	void rx(unsigned port, const u8 *f, size_t n);
	std::function<void(unsigned port, const std::vector<u8> &frame)> out;	// a frame out of a port
	bool trace = false;
	u64 probes = 0, returns = 0;

private:
	void set_pair(unsigned port, unsigned partner);
	void forward(unsigned port, const u8 *f, size_t n);
	u64 now_ms() const;

	rtl837x_system &m_sys;
	bool m_on = false;
	u8 m_id[6] = {};
	u64 m_next = 0;			// ms: the next probe round
	u64 m_heard[10] = {};		// ms: a looped port's last returned probe
	u16 m_looped = 0;
};

#endif
