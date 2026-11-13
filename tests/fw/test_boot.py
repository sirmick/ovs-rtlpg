#!/usr/bin/env python3
"""test_boot.py - boot a real firmware image on rtlsim and check it against
silicon.

    tests/fw/test_boot.py IMAGE [--rtlsim build/rtlsim]

- Every line of the silicon boot log (golden/swtg024as-boot.log, the bench
  SWTG024AS; version and build date left out) appears, in order, before the
  prompt.
- The prompt comes up, and CLI commands work against the modelled chip:
  registers, the VLAN table, the L2 table.
"""
import argparse, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import Console, PROMPT

HERE = os.path.dirname(os.path.abspath(__file__))
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def golden_lines(path):
    lines = [l.rstrip() for l in open(path, errors="replace")]
    start = lines.index("Initializing Flash controller")
    end = next(i for i, l in enumerate(lines) if l.startswith("A minimal prompt"))
    return [l for l in lines[start:end] if l and not re.match(r"(Software version|Build date)", l)]


def in_order(want, have):
    i, missing = 0, []
    for w in want:
        j = i
        while j < len(have) and have[j] != w:
            j += 1
        if j == len(have):
            missing.append(w)
        else:
            i = j + 1
    return missing


def run(c, cmd):
    start = len(c.log)
    c.send(cmd)
    ok = c.expect(PROMPT, 60)
    return ok, c.log[start:].decode(errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()

    c = Console(a.rtlsim, a.image)
    t = time.time()
    up = c.expect(PROMPT, 120)
    check(up, f"boots to the prompt ({time.time() - t:.1f} s)")
    if not up:
        c.close()
        return 1
    boot = [l.rstrip() for l in c.log.decode(errors="replace").splitlines()]
    missing = in_order(golden_lines(os.path.join(HERE, "golden/swtg024as-boot.log")), boot)
    check(not missing, "the silicon boot log appears in order" + (f"; missing: {missing[:3]}" if missing else ""))

    ok, out = run(c, "regget 0004")
    check(ok and "VAL: 0x83720000" in out, "regget reads the chip id (RTL8372)")
    ok, out = run(c, "vlan 10 1t 2")
    check(ok and "members: 0x0230, tagged: 0x0210" in out, "vlan 10 1t 2 goes into the VLAN table")
    ok, out = run(c, "l2")
    check(ok and re.search(r"06:0f:00:c8:54:07\s+0x0001\s+static\s+CPU", out) is not None,
          "the L2 table holds the CPU's MAC (static, VLAN 1)")
    c.close()
    print(f"test_boot: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
