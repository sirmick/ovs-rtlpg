#!/usr/bin/env python3
"""test_l2_age.py - an L2 entry with age 0 isn't valid: the CPU's frames to it flood.

    tests/net/test_l2_age.py IMAGE [--board BOARD] [--rtlsim build/rtlsim]

A peer on the first copper port opens a TCP connection to the web server and
never ACKs, so uIP retransmits its SYN-ACK with no frame from the peer. Before
the retransmit the peer's dynamic entry is written back (the firmware's regset,
as its L2 delete does) with age 0 and the second copper port. Silicon doesn't
use an entry with age 0 (tables.h, word C): the retransmit floods, so it still
reaches the peer's port. A valid entry (age 7) on the second port sends it
there alone.
"""
import argparse, json, os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from rtlsim_ctl import Sim
from console import DEFAULT_IP, peer_ip
from test_nic import ip_bytes, csum, PEER_MAC, SWITCH_MAC

PROMPT = r"\n(\[confirm [0-9]+\] )?\*?> $"
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    ports = json.load(open(a.board))["ports"] if a.board else [{"phys": p} for p in (4, 5, 6, 7)]
    pa, pb = [p["phys"] for p in ports if "sfp" not in p][:2]

    with Sim(a.image, board=a.board, rtlsim=a.rtlsim, stderr=open(os.devnull, "w")) as sim:
        def con(line):
            sim.uart_read()
            sim.uart_write(line + "\r")
            sim.run_until_uart(PROMPT, max_ms=3000)
            return sim.uart_text().replace("\r", "")

        check(sim.run_until_uart(PROMPT, max_ms=30000) is not None, "boots to the prompt")
        m = re.search(r"(?:mgmt\.ip|Current IP:) ([\d.]+)", con("get mgmt.ip") + con("ip"))
        sw = m.group(1) if m else DEFAULT_IP
        sip, pip = ip_bytes(sw), ip_bytes(peer_ip(sw))
        m = re.search(r"Setting MAC to: (\S+)", sim.uart_text())
        smac = bytes.fromhex(m.group(1).replace(":", "")) if m else SWITCH_MAC
        sim.link(pa, True)
        sim.link(pb, True)
        sim.run(500)

        def syn(sport):
            t = struct.pack("!HHIIBBHHH", sport, 80, 1000, 0, 5 << 4, 0x02, 4096, 0, 0)
            t = t[:16] + struct.pack("!H", 0xffff ^ csum(pip + sip + struct.pack("!BBH", 0, 6, len(t)) + t)) + t[18:]
            h = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(t), 1, 0, 64, 6, 0, pip, sip)
            h = h[:10] + struct.pack("!H", 0xffff ^ csum(h)) + h[12:]
            return h + t

        def synacks(frames):
            """the ports the switch's SYN-ACKs to the peer left by"""
            return {p for _, p, f in frames if f[:6] == PEER_MAC and f[12:14] == b"\x08\x00" and f[23] == 6
                    and f[47] & 0x12 == 0x12}

        # ARP first, so uIP knows the peer; then the SYN, its SYN-ACK
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, pip, b"\0" * 6, sip)
        sim.nic_capture(True)
        sim.nic_inject(pa, b"\xff" * 6 + PEER_MAC + b"\x08\x06" + arp + bytes(18))
        sim.run(200)
        sim.nic_read()

        def entry():
            for l in sim.block("table dump l2"):
                w = l.split()
                b = int(w[2], 16)
                if (b & 0xffff) == (PEER_MAC[0] << 8 | PEER_MAC[1]) and int(w[1], 16) == int.from_bytes(PEER_MAC[2:], "big"):
                    return [int(x, 16) for x in w[1:4]]
            return None

        def rewrite(port, age):
            """the peer's entry on port, with age, as the firmware writes one (TBL_DATA_0 method 0: by MAC)"""
            e = entry()
            wa, wb, wc = e[0], e[1] & ~(3 << 30) | (port & 3) << 30, e[2] & ~0x1f | age << 2 | port >> 2
            for r, v in ((0x5cb0, 0), (0x5cb8, wa), (0x5cbc, wb), (0x5cc0, wc), (0x5cac, 0x0403)):
                con("regset %04x %08x" % (r, v))

        for sport, age, what in ((40001, 0, "age 0: the retransmit floods, the peer's port has it"),
                                 (40002, 7, "age 7: the entry's port alone")):
            e = entry()
            check(e is not None and ((e[1] >> 30) & 3 | (e[2] & 3) << 2) == pa, "the peer learned on port %d" % pa)
            sim.nic_inject(pa, smac + PEER_MAC + b"\x08\x00" + syn(sport))
            sim.run(100)
            check(pa in synacks(sim.nic_read()), "SYN %d: the SYN-ACK to the peer's port" % sport)
            rewrite(pb, age)
            sim.run(5000)           # uIP's retransmit, the peer silent
            got = synacks(sim.nic_read())
            check(pa in got and pb in got if age == 0 else got == {pb}, what + " (%s)" % sorted(got))
            # the peer speaks again: it is learned back on its own port
            sim.nic_inject(pa, b"\xff" * 6 + PEER_MAC + b"\x08\x06" + arp + bytes(18))
            sim.run(200)
            sim.nic_read()

    print(f"test_l2_age: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
