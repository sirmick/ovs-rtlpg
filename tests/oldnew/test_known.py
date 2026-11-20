#!/usr/bin/env python3
"""test_known.py - the new-only checks' verdicts against known-failure lines
(oldnew.verdicts(), oldnew.load_known(), oldnew.Run.gap_check()), no rtlsim:

    python3 tests/oldnew/test_known.py

A check is (name, violations, what, worst µs, hard, saturated): saturated is
None, or the scenario's console replies {"cli <line>": µs} when the measure
saturated (`health tasks`' >400ms). Exit 0 when every case gives its verdict.
"""
import inspect, os, subprocess, sys, tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from oldnew import Run, load_known, stale_known, verdicts

KNOWN = """\
stp_upload: stp hello <= 3 s
lacp_l2: long step CLI_RUN <= 3.5 s by cli l2
l2_big: long step CLI_RUN <= 3.5 s by cli *
c: long step CLI_RUN <= 500 ms
x: long step CLI_RUN unbounded
y: lacp gap unbounded
a,b: long step X <= 30 ms
web: long step RX_STEP <= 40 ms
"""


def sat(replies):
    return [("long step CLI_RUN", [">400ms"], "", 400_001, [], replies)]


def known_file(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".known", delete=False)
    f.write(text)
    f.close()
    return f.name


def main():
    path = known_file(KNOWN)
    k = load_known(path)
    cases = [
        # saturated steps: the longest reply of every command typed
        ("A: l2 3.22 s, lacp show 3.0 s: within the ceiling", "lacp_l2",
         sat({"cli l2": 3_220_000, "cli lacp show": 3_000_000}), "KNOWN"),
        ("B: lacp show 4 s saturates, l2 in bounds", "lacp_l2",
         sat({"cli l2": 3_220_000, "cli lacp show": 4_000_000}), "FAIL"),
        ("C: the named command not typed", "lacp_l2", sat({"cli lacp show": 1000}), "FAIL"),
        ("D: by cli *, another command 4 s", "l2_big", sat({"cli l2": 3_220_000, "cli stat": 4_000_000}), "FAIL"),
        ("E: by cli *, nothing typed", "l2_big", sat({}), "FAIL"),
        ("H: a saturated step, an unbounded line", "x", sat({}), "KNOWN"),
        ("I: a saturated step, a ceiling with no `by`", "c", sat({"cli l2": 1000}), "FAIL"),
        ("J: unsaturated, within a ceiling with no `by`", "c",
         [("long step CLI_RUN", ["300ms"], "", 300_000, [], None)], "KNOWN"),
        # unbounded, not saturated
        ("F: unbounded, a 9 s gap", "y", [("lacp gap", ["9 s"], "", 9_000_000, [], None)], "KNOWN"),
        # ceilings and scope
        ("K: over the ceiling", "a", [("long step X", ["40ms"], "", 40_000, [], None)], "FAIL"),
        ("L: another scenario", "z", [("long step X", ["25ms"], "", 25_000, [], None)], "FAIL"),
        # short gaps: hard, whatever the lines say
        ("M: a short gap behind a long one", "stp_upload",
         [("stp hello", ["port 4: 2.8 s", "port 5: 0.5 s, under 1.95 s"], "", 2_800_000,
           ["port 5: 0.5 s, under 1.95 s"], None)], "FAIL"),
        ("N: the long gap alone", "stp_upload", [("stp hello", ["port 4: 2.8 s"], "", 2_800_000, [], None)], "KNOWN"),
        ("O: no violations", "stp_upload", [("stp hello", [], "", None, [], None)], "PASS"),
        ("P: skipped", "boot", [("long steps", None, "no health tasks", None, [], None)], "SKIP"),
    ]
    fails = 0
    for what, scen, checks, want in cases:
        got = verdicts(scen, checks, k)[0]
        ok = got[0] == want
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {what}: {got[0]} (want {want}) {got[2]}")

    # G: a line with no ceiling that doesn't say unbounded is malformed
    bad = known_file("lacp_l2: lacp gap   # no ceiling, no unbounded\n")
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {HERE!r}); "
                        f"from oldnew import load_known; load_known({bad!r})"], capture_output=True, text=True)
    ok = r.returncode == 1 and ":1: not " in r.stderr
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  G: no ceiling, no `unbounded`: malformed (exit {r.returncode}) {r.stderr.strip()}")

    # stale lines: flagged only where a scenario they name ran
    used = set()
    verdicts("a", [("long step X", ["25ms"], "", 25_000, [], None)], k, used)
    st = stale_known(k, used, ["a", "stp_upload"], path)
    ok = len(st) == 1 and "stp_upload: stp hello" in st[0]
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  Q: stale only where its scenario ran ({len(st)}): {st}")

    # gap_check: only gaps over hi make the worst measure; one under lo is hard
    r = SimpleNamespace(b=SimpleNamespace(times={(4, "stp"): [0, 2_000_000, 4_800_000, 6_800_000],
                                                 (5, "stp"): [0, 2_000_000, 2_500_000, 4_500_000, 6_500_000]}),
                        checks=[])
    Run.gap_check(r, "stp hello", "stp", 1_000_000, 7_000_000, lo=1.95, hi=2.05)
    _, bad_, _, worst, hard, _ = r.checks[0]
    ok = worst == 2_800_000 and len(hard) == 1 and "port 5" in hard[0]
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  R: gap_check worst {worst} µs (the long gap), hard {hard}")

    # every line the harness types counts toward `by cli`: Run._type()
    # records its reply, and Run gives the console nothing but through it
    class FakeBench:
        now = 1000
        def console(self, line, until):
            self.now += {"ip": 40_000, "health tasks": 250_000}.get(line, 10_000)
            return "", "reply\n> "
    r = SimpleNamespace(b=FakeBench(), replies={})
    for line in ("ip", "health tasks", "ip"):
        Run._type(r, line, 10_000_000)
    src = inspect.getsource(Run)
    ok = r.replies == {"cli ip": 40_000, "cli health tasks": 250_000} and src.count("self.b.console(") == 1
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  S: the harness's own lines are measured too ({r.replies}); "
          f"b.console() called {src.count('self.b.console(')} time(s) in Run, from _type()")

    # long_steps(): the bound is 50 ms (decision 25), FL_ERASE included; a
    # step at or under it passes, one over fails, FL_ERASE's longest reported
    def steps(table):
        class B:
            now = 0
            def console(self, line, until):
                return "", "health tasks\nslot\ttask\tstate\tmask\twake\nstate\truns\tmax\n" + table + \
                    "passes 00001000 idle 00000800 longest 41ms\n\n> "
        r = SimpleNamespace(b=B(), replies={"cli l2": 3_000_000}, checks=[])
        r._type = lambda line, until: Run._type(r, line, until)
        Run.long_steps(r)
        return r.checks
    c = steps("CLI_RUN\t5\t49ms\nRX_STEP\t9\t51ms\nFL_ERASE\t2\t55ms\nHOUSE_SEC\t3\t40000us\nSFP_READ\t1\t>400ms\n")
    names = sorted(x[0] for x in c)
    sfp = [x for x in c if x[0] == "long step SFP_READ"]
    ok = names == ["long step FL_ERASE", "long step RX_STEP", "long step SFP_READ"] and sfp and sfp[0][5].get("cli l2") == 3_000_000
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  T: over 50 ms fails, FL_ERASE too, 49 ms and 40 ms pass: {names}")
    c = steps("CLI_RUN\t5\t3ms\nFL_ERASE\t2\t45ms\nHOUSE_SEC\t3\t7000us\n")
    ok = len(c) == 1 and c[0][0] == "long steps" and c[0][1] == [] and "FL_ERASE 45ms, 2 runs" in c[0][2] and "over 50 ms" in c[0][2]
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  U: all at or under 50 ms: one PASS, FL_ERASE's longest reported: {c[0][2] if c else c}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
