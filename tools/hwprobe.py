#!/usr/bin/env python3
"""hwprobe.py - read and poke a real RTLPlayground switch over its web CLI.

The firmware's POST /cmd runs CLI commands (newline-separated) and returns
their output, so the ASIC's registers and tables can be read on silicon with
`regget`/`regset` - the oracle the simulator's model is checked against.

    hwprobe.py IP cmd 'igmp show' 'regget 5cb0'
    hwprobe.py IP reg 5cb0 [VALUE]
    hwprobe.py IP l2            # every valid L2-table entry, raw (incl. L3MC)

Password: $RTLPW, default the firmware's "1234".
"""
import http.client
import os
import re
import sys
import urllib.parse

TBL_CTRL, TBL_DATA_0 = 0x5cac, 0x5cb0
DATA_OUT_A, DATA_OUT_B, DATA_OUT_C = 0x5ccc, 0x5cd0, 0x5cd4


class Switch:
	def __init__(self, ip, password=None):
		self.ip = ip
		self.password = password or os.environ.get("RTLPW", "1234")
		self.cookie = None

	def _post(self, path, body, ctype):
		c = http.client.HTTPConnection(self.ip, 80, timeout=10)
		hdr = {"Content-Type": ctype}
		if self.cookie:
			hdr["Cookie"] = self.cookie
		c.request("POST", path, body, hdr)
		r = c.getresponse()
		data = r.read().decode(errors="replace")
		c.close()
		return r, data

	def login(self):
		r, _ = self._post("/login", urllib.parse.urlencode({"pwd": self.password}),
				  "application/x-www-form-urlencoded")
		m = re.search(r"session=([^;]+)", r.getheader("Set-Cookie") or "")
		if not m:
			raise RuntimeError(f"{self.ip}: login refused")
		self.cookie = "session=" + m.group(1)

	def cmd(self, *lines):
		if not self.cookie:
			self.login()
		r, data = self._post("/cmd", "\n".join(lines) + "\n", "text/plain")
		if r.status == 401:
			self.login()
			r, data = self._post("/cmd", "\n".join(lines) + "\n", "text/plain")
		return data

	def regget(self, *addrs):
		out = self.cmd(*(f"regget {a:04x}" for a in addrs))
		vals = [int(v, 16) for v in re.findall(r"\b(?:0x)?([0-9a-fA-F]{8})\b", out)]
		if len(vals) < len(addrs):
			raise RuntimeError(f"regget: cannot parse {out!r}")
		return vals[-len(addrs):] if len(addrs) > 1 else vals[-1]

	def regset(self, addr, val):
		self.cmd(f"regset {addr:04x} {val:08x}")

	def l2_walk(self):
		"""Every entry in the L2 table (type 4), by next-valid-entry reads
		(method 3, as the firmware's `l2` does), as (idx, a, b, c). Unlike
		`l2`, entries valid on bit 28 (L3MC) are not hidden."""
		seen, out, idx = set(), [], 0
		while True:
			d0 = self.regget(TBL_DATA_0)
			self.regset(TBL_DATA_0, (d0 & ~0x4c000) | 0xc000)
			self.regset(TBL_CTRL, ((idx & 0xfff) << 16) | 0x0401)
			d0, a, b, c = self.regget(TBL_DATA_0, DATA_OUT_A, DATA_OUT_B, DATA_OUT_C)
			found = d0 & 0xfff
			if found in seen:
				return out
			seen.add(found)
			if (b >> 28) & 3:
				out.append((found, a, b, c))
			idx = found + 1


	def l3mc_lookup(self, dip, sip="0.0.0.0"):
		"""Hash lookup of an L3MC key (method 0, as igmp_packet_handler does):
		(found, idx, a, b, c) from TBL_DATA_0 and DATA_OUT."""
		s = [int(x) for x in sip.split(".")]
		d = [int(x) for x in dip.split(".")]
		self.regset(0x5cb8, (s[0] << 24) | (s[1] << 16) | (s[2] << 8) | s[3])
		self.regset(0x5cbc, ((0x10 | (d[0] & 0xf)) << 24) | (d[1] << 16) | (d[2] << 8) | d[3])
		self.regset(0x5cc0, 0)
		d0 = self.regget(TBL_DATA_0)
		self.regset(TBL_DATA_0, d0 & ~0x7c000)
		self.regset(TBL_CTRL, 0x0401)
		d0, a, b, c = self.regget(TBL_DATA_0, DATA_OUT_A, DATA_OUT_B, DATA_OUT_C)
		return bool(d0 & 0x1000), d0 & 0xfff, a, b, c

	def l3mc_write(self, dip, sip="0.0.0.0", mask=0, index=0, asic=0):
		"""Write an L3MC entry by hash (method 0), as entry_to_l3mc does:
		(found, idx, a, b, c) from the readback lookup."""
		s = [int(x) for x in sip.split(".")]
		d = [int(x) for x in dip.split(".")]
		self.regset(0x5cb8, (s[0] << 24) | (s[1] << 16) | (s[2] << 8) | s[3])
		self.regset(0x5cbc, ((((mask & 3) << 6) | (d[0] & 0xf) | 0x10) << 24) | (d[1] << 16) | (d[2] << 8) | d[3])
		self.regset(0x5cc0, ((asic & 1) << 16) | ((index & 0xff) << 8) | ((mask >> 2) & 0xff))
		self.regset(TBL_DATA_0, self.regget(TBL_DATA_0) & ~0x7c000)	# method 0, clear off
		self.regset(TBL_CTRL, 0x0403)					# type L2, WRITE|EXECUTE, index 0 (hash)
		return self.l3mc_lookup(dip, sip)

	def l3mc_delete(self, idx):
		"""Delete the entry at `idx` (method-0 write with the clear bit and the
		index in TBL_CTRL), as the firmware's IGMP-leave does."""
		self.regset(TBL_DATA_0, (self.regget(TBL_DATA_0) & ~0x7c000) | 0x40000)	# clear bit
		self.regset(TBL_CTRL, ((idx & 0xfff) << 16) | 0x0403)

	def group_members(self, dip):
		"""Ports in the (*,G) L3MC entry for dip, or None without one."""
		found, _, a, b, c = self.l3mc_lookup(dip)
		return bin(((b >> 30) & 3) | ((c & 0xff) << 2)).count("1") if found else None

	def l2_read(self, idx, method):
		"""Read the L2 table at idx with a given read method (TBL_DATA_0
		bits 15:14): (d0, a, b, c)."""
		d0 = self.regget(TBL_DATA_0)
		self.regset(TBL_DATA_0, (d0 & ~0x4c000) | (method << 14))
		self.regset(TBL_CTRL, ((idx & 0xfff) << 16) | 0x0401)
		return self.regget(TBL_DATA_0, DATA_OUT_A, DATA_OUT_B, DATA_OUT_C)


def l3mc_str(a, b, c):
	if (b >> 29) & 1:
		mac = [b >> 8 & 0xff, b & 0xff, a >> 24, a >> 16 & 0xff, a >> 8 & 0xff, a & 0xff]
		port = (b >> 30) | ((c & 3) << 2)
		return "L2   %s vid %d port %d%s" % (":".join("%02x" % x for x in mac), b >> 16 & 0xfff,
						port, " static" if c >> 16 & 1 else "")
	sip = ".".join(str(a >> s & 0xff) for s in (24, 16, 8, 0))
	dip = "%d.%d.%d.%d" % (0xe0 | (b >> 24 & 0xf), b >> 16 & 0xff, b >> 8 & 0xff, b & 0xff)
	mask = (b >> 30) | ((c & 0xff) << 2)
	return "L3MC (%s, %s) members 0x%03x" % (sip, dip, mask)


def main():
	if len(sys.argv) < 3:
		sys.exit(__doc__)
	sw, what, rest = Switch(sys.argv[1]), sys.argv[2], sys.argv[3:]
	if what == "cmd":
		print(sw.cmd(*rest), end="")
	elif what == "reg":
		if len(rest) > 1:
			sw.regset(int(rest[0], 16), int(rest[1], 16))
		print("%08x" % sw.regget(int(rest[0], 16)))
	elif what == "l2":
		for idx, a, b, c in sw.l2_walk():
			print("%03x  %08x %08x %08x  %s" % (idx, a, b, c, l3mc_str(a, b, c)))
	else:
		sys.exit(__doc__)


if __name__ == "__main__":
	main()
