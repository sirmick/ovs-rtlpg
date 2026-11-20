#!/usr/bin/env python3
"""test_translate.py - the old commands' translation for an object-model
image (translate.py, oldnew.Run.cli()), the expect file's line items
(oldnew.compare()) and the state-path map (P1..: every sample of an old
surface in om_samples.json placed on paths, each row's old text against the
same values from get, the conversions), no rtlsim:

    python3 tests/oldnew/test_translate.py

A fake console answers each line from a table; the cases check what each
side types and what its result is. Exit 0 when every case passes.
"""
import difflib, json, os, sys, tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from oldnew import Run, compare, load_expect, _link_first
import translate

STAT_OLD = ("\nPort\tState\tLink\tTxGood\t\tTxBad\t\tRxGood\t\tRxBad\t\tMACs\n"
            "1\tOn\t1000M\t0x00000010\t0x00000000\t0x00000020\t0x00000001\t1\n"
            "2\tOff\tDown\t0x00000000\t0x00000000\t0x00000000\t0x00000000\t0\n"
            "3\tNO SFP\tDown\t0x00000000\t0x00000000\t0x00000000\t0x00000000\t0\n")
STAT_NEW = ("state.port.1.link up\nstate.port.1.speed 1g\nstate.port.1.duplex full\nstate.port.1.macs 1\n"
            "state.port.1.tx_good 4294967312\nstate.port.1.rx_good 32\nstate.port.1.tx_bad 0\nstate.port.1.rx_bad 1\n"
            "state.port.2.link down\nstate.port.2.speed unknown\nstate.port.2.macs 0\nstate.port.2.tx_good 0\n"
            "state.port.2.rx_good 0\nstate.port.2.tx_bad 0\nstate.port.2.rx_bad 0\n"
            "state.port.3.link down\nstate.port.3.speed unknown\nstate.port.3.macs 0\nstate.port.3.tx_good 0\n"
            "state.port.3.rx_good 0\nstate.port.3.tx_bad 0\nstate.port.3.rx_bad 0\nok\n")


class Console:
    """Answers a line from `replies` (else "ok"), as the bench does: the
    echo, the reply, the prompt; records what was typed."""
    def __init__(self, replies):
        self.replies, self.typed, self.now = replies, [], 0

    def advance(self, t, until=None):
        self.now = max(self.now, t)
        return True

    def pending(self):
        return ""

    def console(self, line, until):
        self.typed.append(line)
        self.now += 1000
        x = self.replies.get(line, "ok")
        if isinstance(x, list):		# one reply after another
            x = x.pop(0) if len(x) > 1 else x[0]
        return "", f"{line}\n{x}\n> "


def run(side_om, line, replies, board=None, canon=("port",)):
    r = object.__new__(Run)
    r.b, r.t, r.n, r.results, r.replies = Console(replies), 0, 0, {}, {}
    r.om, r.oms, r.canon = set(side_om), [set(), set(canon)], set(canon)
    r.P, r.F, r.cages = ([0, 1], ["1", "2"], {1: (8, "3")}) if board is None else (board.P, board.F, board.cages)
    r.cli(line)
    return r.b.typed, r.results[f"000 cli {line}"]


def main():
    fails = 0

    def case(name, ok, detail):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    t, res = run((), "pvid 2 10", {"pvid 2 10": ""})
    case("A", t == ["pvid 2 10"] and res == "port.2.pvid 10", f"old: types the old line, its values ({t}, {res!r})")
    t, res = run((), "pvid 2 10", {"pvid 2 10": "Error: pvid <port> <0-4094>"})
    case("B", res == "refused", f"old: an Error: is refused ({res!r})")
    t, res = run({"port"}, "pvid 2 10", {"get port.2.pvid": "port.2.pvid 10\nok"})
    case("C", t == ["set port.2.pvid 10", "apply", "get port.2.pvid"] and res == "port.2.pvid 10",
         f"new: set, apply, get ({t}, {res!r})")
    t, res = run({"port"}, "pvid 2 9999", {"set port.2.pvid 9999": "err port.2.pvid: 0-4094"})
    case("D", t == ["set port.2.pvid 9999", "discard"] and res == "refused", f"new: an err discards ({t}, {res!r})")
    t, res = run({"port"}, "pvid 2 10", {"apply": "err port.2: no"})
    case("E", t == ["set port.2.pvid 10", "apply", "discard"] and res == "refused", f"new: apply's err too ({t})")

    to, ro = run((), "stat", {"stat": STAT_OLD})
    tn, rn = run({"port"}, "stat", {"get state.port": STAT_NEW, "get port.*.enabled": "port.1.enabled on\nport.2.enabled off\nok"})
    case("F", to == ["stat"] and tn == ["get state.port", "get port.*.enabled"] and ro == rn and
         "state.port.1.tx_good 16" in ro and "state.port.2.speed" not in ro and "port.3.enabled" not in ro,
         f"stat by value, the counters' low 32 bits, no speed without a link:\n{ro}" if ro != rn else "the same values")
    tn, rn2 = run({"port"}, "stat", {"get state.port": STAT_NEW.replace("rx_bad 1", "rx_bad 2"),
                                     "get port.*.enabled": "port.1.enabled on\nport.2.enabled off\nok"})
    case("G", rn2 != ro, "a counter that differs is a difference")

    t, res = run((), "pvid", {"pvid": "Error: pvid <port> <0-4094>"})
    t2, res2 = run({"port"}, "pvid", {})
    case("H", res == res2 == "refused" and t2 == [], f"a usage error: refused on both, the new one types nothing ({t2})")
    t, res = run((), "pvid", {"pvid": "something"})
    case("I", res != "refused", "the old one accepting it is a difference")
    t, res = run({"port"}, "version", {"version": "table"})
    case("J", t == ["version"] and res == "version\ntable\n> ", "a line whose rows' objects aren't named goes as it is")
    t, res = run({"port"}, "ingress t", {})
    case("K", t[:3] == ["set port.1.ingress tagged", "set port.2.ingress tagged", "set port.3.ingress tagged"],
         f"ingress for every port, the cage's too ({t[:3]})")

    class Host:
        def http(self, req, until):
            self.req = req
            return b"HTTP/1.1 200 OK\r\n\r\nok\nok\n"
    sent = []
    for om in ({"sys"}, set()):
        r = object.__new__(Run)
        r.canon, r.om, r.cookie, r.results, r.host = {"sys"}, om, b"c", {}, Host()
        r._slot = lambda s: 0
        r._key = lambda k: k
        Run.post_cmd(r, "hostname lab1")
        sent.append((r.host.req, r.results.get("web POST /cmd")))
    case("L", sent[0][0].startswith(b"POST /cli ") and sent[0][0].endswith(b"set sys.hostname lab1\napply")
         and sent[1][0].startswith(b"POST /cmd ") and sent[1][0].endswith(b"hostname lab1")
         and sent[0][1] == sent[1][1] == "taken",
         "POST /cmd of a moved object: set lines and apply by POST /cli there, as it is on the other; both taken")

    f = tempfile.NamedTemporaryFile("w", suffix=".expect", delete=False)
    f.write("* *boot -/EEE on for \\d+/ -//   # why\n* reg 0f60\n")
    f.close()
    ex = load_expect(f.name)
    os.unlink(f.name)
    old = {"000 boot": "a\n\nEEE on for 1\nEEE on for 2\nb", "reg 0f60": "1", "reg 0f64": "1"}
    new = {"000 boot": "a\nb", "reg 0f60": "<absent>", "reg 0f64": "<absent>"}
    used = set()
    bad, ok = compare("vlan", old, new, ex, used)
    case("M", len(ok) == 2 and len(bad) == 1 and "reg 0f64" in bad[0] and used == {1, 2},
         f"lost lines matched, a whole result, one not listed ({len(ok)} expected, {len(bad)} not)")
    used = set()
    bad, ok = compare("vlan", old, {"000 boot": "a\nb\nc"}, ex, used)
    case("N", any("000 boot" in b for b in bad) and 1 not in used, "a gained line not listed is a difference")

    f = tempfile.NamedTemporaryFile("w", suffix=".expect", delete=False)
    f.write("* *boot -/Verifying:\\n\\nPort\\t.*(\\n\\d+\\t.*)+/\n")
    f.close()
    ex = load_expect(f.name)
    os.unlink(f.name)
    blk = "a\nVerifying:\n\nPort\tState\n1\tOn\n2\tOff\nb"
    ok1 = compare("boot", {"000 boot": blk}, {"000 boot": "a\nb"}, ex)[0] == []
    ok2 = compare("boot", {"000 boot": "x\n\n" + blk}, {"000 boot": "x\na\nb"}, ex)[0] != []
    case("O", ok1 and ok2, "a block item covers its run of lines, not a blank line elsewhere")
    fails += not (ok1 and ok2)
    ok = translate.by_post("/cmd_log_clear", {"http.post"}) and translate.by_post("/l2_del.json?idx=12", {"http.post"}) \
        and not translate.by_post("/cmd_log_clear", {"port"}) and not translate.by_post("/cmd_log", {"http.post"})
    case("R", ok, "the action pages by POST on an image with http.post, GET on the others")
    fails += not ok
    B = SimpleNamespace(P=[0, 1], F=["1", "2"], cages={1: (8, "3")})
    dump = ["r 1250 0fffffff", "r 1350 00000000", "r 4e1c 00001001", "r 4e18 00000103", "r 50c0 00000303",
            "r 50c4 00000303", "r 50e0 00000303", "r 125c 00000003", "r 135c 00000003", "p 1.1f.a610 2858"]
    run_ = {f"port.{n}.{f}": x for n in (1, 2, 3) for f, x in (("mtu", "16383"), ("pvid", "1"), ("ingress", "all"),
                                                              ("ingress_filter", "on"), ("isolate", "1-3,cpu"),
                                                              ("eee", "on"), ("enabled", "on"))}
    bad, n = translate.boot_defaults(dump, run_, B, {"port"})
    got = sorted(p for p, _, _ in bad)
    ok = got == ["port.2.enabled", "port.2.mtu", "port.3.mtu", "port.3.pvid"] and n == 19
    case("S", ok, f"boot defaults: a register the boot left at 0 against its default, a PHY powered down: {got} of {n}")
    fails += not ok
    m, w = surface("cli stat")
    nc = w.not_compared([set(), {"port"}])
    ok = nc == ["not compared: state.sfp.#.present (state.sfp pending)"] and w.not_compared([set(), set()]) == []
    case("T", ok, f"a live row says what it can't compare yet: {nc}")
    fails += not ok
    import bench
    outs = []
    for tail in ("> ", "*> ", "[confirm 30] > ", "[confirm 5] *> "):
        b = object.__new__(bench.Bench)
        b.uart = f"version\nv1\n{tail}"
        b.take_uart, b.advance = (lambda b=b: b.uart), (lambda until, cond: cond())
        outs.append(b._reply(bench.PROMPT, 0))
    lines = translate._lines("stat\nx\n[confirm 5] *> ")
    ok = outs == ["version\nv1\n> "] * 4 and lines == ["x"]
    case("U", ok, f"P3A's prompts ([confirm N], *) end a reply, which keeps the bare prompt; a parser drops them ({outs[3]!r})")
    fails += not ok
    fails += real_gets(case)
    fails += config_rows(case)
    fails += state_paths(case)
    sys.exit(1 if fails else 0)


# Replies of feat/om dab5d13 in rtlsim (2026-11-20), as get prints them
REAL = {"get port.2.isolate": "port.2.isolate 1,3-4,cpu\nok", "get port.7.isolate": "port.7.isolate 1-9,cpu\nok",
        "get port.3.name": "port.3.name front-three\nok", "get port.4.speed": "port.4.speed 100m\nok",
        "get port.4.duplex": "port.4.duplex half\nok", "get port.4.enabled": "port.4.enabled on\nok",
        "get port.5.eee": "port.5.eee off\nok", "get port.6.enabled": "port.6.enabled off\nok",
        "set port.9.speed 1g": "err port.9.speed: not on this port",
        "apply": "ok"}


def real_gets(case):
    """The settings' two sides, the new one from real get replies: the
    old line accepted means the values its parser sets (parse_port(),
    parse_isolate()), and get prints them back in its own form."""
    fails = 0
    B = SimpleNamespace(P=list(range(8)), F=[str(i + 1) for i in range(8)], cages={1: (8, "9")})
    for name, old, want, real in [
            ("Q1", "isolate 2 1 3 4 0", "port.2.isolate 1,3,4,cpu", REAL),
            ("Q2", "isolate 7 off", "port.7.isolate 1,2,3,4,5,6,7,8,9,cpu",
             {**REAL, "get port.7.isolate": "port.7.isolate 1-9,cpu\nok"}),
            ("Q3", "port 3 name front-three", "port.3.name front-three", REAL),
            ("Q4", "port 4 100m half", "port.4.duplex half\nport.4.enabled on\nport.4.speed 100m", REAL),
            ("Q5", "port 4 1g half", "port.4.duplex auto\nport.4.enabled on\nport.4.speed 1g",
             {**REAL, "get port.4.speed": "port.4.speed 1g\nok", "get port.4.duplex": "port.4.duplex auto\nok"}),
            ("Q6", "port 6 off", "port.6.enabled off", REAL),
            ("Q7", "port 4 on", "port.4.duplex auto\nport.4.enabled on\nport.4.speed auto",
             {**REAL, "get port.4.speed": "port.4.speed auto\nok", "get port.4.duplex": "port.4.duplex auto\nok"}),
            ("Q8", "eee off 5", "port.5.eee off", REAL)]:
        _, o = run((), old, {old: ""}, B)
        t, n = run({"port"}, old, real, B)
        ok = o == n == want
        fails += not ok
        case(name, ok, f"{old!r}: old and new both {want!r}" if ok else f"{old!r}: old {o!r}, new {n!r} (typed {t})")
    _, o = run((), "port 9 1g", {"port 9 1g": "Logical Port: 08\n is SFP no PHY information available."}, B)
    t, n = run({"port"}, "port 9 1g", REAL, B)
    _, o2 = run((), "port 2 lldp x", {"port 2 lldp x": "Logical Port: 01\nUnknown port <port> lldp [block|permit] command"}, B)
    ok = o == n == o2 == "refused" and "discard" in t
    fails += not ok
    case("Q9", ok, f"the old SFP and lldp refusals without Error: are refused; the new err too ({o!r}, {n!r}, {o2!r})")
    return fails


# --- the config commands of the P4 objects (P3H): each row on both images.
# A case: the old line, its objects, what the old says (Error..., or ""),
# the values it means (or "refused", "ok"), the lines the new image is
# typed (None: not checked), the new image's state before (get's values).
# The new image's get replies after are the wanted values: the result
# must come back the same (a deleted entry: get refuses it).

E_ = "Error: no"
CONFIG = [
    ("hostname sw-1", "sys", "", {"sys.hostname": "sw-1"}, ["set sys.hostname sw-1", "apply", "get sys.hostname"], None),
    ('hostname a"b\\c', "sys", "", {"sys.hostname": "a.b.c"}, None, None),
    ("hostname a b", "sys", E_, "refused", [], None),
    ("mac 02-11-22-33-44-55", "sys.mac", "", {"sys.mac": "02:11:22:33:44:55"}, ["set sys.mac 02:11:22:33:44:55", "apply", "get sys.mac"], None),
    ("mac 01:00:00:00:00:01", "sys.mac", E_, "refused", [], None),
    ("mac 00:00:00:00:00:01", "sys.mac", E_, "refused", [], None),
    ("led off ports", "sys", "", {"sys.led": "ports_off"}, None, None),
    ("led on", "sys", "", {"sys.led": "on"}, None, None),
    ("led blink", "sys", E_, "refused", [], None),
    ("passwd my long secret", "web", "", {"web.password": "*"}, ["set web.password my long secret", "apply", "get web.password"], None),
    ("passwd 123456789012345678901234", "web", "", {"web.password": "*"}, ["set web.password 12345678901234567890", "apply",
                                                                          "get web.password"], None),
    ("session 300", "web", "Session timeout: 300 s", {"web.session_timeout_s": "300"}, None, None),
    ("session 0", "web", "Must be 1-65535 s", "refused", None, None),
    ("ip 10.0.0.2", "mgmt", "", {"mgmt.ip": "10.0.0.2", "mgmt.mode": "static"}, None, None),
    ("ip 300.0.0.2", "mgmt", E_, "refused", [], None),
    ("ip dhcp", "mgmt", "", {"mgmt.mode": "dhcp"}, ["get mgmt.mode", "set mgmt.mode dhcp", "apply", "do mgmt.dhcp_renew", "get mgmt.mode"],
     {"mgmt.mode": "dhcp"}),
    ("ip dhcp", "mgmt", "", {"mgmt.mode": "dhcp"}, ["get mgmt.mode", "set mgmt.mode dhcp", "apply", "get mgmt.mode"], {"mgmt.mode": "static"}),
    ("gw 10.0.0.1", "mgmt", "", {"mgmt.gateway": "10.0.0.1"}, None, None),
    ("netmask 255.255.0.0", "mgmt", "", {"mgmt.netmask": "255.255.0.0"}, None, None),
    ("vlan 5 mgmt", "mgmt", "", {"mgmt.vlan": "5"}, ["get mgmt.mode", "get mgmt.vlan", "set mgmt.vlan 5", "apply", "do mgmt.dhcp_renew",
                                                     "get mgmt.vlan"], {"mgmt.mode": "dhcp", "mgmt.vlan": "1"}),
    ("vlan 5 mgmt", "mgmt", "", {"mgmt.vlan": "5"}, ["get mgmt.mode", "get mgmt.vlan", "set mgmt.vlan 5", "apply", "get mgmt.vlan"],
     {"mgmt.mode": "dhcp", "mgmt.vlan": "5"}),
    ("dns server 1.1.1.1", "dns", "", {"dns.server1": "1.1.1.1", "dns.server2": "0.0.0.0"}, None, None),
    ("dns server 1.1.1.1 8.8.8.8", "dns", "", {"dns.server1": "1.1.1.1", "dns.server2": "8.8.8.8"}, None, None),
    ("dns server 1.1.1.1 bad", "dns", E_, {"dns.server1": "1.1.1.1", "dns.server2": "0.0.0.0"}, None, None),
    ("dns server bad", "dns", E_, "refused", [], None),
    ("ntp on", "ntp", "", {"ntp.enabled": "on"}, ["get ntp.enabled", "set ntp.enabled on", "apply", "do ntp.sync", "get ntp.enabled"],
     {"ntp.enabled": "on"}),
    ("ntp on", "ntp", "", {"ntp.enabled": "on"}, ["get ntp.enabled", "set ntp.enabled on", "apply", "get ntp.enabled"], {"ntp.enabled": "off"}),
    ("ntp timezone 5:30", "ntp", "", {"ntp.timezone": "+05:30"}, None, None),
    ("ntp timezone -12", "ntp", "", {"ntp.timezone": "-12:00"}, None, None),
    ("ntp timezone +15", "ntp", E_, "refused", [], None),
    ("ntp interval 0", "ntp", E_, "refused", [], None),
    ("ntp dst eu", "ntp", "", {"ntp.dst": "eu"}, None, None),
    ("ntp server pool.example", "ntp", "", {"ntp.server": "pool.example"}, None, None),
    ("syslog ip 10.0.0.9", "syslog", "", {"syslog.server": "10.0.0.9"}, None, None),
    ("syslog port 1514", "syslog", "", {"syslog.port": "1514"}, None, None),
    ("syslog on", "syslog", "", {"syslog.enabled": "on"}, None, None),
    ("vlan 10 office 2 3t", "vlan", "", {"vlan.10.name": "office", "vlan.10.members": "2,3", "vlan.10.tagged": "3"}, None, None),
    ("vlan 10 2 3", "vlan", "", {"vlan.10.members": "2,3", "vlan.10.tagged": "none"}, None, None),
    ("vlan 10 office", "vlan", "", {"vlan.10.name": "office", "vlan.10.members": "none", "vlan.10.tagged": "none"}, None, None),
    ("vlan 10 dmz", "vlan", "", {"vlan.10": "<deleted>"}, ["del vlan.10", "apply", "get vlan.10"], None),
    ("vlan 10 d", "vlan", "", {"vlan.10": "<deleted>"}, None, None),
    ("vlan 0 2", "vlan", E_, "refused", [], None),
    ("vlan 4095 2", "vlan", E_, "refused", [], None),
    ("lag 1 3 4", "lag", "", {"lag.1.mode": "static", "lag.1.members": "3,4"}, None, None),
    ("lag 2 d", "lag", "", {"lag.2.mode": "off", "lag.2.members": "none"}, None, None),
    ("lag 1 lacp 3 4", "lag", "", "ok", ["get lag.1.mode", "get lag.1.members", "get lag.2.mode", "get lag.2.members", "get lag.3.mode",
                                        "get lag.3.members", "get lag.4.mode", "get lag.4.members", "set lag.1.mode lacp",
                                        "set lag.1.members 3,4", "set lag.2.members 5", "apply"],
     {"lag.2.mode": "lacp", "lag.2.members": "4-5"}),
    ("lag 1 lacp 3 4", "lag", "", "ok", None, {"lag.2.mode": "lacp", "lag.2.members": "4"}),
    ("lacp off", "lag", "", "ok", None, {"lag.1.mode": "lacp", "lag.3.mode": "lacp", "lag.2.mode": "static"}),
    ("lag 3 lacp off", "lag", "", "ok", ["get lag.3.mode"], {"lag.3.mode": "static"}),
    ("laghash 1 smac dmac", "lag", "", {"lag.1.hash": "dmac,smac"}, None, None),
    ("laghash 1 bogus", "lag", E_, "refused", [], None),
    ("stp prio 4", "stp", "", {"stp.priority": "4"}, None, None),
    ("stp hello 11", "stp", E_, "refused", [], None),
    ("stp port 3 edge auto", "stp", "", {"stp.port.3.edge": "auto"}, None, None),
    ("stp lag 2 prio 32", "stp", "", {"stp.lag.2.priority": "32"}, None, None),
    ("stp port 3 prio 33", "stp", E_, "refused", [], None),
    ("stp port 3 cost 20000", "stp", "", {"stp.port.3.cost": "20000"}, None, None),
    ("stp clear", "stp", "", "ok", ["do stp.counters_clear"], None),
    ("stp port 2 mcheck", "stp", "", "ok", ["do stp.port.2.mcheck"], None),
    ("stp version stp", "stp", "", {"stp.version": "stp"}, None, None),
    ("igmp on", "igmp", "", {"igmp.enabled": "on"}, None, None),
    ("lldp on", "lldp", "", {"lldp.enabled": "on"}, None, None),
    ("lldp show", "lldp", "", {"lldp.enabled": "off"}, None, None),
    ("lldp", "lldp", "", {"lldp.enabled": "off"}, None, None),
    ("loop on", "loop", "", {"loop.enabled": "on"}, None, None),
    ("mirror 1 2 3r 4t", "mirror", "", {"mirror.enabled": "on", "mirror.port": "1", "mirror.rx": "2,3", "mirror.tx": "2,4"}, None, None),
    ("mirror off", "mirror", "", {"mirror.enabled": "off"}, None, None),
    ("bw in 2 100", "port.design", "", {"port.2.bw_in_kbps": "256", "port.2.bw_in_mode": "fc"}, None, None),
    ("bw in 2 1f", "port.design", "", {"port.2.bw_in_kbps": "16", "port.2.bw_in_mode": "fc"}, None, None),
    ("bw out 3 off", "port.design", "", {"port.3.bw_out_kbps": "0"}, None, None),
    ("bw in 2 drop", "port.design", "", {"port.2.bw_in_mode": "drop"}, None, None),
    ("bw out 2 drop", "port.design", E_, "refused", [], None),
    ("storm 3 bcast 1000 pps", "port.design", "", {"port.3.storm.bcast.rate": "1000", "port.3.storm.bcast.unit": "pps"}, None, None),
    ("storm 3 bcast 0 pps", "port.design", "usage: storm [...]", "refused", [], None),
    ("storm 3 umcast off", "port.design", "", {"port.3.storm.umcast.rate": "0"}, None, None),
    ("sfp 1 10g", "port.design", "", {"port.9.speed": "10g"}, ["set port.9.speed 10g", "apply", "get port.9.speed"], None),
    ("sfp 2 10g", "port.design", E_, "refused", [], None),
]


def config_rows(case):
    """Each CONFIG case on the old image (the line typed, its meaning or
    refused) and the new one (the lines typed; the values read back the
    same). The objects named only for the case."""
    fails, bad = 0, []
    B = SimpleNamespace(P=list(range(8)), F=[str(i + 1) for i in range(8)], cages={1: (8, "9")})
    for line, obj, says, want, typed, prev in CONFIG:
        canon = (obj,)
        _, o = run((), line, {line: says}, B, canon)
        w = want if isinstance(want, str) else translate.canon(translate.normalized(want))
        rep = {f"get {p}": f"{p} {x}\nok" for p, x in (prev or {}).items()}
        after = {} if isinstance(want, str) else {f"get {p}": "err no such" if x == "<deleted>" else f"{p} {x}\nok"
                                                  for p, x in want.items()}
        if isinstance(want, dict) and "web.password" in want:
            after = {"get web.password": "web.password *\nok"}
        both = {k: [rep[k], after[k]] if k in rep and k in after else rep.get(k, after.get(k)) for k in set(rep) | set(after)}
        t, n = run({obj}, line, both, B, canon)
        ok = o == w and n == w and (typed is None or t == typed)
        if not ok:
            bad.append(f"{line!r}: old {o!r}, new {n!r}, want {w!r}" + (f", typed {t}" if typed is not None and t != typed else ""))
    fails += bool(bad)
    case("C1", not bad, f"{len(CONFIG)} config lines of {len({c[1] for c in CONFIG})} objects, both images, the side effects "
         "(ip dhcp and vlan N mgmt renew, ntp on syncs, lacp moves ports, bw rate means fc, ...)" + ("; " + "; ".join(bad[:4]) if bad else ""))
    # every config command form has a row (CONF_CMDS and the others)
    forms = ["hostname x", "mac 02:00:00:00:00:01", "led on", "passwd x", "session 9", "ip 1.2.3.4", "ip dhcp", "gw 1.2.3.4",
             "netmask 255.0.0.0", "vlan 9 mgmt", "dns server 1.2.3.4", "ntp on", "ntp off", "ntp server x", "ntp interval 5",
             "ntp timezone +01:00", "ntp dst us", "syslog on", "syslog ip 1.2.3.4", "syslog port 5", "vlan 9 a 1 2t", "vlan 9 d",
             "lag 1 2 3", "lag 1 lacp 2 3", "lag 1 d", "lag 1 lacp off", "lacp off", "laghash 1 sip", "stp on", "stp prio 1",
             "stp hello 2", "stp maxage 20", "stp fwd 15", "stp txhold 6", "stp version rstp", "stp pathcost short",
             "stp bpdu filter", "stp port 1 on", "stp lag 1 off", "stp port 1 edge on", "stp port 1 cost 5", "stp port 1 prio 16",
             "stp port 1 guard bpdu", "stp port 1 filter on", "stp port 1 p2p on", "igmp on", "lldp on", "loop on",
             "mirror 1 2", "mirror off", "bw in 1 10", "bw in 1 fc", "bw out 1 10", "storm 1 bcast 5 pps", "storm 1 bcast off",
             "sfp 1 1g", "port 1 duplex full", "port 1 name x", "port 1 on", "port 1 1g", "pvid 1 1", "mtu 1 1500",
             "isolate 1 off", "ingress 1u", "eee on 1"]
    every = {o for _, w in translate.ROWS for o in w.objs if o}
    miss = [f for f in forms if not translate.find(f, every) or isinstance(translate.find(f, every)[1], translate.Refused)]
    fails += bool(miss)
    case("C2", not miss, f"{len(forms)} config command forms, each with a row that translates it" + (f": missing {miss}" if miss else ""))
    # the readbacks: a dump as 53ee574 leaves it, and the running values that match it
    B2 = SimpleNamespace(P=[0, 1], F=["1", "2"], cages={})
    dump = ["r 4f38 00000003", "r 4f48 0000007e", "r 6048 00000003", "r 604c 00010002", "r 5310 000fffff", "r 4ee8 00000010",
            "r 52a0 00ff7c2a", "r 106c 0000000d", "r 4c18 00100010", "r 4c8c 00000001", "r 54e4 00000002",
            "r 5d60 000003e8", "r 5ef0 00000000", "r 5ef4 00000000"]
    vlans = ["001 0207ffff", "00a 0207ce0e"]
    l2 = ["118 00b5325d 600106eb 0001001e"]		# rtlsim's, 53ee574's management entry
    running = {"lag.1.members": "1-2", "lag.1.hash": "smac,dmac,sip,dip,sport,dport", "lag.1.mode": "static",
               "lag.2.members": "none", "lag.2.hash": "none", "lag.3.members": "none", "lag.3.hash": "none",
               "lag.4.members": "none", "lag.4.hash": "none", "mirror.enabled": "on", "mirror.port": "2",
               "mirror.rx": "1", "mirror.tx": "2", "stp.enabled": "off", "lldp.enabled": "on", "igmp.enabled": "on",
               "loop.enabled": "on", "vlan.1.members": "1-2", "vlan.1.tagged": "none", "vlan.10.members": "2",
               "vlan.10.tagged": "2", "sys.led": "on", "sys.mac": "06:eb:00:b5:32:5d", "mgmt.vlan": "1",
               "port.1.bw_in_kbps": "256", "port.1.bw_in_mode": "fc", "port.2.bw_in_kbps": "0", "port.2.bw_in_mode": "drop",
               "port.1.bw_out_kbps": "0", "port.2.bw_out_kbps": "0", "port.2.storm.bcast.rate": "1000",
               "port.2.storm.bcast.unit": "kbps", **{f"port.{n}.storm.{t}.rate": "0" for n in (1, 2) for t in translate.STORMS
                                                     if (n, t) != (2, "bcast")}}
    objs = {"lag", "mirror", "stp", "lldp", "igmp", "loop", "vlan", "sys", "mgmt", "port.design"}
    bad, n = translate.boot_defaults(dump, running, B2, objs, vlans, l2)
    got = sorted(p for p, _, _ in bad)
    want = ["vlan.10.tagged"]		# the running says 2 tagged; the word (the 8x's vlan 10) has it untagged
    ok = got == want and n > 30
    fails += not ok
    case("C3", ok, f"the readbacks of the P4 objects: trunk, hash, mirror, STP, LLDP, IGMP, RLDP, VLAN table, LEDs, the "
         f"management entry, bandwidth, storm meters: {n} compared, the differences {got}")
    return fails


# --- the state-path map (P3G): the old surfaces' samples, the conversions,
# and each row's two sides against each other

BOARD = SimpleNamespace(P=list(range(8)), F=[str(i + 1) for i in range(8)], cages={1: (8, "9")})


def surface(key):
    """(match, Read row) of a sample's key ("cli <line>", "web <path>")."""
    kind, line = key.split(" ", 1)
    for rx, w in translate.ROWS if kind == "cli" else translate.PAGES:
        m = rx.fullmatch(line)
        if m and isinstance(w, translate.Read):
            return m, w
    return None, None


def moved(w):
    """An image that has moved all of a row's objects and its owners: the
    surface gone, every value from get."""
    return (w.objs | w.owner) - {None}


def old_side(w, m, text, r=BOARD):
    v, rest = w.parse(text, m, r)
    return translate.canon({p: x for p, x in w.post(v, m, r).items() if w.comparable(p, [set(), moved(w)])}, rest)


def new_side(w, m, gets, r=BOARD):
    v, rest = w.new(gets, m, r, moved(w))
    return translate.canon({p: x for p, x in w.post(v, m, r).items() if w.comparable(p, [set(), moved(w)])}, rest)


def fine_us(s):
    """A microsecond count health tasks prints as s (print_fine())."""
    if s.startswith(">"):
        return ">400000"
    for u in range(0, 0xffff):
        t = f"{u * 6 + (u * 9 >> 6)}us" if u <= 7281 else f"{u // 163}ms"
        if t == s:
            return str(round(u * 6.144))
    return s


def as_get(v, key=""):
    """An old surface's canonical values as the new side's get would print
    them, the narrowed ones as the full value they narrow (the inverse of
    the rows' narrow())."""
    out = []
    for p, x in v.items():
        if x == "500m|unknown":
            x = "unknown"
        elif p.endswith("max_us"):
            x = fine_us(x)
        elif p.endswith(".bpdu_age_ms") and key == "cli stp status":	# seconds
            x = str(int(x) * 1000 + 999)
        elif p == "state.ntp.last_sync_s":
            x = str(int(x.split()[0]) * 60 + 59)
        elif p.endswith((".lp_adv", ".mask", ".actor", ".partner", ".hash", ".status", ".eee.lp", "eee_speeds")):
            x = x.replace(",", " ") if x != "none" else ""	# Flags as words
        elif p.endswith((".members", ".active", ".looped", ".blocked", ".isolate")) and x != "none":
            x = ",".join(x.split(","))			# a port list as om prints it: runs welcome
        out.append(f"{p} {x}")
    return "\n".join(out) + "\nok\n"


def state_paths(case):
    fails = 0

    def c(name, ok, detail):
        nonlocal fails
        fails += not ok
        case(name, ok, detail)

    samples = json.load(open(os.path.join(HERE, "om_samples.json")))
    left, n = [], 0
    for key, texts in sorted(samples.items()):
        m, w = surface(key)
        for x in texts:
            v, rest = w.parse(x, m, BOARD)
            n += 1
            if rest or not v or any(not translate.path_of(p) and not p.startswith("state.l2.") for p in v):
                left.append(f"{key}: {rest[:2]} {[p for p in v if not translate.path_of(p)][:2]}")
    rows = {rx.pattern for rx, w in translate.ROWS + translate.PAGES if isinstance(w, translate.Read)}
    have = {surface(k)[1] for k in samples}
    c("P1", not left and len(have) == len(rows), f"{n} samples of {len(have)} of {len(rows)} surfaces: every line and "
      f"key placed on a path{'; ' + '; '.join(left[:3]) if left else ''}")

    # each row's two sides: the old text, and the same values as get prints them
    bad = []
    for key, texts in sorted(samples.items()):
        m, w = surface(key)
        if "l2" in key:
            continue			# keyed differently: below
        for x in texts:
            v, _ = w.parse(x, m, BOARD)
            o, nw = old_side(w, m, x), new_side(w, m, as_get(v, key))
            if o != nw:
                d = [l for l in difflib.unified_diff(o.splitlines(), nw.splitlines(), lineterm="", n=0)][2:6]
                bad.append(f"{key}: {d}")
    c("P2", not bad, "each row's old text and its values from get compare equal" + ("; " + "; ".join(bad[:3]) if bad else ""))

    # the conversions
    m, w = surface("web /traffic.json")
    v, _ = w.parse('HTTP/1.1 200 OK\n\n[{"port":1,"link":"500M","txG":"0x100000005","txB":"0x1","rxG":"0x0","rxB":"0x0",'
                   '"macs":"0x10003"},{"port":2,"link":"","txG":"0x0","txB":"0x0","rxG":"0x0","rxB":"0x0","macs":"0x0"}]', m, BOARD)
    c("P3", v["state.port.1.speed"] == "500m" and v["state.port.2.speed"] == "unknown" and
      v["state.port.1.tx_good"] == "4294967301" and v["state.port.1.macs"] == "3",
      f"traffic.json: speed codes, the full 64-bit count, the MACs masked to 13 bits ({v['state.port.1.tx_good']})")
    m, w = surface("cli stat")
    o = old_side(w, m, "stat\n1\tOn\tUp\t0x00000005\t0x0\t0x0\t0x0\t0\n> ")
    nw = new_side(w, m, "state.port.1.link up\nstate.port.1.speed 500m\nstate.port.1.tx_good 4294967301\n"
                  "state.port.1.tx_bad 0\nstate.port.1.rx_good 0\nstate.port.1.rx_bad 0\nstate.port.1.macs 0\n"
                  "port.1.enabled on\nok")
    c("P4", o == nw and "500m|unknown" in o and "tx_good 5" in o, "stat: \"Up\" is 500m or unknown, a counter its low 32 bits")
    m, w = surface("cli stp status")
    o = old_side(w, m, "stp status\nSTP on, RSTP\nbridge  8000/06eb00b5325d\nroot    8000/06eb00b5325d (this switch)\n"
                 "changes 0x0001\nport state role edge tx age[s]\n 01  fwd   desg yes  2c 40\n L1  block altn no   ff 255\n> ")
    nw = new_side(w, m, "stp.enabled on\nstp.version rstp\nstate.stp.bridge 8000.06:eb:00:b5:32:5d\n"
                  "state.stp.root 8000/06eb00b5325d\nstate.stp.is_root on\nstate.stp.root_port none\nstate.stp.root_cost 0\n"
                  "state.stp.tc_count 1\nstate.stp.port.1.state fwd\nstate.stp.port.1.role designated\nstate.stp.port.1.edge on\n"
                  "state.stp.port.1.tx_bpdu 300\nstate.stp.port.1.bpdu_age_ms 40980\nstate.stp.lag.1.state block\n"
                  "state.stp.lag.1.role alternate\nstate.stp.lag.1.edge off\nstate.stp.lag.1.tx_bpdu 511\n"
                  "state.stp.lag.1.bpdu_age_ms 600000\nok")
    c("P5", o == nw, "stp status: tx its low byte, age whole seconds capped at 255, a LAG entity, the bridge ID")
    m, w = surface("cli sfp")
    v, _ = w.parse("sfp\n\nSlot 1 - Rate: 67  Encoding: 06\nRTLSIM          SFP-10G-SR      1.0 \nOptions: 68\nTemp: 2380\n"
                   "Vcc: 80e8\nTX Bias: 0bb8\nTX Power: 178a\nRX Power: 0f8d\nLaser: 0000\nState: 06\n> ", m, BOARD)
    want = {"state.sfp.1.rate_mbps": "10300", "state.sfp.1.ddm.temp_c": "35.5", "state.sfp.1.ddm.vcc_v": "3.30",
            "state.sfp.1.ddm.tx_bias_ma": "6.0", "state.sfp.1.ddm.tx_power_dbm": "-2.2", "state.sfp.1.ddm.rx_power_dbm": "-4.0",
            "state.sfp.1.ddm.status": "rx_los,tx_fault", "state.sfp.1.part": "SFP-10G-SR", "state.sfp.1.rev": "1.0"}
    c("P6", all(v.get(k) == x for k, x in want.items()), "sfp: the DDM words in C, V, mA and dBm, the status bits, "
      f"the ID split at its widths ({ {k[12:]: v.get(k) for k in want if v.get(k) != want[k]} or 'all as wanted'})")
    m, w = surface("web /ports.json")
    v, _ = w.parse('HTTP/1.1 200 OK\n\n[{"port":9,"name":"","isSFP":true,"enabled":true,"sfp_options":"0x58",'
                   '"sfp_temp":"0x2380","sfp_vcc":"0x80e8","sfp_txbias":"0x0bb8","sfp_txpower":"0x178a","sfp_rxpower":"0x0f8d",'
                   '"sfp_temp_cal":"0x02000100","sfp_vcc_cal":"0x01000000","sfp_txbias_cal":"0x01000000",'
                   '"sfp_txpower_cal":"0x01000000","sfp_rxpower_cal":"0x' + "00" * 20 + '","sfp_state":"0x00",'
                   '"sfp_vendor":"V","sfp_model":"M","sfp_serial":"S","sfp_los":null,"mtu":"0x3fff"}]', m, BOARD)
    c("P7", v.get("state.sfp.1.ddm.temp_c") == "72.0" and v.get("state.sfp.1.los") == "none" and
      v.get("state.sfp.1.ddm.rx_cal") == "on" and len(v.get("state.sfp.1.ddm.rx_cal_coef", "")) == 40 and
      v.get("port.9.mtu") == "16383",
      f"ports.json: external calibration applied (slope 2, offset 1/256: {v.get('state.sfp.1.ddm.temp_c')} C), "
      "the RX coefficients kept, no LOS pin, the MTU in hex")
    m, w = surface("cli lacp show")
    o = old_side(w, m, "lacp show\nLACP on\nlag 01 ports 0x000c aggregator 02aa00000001 members 0x0004\n"
                 "port 03 actor 3f partner 3d rxst 04 rx 0x000c psys 02aa00000001\n> ")
    nw = new_side(w, m, "state.lacp.running on\nlag.1.mode lacp\nlag.1.members 3-4\nlag.2.mode off\nlag.2.members none\n"
                  "state.lag.1.active 3\nstate.lag.1.agg_valid on\nstate.lag.1.aggregator 02:aa:00:00:00:01\n"
                  "state.lag.2.active none\nstate.lag.2.agg_valid off\nstate.lacp.port.3.actor activity timeout aggregation "
                  "sync collecting distributing\nstate.lacp.port.3.partner activity aggregation sync collecting distributing\n"
                  "state.lacp.port.3.rx_state current\nstate.lacp.port.3.rx_pdus 12\nstate.lacp.port.3.partner_sys 02aa00000001\nok")
    c("P8", o == nw and "lag.1.members 3,4" in o and "lag.2" not in o, "lacp show: logical masks as fronts, flags from "
      "the state bytes, only the LACP LAGs (the new side's modes)" + ("" if o == nw else f": {o!r} vs {nw!r}"))
    m, w = surface("web /stp.json")
    v, _ = w.parse('HTTP/1.1 200 OK\n\n{"ports":[{"p":101,"st":3,"role":1,"f":71,"pc":"0x00004e20","prio":128,"p2":2,'
                   '"mbr":[3,4],"lk":1,"lg":0,"db":"800006eb00b5325d","dp":"8065","dc":"0x00000000"}]}', m, BOARD)
    c("P9", v.get("stp.lag.1.edge") == "on" and v.get("state.stp.lag.1.edge") == "on" and v.get("stp.lag.1.enabled") == "on"
      and v.get("stp.lag.1.p2p") == "off" and v.get("stp.lag.1.cost") == "20000" and v.get("state.stp.lag.1.members") == "3,4"
      and v.get("state.stp.lag.1.dport") == "32869", "stp.json: entity 101 is lag.1; f's config and runtime bits apart; "
      "p2p, cost, members, designated port")
    m, w = surface("cli time")
    o = old_side(w, m, "time\nTime 2026-11-20 13:00:07 UTC+01:00 DST, synchronised 5 min ago, stratum 2\n> ")
    nw = new_side(w, m, "state.ntp.synced on\nstate.ntp.time 2026-11-20T12:00:07Z\nstate.ntp.local_offset_min 60\n"
                  "state.ntp.dst_active on\nstate.ntp.last_sync_s 330\nstate.ntp.stratum 2\nok")
    c("P10", o == nw and "state.ntp.time 2026-11-20 12:00:07" in o, "time: the local time as UTC, the minutes since the sync")
    m, w = surface("web /l2.json?idx=0")
    o = old_side(w, m, 'HTTP/1.1 200 OK\n\n[{"vlan":"001","mac":"06:eb:00:b5:32:5d","type":"s","port":0,"lag":0,"idx":"0118"},'
                 '{"vlan":"00a","mac":"02:00:00:00:00:01","type":"l","port":1,"lag":2,"idx":"0200"},'
                 '{"vlan":"001","mac":"06:eb:00:b5:32:5d","type":"s","port":0,"lag":0,"idx":"0118"}]')
    nw = new_side(w, m, "state.l2.280.mac 06eb00b5325d\nstate.l2.280.vlan 1\nstate.l2.280.port cpu\nstate.l2.280.lag 0\n"
                  "state.l2.280.static on\nstate.l2.512.mac 02:00:00:00:00:01\nstate.l2.512.vlan 10\nstate.l2.512.port 1\n"
                  "state.l2.512.lag 2\nstate.l2.512.static off\nok")
    m2, _ = surface("web /l2.json?idx=300")
    nw2 = new_side(w, m2, "state.l2.280.mac 06eb00b5325d\nstate.l2.280.vlan 1\nstate.l2.280.port cpu\nstate.l2.280.static on\n"
                   "state.l2.512.mac 02:00:00:00:00:01\nstate.l2.512.vlan 10\nstate.l2.512.port 1\nstate.l2.512.static off\nok")
    c("P11", o == nw and o.count("\n") == 2 and nw2.startswith("state.l2.280 ") and "state.l2.512 " in nw2,
      "l2.json: keyed by index (hex), the first again at the wrap; the new side's table as the page from idx shows it")
    m, w = surface("cli l2")
    o = old_side(w, m, "l2\n\n\tMAC\t\tVLAN\ttype\tport\n06:eb:00:b5:32:5d\t0x0001\tstatic\tCPU\n02:00:00:00:00:01\t0x000a\t"
                 "learned\tLAG2\n> ")
    nw = new_side(w, m, "state.l2.512.mac 02:00:00:00:00:01\nstate.l2.512.vlan 10\nstate.l2.512.port 1\nstate.l2.512.lag 2\n"
                  "state.l2.512.static off\nstate.l2.280.mac 06:eb:00:b5:32:5d\nstate.l2.280.vlan 1\nstate.l2.280.port cpu\n"
                  "state.l2.280.lag 0\nstate.l2.280.static on\nok")
    c("P12", o == nw, f"l2: no index, keyed by MAC and VLAN; a LAG entry by its LAG ({o!r})")
    c("P13", translate.n_pmask("1-3,9,cpu") == "1,2,3,9,cpu" and translate.n_pmask("none") == "none" and
      translate.n_bridge("8000.06eb00b5325d") == "8000/06eb00b5325d" and translate.n_flags("") == "none",
      "the new side's port lists, bridge IDs and flags")
    m, w = surface("cli health tasks")
    o = old_side(w, m, "health tasks\nslot\ttask\tstate\tmask\twake\n0\trx\tRX_STEP\t0x0081\t+32766\n1\tsfp1\tfree\n"
                 "state\truns\tmax\nRX_STEP\t11\t30us\nCLI_RUN\t2\t48ms\nFL_ERASE\t1\t>400ms\n"
                 "passes 0x00000039 idle 0x00000000 longest 2167us\noverruns 0, con rx overruns 0, hw timeouts 1 last 0x5cac\n"
                 "last reset: watchdog in stp/STP_TICK after 5000 ms, PC 0x1234 bank 2, states A B C D E F G H\n> ")
    c("P14", "state.sched.slot.rx.mask forever,rx" in o and "state.health.hw_timeout_reg 23724" in o and
      "state.sys.reset.cause watchdog" in o and "state.sys.reset.pc 4660" in o and "max_us 48ms" in o,
      "health tasks: the mask's bits, the hex counts, the reset record")
    old = "\n<new link: 0400020000, was 0000020000>\n\n<MODULE INSERTED>  Slot: 1\n<SFP-RX OK>  Slot: 1\n  Rate: 67\n"
    new = "\n<MODULE INSERTED>  Slot: 1\n<SFP-RX OK>  Slot: 1\n\n<new link: 0400020000, was 0000020000>\n  Rate: 67\n"
    lone = "<MODULE REMOVED>  Slot: 1\n<SFP-RX LOS>  Slot: 1\n"
    links = "\n<new link: 0000020000, was 0000000001>\n\n<new link: 0000020001, was 0000020000>\n"
    c("P15", _link_first(old) == old and _link_first(new) == old and _link_first(lone) == lone and _link_first(links) == links,
      "console: an SFP event and its link change in either order read as one, link first; nothing else moves")
    return fails


if __name__ == "__main__":
    main()
