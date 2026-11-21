#!/usr/bin/env python3
"""test_busy_control.py - the flash busy rule under rtlsim --control.

    tests/flashbusy/test_busy_control.py [--out tests/flashbusy/out] [--rtlsim build/rtlsim]

With the images the Makefile builds (busy_bad.bin, busy_good.bin):

- busy_bad: checks lists the call into BANK1 during the erase as flash-busy,
  stamped with the firmware time it happened at, with PC and PSBANK; rtlsim
  exits 6. The bank window reads 0x00 during the erase (as the 8x's does),
  not the code in the flash.
- busy_good: no finding, exit 0, and the call comes one sector erase (30 ms)
  after the erase started.
- A page program of n bytes keeps the chip busy tBP1 + (n - 1) tBP2, 15 +
  (n - 1) 2.5 µs: rtlsim's default board, the SWGT024, has a W25Q40CL
  (flashctl.cpp parts[], its datasheet's typical). The home bank's status
  polling (a 1 µs command and the loop around it) sees it end 22.5 µs after
  a 4-byte program and 15 µs after a 1-byte one, plus at most a poll; the
  bytes are written.
- A power cut 20 ms into the 30 ms erase of a sector full of a pattern
  leaves it erased up to about two thirds, and the rest as it was.
"""
import argparse, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rtlsim_ctl import Sim, fmt_us

HERE = os.path.dirname(os.path.abspath(__file__))
SECTOR = 0x70000
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    bad, good = (os.path.join(a.out, f"busy_{n}.bin") for n in ("bad", "good"))
    mp = os.path.join(a.out, "busy_good.map")

    sim = Sim(bad, rtlsim=a.rtlsim, args=["--quiet"])
    sim.run(1)
    busy, flash = sim.peek("c:0x4000", 4), sim.peek("b1:0x4000", 4)
    sim.run(29)
    found = [c for c in sim.checks() if c[1] == "flash-busy"]
    sim.close()
    first = found[0] if found else (0, "", "")
    check(found and "instruction fetch of 4000" in first[2] and "psbank=01" in first[2] and first[0] < 1000,
          f"busy_bad: the call into BANK1 during the erase is a flash-busy finding at {fmt_us(first[0])} ms: {first[2]}")
    check(sim.p.returncode == 6, f"busy_bad: rtlsim exits 6 ({sim.p.returncode})")
    check(busy == bytes(4) and flash != bytes(4),
          f"the bank window (PSBANK 1) reads 0x00 during the erase ({busy.hex()}), as the 8x's does, not the flash ({flash.hex()})")

    sim = Sim(good, map=mp, rtlsim=a.rtlsim, args=["--quiet"])
    try:
        sim.run(200)
    except Exception as e:		# it halts (sjmp .) once done
        pass
    lines = sim.uart_read()
    found = [c for c in sim.checks() if c[1] == "flash-busy"]
    sim.close()
    t = {x.split("=")[0]: u for u, x, _ in lines}
    gap = t.get("b1", 0) - t.get("erase", 0)
    check(not found and sim.p.returncode == 0 and [x for _, x, _ in lines][:3] == ["erase", "b1=00000031", "psbank=00000000"],
          f"busy_good: clean, exit {sim.p.returncode}, the same answer")
    check(30000 <= gap < 31000, f"busy_good: BANK1 is called {gap / 1000:.3f} ms after the erase began (a sector erase: 30 ms)")

    with Sim(good, map=mp, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
        sim.run_until_pc("main", max_ms=10)
        sim.poke("x:do_program", b"\x01")	# after the startup code set it
        win = {}
        for n in (4, 1):
            t0 = sim.run_until_pc("mark_programmed", max_ms=100)
            win[n] = sim.run_until_pc("mark_idle", max_ms=10) - t0
        written = sim.peek(f"b{(SECTOR - 0x4000) // 0xc000 + 1}:0x4000", 6)
        found = [c for c in sim.checks() if c[1] == "flash-busy"]
    check(22 <= win[4] < 28 and 15 <= win[1] < 21 and win[4] > win[1] and written == bytes.fromhex("5aa53cc35aff") and not found,
          f"a page program is timed by its bytes: busy seen {win[4]} µs after 4 bytes (22.5), {win[1]} µs after 1 (15); "
          f"written {written.hex()}")

    # a flash with the image and a pattern in the sector the program erases
    flash = os.path.join(a.out, "busy_cut.flash")
    img = open(good, "rb").read()
    data = bytearray(b"\xff" * 0x80000)
    data[:len(img)] = img
    pattern = bytes((i * 7 + 3) & 0xff or 1 for i in range(0x1000))
    data[SECTOR:SECTOR + 0x1000] = pattern
    open(flash, "wb").write(data)
    with Sim(flash=flash, map=mp, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
        t0 = sim.run_until_pc("wait_idle", max_ms=100)
        sim.run(20)
        sim.powercut()
        after = sim.peek(f"b{(SECTOR - 0x4000) // 0xc000 + 1}:0x4000", 0x1000)
    os.unlink(flash)
    done = next((i for i, b in enumerate(after) if b != 0xff), len(after))
    check(2600 < done < 2900 and after[done:] == pattern[done:],
          f"a power cut 20 ms into the 30 ms erase leaves it done in part: {done} of 4096 bytes erased (2/3), the rest as it was")
    print("test_busy_control: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
