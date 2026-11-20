#!/usr/bin/env python3
"""test_translate.py - the old commands' translation for an object-model
image (translate.py, oldnew.Run.cli()) and the expect file's line items
(oldnew.compare()), no rtlsim:

    python3 tests/oldnew/test_translate.py

A fake console answers each line from a table; the cases check what each
side types and what its result is. Exit 0 when every case passes.
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from oldnew import Run, compare, load_expect

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


def run(side_om, line, replies):
    r = object.__new__(Run)
    r.b, r.t, r.n, r.results, r.replies = Console(replies), 0, 0, {}, {}
    r.om, r.canon, r.F, r.cages = set(side_om), {"port"}, ["1", "2"], {1: (8, "3")}
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
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
