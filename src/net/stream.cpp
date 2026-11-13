// stream.cpp - see stream.h.
#include "net/stream.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

static constexpr size_t FRAME_MAX = 65536;

stream_port::stream_port(const std::string &path) : m_path(path)
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

stream_port::~stream_port()
{
	drop_peer();
	if (m_listen >= 0)
		close(m_listen);
	unlink(m_path.c_str());
}

void stream_port::drop_peer()
{
	if (m_peer >= 0)
		close(m_peer);
	m_peer = -1;
	m_in.clear();
}

void stream_port::poll(const std::function<void(const u8 *, size_t)> &frame)
{
	if (m_peer < 0) {
		m_peer = accept4(m_listen, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
		if (m_peer < 0)
			return;
	}
	u8 buf[65536];
	for (;;) {
		ssize_t n = read(m_peer, buf, sizeof buf);
		if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) {
			drop_peer();
			return;
		}
		if (n < 0)
			break;
		m_in.insert(m_in.end(), buf, buf + n);
	}
	size_t at = 0;
	while (m_in.size() - at >= 4) {
		u32 len = (u32(m_in[at]) << 24) | (m_in[at + 1] << 16) | (m_in[at + 2] << 8) | m_in[at + 3];
		if (len > FRAME_MAX) {
			fprintf(stderr, "rtlsim: stream peer sent a %u-byte frame, dropping it\n", len);
			drop_peer();
			return;
		}
		if (m_in.size() - at - 4 < len)
			break;
		frame(&m_in[at + 4], len);
		at += 4 + len;
	}
	m_in.erase(m_in.begin(), m_in.begin() + at);
}

// Blocking, so frames aren't lost when the peer is slow.
void stream_port::send(const std::vector<u8> &frame)
{
	if (m_peer < 0)
		return;
	std::vector<u8> out(4 + frame.size());
	u32 len = frame.size();
	out[0] = len >> 24; out[1] = len >> 16; out[2] = len >> 8; out[3] = len;
	std::copy(frame.begin(), frame.end(), out.begin() + 4);
	int fl = fcntl(m_peer, F_GETFL);
	fcntl(m_peer, F_SETFL, fl & ~O_NONBLOCK);
	size_t done = 0;
	while (done < out.size()) {
		ssize_t n = ::send(m_peer, out.data() + done, out.size() - done, MSG_NOSIGNAL);
		if (n < 0 && errno == EINTR)
			continue;
		if (n <= 0) {
			drop_peer();
			return;
		}
		done += n;
	}
	fcntl(m_peer, F_SETFL, fl);
}
