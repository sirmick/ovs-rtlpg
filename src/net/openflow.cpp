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
	OFPT_PACKET_IN = 10, OFPT_PORT_STATUS = 12, OFPT_PACKET_OUT = 13, OFPT_FLOW_MOD = 14,
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

	auto flow_mod = [](u8 command, u8 table, u16 priority) {
		std::vector<u8> f;
		put64(f, 0);			// cookie
		put64(f, 0);			// cookie mask
		put8(f, table);
		put8(f, command);
		put16(f, 0);			// idle timeout
		put16(f, 0);			// hard timeout
		put16(f, priority);
		put32(f, OFP_NO_BUFFER);
		put32(f, OFPP_ANY);		// out port, out group: any (for delete)
		put32(f, OFPP_ANY);
		put16(f, 0);			// flags
		pad(f, 2);
		put16(f, 1);			// match: OXM, empty
		put16(f, 4);
		pad(f, 4);
		return f;
	};
	send(OFPT_FLOW_MOD, flow_mod(3, 0xff, 0));	// delete everything
	std::vector<u8> miss = flow_mod(0, 0, 0);
	put16(miss, 4);					// apply actions
	put16(miss, 8 + 16);
	pad(miss, 4);
	put16(miss, 0);					// output: controller, whole frame
	put16(miss, 16);
	put32(miss, OFPP_CONTROLLER);
	put16(miss, OFPCML_NO_BUFFER);
	pad(miss, 6);
	send(OFPT_FLOW_MOD, miss);

	std::vector<u8> mp;
	put16(mp, OFPMP_PORT_DESC);
	put16(mp, 0);
	pad(mp, 4);
	send(OFPT_MULTIPART_REQUEST, mp);
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
			packet_in(in_port - 1, b + data, len - data);
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
