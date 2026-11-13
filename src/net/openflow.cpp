// openflow.cpp - see openflow.h. Layouts from the OpenFlow 1.3.5 spec.
#include "net/openflow.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

enum : u8 {
	OFPT_HELLO = 0, OFPT_ERROR = 1, OFPT_ECHO_REQUEST = 2, OFPT_ECHO_REPLY = 3,
	OFPT_FEATURES_REQUEST = 5, OFPT_FEATURES_REPLY = 6, OFPT_SET_CONFIG = 9,
	OFPT_PACKET_IN = 10, OFPT_FLOW_REMOVED = 11, OFPT_PORT_STATUS = 12, OFPT_PACKET_OUT = 13, OFPT_FLOW_MOD = 14,
	OFPT_MULTIPART_REQUEST = 18, OFPT_MULTIPART_REPLY = 19, OFPT_BARRIER_REQUEST = 20, OFPT_BARRIER_REPLY = 21,
};
enum : u32 { OFPP_CONTROLLER = 0xfffffffd, OFPP_ANY = 0xffffffff, OFP_NO_BUFFER = 0xffffffff };
enum : u16 { OFPMP_PORT_DESC = 13, OFPCML_NO_BUFFER = 0xffff };
static constexpr u8 OFP_VERSION = 4;
static constexpr size_t OFP_PORT_LEN = 64;

static void put8(std::vector<u8> &v, u8 x) { v.push_back(x); }
static void put16(std::vector<u8> &v, u16 x) { v.push_back(x >> 8); v.push_back(x); }
static void put32(std::vector<u8> &v, u32 x) { put16(v, x >> 16); put16(v, x); }
static void put64(std::vector<u8> &v, u64 x) { put32(v, x >> 32); put32(v, x); }
static void pad(std::vector<u8> &v, size_t n) { v.insert(v.end(), n, 0); }
static u16 get16(const u8 *p) { return (p[0] << 8) | p[1]; }
static u32 get32(const u8 *p) { return (u32(get16(p)) << 16) | get16(p + 2); }

// OXM headers (OpenFlow basic class): field << 9 | hasmask << 8 | length
enum : u32 {
	OXM_IN_PORT = 0x80000004, OXM_METADATA_W = 0x80000510, OXM_ETH_DST = 0x80000606, OXM_ETH_SRC = 0x80000806,
	OXM_VLAN_VID = 0x80000c02, OXM_VLAN_VID_W = 0x80000d04,
};
enum : u8 { OFPFC_ADD = 0, OFPFC_DELETE = 3, OFPFC_DELETE_STRICT = 4 };

// A flow's match, padded to 8 bytes.
static void put_match(std::vector<u8> &m, const flow &f)
{
	std::vector<u8> o;
	if (f.in_port) {
		put32(o, OXM_IN_PORT);
		put32(o, f.in_port);
	}
	if (f.meta_mask) {
		put32(o, OXM_METADATA_W);
		put64(o, f.meta);
		put64(o, f.meta_mask);
	}
	if (f.has_dst) {
		put32(o, OXM_ETH_DST);
		o.insert(o.end(), f.dst.begin(), f.dst.end());
	}
	if (f.has_src) {
		put32(o, OXM_ETH_SRC);
		o.insert(o.end(), f.src.begin(), f.src.end());
	}
	if (f.vlan_vid >= 0) {
		put32(o, f.vlan_mask == 0x1fff ? OXM_VLAN_VID : OXM_VLAN_VID_W);
		put16(o, f.vlan_vid);
		if (f.vlan_mask != 0x1fff)
			put16(o, f.vlan_mask);
	}
	put16(m, 1);			// OXM
	put16(m, 4 + o.size());
	m.insert(m.end(), o.begin(), o.end());
	pad(m, (8 - (4 + o.size()) % 8) % 8);
}

static void put_actions(std::vector<u8> &m, const std::vector<of_action> &acts)
{
	for (auto &a : acts) {
		switch (a.type) {
		case of_action::OUTPUT:
		case of_action::CONTROLLER:
			put16(m, 0);
			put16(m, 16);
			put32(m, a.type == of_action::CONTROLLER ? OFPP_CONTROLLER : a.arg);
			put16(m, OFPCML_NO_BUFFER);
			pad(m, 6);
			break;
		case of_action::PUSH_VLAN:
			put16(m, 17);
			put16(m, 8);
			put16(m, a.arg);
			pad(m, 2);
			break;
		case of_action::POP_VLAN:
			put16(m, 18);
			put16(m, 8);
			pad(m, 4);
			break;
		case of_action::SET_VID:
			put16(m, 25);		// set-field: the OXM, padded to 8
			put16(m, 16);
			put32(m, OXM_VLAN_VID);
			put16(m, flow::VID_PRESENT | a.arg);
			pad(m, 6);
			break;
		}
	}
}

of_controller::of_controller(const std::string &path) : m_path(path)
{
	sockaddr_un sa = {};
	sa.sun_family = AF_UNIX;
	if (path.size() >= sizeof sa.sun_path)
		throw std::runtime_error("socket path too long: " + path);
	strcpy(sa.sun_path, path.c_str());
	unlink(path.c_str());
	m_listen = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (m_listen < 0 || bind(m_listen, (sockaddr *)&sa, sizeof sa) < 0 || listen(m_listen, 1) < 0)
		throw std::runtime_error("cannot listen on " + path + ": " + strerror(errno));
}

of_controller::~of_controller()
{
	drop();
	if (m_listen >= 0)
		close(m_listen);
	unlink(m_path.c_str());
}

void of_controller::drop()
{
	if (m_conn >= 0) {
		close(m_conn);
		if (trace)
			fprintf(stderr, "OF: switch disconnected\n");
	}
	m_conn = -1;
	m_in.clear();
}

void of_controller::send(u8 type, const std::vector<u8> &body, u32 xid)
{
	if (m_conn < 0)
		return;
	std::vector<u8> m;
	put8(m, OFP_VERSION);
	put8(m, type);
	put16(m, 8 + body.size());
	put32(m, xid ? xid : m_xid++);
	m.insert(m.end(), body.begin(), body.end());
	size_t done = 0;
	while (done < m.size()) {
		ssize_t n = ::send(m_conn, m.data() + done, m.size() - done, MSG_NOSIGNAL);
		if (n < 0 && (errno == EINTR || errno == EAGAIN)) {
			// the switch is slow to read: wait for room rather than drop
			fd_set w;
			FD_ZERO(&w);
			FD_SET(m_conn, &w);
			select(m_conn + 1, nullptr, &w, nullptr, nullptr);
			continue;
		}
		if (n <= 0) {
			drop();
			return;
		}
		done += n;
	}
}

// A new connection: hello, then replace the flow table with the table-miss
// flow to the controller, and ask what ports there are.
void of_controller::start()
{
	send(OFPT_HELLO, {});
	send(OFPT_FEATURES_REQUEST, {});
	std::vector<u8> cfg;
	put16(cfg, 0);
	put16(cfg, OFPCML_NO_BUFFER);
	send(OFPT_SET_CONFIG, cfg);

	flow all;
	all.table = 0xff;			// delete everything, in every table
	flow_mod(OFPFC_DELETE, all);
	m_installed.clear();
	sync();

	std::vector<u8> mp;
	put16(mp, OFPMP_PORT_DESC);
	put16(mp, 0);
	pad(mp, 4);
	send(OFPT_MULTIPART_REQUEST, mp);
	send(OFPT_BARRIER_REQUEST, {});
}

void of_controller::flow_mod(u8 command, const flow &f)
{
	std::vector<u8> m;
	put64(m, 0);			// cookie
	put64(m, 0);			// cookie mask
	put8(m, f.table);
	put8(m, command);
	put16(m, f.idle_timeout);
	put16(m, 0);			// hard timeout
	put16(m, f.priority);
	put32(m, OFP_NO_BUFFER);
	put32(m, OFPP_ANY);		// out port, out group: any (deletes)
	put32(m, OFPP_ANY);
	put16(m, f.send_removed ? 1 : 0);	// OFPFF_SEND_FLOW_REM
	pad(m, 2);
	put_match(m, f);
	if (command == OFPFC_ADD) {
		if (!f.actions.empty()) {
			std::vector<u8> a;
			put_actions(a, f.actions);
			put16(m, 4);		// apply actions
			put16(m, 8 + a.size());
			pad(m, 4);
			m.insert(m.end(), a.begin(), a.end());
		}
		if (f.write_meta_mask) {
			put16(m, 2);		// write metadata
			put16(m, 24);
			pad(m, 4);
			put64(m, f.write_meta);
			put64(m, f.write_meta_mask);
		}
		if (f.goto_table >= 0) {
			put16(m, 1);		// goto table
			put16(m, 8);
			put8(m, f.goto_table);
			pad(m, 3);
		}
	}
	send(OFPT_FLOW_MOD, m);
}

void of_controller::set_flows(const std::vector<flow> &flows)
{
	m_flows = flows;
	sync();
}

// Install what differs: deletes first, so a changed match can't linger.
void of_controller::sync()
{
	if (m_conn < 0)
		return;
	std::vector<flow> want = m_flows;
	if (want.empty()) {			// no compiler: everything to the CPU
		flow miss;
		miss.actions.push_back({of_action::CONTROLLER});
		want.push_back(miss);
	}
	std::map<std::string, const flow *> keyed;
	for (auto &f : want)
		keyed[f.match_text()] = &f;
	for (auto it = m_installed.begin(); it != m_installed.end();) {
		if (keyed.count(it->first)) {
			++it;
			continue;
		}
		flow_mod(OFPFC_DELETE_STRICT, it->second);
		it = m_installed.erase(it);
	}
	for (auto &[k, f] : keyed) {
		auto it = m_installed.find(k);
		if (it != m_installed.end() && it->second.text() == f->text())
			continue;
		flow_mod(OFPFC_ADD, *f);
		m_installed[k] = *f;
	}
	send(OFPT_BARRIER_REQUEST, {});
}

void of_controller::poll()
{
	if (m_conn < 0) {
		m_conn = accept4(m_listen, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
		if (m_conn < 0)
			return;
		if (trace)
			fprintf(stderr, "OF: switch connected\n");
		start();
	}
	u8 buf[65536];
	for (;;) {
		ssize_t n = read(m_conn, buf, sizeof buf);
		if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {
			drop();
			return;
		}
		if (n < 0)
			break;
		m_in.insert(m_in.end(), buf, buf + n);
	}
	size_t at = 0;
	while (m_in.size() - at >= 8) {
		u16 const len = get16(&m_in[at + 2]);
		if (len < 8) {
			fprintf(stderr, "rtlsim: OpenFlow message of length %u, dropping the switch\n", len);
			drop();
			return;
		}
		if (m_in.size() - at < len)
			break;
		handle(m_in[at + 1], get32(&m_in[at + 4]), &m_in[at + 8], len - 8);
		if (m_conn < 0)
			return;
		at += len;
	}
	m_in.erase(m_in.begin(), m_in.begin() + at);
}

void of_controller::port(const u8 *p, bool deleted)
{
	u32 const no = get32(p);
	if (no < 1 || no > 10)
		return;
	bool const up = !(get32(p + 32) & 1) && !(get32(p + 36) & 1);	// not PORT_DOWN, not LINK_DOWN
	if (trace)
		fprintf(stderr, "OF: port %u (%.16s) %s\n", no, (const char *)p + 16, deleted ? "gone" : up ? "up" : "down");
	if (port_status)
		port_status(no - 1, !deleted, up && !deleted);
}

void of_controller::handle(u8 type, u32 xid, const u8 *b, size_t len)
{
	switch (type) {
	case OFPT_HELLO:
	case OFPT_ECHO_REPLY:
	case OFPT_BARRIER_REPLY:
		break;
	case OFPT_ECHO_REQUEST:
		send(OFPT_ECHO_REPLY, std::vector<u8>(b, b + len), xid);
		break;
	case OFPT_FEATURES_REPLY:
		if (trace && len >= 8)
			fprintf(stderr, "OF: datapath %08x%08x\n", get32(b), get32(b + 4));
		break;
	case OFPT_ERROR:
		fprintf(stderr, "rtlsim: OpenFlow error type %u code %u\n", len >= 4 ? get16(b) : 0, len >= 4 ? get16(b + 2) : 0);
		break;
	case OFPT_MULTIPART_REPLY:
		if (len >= 8 && get16(b) == OFPMP_PORT_DESC)
			for (size_t o = 8; o + OFP_PORT_LEN <= len; o += OFP_PORT_LEN)
				port(b + o, false);
		break;
	case OFPT_PORT_STATUS:
		if (len >= 8 + OFP_PORT_LEN)
			port(b + 8, b[0] == 1);
		break;
	case OFPT_PACKET_IN: {
		// buffer id, total length, reason, table, cookie; the match; 2 bytes pad
		if (len < 24)
			break;
		u16 const mlen = get16(b + 18);
		size_t const data = 16 + (mlen + 7) / 8 * 8 + 2;
		u32 in_port = 0;
		for (size_t o = 16 + 4; o + 4 <= 16u + mlen && o + 4 <= len; o += 4 + b[o + 3])
			if (get32(b + o) == 0x80000004 && o + 8 <= len)	// OXM_OF_IN_PORT
				in_port = get32(b + o + 4);
		if (data <= len && in_port >= 1 && in_port <= 10 && packet_in)
			packet_in(in_port - 1, b[7], b + data, len - data);
		break;
	}
	case OFPT_FLOW_REMOVED: {
		// cookie, priority, reason, table, durations, timeouts, counts; the match
		if (len < 44)
			break;
		flow f;
		f.priority = get16(b + 8);
		f.table = b[11];
		u16 const mlen = get16(b + 42);
		for (size_t o = 44; o + 4 <= 40u + mlen && o + 4 <= len; o += 4 + b[o + 3]) {
			u32 const h = get32(b + o);
			const u8 *v = b + o + 4;
			if (o + 4 + b[o + 3] > len)
				break;
			if (h == OXM_IN_PORT)
				f.in_port = get32(v);
			else if (h == OXM_ETH_SRC) {
				f.has_src = true;
				std::copy(v, v + 6, f.src.begin());
			} else if (h == OXM_ETH_DST) {
				f.has_dst = true;
				std::copy(v, v + 6, f.dst.begin());
			} else if (h == OXM_VLAN_VID)
				f.vlan_vid = get16(v);
			else if (h == OXM_VLAN_VID_W) {
				f.vlan_vid = get16(v);
				f.vlan_mask = get16(v + 2);
			} else if (h == OXM_METADATA_W) {
				f.meta = (u64(get32(v)) << 32) | get32(v + 4);
				f.meta_mask = (u64(get32(v + 8)) << 32) | get32(v + 12);
			}
		}
		m_installed.erase(f.match_text());
		if (trace)
			fprintf(stderr, "OF: flow removed: %s\n", f.match_text().c_str());
		if (flow_removed)
			flow_removed(f);
		break;
	}
	default:
		if (trace)
			fprintf(stderr, "OF: message type %u ignored\n", type);
	}
}

void of_controller::packet_out(unsigned phys_port, const std::vector<u8> &frame)
{
	std::vector<u8> m;
	put32(m, OFP_NO_BUFFER);
	put32(m, OFPP_CONTROLLER);
	put16(m, 16);			// actions length
	pad(m, 6);
	put16(m, 0);			// output
	put16(m, 16);
	put32(m, phys_port + 1);
	put16(m, 0);
	pad(m, 6);
	m.insert(m.end(), frame.begin(), frame.end());
	send(OFPT_PACKET_OUT, m);
}
