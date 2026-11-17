#!/usr/bin/env python3
"""test_ldr852.py - a power cut at every step of an install by the loader of
RTLPlayground PR #852 (DrDoof's feat/bootloader).

    tests/flash/test_ldr852.py BOOT_A APP_B --board FILE [--rtlsim build/rtlsim]
                               [--every-op] [--range A-B] [--jobs N] [--list FILE]

BOOT_A is a `make LOADER=1` boot image (loader + application A), APP_B the
plain image of another build. The switch's 2 MiB flash starts with BOOT_A at
0, a saved configuration at 0x70000 and APP_B in the upload area at 0x80000,
as a finished web upload leaves it. The next power-on has the loader install
B (doc/loader.md "Boot", loader/layout.h). A dry run logs its flash
operations; each cut point is then a copy of that flash with the power cut
there, followed by up to 4 power-ons. A cut ends:
- up: at the prompt, running B: B's banks in place, B's home bank in the slot
  at 0x3c000, metadata "RTLB" with B's CRC, the upload header at 0x80000
  gone, the loader and the saved configuration untouched, and the saved
  configuration applied;
- dead: no prompt in 4 power-ons (or one that leaves the flash unchanged);
- bad: anything else.
The loader never rewrites itself here, so no cut should be dead.
"""
import argparse, concurrent.futures, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flashlab import Scenario, ops_of, sweep, sampled, by_sector, ranges, SECTOR

CHIP = 0x200000
LOADER_LEN, APP_A, APP_A_END, META, SLOT, HOME_LEN = 0x4000, 0x4000, 0x3a000, 0x3b000, 0x3c000, 0x3ffe
APP_B, APP_B_END, CONFIG, UPLOAD = 0x40000, 0x70000, 0x70000, 0x80000
SAVED = b"ip 192.168.77.7\ngw 192.168.77.1\nnetmask 255.255.255.0\nhostname saved-cfg\n\0"


def crc16(data, crc=0):
    # the loader's CRC (loader.c crc_table): reflected 0xa001, init 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xa001 if crc & 1 else crc >> 1
    return crc


def upload_cuts(a, s, installed, boot, b, work):
    """B is installed (the dry run). The web upload of A writes 0x80000 up,
    erasing each sector as it reaches it, 256 bytes a program
    (httpd.c stream_upload); above the cut lies what the last install left:
    B's image from 0x84000 on (drop_upload erases only 0x80000-0x83fff).
    The loader must drop the torn upload and start B as it was."""
    a_img = open(a.boot_a.replace("-boot", ""), "rb").read() if "-boot" in a.boot_a else None
    if a_img is None:
        print("upload-cuts: needs BOOT_A named *-boot*.bin with the plain image beside it")
        return 1
    meta = installed[META:META + 7]
    t = Scenario(a.rtlsim, a.boot_a, None, a.board)
    t.expect("still-B", lambda f: f[APP_A:APP_A_END] == b[APP_A:APP_A_END] and f[APP_B:APP_B_END] == b[APP_B:APP_B_END]
             and f[SLOT:SLOT + HOME_LEN] == b[2:2 + HOME_LEN] and f[META:META + 7] == meta)
    t.expect("upload-dropped", lambda f: f[UPLOAD:UPLOAD + 2] != b"\x00\x40")
    t.expect("config", lambda f: f[CONFIG:CONFIG + len(SAVED)] == SAVED)
    t.expect_console("dropped", lambda c: "Bad checksum, image dropped" in c)
    points = sorted({0x100, 0x200} | {k + d for k in range(0, len(a_img), 0x1000) for d in (0x100, 0x800)} |
                    {len(a_img) - 0x100})
    def one(k):
        d = bytearray(installed)
        sec = k & ~(SECTOR - 1)
        d[UPLOAD + sec:UPLOAD + sec + SECTOR] = b"\xff" * SECTOR	# erased when the upload reached it
        d[UPLOAD:UPLOAD + k] = a_img[:k]
        f = os.path.join(work, f"up{k:x}.bin")
        open(f, "wb").write(d)
        r = t.outcome(f)
        os.remove(f)
        return k, r

    bad = 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        for k, (r, n) in sorted(pool.map(one, points)):
            if r != "up":
                bad += 1
                print(f"  upload cut at +0x{k:05x}: {r} ({n} power-ons)")
    print(f"upload-cuts: {len(points)} cut points, {bad} not up")
    print("test_ldr852 upload-cuts: " + ("ALL UP" if not bad else f"{bad} FAILED"))
    return 1 if bad else 0


def recovery(a, s, installed, work):
    """Three unconfirmed starts ("BC" and a zero byte each) on installed B:
    the next power-on offers the recovery window and runs the default
    configuration; it confirms after 15 s, so the one after it runs the
    saved configuration again. The saved configuration is never touched."""
    d = bytearray(installed)
    d[0x3a000:0x3a000 + SECTOR] = b"\xff" * SECTOR
    d[0x3a000:0x3a005] = b"BC\0\0\0"
    f = os.path.join(work, "rec.bin")
    open(f, "wb").write(d)
    fails = 0
    rc, out = s.run(f)
    last = out[out.rfind("RTLPlayground loader"):]
    for ok, msg in [("Recovery boot" in last, "the loader offers the recovery window"),
                    ("Recovery boot: using the default configuration" in last, "the application runs the default configuration"),
                    ("Setting ip: 192.168.77.7" not in last, "and not the saved one"),
                    (open(f, "rb").read()[CONFIG:CONFIG + len(SAVED)] == SAVED, "the saved configuration is untouched")]:
        print(("PASS  " if ok else "FAIL  ") + msg)
        fails += not ok
    rc, out = s.run(f)
    last = out[out.rfind("RTLPlayground loader"):]
    for ok, msg in [("Recovery boot" not in last, "next power-on: no recovery (the start was confirmed)"),
                    ("Setting ip: 192.168.77.7" in last, "and the saved configuration is back")]:
        print(("PASS  " if ok else "FAIL  ") + msg)
        fails += not ok
    print("test_ldr852 recovery: " + ("ALL PASS" if not fails else f"{fails} FAILED"))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boot_a")
    ap.add_argument("app_b")
    ap.add_argument("--board", required=True)
    ap.add_argument("--rtlsim", default=os.path.join(os.path.dirname(__file__), "../../build/rtlsim"))
    ap.add_argument("--every-op", action="store_true")
    ap.add_argument("--range")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--list")
    ap.add_argument("--upload-cuts", action="store_true",
                    help="instead: a power cut during the web upload of A over installed B, one per sector")
    ap.add_argument("--recovery", action="store_true",
                    help="instead: three unconfirmed starts on installed B, then two power-ons")
    a = ap.parse_args()

    boot, b = open(a.boot_a, "rb").read(), open(a.app_b, "rb").read()
    d = bytearray(b"\xff" * CHIP)
    d[:len(boot)] = boot
    d[CONFIG:CONFIG + len(SAVED)] = SAVED
    d[UPLOAD:UPLOAD + len(b)] = b
    work = tempfile.mkdtemp(prefix="ldr852-")
    base = os.path.join(work, "base.bin")
    open(base, "wb").write(d)

    want = crc16(b[2:2 + HOME_LEN] + b[APP_A:APP_A_END] + b[APP_B:APP_B_END])
    s = Scenario(a.rtlsim, a.boot_a, base, a.board)
    s.expect("loader", lambda f: f[:LOADER_LEN] == boot[:LOADER_LEN])
    s.expect("banks", lambda f: f[APP_A:APP_A_END] == b[APP_A:APP_A_END] and f[APP_B:APP_B_END] == b[APP_B:APP_B_END])
    s.expect("slot", lambda f: f[SLOT:SLOT + HOME_LEN] == b[2:2 + HOME_LEN])
    s.expect("meta", lambda f: f[META:META + 4] == b"RTLB" and f[META + 4] | f[META + 5] << 8 == want)
    s.expect("upload", lambda f: f[UPLOAD:UPLOAD + 2] != b"\x00\x40")
    s.expect("config", lambda f: f[CONFIG:CONFIG + len(SAVED)] == SAVED)
    s.expect_console("config-applied", lambda c: "Setting ip: 192.168.77.7" in c[c.rfind("Software version:"):])

    ops, dry = ops_of(s, work)
    # the dry run itself must end up right
    state, n, console = s.settle(dry)
    print(f"dry run: {len(ops)} flash operations; afterwards {s.verdict(dry, console) if state == 'up' else 'dead'}")
    if a.upload_cuts:
        return upload_cuts(a, s, open(dry, "rb").read(), boot, b, work)
    if a.recovery:
        return recovery(a, s, open(dry, "rb").read(), work)
    lo, hi = (int(x) for x in a.range.split("-")) if a.range else (0, len(ops) - 1)
    cuts = sampled(ops, lo, hi, a.every_op)
    print(f"{len(cuts)} cut points, {a.jobs} jobs")
    res = sweep(s, ops, cuts, work, a.jobs, a.list)
    counts = {}
    for i, (r, _) in res.items():
        counts.setdefault(r, []).append(i)
    for r, xs in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(xs):6d}  {r}" + ("" if r == "up" else f"  at {ranges(xs)[:300]}"))
    for sec, c in sorted(by_sector(res, ops).items()):
        if set(c) != {"up"}:
            print(f"  sector 0x{sec:06x}: {dict(c)}")
    bad = sum(len(xs) for r, xs in counts.items() if r != "up")
    print("test_ldr852: " + ("ALL UP" if not bad else f"{bad} of {len(res)} cuts not up"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
