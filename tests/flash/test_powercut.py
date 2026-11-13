#!/usr/bin/env python3
"""test_powercut.py - a power cut at every step of a firmware install.

    tests/flash/test_powercut.py NEW [--old OLD] [--rtlsim build/rtlsim]
                                 [--every-op] [--range A-B] [--jobs N] [--list FILE]

A switch runs OLD (default: NEW) on a 512 KiB flash, with its config
written, and NEW staged in the upload area as a web update leaves it. Its
next power-on installs it: arm the progress record, copy sector by sector,
clear the staging area and the record, reset. A dry run logs every flash
operation of that (rtlsim --flash-ops); then for each cut point the install
runs on a copy of the flash with the power cut there (rtlsim
--powercut-after), followed by up to 4 power-ons. Each cut ends:
- up: at the prompt, the image area is NEW, the staging area and the
  progress record are clear, and the config is the one it had;
- dead: no prompt in 4 power-ons. Allowed only while the home bank (the
  first 16 KiB, copied last) is torn: the boot code itself;
- bad: anything else (up, but on a mixed image, a leftover record, a lost
  config...).
--list writes one line a cut point: "N erase|program ADDR up|dead|bad...".
Cut points: before every erase and before every program that starts a
512-byte block, the host tests' granularity (RTLPlayground programs 4 bytes
a command, about 60,000 of them per install); --every-op takes them all.
Layout: compact (CONFIG_START 0x7f000, UPLOAD_START 0x40000, UPDATE_PROGRESS
0x7e000 in RTLPlayground's layout.h). Needs an image that can resume an
install (dev e3347e6 and later).
"""
import argparse, concurrent.futures, os, re, shutil, subprocess, sys, tempfile, time

CHIP = 0x80000
UPLOAD_START, UPDATE_PROGRESS, CONFIG_START, SECTOR = 0x40000, 0x7e000, 0x7f000, 0x1000
HOME_BANK = 0x4000
BOOT_CYCLES = 600_000_000		# a boot reaches its prompt in ~250 M
POWER_ONS = 4


def rtlsim(a, flash, *args, cycles=BOOT_CYCLES):
    r = subprocess.run([a.rtlsim, "--flash", flash, "--image", a.new, "--cycles", str(cycles), "--quiet", *args],
                       stdin=subprocess.DEVNULL, capture_output=True)
    return r.returncode, r.stdout.decode(errors="replace").replace("\r", "")


def prompt_after_last_boot(out):
    last = out.rfind("Software version:")
    return last >= 0 and "\n> " in out[last:]


def outcome(a, flash, base, new):
    """Power-ons until the prompt; then what state the flash is in."""
    for n in range(POWER_ONS):
        rc, out = rtlsim(a, flash)
        if prompt_after_last_boot(out):
            break
    else:
        return "dead", n + 1
    d = open(flash, "rb").read()
    why = [w for w, bad in (("image", d[:len(new)] != new),
                            ("staging", any(b != 0xff for b in d[UPLOAD_START:UPLOAD_START + 16])),
                            ("record", any(b != 0xff for b in d[UPDATE_PROGRESS:UPDATE_PROGRESS + 16])),
                            ("config", d[CONFIG_START:CONFIG_START + SECTOR] != base[CONFIG_START:CONFIG_START + SECTOR]))
           if bad]
    return ("up" if not why else "bad: " + ",".join(why)), n + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new")
    ap.add_argument("--old")
    ap.add_argument("--rtlsim", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../build/rtlsim"))
    ap.add_argument("--every-op", action="store_true")
    ap.add_argument("--range", help="only cut points A-B (operation numbers)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--list", help="write every cut point's result here")
    a = ap.parse_args()
    new = open(a.new, "rb").read()
    old = open(a.old or a.new, "rb").read()
    if b"Resuming the update" not in new:
        print("powercut: this image can't resume an install, skipped")
        return 0

    d = tempfile.mkdtemp(prefix="rtlsim-powercut-")
    try:
        # the switch on OLD, with its config, then NEW staged
        base_path = os.path.join(d, "base.bin")
        open(os.path.join(d, "old.bin"), "wb").write(old)
        subprocess.run([a.rtlsim, "--image", os.path.join(d, "old.bin"), "--flash", base_path, "--cycles",
                        str(BOOT_CYCLES), "--quiet"], stdin=subprocess.DEVNULL, capture_output=True)
        base = bytearray(open(base_path, "rb").read())
        base[UPLOAD_START:UPLOAD_START + len(new)] = new
        base[UPLOAD_START + len(new)] = 0	# the web upload ends the data with a 0
        open(base_path, "wb").write(base)
        base = bytes(base)

        dry = os.path.join(d, "dry.bin")
        shutil.copy(base_path, dry)
        ops_path = os.path.join(d, "ops.txt")
        rc, out = rtlsim(a, dry, "--flash-ops", ops_path)
        ops = [l.split() for l in open(ops_path)]
        installed = prompt_after_last_boot(out) and outcome(a, dry, base, new)[0] == "up"
        print(("PASS  " if installed else "FAIL  ") + f"without a cut the install runs and the switch comes up on the "
              f"new image ({len(ops)} flash operations: {sum(o[0] == 'E' for o in ops)} erases, "
              f"{sum(o[0] == 'P' for o in ops)} programs)")
        if not installed:
            return 1

        lo, hi = map(int, a.range.split("-")) if a.range else (0, len(ops))
        cuts = [i for i, o in enumerate(ops)
                if lo <= i <= hi and (a.every_op or o[0] == "E" or int(o[1], 16) % 512 == 0)]

        def run(i):
            f = os.path.join(d, f"cut{i}.bin")
            shutil.copy(base_path, f)
            rc, _ = rtlsim(a, f, "--powercut-after", str(i))
            res = outcome(a, f, base, new) if rc == 5 else (f"bad: no cut (rc {rc})", 0)
            os.remove(f)
            return i, res

        t = time.time()
        with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
            results = dict(pool.map(run, cuts))
        took = time.time() - t

        up = [i for i, (r, _) in results.items() if r == "up"]
        dead = [i for i, (r, _) in results.items() if r == "dead"]
        bad = [i for i, (r, _) in results.items() if r.startswith("bad")]
        # dead is allowed only while the home bank is being copied (it goes last)
        home = [i for i, o in enumerate(ops) if int(o[1], 16) < HOME_BANK]
        home_span = (min(home), max(home)) if home else (0, -1)
        dead_outside = [i for i in dead if not home_span[0] <= i <= home_span[1] + 1]
        if a.list:
            with open(a.list, "w") as f:
                for i in sorted(results):
                    r, n = results[i]
                    f.write(f"{i} {'erase' if ops[i][0] == 'E' else 'program'} 0x{ops[i][1]} {r.replace(' ', '')}\n")

        def ranges(xs):
            out, xs = [], sorted(xs)
            for x in xs:
                if out and x == out[-1][1] + 1:
                    out[-1][1] = x
                else:
                    out.append([x, x])
            return ", ".join(f"{s}" if s == e else f"{s}-{e}" for s, e in out)

        print(f"      {len(cuts)} cut points in {took:.0f} s: {len(up)} up on the new image, {len(dead)} dead"
              + (f" (cuts {ranges(dead)}; the home bank is ops {home_span[0]}-{home_span[1]})" if dead else ""))
        for i in bad[:10]:
            print(f"      cut {i} ({' '.join(ops[i])}): {results[i][0]}")
        ok = not bad and not dead_outside
        print(("PASS  " if ok else "FAIL  ") + "a power cut at any point comes back up on the new image, "
              "unless it tears the home bank" + (f"; {len(bad)} bad, {len(dead_outside)} dead outside it" if not ok else ""))
        print(f"test_powercut: {'ALL PASS' if ok else 'FAILED'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
