#!/usr/bin/env python3
"""test_flows.py - the flow sets the firmware's configurations compile to.

    tests/fabric/test_flows.py IMAGE [--board BOARD] [--rtlsim build/rtlsim] [--update]

Each case boots IMAGE, runs CLI commands, ends the session and compares
rtlsim's --print-flows (fabric/compiler.h) with golden/CASE.flows (with
--board, golden/<board>/CASE.flows). No OVS. An object-model image (it
answers get port.1.pvid) takes each case's set/apply lines, an older one the
old commands; the two configure the same and compare with the same golden.
--update rewrites the golden files from this run; review the diff.
"""
import argparse, difflib, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT

CASES = {                   # the old commands, the object model's
    "boot": ([], []),
    "vlans": (["vlan 10 2 3", "pvid 2 10", "pvid 3 10", "vlan 20 1t 3t"],
              ["set vlan.10.members 2-3", "set port.2.pvid 10", "set port.3.pvid 10",
               "set vlan.20.members 1,3", "set vlan.20.tagged 1,3", "apply"]),
    "isolate": (["isolate 1 3"], ["set port.1.isolate 3", "apply"]),
    "ingress": (["ingress 1t 2u"], ["set port.1.ingress tagged", "set port.2.ingress untagged", "apply"]),
    "igmp": (["igmp on"], ["set igmp.enabled on", "apply"]),
}


def flows(rtlsim, image, board, case):
    c = Console(rtlsim, image, ["--print-flows"] + (["--board", board] if board else []))
    try:
        if not c.expect(PROMPT, 30):
            return None
        start = len(c.log)
        c.send("get port.1.pvid")
        if not c.expect(PROMPT, 10):
            return None
        for cmd in case[b"port.1.pvid " in c.log[start:]]:
            c.send(cmd)
            if not c.expect(PROMPT, 10):
                return None
        c.p.stdin.close()	# rtlsim finishes, prints the flows, ends
        c.expect(b"\x00", 30)
        c.p.wait(30)
        out = c.log.decode(errors="replace")
        return out[out.index("--- flows\n") + 10:] if "--- flows\n" in out else None
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    failures = 0
    gold = os.path.join(HERE, "golden", *([os.path.splitext(os.path.basename(a.board))[0]] if a.board else []))
    for name, case in CASES.items():
        got = flows(a.rtlsim, a.image, a.board, case)
        cmds = case[0]
        path = os.path.join(gold, name + ".flows")
        if a.update and got is not None:
            os.makedirs(gold, exist_ok=True)
            open(path, "w").write(got)
        want = open(path).read() if os.path.exists(path) else ""
        ok = got == want
        print(("PASS  " if ok else "FAIL  ") + f"{name}: {len(want.splitlines())} flows"
              + (f" ({'; '.join(cmds)})" if cmds else ""))
        if not ok:
            failures += 1
            sys.stdout.writelines(list(difflib.unified_diff(want.splitlines(True), (got or "(no flows)\n").splitlines(True),
                                                            "golden", "now", n=1))[:40])
    print(f"test_flows: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
