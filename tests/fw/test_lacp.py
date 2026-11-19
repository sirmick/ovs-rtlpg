#!/usr/bin/env python3
"""test_lacp.py - LACP against a partner, in rtlsim --control.

    tests/fw/test_lacp.py IMAGE [--board FILE] [--rtlsim build/rtlsim]

The partner (tests/oldnew/lacp_partner.py) is on front ports 2 and 3, and
`lag 1 lacp 2 3` puts them under LACP:

- Within 5 s both ports reach collecting/distributing: the switch's actor
  and partner states are 0x3f in `lacp show`, the aggregator is the
  partner's system, both ports are trunk members, and the partner sees the
  switch in sync, collecting and distributing.
- The partner goes silent with the links up: the switch times it out (its
  short timeout, 4 s) and takes both ports out of the trunk.
- The partner speaks again: the trunk comes back.
- One member's link goes down: the trunk keeps the other; up again, both.

The run is twice; both transcripts must be the same.
"""
import argparse, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../oldnew"))
from bench import Bench
from lacp_partner import LacpPartner, SYNC, COLL, DIST

failures = 0
FULL = 0x3f		# ACTIVITY TIMEOUT AGGREGATION SYNC COLLECTING DISTRIBUTING


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def show(b):
    out = b.console("lacp show", b.now + 3_000_000)[1] or ""
    lag = re.search(r"lag 01 ports (\w+) aggregator (\S+) members (\w+)", out)
    ports = {int(m.group(1), 16): (int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16))
             for m in re.finditer(r"port (\w+) actor (\w+) partner (\w+) rxst (\w+)", out)}
    return out, lag, ports


def session(a):
    board = a.board or os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json")	# rtlsim's default
    fronts = [p for p in json.load(open(board))["ports"] if "sfp" not in p]
    (pa, fa), (pb, fb) = [(p["phys"], p["front"]) for p in fronts[1:3]]
    b = Bench(a.image, board=a.board, rtlsim=a.rtlsim)
    log, f = [], {}
    try:
        b.wait_uart(r"\n> ", 30_000_000)
        b.advance(b.now + 10_000)
        for p in (pa, pb):
            b.link(p, True)
        partner = LacpPartner(b, [pa, pb])
        b.console(f"lag 1 lacp {fa} {fb}", b.now + 3_000_000)
        t0 = b.now
        # converged: both ports collecting and distributing on the switch's side
        f["converged_at"] = None
        while b.now < t0 + 10_000_000:
            b.advance(b.now + 250_000)
            out, lag, ports = show(b)
            if lag and int(lag.group(3), 16) and all(ports.get(int(x), (0,))[0] == FULL for x in (fa, fb)):
                f["converged_at"] = b.now - t0
                break
        out, lag, ports = show(b)
        log.append(out)
        f["lag"] = lag.groups() if lag else None
        f["ports"] = {k: v for k, v in ports.items() if k in (int(fa), int(fb))}
        f["partner_sees"] = [partner.st[p]["partner"]["state"] if partner.st[p]["partner"] else None for p in (pa, pb)]
        f["partner_state"] = [partner.state(p) for p in (pa, pb)]
        # the partner dies with its links up
        partner.stop()
        t1 = b.now
        f["out_at"] = None
        while b.now < t1 + 8_000_000:
            b.advance(b.now + 250_000)
            out, lag, ports = show(b)
            if lag and int(lag.group(3), 16) == 0:
                f["out_at"] = b.now - t1
                break
        out, lag, ports = show(b)
        log.append(out)
        f["after_stop"] = (lag.groups() if lag else None, {k: v for k, v in ports.items() if k in (int(fa), int(fb))})
        # and comes back
        partner.start()
        b.advance(b.now + 5_000_000)
        out, lag, ports = show(b)
        log.append(out)
        f["back"] = lag.groups() if lag else None
        # one member's link goes down, and up again
        b.link(pb, False)
        b.advance(b.now + 2_000_000)
        out, lag, ports = show(b)
        log.append(out)
        f["flap_down"] = lag.groups() if lag else None
        b.link(pb, True)
        b.advance(b.now + 3_000_000)
        out, lag, ports = show(b)
        log.append(out)
        f["flap_up"] = lag.groups() if lag else None
        f["member_bits"] = (1 << pa, 1 << pb)
        f["frames"] = sorted((p, k, n) for (p, k), n in b.frames.items() if k == "lacp")
        f["partner_sys"] = partner.sys.hex()
    finally:
        b.close()
    return f, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    f, log = session(a)
    members = int(f["lag"][2], 16) if f["lag"] else 0
    check(f["converged_at"] is not None and f["converged_at"] <= 5_000_000,
          f"both ports collecting/distributing {f['converged_at'] / 1e6 if f['converged_at'] else None} s after lag 1 lacp")
    check(all(v[0] == FULL and v[1] == FULL for v in f["ports"].values()) and len(f["ports"]) == 2,
          f"lacp show: actor and partner 0x3f on both ports ({f['ports']})")
    check(f["lag"] and f["lag"][1] == f["partner_sys"] and bin(members).count("1") == 2,
          f"the aggregator is the partner's system, both ports trunk members ({f['lag']})")
    check(all(s is not None and s & (SYNC | COLL | DIST) == SYNC | COLL | DIST for s in f["partner_sees"])
          and all(s & (SYNC | COLL | DIST) == SYNC | COLL | DIST for s in f["partner_state"]),
          f"the partner sees the switch in sync, collecting and distributing ({f['partner_sees']}), and is itself ({f['partner_state']})")
    check(f["out_at"] is not None and 3_000_000 <= f["out_at"] <= 5_500_000,
          f"partner silent: the switch takes both ports out of the trunk after {f['out_at'] / 1e6 if f['out_at'] else None} s "
          f"(its short timeout, 4 s): {f['after_stop']}")
    check(all(not (v[1] & SYNC) for v in f["after_stop"][1].values()),
          f"and no longer holds the partner in sync ({f['after_stop'][1]})")
    check(f["back"] and bin(int(f["back"][2], 16)).count("1") == 2, f"the partner back: the trunk comes back ({f['back']})")
    down = int(f["flap_down"][2], 16) if f["flap_down"] else -1
    check(down != -1 and bin(down).count("1") == 1, f"a member's link down: the trunk keeps the other port only ({f['flap_down']})")
    check(f["flap_up"] and bin(int(f["flap_up"][2], 16)).count("1") == 2, f"and up again: both members ({f['flap_up']})")
    g, log2 = session(a)
    check(f == g and log == log2, "the same run twice")
    print(f"test_lacp: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
