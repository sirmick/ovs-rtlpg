#!/usr/bin/env python3
"""test_cov.py - coverage: rtlsim --cov / cov dump, and tools/cov_report.py.

    tests/cov/test_cov.py [--images tests/flashbusy/out] [--rtlsim build/rtlsim]

On tests/flashbusy's busy_good (t_busy.c, BANK1's b1_value; built by its
Makefile), which erases a sector and calls into BANK1, and programs only if
a test sets do_program:

- --cov FILE (plain) and cov dump (control) record the same functions (an
  instruction count differs only where the baud-rate UART makes a wait spin);
- the functions that ran did (main, erase_start, wait_idle, banked
  b1_value), program() and the markers didn't, and t_busy.c's lines say the
  same line by line; --require-lines fails on them, --require-functions too;
- two runs diff clean; one with do_program set differs in exactly program(),
  the markers and main (its branch), and program()'s lines all ran;
- cov reset forgets what ran before it.
"""
import argparse, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../tools"))
from rtlsim_ctl import Sim, Halted
from cov_report import Coverage

failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def to_halt(sim):
    try:
        sim.run(100)
    except Halted:		# t_busy.c ends in sjmp .
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=os.path.join(HERE, "../flashbusy/out"))
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    img, mp = os.path.join(a.images, "busy_good.bin"), os.path.join(a.images, "busy_good.map")
    report = [sys.executable, os.path.join(HERE, "../../tools/cov_report.py")]
    d = tempfile.mkdtemp(prefix="rtlsim-cov-")
    plain, c1, c2, c3, c4 = (os.path.join(d, n) for n in ("plain", "c1", "c2", "c3", "c4"))

    subprocess.run([a.rtlsim, "--image", img, "--cycles", "5000000", "--quiet", "--cov", plain],
                   stdout=subprocess.DEVNULL, check=True)
    for path in (c1, c2):
        with Sim(img, map=mp, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
            to_halt(sim)
            sim.cmd(f"cov dump {path}")
    with Sim(img, map=mp, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
        sim.run_until_pc("main", max_ms=10)
        sim.poke("x:do_program", b"\x01")
        to_halt(sim)
        sim.cmd(f"cov dump {c3}")
        sim.cmd("cov reset")
        sim.cmd(f"cov dump {c4}")

    p, cov = Coverage(plain, mp), Coverage(c1, mp)
    same_fns = sorted(n for n, f in p.functions.items() if f[0]) == sorted(n for n, f in cov.functions.items() if f[0])
    spins = sorted(n for n in p.functions if p.functions[n] != cov.functions[n])
    check(same_fns and spins == ["out_c"],
          "--cov (plain) and cov dump (control) record the same functions; only out_c's TI wait differs "
          f"(it spins under control's baud-rate UART): {spins}")
    ran = sorted(n for n, f in cov.functions.items() if f[0])
    want = ["b1_value", "erase_start", "go", "main", "status", "wait_idle"]
    check(all(n in ran for n in want) and not any(n in ran for n in ("program", "mark_programmed", "mark_idle")),
          f"what ran ran, banked b1_value too, and program() didn't: {ran}")
    lines = cov.lines("t_busy.c")
    prog = [n for n, (r, t, src) in lines.items() if src.startswith("FL_DATA") and "= 0x" in src]	# program()'s
    check(prog and all(not lines[n][0] for n in prog) and lines[min(n for n, v in lines.items() if "out_s(\"erase" in v[2])][0],
          f"t_busy.c line by line: {sum(1 for v in lines.values() if v[0])} of {len(lines)} lines with code ran, "
          f"program()'s {len(prog)} data lines didn't")
    r = subprocess.run(report + [c1, "--map", mp, "--require-lines", "t_busy.c"], capture_output=True, text=True)
    r2 = subprocess.run(report + [c1, "--map", mp, "--require-functions", "main", "b1_value", "program"], capture_output=True, text=True)
    r3 = subprocess.run(report + [c3, "--map", mp, "--require-functions", "main", "b1_value", "program"], capture_output=True, text=True)
    check(r.returncode == 1 and "didn't run" in r.stdout and r2.returncode == 1 and "program: didn't run" in r2.stdout
          and r3.returncode == 0, "--require-lines and --require-functions fail on what didn't run, pass on what did")
    same = subprocess.run(report + [c1, "--map", mp, "--diff", c2], capture_output=True, text=True)
    other = subprocess.run(report + [c1, "--map", mp, "--diff", c3], capture_output=True, text=True)
    changed = sorted(l.split()[1] for l in other.stdout.splitlines())
    check(same.returncode == 0 and "no differences" in same.stdout, f"two runs diff clean: {same.stdout.strip()}")
    check(other.returncode == 1 and changed == ["main", "mark_idle", "mark_programmed", "program"]
          and all(Coverage(c3, mp).lines("t_busy.c")[n][0] for n in prog),
          f"with do_program the diff is exactly {changed} (main: its if branch), and program()'s lines ran")
    empty = Coverage(c4, mp)
    check(not any(f[0] for f in empty.functions.values()), "cov reset forgets what ran before it")
    for f in os.listdir(d):
        os.unlink(os.path.join(d, f))
    os.rmdir(d)
    print("test_cov: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
