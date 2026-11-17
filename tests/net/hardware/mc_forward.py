#!/usr/bin/env python3
"""mc_forward.py SWITCH_IP SENDER A B C - the IPv4 multicast forwarding
behaviours the firmware never sets up on its own, measured by poking the ASIC
state directly (tools/hwprobe.py over POST /cmd) and streaming across one
switch's ports. The evidence for doc/igmp-multicast.md's "(*,G)-only",
"VLAN-scoped" and "lookup-miss actions" lines.

SENDER, A, B, C are four host NICs on four ports of the SAME switch; the
switch must be reachable at SWITCH_IP. PORTS gives each NIC's physical port
(its member-mask bit). On elder + switch-bench:

    sudo python3 mc_forward.py 192.168.10.247 enp4s0 enp3s0 enp5s0 enp6s0

(enp3/4/5/6 = front 1-4 = phys 4-7.) Three probes:
  sg    (*,G) entry -> A-member, (S,G) entry (S = the sender) -> B-member;
        stream from S and from another source. Silicon: (*,G) only.
  vlan  sender + B in VLAN 2, A in VLAN 1, (*,G) mask = A|B. Silicon: B only.
  miss  unregistered group, IPV4_PORT_MC_LM_ACT of the sender's port set to
        flood (with IPV4_UNKN_MC_FLD_PMSK = A), drop, trap, router (router
        port = A). Silicon: A, none, none, A.
Leaves the switch with igmp off and VLAN 1 PVIDs. Needs root and scapy.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwprobe import Switch  # noqa: E402
import mc_oracle as m  # noqa: E402
from scapy.all import Ether, IP, UDP, Raw, conf, get_if_hwaddr, sendp  # noqa: E402

G, H = "239.1.2.3", "239.9.9.9"
LM_ACT, UNKN_FLD, ROUTER = 0x4f78, 0x5368, 0x529c

# switch-bench (SWGT024, RTL8372), 2026-11-17: frames (of 10) heard by A, B, C.
SILICON = {
	"sg S":       (10, 0, 0),	# source matches the (S,G) entry: still the (*,G) member
	"sg other":   (10, 0, 0),
	"vlan":       (0, 10, 0),	# A is in the other VLAN: nothing, despite the mask
	"miss flood": (10, 0, 0),	# exactly IPV4_UNKN_MC_FLD_PMSK
	"miss drop":  (0, 0, 0),
	"miss trap":  (0, 0, 0),	# to the CPU, not the front ports
	"miss router": (10, 0, 0),	# exactly IGMP_ROUTER_PORT
}


def main():
	if len(sys.argv) < 6:
		sys.exit(__doc__)
	conf.verb = 0
	ip, snd, a, b, c = sys.argv[1:6]
	phys =dict(zip((snd, a, b, c), map(int, os.environ.get("PHYS", "5,4,6,7").split(","))))
	sw = Switch(ip)
	nics = [snd, a, b, c]
	res = {}

	def stream(group, src="10.99.0.1"):
		pkt = Ether(src=get_if_hwaddr(snd), dst=m.mcmac(group)) / IP(src=src, dst=group, ttl=4) / \
			UDP(sport=5001, dport=5001) / Raw(b"mc-forward")
		h = m.heard(nics, snd, lambda: sendp(pkt, iface=snd, count=10, inter=0.02, verbose=0),
			    lambda p: p.haslayer(UDP) and p[IP].dst == group)
		return tuple(h.get(n, 0) for n in (a, b, c))

	def bit(n):
		return 1 << phys[n]

	try:
		sw.cmd("igmp off")
		sw.cmd("igmp on")
		time.sleep(0.5)
		sw.l3mc_write(G, "0.0.0.0", mask=bit(a))
		sw.l3mc_write(G, "10.99.0.1", mask=bit(b))
		res["sg S"] = stream(G, "10.99.0.1")
		res["sg other"] = stream(G, "10.99.0.2")

		sw.cmd("igmp off")
		sw.cmd(f"vlan 2 {phys[snd] - 3} {phys[b] - 3}")	# front port = phys - 3 on this board
		sw.cmd(f"pvid {phys[snd] - 3} 2", f"pvid {phys[b] - 3} 2")
		sw.cmd("igmp on")
		time.sleep(0.5)
		sw.l3mc_write(G, "0.0.0.0", mask=bit(a) | bit(b))
		res["vlan"] = stream(G)
		sw.cmd(f"pvid {phys[snd] - 3} 1", f"pvid {phys[b] - 3} 1")

		sw.cmd("igmp off")
		sw.cmd("igmp on")
		time.sleep(0.5)
		shift = 2 * phys[snd]
		sw.regset(UNKN_FLD, bit(a))
		for step, act in (("miss flood", 0), ("miss drop", 1), ("miss trap", 2), ("miss router", 3)):
			if act == 3:
				sw.regset(ROUTER, bit(a))
			sw.regset(LM_ACT, act << shift)
			time.sleep(0.2)
			res[step] = stream(H)
	finally:
		sw.regset(ROUTER, 0)
		sw.cmd("igmp off")

	fails = 0
	for step, want in SILICON.items():
		ok = res.get(step) == want
		fails += not ok
		print(("PASS  " if ok else "FAIL  ") + "%-12s A,B,C = %s%s" % (step, res.get(step), "" if ok else f"  silicon {want}"))
	print("mc_forward:", "matches SILICON" if not fails else f"{fails} differ")
	return 1 if fails else 0


if __name__ == "__main__":
	sys.exit(main())
