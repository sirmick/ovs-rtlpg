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
from oldnew import Run, compare, load_expect
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
        return "", f"{line}\n{self.replies.get(line, 'ok')}\n> "


def run(side_om, line, replies, board=None):
    r = object.__new__(Run)
    r.b, r.t, r.n, r.results, r.replies = Console(replies), 0, 0, {}, {}
    r.om, r.oms, r.canon = set(side_om), [set(), {"port"}], {"port"}
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
    t, res = run({"port"}, "vlan show", {"vlan show": "table"})
    case("J", t == ["vlan show"] and res == "vlan show\ntable\n> ", "a line with no row goes as it is")
    t, res = run({"port"}, "ingress t", {})
    case("K", t[:3] == ["set port.1.ingress tagged", "set port.2.ingress tagged", "set port.3.ingress tagged"],
         f"ingress for every port, the cage's too ({t[:3]})")

    r = object.__new__(Run)
    r.canon = {"port"}
    try:
        Run.post_cmd(r, "pvid 2 10")
        ok = False
    except NotImplementedError:
        ok = True
    case("L", ok, "POST /cmd of a translated object raises (no web translation yet)")

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
    fails += real_gets(case)
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
            ("Q7", "port 4 on", "port.4.enabled on\nport.4.speed auto", {**REAL, "get port.4.speed": "port.4.speed auto\nok"}),
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
    return fails


if __name__ == "__main__":
    main()
