#!/usr/bin/env python3
"""con_ring_paste.py BOOT_IMAGE [BOARD] [--rtlsim build/rtlsim]: the 256 B console RX ring against the longest steps.
A continuous paste at 115200 (one byte every 87 us, no Enter: the editor keeps
127 and drops the rest, so only a ring overrun loses a byte) over a window of
firmware time; then `health tasks`' con rx overruns and the step maxima.
Window A, 4.5 s from the prompt: no flash erase. Window B, 14-20 s: covers
loader_confirm()'s one-time sector erase (FL_ERASE ~40 ms, ~17 s).

A measure, not a pass/fail test: the ring holds 256 B, ~22 ms at 115200, so a
step longer than that under a continuous paste loses what doesn't fit (a
~40 ms one ~200 B). Decision 25 allows steps up to 50 ms."""
import argparse, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "oldnew"))
from oldnew import Run

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("board", nargs="?")
ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
args = ap.parse_args()
img, board = args.image, args.board
CHAR_US = 87		# 10 bits at 115200


def run(t0, t1):
    r = Run(img, board, args.rtlsim, None)
    try:
        r.boot()
        if t0 is None:				# from the prompt on, 4.5 s
            t0 = r.b.now / 1e6 + 0.5
            t1 = t0 + 4.5
        r.b.advance(int(t0 * 1_000_000))
        n = int((t1 - t0) * 1_000_000 / CHAR_US)
        s = "abcdefghijklmnopqrstuvwxyz0123456789"
        line = (s * (n // len(s) + 1))[:n]
        for i in range(0, n, 4000):
            r.b.sim.uart_write(line[i:i + 4000])
        r.b.advance(int(t1 * 1_000_000) + 300_000)
        r.b.pending()
        _, o = r.b.console("", r.b.now + 3_000_000)	# Enter: the 127 kept run (Unknown command)
        _, out = r.b.console("health tasks", r.b.now + 8_000_000)
        ov = re.search(r"con rx overruns (\d+)", out or "")
        mx = {m.group(1): m.group(2) for m in re.finditer(r"^(\w+)\t\d+\t(>?\d+(?:us|ms))$", out or "", re.M)}
        top = sorted(((v, k) for k, v in mx.items()), key=lambda x: -(int(x[0].strip('>usm')) * (1000 if x[0].endswith('ms') else 1)))[:3]
        return (t0, t1), n, ov.group(1) if ov else "n/a", mx.get("FL_ERASE"), top
    finally:
        r.b.close()


for name, (t0, t1) in (("A, from the prompt (no erase)", (None, None)), ("B, 14-20 s (the erase)", (14, 20))):
    (a, b), n, ov, fe, top = run(t0, t1)
    print(f"{name}: {a:.1f}-{b:.1f} s, {n} bytes pasted, con rx overruns {ov}; FL_ERASE {fe}; longest steps {top}")
