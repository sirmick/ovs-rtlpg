// openflow.h - rtlsim as the OpenFlow 1.3 controller of an OVS bridge whose
// ports are the switch's front ports.
//
// rtlsim listens on a UNIX socket; the bridge connects to it
// (ovs-vsctl set-controller BR unix:PATH, fail_mode=secure). On connect it
// replaces the flow table, asks for the port list and follows port status.
// OpenFlow port N is physical port N - 1 (OpenFlow has no port 0).
//
// The flows are what set_flows() was last given (fabric/compiler.h); until
// then one table-miss flow to the controller, so every frame on a front port
// is a packet-in for the CPU port. set_flows() sends only the difference to
// what is installed (adds, and strict deletes), and a reconnect installs the
// whole set again. The CPU's frames are packet-outs.
//
// Messages: HELLO, ECHO, FEATURES, SET_CONFIG, FLOW_MOD, PORT_DESC,
// PORT_STATUS, PACKET_IN, PACKET_OUT, FLOW_REMOVED, BARRIER, ERROR (logged).
#ifndef RTLSIM_OPENFLOW_H
#define RTLSIM_OPENFLOW_H

#include "emu.h"
#include "fabric/flow.h"

#include <functional>
#include <map>
#include <string>
#include <vector>

class of_controller
{
public:
	explicit of_controller(const std::string &path);	// throws on failure
	~of_controller();

	int fd() const { return m_conn >= 0 ? m_conn : m_listen; }
	bool connected() const { return m_conn >= 0; }
	void poll();
	void packet_out(unsigned phys_port, const std::vector<u8> &frame);
	void set_flows(const std::vector<flow> &flows);
	size_t flows_installed() const { return m_installed.size(); }

	// A frame a flow in `table` sent to the controller.
	std::function<void(unsigned phys_port, u8 table, const u8 *frame, size_t len)> packet_in;
	// A flow with send_flow_rem went (idle timeout): its table and match.
	std::function<void(const flow &f)> flow_removed;
	// A port appeared, changed or went away: up = link up and not disabled.
	std::function<void(unsigned phys_port, bool exists, bool up)> port_status;
	bool trace = false;

private:
	void send(u8 type, const std::vector<u8> &body, u32 xid = 0);
	void handle(u8 type, u32 xid, const u8 *body, size_t len);
	void port(const u8 *p, bool deleted);
	void start();
	void sync();
	void flow_mod(u8 command, const flow &f);
	void drop();

	std::string m_path;
	int m_listen = -1, m_conn = -1;
	std::vector<u8> m_in;
	u32 m_xid = 1;
	std::vector<flow> m_flows;				// what should be installed
	std::map<std::string, flow> m_installed;		// by match text
};

#endif
