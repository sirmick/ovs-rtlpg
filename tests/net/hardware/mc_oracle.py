#!/usr/bin/env python3
"""mc_oracle.py SWITCH_IP SENDER MEMBER OTHER - what a real RTL837x does with
IPv4 multicast under RTLPlayground's IGMP snooping: the oracle for rtlsim's
multicast model (doc/igmp-multicast.md). tests/net/test_igmp.py runs the same
run() against rtlsim and compares with SILICON below.

SENDER, MEMBER, OTHER are three host NICs cabled to three ports of the SAME
switch (elder: enp4s0 enp3s0 enp5s0 on switch-bench, enp7s0 enp8s0 enp9s0
on moker81). They are only ever used
as separate raw-socket hosts - never bridge them, the switch closes the loop.
The switch is left with igmp off. Needs root (raw sockets) and scapy.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../tools"))
from hwprobe import Switch  # noqa: E402

from scapy.all import AsyncSniffer, Ether, IP, UDP, Raw, conf, get_if_hwaddr, sendp  # noqa: E402
from scapy.contrib.igmp import IGMP  # noqa: E402
from scapy.layers.inet import IPOption_Router_Alert  # noqa: E402

G, H, LL = "239.1.2.3", "239.9.9.9", "224.0.0.251"
REPORT, LEAVE = 0x16, 0x17

# switch-bench (SWGT024, RTL8372, RTLPlayground d8715c1) and moker81 (KEEPLINK
# 9XHML, RTL8373, 1be05b9), both the pre-#904 IGMP code, 2026-11-16: identical.
# Per step: frames heard (of 10) by member, other; for igmp steps whether
# either heard the IGMP frame; "entry": the (*,G) L3MC member count a key
# lookup finds (None: no entry).
SILICON = {
	"off":          {"member": 10, "other": 10},
	"on":           {"member": 0, "other": 0, "entry": None},
	"link-local":   {"member": 10, "other": 10},
	"report":       {"forwarded": 0, "entry": 1},
	"stream":       {"member": 10, "other": 0},
	"unregistered": {"member": 0, "other": 0},
	"report-other": {"forwarded": 0, "entry": 2},
	"stream-both":  {"member": 10, "other": 10},
	"leave":        {"forwarded": 0, "entry": 1},
	"leave-other":  {"forwarded": 0, "entry": None},
	"stream-gone":  {"member": 0, "other": 0},
}


def mcmac(ip):
	o = [int(x) for x in ip.split(".")]
	return "01:00:5e:%02x:%02x:%02x" % (o[1] & 0x7f, o[2], o[3])


def heard(nics, src, send, match):
	sn = {i: AsyncSniffer(iface=i, lfilter=match, store=True) for i in nics if i != src}
	for s in sn.values():
		s.start()
	time.sleep(0.5)
	send()
	time.sleep(1.0)
	return {i: len(s.stop()) for i, s in sn.items()}


def stream(nics, src, group, n=10):
	"""n UDP frames to group from src: {nic: count heard}."""
	pkt = Ether(src=get_if_hwaddr(src), dst=mcmac(group)) / IP(src="10.99.0.1", dst=group, ttl=4) / \
		UDP(sport=5001, dport=5001) / Raw(b"mc-oracle " + group.encode())
	return heard(nics, src, lambda: sendp(pkt, iface=src, count=n, inter=0.02, verbose=0),
		     lambda p: p.haslayer(UDP) and p[IP].dst == group and p[UDP].sport == 5001)


def igmp(nics, src, group, typ):
	"""An IGMPv2 report or leave from src: {nic: count heard}."""
	dst = group if typ == REPORT else "224.0.0.2"
	pkt = Ether(src=get_if_hwaddr(src), dst=mcmac(dst)) / \
		IP(src="10.99.0.2", dst=dst, ttl=1, options=[IPOption_Router_Alert()]) / IGMP(type=typ, gaddr=group)
	return heard(nics, src, lambda: sendp(pkt, iface=src, verbose=0),
		     lambda p: p.haslayer(IGMP) and p[IGMP].gaddr == group and p[IGMP].type == typ)


def run(sw, snd, mem, oth, log=print):
	"""The scenario, on any Switch (HTTP or console). Returns {step: result}."""
	conf.verb = 0
	conf.ifaces.reload()	# the NICs may be new veths
	nics = [snd, mem, oth]
	res = {}

	def entry():
		return sw.group_members(G)

	def st(step, src, group):
		h = stream(nics, src, group)
		res[step] = {"member": h[mem], "other": h[oth]}
		log("%-13s %s" % (step, res[step]))

	def ig(step, src, typ):
		h = igmp(nics, src, G, typ)
		res[step] = {"forwarded": sum(h.values()), "entry": entry()}
		log("%-13s %s" % (step, res[step]))

	sw.cmd("igmp off")
	st("off", snd, G)
	sw.cmd("igmp on")
	time.sleep(0.5)
	st("on", snd, G)
	res["on"]["entry"] = entry()
	st("link-local", snd, LL)
	ig("report", mem, REPORT)
	st("stream", snd, G)
	st("unregistered", snd, H)
	ig("report-other", oth, REPORT)
	st("stream-both", snd, G)
	ig("leave", mem, LEAVE)
	ig("leave-other", oth, LEAVE)
	st("stream-gone", snd, G)
	sw.cmd("igmp off")
	return res


def query(nics, src):
	"""An IGMPv2 general query (max response 1 s) from src: {nic: count heard}."""
	pkt = Ether(src=get_if_hwaddr(src), dst="01:00:5e:00:00:01") / \
		IP(src="10.99.0.254", dst="224.0.0.1", ttl=1, options=[IPOption_Router_Alert()]) / \
		IGMP(type=0x11, mrcode=10, gaddr="0.0.0.0")
	return heard(nics, src, lambda: sendp(pkt, iface=src, verbose=0),
		     lambda p: p.haslayer(IGMP) and p[IGMP].type == 0x11)


def gsq(nics, src, group, count=2):
	"""What an IGMPv2 querier sends on a leave (RFC 2236 3): `count`
	group-specific queries to the group, max response 1 s, 1 s apart.
	{nic: count heard}."""
	pkt = Ether(src=get_if_hwaddr(src), dst=mcmac(group)) / \
		IP(src="10.99.0.254", dst=group, ttl=1, options=[IPOption_Router_Alert()]) / \
		IGMP(type=0x11, mrcode=10, gaddr=group)
	return heard(nics, src, lambda: sendp(pkt, iface=src, count=count, inter=1.0, verbose=0),
		     lambda p: p.haslayer(IGMP) and p[IGMP].type == 0x11 and p[IGMP].gaddr == group)


class Host:
	"""An IGMPv2 host on a NIC (RFC 2236 host side): join() sends an
	unsolicited report and keeps the group, leave() sends a leave and drops
	it, and while a group is joined every general or group-specific query
	the NIC hears is answered with a report for it (each host is alone on
	its port, so no report suppression). vanish() drops the groups silently,
	as an unplugged host does."""
	def __init__(self, nic):
		self.nic, self.groups = nic, set()
		self.sn = AsyncSniffer(iface=nic, store=False, prn=self._rx,
				       lfilter=lambda p: p.haslayer(IGMP) and p[IGMP].type == 0x11)
		self.sn.start()

	def _rx(self, p):
		g = p[IGMP].gaddr
		for grp in list(self.groups):
			if g in ("0.0.0.0", grp):
				self._send(grp, REPORT)

	def _send(self, group, typ):
		dst = group if typ == REPORT else "224.0.0.2"
		sendp(Ether(src=get_if_hwaddr(self.nic), dst=mcmac(dst)) /
		      IP(src="10.99.0.2", dst=dst, ttl=1, options=[IPOption_Router_Alert()]) /
		      IGMP(type=typ, gaddr=group), iface=self.nic, verbose=0)

	def join(self, group):
		self.groups.add(group)
		self._send(group, REPORT)

	def leave(self, group):
		self.groups.discard(group)
		self._send(group, LEAVE)

	def vanish(self):
		self.groups.clear()

	def stop(self):
		self.sn.stop()


def run_router(sw, snd, mem, oth, rtr, age_s=0, log=print):
	"""The scenario with a fourth host, a multicast router / IGMP querier on
	rtr: who gets the stream, unregistered and IGMP traffic once a router port
	exists (RFC 4541 forwards all of them to router ports too), a leave that
	no group-specific query follows (rtr doesn't send one), and - if age_s -
	whether a membership nobody refreshes ages out after age_s seconds.
	Returns {step: {role: frames heard}} (roles S M O R, the source left out)
	plus "entry" (members of G) for the IGMP steps."""
	conf.verb = 0
	conf.ifaces.reload()	# the NICs may be new veths
	nics = [snd, mem, oth, rtr]
	role = {snd: "S", mem: "M", oth: "O", rtr: "R"}
	res = {}

	def rec(step, h, ig=False):
		res[step] = {role[n]: c for n, c in sorted(h.items(), key=lambda x: role[x[0]])}
		if ig:
			res[step]["entry"] = sw.group_members(G)
		log("%-14s %s" % (step, res[step]))

	hm, ho = Host(mem), Host(oth)

	def act(nic, fn):	# who heard the IGMP frame fn sends from nic
		return heard(nics, nic, fn, lambda p: p.haslayer(IGMP) and p[IGMP].type != 0x11)

	sw.cmd("igmp off")
	rec("off", stream(nics, snd, G))
	sw.cmd("igmp on")
	time.sleep(0.5)
	rec("on", stream(nics, snd, G))
	rec("query", query(nics, rtr))
	time.sleep(1.5)		# past the max response time: a querier exists now (Linux floods until then)
	rec("unregistered", stream(nics, snd, H))
	rec("link-local", stream(nics, snd, LL))
	rec("report", act(mem, lambda: hm.join(G)), True)
	rec("stream", stream(nics, snd, G))
	rec("report-other", act(oth, lambda: ho.join(G)), True)
	rec("stream-both", stream(nics, snd, G))
	rec("leave", act(mem, lambda: hm.leave(G)), True)
	rec("stream-leave", stream(nics, snd, G))
	rec("gsq", gsq(nics, rtr, G))	# the querier's follow-up: O answers, M doesn't
	time.sleep(1.5)
	rec("stream-lmqt", stream(nics, snd, G))
	res["stream-lmqt"]["entry"] = sw.group_members(G)
	rec("leave-other", act(oth, lambda: ho.leave(G)), True)
	gsq(nics, rtr, G)
	time.sleep(1.5)
	rec("stream-gone", stream(nics, snd, G))
	res["stream-gone"]["entry"] = sw.group_members(G)
	rec("re-report", act(mem, lambda: hm.join(G)), True)
	time.sleep(1)
	rec("general-query", query(nics, rtr))	# M answers it: still a member
	time.sleep(1.5)
	rec("stream-kept", stream(nics, snd, G))
	res["stream-kept"]["entry"] = sw.group_members(G)
	if age_s:
		hm.vanish()		# unplugged without a leave
		query(nics, rtr)	# the router port stays; nobody answers
		time.sleep(age_s)
		res["aged"] = {"entry": sw.group_members(G)}
		h = stream(nics, snd, G)
		res["aged"].update({role[n]: c for n, c in h.items()})
		log("%-14s %s" % ("aged", res["aged"]))
	sw.cmd("igmp off")
	hm.stop()
	ho.stop()
	return res


def diff(res, ref=SILICON):
	"""[(step, got, want)] where res differs from ref."""
	return [(s, res.get(s), w) for s, w in ref.items() if res.get(s) != w]


def main():
	if len(sys.argv) < 5:
		sys.exit(__doc__)
	res = run(Switch(sys.argv[1]), *sys.argv[2:5])
	d = diff(res)
	for s, got, want in d:
		print("DIFF  %-13s got %s, silicon %s" % (s, got, want))
	print("matches SILICON" if not d else f"{len(d)} steps differ from SILICON")


if __name__ == "__main__":
	main()
