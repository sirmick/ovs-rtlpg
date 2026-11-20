#!/usr/bin/env python3
"""test_boot.py - boot a real firmware image on rtlsim and check it against
silicon.

    tests/fw/test_boot.py IMAGE [--board FILE] [--rtlsim build/rtlsim]

- Every line of the silicon boot log (golden/swtg024as-boot.log, the bench
  SWTG024AS; version, build date and toolchain left out: they are the
  build's, not the chip's) appears, in order, before the prompt.
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

An object-model image (it answers get port.1.pvid) is checked the same way
through its CLI: the silicon log less the lines its boot no longer prints
(the old boot's EEE and igmp_setup debug lines: it applies those silently);
the port table as state.port.N.link/speed and port.N.enabled or the cage's
state.sfp.S.present; set/apply vlan.10 read back from the VLAN table
(TBL_CTRL, as regget/regset); state.l2's own entry; the module in
state.sfp.S once it is ready; and --flash keeping a saved config (its boot
writes no default config: a blank one stays blank until a save).

--board (default the SWTG024AS's): the chip, the ports and cages follow it;
the silicon log and port table are the SWTG024AS's, checked on it alone.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import Console, PROMPT

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BOARD = os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json")	# rtlsim's
OM_QUIET = re.compile(r"(EEE (on for|can't be enabled)|igmp_setup called)")	# the old boot's debug lines
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def golden_lines(path):
    lines = [l.rstrip() for l in open(path, errors="replace")]
    start = lines.index("Initializing Flash controller")
    end = next(i for i, l in enumerate(lines) if l.startswith("A minimal prompt"))
    return [l for l in lines[start:end] if l and not re.match(r"(Software version|Build date|Toolchain)", l)]


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


def values(out):
    return dict(l.split(" ", 1) for l in out.replace("\r", "").split("\n") if l.startswith(("state.", "port.", "vlan.", "sys.")) and " " in l)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    bd = json.load(open(a.board or DEFAULT_BOARD))
    bargs = ["--board", a.board] if a.board else []
    silicon = os.path.basename(a.board or DEFAULT_BOARD) == os.path.basename(DEFAULT_BOARD)	# the log's board
    copper = [p for p in bd["ports"] if "sfp" not in p]
    cages = sorted((p for p in bd["ports"] if "sfp" in p), key=lambda p: p["sfp"]["slot"])
    p1 = copper[0]["phys"]

    c = Console(a.rtlsim, a.image, bargs + ["--link", f"{p1}=2g5"])
    t = time.time()
    up = c.expect(PROMPT, 120)
    check(up, f"boots to the prompt ({time.time() - t:.1f} s)")
    if not up:
        c.close()
        return 1
    boot = [l.rstrip() for l in c.log.decode(errors="replace").splitlines()]
    smac = re.search(r"Setting MAC to: ([0-9a-f:]+)", "\n".join(boot))
    smac = smac.group(1) if smac else "06:0f:00:c8:54:07"
    om = "port.1.pvid " in run(c, "get port.1.pvid")[1]
    gold = [l.rstrip() for l in open(os.path.join(HERE, "golden/swtg024as-boot.log"), errors="replace")]

    def port_rows(lines):
        i = next(k for k, l in enumerate(lines) if l.startswith("Port\tState"))
        return [tuple(l.split("\t")[:3]) for l in lines[i + 1:i + 7]]
    if not silicon:
        print("skipped: the silicon boot log and port table (the SWTG024AS's, not this board's)")
    elif om:
        want = [l for l in golden_lines(os.path.join(HERE, "golden/swtg024as-boot.log")) if not OM_QUIET.match(l)]
        missing = in_order(want, boot)
        check(not missing, "the silicon boot log appears in order, less the old boot's EEE and igmp_setup lines"
              + (f"; missing: {missing[:3]}" if missing else ""))
        cage = {p["front"]: p["sfp"]["slot"] for p in cages}
        v = values(run(c, "get state.port.link state.port.speed port.enabled state.sfp.present")[1])
        rows = []
        for f, st, _ in port_rows(gold):
            on = v.get(f"state.sfp.{cage[f]}.present") == "on" if f in cage else v.get(f"port.{f}.enabled") == "on"
            link = v.get(f"state.port.{f}.speed") if v.get(f"state.port.{f}.link") == "up" else "down"
            rows.append((f, ("On" if on else "NO SFP") if f in cage else ("On" if on else "Off"),
                         {"2g5": "2.5G", "10g": "10G", "1g": "1G", "down": "Down"}.get(link, link)))
        check(rows == port_rows(gold), f"state.port and state.sfp match silicon's port table: {rows}")
    else:
        missing = in_order(golden_lines(os.path.join(HERE, "golden/swtg024as-boot.log")), boot)
        check(not missing, "the silicon boot log appears in order" + (f"; missing: {missing[:3]}" if missing else ""))
        check(port_rows(boot) == port_rows(gold), f"port table State/Link match silicon: {port_rows(boot)}")

    chip = int(bd.get("chip", "RTL8372")[3:], 16)
    ok, out = run(c, "regget 0004")
    check(ok and f"VAL: 0x{chip:04x}0000" in out, f"regget reads the chip id (RTL{chip:04x})")
    lm = lambda *fr: sum(1 << next(p["phys"] for p in bd["ports"] if p["front"] == str(f)) for f in fr)
    if om:
        for l in ("set vlan.10.members 1-2", "set vlan.10.tagged 1"):
            run(c, l)
        ok, out = run(c, "apply")
        run(c, "regset 5cac 000a0301")			# read VID 10 (TBL_VLAN, execute)
        w = int(run(c, "regget 5ccc")[1].rsplit("VAL:", 1)[-1].split()[0], 16)
        m, u = (w >> 8 & 3) << 8 | (w & 0xff), (w >> 16 & 0xf) << 6 | (w >> 8 & 0xff) >> 2
        check(ok and "ok" in out and m == lm(1, 2) | 0x200 and m & ~u == lm(1) | 0x200,
              f"vlan.10 members 1-2, 1 tagged: applied, in the VLAN table (members {m:#06x}, tagged {m & ~u:#06x})")
        v = values(run(c, "get state.l2")[1])
        own = [k.split(".")[2] for k, x in v.items() if k.endswith(".mac") and x == smac]
        check(own and v.get(f"state.l2.{own[0]}.vlan") == "1" and v.get(f"state.l2.{own[0]}.static") == "on"
              and v.get(f"state.l2.{own[0]}.port") == "cpu", f"state.l2 holds the CPU's MAC {smac} (static, VLAN 1)")
    else:
        ok, out = run(c, "vlan 10 1t 2")
        check(ok and "members: 0x%04x, tagged: 0x%04x" % (lm(1, 2) | 0x200, lm(1) | 0x200) in out,
              "vlan 10 1t 2 goes into the VLAN table")
        ok, out = run(c, "l2")
        check(ok and re.search(re.escape(smac) + r"\s+0x0001\s+static\s+CPU", out) is not None,
              "the L2 table holds the CPU's MAC (static, VLAN 1)")
    c.close()

    with tempfile.TemporaryDirectory() as d:
        sfp = os.path.join(d, "sfp.bin")
        subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), sfp, "--vendor", "ACME"], check=True)
        c = Console(a.rtlsim, a.image, bargs + ["--sfp", f"1={sfp}", "--link", f"{cages[0]['phys']}=10g"])
        # the main loop's SFP poll finds it after the prompt
        up = c.expect(PROMPT, 120) and c.expect(b"Module: ACME", 30)
        boot = c.log.decode(errors="replace")
        check(up and "<MODULE INSERTED>  Slot: 1" in boot and "<SFP-RX OK>  Slot: 1" in boot
              and re.search(r"Rate: 67  Encoding: 06  Module: ACME ", boot) is not None
              and (om or re.search(r"\tSFP IN\t10G\t", boot) is not None),
              "an SFP module in slot 1: its port is up at 10G, its LOS pin says light, the firmware reads its EEPROM")
        if om:
            f = cages[0]["front"]
            for _ in range(20):		# the diagnostics once the module is ready
                v = values(run(c, f"get state.sfp state.port.{f}.link state.port.{f}.speed")[1])
                if v.get("state.sfp.1.ready") == "on":
                    break
                c.expect(b"\x00never", 0.5)
            check(v.get(f"state.port.{f}.link") == "up" and v.get(f"state.port.{f}.speed") == "10g"
                  and v.get("state.sfp.1.rate_mbps") == "10300" and v.get("state.sfp.1.options") == "104"
                  and v.get("state.sfp.1.ddm_ok") == "on" and v.get("state.sfp.1.ddm.vcc_v") == "3.3000"
                  and v.get("state.sfp.2.present", "off") == "off",
                  "state.sfp has its identity and diagnostics (rate 0x67, options 0x68, Vcc 0x80e8), its port up at 10G; "
                  "slot 2 empty")
        else:
            ok, out = run(c, "sfp")
            check(ok and "Slot 1 - Rate: 67" in out and "Options: 68" in out and "Vcc: 80e8" in out
                  and "Slot 2 - empty" in out, "the sfp command reads its identity and diagnostics")
        c.close()

    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, os.path.join(HERE, "../../tools/mksfp.py"), os.path.join(d, "m.bin"),
                        "--vendor", "BOARDFILE"], check=True)
        b = json.load(open(a.board or DEFAULT_BOARD))
        b["uuid"] = "0x12345600"
        next(p for p in b["ports"] if "sfp" in p and p["sfp"]["slot"] == 1)["sfp"]["module"] = "m.bin"
        json.dump(b, open(os.path.join(d, "board.json"), "w"))
        c = Console(a.rtlsim, a.image, ["--board", os.path.join(d, "board.json"), "--link", f"{cages[0]['phys']}=10g"])
        up = c.expect(PROMPT, 120) and c.expect(b"Module: BOARDFILE", 30)
        mac = re.search(rb"Setting MAC to: ([0-9a-f:]+)", c.log)
        check(up and mac is not None and mac.group(1).decode() != smac,
              f"a board file: another UUID gives another MAC ({mac.group(1).decode() if mac else None}), "
              "and its module is in slot 1")
        c.close()

    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "flash.bin")
        seen = []
        for n in range(2):
            c = Console(a.rtlsim, a.image, bargs + ["--flash", state])
            c.expect(PROMPT, 120)
            if om:		# a save on the first boot, its hostname on the second
                if n == 0:
                    for l in ("set sys.hostname persist-test", "apply", "save"):
                        run(c, l)
                    c.expect(b"\x00never", 1)	# the flash job
                v = values(run(c, "get state.cfg.format sys.hostname")[1])
                seen.append((v.get("state.cfg.format"), v.get("sys.hostname")))
            else:
                seen.append(b"Written default config to flash" in c.log)
            c.close()
        want = [("ok", "persist-test")] * 2 if om else [True, False]
        check(seen == want, f"--flash persists the saved config across boots ({'format, hostname' if om else 'default written'}: {seen})")

    print(f"test_boot: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
