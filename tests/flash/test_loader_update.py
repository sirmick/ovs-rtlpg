#!/usr/bin/env python3
"""test_loader_update.py - a web update of a compact board that has the loader, on
every board of the image matrix.

    tests/flash/test_loader_update.py [--images DIR] [--rtlsim build/rtlsim]

DIR is an image matrix (RTLPlayground's build for rtlsim, manifest.json;
default the newest in $RTLSIM_IMAGES, ~/rtlsim/images). Per board, a chip the
size of its flash gets the programmer image (*-compact-boot.bin: the loader,
its metadata, the application), a saved config at 0x7F000, the factory MAC
of a board that has one and, on the PoE board, a machine data region at
0x80000; it's powered on until it's up. Then a new application (the build's,
one byte in the unused end of a code bank changed and the CRC fixed, so the install
shows) is staged at 0x40000 as a web upload leaves it:
- "install": the install image, the application then the loader on the
  stage at 0x7A000 (the current firmware's upload);
- "app": the application alone, then the 0 that firmware before
  RTLPlayground baa8102 wrote past an upload (a board such as one running
  05cdb23 takes only this).
At the next power-on the loader installs it: the switch comes up on the new
application, with the loader, the config, the factory MAC and the machine
data as they were (and PoE loaded), the upload's header erased; on a chip that
no bank window wraps onto the loader (4 MiB), with the loader's copy where
bank 0x3f shows it. Another power-on installs nothing.
"""
import argparse, glob, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flashlab

BOARDS = {"SWGT024_V2_0_UNMANAGED": None, "AMPCOM_WAM902_SWTG018AS": "ampcom_wam902_swtg018as.json",
          "POE_2G040210GSM": "poe_2g040210gsm.json", "KEEPLINK_KP_9000_9XHPML_V1_2": "keeplink_kp_9000_9xhpml_v1_2.json",
          "MOKERLINK_POE_2G080110GS": "mokerlink_poe_2g080110gs.json"}
SIZES = {"512K": 0x80000, "1M": 0x100000, "2M": 0x200000, "4M": 0x400000}
# RTLPlayground layout.h (compact) and loader/layout.h
LOADER_LEN, WEB, IMAGE = 0x4000, 0x28000, 0x3a000
UPLOAD, STAGE, CONFIG, MDATA, MAC = 0x40000, 0x7a000, 0x7f000, 0x80000, 0x1fc000
ALIAS, ALIAS_LEN, ALIAS_REACH, ALIAS_HIGH = 0x3800, 0x800, 0x2f8000, 0x2ef800
CFG = b"ip 192.168.2.2\ngw 192.168.2.1\nnetmask 255.255.255.0\nhostname update-test\n"
FACTORY_MAC = bytes.fromhex("00e04c55aa01")
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond
    return cond


def crc16(data, c=0):
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


def erased(d, a, n):
    return d[a:a + n] == b"\xff" * n


def changed(app):
    """The application with one byte changed in the unused end of a code bank,
    its CRC fixed."""
    img = bytearray(app)
    run = img.find(bytes(256), LOADER_LEN, WEB)	# the builder pads with 0
    if run < 0:
        sys.exit("update: no unused bank space to change a byte in")
    at = run + 128
    img[at] = 0x5a
    c = crc16(img[:-2]) ^ 0xffff
    img[-2], img[-1] = c & 0xff, c >> 8
    assert crc16(img) == 0xb001
    return bytes(img), at


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    root = os.environ.get("RTLSIM_IMAGES", os.path.expanduser("~/rtlsim/images"))
    dirs = sorted(glob.glob(os.path.join(root, "*", "manifest.json")), key=os.path.getmtime)
    images = a.images or (os.path.dirname(dirs[-1]) if dirs else None)
    if not images:
        print("update: no image matrix, skipped")
        return 0
    print(f"images: {images}")
    man = {e["name"]: e for e in json.load(open(os.path.join(images, "manifest.json")))}
    d = tempfile.mkdtemp(prefix="rtlsim-ldrupd-")
    try:
        for machine, board in BOARDS.items():
            t = machine + "-compact"
            if t + "-boot" not in man or t + "-install" not in man:
                print(f"{machine}: no compact boot or install image, skipped")
                continue
            size = SIZES[man[t]["flash"]]
            bj = json.load(open(os.path.join(HERE, "../../boards", board))) if board else None
            if bj and not check(SIZES.get(bj["flash"]["size"]) == size,
                                f"{machine}: the board's flash ({bj['flash']['size']}) is the build's ({man[t]['flash']})"):
                continue
            chip = bytearray(b"\xff" * size)
            boot = open(os.path.join(images, t + "-boot.bin"), "rb").read()
            chip[:len(boot)] = boot
            chip[CONFIG:CONFIG + len(CFG)] = CFG
            mac = bool(bj and bj.get("factory_mac_at"))
            if mac:
                chip[MAC:MAC + 6] = FACTORY_MAC
            md = b""
            if machine == "POE_2G040210GSM" and "synth-pse-mdata" in man:
                md = open(os.path.join(images, man["synth-pse-mdata"]["file"]), "rb").read()
                chip[MDATA:MDATA + len(md)] = md
            base = os.path.join(d, f"{machine}-base.bin")
            open(base, "wb").write(chip)
            bp = os.path.join(HERE, "../../boards", board) if board else None
            s = flashlab.Scenario(a.rtlsim, os.path.join(images, t + "-boot.bin"), base, bp)
            state, n, console = s.settle(base)
            if not check(state == "up" and "Starting application" in console,
                         f"{machine} ({man[t]['flash']}): the loader starts the programmed application ({n} power-ons)"):
                continue
            up = open(base, "rb").read()
            app = open(os.path.join(images, t + ".bin"), "rb").read()
            inst = open(os.path.join(images, t + "-install.bin"), "rb").read()
            new, at = changed(app)
            for kind, staged in (("install", new + inst[IMAGE:]), ("app", new + b"\0")):
                f = os.path.join(d, f"{machine}-{kind}.bin")
                st = bytearray(up)
                st[UPLOAD:UPLOAD + len(staged)] = staged
                open(f, "wb").write(st)
                state, n, console = s.settle(f)
                r = open(f, "rb").read()
                what = f"{machine}, {kind} upload"
                check(state == "up" and "Installed" in console, f"{what}: the loader installs it and the switch comes up ({n} power-ons)")
                check(r[LOADER_LEN:IMAGE] == new[LOADER_LEN:IMAGE] and r[at] == 0x5a, f"{what}: the new application is in place")
                check(r[:LOADER_LEN] == up[:LOADER_LEN], f"{what}: the loader is untouched")
                # the loader erases the first 16 KiB (drop_upload()): no header left to take again
                check(erased(r, UPLOAD, 0x4000), f"{what}: the upload's header is erased")
                check(r[CONFIG:CONFIG + 0x1000] == up[CONFIG:CONFIG + 0x1000], f"{what}: the saved config is kept")
                if mac:
                    check(r[MAC:MAC + 6] == FACTORY_MAC and f"Setting MAC to: {FACTORY_MAC.hex(':')}" in console,
                          f"{what}: the factory MAC is kept and used")
                if md:
                    check(r[MDATA:MDATA + len(md)] == md and "PSE: load ok" in console,
                          f"{what}: the machine data is kept and PoE loads from it")
                if size > ALIAS_REACH:
                    check(r[ALIAS_HIGH:ALIAS_HIGH + ALIAS_LEN] == r[ALIAS:ALIAS + ALIAS_LEN],
                          f"{what}: the loader's window copy is at 0x2EF800")
                rc, again = s.run(f)
                check(s.prompted(again) and "Installing application" not in again and "Checking uploaded image" not in again,
                      f"{what}: another power-on installs nothing")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"test_loader_update: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
