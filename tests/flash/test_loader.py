#!/usr/bin/env python3
"""test_loader.py - RTLPlayground's loader starts its application.

    tests/flash/test_loader.py [CHIP] [--rtlsim build/rtlsim]

CHIP is a loader + application image for a programmer (*-compact-loader-
boot*.bin; default: the newest in $RTLSIM_IMAGES, ~/rtlsim/images/*/;
skipped without one). The loader (loader/loader_alias.c app_start()) copies
the application's home bank into code RAM through HW_CONF (0x6040) and the
register window at 0x80b0, checks it with movc and jumps to 0: rtlsim must
show the loader's banner, "Starting application", and the application at its
prompt.
"""
import argparse, glob, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chip", nargs="?")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    root = os.environ.get("RTLSIM_IMAGES", os.path.expanduser("~/rtlsim/images"))
    found = sorted(glob.glob(os.path.join(root, "*", "SWGT024_V2_0_UNMANAGED-compact-loader-boot*.bin")),
                   key=os.path.getmtime)
    chip = a.chip or (found[-1] if found else None)
    if not chip:
        print("loader: no loader + application chip image, skipped")
        return 0
    out = subprocess.run([a.rtlsim, "--image", chip, "--cycles", "700000000", "--quiet"], stdin=subprocess.DEVNULL,
                         capture_output=True).stdout.decode(errors="replace").replace("\r", "")
    i, j = out.find("RTLPlayground loader"), out.find("Starting application")
    app = out.find("Software version:", max(j, 0))
    ok = 0 <= i < j < app and "\n> " in out[app:]
    print(("PASS  " if ok else "FAIL  ") + f"the loader ({os.path.basename(chip)}) loads the application into code RAM "
          "and starts it" + ("" if ok else f": {out[-300:]!r}"))
    print(f"test_loader: {'ALL PASS' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
