"""translate.py - the scenarios' old console commands, for an image whose
objects have moved to the object model (RTLPlayground feat/om, DESIGN.md
18): what to type instead, and both sides' replies as values by path, so
an old image and a new one are compared by value.

A row is an object (the om object the old command's state lives in, as
`--new-om` names them), a regex the old line must match in full, and what
it means:

- Set(values): a setting. values(m, r) is {path: value}, in the new
  syntax. The old image is given the old line; its result is the values,
  or "refused" if it said "Error:". The new one is given `set` for each,
  `apply`, then `get` for each: its result is what get printed, or
  "refused" if any line said err.
- Get(news, parse, new): a read. The old image is given the old line, and
  parse(reply, m, r) is its {path: value}; the new one is given news(m, r),
  get lines, and new(text) is its {path: value} from what they printed
  (default: every "path value" line). Each side's result is its values'
  "path value" lines: the two must hold the same paths.
- Refused(): a line the old image refuses (a usage error): the new one
  isn't given anything; both results are "refused" (the old one only if it
  did refuse).

A row applies when its object is in the set the run compares by value
(Run.canon: the objects either image has moved); the line is translated
only on an image that has moved that object (Run.om). Anything else is
typed as it is and compared as text.

Each object migration adds its rows here (and its intended differences to
om.expect).
"""
import re


class Set:
    def __init__(self, values):
        self.values = values


class Get:
    def __init__(self, news, parse, new=None):
        self.news, self.parse, self.new = news, parse, new or values


class Refused:
    pass


ROWS = []


def row(obj, rx, what):
    ROWS.append((obj, re.compile(rx), what))


def find(line, canon):
    """(row's object, match, what) for an old line, or None."""
    for obj, rx, what in ROWS:
        m = rx.fullmatch(line)
        if m and obj in canon:
            return obj, m, what
    return None


def refused(text):
    return text is None or any(l.startswith(("Error:", "err ")) for l in text.splitlines())


def values(text):
    """A get's "path value" lines, {path: value}; the value may be empty."""
    out = {}
    for l in (text or "").splitlines():
        m = re.fullmatch(r"((?:state\.)?[a-z_]+\.[\w.*]+) ?(.*)", l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _key(p):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p)]


def canon(v):
    return "\n".join(f"{p} {v[p]}" for p in sorted(v, key=_key))


# --- port.N (feat/om dab5d13: port, mtu, pvid, ingress, isolate, eee, stat)

SPEED = {"10M": "10m", "100M": "100m", "1000M": "1g", "10G": "10g", "2.5G": "2g5", "5G": "5g"}
INGRESS = {"u": "untagged", "t": "tagged", "a": "all"}


def _ports(r):
    return [int(f) for f in r.F] + [int(c[1]) for c in r.cages.values()]


def _stat(reply, m, r):
    """stat's table: per port, its state (copper: the PHY on or off, i.e.
    enabled; an SFP cage's module present has no path yet), link and
    speed, the counters' low 32 bits, the MACs learned."""
    v = {}
    for l in (reply or "").splitlines():
        f = l.split("\t")
        if len(f) < 8 or not f[0].isdigit():
            continue
        f = [x for x in f if x]
        n = f[0]
        if f[1] in ("On", "Off"):
            v[f"port.{n}.enabled"] = "on" if f[1] == "On" else "off"
        s = f"state.port.{n}."
        v[s + "link"] = "down" if f[2] == "Down" else "up"
        if f[2] != "Down":
            v[s + "speed"] = SPEED.get(f[2], "unknown")
        for k, x in zip(("tx_good", "tx_bad", "rx_good", "rx_bad"), f[3:7]):
            v[s + k] = str(int(x, 16))
        v[s + "macs"] = f[7]
    return v


STAT = ("link", "speed", "tx_good", "tx_bad", "rx_good", "rx_bad", "macs")


def _stat_new(text):
    """The same from get: those fields, the speed only with a link, the
    counters' low 32 bits."""
    v, out = values(text), {}
    for p, x in v.items():
        m = re.fullmatch(r"(state\.)?port\.(\d+)\.(\w+)", p)
        if not m:
            continue
        st, n, f = m.groups()
        if not st and f == "enabled":
            out[p] = x
        elif st and f in STAT and not (f == "speed" and v.get(f"state.port.{n}.link") != "up"):
            out[p] = str(int(x) & 0xffffffff) if f.endswith(("_good", "_bad")) else x
    return out


def _mtu_show(reply, m, r):
    return {f"port.{a}.mtu": b for a, b in re.findall(r"^Port (\d+) (\d+)$", reply or "", re.M)}


row("port", r"stat", Get(lambda m, r: ["get state.port", "get port.*.enabled"], _stat, _stat_new))
row("port", r"mtu show", Get(lambda m, r: ["get port.*.mtu"], _mtu_show))
row("port", r"pvid (\d+) (\d+)", Set(lambda m, r: {f"port.{m[1]}.pvid": m[2]}))
row("port", r"mtu (\d+) (\d+)", Set(lambda m, r: {f"port.{m[1]}.mtu": m[2]}))
row("port", r"port (\d+) name (\S.*)", Set(lambda m, r: {f"port.{m[1]}.name": m[2][:31]}))
row("port", r"port (\d+) lldp (block|permit)", Set(lambda m, r: {f"port.{m[1]}.lldp": m[2]}))
row("port", r"port (\d+) (on|off)", Set(lambda m, r: {f"port.{m[1]}.enabled": m[2]}))
row("port", r"port (\d+) (auto|10m|100m|1g|2g5|5g|10g)(?: (half|full))?",
    Set(lambda m, r: {f"port.{m[1]}.speed": m[2], **({f"port.{m[1]}.duplex": m[3]} if m[3] else {})}))
row("port", r"port (\d+) duplex (full|half)", Set(lambda m, r: {f"port.{m[1]}.duplex": m[2]}))
row("port", r"isolate (\d+) off", Set(lambda m, r: {f"port.{m[1]}.isolate": "all,cpu"}))
row("port", r"isolate (\d+)((?: (?:\d+|cpu))+)",
    Set(lambda m, r: {f"port.{m[1]}.isolate": ",".join(m[2].split())}))
row("port", r"ingress ([uta])", Set(lambda m, r: {f"port.{p}.ingress": INGRESS[m[1]] for p in _ports(r)}))
row("port", r"ingress((?: \d+[uta])+)",
    Set(lambda m, r: {f"port.{w[:-1]}.ingress": INGRESS[w[-1]] for w in m[1].split()}))
row("port", r"eee (on|off)", Set(lambda m, r: {f"port.{f}.eee": m[1] for f in r.F}))	# copper only
row("port", r"eee (on|off) (\d+)", Set(lambda m, r: {f"port.{m[2]}.eee": m[1]}))
row("port", r"port|port \S+|pvid|pvid \S+|mtu|isolate|isolate \S+|ingress|eee", Refused())
