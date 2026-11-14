#!/usr/bin/env python3
"""test_uip_hardening.py - crafted TCP frames must not hang or underflow uIP.

    tests/net/test_uip_hardening.py IMAGE [--rtlsim build/rtlsim]

A "broken on upstream, fixed on the branch" proof for the TCP option /
urgent-pointer hardening ported into RTLPlayground's uip.c from Contiki-NG
(process_tcp_options bounds + the URG length cap). Two regressions:

  1. A SYN whose TCP options make the old 8-bit option cursor cycle (data
     offset 6, option bytes 01 03 FF 00: NOOP, then an unknown option of
     length 255 that wraps the cursor back to 0). On the unpatched firmware
     the option walk never terminates and the whole main loop stops; on the
     fix the walk is bounded and the switch stays alive.

  2. A segment with URG set and a huge urgent pointer. On the unpatched build
     uip_len underflows and uip_appdata is pushed out of uip_buf; the fix caps
     the urgent length at the segment length.

Both are judged by liveness: after the crafted frame, a normal ARP must still
be answered. No privileges needed - frames go straight onto the --nic socket.
The switch IP is read from the firmware (switch_ip), so this runs unchanged on
an upstream-format image (config.txt address) or our compact build.
"""
import argparse, os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT, switch_ip, peer_ip
import test_nic
from test_nic import Peer, eth, ipv4, csum, ip_bytes, PEER_MAC, SWITCH_MAC

failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def tcp(flags, options=b"", urgp=0):
    """A TCP header to port 80. The data offset counts the 20-byte base plus
    options, so a crafted option list is reachable by the firmware's parser."""
    off = (20 + len(options)) // 4
    h = struct.pack("!HHIIBBHHH", 40000, 80, 1000, 0, off << 4, flags, 4096, 0, urgp) + options
    pseudo = test_nic.PEER_IP + test_nic.SWITCH_IP + struct.pack("!BBH", 0, 6, len(h))
    return h[:16] + struct.pack("!H", 0xffff ^ csum(pseudo + h)) + h[18:]


def arp_frame():
    a = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, test_nic.PEER_IP, b"\0" * 6, test_nic.SWITCH_IP)
    return eth(b"\xff" * 6, 0x0806, a)


def alive(p):
    """The switch still answers ARP -> the main loop is running."""
    p.send(arp_frame())
    r = p.reply(0x0806, timeout=1.5)
    return r is not None and r[6:12] == SWITCH_MAC


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
            check(alive(p), "baseline: the switch answers ARP before any crafted frame")

            # 1. SYN with a cursor-cycling option list (01 03 FF 00).
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(0x02, options=bytes([0x01, 0x03, 0xFF, 0x00])))))
            check(alive(p), "crafted SYN options do not hang the main loop")

            # 2. A segment with URG set and a huge urgent pointer.
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, tcp(0x10 | 0x20, urgp=0xFFFF) + b"x" * 8)))
            check(alive(p), "oversized urgent pointer does not wedge the stack")
        finally:
            c.close()

    print(f"test_uip_hardening: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
