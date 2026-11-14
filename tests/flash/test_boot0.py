#!/usr/bin/env python3
"""test_boot0.py - an install's resets boot through, and a cut mid-copy
resumes: the late install reset and boot0's early resume reset.

    tests/flash/test_boot0.py IMAGE [--new NEW] [--board FILE] [--rtlsim build/rtlsim]

A switch runs IMAGE, with NEW (default IMAGE) staged as a finished web
upload leaves it (at 0x40000, then a 0). Two runs:
- the install: one power-on. Since RTLPlayground 169df92 a loaderless app
  installs late in its boot (after the chip init): it arms the progress
  record (UPD1 at 0x7E000), copies, and resets with reset_chip(). Before,
  the app armed and reset at once and boot0 copied early in the next boot
  and reset with CHIP_RESET_NOW(). Either way the switch must come up on
  NEW in that power-on.
- the resume: the power is cut mid-copy, just after a sector above the home
  bank is erased (so the image area has a hole and the record is armed).
  The next power-on's boot_resume(), early (from __sdcc_external_startup),
  must finish the copy and reset; the switch must come up on NEW, with
  staging and the record clear and the config kept, in at most two
  power-ons.
Each chip reset is reported as early (before the boot's banner) or late
(after it), from rtlsim --trace-reset.

A PASS is NOT proof that the resets work on silicon: rtlsim boots every
reset and doesn't model why some hang a real switch (observed so far: resets
issued before setup_clock()).
Needs an image that installs web updates (dev e3347e6 and later).
"""
import argparse, os, re, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOAD, PROGRESS, CONFIG, SECTOR, HOME_BANK = 0x40000, 0x7e000, 0x7f000, 0x1000, 0x4000
CYCLES = 3_000_000_000  # OLD boot + copy + reset + NEW boot, in one power-on
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond
    return cond


def version(out):
    m = re.findall(r"Software version: (\S+)", out)
    return m[-1] if m else None


def power_on(a, flash, *args, cycles=CYCLES):
    cmd = [a.rtlsim, "--flash", flash, "--image", a.image, "--cycles", str(cycles), "--quiet", "--trace-reset", *args]
    if a.board:
        cmd += ["--board", a.board]
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True)
    out, err = r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
    resets, start, lines = [], 0, out.split("\n")
    for m in re.finditer(r"chip reset \d+ at pc (0x[0-9a-f]+), after (\d+) UART lines", err):
        n = int(m.group(2))	# a banner since the last reset: the boot was under way, a late reset
        kind = "late" if any("Software version:" in l for l in lines[start:n]) else "early"
        resets.append(f"{kind} (pc {m.group(1)})")
        start = n
    text = out.replace("\r", "")
    up = "Software version:" in text and "\n> " in text[text.rfind("Software version:"):]
    return r.returncode, text, resets, up


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--new", help="the image to install (default: IMAGE itself)")
    ap.add_argument("--board")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    img = open(a.image, "rb").read()
    new = open(a.new or a.image, "rb").read()
    if b"found update image" not in img:
        print("boot0: this image doesn't install web updates, skipped")
        return 0
    d = tempfile.mkdtemp(prefix="rtlsim-boot0-")
    try:
        base = os.path.join(d, "base.bin")
        # to the prompt and past the first config save: an RTL8373 board writes its default config after ~400 M
        setup = subprocess.run([a.rtlsim, "--flash", base, "--image", a.image, "--cycles", "1500000000", "--quiet",
                                *(["--board", a.board] if a.board else [])], stdin=subprocess.DEVNULL, capture_output=True)
        old_ver = version(setup.stdout.decode(errors="replace"))
        chip = bytearray(open(base, "rb").read())
        chip[UPLOAD:UPLOAD + len(new)] = new
        chip[UPLOAD + len(new)] = 0
        open(base, "wb").write(chip)
        cfg = bytes(chip[CONFIG:CONFIG + SECTOR])
        name = f"{os.path.basename(a.image)} ({old_ver})" + (f" -> {os.path.basename(a.new)}" if a.new else "")

        def settled(flash):
            d_ = open(flash, "rb").read()
            return [w for w, bad in (("image", d_[:len(new)] != new),
                                     ("staging", any(b != 0xff for b in d_[UPLOAD:UPLOAD + 16])),
                                     ("record", any(b != 0xff for b in d_[PROGRESS:PROGRESS + 16])),
                                     ("config", d_[CONFIG:CONFIG + SECTOR] != cfg)) if bad]

        # the install, with its flash operations for the cut
        flash, ops_log = os.path.join(d, "install.bin"), os.path.join(d, "ops.txt")
        shutil.copy(base, flash)
        rc, out, resets, up = power_on(a, flash, "--flash-ops", ops_log)
        why = settled(flash) if up else []
        check(up and not why, f"the install of {name}: " + (f"up on {version(out)}" if up else "NOT up") + " in one power-on; "
              f"resets: {', '.join(resets) or 'none'}" + (f"; wrong: {why}" if why else ""))
        ops = [l.split() for l in open(ops_log)]

        # a cut right after the first erase of a sector above the home bank, once the record is armed
        armed = next((i for i, o in enumerate(ops) if o[0] == "P" and int(o[1], 16) == PROGRESS), None)
        cut = next((i + 1 for i, o in enumerate(ops) if armed is not None and i > armed and o[0] == "E"
                    and HOME_BANK <= int(o[1], 16) < UPLOAD), None)
        if not check(cut is not None, f"the install arms the record (op {armed}) and then erases the image area "
                     f"(cut before op {cut} of {len(ops)})"):
            return 1
        flash = os.path.join(d, "cut.bin")
        shutil.copy(base, flash)
        rc, _, _, _ = power_on(a, flash, "--powercut-after", str(cut))
        torn = open(flash, "rb").read()
        check(rc == 5 and torn[PROGRESS:PROGRESS + 4] == b"UPD1",
              f"the power cut at op {cut} ({' '.join(ops[cut - 1])}) leaves the record armed")
        story = []
        for n in range(2):
            rc, out, resets, up = power_on(a, flash)
            story.append(f"power-on {n + 1}: {version(out) if up else 'no prompt'}"
                         + (f", resets {', '.join(resets)}" if resets else ""))
            if up:
                break
        why = settled(flash) if up else []
        check(up and not why, "after the cut, boot_resume finishes the copy: "
              + "; ".join(story) + (f"; wrong: {why}" if why else ""))
        print(f"test_boot0: {'ALL PASS' if not failures else f'{failures} FAILED'}")
        return 1 if failures else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
