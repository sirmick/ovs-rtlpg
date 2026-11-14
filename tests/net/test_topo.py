#!/usr/bin/env python3
"""test_topo.py - two switches, cabled, each running the real firmware.

    tests/net/test_topo.py IMAGE [--rtlsim build/rtlsim]

Switches a and b (tools/rtlswitch.py Net), a cable between their front ports
4, hosts h1 on a.1, h2 on b.1 and h3 on b.2, configured only through the
firmwares' CLIs. Checks:
- the two chips have different MACs, and each switch has its own address;
- h1 reaches h2 and h3 across the cable, and both switches' addresses;
- both l2 tables have the far host's MAC (learned on the cable's port);
- a tagged VLAN across the cable as a trunk (vlan 20 1t 4t on both);
- an untagged VLAN carried tagged on the trunk (vlan 30, PVID 30 on a.1
  and b.2): h1 reaches h3, not h2;
- a cable pull: both firmwares see port 4 go down and come back, and the
  traffic across it stops and resumes.
Needs sudo; skipped without it.
"""
import argparse, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../../tools"))
from rtlswitch import Net

failures = 0
IP = {"a": "192.168.2.2", "b": "192.168.2.3", "h1": "192.168.2.11", "h2": "192.168.2.12", "h3": "192.168.2.13"}
CABLE_PHYS = 7		# front port 4 on the SWTG024AS


def check(cond, msg):
    global failures
    print(("PASS  " if cond else "FAIL  ") + msg)
    failures += not cond


def link_seen(c, port, up, start, timeout):
    """The firmware's "<new link: S9..S0, ...>" line (a speed digit per
    physical port, 0 for down) shows `port` up or down, after log offset start."""
    end = time.time() + timeout
    while time.time() < end:
        c.expect(b">", 0.5)
        for m in re.finditer(rb"<new link: ([0-9a-f]{10}),", c.log[start:]):
            if (m.group(1)[9 - port:10 - port] != b"0") == up:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
        print("topo: no passwordless sudo, skipped")
        return 0

    topo = {
        "switches": {"a": {"ip": IP["a"]}, "b": {"ip": IP["b"]}},
        "links": [["a.4", "b.4"]],
        "hosts": {"h1": {"port": "a.1", "ip": IP["h1"] + "/24"}, "h2": {"port": "b.1", "ip": IP["h2"] + "/24"},
                  "h3": {"port": "b.2", "ip": IP["h3"] + "/24"}},
    }
    net = Net(a.image, topo, a.rtlsim, prefix=f"t{os.getpid() % 10000}", stderr=open(os.devnull, "w"))

    def ping(src, dst, n=2):
        return net.sh(src, "ping", "-c", str(n), "-i", "0.2", "-W", "1", IP.get(dst, dst)).returncode == 0

    def mac(host):
        m = re.search(r"link/ether ([0-9a-f:]+)", net.sh(host, "ip", "link", "show", "eth0").stdout)
        return m.group(1) if m else "?"

    try:
        net.start()
        time.sleep(1)
        ma, mb = net.mac("a"), net.mac("b")
        check(ma and mb and ma != mb, f"the two chips have their own MACs: a {ma}, b {mb}")
        check(IP["a"] in net.cli("a", "ip") and IP["b"] in net.cli("b", "ip"),
              f"each switch has its own address: a {IP['a']}, b {IP['b']}")

        check(ping("h1", "h2") and ping("h1", "h3") and ping("h2", "h3"),
              "h1 on a reaches h2 and h3 on b across the cable")
        check(ping("h1", "a") and ping("h1", "b") and ping("h2", "a"),
              "hosts reach both switches' addresses, near and far")
        la, lb = net.cli("a", "l2"), net.cli("b", "l2")
        check(mac("h2") in la and mac("h1") in lb,
              "each l2 table has the far host's MAC: "
              + "; ".join(r.group(0).strip() for r in (re.search(re.escape(mac("h2")) + ".*", la),
                                                       re.search(re.escape(mac("h1")) + ".*", lb)) if r))

        for sw in "ab":
            net.cli(sw, "vlan 20 1t 4t")
        for h, ip in (("h1", "10.0.20.1/24"), ("h2", "10.0.20.2/24")):
            net.sh(h, "ip", "link", "add", "link", "eth0", "name", "eth0.20", "type", "vlan", "id", "20", check=True)
            net.sh(h, "ip", "addr", "add", ip, "dev", "eth0.20", check=True)
            net.sh(h, "ip", "link", "set", "eth0.20", "up", check=True)
        check(ping("h1", "10.0.20.2"), "VLAN 20 tagged on a.1, b.1 and the cable (a trunk): h1 reaches h2 on it")

        net.cli("a", "vlan 30 1 4t")
        net.cli("a", "pvid 1 30")
        net.cli("b", "vlan 30 2 4t")
        net.cli("b", "pvid 2 30")
        check(ping("h1", "h3") and not ping("h1", "h2") and not ping("h2", "h3"),
              "VLAN 30 untagged on a.1 and b.2, tagged on the cable: h1 reaches h3, not h2")

        ca, cb = net.labs["a"].console, net.labs["b"].console
        sa, sb = len(ca.log), len(cb.log)
        net.link("a.4", False)
        down = link_seen(ca, CABLE_PHYS, False, sa, 10) and link_seen(cb, CABLE_PHYS, False, sb, 10)
        gone = not ping("h1", "h3")
        sa, sb = len(ca.log), len(cb.log)
        net.link("a.4", True)
        up = link_seen(ca, CABLE_PHYS, True, sa, 10) and link_seen(cb, CABLE_PHYS, True, sb, 10)
        check(down and up, "a cable pull: both firmwares see port 4 go down and come back")
        back = any(ping("h1", "h3") for _ in range(5))
        check(gone and back, "and h1 -> h3 stops while it's out, and works again after")
    finally:
        net.stop()

    print(f"test_topo: {'ALL PASS' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
