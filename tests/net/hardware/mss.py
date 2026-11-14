#!/usr/bin/env python3
"""mss.py SWITCH_IP [MSS] - does the switch CPU honor an advertised TCP MSS
option (so options reach the uIP parser) or ignore it (options sanitized by
the ASIC before the CPU)? Handshake offering a tiny MSS, GET, measure the
switch's response segment sizes."""
import random, sys, time
from scapy.all import ARP, AsyncSniffer, Ether, IP, TCP, Raw, conf, get_if_hwaddr, sendp, srp1

IFACE = __import__("os").environ.get("RTLHW_IFACE", "eth0")
conf.verb = 0
dst = sys.argv[1]
peer = "172.16.16.111"
mss = int(sys.argv[2]) if len(sys.argv) > 2 else 40
mymac = get_if_hwaddr(IFACE)
sport = random.randint(20000, 60000)
isn = random.randint(1, 1000000)
swmac = srp1(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=dst), iface=IFACE, timeout=3)[ARP].hwsrc
cap = []
st = {"sw": None}


def tx(payload=b"", **t):
    pkt = Ether(dst=swmac, src=mymac) / IP(src=peer, dst=dst) / TCP(sport=sport, dport=80, **t)
    if payload:
        pkt = pkt / Raw(payload)
    sendp(pkt, iface=IFACE, verbose=0)


def handler(p):
    if p.haslayer(ARP) and p[ARP].op == 1 and p[ARP].pdst == peer:
        sendp(Ether(dst=p[ARP].hwsrc, src=mymac) / ARP(op=2, psrc=peer, hwsrc=mymac, pdst=p[ARP].psrc, hwdst=p[ARP].hwsrc), iface=IFACE, verbose=0)
    elif p.haslayer(TCP) and p.haslayer(IP) and p[IP].src == dst and p[TCP].dport == sport:
        cap.append(p)
        if (int(p[TCP].flags) & 0x12) == 0x12 and st["sw"] is None:
            st["sw"] = int(p[TCP].seq)
            tx(flags="A", seq=isn + 1, ack=st["sw"] + 1)


snf = AsyncSniffer(iface=IFACE, filter=f"(tcp and host {dst}) or arp", prn=handler, store=False)
snf.start()
time.sleep(0.3)
sendp(Ether(dst=swmac, src=mymac) / ARP(op=2, psrc=peer, hwsrc=mymac, pdst=dst, hwdst=swmac), iface=IFACE, verbose=0)
opt = bytes([0x02, 0x04, (mss >> 8) & 0xff, mss & 0xff])
sendp(Ether(dst=swmac, src=mymac) / IP(src=peer, dst=dst) / TCP(sport=sport, dport=80, flags="S", seq=isn, window=8192, dataofs=6) / Raw(opt), iface=IFACE, verbose=0)
for _ in range(200):
    if st["sw"] is not None:
        break
    time.sleep(0.01)
if st["sw"] is None:
    print("no SYN-ACK")
    snf.stop()
    sys.exit()
sw = st["sw"]
time.sleep(0.2)
tx(b"GET / HTTP/1.0\r\n\r\n", flags="PA", seq=isn + 1, ack=sw + 1)
time.sleep(1.5)
tx(flags="R", seq=isn + 2, ack=sw + 1)
snf.stop()
data = [len(bytes(p[Raw])) for p in cap if p.haslayer(Raw) and p.haslayer(TCP)]
verdict = "MSS HONORED (options reach the CPU)" if data and max(data) <= mss else "MSS IGNORED (CPU did not see the option)"
print(f"offered MSS={mss}; switch response data-segment sizes: {data}")
print("->", verdict)
