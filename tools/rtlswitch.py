#!/usr/bin/env python3
"""rtlswitch.py - one simulated switch on a private OVS bridge, with Linux
hosts on its front ports.

    tools/rtlswitch.py IMAGE [--board boards/swtg024as.json] [--name sw0]
                       [--host FRONT=ADDR/LEN]... [--port FRONT=IFACE]...
                       [--flash FILE] [--rtlsim build/rtlsim] [-- RTLSIM-ARGS...]

The switch's serial console is this terminal (^C ends it all). Every front
port of the board is an OVS port: a veth whose other end, for a --host,
sits in a namespace of its own (rtl-NAME-FRONT, as eth0, with the address)
and stays down otherwise, so the firmware sees that port's link down. A
--port puts a real interface there instead (say eth1, cabled to a LAN or
another switch): it moves into the switch's namespace for as long as the
switch runs, with no address and IPv6 off, and is back, down, afterwards.
eth0 is refused. --flash keeps the switch's flash in a file, so its saved
config (address, VLANs...) survives a restart. Then:

    sudo ip netns exec rtl-sw0-1 ping 192.168.2.2
    sudo ip netns exec rtl-sw0-1 curl http://192.168.2.2/

OVS (tools/privovs.py) runs as root in the namespace rtl-NAME, with its
database and logs in a temporary directory; rtlsim runs as you, as the
bridge's OpenFlow controller (--ofp). Needs sudo. Nothing outside these
namespaces is touched, and they go when it ends.

As a library: Lab(image, board, name, hosts, extra, console=True).start()
and .stop(), with .console (a tests/fw/console.py Console). A host's address
may be None, to set later with .address(); start(attach=False) leaves the
bridge without its controller until .attach(), so the boot runs at full
speed before the firmware's clock is held to the wall clock.
"""
import argparse, json, os, re, shutil, signal, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from privovs import Ovs, sudo

DEFAULT_BOARD = os.path.join(HERE, "../boards/swtg024as.json")


def ifname(*parts):
    """An interface or namespace name: [A-Za-z0-9-], at most 15 characters."""
    return re.sub(r"[^A-Za-z0-9-]", "", "-".join(parts))[:15]


class Lab:
    def __init__(self, image, board=DEFAULT_BOARD, name="sw0", hosts=None, extra=(),
                 rtlsim=os.path.join(HERE, "../build/rtlsim"), console=False, stderr=None, ports=None):
        self.image, self.board_file, self.name = image, board, name
        self.board = json.load(open(board))
        self.hosts = hosts or {}		# front port label -> "addr/len"
        self.ports = ports or {}		# front port label -> a real interface
        self.extra, self.rtlsim, self.interactive, self.stderr = list(extra), rtlsim, not console, stderr
        self.ns = ifname("rtl", name)
        self.dir = tempfile.mkdtemp(prefix=f"rtlswitch-{name}-")
        os.chmod(self.dir, 0o755)	# root's OVS works in here
        self.sock = os.path.join(self.dir, "ofp.sock")
        self.ovs, self.proc, self.console = None, None, None
        fronts = [p["front"] for p in self.board["ports"]]
        for f in self.ports:
            if f in self.hosts:
                raise SystemExit(f"rtlswitch: front port {f} can't have both --host and --port")
        for f in [*self.hosts, *self.ports]:
            if f not in fronts:
                raise SystemExit(f"rtlswitch: the {self.board['name']} board has no front port {f!r} ({', '.join(fronts)})")

    def host_ns(self, front):
        return ifname("rtl", self.name, front)

    def address(self, front, addr):
        sudo("ip", "netns", "exec", self.host_ns(front), "ip", "addr", "add", addr, "dev", "eth0")

    def attach(self):
        self.ovs.vsctl("set-controller", self.name, f"unix:{self.sock}")

    def start(self, attach=True):
        sudo("ip", "netns", "add", self.ns)
        self.ovs = Ovs(self.ns, self.dir)
        self.ovs.start()
        self.ovs.bridge(self.name, controller=self.sock if attach else None)
        for p in self.board["ports"]:
            front, host = p["front"], ifname(self.name, "h", p["front"])
            if front in self.ports:
                self.ovs.port(self.name, self.ports[front], ofport=p["phys"] + 1)
                continue
            self.ovs.veth(self.name, host, ofport=p["phys"] + 1)
            if front not in self.hosts:
                continue
            ns = self.host_ns(front)
            sudo("ip", "netns", "add", ns)
            sudo("ip", "netns", "exec", self.ns, "ip", "link", "set", host, "netns", ns)
            nsx = ["ip", "netns", "exec", ns]
            for cmd in (["ip", "link", "set", "lo", "up"], ["ip", "link", "set", host, "name", "eth0"],
                        ["ip", "link", "set", "eth0", "up"]):
                sudo(*nsx, *cmd)
            if self.hosts[front]:
                self.address(front, self.hosts[front])
        args = ["--image", self.image, "--board", self.board_file, "--ofp", self.sock, "--cycles", "0", *self.extra]
        if self.interactive:
            self.proc = subprocess.Popen([self.rtlsim, *args])
        else:
            sys.path.insert(0, os.path.join(HERE, "../tests/fw"))
            from console import Console
            self.console = Console(self.rtlsim, self.image, args[2:], **({"stderr": self.stderr} if self.stderr else {}))
            self.proc = self.console.p
        return self

    def stop(self):
        if self.console:
            self.console.close()
        elif self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait()
        if self.ovs:
            try:
                self.ovs.stop()
            except RuntimeError as e:	# keep the namespace: it still holds an interface
                print(f"rtlswitch: {e}; namespace {self.ns} kept", file=sys.stderr)
                return
        for front in self.hosts:
            sudo("ip", "netns", "del", self.host_ns(front), check=False)
        sudo("ip", "netns", "del", self.ns, check=False)
        sudo("rm", "-rf", self.dir, check=False)


def main():
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        extra = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    ap = argparse.ArgumentParser(description="One simulated switch on a private OVS bridge.")
    ap.add_argument("image")
    ap.add_argument("--board", default=DEFAULT_BOARD)
    ap.add_argument("--name", default="sw0")
    ap.add_argument("--host", action="append", default=[], metavar="FRONT=ADDR/LEN")
    ap.add_argument("--port", action="append", default=[], metavar="FRONT=IFACE")
    ap.add_argument("--flash", metavar="FILE")
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../build/rtlsim"))
    a = ap.parse_args(argv)
    hosts = dict(h.split("=", 1) for h in a.host)
    ports = dict(p.split("=", 1) for p in a.port)
    if a.flash:
        extra = ["--flash", os.path.abspath(a.flash), *extra]
    lab = Lab(a.image, a.board, a.name, hosts, extra, a.rtlsim, ports=ports)
    try:
        lab.start()
        for front in hosts:
            print(f"rtlswitch: port {front}: sudo ip netns exec {lab.host_ns(front)} ...", file=sys.stderr)
        for front, iface in ports.items():
            print(f"rtlswitch: port {front}: {iface}", file=sys.stderr)
        signal.signal(signal.SIGINT, lambda *_: lab.proc.terminate())
        lab.proc.wait()
    finally:
        lab.stop()


if __name__ == "__main__":
    main()
