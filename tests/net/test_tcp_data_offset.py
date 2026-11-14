#!/usr/bin/env python3
"""test_tcp_data_offset.py - TCP data-offset underflow in uIP.

    tests/net/test_tcp_data_offset.py IMAGE [--rtlsim build/rtlsim]

uip_process() computes the TCP payload length as
    uip_len = uip_len - (data_offset * 4) - UIP_IPH_LEN;
with no check that the claimed header fits the received bytes. A segment with
data offset 15 (a 60-byte header) but only a few bytes of payload underflows
uip_len to ~65500, so the stack uip_add_rcv_nxt()s data it never received and
hands the application uip_appdata past the end of uip_buf.

Like CVE-2020-17437, the proof reads the acknowledgement number back: open a
connection to the web server, send a 1-byte segment that claims data offset
15, and watch the switch's ACK. Unpatched, rcv_nxt leaps by ~65500 (or httpd
runs off the end and the switch stops answering); patched, the segment is
dropped and the connection is untouched.

The switch IP is read from the firmware (switch_ip), so this runs unchanged on
an upstream-format image or our compact build. No privileges needed.
"""
import argparse, os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT, switch_ip, peer_ip
import test_nic
from test_nic import Peer, eth, ipv4, csum, ip_bytes, PEER_MAC, SWITCH_MAC

failures = 0
ISN = 1000
SPORT = 40000


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def tcp(seq, ack, flags, offset_words=5, payload=b""):
    """A TCP segment to port 80 with a chosen data offset (in 32-bit words)."""
    h = struct.pack("!HHIIBBHHH", SPORT, 80, seq & 0xffffffff, ack & 0xffffffff,
                    offset_words << 4, flags, 4096, 0, 0) + payload
    pseudo = test_nic.PEER_IP + test_nic.SWITCH_IP + struct.pack("!BBH", 0, 6, len(h))
    return h[:16] + struct.pack("!H", 0xffff ^ csum(pseudo + h)) + h[18:]


def recv_tcp(p, timeout=2.0):
    r = p.reply(0x0800, timeout=timeout)
    while r is not None and r[23] != 6:
        r = p.reply(0x0800, timeout=timeout)
    if r is None:
        return None
    t = r[14:][(r[14] & 0xf) * 4:]
    return struct.unpack("!I", t[4:8])[0], struct.unpack("!I", t[8:12])[0], t[13]


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

            arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, test_nic.PEER_IP, b"\0" * 6, test_nic.SWITCH_IP)
            p.send(eth(b"\xff" * 6, 0x0806, arp))
            check(p.reply(0x0806, timeout=2) is not None, "switch answers ARP")

            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN, 0, 0x02))))
            sa = recv_tcp(p)
            ok = sa is not None and (sa[2] & 0x12) == 0x12 and sa[1] == ISN + 1
            check(ok, "SYN -> SYN-ACK")
            if not ok:
                return 1
            sw_isn = sa[0]
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN + 1, sw_isn + 1, 0x10))))

            # 1 byte of data, but the header claims a 60-byte TCP header.
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(ISN + 1, sw_isn + 1, 0x10, offset_words=15, payload=b"A"))))
            r = recv_tcp(p, timeout=3)
            advance = (r[1] - (ISN + 1)) & 0xffffffff if r else 0
            print(f"switch acknowledged rcv_nxt +{advance} for a 1-byte payload"
                  + ("" if r else " (no reply)"))
            check(advance <= 1, f"an oversized data offset does not underflow uip_len "
                  f"(rcv_nxt +{advance}, not the ~65500 of the unchecked subtraction)")

            p.send(eth(b"\xff" * 6, 0x0806, arp))
            check(p.reply(0x0806, timeout=2) is not None, "switch is still running afterwards")
        finally:
            c.close()

    print(f"test_tcp_data_offset: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
