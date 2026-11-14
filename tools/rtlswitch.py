#!/usr/bin/env python3
"""rtlswitch.py - one simulated switch on a private OVS bridge, with Linux
hosts on its front ports.

    tools/rtlswitch.py IMAGE [--board boards/swgt024_v2_0_unmanaged.json] [--name sw0]
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

Several switches, cabled to each other (DESIGN.md, "Topology file"):

    tools/rtlswitch.py IMAGE --topo topo.yaml

    switches:
      a: { ip: 192.168.2.2 }                   # also image, board, flash, args: [...]
      b: { ip: 192.168.2.3, board: boards/swgt024_v2_0_unmanaged.json }
    links:
      - [a.4, b.4]                             # a cable: front port 4 to front port 4
    hosts:
      h1: { port: a.1, ip: 192.168.2.11/24 }
      h2: { port: b.1, ip: 192.168.2.12/24, vlan: 20 }   # on eth0.20 instead

A cable is a veth pair, one end a --port of each switch, so pulling it
(`link a.4 down`) takes the carrier from both. Each switch gets its own
namespace and OVS, as with one; switches on the same board file get
distinct chip UUIDs (so distinct MACs), and a switch's ip is set through
its CLI once it boots. The consoles share this terminal: output is
prefixed with the switch's name, and a line `NAME CMD` goes to that
switch's CLI; `link A.F up|down` pulls or replugs a cable; `NAME` alone
picks the switch that unprefixed lines go to. As a library: Net(image,
topo).start() and .stop(), .cli(NAME, CMD), .link(END, up),
.sh(HOST, *cmd).
"""
import argparse, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from privovs import Ovs, sudo

DEFAULT_BOARD = os.path.join(HERE, "../boards/swgt024_v2_0_unmanaged.json")


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
        self.created = []		# namespaces this lab made, the only ones stop() removes
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
        if os.path.exists(f"/run/netns/{self.ns}"):
            raise SystemExit(f"rtlswitch: namespace {self.ns} exists: is a switch called {self.name} running? (--name)")
        sudo("ip", "netns", "add", self.ns)
        self.created.append(self.ns)
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
            self.created.append(ns)
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
        for ns in reversed(self.created):
            sudo("ip", "netns", "del", ns, check=False)
        self.created = []
        sudo("rm", "-rf", self.dir, check=False)


PROMPT = b"\n> "


class Net:
    """Switches (each a Lab, with its console), cables between their front
    ports and Linux hosts on them, from a topology (a dict, as the YAML)."""

    def __init__(self, image, topo, rtlsim=os.path.join(HERE, "../build/rtlsim"), prefix="", stderr=None):
        self.topo, self.prefix = topo, prefix
        self.dir = tempfile.mkdtemp(prefix="rtlswitch-net-")
        self.labs, self.cables, self.made = {}, {}, []	# name -> Lab; "a.4" -> veth end; veths made
        self.hostport = {}				# host -> (switch, front)
        sws = topo.get("switches") or {}
        for i, (name, sw) in enumerate(sws.items()):
            sw = sw or {}
            board = sw.get("board", DEFAULT_BOARD)
            b = json.load(open(board))
            if i:	# a chip of its own: the firmware's default MAC comes from the UUID
                b["uuid"] = hex(int(b.get("uuid", "0xc8540700"), 16) ^ (i << 8))
                board = os.path.join(self.dir, f"{name}.json")
                json.dump(b, open(board, "w"))
            extra = [*(["--flash", os.path.abspath(sw["flash"])] if sw.get("flash") else []), *sw.get("args", [])]
            self.labs[name] = dict(image=sw.get("image", image), board=board, extra=extra)
        hosts = {n: {} for n in self.labs}
        for h, spec in (topo.get("hosts") or {}).items():
            s, f = self.end(spec["port"])
            hosts[s][f] = None if spec.get("vlan") else spec.get("ip")
            self.hostport[h] = (s, f)
        ports = {n: {} for n in self.labs}
        for link in topo.get("links") or []:
            if len(link) != 2:
                raise SystemExit(f"rtlswitch: a link joins two ports, not {link}")
            for end in link:
                s, f = self.end(end)
                if f in ports[s] or f in hosts[s]:
                    raise SystemExit(f"rtlswitch: {end} is used twice")
                ports[s][f] = self.cables[end] = ifname(prefix, s, "c", f)
            self.made.append(tuple(self.cables[e] for e in link))
        for name, kw in self.labs.items():
            self.labs[name] = Lab(name=ifname(prefix, name), hosts=hosts[name], ports=ports[name], rtlsim=rtlsim,
                                  console=True, stderr=stderr, **kw)

    def end(self, ref):
        s, _, f = str(ref).partition(".")
        if s not in self.labs or not f:
            raise SystemExit(f"rtlswitch: {ref!r} isn't SWITCH.FRONT, with a switch of {', '.join(self.labs)}")
        return s, f

    def start(self):
        for a, b in self.made:
            sudo("ip", "link", "add", a, "type", "veth", "peer", "name", b)
        for lab in self.labs.values():
            lab.start(attach=False)
        for name, lab in self.labs.items():
            if not lab.console.expect(PROMPT, 60):
                raise RuntimeError(f"rtlswitch: switch {name} didn't boot to its prompt")
        for h, spec in (self.topo.get("hosts") or {}).items():
            for sysctl in ("net.ipv6.conf.all.disable_ipv6=1", "net.ipv6.conf.default.disable_ipv6=1"):
                self.sh(h, "sysctl", "-q", "-w", sysctl)
            if spec.get("vlan"):
                dev = f"eth0.{spec['vlan']}"
                self.sh(h, "ip", "link", "add", "link", "eth0", "name", dev, "type", "vlan", "id", str(spec["vlan"]),
                        check=True)
                if spec.get("ip"):
                    self.sh(h, "ip", "addr", "add", spec["ip"], "dev", dev, check=True)
                self.sh(h, "ip", "link", "set", dev, "up", check=True)
        for name, sw in (self.topo.get("switches") or {}).items():
            if sw and sw.get("ip"):
                self.cli(name, f"ip {sw['ip']}")
        for lab in self.labs.values():
            lab.attach()
        return self

    def cli(self, name, cmd, timeout=10):
        c = self.labs[name].console
        start = len(c.log)
        c.send(cmd)
        c.expect(PROMPT, timeout)
        return c.log[start:].decode(errors="replace")

    def link(self, end, up):
        """Pulls (up=False) or replugs a cable, by either of its ends."""
        s, _ = self.end(end)
        if end not in self.cables:
            raise KeyError(f"rtlswitch: {end} has no cable")
        sudo("ip", "netns", "exec", self.labs[s].ns, "ip", "link", "set", self.cables[end], "up" if up else "down")

    def host_ns(self, host):
        s, f = self.hostport[host]
        return self.labs[s].host_ns(f)

    def sh(self, host, *cmd, check=False, timeout=30):
        return sudo("ip", "netns", "exec", self.host_ns(host), *cmd, check=check, timeout=timeout)

    def mac(self, name):
        """The MAC the firmware gave itself at boot."""
        m = re.search(rb"Setting MAC to: ([0-9a-fA-F:]{17})", self.labs[name].console.log)
        return m.group(1).decode().lower() if m else None

    def stop(self):
        for lab in self.labs.values():
            lab.stop()
        for a, _ in self.made:	# the ends are back in this namespace, down
            sudo("ip", "link", "del", a, check=False)
        shutil.rmtree(self.dir, ignore_errors=True)


def run_topo(image, topo_file, rtlsim):
    import yaml
    net = Net(image, yaml.safe_load(open(topo_file)), rtlsim)
    lock = threading.Lock()

    def pump(name, c):	# the console's output, a line at a time, behind its name
        buf = b""
        while True:
            d = os.read(c.p.stdout.fileno(), 4096)
            if not d:
                break
            c.log += d.replace(b"\r", b"")
            buf += d.replace(b"\r", b"")
            *lines, buf = buf.split(b"\n")
            with lock:
                for l in lines:
                    print(f"[{name}] {l.decode(errors='replace')}", flush=True)
                if buf.startswith(b"> ") or buf == b">":
                    print(f"[{name}] {buf.decode(errors='replace')}", flush=True)
                    buf = b""

    try:
        net.start()
        for name, lab in net.labs.items():
            sys.stdout.write("".join(f"[{name}] {l}\n" for l in lab.console.log.decode(errors="replace").splitlines()))
        for h in net.hostport:
            print(f"rtlswitch: host {h}: sudo ip netns exec {net.host_ns(h)} ...", file=sys.stderr)
        for name, lab in net.labs.items():
            threading.Thread(target=pump, args=(name, lab.console), daemon=True).start()
        cur = next(iter(net.labs))
        print(f"rtlswitch: lines go to {cur}; NAME CMD for another, link A.F up|down, ^D or ^C ends it",
              file=sys.stderr)
        for line in sys.stdin:
            words = line.split()
            if words[:1] == ["link"] and len(words) == 3 and words[2] in ("up", "down"):
                try:
                    net.link(words[1], words[2] == "up")
                except (KeyError, SystemExit) as e:
                    print(e, file=sys.stderr)
            elif words and words[0] in net.labs:
                if len(words) == 1:
                    cur = words[0]
                else:
                    net.labs[words[0]].console.send(line.strip()[len(words[0]):].strip())
            else:
                net.labs[cur].console.send(line.rstrip("\n"))
    except KeyboardInterrupt:
        pass
    finally:
        net.stop()


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
    ap.add_argument("--topo", metavar="FILE", help="several switches and their cables, from a YAML file")
    a = ap.parse_args(argv)
    if a.topo:
        return run_topo(a.image, a.topo, a.rtlsim)
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
