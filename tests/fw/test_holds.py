#!/usr/bin/env python3
"""test_holds.py - control mode's knobs that force a poll's timeout
(src/ctl/control.h): reg hold, phy hold, flash wp.

    tests/fw/test_holds.py BOOT_IMAGE [--board FILE] [--rtlsim build/rtlsim]

On a boot image of fork main (or any with regget/phyget and the web config
save), through the firmware's own reads:
- reg hold 5cac 1: `regget 5cac` reads the bit set, with what the firmware
  wrote still stored (reg dump); reg release: it reads clear again;
- phy hold 0 30 3f8 8000 (the RTL8224's SDS_INDACS_CMD busy bit): `phyget 0
  30 03f8` reads the bit set over what it read before (0 on the SWTG024AS);
  released, what it read before;
- holds lists them; they are kept over a power cut;
- reg busy 5cac 1 2: a table command (regset 5cac, a VLAN read) reads busy
  twice, its output (DATA_OUT_A) stale until then, done at the third read;
  one written while busy is dropped, in checks (table-busy); a boot with it
  gives the same VLAN table as one without (fork main waits);
- flash wp on: a config save from the web (POST /config) programs nothing,
  the config sector stays as it was; flash wp off (and a power cut: fork
  main waits for WEL without end): the save goes through. On an
  object-model image (it answers get port.1.pvid) the save is set
  sys.hostname and save by POST /cli (oldnew's post_config).
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oldnew"))
from oldnew import Run, CONFIG_START, CONFIG_LEN

fails = 0


def check(ok, what):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {what}")


def val(reply):
    return reply.rsplit("VAL:", 1)[-1].split()[0] if reply and "VAL:" in reply else None


def last(r):
    """The reply of the last console command."""
    return r.results[max(k for k in r.results if " cli " in k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    r = Run(a.image, a.board, a.rtlsim, None)
    sim = r.b.sim
    try:
        r.boot()
        r.cli("get port.1.pvid")
        if "port.1.pvid " in (last(r) or ""):
            r.om = {"cfg", "sys"}		# post_config: set lines and save by /cli
        r.cli("regget 5cac")
        before = val(last(r))
        check(sim.cmd("reg hold 5cac 1") == "", "reg hold 5cac 1")
        r.cli("regget 5cac")
        held = val(last(r))
        check(held is not None and int(held, 16) == int(before, 16) | 1,
              f"the firmware reads 0x5cac with bit 0 held: {before} -> {held}")
        stored = [l for l in sim.block("reg dump") if l.startswith("r 5cac ")]
        check(stored == [] or int(stored[0].split()[2], 16) & 1 == 0,
              f"what is stored is not changed: {stored or 'nothing stored'}")
        r.cli("phyget 0 30 03f8")
        p0 = int(val(last(r)) or "0", 16)		# the PHY's own value (the 8x's model: not 0)
        check(sim.cmd("phy hold 0 30 3f8 8000") == "", "phy hold 0 30 3f8 8000")
        r.cli("phyget 0 30 03f8")
        pv = val(last(r))
        check(pv is not None and int(pv, 16) == p0 | 0x8000, f"the firmware reads the RTL8224's SDS_INDACS_CMD as {pv} ({p0:#06x} unheld)")
        holds = sim.block("holds")
        check(holds == ["r 5cac 00000001", "p 0.1e.03f8 8000"], f"holds: {holds}")

        # fork main's table polls have no timeout: a held TBL_CTRL bit hangs
        # it (what E4's timeouts are for); release it before the power cut
        check(sim.cmd("reg release 5cac") == "", "reg release 5cac")
        r.cli("regget 5cac")
        rel = val(last(r))
        check(rel is not None and int(rel, 16) & 1 == 0, f"released: 0x5cac reads {rel}")
        sim.powercut()
        r.boot()
        check(sim.block("holds") == ["p 0.1e.03f8 8000"], "the phy hold is kept over a power cut")
        r.cli("phyget 0 30 03f8")
        pv = val(last(r))
        check(pv is not None and int(pv, 16) == p0 | 0x8000, f"and read after it: {pv}")
        check(sim.cmd("phy release all") == "", "phy release all")
        r.cli("phyget 0 30 03f8")
        rv = val(last(r))
        check(rv is not None and int(rv, 16) == p0, f"released: SDS_INDACS_CMD reads {rv} again ({p0:#06x})")
        check(sim.block("holds") == [], "holds: none")

        # a table command that takes time
        vlans = sim.block("table dump vlan")
        check(sim.cmd("reg busy 5cac 1 2") == "" and sim.block("holds") == ["reg busy 5cac 1 2"], "reg busy 5cac 1 2, listed")
        r.cli("regset 5ccc 12345678")
        r.cli("regset 5cac 00010301")			# read VID 1 (TBL_VLAN, execute)
        r.cli("regget 5ccc")
        stale = val(last(r))
        reads = []
        for _ in range(3):
            r.cli("regget 5cac")
            reads.append(val(last(r)))
        r.cli("regget 5ccc")
        done = val(last(r))
        vid1 = int([l for l in vlans if l.startswith("001 ")][0].split()[1], 16)
        check(stale == "0x12345678", f"while busy the output is stale: DATA_OUT_A {stale}")
        check(reads == ["0x00010301", "0x00010301", "0x00010300"], f"TBL_CTRL reads busy twice, then done: {reads}")
        check(done is not None and int(done, 16) == vid1, f"done: DATA_OUT_A {done} = VID 1's word {vid1:08x}")
        r.cli("regset 5cac 00010301")
        r.cli("regset 5cac 00020301")
        drops = [x for x in sim.checks() if x[1] == "table-busy"]
        check(len(drops) == 1 and "00020301" in drops[0][2], f"one written while busy is dropped: {drops}")
        r.cli("regget 5cac")
        r.cli("regget 5cac")
        r.cli("regget 5cac")
        sim.powercut()
        r.boot()
        r.wait(3000)
        r._now()
        check(sim.block("table dump vlan") == vlans, "a boot with busy table commands: the same VLAN table")
        check(sim.cmd("reg busy off") == "" and sim.block("holds") == [], "reg busy off")

        cfg0 = r.b.flash(CONFIG_START, CONFIG_LEN)
        check(sim.cmd("flash wp on") == "" and sim.block("holds") == ["flash wp"], "flash wp on, listed")
        r.login()
        r.post_config("hostname wp-test\n", slot=5000)
        r._now()
        check(r.b.flash(CONFIG_START, CONFIG_LEN) == cfg0, "a config save under wp: the config sector is unchanged")
        # fork main sends write enable once and waits for WEL without end, so
        # it is stuck now; write enabled again, a fresh boot saves
        check(sim.cmd("flash wp off") == "", "flash wp off")
        sim.powercut()
        r.boot()
        r.login()
        r.post_config("hostname wp-test\n", slot=5000)
        r._now()
        check(b"wp-test" in r.b.flash(CONFIG_START, CONFIG_LEN), "write enabled, after a power cut: the config is saved")
    finally:
        r.b.close()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
