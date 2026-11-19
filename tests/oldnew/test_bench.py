#!/usr/bin/env python3
"""test_bench.py - the bench's HTTP client (bench.Host.http()) against a
scripted switch, no rtlsim: it honours the switch's TCP window.

    python3 tests/oldnew/test_bench.py

The fake switch answers ARP, the handshake, and an HTTP request with
Content-Length. Its receive window, per case:
- open: always 1472 (fork main's uIP);
- stop: closed (0) in the ACK of every segment, opened again by a pure ACK
  3 ms later (E3's upload: uip_stop(), then uip_restart() once the flash job
  has written it);
- small: always 500.
A segment that arrives while the window is closed, or is larger than it, is
dropped without an ACK (as uIP does) and counted: there must be none, and
the response must come back.
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bench import Bench, Host, csum, ip_bytes

SW_MAC, SW_IP = bytes.fromhex("06eb00b5325d"), ip_bytes("192.168.10.247")
PORT = 1


class FakeSwitch:
    """The rtlsim_ctl.Sim calls the bench makes, and a switch behind them."""

    def __init__(self, mode, mss=1460):
        self.mode, self.mss = mode, mss
        self.now = 0
        self.out = []			# (t, frame) to send at t
        self.wnd = 500 if mode == "small" else 1472
        self.open_at = 0		# stop: the window is closed until then
        self.rcv = None
        self.iss = 5000
        self.data = b""
        self.dropped = 0		# segments into a closed or too small window
        self.segs = []			# sizes of the data segments accepted
        self.answered = False

    # --- what Bench calls
    def run(self, dur):
        ms, _, us = dur.partition(".")
        self.now += int(ms) * 1000 + int(us)
        return self.now

    def nic_read(self):
        due = [(t, PORT, f) for t, f in self.out if t <= self.now]
        self.out = [(t, f) for t, f in self.out if t > self.now]
        return due

    def nic_inject(self, port, f):
        et = struct.unpack("!H", f[12:14])[0]
        if et == 0x0806 and struct.unpack("!H", f[20:22])[0] == 1:
            self.host_mac, self.host_ip = f[6:12], f[28:32]
            self._send(f[6:12], 0x0806, struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2,
                                                     SW_MAC, SW_IP, f[6:12], f[28:32]))
        elif et == 0x0800 and f[23] == 6:
            ihl = (f[14] & 15) * 4
            self._tcp_in(f[14 + ihl:14 + struct.unpack("!H", f[16:18])[0]])

    # --- the switch
    def _send(self, dst, et, p, delay=0):
        self.out.append((self.now + delay, dst + SW_MAC + struct.pack("!H", et) + p))

    def _seg(self, flags, data=b"", wnd=None, delay=0, mss=False):
        opt = struct.pack("!BBH", 2, 4, self.mss) if mss else b""
        h = struct.pack("!HHIIBBHHH", 80, self.sport, self.snd, self.rcv, (5 + len(opt) // 4) << 4, flags,
                        self.wnd if wnd is None else wnd, 0, 0) + opt
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(h) + len(data), 0, 0, 64, 6, 0, SW_IP, self.host_ip)
        ip = ip[:10] + struct.pack("!H", 0xffff ^ csum(ip)) + ip[12:]
        self._send(self.host_mac, 0x0800, ip + h + data, delay)
        self.snd += len(data) + (1 if flags & 0x03 else 0)

    def _tcp_in(self, s):
        sport, _, seq, ack, off, fl = struct.unpack("!HHIIBB", s[:14])
        data = s[(off >> 4) * 4:]
        if fl & 0x02:
            self.sport, self.snd, self.rcv = sport, self.iss, seq + 1
            self._seg(0x12, mss=True)
            return
        if not data:
            if fl & 0x01:
                self.rcv = seq + 1
                self._seg(0x10)		# the client's FIN
            return
        if self.now < self.open_at or len(data) > self.wnd:
            self.dropped += 1		# uIP drops it, unacknowledged
            return
        if seq != self.rcv:
            return
        self.data += data
        self.rcv += len(data)
        self.segs.append(len(data))
        head, sep, body = self.data.partition(b"\r\n\r\n")
        done = sep and len(body) >= int(head.lower().split(b"content-length:")[1].split(b"\r\n")[0])
        if done and not self.answered:
            self.answered = True
            self._seg(0x18, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            self._seg(0x11)
        elif self.mode == "stop":
            self._seg(0x10, wnd=0)		# the ACK closes the window
            self.open_at = self.now + 3000
            self._seg(0x10, delay=3000)	# the flash job wrote it: open again
        else:
            self._seg(0x10)


def run(mode, body_len=5000):
    b = Bench.__new__(Bench)
    b.sim = FakeSwitch(mode)
    b.now, b.frames, b.keep, b.kept, b.timed, b.times = 0, {}, set(), [], set(), {}
    b.cables, b.devices, b.uart = {}, [], ""
    from collections import Counter
    b.frames = Counter()
    h = Host(b, PORT)
    body = bytes(i & 0xff for i in range(body_len))
    req = b"POST /upload HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % len(body) + body
    r = h.http(req, 10_000_000)
    return r, b.sim, len(req)


def main():
    fails = 0
    for mode, most in (("open", 1460), ("stop", 1460), ("small", 500)):
        r, sw, n = run(mode)
        ok = (isinstance(r, bytes) and r.endswith(b"OK") and sw.dropped == 0 and sum(sw.segs) == n
              and max(sw.segs) <= most)
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {mode}: response {r[-2:] if isinstance(r, bytes) else r!r}, "
              f"{len(sw.segs)} segments (largest {max(sw.segs) if sw.segs else 0}), "
              f"{sw.dropped} into a closed or small window, {sw.now / 1000:.1f} ms")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
