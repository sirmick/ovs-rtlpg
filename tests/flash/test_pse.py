#!/usr/bin/env python3
"""test_pse.py - the POE-2G040210GSM's firmware loads its PoE controller.

    tests/flash/test_pse.py [IMAGE] [--rtlsim build/rtlsim]

IMAGE is a POE_2G040210GSM compact build (default: the newest in
$RTLSIM_IMAGES, ~/rtlsim/images/*/; skipped without one). The test makes its
own machine data region: two "PSE images" of random bytes (never the vendor's
firmware), in RTLPlayground's format (tools/mdata.py: "MDT1", length, "PSE1",
the two lengths, the images, a CRC-16 to 0xb001). It writes the region at
MDATA_START (0x80000) of a 2 MiB flash and boots. Checks: the firmware streams
image 0 to the RTL8238B stub (sys/rtl8238b.h) byte for byte and says "PSE:
load ok"; with the chip type reading 2 it sends image 1 instead; the alternate
arming works; without machine data it leaves PoE off.
"""
import argparse, glob, os, random, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "../../boards/poe_2g040210gsm.json")
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def crc16(data, c=0):
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


def region(img0, img1):
    body = b"MDT1"
    payload = b"PSE1" + struct.pack("<II", len(img0), len(img1)) + img0 + img1
    body += struct.pack("<I", len(payload)) + payload
    c = crc16(body) ^ 0xFFFF
    return body + bytes((c & 0xFF, c >> 8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    root = os.environ.get("RTLSIM_IMAGES", os.path.expanduser("~/rtlsim/images"))
    found = sorted(glob.glob(os.path.join(root, "*", "POE_2G040210GSM-compact.bin")), key=os.path.getmtime)
    image = a.image or (found[-1] if found else None)
    if not image or not os.path.exists(image):
        print("pse: no POE_2G040210GSM compact image, skipped")
        return 0
    rnd = random.Random(8238)
    img0, img1 = bytes(rnd.randrange(256) for _ in range(9000)), bytes(rnd.randrange(256) for _ in range(7000))
    md = region(img0, img1)
    assert crc16(md) == 0xB001
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base.bin")
        subprocess.run([a.rtlsim, "--board", BOARD, "--image", image, "--flash", base, "--cycles", "400000000",
                        "--quiet"], stdin=subprocess.DEVNULL, capture_output=True)

        def boot(*opts, mdata=True):
            f = os.path.join(d, "f.bin")
            chip = bytearray(open(base, "rb").read())
            if mdata:
                chip[0x80000:0x80000 + len(md)] = md
            open(f, "wb").write(chip)
            dump = os.path.join(d, "pse.bin")
            if os.path.exists(dump):
                os.remove(dump)
            r = subprocess.run([a.rtlsim, "--board", BOARD, "--image", image, "--flash", f, "--cycles", "500000000",
                                "--quiet", "--rtl8238b-dump", dump, *opts], stdin=subprocess.DEVNULL, capture_output=True)
            out = r.stdout.decode(errors="replace")
            return out, open(dump, "rb").read() if os.path.exists(dump) else None

        out, got = boot()
        check("PSE: loading image 0" in out and "PSE: load ok" in out and got == img0,
              f"image 0 ({len(img0)} bytes) goes to the RTL8238B byte for byte, and the firmware says load ok")
        out, got = boot("--rtl8238b-e8", "2")
        check("PSE: loading image 1" in out and "PSE: load ok" in out and got == img1,
              f"with chip type 2 it's image 1 ({len(img1)} bytes)")
        out, got = boot("--rtl8238b-alt-arm")
        check("PSE: load ok" in out and got == img0, "a chip that arms only by the alternate sequence is loaded too")
        out, got = boot(mdata=False)
        check("PSE: no RTL8238B image, PoE stays off" in out and got is None, "without machine data, PoE stays off")
    print(f"test_pse: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
