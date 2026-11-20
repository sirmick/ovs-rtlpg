#!/usr/bin/env python3
"""cov_diff.py - the functions old and new ran, by name, over oldnew's
scenarios (oldnew.py --cov DIR): which the old build ran that the new one
doesn't, and the other way round.

    tests/oldnew/cov_diff.py DIR --old-map OLD.map --new-map NEW.map [--region loaded]

Each side's dumps (<scenario>.old.cov, <scenario>.new.cov) are mapped
through its own map (tools/cov_report.py's Coverage, region "loaded": the
application the loader started), and a function counts as run if it ran in
any scenario. Two builds' functions are compared by name only: a function
renamed or split is listed on both sides.
"""
import argparse, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from cov_report import Coverage


def ran(dumps, map_path, region):
    out, mods, total = set(), {}, set()
    for d in dumps:
        c = Coverage(d, map_path, region=region)
        for f, (n, _, mod) in c.functions.items():
            total.add(f)
            mods[f] = mod
            if n:
                out.add(f)
    return out, mods, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--old-map", required=True)
    ap.add_argument("--new-map", required=True)
    ap.add_argument("--region", default="loaded")
    a = ap.parse_args()
    o, om, ot = ran(sorted(glob.glob(os.path.join(a.dir, "*.old.cov"))), a.old_map, a.region)
    n, nm, nt = ran(sorted(glob.glob(os.path.join(a.dir, "*.new.cov"))), a.new_map, a.region)
    print(f"old: {len(o)} of {len(ot)} functions ran; new: {len(n)} of {len(nt)}")
    gone = sorted(o - n)
    print(f"ran in old, not in new: {len(gone)}")
    for f in gone:
        print(f"  {f}\t{om.get(f, '')}\t{'(no longer in the new build)' if f not in nt else '(in the new build, not run)'}")
    new = sorted(n - o)
    print(f"ran in new, not in old: {len(new)}")
    for f in new:
        print(f"  {f}\t{nm.get(f, '')}")


if __name__ == "__main__":
    main()
