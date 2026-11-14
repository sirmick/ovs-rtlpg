#!/usr/bin/env python3
"""test_uip_hardening.py - a crafted TCP SYN must not hang the main loop.

    tests/net/test_uip_hardening.py IMAGE [--rtlsim build/rtlsim]

uip_process() walks the TCP options of an incoming SYN with an 8-bit cursor
and advances it by an attacker-controlled option length with no bound. A
crafted option list wraps the cursor instead of ending the walk, so the loop
never terminates and the firmware's single main loop never returns - ping,
ARP and the console all stop. One SYN to the web server (port 80) is a remote
denial of service.

The proof is liveness: send the cursor-cycling SYN, then check the switch
still answers ARP. The option bytes are 01 03 FF 00 (NOOP; then an unknown
option of length 255 that wraps the cursor back to 0) with the data offset
set to cover them. On the unpatched firmware the switch stops answering; with
process_tcp_options() bounded, the walk terminates and it stays alive.

(The urgent-pointer out-of-bounds, CVE-2020-17437, is a separate bug with its
own proof in test_cve_2020_17437.py.)

The switch IP is read from the firmware (switch_ip), so this runs unchanged on
an upstream-format image or our compact build. No privileges needed - frames
go straight onto the --nic socket.
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


def syn(options):
    """A TCP SYN to port 80 carrying `options`, with its checksum filled in.
    The data offset counts the 20-byte base plus the options."""
    off = (20 + len(options)) // 4
    h = struct.pack("!HHIIBBHHH", 40000, 80, 1000, 0, off << 4, 0x02, 4096, 0, 0) + options
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
            check(alive(p), "switch answers ARP before the crafted SYN")

            # SYN with a cursor-cycling option list (01 03 FF 00).
            p.send(eth(SWITCH_MAC, 0x0800, ipv4(6, syn(bytes([0x01, 0x03, 0xFF, 0x00])))))
            check(alive(p), "crafted SYN options do not hang the main loop")
        finally:
            c.close()

    print(f"test_uip_hardening: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
