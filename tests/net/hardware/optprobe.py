import sys, time
from scapy.all import ARP, AsyncSniffer, Ether, IP, TCP, Raw, conf, get_if_hwaddr, sendp, srp1
IFACE=__import__("os").environ.get("RTLHW_IFACE","eth0"); conf.verb=0; dst=sys.argv[1]; peer="172.16.16.111"
optbytes=bytes.fromhex(sys.argv[2])   # TCP options to append
mymac=get_if_hwaddr(IFACE); swmac=srp1(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst=dst),iface=IFACE,timeout=3)[ARP].hwsrc
cap=[]
def h(p):
    if p.haslayer(TCP) and p.haslayer(IP) and p[IP].src==dst: cap.append(p)
snf=AsyncSniffer(iface=IFACE, filter=f"tcp and host {dst}", prn=h, store=False); snf.start(); time.sleep(0.3)
off=(20+len(optbytes))//4
syn=Ether(dst=swmac,src=mymac)/IP(src=peer,dst=dst)/TCP(sport=45000,dport=80,flags="S",seq=1000,window=4096,dataofs=off)/Raw(optbytes)
sendp(syn, iface=IFACE, verbose=0); print(f"sent SYN with options {optbytes.hex()} (dataofs={off})")
time.sleep(1.5); snf.stop()
print(f"replies: {len(cap)}  " + " ".join(f"0x{int(p[TCP].flags):02x}" for p in cap[:4]))
