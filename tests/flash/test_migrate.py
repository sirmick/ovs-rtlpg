#!/usr/bin/env python3
"""test_migrate.py - standard -> compact on 2 and 4 MiB boards: the bridge
install, then the config and machine data moves, once each.

    tests/flash/test_migrate.py [--images DIR] [--rtlsim build/rtlsim]
                                [--sweep] [--jobs N] [--results DIR]

DIR is an image matrix (RTLPlayground's build for rtlsim, manifest.json;
default the newest in $RTLSIM_IMAGES, ~/rtlsim/images). A switch on the old
standard firmware (*-chip-old-standard.bin, the size of the board's flash: its
config at 0x70000, a factory MAC at 0x1FC000 on boards that have one) gets a
bridge image (*-bridge.bin) staged in its upload area (0x80000), as a web
upload leaves it; then it's powered on until it's up:
- AMPCOM_WAM902_SWTG018AS (RTL8373) and KEEPLINK_KP_9000_9XHPML_V1_2 (RTL8373,
  2 MiB): the compact app is in place (the bridge's first 0x70000 bytes), the
  config moved 0x70000 -> 0x7F000 and 0x70000 is erased, the upload area is
  erased, the factory MAC is the one it uses, and another power-on moves
  nothing;
- MOKERLINK_POE_2G080110GS (RTL8373, 4 MiB, no factory MAC): the same, and
  the loader, which no bank window wraps onto on a chip this big, has
  written its copy where bank 0x3f shows it (0x2EF800) and started the app;
- POE_2G040210GSM: the same as the first, and the bridge's machine data moved
  0x60000 -> 0x80000 (its transfer header erased), and the PoE controller
  (the RTL8238B stub) gets image 0 from it;
- a non-config sector at 0x70000 (staging leftovers) isn't taken for a
  config: nothing moves, and the switch comes up on its default config.
--sweep also cuts the power before every erase and every 512-byte block of
both migrations (tests/flash/flashlab.py) and counts up / dead / bad; bad
(up, but not in the state above) fails the test. Per-cut results go to
--results (one file per board).
"""
import argparse, glob, json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flashlab

BOARDS = {"AMPCOM_WAM902_SWTG018AS": "ampcom_wam902_swtg018as.json", "POE_2G040210GSM": "poe_2g040210gsm.json",
          "KEEPLINK_KP_9000_9XHPML_V1_2": "keeplink_kp_9000_9xhpml_v1_2.json",
          "MOKERLINK_POE_2G080110GS": "mokerlink_poe_2g080110gs.json"}
LEGACY, CONFIG, UPLOAD, COPY = 0x70000, 0x7f000, 0x80000, 0x70000
MDATA, MDATA_XFER, MDATA_LEN, MAC = 0x80000, 0x60000, 0xc000, 0x1fc000
# RTLPlayground loader/layout.h: the banks reach no further, and where bank 0x3f shows the loader's copy
ALIAS, ALIAS_LEN, ALIAS_REACH, ALIAS_HIGH = 0x3800, 0x800, 0x2f8000, 0x2ef800
SIZES = {"512K": 0x80000, "1M": 0x100000, "2M": 0x200000, "4M": 0x400000}
failures = 0


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond
    return cond


def erased(d, a, n):
    return all(b == 0xff for b in d[a:a + n])


def mdata_len(d, a):
    if d[a:a + 4] != b"MDT1":
        return 0
    return int.from_bytes(d[a + 4:a + 8], "little") + 10


def scenario(a, d, machine, base, bridge, staged_path):
    chip = bytearray(open(base, "rb").read())
    br = open(bridge, "rb").read()
    chip[UPLOAD:UPLOAD + len(br)] = br
    chip[UPLOAD + len(br)] = 0		# the web upload ends the data with a 0
    open(staged_path, "wb").write(chip)
    s = flashlab.Scenario(a.rtlsim, bridge, staged_path, os.path.join(HERE, "../../boards", BOARDS[machine]))
    old = bytes(chip)
    md = mdata_len(br, MDATA_XFER)
    mac = ":".join(f"{b:02x}" for b in old[MAC:MAC + 6])
    # the image the old installer copied, up to the machine data's transfer copy (which
    # the first compact boot moves out and erases the header of)
    upto = MDATA_XFER if md else COPY
    s.expect("image", lambda f: f[:upto] == br[:upto])
    s.expect("config at 0x7F000", lambda f: f[CONFIG:CONFIG + 0x1000] == old[LEGACY:LEGACY + 0x1000])
    s.expect("0x70000 erased", lambda f: erased(f, LEGACY, 0x1000))
    s.expect("upload area erased", lambda f: erased(f, UPLOAD + (MDATA_LEN if md else 0), 0x80000 - (MDATA_LEN if md else 0)))
    if old[MAC:MAC + 6] != b"\xff" * 6:
        s.expect("factory MAC kept", lambda f: f[MAC:MAC + 6] == old[MAC:MAC + 6])
        s.expect_console("uses the factory MAC", lambda c: c.count(f"Setting MAC to: {mac}") >= 1)
    if len(old) > ALIAS_REACH:
        s.expect("the loader's window copy at 0x2EF800", lambda f: f[ALIAS_HIGH:ALIAS_HIGH + ALIAS_LEN] == f[ALIAS:ALIAS + ALIAS_LEN])
    if md:
        s.expect("mdata at 0x80000", lambda f: f[MDATA:MDATA + md] == br[MDATA_XFER:MDATA_XFER + md])
        s.expect("mdata transfer header erased", lambda f: erased(f, MDATA_XFER, 0x1000))
    return s, old, br, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--results", default=os.path.expanduser("~/rtlsim/results"))
    a = ap.parse_args()
    root = os.environ.get("RTLSIM_IMAGES", os.path.expanduser("~/rtlsim/images"))
    dirs = sorted(glob.glob(os.path.join(root, "*", "manifest.json")), key=os.path.getmtime)
    images = a.images or (os.path.dirname(dirs[-1]) if dirs else None)
    if not images:
        print("migrate: no image matrix, skipped")
        return 0
    print(f"images: {images}")
    d = tempfile.mkdtemp(prefix="rtlsim-migrate-")
    try:
        for machine in BOARDS:
            base = os.path.join(images, f"{machine}-chip-old-standard.bin")
            bridge = os.path.join(images, f"{machine}-bridge.bin")
            if not (os.path.exists(base) and os.path.exists(bridge)):
                print(f"{machine}: no old chip or bridge, skipped")
                continue
            fl = json.load(open(os.path.join(HERE, "../../boards", BOARDS[machine])))["flash"]["size"]
            if not check(SIZES.get(fl) == os.path.getsize(base),
                         f"{machine}: the board's flash ({fl}) is the size of the old chip ({os.path.getsize(base):#x})"):
                continue
            staged = os.path.join(d, f"{machine}-staged.bin")
            s, old, br, md = scenario(a, d, machine, base, bridge, staged)
            f = os.path.join(d, "run.bin")
            shutil.copy(staged, f)
            state, n, console = s.settle(f)
            verdict = s.verdict(f, console) if state == "up" else "dead"
            check(verdict == "up", f"{machine}: the bridge installs and the switch comes up migrated "
                  f"({n} power-ons){'' if verdict == 'up' else '; ' + verdict}")
            check(console.count("Moving the saved config to its new place... done") == 1, f"{machine}: the config moved once")
            if md:
                check(console.count("Moving the machine data to its own region... done") == 1,
                      f"{machine}: the machine data moved once ({md} bytes)")
                check("PSE: load ok" in console, f"{machine}: and PoE loads from it")
            rc, again = s.run(f)
            check("Moving" not in again and s.prompted(again), f"{machine}: another power-on moves nothing")

            if machine == "AMPCOM_WAM902_SWTG018AS":
                junk = bytearray(open(staged, "rb").read())
                junk[LEGACY:LEGACY + 0x1000] = br[0x10000:0x11000]	# code, not config text
                jf = os.path.join(d, "junk.bin")
                open(jf, "wb").write(junk)
                state, n, console = s.settle(jf)
                j = open(jf, "rb").read()
                check(state == "up" and "Moving the saved config" not in console and j[CONFIG] != old[LEGACY],
                      f"{machine}: a non-config sector at 0x70000 isn't taken for the config")

            if a.sweep:
                ops, _ = flashlab.ops_of(s, d)
                cuts = flashlab.sampled(ops)
                os.makedirs(a.results, exist_ok=True)
                listing = os.path.join(a.results, f"migrate-{machine}-{os.path.basename(images)}.txt")
                t = time.time()
                res = flashlab.sweep(s, ops, cuts, d, a.jobs, listing)
                up = sum(r == "up" for r, _ in res.values())
                dead = [i for i, (r, _) in res.items() if r == "dead"]
                bad = [i for i, (r, _) in res.items() if r.startswith("bad")]
                print(f"      {machine}: {len(ops)} flash operations, {len(cuts)} cut points in {time.time() - t:.0f} s: "
                      f"{up} up, {len(dead)} dead" + (f" (cuts {flashlab.ranges(dead)})" if dead else "") + f", {len(bad)} bad")
                for sec, c in sorted(flashlab.by_sector(res, ops).items()):
                    if c["dead"] or c["bad"]:
                        print(f"      sector 0x{sec:05x}: {c['up']} up, {c['dead']} dead, {c['bad']} bad")
                for i in bad[:5]:
                    print(f"      cut {i} ({' '.join(ops[i])}): {res[i][0]}")
                check(not bad, f"{machine}: no power cut leaves it up but wrong")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print(f"test_migrate: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
