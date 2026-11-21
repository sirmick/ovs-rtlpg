#!/usr/bin/env python3
"""simconsole.py - rtlsim's serial console, from Python.

    tools/simconsole.py IMAGE [--rtlsim build/rtlsim] [--until TEXT] [--cmd LINE]... [--timeout S]

Console(rtlsim, image, extra) runs rtlsim with the image, its UART on a
pipe: .send(line) types a line (with CR), .expect(pattern, timeout) waits
for bytes in the output since the call, .log is everything so far (CRs
dropped). It knows nothing of the firmware: what its prompt looks like and
what its commands are is the caller's (rtlpg-tests' lib/console.py).
"""
import argparse, os, select, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


class Console:
    def __init__(self, rtlsim, image, extra=(), stderr=subprocess.DEVNULL):
        self.p = subprocess.Popen([rtlsim, "--image", image, "--cycles", "0", "--quiet", *extra],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
        self.log = b""

    def expect(self, pat, timeout):
        end = time.time() + timeout
        start = len(self.log)
        while time.time() < end:
            r, _, _ = select.select([self.p.stdout], [], [], 0.2)
            if r:
                d = os.read(self.p.stdout.fileno(), 4096)
                if not d:
                    break
                self.log += d.replace(b"\r", b"")
                if (pat.search(self.log, start) if hasattr(pat, "search") else pat in self.log[start:]):
                    return True
        return False

    def send(self, line):
        self.p.stdin.write(line.encode() + b"\r")
        self.p.stdin.flush()

    def close(self):
        self.p.kill()
        self.p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../build/rtlsim"))
    ap.add_argument("--until", default="> ", help="what ends the boot and each line's reply (default: '> ')")
    ap.add_argument("--cmd", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    until = a.until.encode()
    c = Console(a.rtlsim, a.image)
    t = time.time()
    ok = c.expect(until, a.timeout)
    print(f"[boot: {time.time() - t:.1f} s{'' if ok else ', NO ' + repr(a.until)}]", file=sys.stderr)
    for cmd in a.cmd if ok else []:
        t = time.time()
        c.send(cmd)
        ok = c.expect(until, a.timeout)
        print(f"[{cmd!r}: {time.time() - t:.1f} s{'' if ok else ', NO ' + repr(a.until)}]", file=sys.stderr)
    c.close()
    sys.stdout.write(c.log.decode(errors="replace"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
