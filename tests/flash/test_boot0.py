#!/usr/bin/env python3
"""test_boot0.py - a boot0 firmware install boots through, not hangs.

    tests/flash/test_boot0.py IMAGE [--rtlsim build/rtlsim]

boot0 installs an update by copying the staged image into place and doing a
chip reset. That reset must leave the SPI flash controller in a read setup,
or the boot ROM's refetch of sector 0 reads garbage and the switch hangs
until a power cycle (modelled in sys/flashctl.cpp; the firmware fix is
RTLPlayground 1fda21e, "restore the MMIO read setup before CHIP_RESET_NOW").

The test stages IMAGE as an update over a switch already running it (a boot0
self-install: the same boot0 copy + reset path a real update takes), powers
the switch on once, and requires it to reach the prompt on IMAGE's version
in that one power-on - no hang, no power cycle needed. If the fix regresses,
boot0's reset hangs and this fails. Needs an image that installs via boot0
(dev e3347e6 and later).

A PASS is NOT proof that boot0's reset works on silicon. The flash-controller
model is a hypothesis that silicon has falsified as the whole cause: on
2026-11-14 an install through the fixed boot0 (1fda21e -> 1fda21e-next) hung
switch-denlounge too. What fits so far is that resets before setup_clock()
hang, and rtlsim doesn't model the reset clock. This test only guards the
CMD_R part; RTLPlayground is retiring boot0 for a late main()-path install.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOAD = 0x40000
CYCLES = 3_000_000_000  # OLD boot + arm + app reset + boot0 copy + NEW boot, one power-on


def version(out):
    m = re.findall(r"Software version: (\S+)", out)
    return m[-1] if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    img = open(a.image, "rb").read()
    if b"found update image" not in img:
        print("boot0: this image doesn't install web updates, skipped")
        return 0
    d = tempfile.mkdtemp(prefix="rtlsim-boot0-")
    try:
        flash = os.path.join(d, "flash.bin")
        # a switch running IMAGE, then IMAGE staged over it as a finished upload
        setup = subprocess.run([a.rtlsim, "--flash", flash, "--image", a.image, "--cycles", "400000000", "--quiet"],
                               stdin=subprocess.DEVNULL, capture_output=True)
        want = version(setup.stdout.decode(errors="replace")) or ""  # the real banner, not the image's format string
        chip = bytearray(open(flash, "rb").read())
        chip[UPLOAD:UPLOAD + len(img)] = img
        chip[UPLOAD + len(img)] = 0
        open(flash, "wb").write(chip)

        # one power-on: OLD boots, arms the record, boot0 copies and resets
        r = subprocess.run([a.rtlsim, "--flash", flash, "--image", a.image, "--cycles", str(CYCLES), "--quiet"],
                           stdin=subprocess.DEVNULL, capture_output=True)
        out = r.stdout.decode(errors="replace").replace("\r", "")
        hung = "hangs until a power cycle" in r.stderr.decode(errors="replace")
        installed = out.count("found update image") >= 1 or out.count("Installing") >= 1
        reached = "\n> " in out[out.rfind("Software version:"):] if "Software version:" in out else False
        ok = installed and reached and not hung and version(out) == want
        print(("PASS  " if ok else "FAIL  ")
              + f"a boot0 install of {os.path.basename(a.image)} ({want}) boots through to the prompt in one power-on"
              + " in rtlsim (CMD_R model only: not proof for silicon)"
              + ("" if ok else f"; hung={hung}, reached={version(out)}, installed={installed}"))
        print(f"test_boot0: {'ALL PASS' if ok else 'FAILED'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
