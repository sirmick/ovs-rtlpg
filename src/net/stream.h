// stream.h - one front port on a UNIX stream socket, framed as QEMU's
// -netdev stream: each frame is a 4-byte big-endian length and the frame
// (no FCS).
//
// rtlsim listens at the path and takes one peer at a time; when it goes
// away the port waits for the next. A peer can be QEMU (-netdev
// stream,addr.type=unix,addr.path=PATH,server=off) or tests/net/tapbridge.py.
#ifndef RTLSIM_STREAM_H
#define RTLSIM_STREAM_H

#include "emu.h"

#include <functional>
#include <string>
#include <vector>

class stream_port
{
public:
	explicit stream_port(const std::string &path);	// throws on failure
	~stream_port();

	int fd() const { return m_peer >= 0 ? m_peer : m_listen; }
	bool connected() const { return m_peer >= 0; }
	// Accept a peer or read what it sent; calls `frame` for each whole frame.
	void poll(const std::function<void(const u8 *, size_t)> &frame);
	void send(const std::vector<u8> &frame);

private:
	void drop_peer();

	std::string m_path;
	int m_listen = -1, m_peer = -1;
	std::vector<u8> m_in;
};

#endif
