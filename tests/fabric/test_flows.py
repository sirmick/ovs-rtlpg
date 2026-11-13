#!/usr/bin/env python3
"""test_flows.py - the flow sets the firmware's configurations compile to.

    tests/fabric/test_flows.py IMAGE [--rtlsim build/rtlsim] [--update]

Each case boots IMAGE, runs CLI commands, ends the session and compares
rtlsim's --print-flows (fabric/compiler.h) with golden/CASE.flows. No OVS.
--update rewrites the golden files from this run; review the diff.
"""
import argparse, difflib, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../fw"))
from console import Console, PROMPT

CASES = {
    "boot": [],
    "vlans": ["vlan 10 2 3", "pvid 2 10", "pvid 3 10", "vlan 20 1t 3t"],
    "isolate": ["isolate 1 3"],
    "ingress": ["ingress 1t 2u"],
}


def flows(rtlsim, image, cmds):
    c = Console(rtlsim, image, ["--print-flows"])
    try:
        if not c.expect(PROMPT, 30):
            return None
        for cmd in cmds:
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
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()
    failures = 0
    for name, cmds in CASES.items():
        got = flows(a.rtlsim, a.image, cmds)
        path = os.path.join(HERE, "golden", name + ".flows")
        if a.update and got is not None:
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
