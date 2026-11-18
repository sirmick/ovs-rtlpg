#!/usr/bin/env python3
"""test_control.py - rtlsim --control: a test drives the firmware's clock.

    tests/fw/test_control.py IMAGE [--board FILE] [--map FILE] [--rtlsim build/rtlsim]
                             [--transcript OUT]

--map defaults to rtlplayground.map next to IMAGE. One scripted session:

- The firmware boots, paused until told, to its prompt (run until uart), and
  prints what it prints without --control (tests/fw/console.py), line for line.
- A symbol from the map (ticks) reads through peek and the struct decoder,
  and moves on as firmware time does; run until <sym> > value stops there.
- A command typed on the UART reaches cmd_parser (run until pc, a banked
  symbol) and answers.
- A power cut and a chip reset both boot it again, with time running on; a
  line the cut interrupted stays unfinished, and the boot starts afresh.

Then the same session again: both stamped transcripts are identical, byte for
byte. --transcript writes the first.

And inputs go in at fixed firmware times, however the test advances: a
command queued after the boot, then 5 ms run in one go, in uneven pieces,
up to the prompt and on, and up to cmd_parser and on, gives the same stamped
UART output each way, and, run on to the same cycle, the same timers;
Timer 1, which runs free as the baud generator, stays in step with the
cycle count through all the idling.

The prompt comes before cmd_editor_init(), which drops whatever arrived
before it: input waits 10 ms after the boot's prompt.
"""
import argparse, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rtlsim_ctl import Sim, fmt_us
from console import Console, PROMPT

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_RE = r"\n> $"
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def session(a):
    """One scripted session; returns (transcript lines, facts for the checks)."""
    out, facts = [], {}

    def log(s):
        out.append(s)

    def uart(sim):
        for t, text, done in sim.uart_read():
            log(f"uart {fmt_us(t)}{'' if done else '+'} {text}")

    def boot(sim, what):
        t = sim.run_until_uart(PROMPT_RE, max_ms=30000)
        log(f"{what}: prompt at {fmt_us(t) if t is not None else 'timeout'}")
        facts.setdefault(what, t)
        lines = sim.uart_read()
        for u, text, done in lines:
            log(f"uart {fmt_us(u)}{'' if done else '+'} {text}")
        facts.setdefault(what + "_lines", [(text, done) for _, text, done in lines])
        sim.run(10)

    with Sim(a.image, board=a.board, map=a.map, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
        log(f"time {sim.time()}")
        boot(sim, "boot")

        space, addr, size = sim.sym("ticks")
        log(f"sym ticks {space} {addr:#x} {size}")
        t0 = int.from_bytes(sim.peek_sym("ticks"), "little")
        sim.run(100)
        t1 = sim.read_struct("ticks", [("v", "I")])["v"]
        log(f"ticks {t0} -> {t1} over 100 ms")
        facts["ticks"] = (t0, t1)
        hit = sim.run_until("ticks", ">", t1 + 20, max_ms=1000)
        t2 = int.from_bytes(sim.peek_sym("ticks"), "little")
        log(f"until ticks > {t1 + 20}: {fmt_us(hit) if hit is not None else 'timeout'}, ticks {t2}")
        facts["until"] = (hit, t1 + 20, t2)

        sim.uart_write("version\r")
        hit = sim.run_until_pc("cmd_parser", max_ms=1000)
        log(f"pc cmd_parser: {fmt_us(hit) if hit is not None else 'timeout'} {sim.sym('cmd_parser')}")
        facts["pc"] = hit
        t = sim.run_until_uart(PROMPT_RE, max_ms=1000)
        log(f"version answered at {fmt_us(t) if t is not None else 'timeout'}")
        text = sim.uart_text()
        facts["version"] = text
        uart(sim)
        log(f"sp {sim.peek('s:0x81', 1).hex()} psbank {sim.peek('s:0x96', 1).hex()} xdata0 {sim.peek('x:0', 16).hex()}")

        sim.uart_write("ver")		# echoed, no Enter: the cut ends the line
        sim.run(5)
        log(f"powercut {fmt_us(sim.powercut())}")
        boot(sim, "powercut")
        log(f"reset {fmt_us(sim.reset())}")
        boot(sim, "reset")
        log(f"time {sim.time()}")
        for t, kind, text in sim.checks():
            log(f"check {fmt_us(t)} {kind} {text}")
    return out, facts


STYLES = {
    "run 5": [("run", "5")],
    "run 0.517 + 1.111 + 3.372": [("run", "0.517"), ("run", "1.111"), ("run", "3.372")],
    "until uart, run on": [("uart", r"\n> $"), ("to", "5")],
    "until pc=cmd_parser, until uart, run on": [("pc", "cmd_parser"), ("uart", r"\n> $"), ("to", "5")],
}


def styles(a):
    """The same input, advanced in each STYLE: the UART output, stamped."""
    out = {}
    for name, steps in STYLES.items():
        with Sim(a.image, board=a.board, map=a.map, rtlsim=a.rtlsim, args=["--quiet"]) as sim:
            sim.run_until_uart(PROMPT_RE, max_ms=30000)
            sim.run(10)
            sim.uart_read()
            t0 = sim.time()[1]
            c0, tl1_0 = sim.time()[0], sim.peek("s:0x8b", 1)[0]
            sim.uart_write("version\r")
            for kind, arg in steps:
                if kind == "run":
                    sim.run(arg)
                elif kind == "uart":
                    sim.run_until_uart(arg, max_ms=5)
                elif kind == "pc":
                    sim.run_until_pc(arg, max_ms=5)
                else:
                    sim.run(fmt_us(t0 + int(arg) * 1000 - sim.time()[1]))
            # end on the same cycle: a value watch stops at a slice's end, a
            # multiple of 256 cycles (a time in ms.µs doesn't pin a cycle)
            n = int.from_bytes(sim.peek_sym("ticks"), "little") + 2
            sim.run_until("ticks", ">", n, max_ms=100)
            c1, tl1_1 = sim.time()[0], sim.peek("s:0x8b", 1)[0]
            th1 = sim.peek("s:0x8d", 1)[0]
            # Timer 1 (mode 2, a count a cycle: the baud generator) runs free:
            # its phase moves with the cycles, however the idle was skipped
            period = 0x100 - th1
            phase = ((tl1_1 - th1) - (tl1_0 - th1) - (c1 - c0)) % period
            out.setdefault("t1 phase", []).append(phase)
            state = f"cycles {sim.time()[0]} TL0 {sim.peek('s:0x8a', 1).hex()} TL1 {sim.peek('s:0x8b', 1).hex()} timers {sim.peek('s:0x88', 6).hex()} t2 {sim.peek('s:0xcc', 2).hex()} ticks {sim.peek_sym('ticks').hex()}"
            out[name] = (sim.time()[1] - t0, [f"{fmt_us(t)}{'' if d else '+'} {x}" for t, x, d in sim.uart_read()] + [state])
    return out


def plain_boot(a):
    """The boot without --control, to the prompt: its lines."""
    extra = ["--board", a.board] if a.board else []
    c = Console(a.rtlsim, a.image, extra)
    ok = c.expect(PROMPT, 120)
    c.close()
    return ok, c.log.decode(errors="replace").split("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--map")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--transcript")
    a = ap.parse_args()
    a.map = a.map or os.path.join(os.path.dirname(os.path.abspath(a.image)), "rtlplayground.map")

    first, f = session(a)
    if a.transcript:
        with open(a.transcript, "w") as o:
            o.write("\n".join(first) + "\n")
    check(f["boot"] is not None, f"boots to the prompt under --control, paused until told (at {fmt_us(f['boot'] or 0)} ms)")
    ok, plain = plain_boot(a)
    check(ok and [t for t, _ in f["boot_lines"]] == plain, f"the boot prints what it prints without --control ({len(plain)} lines)")
    t0, t1 = f["ticks"]
    check(t1 > t0, f"peek of a map symbol: ticks {t0} -> {t1} over 100 ms of firmware time")
    hit, want, t2 = f["until"]
    check(hit is not None and t2 == want + 1, f"run until ticks > {want} stops at ticks {t2}")
    check(f["pc"] is not None and "Software version" in f["version"],
          "a typed command reaches cmd_parser (run until pc, banked) and answers")
    check(f["powercut"] is not None and f["reset"] is not None and f["powercut"] > f["boot"],
          "a power cut and a chip reset boot it again, firmware time running on")
    check(f["powercut_lines"][0] == ("ver", False) and f["powercut_lines"][1:3] == f["boot_lines"][:2],
          "a line the power cut interrupted stays unfinished; the boot after it starts a line of its own")
    second, _ = session(a)
    diff = next((i for i, (x, y) in enumerate(zip(first, second)) if x != y), None)
    check(first == second, f"two runs give byte-identical stamped transcripts ({len(first)} lines)"
          + ("" if diff is None else f": line {diff}: {first[diff]!r} vs {second[diff]!r}"))
    st = styles(a)
    phases = st.pop("t1 phase")
    check(set(phases) == {0}, f"Timer 1 (baud generator, mode 2) keeps time with the cycles through idle: phase errors {phases}")
    ref = next(iter(st.values()))
    bad = [n for n, v in st.items() if v != ref]
    check(not bad and any("Software version" in l for l in ref[1]),
          f"a queued command, advanced {len(st)} ways, gives the same stamped output and state ({len(ref[1]) - 1} lines over "
          f"{fmt_us(ref[0])} ms)" + (f": differs: {bad}" if bad else ""))
    if bad:
        for n, v in st.items():
            print(f"      {n}: {v[0]} {v[1][:3]}")
    print("test_control: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
