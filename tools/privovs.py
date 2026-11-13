"""privovs.py - a private Open vSwitch for a test: its own ovsdb-server and
ovs-vswitchd (userspace datapath, --disable-system), run as root inside a
network namespace, with database, sockets and logs in one directory. Nothing
of the system's OVS (/var/run/openvswitch, /etc/openvswitch) is used.

    o = Ovs(ns, dir); o.start()
    o.bridge("sw0", controller=sock)          # rtlsim --ofp sock (None: later)
    o.veth("sw0", "h1", ofport=5)             # h1 in the namespace <-> port 5
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

    def stop(self):
        for p in ("vswitchd", "ovsdb"):
            pid = os.path.join(self.d, f"{p}.pid")
            if os.path.exists(pid):
                sudo("kill", open(pid).read().strip(), check=False)
        time.sleep(0.2)
