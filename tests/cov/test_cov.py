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
- cov reset forgets what ran before it;
- lines that can't run by design are left out: t_busy.c's "return 0;" and
  "}" after halt() carry cov:ignore, so with do_program every line ran;
  without the source (--src elsewhere) they are misses, and --except
  FILE:LINE-LAST leaves them out instead; --except on all but one miss fails
  on that one;
- two linked objects of one basename (a/m.rel, b/m.rel) are both read, as
  modules a/m and b/m.
"""
import argparse, os, shutil, subprocess, sys, tempfile
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

    nowhere = os.path.join(d, "nosrc")
    os.mkdir(nowhere)
    req = lambda dump, *extra: subprocess.run(report + [dump, "--map", mp, "--require-lines", "t_busy.c", *extra],
                                              capture_output=True, text=True)
    marked, unmarked = req(c3), req(c3, "--src", nowhere)
    excepted = req(c3, "--src", nowhere, "--except", "t_busy.c:109-110")
    check(marked.returncode == 0 and "2 lines left out" in marked.stdout and unmarked.returncode == 1
          and "t_busy.c:109: didn't run" in unmarked.stdout and "t_busy.c:110: didn't run" in unmarked.stdout
          and excepted.returncode == 0,
          "cov:ignore leaves halt()'s dead return and } out (every other line ran); without the source they are "
          "misses; --except t_busy.c:109-110 leaves them out too")
    misses = [n for n, _ in Coverage(c1, mp, src_dirs=[nowhere]).missed_lines("t_busy.c")]
    all_but_one = req(c1, "--src", nowhere, *[x for n in misses[1:] for x in ("--except", f"t_busy.c:{n}")])
    check(all_but_one.returncode == 1 and f"t_busy.c:{misses[0]}: didn't run" in all_but_one.stdout
          and all_but_one.stdout.count("didn't run") == 1,
          f"--except on {len(misses) - 1} of {len(misses)} misses fails on the one left: line {misses[0]}")

    # two objects named m, in a/ and b/: a link of both, m's functions fa and fb
    syn = os.path.join(d, "syn")
    for sub, fn, addr in (("a", "fa", 0x100), ("b", "fb", 0x200)):
        os.makedirs(os.path.join(syn, sub))
        with open(os.path.join(syn, sub, "m.rst"), "w") as o:
            o.write(f"                                      1 ;\t-----\n"
                    f"                                      2 ;\t function {fn}\n"
                    f"                                      3 ;\tm.c:5: x = 0;\n"
                    f"      {addr:06X} E4               [12]    4 \tclr\ta\n")
    with open(os.path.join(syn, "syn.map"), "w") as o:
        o.write("Files Linked                              [ module(s) ]\n\nsyn/a/m.rel  [  ]\nsyn/b/m.rel  [  ]\n\n\nUser Base Address Definitions\n")
    bits = bytearray(8192)
    bits[0x100 >> 3] |= 1 << (0x100 & 7)
    with open(os.path.join(syn, "syn.cov"), "wb") as o:
        o.write(b"RTLCOV1\n" + bytes([0, 0, 0, 0]) + bits)
    syncov = Coverage(os.path.join(syn, "syn.cov"), os.path.join(syn, "syn.map"))
    check(syncov.functions == {"fa": (1, 1, "syn/a/m"), "fb": (0, 1, "syn/b/m")},
          f"two linked objects named m are both read, as modules syn/a/m and syn/b/m: {syncov.functions}")
    shutil.rmtree(d)
    print("test_cov: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
