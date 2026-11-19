"""bench.py - one firmware image in rtlsim --control, with what a scenario
needs around it: the console, devices on front ports (a host that speaks ARP,
TCP and HTTP; an LACP partner, lacp_partner.py), cables between front ports,
SFP modules, and a count of every frame the chip sends, by port and kind.

Time is firmware time in µs (rtlsim_ctl's). The bench advances on a fixed
grid (GRID µs from firmware time 0; COARSE while no cable is in and nothing
is waited for), and only at its points does it look at
the frames sent: it counts them, hands them to the host, and puts those sent
out of a cabled port into the port at the cable's other end. So two images
given the same scenario see a frame cross a cable, or a device answer, at the
same firmware times whenever they sent it at the same times; a device's own
timers (tick) run at the grid points too.
"""
import os, re, struct, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from rtlsim_ctl import Sim, SimError

GRID = 500			# µs between looks at the wire
COARSE = 10000			# ... with no cable and nothing to wait for
PROMPT = r"\n> "			# a reply ends at the first prompt; what follows is kept
# Lines the firmware prints on its own, not as a command's reply (link
# changes, protocol events): taken out of a reply, wherever they fall in it,
# and kept with what came after the prompt, so a line printed a scheduler
# pass before or after a prompt lands in the same place. Each its own line.
# From RTLPlayground's sources (rtlplayground.c, rtl837x_stp.c, sfp.c,
# rtl837x_rldp.c, rtl837x_igmp.c, rtl837x_port.c); port_lag_members_set is
# printed in a lag command's reply and on its own when LACP changes a trunk,
# so it always moves. A blank line just before a moved line moves with it
# (the firmware starts these with "\n").
ASYNC = [re.compile(p + "$") for p in (
    r"<new link: .*>", r"STP: .*", r"Updating Root bridge",
    r"<(MODULE INSERTED|MODULE REMOVED|SFP-RX LOS|SFP-RX OK)>.*", r"  Rate: .*Module: .*", r"SFP: an I2C read failed.*",
    r"Loop on port .*", r"Loop: port .*",
    r"IGMP[-_]Entry FOUND", r"Updating IGMP entry", r"IGMP Entry (already )?deleted", r"igmp_router_port_set: .*",
    r"port_lag_members_set, .*", r"sds_config sds: .*", r">(3|10)s button detected.*",
)]
BCAST = b"\xff" * 6


def ip_bytes(dotted):
    return bytes(int(x) for x in dotted.split("."))


def csum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xffff) + (s >> 16)
    return s


def kind(f):
    """What a frame is, for counting: stp, lacp, lldp, rldp, arp, igmp,
    icmp, tcp/<port>, udp/<port> (the switch's side: source port), ip/<proto>,
    eth/<type>; prefixed vlan<id>: when tagged."""
    if len(f) < 14:
        return "runt"
    dst, et, p = f[:6], struct.unpack("!H", f[12:14])[0], f[14:]
    tag = ""
    if et == 0x8100 and len(p) >= 4:
        tag = f"vlan{struct.unpack('!H', p[:2])[0] & 0xfff}:"
        et, p = struct.unpack("!H", p[2:4])[0], p[4:]
    if dst[:5] == b"\x01\x80\xc2\x00\x00" and dst[5] == 0 and et <= 1500:
        return tag + "stp"
    if et == 0x8809:
        return tag + "lacp"
    if et == 0x88cc:
        return tag + "lldp"
    if et == 0x8899:
        return tag + "rldp"
    if et == 0x0806:
        return tag + "arp"
    if et == 0x0800 and len(p) >= 20:
        proto, ihl = p[9], (p[0] & 15) * 4
        if proto == 1:
            return tag + "icmp"
        if proto == 2:
            return tag + "igmp"
        if proto in (6, 17) and len(p) >= ihl + 4:
            return tag + ("tcp" if proto == 6 else "udp") + f"/{struct.unpack('!H', p[ihl:ihl + 2])[0]}"
        return tag + f"ip/{proto}"
    return tag + f"eth/{et:04x}"


class Bench:
    def __init__(self, image, board=None, rtlsim=None, args=()):
        kw = {"rtlsim": rtlsim} if rtlsim else {}
        self.sim = Sim(image, board=board, args=["--quiet", *args], **kw)
        self.sim.nic_capture(True)
        self.now = 0
        self.frames = Counter()		# (port, kind) -> frames sent
        self.cables = {}		# port -> the port at the other end
        self.devices = []		# on the ports: take(port, frame), tick(now)
        self.uart = ""			# console output not yet given out: every byte is

    def close(self):
        self.sim.close()

    # --- time
    def _look(self):
        for _, p, f in self.sim.nic_read():
            self.frames[(p, kind(f))] += 1
            if p in self.cables:
                self.sim.nic_inject(self.cables[p], f)
            for d in self.devices:
                d.take(p, f)

    def advance(self, until, stop=None):
        """Run to firmware time `until` (µs), looking at the wire at each grid
        point; stop() (asked at each) True ends it there. True if it did."""
        g = GRID if stop or self.cables else COARSE
        while self.now < until:
            t = min(until, (self.now // g + 1) * g)
            self.now = self.sim.run(f"{(t - self.now) // 1000}.{(t - self.now) % 1000:03d}")
            self._look()
            for d in self.devices:
                d.tick(self.now)
            if stop and stop():
                return True
        return False

    # --- the console
    def take_uart(self):
        self.uart += self.sim.uart_text()
        return self.uart

    def pending(self):
        """What the UART said that no reply has taken, and forget it."""
        self.take_uart()
        out, self.uart = self.uart, ""
        return out

    def _reply(self, regex, until):
        """The output from now up to the end of `regex`'s first match, less
        the ASYNC lines in it, or None if it hasn't come by `until`. What
        follows the match, and the ASYNC lines, stay for pending()."""
        rx = re.compile(regex)
        if not self.advance(until, lambda: rx.search(self.take_uart()) is not None):
            return None
        m = rx.search(self.uart)
        reply, rest = self.uart[:m.end()], self.uart[m.end():]
        keep, moved = [], []
        for l in reply.splitlines(keepends=True):
            if any(a.match(l.rstrip("\n")) for a in ASYNC):
                if keep and keep[-1] == "\n":
                    moved.append(keep.pop())
                moved.append(l)
            else:
                keep.append(l)
        self.uart = "".join(moved) + rest
        return "".join(keep)

    def console(self, line, until, regex=PROMPT):
        """Type `line` (and Enter): (what the UART said before, unasked;
        the reply up to the prompt, or None if it hasn't come by firmware
        time `until`)."""
        before = self.pending()
        self.sim.uart_write(line + "\r")
        return before, self._reply(regex, until)

    def wait_uart(self, regex, until):
        """The output up to `regex` (as a reply), from what is pending on."""
        return self._reply(regex, until)

    # --- the outside
    def cable(self, a, b):
        self.cables[a], self.cables[b] = b, a

    def uncable(self, a):
        b = self.cables.pop(a, None)
        self.cables.pop(b, None)

    def link(self, port, up=True, speed=None):
        self.sim.link(port, up, speed)

    def sfp(self, slot, eeprom):
        self.sim.cmd(f"sfp {slot} {eeprom.hex() if eeprom else 'empty'}")

    # --- results
    def reg_dump(self):
        return self.sim.block("reg dump")

    def table(self, which):
        return self.sim.block(f"table dump {which}")

    def flash(self, addr, n):
        return bytes.fromhex(self.sim.cmd(f"flash read {addr:#x} {n}"))


class Host:
    """A host with a cable into front port `port`: answers ARP for its
    address, and talks HTTP to the switch over TCP, one connection at a
    time, sending a segment only once the last was acknowledged."""
    MSS = 1460

    def __init__(self, bench, port, mac="020000000001", ip="192.168.10.1", switch_ip="192.168.10.247"):
        self.b, self.port = bench, port
        self.mac, self.ip, self.sw_ip = bytes.fromhex(mac), ip_bytes(ip), ip_bytes(switch_ip)
        self.sw_mac = None
        self.rx = []			# TCP segments for us, not yet handled
        self.next_port = 40000
        bench.devices.append(self)

    def tick(self, now):
        pass

    def _eth(self, dst, et, p):
        return dst + self.mac + struct.pack("!H", et) + p

    def _ip(self, proto, p):
        h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(p), 0, 0x4000, 64, proto, 0, self.ip, self.sw_ip)
        return h[:10] + struct.pack("!H", 0xffff ^ csum(h)) + h[12:] + p

    def take(self, port, f):
        if port != self.port or f[:6] not in (self.mac, BCAST) or len(f) < 14:
            return
        et = struct.unpack("!H", f[12:14])[0]
        if et == 0x0806 and len(f) >= 42:
            op, tpa = struct.unpack("!H", f[20:22])[0], f[38:42]
            if op == 1 and tpa == self.ip:		# who has us: we do
                self.b.sim.nic_inject(self.port, self._eth(f[6:12], 0x0806, struct.pack(
                    "!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2, self.mac, self.ip, f[22:28], f[28:32])))
            elif op == 2 and f[28:32] == self.sw_ip:
                self.sw_mac = f[22:28]
        elif et == 0x0800 and f[14 + 9] == 6 and f[14 + 16:14 + 20] == self.ip:
            ihl = (f[14] & 15) * 4
            tot = struct.unpack("!H", f[16:18])[0]
            self.rx.append(f[14 + ihl:14 + tot])

    def arp(self, until):
        if self.sw_mac:
            return True
        self.b.sim.nic_inject(self.port, self._eth(BCAST, 0x0806, struct.pack(
            "!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, self.mac, self.ip, b"\0" * 6, self.sw_ip)))
        return self.b.advance(until, lambda: self.sw_mac is not None)

    def _tcp(self, sport, seq, ack, flags, data=b"", mss=False):
        opt = struct.pack("!BBH", 2, 4, self.MSS) if mss else b""
        h = struct.pack("!HHIIBBHHH", sport, 80, seq & 0xffffffff, ack & 0xffffffff, (5 + len(opt) // 4) << 4,
                        flags, 4096, 0, 0) + opt
        pseudo = self.ip + self.sw_ip + struct.pack("!BBH", 0, 6, len(h) + len(data))
        h = h[:16] + struct.pack("!H", 0xffff ^ csum(pseudo + h + data)) + h[18:]
        self.b.sim.nic_inject(self.port, self._eth(self.sw_mac, 0x0800, self._ip(6, h + data)))

    def http(self, request, until):
        """One request (bytes, headers and body) on a new connection; the
        response (bytes: up to the switch's FIN, or as long as its
        Content-Length says - an upload's answer is followed by a chip reset,
        not a FIN), or None if it hasn't ended by `until`; "reset" if the
        switch refused or reset the connection."""
        if not self.arp(until):
            return None
        sport, self.next_port = self.next_port, self.next_port + 1
        iss = 1000 + (sport - 40000) * 100000
        st = {"snd": iss + 1, "rcv": None, "una": iss, "resp": b"", "fin": False, "rst": False, "mss": 536}
        self.rx = []

        def segs():
            for s in self.rx:
                dport, = struct.unpack("!H", s[2:4])
                if dport != sport:
                    continue
                seq, ack, off, fl = struct.unpack("!IIBB", s[4:14])
                data = s[(off >> 4) * 4:]
                if fl & 0x04:
                    st["rst"] = True
                if fl & 0x10:
                    st["una"] = max(st["una"], ack)
                if fl & 0x02 and st["rcv"] is None:	# SYN-ACK
                    st["rcv"] = seq + 1
                    o = s[20:(off >> 4) * 4]
                    if len(o) >= 4 and o[0] == 2:
                        st["mss"] = min(self.MSS, struct.unpack("!H", o[2:4])[0])
                    continue
                if st["rcv"] is None:
                    continue
                if seq == st["rcv"] and data:
                    st["resp"] += data
                    st["rcv"] += len(data)
                if seq + len(data) == st["rcv"] and fl & 0x01 and not st["fin"]:
                    st["fin"] = True
                    st["rcv"] += 1
                if data or fl & 0x01:
                    self._tcp(sport, st["snd"], st["rcv"], 0x10)
            self.rx = []

        def complete():
            if st["fin"]:
                return True
            head, sep, body = st["resp"].partition(b"\r\n\r\n")
            m = re.search(rb"(?im)^content-length: *(\d+)", head) if sep else None
            return m is not None and len(body) >= int(m.group(1))

        def step(cond):
            def stop():
                segs()
                return st["rst"] or cond()
            self.b.advance(until, stop)
            return not st["rst"] and cond()

        self._tcp(sport, iss, 0, 0x02, mss=True)
        if not step(lambda: st["rcv"] is not None):
            return "reset" if st["rst"] else None
        self._tcp(sport, st["snd"], st["rcv"], 0x10)
        off = 0
        while off < len(request):
            chunk = request[off:off + st["mss"]]
            self._tcp(sport, st["snd"], st["rcv"], 0x18, chunk)
            st["snd"] += len(chunk)		# what our ACKs carry from now on
            if not step(lambda: st["una"] >= st["snd"] or complete()):
                return "reset" if st["rst"] else None
            off += len(chunk)
            if complete():
                break
        if not step(complete):
            return "reset" if st["rst"] else None
        self._tcp(sport, st["snd"], st["rcv"], 0x11)
        st["snd"] += 1
        if st["fin"]:
            step(lambda: st["una"] >= st["snd"])
        return st["resp"]
