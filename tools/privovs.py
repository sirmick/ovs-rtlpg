"""privovs.py - a private Open vSwitch for a test: its own ovsdb-server and
ovs-vswitchd (userspace datapath, --disable-system), run as root inside a
network namespace, with database, sockets and logs in one directory. Nothing
of the system's OVS (/var/run/openvswitch, /etc/openvswitch) is used.

    o = Ovs(ns, dir); o.start()
    o.bridge("sw0", controller=sock)          # rtlsim --ofp sock (None: later)
    o.veth("sw0", "h1", ofport=5)             # h1 in the namespace <-> port 5
    o.port("sw0", "eth1", ofport=6)           # a real interface as port 6
    ...
    o.stop()
"""
import os, subprocess, time

SCHEMA = "/usr/share/openvswitch/vswitch.ovsschema"


def sudo(*cmd, check=True, **kw):
    r = subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, **kw)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r


class Ovs:
    def __init__(self, ns, d):
        self.ns, self.d = ns, d
        self.env = [f"OVS_{k}DIR={d}" for k in ("RUN", "LOG", "DB", "SYSCONF", "PKGDATA")]
        self.env[-1] = "OVS_PKGDATADIR=/usr/share/openvswitch"
        self.db = f"unix:{d}/db.sock"

    def run(self, *cmd, check=True):
        return sudo("ip", "netns", "exec", self.ns, "env", *self.env, *cmd, check=check)

    def vsctl(self, *args):
        return self.run("ovs-vsctl", f"--db={self.db}", "--timeout=10", *args).stdout

    def ofctl(self, *args):
        return self.run("ovs-ofctl", "-O", "OpenFlow13", *args).stdout

    def start(self):
        subprocess.run(["ovsdb-tool", "create", f"{self.d}/conf.db", SCHEMA], check=True, capture_output=True)
        self.run("/usr/sbin/ovsdb-server", f"{self.d}/conf.db", f"--remote=punix:{self.d}/db.sock",
                 f"--pidfile={self.d}/ovsdb.pid", "--detach", f"--log-file={self.d}/ovsdb.log")
        self.vsctl("--no-wait", "init")
        self.run("/usr/sbin/ovs-vswitchd", self.db, "--disable-system", f"--pidfile={self.d}/vswitchd.pid",
                 "--detach", f"--log-file={self.d}/vswitchd.log")

    def bridge(self, name, controller):
        """A bridge for one rtlsim: userspace datapath, OpenFlow 1.3 only, no
        in-band control and nothing of its own (fail_mode=secure: without
        rtlsim it forwards only what rtlsim installed)."""
        self.vsctl("add-br", name, "--", "set", "bridge", name, "datapath_type=netdev", "fail_mode=secure",
                   "protocols=OpenFlow13", "other-config:disable-in-band=true")
        if controller:
            self.vsctl("set-controller", name, f"unix:{controller}")

    def veth(self, bridge, host, ofport):
        """A veth pair: `host` stays in the namespace for Linux, the other end
        is OpenFlow port `ofport` (physical port ofport - 1). The host end
        computes its own checksums: the userspace datapath doesn't."""
        sw = f"{bridge}p{ofport}"
        ip = ["ip", "netns", "exec", self.ns]
        sudo(*ip, "ip", "link", "add", host, "type", "veth", "peer", "name", sw)
        sudo(*ip, "ethtool", "-K", host, "tx", "off")
        sudo(*ip, "ip", "link", "set", sw, "up")
        self.vsctl("add-port", bridge, sw, "--", "set", "interface", sw, f"ofport_request={ofport}")
        return sw

    def port(self, bridge, iface, ofport):
        """A real interface as OpenFlow port `ofport`: moved into the
        namespace (it leaves the host's own networking until stop()), with no
        address, IPv6 off so the kernel sends nothing of its own, and offloads
        off, as the userspace datapath wants whole frames."""
        if iface == "eth0" or iface.startswith("eth0@"):
            raise RuntimeError("privovs: eth0 is the host's uplink, never a switch port")
        ip = ["ip", "netns", "exec", self.ns]
        sudo("ip", "link", "set", iface, "down")
        sudo("ip", "link", "set", iface, "netns", self.ns)
        self.moved = getattr(self, "moved", []) + [iface]
        sudo(*ip, "sysctl", "-q", "-w", f"net.ipv6.conf.{iface}.disable_ipv6=1")
        sudo(*ip, "ip", "addr", "flush", "dev", iface)
        sudo(*ip, "ethtool", "-K", iface, "gro", "off", "gso", "off", "tso", "off", check=False)
        sudo(*ip, "ip", "link", "set", iface, "up")
        self.vsctl("add-port", bridge, iface, "--", "set", "interface", iface, f"ofport_request={ofport}")
        return iface

    def stop(self):
        for p in ("vswitchd", "ovsdb"):
            pid = os.path.join(self.d, f"{p}.pid")
            if os.path.exists(pid):
                sudo("kill", open(pid).read().strip(), check=False)
        time.sleep(0.2)
        self.give_back()

    def give_back(self):
        """Hand the real interfaces back, down: to the namespace of PID 1, the
        one they came from. Left in the namespace, a deleted namespace would
        return them to the kernel's first namespace, which in a container is
        the host's. Raises if one can't be moved, so the caller keeps the
        namespace (and the interface) rather than lose it."""
        for iface in list(getattr(self, "moved", [])):
            ip = ["ip", "netns", "exec", self.ns]
            sudo(*ip, "ip", "link", "set", iface, "down", check=False)
            sudo(*ip, "ip", "link", "set", iface, "netns", "1")
            sudo("ip", "link", "set", iface, "down")
            self.moved.remove(iface)
