#!/usr/bin/env python3
"""mcprobe.py - differential probe of the L3MC table engine: rtlsim vs a real
RTL837x, driven to the same ASIC state by direct register pokes (no firmware
IGMP logic). The companion to rtlpg-tests's net/test_igmp.py: that proves forwarding,
this proves the table engine (switch_tables) the forwarding rests on.

    mcprobe.py IMAGE [--rtlsim build/rtlsim]        # run on rtlsim, check vs EXPECTED
    mcprobe.py --switch serial:/dev/ttyUSB1         # re-measure on silicon (serial)
    mcprobe.py --switch http:192.168.10.247         # re-measure on silicon (POST /cmd)

EXPECTED is what switch-bench (SWGT024/RTL8372) and moker81 (RTL8373) do,
measured 2026-11-16 (see doc/igmp-multicast.md). Each probe writes/looks-up/
deletes L3MC entries through the ASIC's own TBL engine and records the result;
the sim must match silicon step for step. Entry hash *index* is excluded from
the comparison (sim uses a placeholder hash; see tables.h).
"""
import argparse, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hwprobe

G = "239.1.2.3"

# What a real RTL837x does, measured on switch-bench + moker81 (2026-11-16).
EXPECTED = {
	"write(*,G)":        {"found": True, "b": 0x1f010203, "c": 0x00000004},
	"lookup(*,G)":       {"found": True},
	"lookup(S,G) exact": {"found": False},	# specific-source lookup misses a (*,G) entry
	"hash-clear idx0":   {"found": True},	# write+clear at index 0 leaves the entry
	"index-clear":       {"found": False},	# write+clear at the entry's own index deletes it
}


class SerialSwitch(hwprobe.Switch):
	def __init__(self, dev):
		import termios
		self._t = termios
		self.fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
		a = termios.tcgetattr(self.fd)
		a[0] = a[1] = a[3] = 0
		a[2] = (a[2] & ~termios.PARENB & ~termios.CSTOPB & ~termios.CSIZE & ~termios.CRTSCTS) | termios.CS8 | termios.CLOCAL | termios.CREAD
		a[4] = a[5] = termios.B115200
		termios.tcsetattr(self.fd, termios.TCSANOW, a)
		termios.tcflush(self.fd, termios.TCIOFLUSH)

	def cmd(self, *lines):
		import select
		out = b""
		for ln in lines:
			self._t.tcflush(self.fd, self._t.TCIFLUSH)
			for ch in ln + "\r":		# type a char at a time: bursts drop characters
				os.write(self.fd, ch.encode())
				time.sleep(0.004)
			end, buf = time.time() + 2, b""
			while time.time() < end:
				r, _, _ = select.select([self.fd], [], [], 0.2)
				if r:
					try:
						buf += os.read(self.fd, 4096)
					except BlockingIOError:
						pass
				if buf.endswith(b"> "):
					break
			out += buf
		return out.decode(errors="replace")


class SimSwitch(hwprobe.Switch):
	def __init__(self, image, rtlsim):
		from simconsole import Console
		self.PROMPT = b"\n> "	# the firmware's prompt
		self.c = Console(rtlsim, image, ["--board", os.path.join(HERE, "../boards/swgt024_v2_0_unmanaged.json"), "--cycles", "0"])
		if not self.c.expect(PROMPT, 30):
			raise SystemExit("mcprobe: rtlsim did not reach the prompt")

	def cmd(self, *lines):
		out = b""
		for ln in lines:
			n = len(self.c.log)
			for ch in ln + "\r":
				self.c.p.stdin.write(ch.encode())
				self.c.p.stdin.flush()
				time.sleep(0.004)
			self.c.expect(self.PROMPT, 10)
			out += self.c.log[n:]
		return out.decode(errors="replace")

	def close(self):
		self.c.close()


def run(sw):
	"""The probe sequence; returns {step: {found, [b, c]}}."""
	sw.cmd("igmp off")
	res = {}
	f, idx, a, b, c = sw.l3mc_write(G, mask=0x010)		# (*,G), member port 4
	res["write(*,G)"] = {"found": f, "b": b, "c": c}
	res["lookup(*,G)"] = {"found": sw.l3mc_lookup(G)[0]}
	res["lookup(S,G) exact"] = {"found": sw.l3mc_lookup(G, "1.2.3.4")[0]}
	sw.l3mc_delete(0)					# hash-clear at index 0: should NOT delete
	res["hash-clear idx0"] = {"found": sw.l3mc_lookup(G)[0]}
	f, idx, a, b, c = sw.l3mc_write(G, mask=0x010)		# re-add, then delete by its own index
	sw.l3mc_delete(idx)
	res["index-clear"] = {"found": sw.l3mc_lookup(G)[0]}
	return res


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument("image", nargs="?")
	ap.add_argument("--rtlsim", default=os.path.join(HERE, "../build/rtlsim"))
	ap.add_argument("--switch", help="serial:/dev/ttyUSB1 or http:IP (re-measure on silicon)")
	a = ap.parse_args()

	if a.switch:
		kind, _, loc = a.switch.partition(":")
		sw = SerialSwitch(loc) if kind == "serial" else hwprobe.Switch(loc)
		res, label, sim = run(sw), a.switch, None
	else:
		if not a.image:
			sys.exit("mcprobe: give an IMAGE (sim) or --switch (silicon)")
		sim = SimSwitch(a.image, a.rtlsim)
		res, label = run(sim), "rtlsim"

	fails = 0
	for step, want in EXPECTED.items():
		got = res.get(step, {})
		bad = [k for k, v in want.items() if got.get(k) != v]
		ok = not bad
		fails += not ok
		extra = "" if ok else "  <- " + ", ".join("%s=%s want %s" % (k, got.get(k), want[k]) for k in bad)
		print(("PASS  " if ok else "FAIL  ") + "%-18s %s%s" % (step, {k: (hex(v) if isinstance(v, int) else v) for k, v in got.items()}, extra))
	if sim:
		sim.close()
	print("mcprobe (%s): %s" % (label, "ALL PASS" if not fails else "%d FAILED" % fails))
	return 1 if fails else 0


if __name__ == "__main__":
	sys.exit(main())
