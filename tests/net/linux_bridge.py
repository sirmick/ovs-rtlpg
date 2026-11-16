"""linux_bridge.py - a Linux bridge with IGMP snooping as the reference switch.

The multicast oracle (tests/net/hardware/mc_oracle.py) drives a switch through
two calls: cmd("igmp on"/"igmp off") and group_members(group). LinuxBridge
implements them on a bridge in its own network namespace, with the oracle's
veth hosts as its ports, so the same scenario that runs on silicon and on
rtlsim runs on Linux's snooping code (RFC 4541), the reference for what a
snooping switch should do. Needs root.

    br = LinuxBridge(["igS", "igM", "igO"], **opts)   # veth names; NAME-p is the port
    ...
    br.stop()

opts are bridge options (ip link ... type bridge), e.g. mcast_querier=0,
mcast_membership_interval=500 (centiseconds). Port options go in port_opts
(e.g. {"mcast_flood": "off"}).
"""
import json
import subprocess

DEFAULTS = {"mcast_snooping": 0, "mcast_querier": 0, "mcast_igmp_version": 2}


def sh(*c, check=True):
	return subprocess.run(c, check=check, capture_output=True, text=True)


class LinuxBridge:
	def __init__(self, nics, ns="refbr", port_opts=None, **opts):
		self.ns, self.nics = ns, list(nics)
		self.ports = [n + "-p" for n in self.nics]
		subprocess.run(["ip", "netns", "del", ns], capture_output=True)
		sh("ip", "netns", "add", ns)
		ip = ["ip", "-n", ns]
		sh(*ip, "link", "set", "lo", "up")
		o = dict(DEFAULTS, **opts)
		sh(*ip, "link", "add", "br0", "type", "bridge", *[str(x) for kv in o.items() for x in kv])
		sh(*ip, "link", "set", "br0", "up")
		for n, p in zip(self.nics, self.ports):
			subprocess.run(["ip", "link", "del", n], capture_output=True)
			sh("ip", "link", "add", n, "type", "veth", "peer", "name", p)
			sh("sysctl", "-q", "-w", f"net.ipv6.conf.{n}.disable_ipv6=1")
			sh("ip", "link", "set", n, "up")
			sh("ip", "link", "set", p, "netns", ns)
			sh(*ip, "link", "set", p, "master", "br0")
			sh(*ip, "link", "set", p, "up")
			if port_opts:
				sh("bridge", "-n", ns, "link", "set", "dev", p, *[str(x) for kv in port_opts.items() for x in kv])

	def cmd(self, *lines):
		for line in lines:
			if line in ("igmp on", "igmp off"):
				sh("ip", "-n", self.ns, "link", "set", "br0", "type", "bridge",
				   "mcast_snooping", "1" if line == "igmp on" else "0")
			else:
				raise ValueError(f"LinuxBridge: no command {line!r}")
		return ""

	def mdb(self):
		"""[(group, port)] in the bridge's multicast database."""
		out = json.loads(sh("bridge", "-n", self.ns, "-j", "mdb", "show").stdout or "[]")
		res = []
		for br in out:
			for e in br.get("mdb", []):
				res.append((e["grp"], e["port"]))
		return res

	def group_members(self, group):
		ports = {p for g, p in self.mdb() if g == group}
		return len(ports) or None

	def router_ports(self):
		out = json.loads(sh("bridge", "-n", self.ns, "-j", "mdb", "show").stdout or "[]")
		return [r.get("port") for br in out for r in br.get("router", {}).get("ports", [])]

	def stop(self):
		for n in self.nics:
			subprocess.run(["ip", "link", "del", n], capture_output=True)
		subprocess.run(["ip", "netns", "del", self.ns], capture_output=True)
