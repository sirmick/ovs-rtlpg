#!/usr/bin/env python3
"""console.py - drive the firmware's serial console on rtlsim.

    tests/fw/console.py IMAGE [--rtlsim build/rtlsim] [--cmd "help"]... [--timeout S]

Boots IMAGE, waits for the prompt, sends each --cmd and waits for the prompt
again. Prints the whole console log and the time each step took.
"""
import argparse, os, re, select, subprocess, sys, time

PROMPT = b"\n> "

# The management IP to assume when the firmware's `ip` command can't be read.
# It is the IP that upstream RTLPlayground bakes into the image (config.txt),
# so an upstream-format image is reachable even if the CLI probe below is
# defeated; our compact build keeps config outside the image and the probe
# finds its address (192.168.2.2) instead.
DEFAULT_IP = "192.168.10.247"


def switch_ip(console, timeout=10):
    """The firmware's management IP, read from its `ip` command, so a test
    works on any image whatever address it carries. Falls back to DEFAULT_IP
    when the CLI can't be parsed."""
    start = len(console.log)
    console.send("ip")
    console.expect(PROMPT, timeout)
    m = re.search(rb"Current IP: ([\d.]+)", console.log[start:])
    return m.group(1).decode() if m else DEFAULT_IP


def peer_ip(switch_ip):
    """An address on the switch's /24 for the test peer to use."""
    return re.sub(r"\.\d+$", ".2" if switch_ip.endswith(".1") else ".1", switch_ip)


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
                if pat in self.log[start:]:
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
    ap.add_argument("--rtlsim", default=os.path.join(os.path.dirname(__file__), "../../build/rtlsim"))
    ap.add_argument("--cmd", action="append", default=[])
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    c = Console(a.rtlsim, a.image)
    t = time.time()
    ok = c.expect(PROMPT, a.timeout)
    print(f"[boot to prompt: {time.time() - t:.1f} s{'' if ok else ', NO PROMPT'}]", file=sys.stderr)
    for cmd in a.cmd if ok else []:
        t = time.time()
        c.send(cmd)
        ok = c.expect(PROMPT, a.timeout)
        print(f"[{cmd!r}: {time.time() - t:.1f} s{'' if ok else ', NO PROMPT'}]", file=sys.stderr)
    c.close()
    sys.stdout.write(c.log.decode(errors="replace"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
