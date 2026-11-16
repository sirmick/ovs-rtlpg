#!/usr/bin/env python3
"""test_igmp.py - rtlsim's IPv4 multicast under IGMP snooping matches silicon.

    tests/net/test_igmp.py IMAGE [--board boards/X.json] [--rtlsim build/rtlsim]
    tests/net/test_igmp.py IMAGE --oracle linux [--age S]

Runs tests/net/hardware/mc_oracle.py's scenario - igmp on/off, an unregistered
group, link-local, reports and leaves from two hosts, a stream after each -
against a switch on OVS, and compares every step with what switch-bench (a
real SWGT024) did (mc_oracle.SILICON). The three hosts are veth ends in this
namespace on front ports 1-3, so the oracle sniffs them exactly as it sniffs
real NICs. Both bench switches (switch-bench, RTL8372; moker81, RTL8373)
gave the same table, so it holds for either board.

--oracle linux judges the FIRMWARE instead: mc_oracle.run_router - a fourth
host as multicast router / IGMP querier, member hosts that answer queries,
leaves the querier follows up, a host that vanishes - first on a Linux bridge
with IGMP snooping (tests/net/linux_bridge.py, RFC 4541's reference
behaviour), then on the firmware in rtlsim, on the same four veths; every step
where the firmware does otherwise is a FAIL. Differences are expected until
the firmware's snooping is complete, so this is not in make test-net. Needs root (re-runs
itself with sudo); skipped without it.
"""
import argparse, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
sys.path.insert(0, os.path.join(HERE, "../../tools"))
sys.path.insert(0, os.path.join(HERE, "hardware"))
from console import PROMPT
from rtlswitch import Lab
from hwprobe import Switch

NICS = {"1": "igS", "2": "igM", "3": "igO", "4": "igR"}	# sender, member, other, router


class ConsoleSwitch(Switch):
	"""hwprobe's Switch over the simulator's serial console."""
	def __init__(self, console):
		super().__init__("rtlsim")
		self.c = console

	def cmd(self, *lines):
		out = []
		for line in lines:
			for _ in range(5):	# and if one is still lost, the echo shows it: retry
				start = len(self.c.log)
				for ch in line + "\r":	# typed: a busy firmware drops a burst's characters
					self.c.p.stdin.write(ch.encode())
					self.c.p.stdin.flush()
					time.sleep(0.005)
				self.c.expect(PROMPT, 10)
				got = self.c.log[start:].decode(errors="replace")
				if "\n" + line + "\n" in "\n" + got:
					break
			out.append(got)
		if os.environ.get("IGMP_DEBUG"):
			print("console:", repr("".join(out)), file=sys.stderr)
		return "".join(out)


def sh(*cmd):
	subprocess.run(cmd, check=True, capture_output=True)


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("image")
	ap.add_argument("--board", default=os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json"))
	ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
	ap.add_argument("--oracle", choices=["silicon", "linux"], default="silicon")
	ap.add_argument("--age", type=int, default=32, help="--oracle linux: membership interval + 2, seconds")
	a = ap.parse_args()
	if os.geteuid():
		if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
			print("igmp: no passwordless sudo, skipped")
			return 0
		return subprocess.run(["sudo", "-n", sys.executable, *sys.argv]).returncode
	import mc_oracle
	nics = [NICS[f] for f in "1234"]
	ref = None
	if a.oracle == "linux":
		os.environ["PATH"] += ":/usr/sbin:/sbin"
		from linux_bridge import LinuxBridge
		br = LinuxBridge(nics, mcast_membership_interval=(a.age - 2) * 100)
		try:
			ref = mc_oracle.run_router(br, *nics, age_s=a.age, log=lambda s: None)
		finally:
			br.stop()

	for n in NICS.values():
		subprocess.run(["ip", "link", "del", n], capture_output=True)
		sh("ip", "link", "add", n, "type", "veth", "peer", "name", n + "-p")
		sh("sysctl", "-q", "-w", f"net.ipv6.conf.{n}.disable_ipv6=1")
		sh("ethtool", "-K", n, "tx", "off")
		sh("ip", "link", "set", n, "up")
	lab = Lab(a.image, board=a.board, name=f"i{os.getpid() % 100000}", ports={f: n + "-p" for f, n in NICS.items()},
		  rtlsim=a.rtlsim, console=True, stderr=open(os.devnull, "w"))
	failures = 0
	try:
		lab.start(attach=False)
		if not lab.console.expect(PROMPT, 30):
			print("FAIL  boots to the prompt")
			return 1
		lab.attach()
		time.sleep(2)
		sw = ConsoleSwitch(lab.console)
		if ref is None:
			res = mc_oracle.run(sw, NICS["1"], NICS["2"], NICS["3"], log=lambda s: None)
			ref, name = mc_oracle.SILICON, "silicon"
		else:
			res = mc_oracle.run_router(sw, *nics, age_s=a.age, log=lambda s: None)
			name = "linux"
		for step, want in ref.items():
			ok = res.get(step) == want
			failures += not ok
			print(("PASS  " if ok else "FAIL  ") + f"{step}: {res.get(step)}" + ("" if ok else f", {name} {want}"))
	finally:
		lab.stop()
		for n in NICS.values():
			subprocess.run(["ip", "link", "del", n], capture_output=True)
	print(f"test_igmp: {'ALL PASS' if not failures else f'{failures} FAILED'}")
	return 1 if failures else 0


if __name__ == "__main__":
	sys.exit(main())
