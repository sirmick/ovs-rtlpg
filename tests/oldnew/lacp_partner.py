"""lacp_partner.py - an LACP partner (IEEE 802.1AX) for rtlsim --control
tests: a bench device (bench.py) on one or more front ports.

It is the other end of a LAG, deterministic. All its ports belong to one
system with one key, so they aggregate. It is active. In each LACPDU the
partner block echoes the switch's actor information as last received.

Its rate and timeout (802.1AX periodic and receive machines):
- short=True (the default) advertises the short timeout: the switch sends
  every second, and a port that hears nothing for 3 s expires;
- short=False advertises the long timeout: the switch may send every 30 s
  (its slow periodic), and a port expires after 90 s of silence.
Either way it sends at the rate the switch asks for with its own TIMEOUT bit
(every second for the short timeout, which RTLPlayground always asks for;
every 30 s for the long one), on the period's boundary, and at once when its
state changes.

Its mux is coupled, as Linux's is:
- SYNC on a port once the switch's LACPDU names us as its partner there
  (system, key and port matching ours): we have selected the same
  aggregator;
- COLLECTING and DISTRIBUTING once the switch says SYNC too;
- a port that hears nothing for its receive timeout (above) expires: its
  partner information is forgotten, and it is out of sync until the switch
  is heard again.

stop() makes it go silent with the link up: a partner that died. The switch's
timeout then takes the ports out of the trunk. start() resumes it.
"""
import struct

SLOW = b"\x01\x80\xc2\x00\x00\x02"
ACT, TIMEOUT, AGG, SYNC, COLL, DIST, DEFAULTED, EXPIRED = (1 << i for i in range(8))
FAST_US, SLOW_US = 1_000_000, 30_000_000
SHORT_TIMEOUT_US, LONG_TIMEOUT_US = 3_000_000, 90_000_000


def info(sys_prio, system, key, port_prio, port, state):
    return struct.pack("!H6sHHHB3x", sys_prio, system, key, port_prio, port, state)


def parse_info(b):
    sp, sy, k, pp, p, st = struct.unpack("!H6sHHHB", b[:15])
    return {"sys_prio": sp, "sys": sy, "key": k, "port_prio": pp, "port": p, "state": st}


class LacpPartner:
    def __init__(self, bench, ports, system="02aa00000001", key=0x2a, sys_prio=0x8000, short=True):
        self.b = bench
        self.base = ACT | AGG | (TIMEOUT if short else 0)
        self.timeout_us = SHORT_TIMEOUT_US if short else LONG_TIMEOUT_US
        self.ports = list(ports)
        self.sys, self.key, self.sys_prio = bytes.fromhex(system), key, sys_prio
        self.running = True
        self.pdus_in = {p: 0 for p in self.ports}
        self.pdus_out = {p: 0 for p in self.ports}
        self.st = {p: {"actor": self.base | DEFAULTED, "partner": None, "heard": None, "next": 0}
                   for p in self.ports}
        bench.devices.append(self)

    def our_port(self, p):
        return self.ports.index(p) + 1

    def _mac(self, p):
        return self.sys[:5] + bytes([self.sys[5] + self.our_port(p)])

    def take(self, port, f):
        if port not in self.st or f[:6] != SLOW or f[12:14] != b"\x88\x09" or len(f) < 14 + 74 or f[14] != 1:
            return
        if not self.running:
            return
        body = f[14:]
        if body[2] != 1 or body[22] != 2:		# actor, partner TLVs
            return
        s = self.st[port]
        s["partner"] = parse_info(body[4:22])	# the switch's actor information
        about_us = parse_info(body[24:42])	# what the switch thinks of us
        s["heard"] = self.b.now
        self.pdus_in[port] += 1
        new = self.base
        if (about_us["sys"], about_us["key"], about_us["port"]) == (self.sys, self.key, self.our_port(port)):
            new |= SYNC
            if s["partner"]["state"] & SYNC:
                new |= COLL | DIST
        if new != s["actor"]:
            s["actor"] = new
            s["next"] = 0			# need to transmit: the state changed

    def tick(self, now):
        if not self.running:
            return
        for p, s in self.st.items():
            if s["heard"] is not None and now - s["heard"] > self.timeout_us:
                s["partner"], s["heard"] = None, None
                s["actor"] = self.base | DEFAULTED | EXPIRED
                s["next"] = 0
            if now >= s["next"]:
                self._send(p)
                # the switch's TIMEOUT bit sets our rate (fast until we know);
                # on the period's boundary: the same for any image
                period = SLOW_US if s["partner"] and not s["partner"]["state"] & TIMEOUT else FAST_US
                s["next"] = (now // period + 1) * period

    def _send(self, p):
        s = self.st[p]
        pi = s["partner"] or {"sys_prio": 0, "sys": bytes(6), "key": 0, "port_prio": 0, "port": 0, "state": 0}
        body = (b"\x01\x01" +
                b"\x01\x14" + info(self.sys_prio, self.sys, self.key, 0x80, self.our_port(p), s["actor"]) +
                b"\x02\x14" + info(pi["sys_prio"], pi["sys"], pi["key"], pi["port_prio"], pi["port"], pi["state"]) +
                b"\x03\x10" + struct.pack("!H", 0) + bytes(12) +
                b"\x00\x00" + bytes(50))
        self.b.sim.nic_inject(p, SLOW + self._mac(p) + b"\x88\x09" + body)
        self.pdus_out[p] += 1

    def stop(self):
        self.running = False

    def start(self):
        self.running = True
        for s in self.st.values():
            s["next"] = 0

    def state(self, p):
        return self.st[p]["actor"]
