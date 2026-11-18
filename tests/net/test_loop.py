#!/usr/bin/env python3
"""test_loop.py - loop detection (RLDP): a cable from a switch to itself.

    tests/net/test_loop.py IMAGE [--rtlsim build/rtlsim]

IMAGE must have the "loop" command (RTLPlayground feat/loop-detect). One
switch, host h1 on front 1, a cable between fronts 3 and 4 that starts
unplugged. With "loop on" and the cable plugged in, the RLDP engine
(fabric.cpp) sees its own probe come back, and the firmware blocks the
higher port. Checks:
- the firmware reports the loop and blocks front 4 (MSTP_STATES);
- one broadcast from h1 doesn't come back: the blocked port stops the loop;
- h1 still reaches the switch;
- unplugging: the firmware reports the loop gone and unblocks;
- with STP on, a loop is reported but nothing blocked.
Needs sudo; skipped without it.
"""
import argparse, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../../tools"))
from rtlswitch import Net

failures = 0
SW_IP, H1_IP = "192.168.2.2", "192.168.2.11"
PHYS = {"3": 6, "4": 7}		# fronts 3 and 4 on the SWGT024 board

# One broadcast from h1, then count the copies h1 hears within 2 s.
BCAST = r"""
import socket, time
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x88b5))
s.bind(("eth0", 0)); s.settimeout(0.2)
mark = b"rtlsim-loop-%d" % int(time.time() * 1000)
s.send(b"\xff" * 6 + bytes.fromhex("020000000b01") + b"\x88\xb5" + mark + b"\0" * 30)
n, end = 0, time.time() + 2
while time.time() < end:
    try:
        if mark in s.recv(2048): n += 1
    except socket.timeout:
        pass
print(n)
"""


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def state(net, phys):
    out = net.cli("a", "regget 5310")
    m = re.search(r"VAL: 0x([0-9a-f]+)", out)
    return (int(m.group(1), 16) >> (2 * phys)) & 3 if m else None


def wait_for(net, pattern, timeout):
    c = net.labs["a"].console
    start, end = len(c.log), time.time() + timeout
    while time.time() < end:
        if re.search(pattern.encode(), c.log[start:]):
            return True
        c.expect(b"\n", 0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        print("loop: no passwordless sudo, skipped")
        return 0
    topo = {"switches": {"a": {"ip": SW_IP}},
            "links": [["a.3", "a.4"]],
            "hosts": {"h1": {"port": "a.1", "ip": H1_IP + "/24"}}}
    net = Net(a.image, topo, a.rtlsim, prefix=f"l{os.getpid() % 10000}", stderr=open(os.devnull, "w"))
    try:
        net.start()
        net.link("a.3", False)
        time.sleep(1)
        net.cli("a", "loop on")
        net.link("a.3", True)
        check(wait_for(net, r"Loop: port 3 to port 4, blocking port 4", 10),
              "plugged in: the firmware reports the loop and blocks port 4")
        check(state(net, PHYS["4"]) == 1 and state(net, PHYS["3"]) == 3,
              "MSTP_STATES: port 4 blocking, port 3 forwarding")
        r = net.sh("h1", "python3", "-c", BCAST, timeout=20)
        copies = r.stdout.strip()
        check(copies == "0", f"one broadcast from h1 doesn't come back through the loop ({copies or r.stderr[-80:]} copies)")
        r = net.sh("h1", "ping", "-c", "3", "-i", "0.3", "-W", "2", SW_IP, timeout=20)
        check(r.returncode == 0, "h1 still reaches the switch")

        net.link("a.3", False)
        check(wait_for(net, r"Loop on port 4 gone", 15), "unplugged: the firmware reports the loop gone")
        check(state(net, PHYS["4"]) == 3, "and unblocks port 4")

        net.cli("a", "stp on")
        net.link("a.3", True)
        check(wait_for(net, r"\(STP is on: not blocked\)", 10), "with STP on, a loop is reported, not blocked by RLDP")
    finally:
        net.stop()
    print("test_loop: " + ("ALL PASS" if not failures else f"{failures} FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
