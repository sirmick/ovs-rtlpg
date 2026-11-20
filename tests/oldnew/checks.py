#!/usr/bin/env python3
"""checks.py - the new-only acceptance checks (rtlpg-sched doc/sched/testing.md,
"New-only acceptance checks") on one image, without an old one to compare to:

    tests/oldnew/checks.py --image IMAGE [--board FILE] [--only NAME...]
                           [--known FILE] [--payload FILE] [-j N] [--rtlsim build/rtlsim]

It runs the scenarios (scenarios.py; by default those with a check) in
rtlsim --control, as oldnew.py does, and prints each check's verdict:
- lacp gap: the switch's LACPDUs on the LAG < 1.2 s apart (lacp, lacp_l2:
  during the 1000-entry `l2` and /l2.json);
- stp hello: BPDUs 2 s +-50 ms apart on every port sending them (stp;
  stp_upload: through a firmware upload);
- long steps: `health tasks` at the scenario's end, no state's longest step
  over 50 ms (decision 25; FL_ERASE too, its longest reported) (every
  scenario; skipped on a build without it, as
  fork main);
- the upload's flash (upload, stp_upload: the app, its slot, the staged
  image, the config) is a result: oldnew.py compares it, here it is shown.

Times are firmware times, the frames' send times as rtlsim captured them.
PASS, FAIL, KNOWN (a failure --known lists, within its ceiling:
oldnew.load_known()), SKIP. Exit 0 when no check FAILs.
"""
import argparse, os, sys, time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from oldnew import run_one, verdicts, load_known, stale_known, default_payload
from scenarios import SCENARIOS

CHECKED = ["lacp", "stp", "upload", "l2_big", "lacp_l2", "stp_upload"]


def main():
    ap = argparse.ArgumentParser(description="the new-only acceptance checks on one image")
    ap.add_argument("--image", required=True)
    ap.add_argument("--board")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--known")
    ap.add_argument("--payload", help="what the upload scenarios upload (default: the image's own application)")
    ap.add_argument("--om", default="", metavar="OBJS", help="the objects the image has on the object model (oldnew --new-om)")
    ap.add_argument("-j", type=int, default=os.cpu_count())
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    names = a.only or CHECKED
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"checks: no scenario {n} (have: {' '.join(SCENARIOS)})")
    opts = {"board": a.board, "rtlsim": a.rtlsim, "payload": a.payload or default_payload(a.image),
            "om_old": set(), "om_new": {x for x in a.om.split(",") if x}}
    known = load_known(a.known)
    t0 = time.time()
    print(f"image: {a.image}" + (f"\nknown failures: {a.known}" if a.known else ""))
    fails, used = 0, set()
    with ProcessPoolExecutor(max_workers=a.j) as ex:
        for name, _, res, checks, wall in ex.map(run_one, [(n, "new", a.image, opts) for n in names]):
            print(f"{name} ({wall:.1f} s)")
            for v, c, text in verdicts(name, checks, known, used):
                fails += v == "FAIL"
                print(f"  {v} {c}: {text}")
            for k in sorted(k for k in res if " flash " in k):
                print(f"  {k[4:]}: {res[k]}")
    for l in stale_known(known, used, names, a.known):
        print(l)
    print(f"checks: {fails} failed, {time.time() - t0:.0f} s wall")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
