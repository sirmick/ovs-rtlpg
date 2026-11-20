#!/usr/bin/env python3
"""test_nic_ring.py - the NIC RX ring's capacity, and the drops when it fills.

    tests/fw/test_nic_ring.py IMAGE [--board FILE] [--rtlsim build/rtlsim]

rtlsim --control gives the ring the silicon's 10 KiB (--nic-rx-ring,
sys/nic.h): a frame takes its 8-byte descriptor and the frame with the CPU and
802.1Q tags in, padded to 8; one that doesn't fit is dropped and counted
(nic stats: ring_full), and checks lists the first of each run of drops as
nic-rx-full.

- A burst of full frames (1514 bytes) in one instant, before the firmware
  can take any: as many as fit in 10 KiB are taken (6), the rest dropped.
  With --nic-rx-ring 4096, 2.
- A stream of small frames while the firmware idles at its prompt: every
  one taken.
- The same stream while the firmware is busy printing `stat` (its main loop
  doesn't drain the ring meanwhile): the ring fills and frames are dropped,
  and checks says so; once it is done, frames are taken again.
- Each run twice: the same counts.
"""
import argparse, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from rtlsim_ctl import Sim

PROMPT = r"\n(\[confirm [0-9]+\] )?\*?> "	# with P3A's markers; a POSIX ERE too
failures = 0
SLOT = 8


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def frame(n, i):
    """A broadcast of n bytes the firmware takes and ignores (EtherType 0x88b5)."""
    head = b"\xff" * 6 + bytes([2, 0, 0, 0, 0x55, i & 0xff]) + b"\x88\xb5"
    return head + bytes(n - len(head))


def stats(sim):
    r = sim.cmd("nic stats").split()
    d = {r[i]: r[i + 1] for i in range(0, len(r) - 1, 2)}
    return {k: (int(v) if v.isdigit() else v) for k, v in d.items()}


def stream(sim, port, count, gap_ms):
    taken = 0
    for i in range(count):
        taken += sim.nic_inject(port, frame(60, i))
        sim.run(gap_ms)
    return taken


def session(a, ring=None):
    args = ["--quiet"] + (["--nic-rx-ring", str(ring)] if ring else [])
    board = a.board or os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json")	# rtlsim's default
    port = json.load(open(board))["ports"][0]["phys"]	# front port 1
    with Sim(a.image, board=a.board, rtlsim=a.rtlsim, args=args) as sim:
        sim.link(port, True)
        sim.run_until_uart(PROMPT, max_ms=30000)
        sim.run(100)
        sim.uart_read()
        f = {}
        s0 = stats(sim)
        f["burst"] = sum(sim.nic_inject(port, frame(1514, i)) for i in range(20))
        s1 = stats(sim)
        f["burst_drops"] = s1["ring_full"] - s0["ring_full"]
        f["ring_after_burst"] = s1["ring"]
        sim.run(200)
        f["drained"] = stats(sim)["ring"]
        f["idle"] = stream(sim, port, 50, 0.2)
        s2 = stats(sim)
        f["idle_drops"] = s2["ring_full"] - s1["ring_full"]
        sim.uart_write("stat\r")
        f["busy"] = stream(sim, port, 300, 0.1)
        s3 = stats(sim)
        f["busy_drops"] = s3["ring_full"] - s2["ring_full"]
        sim.run_until_uart(PROMPT, max_ms=5000)
        sim.run(50)
        f["after"] = stream(sim, port, 20, 0.2)
        f["after_drops"] = stats(sim)["ring_full"] - s3["ring_full"]
        f["checks"] = [c for c in sim.checks() if c[1] == "nic-rx-full"]
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default="build/rtlsim")
    a = ap.parse_args()

    f = session(a)
    need = 1 + -(-(1514 + 12) // SLOT)		# descriptor + the frame with its tags, in slots
    fit = 10240 // SLOT // need
    check(f["burst"] == fit and f["burst_drops"] == 20 - fit,
          f"a burst of 20 full frames: {f['burst']} taken (10 KiB holds {fit}), {f['burst_drops']} dropped as ring_full; "
          f"ring {f['ring_after_burst']} slots")
    check(f["drained"].startswith("0/"), f"the firmware drains the ring once it runs (ring {f['drained']})")
    check(f["idle"] == 50 and f["idle_drops"] == 0, f"50 small frames 200 µs apart while idle: {f['idle']} taken, {f['idle_drops']} dropped")
    check(f["busy_drops"] > 0, f"300 small frames 100 µs apart while `stat` prints: {f['busy']} taken, {f['busy_drops']} dropped as ring_full")
    check(f["after"] == 20 and f["after_drops"] == 0, f"after `stat`: frames are taken again ({f['after']} of 20)")
    check(len(f["checks"]) >= 2 and all("RX ring full" in c[2] for c in f["checks"]),
          f"checks lists each run of drops once as nic-rx-full ({len(f['checks'])}): "
          + (f["checks"][0][2] if f["checks"] else "none"))
    g = session(a)
    check(f == g, "the same counts, run again")
    h = session(a, ring=4096)
    check(h["burst"] == 4096 // SLOT // need, f"--nic-rx-ring 4096: {h['burst']} full frames fit ({4096 // SLOT // need})")
    print(f"test_nic_ring: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
