#!/usr/bin/env python3
"""test_early_reset.py - a chip reset before the switch init hangs the chip.

    tests/fw/test_early_reset.py IMAGE [--board FILE] [--expect boot|hang]
                                 [--rtlsim build/rtlsim] [--transcript OUT]

What the 8x (RTL8373) was seen to do (rtlpg-sched doc/sched/silicon.md): a
reset made by the application, started by RTLPlayground's loader, before it
has done the switch init resets the chip, then the CPU never runs again - no
loader banner, no UART - until the power is cycled. rtlsim --control models
it, for the application's resets only (sys/system.h has the proxy for "the
switch init"); a reset by the loader itself boots, unconfirmed on silicon.
With --no-early-reset-hang every reset boots, as before. IMAGE is a boot
image (loader and application).

--expect boot (the default): IMAGE's application does the switch init
before its main loop (RTLPlayground's own).
- Booted to the prompt, ^R ^R and the reset request both boot it again, with
  no finding.
- After a power cut, a reset in the loader (just after its banner) boots it
  to the prompt again, with no finding.
- After another, a reset once the application runs ("Initializing Flash
  controller"), before the init: the chip hangs. One early-reset finding
  names the PC, PSBANK and firmware time; no UART for 2 s; a frame in is
  dropped; a power cut boots it to the prompt again; rtlsim exits with 7.
- The same with --no-early-reset-hang boots, no finding, exit 0.

--expect hang: IMAGE's application gets to its prompt without the switch
init (schedtest before its switch-init change).
- ^R ^R at the prompt hangs the chip: the finding, no UART for 2 s (no loader
  banner), a power cut boots it to the prompt again, exit 7.
- The same with --no-early-reset-hang: ^R ^R boots it again, exit 0.

Each session runs twice; the stamped transcripts must be identical.
"""
import argparse, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rtlsim_ctl import Sim, fmt_us

PROMPT_RE = r"\n(\[confirm [0-9]+\] )?\*?> $"	# with P3A's markers; a POSIX ERE too
FINDING_RE = (r"early reset: chip reset by the application at pc=[0-9a-f]{4} psbank=[0-9a-f]{2}, t=\d+\.\d{3} ms, "
              r"before the switch init since power-on \(proxy: .*\): the chip hangs until a power cycle, as the 8x's did")
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def session(a, model, script):
    """One session of `script` (boot or hang); returns (transcript, facts, exit code)."""
    out, facts = [], {}
    log = out.append

    def boot(sim, what):
        t = sim.run_until_uart(PROMPT_RE, max_ms=30000)
        log(f"{what}: prompt at {fmt_us(t) if t is not None else 'timeout'}")
        facts[what] = t
        sim.uart_read()
        sim.run(10)		# cmd_editor_init() drops input before it

    def quiet(sim, what):
        """2 s on: the UART says nothing, a frame in is dropped"""
        sim.uart_read()
        taken = sim.cmd("nic inject 3 ffffffffffff020000000001080600010800060400010200000000010a0000010000000000000a000002")
        sim.run(2000)
        lines = sim.uart_read()
        log(f"{what}: {len(lines)} UART lines in 2 s, frame {taken}")
        facts[what] = (lines, taken)

    def findings(sim):
        found = [c for c in sim.checks() if c[1] == "early-reset"]
        for t, kind, text in found:
            log(f"check {fmt_us(t)} {kind} {text}")
        return found

    args = ["--quiet"] + ([] if model else ["--no-early-reset-hang"])
    sim = Sim(a.image, board=a.board, rtlsim=a.rtlsim, args=args)
    with sim:
        boot(sim, "boot")
        if script == "boot":
            sim.uart_write(b"\x12\x12")
            boot(sim, "ctrl_r")
            log(f"reset {fmt_us(sim.reset())}")
            boot(sim, "reset")
            log(f"powercut {fmt_us(sim.powercut())}")
            sim.run_until_uart(r"RTLPlayground loader\n", max_ms=1000)
            sim.run(10)
            log(f"loader reset {fmt_us(sim.reset())}")
            boot(sim, "loader")
            facts["late"] = findings(sim)
            log(f"powercut {fmt_us(sim.powercut())}")
            t = sim.run_until_uart(r"Initializing Flash controller\n", max_ms=5000)
            log(f"application starts: {fmt_us(t) if t is not None else 'timeout'}")
            log(f"early reset {fmt_us(sim.reset())}")
        else:
            sim.uart_write(b"\x12\x12")
            sim.run(5)
            log("ctrl_r")
        if model:
            quiet(sim, "held")
            facts["early"] = findings(sim)
            log(f"powercut {fmt_us(sim.powercut())}")
            boot(sim, "recovered")
        else:
            boot(sim, "early_boot")
            facts["early"] = findings(sim)
    return out, facts, sim.p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--expect", choices=["boot", "hang"], default="boot")
    ap.add_argument("--rtlsim", default="build/rtlsim")
    ap.add_argument("--transcript")
    a = ap.parse_args()

    out, f, rc = session(a, True, a.expect)
    check(f["boot"] is not None, f"it boots to the prompt ({a.expect} image)")
    if a.expect == "boot":
        check(f["ctrl_r"] is not None and f["reset"] is not None,
              "after the init, ^R ^R and the reset request boot it again")
        check(f["loader"] is not None and not f["late"],
              "a reset by the loader, before any application ran, boots it again; no finding so far")
    early = f["early"]
    check(len(early) == 1 and re.match(FINDING_RE, early[0][2]) is not None,
          "the early reset is one early-reset finding with PC, PSBANK and firmware time: "
          + (early[0][2] if early else "none"))
    lines, taken = f["held"]
    check(lines == [] and taken == "dropped", f"then the chip is held: no UART in 2 s ({lines[:2]}), a frame in {taken}")
    check(f["recovered"] is not None, "a power cut boots it to the prompt again")
    check(rc == 7, f"rtlsim exits with 7 ({rc})")

    out2, _, _ = session(a, True, a.expect)
    check(out == out2, f"the transcript is the same twice ({len(out)} lines)")
    if a.transcript:
        with open(a.transcript, "w") as t:
            t.write("\n".join(out) + "\n")

    _, f, rc = session(a, False, a.expect)
    check(f["early_boot"] is not None and not f["early"] and rc == 0,
          f"--no-early-reset-hang: the same reset boots, no finding, exit {rc}")

    print(f"test_early_reset: {'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
