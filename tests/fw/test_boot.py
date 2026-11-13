#!/usr/bin/env python3
"""test_boot.py - boot a real firmware image on rtlsim and check it against
silicon.

    tests/fw/test_boot.py IMAGE [--rtlsim build/rtlsim]

- Every line of the silicon boot log (golden/swtg024as-boot.log, the bench
  SWTG024AS; version and build date left out) appears, in order, before the
  prompt.
- Booted with the bench's cable (2.5G on physical port 4, front port 1), the
  port table's State and Link columns match silicon's.
- The prompt comes up, and CLI commands work against the modelled chip:
  registers, the VLAN table, the L2 table.
- With a 10G module in SFP slot 1 (tools/mksfp.py), the firmware reads its
  EEPROM over I2C when its SFP poll finds it, and in the sfp command; slot 2
  stays empty.
- A board file (--board) sets the chip's identity (another UUID: another
  default MAC) and the modules in the cages (a path relative to it).
- With --flash, the chip persists: the first boot writes the default config,
  the second finds it.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time
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

    c = Console(a.rtlsim, a.image, ["--link", "4=2g5"])
    t = time.time()
    up = c.expect(PROMPT, 120)
    check(up, f"boots to the prompt ({time.time() - t:.1f} s)")
    if not up:
        c.close()
        return 1
    boot = [l.rstrip() for l in c.log.decode(errors="replace").splitlines()]
    missing = in_order(golden_lines(os.path.join(HERE, "golden/swtg024as-boot.log")), boot)
    check(not missing, "the silicon boot log appears in order" + (f"; missing: {missing[:3]}" if missing else ""))

    def port_rows(lines):
        i = next(k for k, l in enumerate(lines) if l.startswith("Port\tState"))
        return [tuple(l.split("\t")[:3]) for l in lines[i + 1:i + 7]]
    gold = [l.rstrip() for l in open(os.path.join(HERE, "golden/swtg024as-boot.log"), errors="replace")]
    check(port_rows(boot) == port_rows(gold), f"port table State/Link match silicon: {port_rows(boot)}")

    ok, out = run(c, "regget 0004")
    check(ok and "VAL: 0x83720000" in out, "regget reads the chip id (RTL8372)")
    ok, out = run(c, "vlan 10 1t 2")
    check(ok and "members: 0x0230, tagged: 0x0210" in out, "vlan 10 1t 2 goes into the VLAN table")
    ok, out = run(c, "l2")
    check(ok and re.search(r"06:0f:00:c8:54:07\s+0x0001\s+static\s+CPU", out) is not None,
          "the L2 table holds the CPU's MAC (static, VLAN 1)")
    c.close()

    with tempfile.TemporaryDirectory() as d:
        sfp = os.path.join(d, "sfp.bin")
        subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), sfp, "--vendor", "ACME"], check=True)
        c = Console(a.rtlsim, a.image, ["--sfp", f"1={sfp}", "--link", "8=10g"])
        # the main loop's SFP poll finds it after the prompt
        up = c.expect(PROMPT, 120) and c.expect(b"Module: ACME", 30)
        boot = c.log.decode(errors="replace")
        check(up and re.search(r"<MODULE INSERTED>  Slot: 1  Rate: 67  Encoding: 06  Module: ACME ", boot) is not None
              and re.search(r"\tSFP IN\t10G\t", boot) is not None,
              "an SFP module in slot 1: its port is up at 10G, the firmware reads its EEPROM")
        ok, out = run(c, "sfp")
        check(ok and "Slot 1 - Rate: 67" in out and "Options: 68" in out and "Vcc: 80e8" in out
              and "Slot 2 - empty" in out, "the sfp command reads its identity and diagnostics")
        c.close()

    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), os.path.join(d, "m.bin"),
                        "--vendor", "BOARDFILE"], check=True)
        b = json.load(open(os.path.join(HERE, "../../boards/swtg024as.json")))
        b["uuid"] = "0x12345600"
        b["ports"][4]["sfp"]["module"] = "m.bin"
        json.dump(b, open(os.path.join(d, "board.json"), "w"))
        c = Console(a.rtlsim, a.image, ["--board", os.path.join(d, "board.json"), "--link", "8=10g"])
        up = c.expect(PROMPT, 120) and c.expect(b"Module: BOARDFILE", 30)
        mac = re.search(rb"Setting MAC to: ([0-9a-f:]+)", c.log)
        check(up and mac is not None and mac.group(1) != b"06:0f:00:c8:54:07",
              f"a board file: another UUID gives another MAC ({mac.group(1).decode() if mac else None}), "
              "and its module is in slot 1")
        c.close()

    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "flash.bin")
        seen = []
        for _ in range(2):
            c = Console(a.rtlsim, a.image, ["--flash", state])
            c.expect(PROMPT, 120)
            seen.append(b"Written default config to flash" in c.log)
            c.close()
        check(seen == [True, False], f"--flash persists the saved config across boots (default written: {seen})")

    print(f"test_boot: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
