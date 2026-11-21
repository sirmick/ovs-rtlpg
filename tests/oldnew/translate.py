"""translate.py - the scenarios' old console commands and web pages, for an
image whose objects have moved to the object model (RTLPlayground feat/om,
DESIGN.md 18): what to type instead, and both sides' results as values by
path, so an old image and a new one are compared by value.

Paths and objects. PATHS lists every path a row can produce (DESIGN.md 6
and 7; research/state.md for the surfaces), each with its object, the name
`--new-om` takes, and how a `get` of it prints (norm(): the new side's text
made canonical: decimal numbers, on/off, port lists as "3,4" or "none",
flags as sorted words or "none", MACs as aa:bb:.., bridge IDs as
pppp/mmmmmmmmmmmm). An object in PENDING has its rows written but its
paths not yet on the switch: naming it is refused until the node that moves
it takes it out (and corrects what its schema named otherwise).

A row is a regex an old console line (or an old GET path) must match in
full, and what it means:

- Set(obj, values): a setting. values(m, r) is {path: value}. The old image
  is given the old line; its result is the values, or "refused" if it said
  "Error:". The new one is given `set` for each, `apply`, then `get` for
  each: its result is what get printed, or "refused" if any line said err.
- Read(paths, parse, ...): a printer or a JSON page. paths are templates
  of the paths it shows ("#" a number, "*" a segment, "**" the rest), and
  so its objects. On a side that hasn't moved all of them, the old line is
  typed (the page fetched), and parse(text, m, r) gives its
  ({path: value}, [lines it couldn't place]); a side that has moved some
  gets those from `get` (gets(m, r, objs) the lines, by default `get <obj>`
  per object; the values kept are the row's paths, each normalized, then
  narrow(path, value) to what the old surface shows: a truncation, a unit).
  post(values, m, r) then evens out the structure on both sides (what the
  old surface leaves out). The result is the "path value" lines, then the
  lines no path took, as "~ line", so nothing on either surface goes
  unseen.
- Refused(obj): a line the old image refuses (a usage error): the new one
  isn't given anything; both results are "refused" (the old one only if it
  did refuse).

A row applies when one of its objects is in the set the run compares by
value (Run.canon: the objects either image has moved); anything else is
typed as it is and compared as text. Each object migration adds or flips
its rows here (and its intended differences to om.expect).
"""
import json, math, re


# --- canonical values

def onoff(b):
    return "on" if b else "off"


def plist(ports, cpu=False):
    """A port list as the canon has it: "3,4", "3,cpu", "none"."""
    w = [str(p) for p in sorted(set(ports))] + (["cpu"] if cpu else [])
    return ",".join(w) or "none"


def flags(words):
    return ",".join(sorted(set(words))) or "none"


def mac(s):
    h = re.sub(r"[^0-9a-f]", "", s.lower())
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else s


def bridge(prio, m):
    return f"{prio.lower():0>4}/{re.sub(r'[^0-9a-f]', '', m.lower())}"


def num(s):
    try:
        return str(int(s, 0) if isinstance(s, str) else int(s))
    except ValueError:
        return s


def bits(v, names):
    """Flags from a bit field: names[i] is bit i's word."""
    return flags(n for i, n in enumerate(names) if n and v >> i & 1)


# --- the new side's text, made canonical

def n_pmask(s):
    """om's PortMask text ("1-3,9,cpu", "none", "all") as a port list."""
    ports, cpu = [], False
    for w in s.split(","):
        if w == "cpu":
            cpu = True
        elif "-" in w:
            a, b = w.split("-")
            ports += range(int(a), int(b) + 1)
        elif w.isdigit():
            ports.append(int(w))
    return plist(ports, cpu)


def n_flags(s):
    return flags(w for w in re.split(r"[ ,]+", s) if w and w != "none")


def n_bridge(s):
    m = re.fullmatch(r"(\d+)\.(\d+)\.((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", s.strip())    # get's: prio/4096.ext.mac
    if m:
        return bridge(f"{int(m[1]) * 4096 + int(m[2]):04x}", m[3].replace(":", ""))
    m = re.fullmatch(r"(?:0x)?([0-9a-fA-F]{1,4})[/.:-]?((?:[0-9a-fA-F]{2}[:-]?){6})", s.strip())
    return bridge(m[1], m[2]) if m else s


def n_dec(places):
    def f(s):
        try:
            return f"{float(s):.{places}f}"
        except ValueError:
            return s
    return f


def n_bool(s):
    return {"true": "on", "yes": "on", "1": "on", "false": "off", "no": "off", "0": "off"}.get(s, s)


def ident(s):
    return s


# --- the paths: (template, object, norm)

PORT_CFG = "name enabled speed duplex mtu pvid ingress ingress_filter lldp eee isolate".split()
PORT_ST = "link speed duplex media ms_role pause_tx pause_rx macs tx_good rx_good tx_bad rx_bad eee.active".split()
PATHS = [
    # port.N (DESIGN 6.5, 7.5; P4-6 the bandwidth, storm and SFP speed, and 7.5's names)
    (r"port.#.(mtu|pvid)", "port", num), (r"port.#.isolate", "port", n_pmask),
    (r"port.#.(%s)" % "|".join(PORT_CFG), "port", ident),
    (r"state.port.#.(macs|tx_good|rx_good|tx_bad|rx_bad)", "port", num),
    (r"state.port.#.(%s)" % "|".join(PORT_ST), "port", ident),
    (r"state.port.#.cabletest.state", "port", ident), (r"state.port.#.cabletest.pair.[abcd].result", "port", ident),
    (r"state.port.#.cabletest.pair.[abcd].length_m", "port", n_dec(1)),
    (r"state.port.#.(eee.lp|lp_adv)", "port", n_flags),
    (r"state.port.#.lag", "port", num), (r"port.#.eee_speeds", "port", n_flags),
    (r"port.#.(bw_in_kbps|bw_out_kbps)", "port", num), (r"port.#.bw_in_mode", "port", ident),
    (r"port.#.storm.(bcast|mcast|ucast|umcast).rate", "port", num),
    (r"port.#.storm.(bcast|mcast|ucast|umcast).unit", "port", ident),
    (r"state.port.#.mib.*", "state.mib", num),
    # config (DESIGN 6)
    (r"sys.mac", "sys", mac), (r"sys.(hostname|led)", "sys", ident),
    (r"web.session_timeout_s", "web", num), (r"mgmt.vlan", "mgmt", num), (r"mgmt.*", "mgmt", ident),
    (r"web.password", "web", lambda x: "*"),	# a Secret: get prints *
    (r"syslog.port", "syslog", num), (r"syslog.*", "syslog", ident), (r"dns.*", "dns", ident),
    (r"ntp.interval_min", "ntp", num), (r"ntp.*", "ntp", ident),
    (r"lag.#.members", "lag", n_pmask), (r"lag.#.hash", "lag", n_flags), (r"lag.#.mode", "lag", ident),
    (r"stp.(priority|hello_s|maxage_s|fwd_delay_s|txhold)", "stp", num),
    (r"stp.(port|lag).#.(cost|priority)", "stp", num), (r"stp.**", "stp", ident),
    (r"loop.enabled", "loop", ident), (r"lldp.enabled", "lldp", ident), (r"igmp.enabled", "igmp", ident),
    (r"vlan.#.(members|tagged)", "vlan", n_pmask), (r"vlan.#.name", "vlan", ident), (r"vlan.#", "vlan", ident),
    (r"mirror.(rx|tx)", "mirror", n_pmask), (r"mirror.(enabled|port)", "mirror", ident),
    # state (DESIGN 7)
    (r"state.sys.mac", "state.sys", mac), (r"state.sys.(uptime_s|image_size|flash.size)", "state.sys", num),
    (r"state.sys.(temp_c|temp_poweron_c)", "state.sys", n_dec(1)),
    (r"state.sys.reset.(step_ms|pc|bank)", "state.sys", num), (r"state.sys.**", "state.sys", ident),
    (r"state.sched.slot.*.mask", "state.sched", n_flags), (r"state.sched.slot.*.*", "state.sched", ident),
    (r"state.sched.state.*.runs", "state.sched", num), (r"state.sched.state.*.max_us", "state.sched", ident),
    (r"state.sched.(passes|idle|overruns)", "state.sched", num), (r"state.sched.**", "state.sched", ident),
    (r"state.health.**", "state.sched", num), (r"state.con.**", "state.sched", num),
    (r"state.dbg.igmp.port.#.cfg", "state.dbg", ident), (r"state.dbg.*", "state.dbg", ident),	# P7, raw words
    (r"state.mgmt.dhcp.renew_s", "state.mgmt", num), (r"state.mgmt.**", "state.mgmt", ident),
    (r"state.ntp.(local_offset_min|last_sync_s|stratum)", "state.ntp", num), (r"state.ntp.**", "state.ntp", ident),
    (r"state.dns.**", "state.dns", ident),
    (r"state.loop.(looped|blocked)", "state.loop", n_pmask), (r"state.loop.**", "state.loop", ident),
    (r"state.loader.**", "state.loader", n_bool),
    (r"state.sfp.#.(encoding|rate_mbps|options|quirks|ddm.laser)", "state.sfp", num),
    (r"state.sfp.#.ddm.status", "state.sfp", n_flags), (r"state.sfp.#.ddm.(temp|tx_bias)", "state.sfp", num),
    (r"state.sfp.#.ddm.(vcc_v|tx_power_mw|rx_power_mw)", "state.sfp", n_dec(4)),
    (r"state.sfp.#.ddm.cal", "state.sfp", lambda x: x[:72]),	# the old pages had 184..219
    (r"state.sfp.#.(vendor|part|rev|serial)", "state.sfp", str.strip),
    (r"state.sfp.**", "state.sfp", ident),
    (r"state.stp.(bridge|root)", "state.stp", n_bridge), (r"state.stp.(port|lag).#.dbridge", "state.stp", n_bridge),
    (r"state.stp.(port|lag).#.members", "state.stp", n_pmask),
    (r"state.stp.(port|lag).#.(state|role|edge|guard_tripped|link|legacy|loop_held)", "state.stp", ident),
    (r"state.stp.(is_root|root_port)", "state.stp", ident), (r"state.stp.**", "state.stp", num),
    (r"state.lag.#.active", "state.lag", n_pmask), (r"state.lag.#.aggregator", "state.lag", mac),
    (r"state.lag.#.agg_valid", "state.lag", ident), (r"state.lacp.running", "state.lag", ident),
    (r"state.lacp.port.#.(actor|partner)", "state.lag", n_flags), (r"state.lacp.port.#.partner_sys", "state.lag", mac),
    (r"state.lacp.port.#.rx_state", "state.lag", ident), (r"state.lacp.port.#.*", "state.lag", num),
    (r"state.l2.#.mac", "state.l2", mac), (r"state.l2.#.(vlan|lag)", "state.l2", num), (r"state.l2.#.*", "state.l2", ident),
    (r"state.l2.*", "state.l2", ident),	# an entry as the old shows it: by index, or MAC/VLAN
    # a value with no path in the design: compared from its surface while that lasts
    (r"raw.**", None, ident),
]
# Objects whose rows wait for their node; each takes itself out (P4-*, P5-*)
# sys.mac: P4-1 left it (the MAC type's cost); the mac command stays until it moves
PENDING = {"state.loader"}
# Fields a moved state object defers (its schema lists them): the new image
# has no path for them, so while the surface lasts both images read them there
DEFERRED = [_r for _r in (
    r"state\.stp\.msg_age_s", r"state\.stp\.(port|lag)\.\d+\.loop_held",	# P7: the rest of /stp.json moved
    r"state\.lacp\.running", r"state\.lacp\.port\.\d+\.partner_(key|port|sys_prio|port_prio)")]


def deferred(p):
    return any(re.fullmatch(x, p) for x in DEFERRED)


# What `get` takes for an object, where that isn't its own name
GETS = {"port": ["port", "state.port"], "state.sched":
        ["state.sched", "state.health", "state.con"], "state.lag": ["state.lag", "state.lacp"],
        "state.mib": ["state.port"]}


def _rx(t):
    return re.compile(t.replace(".", r"\.").replace("#", r"\d+").replace(r"\.**", r"\..+")
                      .replace("**", ".+").replace("*", r"[^.]+"))


PATHS = [(_rx(t), o, n) for t, o, n in PATHS]


def path_of(p):
    """(object, norm) of a path, or None if no template has it."""
    for rx, o, n in PATHS:
        if rx.fullmatch(p):
            return o, n
    return None


def obj_of(p):
    t = path_of(p)
    return t[0] if t else None


def values(text):
    """A get's "path value" lines, {path: value}; the value may be empty."""
    out = {}
    for l in (text or "").splitlines():
        m = re.fullmatch(r"((?:state\.)?[a-z_]+\.[\w.*-]+) ?(.*)", l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _key(p):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p)]


def canon(v, rest=()):
    return "\n".join([f"{p} {v[p]}" for p in sorted(v, key=_key)] + [f"~ {l}" for l in rest])


# What the old CLI says, not through cmd_error(), when it does nothing
REFUSALS = ("is SFP no PHY information available", "Unknown port <port> lldp", "Must be 1-65535 s", "usage: storm")


def refused(text):
    return text is None or any(l.startswith(("Error:", "err ")) or any(x in l for x in REFUSALS)
                               for l in text.splitlines())


def normalized(v):
    """Values by path as the canon has them (each path's norm())."""
    return {p: path_of(p)[1](x) if path_of(p) and not x.startswith("<") else x for p, x in v.items()}


# --- rows

DEL = None	# a Set value: the keyed entry goes (del <path>)


class Set:
    """A setting. values(m, r, prev) is {path: value}, a value DEL for an
    entry deleted; prev is {path: value} of the before(m, r) paths as get
    printed them on the new image (None on the old). then(m, r, prev) are
    the lines typed after apply: the old command's side effects that are
    actions now (do ...). status: the values depend on prev, which the old
    image can't print, so the result is only accepted or refused (the
    effect is compared in the registers and tables). accept_error: the old
    command sets the values even though it says Error (dns server a <bad>)."""
    def __init__(self, obj, values, before=None, then=None, status=False, accept_error=False):
        self.objs, self.values, self.before, self.then = {obj}, values, before, then
        self.status, self.accept_error = status, accept_error

    def vals(self, m, r, prev=None):
        if self.values.__code__.co_argcount == 3:
            return self.values(m, r, prev)
        return self.values(m, r)


class Refused:
    def __init__(self, obj):
        self.objs = {obj}


def _probe(t):
    """A path a template matches: its first alternative of each group."""
    t = re.sub(r"\(([^|)]*)[^)]*\)", r"\1", t)
    t = re.sub(r"\[(.)[^]]*\]", r"\1", t)
    return t.replace("**", "x").replace("#", "1").replace("*", "x")


class Read:
    def __init__(self, owner, paths, parse, gets=None, narrow=None, post=None):
        self.owner = set(owner.split())
        self.templates = paths
        self.paths = [_rx(t) for t in paths]
        self.objs = {obj_of(_probe(t)) for t in paths}
        assert all(path_of(_probe(t)) for t in paths), [t for t in paths if not path_of(_probe(t))]
        self.parse, self._gets, self.narrow, self._post = parse, gets, narrow, post

    def gone(self, om):
        """Whether the surface is gone on an image that has moved om: its
        owners (the nodes that delete it) all moved."""
        return self.owner <= set(om)

    def not_compared(self, oms):
        """The row's paths neither image compares, and why: their object
        pending (or a path with no design object) while one image's
        surface is gone, as "~" lines both images print the same."""
        out = []
        for t in self.templates:
            o = obj_of(_probe(t))
            if not all((o is not None and o in om) or not self.gone(om) for om in oms):
                out.append(f"not compared: {t} ({o + ' pending' if o in PENDING else o + ' not moved' if o else 'no path'})")
        return out

    def comparable(self, p, oms):
        """Whether both images can show p: each has moved its object, or
        still has the surface (a deferred field: only the surface)."""
        o = obj_of(p)
        if deferred(p):
            return all(not self.gone(om) for om in oms)
        return all((o is not None and o in om) or not self.gone(om) for om in oms)

    def gets(self, m, r, objs):
        if self._gets:
            return self._gets(m, r, objs)
        return [f"get {g}" for o in sorted(objs) for g in GETS.get(o, [o])]

    def new(self, text, m, r, objs):
        """({path: value}, [rest]) from the get lines' replies: the row's
        paths of these objects, normalized and narrowed."""
        v, rest = {}, []
        for l in (text or "").splitlines():
            if l.startswith("err "):
                rest.append(l)
        for p, x in values(text).items():
            t = path_of(p)
            if not t or t[0] not in objs or not any(rx.fullmatch(p) for rx in self.paths):
                continue
            a = re.match(r"state\.sfp\.(\d+)\.", p)
            if a and int(a[1]) not in r.cages:         # state.sfp has 2 on every board: a cage it hasn't
                continue
            x = t[1](x)
            if self.narrow:
                x = self.narrow(p, x, m, r)
            if x is not None:
                v[p] = x
        return v, rest

    def post(self, v, m, r):
        return self._post(v, m, r) if self._post else v


ROWS, PAGES, ACTIONS = [], [], []


def row(rx, what):
    ROWS.append((re.compile(rx), what))


def page(rx, what):
    PAGES.append((re.compile(rx), what))


def find(line, canon, rows=None):
    """(match, what) for an old line (or GET path), or None: a row one of
    whose objects is compared by value."""
    for rx, what in ROWS if rows is None else rows:
        m = rx.fullmatch(line)
        if m and what.objs & canon:
            return m, what
    return None


def find_page(path, canon):
    return find(path, canon, PAGES)


def action(rx, obj):
    """A page with a side effect, which an image that has obj takes by POST."""
    ACTIONS.append((re.compile(rx), obj))


def by_post(path, om):
    """Whether this image takes GET path by POST (an action page)."""
    return any(rx.fullmatch(path) and o in om for rx, o in ACTIONS)


# cfg: P3B's binary saved config. POST /config's text upload is gone, and
# the config scenario saves with `save` (oldnew.py post_config)
MARKERS = {"cfg"}


def objects():
    return {o for _, w in ROWS + PAGES for o in w.objs if o} | {o for _, o in ACTIONS} | MARKERS


def pending(objs):
    return sorted(set(objs) & PENDING)


# --- the board's ports

def fronts(r):
    """{logical port: front number}: the copper ports and the cages."""
    f = {p: int(x) for p, x in zip(r.P, r.F)}
    f.update({c[0]: int(c[1]) for c in r.cages.values()})
    return f


def lmask(v, r):
    """A logical port mask (bit 9 the CPU) as a port list."""
    f = fronts(r)
    return plist([f[i] for i in f if v >> i & 1], v >> 9 & 1)


def slot_of(front, r):
    for s, (_, f) in r.cages.items():
        if int(f) == front:
            return s
    return None


def _lines(text):
    """A reply's lines less the echo, the prompt and blank lines."""
    ls = (text or "").replace("\r", "").split("\n")
    return [l for l in ls[1:] if l.strip() and not re.fullmatch(r"(\[confirm [0-9]+\] )?\*?>", l.strip())]


def _cli(parse):
    """A printer's parser from one that takes a line at a time: f(line, v,
    m, r) is True if the line was placed (into v)."""
    def p(text, m, r):
        v, rest = {}, []
        for l in _lines(text):
            if not parse(l, v, m, r):
                rest.append(l)
        return v, rest
    return p


def _json(parse):
    """A page's parser: f(doc, v, rest, m, r) places what it knows; a body
    that isn't JSON, or a status that isn't 200, is a rest line."""
    def p(text, m, r):
        v, rest = {}, []
        head, _, body = (text or "").partition("\n\n")
        st = head.split("\n", 1)[0]
        if " 200 " not in st + " ":
            rest.append(st or "<no response>")
        try:
            doc = json.loads(body)
        except ValueError:
            if body:
                rest.append("not JSON: " + body[:200])
            return v, rest
        parse(doc, v, rest, m, r)
        return v, rest
    return p


def _left(d, known, rest, where):
    """The keys of d a parser didn't place, as rest lines."""
    for k in d:
        if k not in known:
            rest.append(f"{where}{k}: {json.dumps(d[k])}")


# --- port.N (live on feat/om dab5d13: port, mtu, pvid, ingress, isolate, eee, stat)

SPEED = {"10M": "10m", "100M": "100m", "1000M": "1g", "500M": "500m", "10G": "10g", "2.5G": "2g5", "5G": "5g"}
INGRESS = {"u": "untagged", "t": "tagged", "a": "all"}


def _ports(r):
    return [int(f) for f in r.F] + [int(c[1]) for c in r.cages.values()]


@_cli
def _stat(l, v, m, r):
    """stat's table: per port, its state (copper: the PHY on or off, i.e.
    enabled; a cage: the module, state.sfp.S.present), link and speed (a
    nibble of 3 or 7+ prints "Up": 500m or unknown), the counters' low 32
    bits, the MACs learned."""
    f = [x for x in l.split("\t") if x]
    if l.startswith("Port\tState"):
        return True
    if len(f) != 8 or not f[0].isdigit():
        return False
    n = f[0]
    if f[1] in ("On", "Off"):
        v[f"port.{n}.enabled"] = onoff(f[1] == "On")
    elif f[1] in ("SFP IN", "NO SFP") and slot_of(int(n), r):
        v[f"state.sfp.{slot_of(int(n), r)}.present"] = onoff(f[1] == "SFP IN")
    s = f"state.port.{n}."
    v[s + "link"] = "down" if f[2] == "Down" else "up"
    if f[2] != "Down":
        v[s + "speed"] = SPEED.get(f[2], "500m|unknown")
    for k, x in zip(("tx_good", "tx_bad", "rx_good", "rx_bad"), f[3:7]):
        v[s + k] = str(int(x, 16))
    v[s + "macs"] = f[7]
    return True


def _stat_narrow(p, x, m, r):
    if p.endswith(("_good", "_bad")):
        return str(int(x) & 0xffffffff)
    if p.endswith(".speed") and x in ("500m", "unknown"):
        return "500m|unknown"
    return x


def _no_speed_down(v, m, r):
    """No speed without a link: the old surfaces print none."""
    return {p: x for p, x in v.items() if not (p.endswith(".speed") and p.startswith("state.port.")
                                              and v.get(p[:-len("speed")] + "link") != "up")}


STAT = ["port.#.enabled", "state.port.#.(link|speed|tx_good|tx_bad|rx_good|rx_bad|macs)", "state.sfp.#.present"]
row(r"stat", Read("port", STAT, _stat, gets=lambda m, r, o: (["get state.port", "get port.*.enabled"] if "port" in o else []) +
                  (["get state.sfp"] if "state.sfp" in o else []), narrow=_stat_narrow, post=_no_speed_down))


@_cli
def _mtu_show(l, v, m, r):
    a = re.fullmatch(r"Port (\d+) (0x[0-9a-f]+)", l)	# print_short: hex
    if a:
        v[f"port.{a[1]}.mtu"] = num(a[2])
    return bool(a)


row(r"mtu show", Read("port", ["port.#.mtu"], _mtu_show, gets=lambda m, r, o: ["get port.*.mtu"]))
row(r"pvid (\d+) (\d+)", Set("port", lambda m, r: {f"port.{m[1]}.pvid": m[2]}))
row(r"mtu (\d+) (\d+)", Set("port", lambda m, r: {f"port.{m[1]}.mtu": m[2]}))
row(r"port (\d+) name (\S.*)", Set("port", lambda m, r: {f"port.{m[1]}.name": m[2][:31]}))
row(r"port (\d+) lldp (block|permit)", Set("port", lambda m, r: {f"port.{m[1]}.lldp": m[2]}))
row(r"port (\d+) lldp(?: .*)?", Refused("port"))	# "Unknown port <port> lldp ..." (cmd_parser.c:1002)
# parse_port(): on is enabled at auto speed and duplex (cmd_parser.c:973,
# 1051); off only
# powers the PHY down; a speed powers it up, its duplex auto (:973) but for
# 10m and 100m's half or full (:1027-1046; other words ignored); duplex is
# full for "full", else half
row(r"port (\d+) on", Set("port", lambda m, r: {f"port.{m[1]}.enabled": "on", f"port.{m[1]}.speed": "auto",
                                                f"port.{m[1]}.duplex": "auto"}))
row(r"port (\d+) off", Set("port", lambda m, r: {f"port.{m[1]}.enabled": "off"}))
row(r"port (\d+) (auto|10m|100m|1g|2g5|5g|10g)(?: (\S+).*)?",
    Set("port", lambda m, r: {f"port.{m[1]}.enabled": "on", f"port.{m[1]}.speed": m[2], f"port.{m[1]}.duplex":
                              m[3] if m[2] in ("10m", "100m") and m[3] in ("half", "full") else "auto"}))
row(r"port (\d+) duplex(?: (\S+).*)?", Set("port", lambda m, r: {f"port.{m[1]}.duplex": "full" if m[2] == "full" else "half"}))
# parse_isolate(): off is every port and the CPU; the CPU is port 0
row(r"isolate (\d+) off", Set("port", lambda m, r: {f"port.{m[1]}.isolate": plist(_ports(r), True)}))
row(r"isolate (\d+)((?: \d+)+)",
    Set("port", lambda m, r: {f"port.{m[1]}.isolate": plist([int(x) for x in m[2].split() if x != "0"], "0" in m[2].split())}))
row(r"ingress ([uta])", Set("port", lambda m, r: {f"port.{p}.ingress": INGRESS[m[1]] for p in _ports(r)}))
row(r"ingress((?: \d+[uta])+)",
    Set("port", lambda m, r: {f"port.{w[:-1]}.ingress": INGRESS[w[-1]] for w in m[1].split()}))
row(r"eee (on|off)", Set("port", lambda m, r: {f"port.{f}.eee": m[1] for f in r.F}))	# copper only
row(r"eee (on|off) (\d+)", Set("port", lambda m, r: {f"port.{m[2]}.eee": m[1]}))


EEE_W = {"10G": "10g", "5G": "5g", "2.5G": "2g5", "1G": "1g", "100M": "100m"}


@_cli
def _eee_status(l, v, m, r):
    """eee status [N]: per port the speeds advertised (port.N.eee_speeds),
    the partner's (state.port.N.eee.lp) and whether EEE is active; a cage
    says SFP (its media)."""
    a = re.fullmatch(r"Port: (\d+): (?:SFP|Advertising:(.*)Link Partner:(.*?) (ACTIVE|INACTIVE) *)", l)
    if not a:
        return False
    n = a[1]
    if a[2] is None:
        v[f"state.port.{n}.media"] = "sfp"
        return True
    v[f"port.{n}.eee_speeds"] = flags(EEE_W[w] for w in a[2].split())
    v[f"state.port.{n}.eee.lp"] = flags(EEE_W[w] for w in a[3].split())
    v[f"state.port.{n}.eee.active"] = onoff(a[4] == "ACTIVE")
    return True


def _media_sfp(v, m, r):
    """eee status names a cage (SFP), not a copper port's media."""
    return {p: x for p, x in v.items() if not (p.endswith(".media") and x == "copper")}


EEE = ["port.#.eee_speeds", "state.port.#.eee.(lp|active)", "state.port.#.media"]
row(r"eee status(?: (\d+))?", Read("port", EEE, _eee_status, post=lambda v, m, r: _one_port(_media_sfp(v, m, r), m[1])))


def _one_port(v, n):
    """The paths of port n only, when the old command named one."""
    return v if not n else {p: x for p, x in v.items() if re.match(r"(state\.)?port\.%s\." % n, p)}


LP_W = {"10Base-Half": "10h", "10Base-Full": "10f", "100Base-Half": "100h", "100Base-Full": "100f",
        "1000Base-Half": "1000h", "1000Base-Full": "1000f", "2500Base-Full": "2g5", "5000Base-Full": "5g",
        "10GBase-Full": "10g"}
FORCED = {"10M": "10m", "100M": "100m", "1000M": "1g", "2500M": "2g5", "5G": "5g", "10G": "10g"}


def _port_show(text, m, r):
    """port N show: the name, the duplex the PHY reports (its link speed,
    PHYSR, has no path: the MAC's is state.port.N.speed), auto-negotiation
    or the forced speed and duplex (port.N.speed, duplex; what AN
    advertises has no path), the partner's abilities, the link role and the
    pause negotiated."""
    n, v, rest, forced = m[1], {}, [], False
    for l in _lines(text):
        if l.startswith("Logical Port:"):
            continue
        a = re.fullmatch(r"Name: ?(.*)", l)
        if a:
            v[f"port.{n}.name"] = a[1]
            continue
        a = re.fullmatch(r"Link speed: (\w+)(?: (full|half) duplex)?", l)
        if a:
            v[f"raw.port.{n}.phy_speed"] = a[1]
            if a[2]:
                v[f"state.port.{n}.duplex"] = a[2]
            continue
        a = re.fullmatch(r"AN enabled, advertising:(.*)", l)
        if a:
            v[f"port.{n}.speed"] = "auto"
            v[f"raw.port.{n}.adv"] = flags(LP_W.get(w.replace("BaseN", "Base"), w) for w in a[1].split())
            continue
        if l.startswith("Forced speed:"):
            forced = True
            continue
        if forced and l in FORCED:
            v[f"port.{n}.speed"] = FORCED[l]
            continue
        a = re.fullmatch(r"Duplex: \S+ enabled: (yes|no)", l)
        if a:
            v[f"port.{n}.duplex"] = "full" if a[1] == "yes" else "half"
            continue
        a = re.fullmatch(r"Link Partner advertises:(.*)", l)
        if a:
            v[f"state.port.{n}.lp_adv"] = flags(LP_W[w] for w in a[1].split())
            continue
        a = re.fullmatch(r"Link role: (master|slave)", l)
        if a:
            v[f"state.port.{n}.ms_role"] = a[1]
            continue
        a = re.fullmatch(r"Pause negotiated:(.*)", l)
        if a:
            v[f"state.port.{n}.pause_tx"] = onoff("TX" in a[1].split())
            v[f"state.port.{n}.pause_rx"] = onoff("RX" in a[1].split())
            continue
        rest.append(l)
    return v, rest


def _port_show_post(v, m, r):
    """The role only with a link (the old shows none without); the duplex
    of a forced port is the config's, on both."""
    n = m[1]
    v = _one_port(v, n)
    if v.pop(f"state.port.{n}.link", "up") != "up":
        v.pop(f"state.port.{n}.ms_role", None)
    if v.get(f"port.{n}.speed") == "auto":
        v.pop(f"port.{n}.duplex", None)
    return v


row(r"port (\d+) show", Read("port", ["port.#.(name|speed|duplex)", "state.port.#.(duplex|ms_role|pause_tx|pause_rx|link)",
                              "state.port.#.lp_adv", "raw.port.#.(phy_speed|adv)"], _port_show, post=_port_show_post))

CT = {"ok": "ok", "open at": "open", "short at": "short", "short to another pair at": "cross_short",
      "impedance mismatch at": "mismatch", "unknown": "unknown"}


def _cabletest_show(text, m, r):
    """port N cabletest show: running, no result (idle), timed out, or the
    four pairs (a length for a fault)."""
    n, v, rest = m[1], {}, []
    s = f"state.port.{n}.cabletest."
    for l in _lines(text):
        a = re.fullmatch(r"Pair ([A-D]): (ok|unknown|[a-z ]+ at) ?([\d.]+)?(?: m)?", l)
        if l.startswith("Logical Port:"):
            continue
        elif l == "Cable test running":
            v[s + "state"] = "running"
        elif l == "No cable test result":
            v[s + "state"] = "idle"
        elif re.fullmatch(r"Cable test port \d+: timed out", l):
            v[s + "state"] = "timeout"
        elif re.fullmatch(r"Cable test port \d+", l):
            v[s + "state"] = "done"
        elif a and a[2] in CT:
            v[s + "pair." + a[1].lower() + ".result"] = CT[a[2]]
            if a[3]:
                v[s + "pair." + a[1].lower() + ".length_m"] = a[3]
        else:
            rest.append(l)
    return v, rest


def _cabletest_post(v, m, r):
    """Pairs only when done; a length only for a fault."""
    v = _one_port(v, m[1])
    s = f"state.port.{m[1]}.cabletest."
    out = {}
    for p, x in v.items():
        pr = re.fullmatch(re.escape(s) + r"pair\.([a-d])\.(result|length_m)", p)
        if pr and (v.get(s + "state") != "done" or (pr[2] == "length_m" and v.get(s + "pair." + pr[1] + ".result") in ("ok", "unknown"))):
            continue
        out[p] = x
    return out


row(r"port (\d+) cabletest show", Read("port", ["state.port.#.cabletest.state", "state.port.#.cabletest.pair.[abcd].(result|length_m)"], _cabletest_show, post=_cabletest_post))
row(r"port|port \S+|pvid|pvid \S+|mtu|isolate|isolate \S+|ingress|eee", Refused("port"))


# --- state.sys (P5-1): version, temp

@_cli
def _version(l, v, m, r):
    a = re.fullmatch(r"(Software version|Build date|Compiler|Toolchain|Hardware): (.*)", l)
    if a:
        k = {"Software version": "version", "Build date": "build_date", "Hardware": "machine"}.get(a[1])
        if k:			# Compiler, Toolchain: state.sys.toolchain, deferred (P5-1)
            v["state.sys." + k] = a[2]
    return bool(a)


# state.sys.toolchain and temp_poweron_c: deferred for room in P5-1 (schema/state_sys.py)
row(r"version", Read("state.sys", ["state.sys.(version|build_date|machine)"], _version))


@_cli
def _temp(l, v, m, r):
    a = re.fullmatch(r"(Chip temperature|At power-on): +(-?[\d.]+) C", l)
    if a:
        if a[1] == "Chip temperature":	# At power-on: temp_poweron_c, deferred (P5-1)
            v["state.sys.temp_c"] = a[2]
    return bool(a)


row(r"temp", Read("state.sys", ["state.sys.temp_c"], _temp))


# --- state.sched (P5-2): health tasks

MASK = ["rx", "link", "con_rx", "con_tx", None, None, None, "forever"]
CAUSE = {"?": "power", "watchdog": "watchdog", "^R ^R": "ctrl_r", "reset command": "cmd"}


def _fine(s):
    """health tasks' fine time ("24us", "3ms", ">400ms") as it prints."""
    return s


def _max_us(p, x, m, r):
    """state.sched.state.*.max_us as health tasks prints it: us up to
    7281 fine units (44.7 ms), else whole ms of 163 units; >400ms
    saturated."""
    if not p.endswith(".max_us"):
        return x
    if x.startswith(">") or int(x) >= 402640:      # 0xffff fine units: saturated
        return ">400ms"
    u = round(int(x) / 6.144)
    return f"{u * 6 + (u * 9 >> 6)}us" if u <= 7281 else f"{u // 163}ms"


@_cli
def _health_tasks(l, v, m, r):
    if l in ("slot\ttask\tstate\tmask\twake", "state\truns\tmax"):
        return True
    a = re.fullmatch(r"\d+\t(\S+)\t(?:free|(\w+)\t0x([0-9a-f]{4})\t(now|\+\d+))", l)
    if a:
        s = f"state.sched.slot.{a[1]}."
        v[s + "state"] = a[2] or "none"         # get's word for a free slot
        if a[2]:
            v[s + "mask"] = bits(int(a[3], 16), MASK)
            v[s + "wake"] = "0" if a[4] == "now" else a[4].lstrip("+")
        return True
    a = re.fullmatch(r"([A-Z][A-Z0-9_]+)\t(\d+)\t(>?\d+(?:us|ms))", l)
    if a:
        v[f"state.sched.state.{a[1]}.runs"] = a[2]
        v[f"state.sched.state.{a[1]}.max_us"] = a[3]
        return True
    a = re.fullmatch(r"passes 0x([0-9a-f]+) idle 0x([0-9a-f]+) longest (\S+)", l)
    if a:
        v["state.sched.passes"], v["state.sched.idle"] = str(int(a[1], 16)), str(int(a[2], 16))
        v["state.sched.pass_max_us"] = a[3]
        return True
    a = re.fullmatch(r"overruns (\d+)(?: last (\w+))?, con rx overruns (\d+), hw timeouts (\d+)(?: last 0x([0-9a-f]+))?", l)
    if a:
        v["state.sched.overruns"], v["state.con.rx_overruns"], v["state.health.hw_timeouts"] = a[1], a[3], a[4]
        if a[2]:
            v["state.sched.overrun_last"] = a[2]
        if a[5]:
            v["state.health.hw_timeout_reg"] = str(int(a[5], 16))
        return True
    a = re.fullmatch(r"last reset: (.+?) in (\S+)/(\S+) after (\d+) ms(?:, PC 0x([0-9a-f]+))? bank (\d+), states(.*)", l)
    if a:
        s = "state.sys.reset."
        nm = lambda x: "none" if x == "-" else x            # get's word for no slot or state (P5-2)
        v[s + "cause"], v[s + "slot"], v[s + "state"], v[s + "step_ms"] = CAUSE.get(a[1], a[1]), nm(a[2]), nm(a[3]), a[4]
        v[s + "pc"], v[s + "bank"] = str(int(a[5] or "0", 16)), a[6]
        for i, x in enumerate(a[7].split()):                # states.1-8, oldest first (P5-2)
            v[f"{s}states.{i + 1}"] = nm(x)
        return True
    return False


def _health_post(v, m, r):
    """A free slot shows no mask or wake; the reset record only after a
    reset that left one (the new side's cause power: none)."""
    free = {p[:-len("state")] for p, x in v.items() if p.startswith("state.sched.slot.") and p.endswith(".state") and x == "none"}
    out = {p: x for p, x in v.items() if not any(p.startswith(f) and not p.endswith(".state") for f in free)}
    if out.get("state.sys.reset.cause") == "power":
        out = {p: x for p, x in out.items() if not p.startswith("state.sys.reset.")}
    if out.get("state.sched.overruns") == "0":
        out.pop("state.sched.overrun_last", None)
    if out.get("state.health.hw_timeouts") == "0":
        out.pop("state.health.hw_timeout_reg", None)
    return out


row(r"health tasks", Read("state.sched", ["state.sched.slot.*.(state|mask|wake)", "state.sched.state.*.(runs|max_us)",
                           "state.sched.(passes|idle|pass_max_us|overruns|overrun_last)", "state.con.rx_overruns",
                           "state.health.(hw_timeouts|hw_timeout_reg)", "state.sys.reset.(cause|slot|state|step_ms|pc|bank|states.#)"],
                          _health_tasks, narrow=lambda p, x, m, r: _max_us(p, x, m, r) if not p.endswith("pass_max_us")
                          else _max_us(".max_us", x, m, r), post=_health_post))


# --- state.mgmt (P5-3): ip, gw, netmask

@_cli
def _ip(l, v, m, r):
    """ip: the address, and leasing (with the renewal) or not: the old says
    "static" for every DHCP state but leasing."""
    a = re.fullmatch(r"Current IP: ([\d.]+) \((?:static|dhcp, renewal in sec: 0x([0-9a-f]+))\)", l)
    if a:
        v["state.mgmt.ip"] = a[1]
        v["state.mgmt.dhcp.state"] = "leasing" if a[2] else "not leasing"
        if a[2]:
            v["state.mgmt.dhcp.renew_s"] = str(int(a[2], 16))
    return bool(a)


def _ip_post(v, m, r):
    if v.get("state.mgmt.dhcp.state") not in ("leasing", "not leasing"):
        v["state.mgmt.dhcp.state"] = "not leasing"
    if v["state.mgmt.dhcp.state"] != "leasing":
        v.pop("state.mgmt.dhcp.renew_s", None)
    return v


def _ip_narrow(p, x, m, r):
    """get's DHCP state as the old says it: leasing, or not."""
    if p == "state.mgmt.dhcp.state":
        return "leasing" if x == "leasing" else "not leasing"
    return x


# the no-argument forms go with state.mgmt (P5-3); setting stays with mgmt
row(r"ip", Read("state.mgmt", ["state.mgmt.ip", "state.mgmt.dhcp.(state|renew_s)"], _ip, narrow=_ip_narrow, post=_ip_post))


def _current(name, path):
    @_cli
    def p(l, v, m, r):
        a = re.fullmatch(r"Current %s: ([\d.]+)" % name, l)
        if a:
            v[path] = a[1]
        return bool(a)
    return p


row(r"gw", Read("state.mgmt", ["state.mgmt.gateway"], _current("gw", "state.mgmt.gateway")))
row(r"netmask", Read("state.mgmt", ["state.mgmt.netmask"], _current("netmask", "state.mgmt.netmask")))


# --- state.ntp, state.dns, state.loop (P5-3): ntp, time, dns, loop

def _time_line(l, v):
    """ntp's and time's time line: synchronised or not; the time, which
    the old prints local, in UTC; the offset with DST; the minutes since
    the sync (state.ntp.last_sync_s in whole minutes); the stratum."""
    if l == "Time not synchronised":
        v["state.ntp.synced"] = "off"
        return True
    a = re.fullmatch(r"Time (\d+)-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) UTC([+-])(\d\d):(\d\d)( DST)?, "
                     r"synchronised (\d+) min ago, stratum (\d+)", l)
    if not a:
        return False
    import datetime
    off = (1 if a[7] == "+" else -1) * (int(a[8]) * 60 + int(a[9]))
    t = datetime.datetime(*map(int, a.groups()[:6])) - datetime.timedelta(minutes=off)
    v["state.ntp.synced"] = "on"
    v["state.ntp.time"] = t.strftime("%Y-%m-%d %H:%M:%S")
    v["state.ntp.local_offset_min"], v["state.ntp.dst_active"] = str(off), onoff(a[10])
    v["state.ntp.last_sync_s"], v["state.ntp.stratum"] = f"{a[11]} min", a[12]
    return True


def _ntp_narrow(p, x, m, r):
    if p == "state.ntp.last_sync_s":
        return f"{int(x) // 60} min"
    if p == "state.ntp.time":
        if x.isdigit():                         # seconds since 1970 (P5-3)
            import datetime
            return datetime.datetime.fromtimestamp(int(x), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        a = re.match(r"(\d+-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)", x)
        return f"{a[1]} {a[2]}" if a else x
    return x


def _ntp_post(v, m, r):
    if v.get("state.ntp.synced") == "off":
        v = {p: x for p, x in v.items() if not p.startswith("state.ntp.") or p == "state.ntp.synced"}
    return v


NTP = ["state.ntp.(synced|time|local_offset_min|dst_active|last_sync_s|stratum)"]


@_cli
def _ntp(l, v, m, r):
    a = re.fullmatch(r"NTP (on|off), server (\S*), address ([\d.]+), interval (\d+) min, timezone (\S+), dst (\w+)", l)
    if a:
        v["ntp.enabled"], v["ntp.server"], v["state.ntp.server_ip"] = a[1], a[2], a[3]
        v["ntp.interval_min"], v["ntp.timezone"], v["ntp.dst"] = a[4], a[5], a[6]
        return True
    return _time_line(l, v)


row(r"ntp", Read("ntp state.ntp", ["ntp.(enabled|server|interval_min|timezone|dst)", "state.ntp.server_ip"] + NTP, _ntp,
                 narrow=_ntp_narrow, post=_ntp_post))
row(r"time", Read("state.ntp", NTP, _cli(lambda l, v, m, r: _time_line(l, v)), narrow=_ntp_narrow, post=_ntp_post))


@_cli
def _dns(l, v, m, r):
    a = re.fullmatch(r"DNS servers ([\d.]+) ([\d.]+)(?:, from DHCP ([\d.]+))?", l)     # P5-3: the servers only
    if a:
        v["dns.server1"], v["dns.server2"] = a[1], a[2]
        if a[3]:
            v["state.dns.dhcp_server"] = a[3]
        return True
    a = re.fullmatch(r"Last lookup (.*): (pending|failed|[\d.]+)", l)
    if a:                                       # the name: deferred (P5-3)
        v["state.dns.lookup.status"] = a[2] if a[2] in ("pending", "failed") else "done"
        if v["state.dns.lookup.status"] == "done":
            v["state.dns.lookup.ip"] = a[2]
        return True
    return False


def _dns_post(v, m, r):
    """No lookup line before the first lookup; an address only when done."""
    if v.get("state.dns.lookup.status") in (None, "none"):
        return {p: x for p, x in v.items() if not p.startswith("state.dns.lookup.")}
    if v["state.dns.lookup.status"] != "done":
        v.pop("state.dns.lookup.ip", None)
    return v


row(r"dns", Read("dns state.dns", ["dns.server[12]", "state.dns.dhcp_server", "state.dns.lookup.(status|ip)"], _dns, post=_dns_post))


@_cli
def _loop_show(l, v, m, r):
    a = re.fullmatch(r"Loop detection: (on|off)|(Looped|Blocked) ports: ([\d ]*)", l)
    if a and a[1]:
        v["loop.enabled"] = a[1]
    elif a:
        v["state.loop." + a[2].lower()] = plist(int(x) for x in a[3].split())
    return bool(a)


def _loop_post(v, m, r):
    """The old shows the ports only with detection on."""
    if v.get("loop.enabled") == "off":
        v = {p: x for p, x in v.items() if p not in ("state.loop.looped", "state.loop.blocked")}
    return v


def _loop_json_post(v, m, r):
    """loop.json says none for both when detection is off."""
    if v.get("loop.enabled") == "off":
        v.update({p: "none" for p in ("state.loop.looped", "state.loop.blocked") if p in v})
    return v


row(r"loop(?: show)?", Read("loop", ["loop.enabled", "state.loop.(looped|blocked)"], _loop_show, post=_loop_post))


# --- state.sfp (P5-4): sfp

def _ddm(v, s, raw):
    """The DDM words (SFF-8472 A2 224..233) as get prints them (P5-4, the
    DESIGN 7.7 deviation): temp (1/256 C) and TX bias (2 uA) raw, Vcc
    (100 uV) and the TX and RX power (0.1 uW) in V and mW. No calibration:
    that is state.sfp.S.ddm.cal's, the UI's."""
    def word(k):
        return int(raw[k], 16)
    for k, path in (("temp", "ddm.temp"), ("txbias", "ddm.tx_bias")):
        if k in raw:
            v[s + path] = str(word(k))
    for k, path in (("vcc", "ddm.vcc_v"), ("txpower", "ddm.tx_power_mw"), ("rxpower", "ddm.rx_power_mw")):
        if k in raw:
            v[s + path] = f"{word(k) / 10000:.4f}"


STATUS = [("not_ready", 0x01), ("rx_los", 0x02), ("tx_fault", 0x04), ("tx_disable", 0x80)]


def _status(x):
    return flags(n for n, b in STATUS if x & b)


def _sfp_print(text, m, r):
    """sfp: per slot empty, unread, or the rate (byte 12, as read: x100
    Mbit/s), encoding, the vendor/part/rev fields as one string (split at
    their widths, 16/16/4), the options before the quirks (no path: DESIGN
    keeps them after, state.sfp.S.options), the DDM words."""
    v, rest, s, raw = {}, [], None, {}
    keys = {"Temp": "temp", "Vcc": "vcc", "TX Bias": "txbias", "TX Power": "txpower", "RX Power": "rxpower"}
    for l in _lines(text):
        a = re.fullmatch(r"Slot (\d) - (empty|I2C read failed on this slot|Rate: ([0-9a-f]{2})  Encoding: ([0-9a-f]{2}))", l)
        if a:
            if s and raw:
                _ddm(v, s, raw)
            s, raw = f"state.sfp.{a[1]}.", {}
            v[s + "present"] = onoff(a[2] != "empty")
            if a[2] != "empty":
                v[s + "ready"] = onoff(a[3] is not None)
            if a[3]:
                v[s + "rate_mbps"], v[s + "encoding"] = str(int(a[3], 16) * 100), str(int(a[4], 16))
            continue
        a = re.fullmatch(r"(Temp|Vcc|TX Bias|TX Power|RX Power|Laser|State|Options): ([0-9a-f]+)", l)
        if s and a:
            if a[1] == "Options":
                v["raw." + s[6:] + "options_read"] = str(int(a[2], 16))
            elif a[1] == "Laser":
                v[s + "ddm.laser"] = str(int(a[2], 16))
            elif a[1] == "State":
                v[s + "ddm.status"] = _status(int(a[2], 16))
            else:
                raw[keys[a[1]]] = a[2]
            continue
        if s and s + "rate_mbps" in v and s + "vendor" not in v and l == "I2C read failed on this slot":
            v[s + "ddm_ok"] = "off"
            continue
        if s and s + "rate_mbps" in v and s + "vendor" not in v:
            v[s + "vendor"], v[s + "part"], v[s + "rev"] = l[:16].strip(), l[16:32].strip(), l[32:36].strip()
            continue
        rest.append(l)
    if s and raw:
        _ddm(v, s, raw)
    return v, rest


def _sfp_post(v, m, r):
    """An empty cage shows nothing else; ready only for a module; the DDM
    and the rest only for one that was read."""
    out = {}
    for p, x in v.items():
        s = re.match(r"state\.sfp\.(\d)\.", p)
        if s:
            b = f"state.sfp.{s[1]}."
            if v.get(b + "present") != "on" and p != b + "present":
                continue
            if v.get(b + "ready") == "off" and p not in (b + "present", b + "ready"):
                continue
            if p == b + "ddm_ok" and x == "on":
                continue
            if ".ddm." in p and v.get(b + "ddm_ok") == "off":     # get prints the empty cache
                continue
        out[p] = x
    return out


SFP = ["state.sfp.#.(present|ready|ddm_ok|rate_mbps|encoding|vendor|part|rev)",
       "state.sfp.#.ddm.(temp|vcc_v|tx_bias|tx_power_mw|rx_power_mw|laser|status)", "raw.sfp.#.options_read"]
row(r"sfp", Read("state.sfp", SFP, _sfp_print, post=_sfp_post))


# --- state.stp (P5-4): stp status

STATE_W = {"off": "off", "block": "block", "learn": "learn", "fwd": "fwd"}
ROLE_W = {"dis": "disabled", "root": "root", "desg": "designated", "altn": "alternate", "back": "backup"}


def _ent(e):
    """An STP entity as the old prints it ("01", "L1"; JSON 1, 101) as a path."""
    e = str(e)
    if e.startswith("L"):
        return f"stp.lag.{e[1:]}"
    n = int(e)
    return f"stp.lag.{n - 100}" if n > 100 else f"stp.port.{n}"


def _stp_status(text, m, r):
    v, rest = {}, []
    for l in _lines(text):
        a = re.fullmatch(r"STP (off|on, (RSTP|STP))", l)
        if a:
            v["stp.enabled"] = "off" if a[1] == "off" else "on"
            if a[2]:
                v["stp.version"] = a[2].lower()
            continue
        a = re.fullmatch(r"(bridge|root) +([0-9a-f]{4})/([0-9a-f]{12})(?: \(this switch\)| port (\S+) cost 0x([0-9a-f]+))?", l)
        if a:
            v["state.stp." + a[1]] = bridge(a[2], a[3])
            if a[1] == "root":
                v["state.stp.is_root"] = onoff(a[4] is None)
                if a[4]:
                    v["state.stp.root_port"] = _ent(a[4])[4:].replace("port.", "")
                    v["state.stp.root_cost"] = str(int(a[5], 16))
            continue
        a = re.fullmatch(r"changes 0x([0-9a-f]+)", l)
        if a:
            v["state.stp.tc_count"] = str(int(a[1], 16))
            continue
        if l == "port state role edge tx age[s]":
            continue
        a = re.fullmatch(r" (\d\d|L\d)  (\w+) +(\w+) +(yes|no) +([0-9a-f]{2}) (\d+)", l)
        if a:
            s = "state." + _ent(a[1]) + "."
            v[s + "state"], v[s + "role"] = STATE_W.get(a[2], a[2]), ROLE_W.get(a[3], a[3])
            v[s + "edge"], v[s + "tx_bpdu"], v[s + "bpdu_age_ms"] = onoff(a[4] == "yes"), str(int(a[5], 16)), a[6]
            continue
        rest.append(l)
    return v, rest


def _stp_status_narrow(p, x, m, r):
    """tx: its low byte; age: whole seconds of the 50 Hz ticks, capped at 255."""
    if p.endswith(".tx_bpdu"):
        return str(int(x) & 0xff)
    if p.endswith(".bpdu_age_ms"):
        return str(min(int(x) // 1000, 255))
    return x


def _stp_status_post(v, m, r):
    """With STP off the old prints that alone; the root port and cost only
    when not root; the root port as the old names it (N or lag.L)."""
    if v.get("stp.enabled") == "off":
        return {"stp.enabled": "off"}
    if v.get("state.stp.is_root") == "on":
        v = {p: x for p, x in v.items() if p not in ("state.stp.root_port", "state.stp.root_cost")}
    return v


row(r"stp status", Read("state.stp", ["stp.(enabled|version)", "state.stp.(bridge|root|is_root|root_port|root_cost|tc_count)",
                         "state.stp.(port|lag).#.(state|role|edge|tx_bpdu|bpdu_age_ms)", "state.stp.lag.#.members"], _stp_status,
                        narrow=_stp_status_narrow,
                        post=lambda v, m, r: _stp_status_post({p: x for p, x in _active_ents(v, m, r).items()
                                                               if not p.endswith(".members")}, m, r)))


# --- state.lag (P5-4): lacp show, lag show

ACTOR = ["activity", "timeout", "aggregation", "sync", "collecting", "distributing", "defaulted", "expired"]
RXST = ["initialize", "port_disabled", "expired", "defaulted", "current"]
HASH = ["spa", "smac", "dmac", "sip", "dip", "sport", "dport"]


def _lacp_show(text, m, r):
    """lacp show: running; per LACP LAG its configured ports (lag.L.members),
    aggregator (or none: agg_valid off) and active members (logical masks);
    per port the actor and partner states, the receive state, the LACPDUs
    received and the partner's system."""
    v, rest = {}, []
    for l in _lines(text):
        if re.fullmatch(r"LACP (on|off)", l):           # state.lacp.running: deferred (P5-4)
            continue
        a = re.fullmatch(r"lag (\d\d) ports 0x([0-9a-f]{4}) aggregator (\(none\)|[0-9a-f]{12}) members 0x([0-9a-f]{4})", l)
        if a:
            L = int(a[1])
            v[f"lag.{L}.members"], v[f"state.lag.{L}.active"] = lmask(int(a[2], 16), r), lmask(int(a[4], 16), r)
            v[f"state.lag.{L}.agg_valid"] = onoff(a[3] != "(none)")
            if a[3] != "(none)":
                v[f"state.lag.{L}.aggregator"] = mac(a[3])
            continue
        a = re.fullmatch(r"port (\d\d) actor ([0-9a-f]{2}) partner ([0-9a-f]{2}) rxst ([0-9a-f]{2}) rx 0x([0-9a-f]{4}) psys ([0-9a-f]{12})", l)
        if a:
            s = f"state.lacp.port.{int(a[1])}."
            v[s + "actor"], v[s + "partner"] = bits(int(a[2], 16), ACTOR), bits(int(a[3], 16), ACTOR)
            v[s + "rx_state"], v[s + "rx_pdus"], v[s + "partner_sys"] = RXST[int(a[4], 16)], str(int(a[5], 16)), mac(a[6])
            continue
        rest.append(l)
    return v, rest


def _lacp_post(v, m, r):
    """The LAG lines only for a LAG in LACP mode (the new side's lag.L.mode
    says, then goes); no aggregator when not valid."""
    lacp = {p.split(".")[1] for p, x in v.items() if re.fullmatch(r"lag\.\d\.mode", p) and x == "lacp"}
    # get state.lag (P5-4) lists every LAG: an LACP one has a port in it
    lacp |= {x for p, x in v.items() if re.fullmatch(r"state\.lacp\.port\.\d+\.lag", p) and x != "0"}
    if any(re.fullmatch(r"(state\.lacp\.port\.\d+\.)?lag(\.\d\.mode)?", p) for p in v):
        v = {p: x for p, x in v.items() if not p.endswith(".lag")}
        v = {p: x for p, x in v.items() if not re.fullmatch(r"(state\.)?lag\.\d\..*", p) or p.split(".")[-2] in lacp}
        v = {p: x for p, x in v.items() if not p.endswith(".mode")}
    return {p: x for p, x in v.items() if not (p.endswith(".aggregator") and
                                              v.get(p[:-len("aggregator")] + "agg_valid") == "off")}


row(r"lacp show", Read("state.lag", ["lag.#.(members|mode)", "state.lag.#.(active|agg_valid|aggregator)",
                        "state.lacp.port.#.(lag|actor|partner|rx_state|rx_pdus|partner_sys)"], _lacp_show, post=_lacp_post))


@_cli
def _lag_show(l, v, m, r):
    """lag show: each LAG's members as the hardware has them (the active
    set: state.lag.L.active) and, with members, the hash's low byte."""
    if l == "LAG status:":
        return True
    a = re.fullmatch(r" (\d) (?:disabled|member ports: ([\d ]+) \(hash: 0x([0-9a-f]{2})\))", l)
    if a:
        L = a[1]
        v[f"state.lag.{L}.active"] = plist(int(x) for x in (a[2] or "").split())
        if a[3]:
            v[f"lag.{L}.hash"] = bits(int(a[3], 16), HASH)
    return bool(a)


def _lag_show_post(v, m, r):
    return {p: x for p, x in v.items() if not (p.endswith(".hash") and v.get(f"state.lag.{p.split('.')[1]}.active") == "none")}


row(r"lag show", Read("lag state.lag", ["state.lag.#.active", "lag.#.hash"], _lag_show, post=_lag_show_post))


# --- state.l2 (P5-5): l2

def _l2_entry(v, key, m_, vlan, port, static, lag):
    v[key] = f"vlan {vlan} " + (f"lag {lag}" if lag != "0" else f"port {port}") + f" static {static}"


@_cli
def _l2(l, v, m, r):
    """l2: the table, an entry a line (no index): MAC, VLAN, static or
    learned, the port (CPU) or the LAG; compared as a set keyed by MAC and
    VLAN."""
    if l == "\tMAC\t\tVLAN\ttype\tport":
        return True
    a = re.fullmatch(r"([0-9a-f:]{17})\t0x([0-9a-f]{4})\t(static|learned)\t(CPU|LAG\d|\d+)", l)
    if a:
        lag = a[4][3:] if a[4].startswith("LAG") else "0"
        _l2_entry(v, f"state.l2.{a[1]}/{int(a[2], 16)}", a[1], int(a[2], 16), a[4].lower(), onoff(a[3] == "static"), lag)
    return bool(a)


def _l2_entries(v):
    """The new side's state.l2.<idx>.* as {idx: {field: value}}."""
    e = {}
    for p, x in v.items():
        a = re.fullmatch(r"state\.l2\.(\d+)\.(\w+)", p)
        if a:
            e.setdefault(int(a[1]), {})[a[2]] = x
    return e


def _l2_post(v, m, r):
    out = {p: x for p, x in v.items() if not re.fullmatch(r"state\.l2\.\d+\.\w+", p)}
    for i, f in _l2_entries(v).items():
        _l2_entry(out, f"state.l2.{f.get('mac')}/{f.get('vlan')}", f.get("mac"), f.get("vlan"), f.get("port"),
                  f.get("static"), f.get("lag", "0"))
    return out


row(r"l2", Read("state.l2", ["state.l2.#.(mac|vlan|port|lag|static)"], _l2, post=_l2_post))
row(r"l2 forget", Set("l2", lambda m, r: {}, then=lambda m, r, prev: ["do l2.flush"], status=True))


# --- the JSON pages (P5-1, P5-4, P5-5)

POE = {"ok": "ok", "no image": "no_image", "no answer": "no_answer", "no image for this chip": "no_chip_image",
       "failed": "failed", "no room": "no_room"}
FLASH = {"MB": 1 << 20, "KB": 1 << 10}


def _information(d, v, rest, m, r):
    """/information.json: the management address (effective: state.mgmt),
    syslog, session timeout, the address mode, the MAC, hostname, build,
    uptime (hex), machine, PCB, temperature ("41.5 C"), flash size ("2
    MB"), image size (hex), loader, PoE, LEDs, and per cage the module's
    vendor, part and rev as one string."""
    k = {}
    for key, p, f in [("ip_address", "state.mgmt.ip", str), ("ip_gateway", "state.mgmt.gateway", str),
                      ("ip_netmask", "state.mgmt.netmask", str), ("session_timeout", "web.session_timeout_s", str),
                      ("ip_source", "mgmt.mode", str), ("mac_address", "state.sys.mac", mac),
                      ("hostname", "sys.hostname", str), ("sw_ver", "state.sys.version", str),
                      ("build_date", "state.sys.build_date", str), ("uptime", "state.sys.uptime_s", num),
                      ("hw_ver", "state.sys.machine", str), ("pcb_ver", "state.sys.pcb", str),
                      ("chip_temp", "state.sys.temp_c", lambda x: x.split()[0]),
                      ("flash_size", "state.sys.flash.size", lambda x: str(int(x.split()[0]) * FLASH[x.split()[1]])),
                      ("img_size", "state.sys.image_size", num), ("loader", "state.loader.present", lambda x: onoff(x == "yes")),
                      ("poe", "state.sys.poe", lambda x: POE.get(x, x)),
                      ("leds", "sys.led", lambda x: {"ports": "ports_off"}.get(x, x))]:
        if key in d:
            v[p], k[key] = f(d[key]), 1
    if "syslog_server" in d:
        v["syslog.server"], v["syslog.port"] = d["syslog_server"].rsplit(":", 1)
        k["syslog_server"] = 1
    for s in (0, 1):
        x = d.get(f"sfp_slot_{s}")
        if x is not None:
            k[f"sfp_slot_{s}"] = 1
            b = f"state.sfp.{s + 1}."
            if x:
                v[b + "vendor"], v[b + "part"], v[b + "rev"] = x[:16].strip(), x[16:32].strip(), x[32:36].strip()
            else:
                v[b + "id_ok"] = "off"
    _left(d, k, rest, "")


def _information_post(v, m, r):
    """A cage whose module wasn't identified sends "" (id_ok off); the rest
    of its ID only when identified. A build without PoE sends no poe
    (state.sys.poe none)."""
    out = {}
    for p, x in v.items():
        if p == "state.sys.poe" and x == "none":
            continue
        s = re.match(r"(state\.sfp\.\d\.)", p)
        if s and p != s[1] + "id_ok" and v.get(s[1] + "id_ok") == "off":
            continue
        if s and p == s[1] + "id_ok" and x == "on":
            continue
        out[p] = x
    return out


# gone with state.sys (P5-1 deletes the whole page): its config echoes are
# compared once their objects move, by get
page(r"/information\.json", Read("state.sys", ["state.mgmt.(ip|gateway|netmask)", "web.session_timeout_s", "mgmt.mode",
                                  "syslog.(server|port)", "sys.(hostname|led)",
                                  "state.sys.(mac|version|build_date|uptime_s|machine|pcb|temp_c|flash.size|image_size|poe)",
                                  "state.loader.present", "state.sfp.#.(vendor|part|rev|id_ok)"],
                                 _json(_information), post=_information_post))

ADV = {"2.5G": "2g5", "1G": "1000f", "100M-FD": "100f", "100M-HD": "100h", "10M-FD": "10f", "10M-HD": "10h"}
CAL = {"sfp_temp_cal": "temp", "sfp_vcc_cal": "vcc", "sfp_txbias_cal": "txbias", "sfp_txpower_cal": "txpower"}


def _ports_json(d, v, rest, m, r):
    """/ports.json: per port the name, media, copper: enabled and what AN
    advertises (no path: implied by port.N.speed), the MTU (hex); a cage:
    the module present (enabled), its options after the quirks, DDM words
    (raw hex, calibration blobs when external: applied here), state byte,
    vendor, model (part), serial, LOS (null without a pin)."""
    for e in d:
        n, k = e.get("port"), {"port": 1}
        p = f"port.{n}."
        for key, path, f in [("name", p + "name", str), ("isSFP", f"state.port.{n}.media", lambda x: "sfp" if x else "copper"),
                             ("mtu", p + "mtu", num)]:
            if key in e:
                v[path], k[key] = f(e[key]), 1
        if not e.get("isSFP"):
            if "enabled" in e:
                v[p + "enabled"], k["enabled"] = onoff(e["enabled"]), 1
            if "adv" in e:
                v[f"raw.port.{n}.adv"], k["adv"] = flags(ADV.get(x, x) for x in e["adv"]), 1
        else:
            s = f"state.sfp.{slot_of(n, r)}."
            if "enabled" in e:
                v[s + "present"], k["enabled"] = onoff(e["enabled"]), 1
            for key, path, f in [("sfp_options", s + "options", num), ("sfp_vendor", s + "vendor", str),
                                 ("sfp_model", s + "part", str), ("sfp_serial", s + "serial", str),
                                 ("sfp_state", s + "ddm.status", lambda x: _status(int(x, 16)))]:
                if key in e:
                    v[path], k[key] = f(e[key]), 1
            if "sfp_los" in e:
                v[s + "los"], k["sfp_los"] = "none" if e["sfp_los"] is None else "los" if e["sfp_los"] else "ok", 1
            raw = {key[4:]: e[key][2:] for key in ("sfp_temp", "sfp_vcc", "sfp_txbias", "sfp_txpower", "sfp_rxpower")
                   if key in e and len(e[key]) > 2}
            k.update({key: 1 for key in ("sfp_temp", "sfp_vcc", "sfp_txbias", "sfp_txpower", "sfp_rxpower")})
            _ddm(v, s, raw)
            if "sfp_rxpower_cal" in e:              # 184..203, then the pairs 204..219, as ddm.cal holds them
                v[s + "ddm.cal"] = "".join(e[key][2:] for key in ("sfp_rxpower_cal", "sfp_txbias_cal", "sfp_txpower_cal",
                                                                  "sfp_temp_cal", "sfp_vcc_cal") if key in e)
                k.update({key: 1 for key in list(CAL) + ["sfp_rxpower_cal"]})
        _left(e, k, rest, f"port {n} ")


def _ports_post(v, m, r):
    """A cage without a module shows only that; the DDM only when the module
    has it (options 0x40)."""
    out = {}
    for p, x in v.items():
        s = re.match(r"(state\.sfp\.\d\.)", p)
        if s and v.get(s[1] + "present") != "on" and p != s[1] + "present":
            continue
        if s and ".ddm." in p and not int(v.get(s[1] + "options", "0")) & 0x40:
            continue
        if s and p.endswith(".ddm.cal") and not x:          # internal calibration: get prints it empty
            continue
        out[p] = x
    return out


page(r"/ports\.json", Read("state.mib", ["port.#.(name|enabled|mtu)", "state.port.#.media", "raw.port.#.adv",
                            "state.sfp.#.(present|options|vendor|part|serial|los)",
                            "state.sfp.#.ddm.(status|temp|vcc_v|tx_bias|tx_power_mw|rx_power_mw|cal)"],
                           _json(_ports_json), post=_ports_post))


def _traffic(d, v, rest, m, r):
    """/traffic.json: per port the link ("DOWN" or the speed, "" for a
    nibble of 7+), the good counters (64 bits), the bad ones (32), the MACs
    learned (the raw register: masked to its 13 bits, as state has it)."""
    for e in d:
        n, k = e.get("port"), {"port": 1}
        s = f"state.port.{n}."
        if "link" in e:
            v[s + "link"], k["link"] = "down" if e["link"] == "DOWN" else "up", 1
            if e["link"] != "DOWN":
                v[s + "speed"] = SPEED.get(e["link"], "unknown")
        for key, f in [("txG", "tx_good"), ("txB", "tx_bad"), ("rxG", "rx_good"), ("rxB", "rx_bad")]:
            if key in e:
                v[s + f], k[key] = str(int(e[key], 16)), 1
        if "macs" in e:
            v[s + "macs"], k["macs"] = str(int(e["macs"], 16) & 0x1fff), 1
        _left(e, k, rest, f"port {n} ")


page(r"/traffic\.json", Read("state.mib", ["state.port.#.(link|speed|tx_good|tx_bad|rx_good|rx_bad|macs)"], _json(_traffic),
                             post=_no_speed_down))

def _counters(d, v, rest, m, r):
    """/counters.json?port=N: the 55 STAT indexes, each "0x" + hex: as
    state.port.N.mib.K holds them (P5-5), the raw 64-bit word of index K
    (the names are schema.js's)."""
    n = m[1]
    if not isinstance(d, list) or len(d) != 55:
        rest.append(f"{len(d) if isinstance(d, list) else type(d).__name__} counters, not 55")
        return
    for k, x in enumerate(d):
        v[f"state.port.{n}.mib.{k}"] = str(int(x, 16))


page(r"/counters\.json\?port=(\d+)", Read("state.mib", ["state.port.#.mib.*"], _json(_counters),
                                          gets=lambda m, r, o: [f"get state.port.{m[1]}.mib"]))

STP_F = {"enabled": 0x01, "filter": 0x20}


def _stp_json(d, v, rest, m, r):
    """/stp.json: the bridge config (stp.*), the TC age (hex), the root's
    timers, the root (prio + MAC), our MAC (state.sys.mac), cost, root or
    not, TC count; per entity (port N, LAG 101..104) its state and role
    (numbers), flags (config bits 0x01..0x20 to stp.*.enabled/edge/guard/
    filter, runtime 0x40 edge, 0x80 guard tripped), cost, LAG members,
    priority, p2p (0 auto, 1 on, 2 off), link, legacy, designated
    bridge/port/cost."""
    k = {}
    for key, p, f in [("on", "stp.enabled", onoff), ("type", "stp.version", str), ("prio", "stp.priority", str),
                      ("hello", "stp.hello_s", str), ("maxage", "stp.maxage_s", str), ("fwd", "stp.fwd_delay_s", str),
                      ("txhold", "stp.txhold", str), ("pcs", "stp.pathcost", str), ("bh", "stp.bpdu", str),
                      ("tcs", "state.stp.tc_age_s", num), ("rMaxage", "state.stp.root_maxage_s", str),
                      ("rFwd", "state.stp.root_fwd_s", str), ("myMac", "state.sys.mac", mac),
                      ("cost", "state.stp.root_cost", num), ("weRoot", "state.stp.is_root", onoff),
                      ("tc", "state.stp.tc_count", num)]:
        if key in d:
            v[p], k[key] = f(d[key]), 1
    if "rootPrio" in d and "rootMac" in d:
        v["state.stp.root"], k["rootPrio"], k["rootMac"] = bridge(d["rootPrio"], d["rootMac"]), 1, 1
    for e in d.get("ports", []):
        c, s, ke = _ent(e.get("p")), "state." + _ent(e.get("p")) + ".", {"p": 1}
        for key, p, f in [("st", s + "state", lambda x: ["off", "block", "learn", "fwd"][x]),
                          ("role", s + "role", lambda x: ["disabled", "root", "designated", "alternate", "backup"][x]),
                          ("pc", c + ".cost", num), ("prio", c + ".priority", str),
                          ("p2", c + ".p2p", lambda x: ["auto", "on", "off"][x]), ("lk", s + "link", onoff),
                          ("lg", s + "legacy", onoff), ("db", s + "dbridge", lambda x: bridge(x[:4], x[4:])),
                          ("dp", s + "dport", lambda x: str(int(x, 16))), ("dc", s + "dcost", num),
                          ("mbr", s + "members", plist)]:
            if key in e:
                v[p], ke[key] = f(e[key]), 1
        if "f" in e:
            f, ke["f"] = e["f"], 1
            v[c + ".enabled"], v[c + ".filter"] = onoff(f & 0x01), onoff(f & 0x20)
            v[c + ".edge"] = "on" if f & 0x02 else "auto" if f & 0x04 else "off"
            v[c + ".guard"] = "bpdu" if f & 0x08 else "root" if f & 0x10 else "none"
            v[s + "edge"], v[s + "guard_tripped"] = onoff(f & 0x40), onoff(f & 0x80)
        _left(e, ke, rest, f"entity {e.get('p')} ")
    _left(d, set(k) | {"ports"}, rest, "")


def _active_ents(v, m, r):
    """The entities the old lists are the active ones: a LAG with members,
    a port in none. The new side lists every one: those of the others go."""
    lagged = set()
    for p, x in v.items():
        a = re.fullmatch(r"state\.stp\.lag\.\d\.members", p)
        if a and x != "none":
            lagged |= set(x.split(","))
    lags = {p.split(".")[3] for p, x in v.items() if re.fullmatch(r"state\.stp\.lag\.\d\.members", p) and x != "none"}

    def keep(p):
        a = re.match(r"(?:state\.)?stp\.(port|lag)\.(\d+)\.", p)
        return not a or (a[1] == "port" and a[2] not in lagged) or (a[1] == "lag" and a[2] in lags)
    return {p: x for p, x in v.items() if keep(p)}


STP_ENT = ["stp.(port|lag).#.(enabled|edge|cost|priority|guard|filter|p2p)",
           "state.stp.(port|lag).#.(state|role|edge|guard_tripped|link|legacy|members|dbridge|dport|dcost)"]
page(r"/stp\.json", Read("stp state.stp", ["stp.(enabled|version|priority|hello_s|maxage_s|fwd_delay_s|txhold|pathcost|bpdu)",
                          "state.stp.(tc_age_s|root_maxage_s|root_fwd_s|root|root_cost|is_root|tc_count)",
                          "state.sys.mac"] + STP_ENT, _json(_stp_json), post=_active_ents))


def _stpcnt(d, v, rest, m, r):
    """/stpcnt.json: on, the tick rate (50 Hz: no path, ages are in ms), the
    TC count (hex); per entity 4 hex words (BPDUs in, out, TC in, TC out)
    and the BPDU age in ticks."""
    k = {"hz": 1}
    if d.get("hz") != 50:
        rest.append(f"hz {d.get('hz')}")
    if "on" in d:
        v["stp.enabled"], k["on"] = onoff(d["on"]), 1
    if "tc" in d:
        v["state.stp.tc_count"], k["tc"] = str(int(d["tc"], 16)), 1
    for e in d.get("ports", []):
        s, c = "state." + _ent(e.get("p")) + ".", e.get("c", "")
        if len(c) == 36:
            for i, f in enumerate(("rx_bpdu", "tx_bpdu", "rx_tc", "tx_tc")):
                v[s + f] = str(int(c[8 * i:8 * i + 8], 16))
            v[s + "bpdu_age_ms"] = str(int(c[32:], 16) * 20)
        else:
            rest.append(f"entity {e.get('p')} c {c!r}")
        _left(e, {"p", "c"}, rest, f"entity {e.get('p')} ")
    _left(d, set(k) | {"ports"}, rest, "")


def _stpcnt_post(v, m, r):
    """The active entities; the BPDU ages in whole seconds: get reads them a
    step or two after the page would (20 ms ticks)."""
    return {p: (str(int(x) // 1000) + " s" if p.endswith(".bpdu_age_ms") else x)
            for p, x in _active_ents(v, m, r).items() if not p.endswith(".members")}


page(r"/stpcnt\.json", Read("state.stp", ["stp.enabled", "state.stp.tc_count", "state.stp.(port|lag).#.(rx_bpdu|tx_bpdu|rx_tc|tx_tc|bpdu_age_ms)",
                             "state.stp.lag.#.members"], _json(_stpcnt), post=_stpcnt_post))


def _lag_json(d, v, rest, m, r):
    """/lag.json: per LAG (0-based) its type (static also for a LAG
    without members: mode off), members (configured), and for LACP the
    active members, valid, aggregator; the hash (raw register, hex); per
    port its LAG (255 none, else 0-based), the actor and partner states,
    receive state, LACPDUs in, partner system."""
    for e in d.get("lags", []):
        L, k = e.get("lagNum", 0) + 1, {"lagNum": 1}
        mem = plist(e.get("members", []))
        v[f"lag.{L}.members"], k["members"] = mem, 1
        v[f"lag.{L}.mode"], k["type"] = "lacp" if e.get("type") == "lacp" else "static" if mem != "none" else "off", 1
        for key, p, f in [("active_members", f"state.lag.{L}.active", plist), ("valid", f"state.lag.{L}.agg_valid", onoff),
                          ("aggregator", f"state.lag.{L}.aggregator", mac),
                          ("hash", f"lag.{L}.hash", lambda x: bits(int(x, 16), HASH))]:
            if key in e:
                v[p], k[key] = f(e[key]), 1
        _left(e, k, rest, f"lag {L} ")
    for e in d.get("ports", []):
        s, k = f"state.lacp.port.{e.get('p')}.", {"p": 1}
        for key, p, f in [("lag", s + "lag", lambda x: "0" if x == 255 else str(x + 1)),
                          ("a", s + "actor", lambda x: bits(int(x, 16), ACTOR)),
                          ("pt", s + "partner", lambda x: bits(int(x, 16), ACTOR)),
                          ("rs", s + "rx_state", lambda x: RXST[x]), ("rx", s + "rx_pdus", lambda x: str(int(x, 16))),
                          ("psys", s + "partner_sys", mac)]:
            if key in e:
                v[p], k[key] = f(e[key]), 1
        _left(e, k, rest, f"port {e.get('p')} ")
    _left(d, {"lags", "ports"}, rest, "")


def _lag_post(v, m, r):
    """Active, valid and aggregator only for a LACP LAG."""
    return {p: x for p, x in v.items() if not (re.fullmatch(r"state\.lag\.\d\.\w+", p) and
                                              v.get(f"lag.{p.split('.')[2]}.mode") != "lacp")}


page(r"/lag\.json", Read("lag state.lag", ["lag.#.(members|mode|hash)", "state.lag.#.(active|agg_valid|aggregator)",
                          "state.lacp.port.#.(lag|actor|partner|rx_state|rx_pdus|partner_sys)"], _json(_lag_json), post=_lag_post))


def _loop_json(d, v, rest, m, r):
    """/loop.json: on, STP on, the looped and blocked ports (none when off)."""
    for key, p, f in [("on", "loop.enabled", onoff), ("stp", "stp.enabled", onoff),
                      ("looped", "state.loop.looped", plist), ("blocked", "state.loop.blocked", plist)]:
        if key in d:
            v[p] = f(d[key])
    _left(d, {"on", "stp", "looped", "blocked"}, rest, "")


page(r"/loop\.json", Read("loop state.loop", ["loop.enabled", "stp.enabled", "state.loop.(looped|blocked)"], _json(_loop_json), post=_loop_json_post))


def _l2_json(d, v, rest, m, r):
    """/l2.json?idx=N: up to 30 entries from index N on, wrapping round to
    the first again: VLAN (hex), MAC, type (s/l), port (0 the CPU), LAG
    (0 none), index (hex); keyed by index, a repeat by its count."""
    for e in d:
        i = int(e.get("idx", "-1"), 16)
        key = f"state.l2.{i}"
        while key in v:
            key += "+"
        _l2_entry(v, key, e.get("mac"), int(e.get("vlan", "0"), 16), "cpu" if e.get("port") == 0 else e.get("port"),
                  onoff(e.get("type") == "s"), str(e.get("lag", 0)))
        _left(e, {"vlan", "mac", "type", "port", "lag", "idx"}, rest, f"entry {e.get('idx')} ")


def _l2_page(v, m, r):
    """The new side's table as the page from idx would show it (send_l2():
    from the first entry at or after idx, round the table, until it comes
    back to the first, 30 have gone or the next one's worst case (76 B)
    would not fit outbuf (2,500 B))."""
    e = _l2_entries(v)
    out = {p: x for p, x in v.items() if not re.fullmatch(r"state\.l2\.\d+\.\w+", p)}
    if not e:
        return out
    idx = sorted(e)
    start = int(m[1]) & 0xfff
    first, left, entry = None, 30, start
    slen = 70 + 1                       # HTTP_RESPONSE_JSON, "["
    while True:
        left -= 1
        nxt = next((i for i in idx if i >= entry), idx[0])
        f = e[nxt]
        if slen + 76 > 2500:            # send_l2(): an entry's worst case must fit outbuf
            break
        slen += (slen > 71) + len('{"vlan":"%03x","mac":"%s","type":"l","port":%s,"lag":%s,"idx":"%04x"}' % (
            int(f.get("vlan", "0")), f.get("mac"), "0" if f.get("port") == "cpu" else f.get("port"), f.get("lag", "0"), nxt))
        key = f"state.l2.{nxt}"
        while key in out:
            key += "+"
        _l2_entry(out, key, f.get("mac"), f.get("vlan"), f.get("port"), f.get("static"), f.get("lag", "0"))
        entry = nxt + 1
        if first is None:
            first = entry
        elif first == entry or not left:
            break
    return out


page(r"/l2\.json\?idx=(\d+)", Read("state.l2", ["state.l2.#.(mac|vlan|port|lag|static)"], _json(_l2_json), post=_l2_page))


# --- the pages port.N deleted (P2): /eee.json, /isolation.json

def _eee_json(d, v, rest, m, r):
    """/eee.json: per port SFP or not; copper: the speeds advertised, the
    partner's, active (no 5G/10G words: the old page has none)."""
    for e in d:
        n, k = e.get("port"), {"port": 1}
        for key, p, f in [("isSFP", f"state.port.{n}.media", lambda x: "sfp" if x else "copper"),
                          ("advertised", f"port.{n}.eee_speeds", lambda x: flags(EEE_W[w] for w in x)),
                          ("linkPartner", f"state.port.{n}.eee.lp", lambda x: flags(EEE_W[w] for w in x)),
                          ("active", f"state.port.{n}.eee.active", onoff)]:
            if key in e:
                v[p], k[key] = f(e[key]), 1
        _left(e, k, rest, f"port {n} ")


def _no_5g(p, x, m, r):
    """The page knows no 5G or 10G EEE words."""
    if p.endswith((".eee_speeds", ".eee.lp")):
        return flags(w for w in x.split(",") if w not in ("5g", "10g", "none"))
    return x


page(r"/eee\.json", Read("port", EEE, _json(_eee_json), narrow=_no_5g, post=_media_sfp))


def _isolation(d, v, rest, m, r):
    """/isolation.json: per port the ports it may send to, 0 the CPU."""
    for e in d:
        n = e.get("port")
        if "allow" in e:
            v[f"port.{n}.isolate"] = plist([x for x in e["allow"] if x], 0 in e["allow"])
        _left(e, {"port", "allow"}, rest, f"port {n} ")


page(r"/isolation\.json", Read("port", ["port.#.isolate"], _json(_isolation), gets=lambda m, r, o: ["get port.*.isolate"]))


# --- the pages P4-6 deleted: /bandwidth.json, /storm.json

def _bw_json(d, v, rest, m, r):
    """/bandwidth.json: per port in and out, limited or not, the register's
    20-bit rate field (16 kbit/s steps, hex), and the ingress action."""
    for e in d:
        n, k = e.get("port"), {"port": 1}
        for lim, bw, p in (("iLimited", "iBW", f"port.{n}.bw_in_kbps"), ("eLimited", "eBW", f"port.{n}.bw_out_kbps")):
            if lim in e and bw in e:
                v[p], k[lim], k[bw] = str(int(e[bw], 16) << 4) if e[lim] else "0", 1, 1
        if e.get("iAction") in ("fc", "drop"):
            v[f"port.{n}.bw_in_mode"], k["iAction"] = e["iAction"], 1
        _left(e, k, rest, f"port {n} ")


page(r"/bandwidth\.json", Read("port", ["port.#.(bw_in_kbps|bw_in_mode|bw_out_kbps)"], _json(_bw_json),
     gets=lambda m, r, o: ["get port.*.bw_in_kbps port.*.bw_in_mode port.*.bw_out_kbps"]))


def _storm_json(d, v, rest, m, r):
    """/storm.json: per port and type enabled, the unit (pps, kbit/s) and
    the meter's rate (hex)."""
    for e in d:
        n, k = e.get("port"), {"port": 1}
        for t in STORMS:
            x = e.get(t)
            if not isinstance(x, dict) or not {"enabled", "unit", "rate"} <= set(x):
                continue
            k[t] = 1
            s = f"port.{n}.storm.{t}."
            v[s + "rate"] = str(int(x["rate"], 16)) if x["enabled"] else "0"
            v[s + "unit"] = "kbps" if x["unit"] == "kbit/s" else x["unit"]
        _left(e, k, rest, f"port {n} ")


page(r"/storm\.json", Read("port", ["port.#.storm.(bcast|mcast|ucast|umcast).(rate|unit)"], _json(_storm_json),
     post=lambda v, m, r: _storm_post(v, m, r), gets=lambda m, r, o: ["get port.*.storm"]))

# --- the pages with side effects (P3C: POST only on the new image; the
# old takes GET)

for _page in (r"/reset", r"/cmd_log_clear", r"/l2_del\.json\?idx=\d+"):
    action(_page, "http.post")


# --- the boot's defaults against the hardware (a design call: the boot
# programs only the fields that differ from the schema's defaults, so a
# default the chip's reset or init state doesn't hold is a bug). Each row:
# a config field (N a front port, L a LAG, V a VLAN), its object, its scope
# (port, copper, lag, vlan, global) and how to read it back from rtlsim's
# stored state as the old firmware's setters encode it: f(c, i) with c.regs
# {addr: value}, c.phys {(phy, mmd, reg): value}, c.vlans {vid: word},
# c.l2 [(idx, a, b, c)], c.r (the board), c.running (get's values); i the
# logical port, the LAG or the VID. It gives the value, a tuple of values
# any of which is right, or None: not compared here. Registers never
# written read 0, as in rtlsim.

READBACK = []


def readback(path, obj, f, scope="port"):
    READBACK.append((path, obj, f, scope))


def _reg(c, a):
    return c.regs.get(a, 0)


def _bit(c, a, b):
    return _reg(c, a) >> b & 1


# port.N (live): parse_mtu(), port_pvid_write(), port_ingress_filter(),
# port_ingress_vlan_filter_set(), port_isolate(), port_eee_enable(), the PHY's
# power-down bit (phy_set_speed())
readback("port.N.mtu", "port", lambda c, l: str(_reg(c, 0x1250 + (l << 8)) & 0x3fff))
readback("port.N.pvid", "port", lambda c, l: str(_reg(c, 0x4e1c + (l >> 1 << 2)) >> (12 if l & 1 else 0) & 0xfff))
readback("port.N.ingress", "port", lambda c, l: ("all", "tagged", "untagged", "3")[_reg(c, 0x4e10) >> 2 * l & 3])
readback("port.N.ingress_filter", "port", lambda c, l: onoff(_bit(c, 0x4e18, l)))
readback("port.N.isolate", "port", lambda c, l: lmask(_reg(c, 0x50c0 + (l << 2)) & 0xffff, c.r))
readback("port.N.eee", "port", lambda c, l: onoff(_reg(c, 0x125c + (l << 8)) & 3 == 3), "copper")	# EEE_CTRL rx|tx
readback("port.N.enabled", "port", lambda c, l: onoff(not c.phys.get((l, 0x1f, 0xa610), 0) & 0x0800), "copper")
# not read back: speed and duplex (the PHY's AN and forced registers, as
# phy_set_speed() leaves them), lldp and name (no hardware)

# port.N bandwidth and storm (P4-6): bandwidth_ingress_set() (IGBW_PORT_CTRL
# + 4 port, bit 20 on, the rate >> 4), its flow control bit, the egress at
# + 1024 port; storm_set() (STORM_CTRL + 4 type, the port's bit; meter 24 +
# 4 port + type: METER_RATE, METER_MODE's bit for pps)
STORMS = ("bcast", "mcast", "ucast", "umcast")


def _bw(c, a):
    v = _reg(c, a)
    return str((v & 0xfffff) << 4) if v >> 20 & 1 else "0"


readback("port.N.bw_in_kbps", "port", lambda c, l: _bw(c, 0x4c18 + 4 * l))
readback("port.N.bw_in_mode", "port", lambda c, l: "fc" if _bit(c, 0x4c8c, l) else "drop")
readback("port.N.bw_out_kbps", "port", lambda c, l: _bw(c, 0x1c34 + 1024 * l))
for _t, _name in enumerate(STORMS):
    readback(f"port.N.storm.{_name}.rate", "port", lambda c, l, t=_t: str(_reg(c, 0x5cf0 + (24 + 4 * l + t << 2)) & 0xffffff)
             if _bit(c, 0x54e4 + (t << 2), l) else "0")
    readback(f"port.N.storm.{_name}.unit", "port", lambda c, l, t=_t: ("pps" if _bit(c, 0x5ef0 + ((24 + 4 * l + t) >> 5 << 2),
                                                                                    (24 + 4 * l + t) & 31) else "kbps")
             if _bit(c, 0x54e4 + (t << 2), l) else None)


# vlan.V (P4-2): vlan_create()'s table word: members in bits 0-9 (the CPU
# bit 9, always a tagged member: not in the config's masks), the untagged
# set in bits 10-19; a VID not in the table is absent
def _vlan_word(c, v):
    w = c.vlans.get(v)
    if w is None:
        return None, None
    b1, b2, b3 = w >> 16 & 0xff, w >> 8 & 0xff, w & 0xff
    return (b2 & 3) << 8 | b3, (b1 & 0xf) << 6 | b2 >> 2


readback("vlan.V.members", "vlan", lambda c, v: "<absent>" if _vlan_word(c, v)[0] is None else
         lmask(_vlan_word(c, v)[0] & 0x1ff, c.r), "vlan")
readback("vlan.V.tagged", "vlan", lambda c, v: "<absent>" if _vlan_word(c, v)[0] is None else
         lmask(_vlan_word(c, v)[0] & ~_vlan_word(c, v)[1] & 0x1ff, c.r), "vlan")

# lag.L (P4-3): TRK_MBR_CTRL + 4 (L - 1) (a LACP LAG's holds the active set:
# not config), TRK_HASH_CTRL's low byte
readback("lag.L.members", "lag", lambda c, L: None if c.running.get(f"lag.{L}.mode") == "lacp" else
         lmask(_reg(c, 0x4f38 + (L - 1 << 2)) & 0xffff, c.r), "lag")
readback("lag.L.hash", "lag", lambda c, L: bits(_reg(c, 0x4f48 + (L - 1 << 2)) & 0xff, HASH), "lag")

# mirror (P4-5): MIRROR_CTRL bit 0 and the monitor port above it,
# MIRROR_CONF rx (high half) and tx (low)
readback("mirror.enabled", "mirror", lambda c, i: onoff(_reg(c, 0x6048) & 1), "global")
readback("mirror.port", "mirror", lambda c, i: str(fronts(c.r).get(_reg(c, 0x6048) >> 1 & 0xf, "?"))
         if _reg(c, 0x6048) & 1 else None, "global")
readback("mirror.rx", "mirror", lambda c, i: lmask(_reg(c, 0x604c) >> 16, c.r) if _reg(c, 0x6048) & 1 else None, "global")
readback("mirror.tx", "mirror", lambda c, i: lmask(_reg(c, 0x604c) & 0xffff, c.r) if _reg(c, 0x6048) & 1 else None, "global")

# stp, lldp, igmp, loop (P4-4, P4-5): STP off leaves every port forwarding
# (MSTP_STATES, 2 bits a port, 3); LLDP on traps RMA 0x0e (RMA0E_CONF's
# action bits 0x30: 0x10 trap, 0 forward); IGMP_PORT_CFG trap (0x2a) when
# snooping is on, flood (0x15) when off; RLDP_RLPP's enable bit
readback("stp.enabled", "stp", lambda c, i: ("on", "off") if _reg(c, 0x5310) & 0xfffff == 0xfffff else "on", "global")
readback("lldp.enabled", "lldp", lambda c, i: {0x10: "on", 0: "off"}.get(_reg(c, 0x4ee8) & 0x30, "?"), "global")
readback("igmp.enabled", "igmp", lambda c, i: {0x2a: "on", 0x15: "off"}.get(_reg(c, 0x52a0 + (min(c.r.P) << 2)) & 0x3f, "?"),
         "global")
readback("loop.enabled", "loop", lambda c, i: onoff(_reg(c, 0x106c) & 1), "global")
# sys (P4-1): leds_off()'s LED_PORT_SW_EN (ports off: every bit; the system
# LED is a GPIO, not read here); the static management L2 entry (on the CPU,
# port 9): sys.mac its MAC (auto: compared by state.sys.mac), mgmt.vlan its
# VID (0: VLAN 1)
readback("sys.led", "sys", lambda c, i: ("on",) if not _reg(c, 0x6554) else ("ports_off", "off"), "global")


def _mgmt_entry(c):
    """The static unicast L2 entry on the CPU (port 9): (MAC, VID), or None."""
    for idx, a, b, cc in c.l2:
        port, static = (b >> 30 & 3) | (cc & 3) << 2, cc >> 16 & 1
        mac_ = (b & 0xffff) << 32 | a
        if static and port == 9 and not mac_ >> 40 & 1:
            return ":".join(f"{mac_ >> 8 * k & 0xff:02x}" for k in range(5, -1, -1)), b >> 16 & 0xfff
    return None


readback("sys.mac", "sys", lambda c, i: None if c.running.get("sys.mac") in (None, "auto") else
         (_mgmt_entry(c) or ("<none>",))[0], "global")
readback("mgmt.vlan", "mgmt", lambda c, i: "<none>" if not _mgmt_entry(c) else
         ("0", "1") if _mgmt_entry(c)[1] == 1 else str(_mgmt_entry(c)[1]), "global")
# not read back: mgmt.ip/netmask/gateway/mode, dns, ntp, syslog, web, the
# VLAN names, stp's bridge and per-port fields (RAM only, no register)


def boot_defaults(dump, running, r, om, vlans=(), l2=()):
    """[(path, hardware, running)] where the boot's hardware state and the
    running config differ, and the number of fields compared: the readback
    rows of the objects om has, over their scope."""
    from types import SimpleNamespace
    c = SimpleNamespace(regs={}, phys={}, vlans={}, l2=[], r=r, running={p: path_of(p)[1](x) if path_of(p) else x
                                                                        for p, x in running.items()})
    for l in dump:
        k, a, v = l.split()
        if k == "r":
            c.regs[int(a, 16)] = int(v, 16)
        elif k == "p":
            p, m, g = a.split(".")
            c.phys[(int(p, 16), int(m, 16), int(g, 16))] = int(v, 16)
    for l in vlans:
        vid, w = l.split()
        c.vlans[int(vid, 16)] = int(w, 16)
    for l in l2:
        idx, a, b, cc = (int(x, 16) for x in l.split())
        c.l2.append((idx, a, b, cc))
    copper = set(r.P)
    run_vids = {int(p.split(".")[1]) for p in c.running if re.fullmatch(r"vlan\.\d+\.\w+", p)}
    scopes = {"port": [(l, f".{f}.") for l, f in sorted(fronts(r).items())],
              "copper": [(l, f".{f}.") for l, f in sorted(fronts(r).items()) if l in copper],
              "lag": [(L, f".{L}.") for L in range(1, 5)],
              "vlan": [(v, f".{v}.") for v in sorted(run_vids | set(c.vlans))],
              "global": [(None, None)]}
    bad, n = [], 0
    for path, obj, f, scope in READBACK:
        if obj not in om:
            continue
        for i, sub in scopes[scope]:
            p = path.replace(".N.", sub).replace(".L.", sub).replace(".V.", sub) if sub else path
            hw = f(c, i)
            if hw is None:
                continue
            run = c.running.get(p, "<absent>")
            n += 1
            if run not in (hw if isinstance(hw, tuple) else (hw,)):
                bad.append((p, hw if isinstance(hw, str) else "|".join(hw), run))
    return bad, n


# === P3H: the config commands of the P4 objects (research/config.md 1-13,
# 17), each to set/apply lines on DESIGN 6's paths, with the old command's
# side effects; pending until the object moves. The old parsers they mirror
# are 53ee574's cmd_parser.c, dns_cli.c, ntp_cli.c, rtl837x_stp_cli.c.

def name_char(c):
    """cmd_parser.c name_char(): what the old stores of a name's character."""
    return "." if ord(c) < 0x20 or ord(c) > 0x7e or c in '"\\' else c


def _ip(s):
    """parse_ip(): a.b.c.d, each 0..255, or None."""
    a = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})", s or "")
    return s if a and all(int(x) <= 255 for x in a.groups()) else None


IP = r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"


def _mac(s):
    """parse_mac(): six hex pairs, ':' or '-'; unicast, the first three
    bytes not all zero (else refused)."""
    a = re.fullmatch(r"([0-9a-fA-F]{2})[:-]([0-9a-fA-F]{2})[:-]([0-9a-fA-F]{2})[:-]([0-9a-fA-F]{2})[:-]([0-9a-fA-F]{2})[:-]([0-9a-fA-F]{2})", s)
    if not a:
        return None
    b = [int(x, 16) for x in a.groups()]
    return None if b[0] & 1 or not (b[0] | b[1] | b[2]) else ":".join(f"{x:02x}" for x in b)


def _prev(prev, p, default=None):
    return (prev or {}).get(p, default)


# --- sys, web (P4-1)

row(r"hostname (\S+)", Set("sys", lambda m, r: {"sys.hostname": "".join(map(name_char, m[1]))[:23]}))
row(r"hostname \S+ .*", Refused("sys"))		# a name with spaces: refused
row(r"mac (\S+)", Set("sys.mac", lambda m, r: {"sys.mac": _mac(m[1])} if _mac(m[1]) else {}))
row(r"led on", Set("sys", lambda m, r: {"sys.led": "on"}))
row(r"led off", Set("sys", lambda m, r: {"sys.led": "off"}))
row(r"led off ports", Set("sys", lambda m, r: {"sys.led": "ports_off"}))
row(r"led(?: .*)?", Refused("sys"))
# passwd: the rest of the line, spaces too, up to 20 (a Secret: compared as
# *); apply logs every session out, as session_init() did
row(r"passwd (.+)", Set("web", lambda m, r: {"web.password": m[1][:20]}))
row(r"passwd", Refused("web"))
row(r"session (\d+)", Set("web", lambda m, r: {"web.session_timeout_s": str(int(m[1]))} if 1 <= int(m[1]) <= 65535 else {}))
row(r"session \S+", Refused("web"))		# "Must be 1-65535 s"


@_cli
def _hostname(l, v, m, r):
    v["sys.hostname"] = l
    return True


row(r"hostname", Read("sys", ["sys.hostname"], _hostname, gets=lambda m, r, o: ["get sys.hostname"]))


@_cli
def _mac_show(l, v, m, r):
    a = re.fullmatch(r"Current MAC: ([0-9a-f:]{17})", l)
    if a:
        v["state.sys.mac"] = a[1]
    return bool(a)


row(r"mac", Read("sys.mac", ["state.sys.mac"], _mac_show))


@_cli
def _session(l, v, m, r):
    a = re.fullmatch(r"Session timeout: (\d+) s", l)
    if a:
        v["web.session_timeout_s"] = a[1]
    return bool(a)


row(r"session", Read("web", ["web.session_timeout_s"], _session, gets=lambda m, r, o: ["get web.session_timeout_s"]))


# --- mgmt (P4-1): ip, gw, netmask, vlan N mgmt

# ip <addr> stops DHCP: static with that address. ip dhcp (re)starts DHCP
# every time: dhcp mode, and a renew if it already was (apply starts it if
# not)
row(r"ip " + IP, Set("mgmt", lambda m, r: {"mgmt.mode": "static", "mgmt.ip": m[1]} if _ip(m[1]) else {}))
row(r"ip dhcp", Set("mgmt", lambda m, r: {"mgmt.mode": "dhcp"}, before=lambda m, r: ["mgmt.mode"],
                    then=lambda m, r, prev: ["do mgmt.dhcp_renew"] if _prev(prev, "mgmt.mode") == "dhcp" else []))
row(r"ip \S+", Refused("mgmt"))
row(r"gw " + IP, Set("mgmt", lambda m, r: {"mgmt.gateway": m[1]} if _ip(m[1]) else {}))
row(r"gw \S+", Refused("mgmt"))
row(r"netmask " + IP, Set("mgmt", lambda m, r: {"mgmt.netmask": m[1]} if _ip(m[1]) else {}))
row(r"netmask \S+", Refused("mgmt"))
# vlan N mgmt: moves the management L2 entry; restarts DHCP if it is on and
# the VLAN changed ("Management VLAN unchanged" else)
row(r"vlan (\d+) mgmt", Set("mgmt", lambda m, r: {"mgmt.vlan": str(int(m[1]))} if int(m[1]) <= 4094 else {},
                            before=lambda m, r: ["mgmt.mode", "mgmt.vlan"],
                            then=lambda m, r, prev: ["do mgmt.dhcp_renew"] if _prev(prev, "mgmt.mode") == "dhcp"
                            and _prev(prev, "mgmt.vlan") != str(int(m[1])) else []))


# --- dns, ntp, syslog (P4-1)

# one command sets both servers: the second 0.0.0.0 when absent, and when
# it is bad (the first is set by then, and the old says Error)
row(r"dns server " + IP, Set("dns", lambda m, r: {"dns.server1": m[1], "dns.server2": "0.0.0.0"} if _ip(m[1]) else {}))
row(r"dns server " + IP + " " + IP, Set("dns", lambda m, r: {"dns.server1": m[1], "dns.server2": m[2]}
                                        if _ip(m[1]) and _ip(m[2]) else {"dns.server1": m[1], "dns.server2": "0.0.0.0"},
                                        accept_error=True))
row(r"dns server " + IP + r" \S+.*", Set("dns", lambda m, r: {"dns.server1": m[1], "dns.server2": "0.0.0.0"},
                                          accept_error=True))
row(r"dns server.*", Refused("dns"))
# ntp on starts a sync too: a sync if it was on already (apply starts it if not)
row(r"ntp on", Set("ntp", lambda m, r: {"ntp.enabled": "on"}, before=lambda m, r: ["ntp.enabled"],
                   then=lambda m, r, prev: ["do ntp.sync"] if _prev(prev, "ntp.enabled") == "on" else []))
row(r"ntp off", Set("ntp", lambda m, r: {"ntp.enabled": "off"}))
row(r"ntp server (\S{1,47})", Set("ntp", lambda m, r: {"ntp.server": m[1]}))	# restarts if on: apply's
row(r"ntp interval (\d+)", Set("ntp", lambda m, r: {"ntp.interval_min": str(int(m[1]))} if 1 <= int(m[1]) <= 1440 else {}))


def _tz(s):
    """ntp_cli_timezone(): [+-]h[h][:mm], mm 00/30/45, -12:00..+14:00, as +hh:mm."""
    a = re.fullmatch(r"([+-]?)(\d{1,2})(?::(00|30|45))?", s)
    if not a:
        return None
    v = int(a[2]) * 60 + int(a[3] or 0)
    neg = a[1] == "-"
    if v > (12 * 60 if neg else 14 * 60):
        return None
    return f"{'-' if neg and v else '+'}{v // 60:02d}:{v % 60:02d}"


row(r"ntp timezone (\S+)", Set("ntp", lambda m, r: {"ntp.timezone": _tz(m[1])} if _tz(m[1]) else {}))
row(r"ntp dst (off|eu|us)", Set("ntp", lambda m, r: {"ntp.dst": m[1]}))
row(r"ntp \S+.*", Refused("ntp"))
row(r"syslog (on|off)", Set("syslog", lambda m, r: {"syslog.enabled": m[1]}))
row(r"syslog ip " + IP, Set("syslog", lambda m, r: {"syslog.server": m[1]} if _ip(m[1]) else {}))	# stop, start: apply's
row(r"syslog port (\d+)", Set("syslog", lambda m, r: {"syslog.port": str(int(m[1]))} if 1 <= int(m[1]) <= 65535 else {}))


@_cli
def _syslog(l, v, m, r):
    a = re.fullmatch(r"Current syslog status: (disabled|enabled, sending to ([\d.]+):(\d+))", l)
    if a:
        v["syslog.enabled"] = "off" if a[1] == "disabled" else "on"
        if a[2]:
            v["syslog.server"], v["syslog.port"] = a[2], a[3]
    return bool(a)


row(r"syslog", Read("syslog", ["syslog.(enabled|server|port)"], _syslog,
                    post=lambda v, m, r: {p: x for p, x in v.items() if v.get("syslog.enabled") == "on" or p == "syslog.enabled"}))


# --- vlan.V (P4-2)

def _vlan(m, r):
    """vlan V [name] [port[t]]...: the membership replaced (the CPU always a
    tagged member: not in the config's masks), the name if one is given (a
    word starting with a letter), else kept."""
    v, w = int(m[1]), m[2].split() if m[2] else []
    if not 1 <= v <= 4094:
        return {}
    out, mem, tag = {}, [], []
    if w and w[0][0].isalpha():
        out[f"vlan.{v}.name"] = "".join(map(name_char, w[0]))
        w = w[1:]
    for x in w:
        a = re.fullmatch(r"(\d)(t?)", x)
        if not a:
            return {}
        mem.append(int(a[1]))
        if a[2]:
            tag.append(int(a[1]))
    out[f"vlan.{v}.members"], out[f"vlan.{v}.tagged"] = plist(mem), plist(tag)
    return out


# vlan V d...: any three-word line whose third word starts with d deletes
# (so "vlan 10 dmz" too: cmd_parser.c:724)
row(r"vlan (\d+) d\S*", Set("vlan", lambda m, r: {f"vlan.{int(m[1])}": DEL} if 1 <= int(m[1]) <= 4094 else {}))
row(r"vlan (\d+)((?: \S+)*)", Set("vlan", _vlan))


@_cli
def _vlan_show(l, v, m, r):
    """vlan show: per port (and the CPU: no path) its PVID (hex), the
    acceptable frames, the VLAN filter."""
    if l in ("Ingress VLAN configuration:", "Port\tPVID\tType\tFiltering") or l.startswith(("Type - ", "Filtering - ", "PVID - ")):
        return True
    a = re.fullmatch(r"(\d|CPU)\t0x([0-9a-f]{4})\t(Any|Tagged|Untag\.)\t(Enabled|Disabled)", l)
    if a:
        p = "raw.cpu." if a[1] == "CPU" else f"port.{a[1]}."
        v[p + "pvid"], v[p + "ingress"] = str(int(a[2], 16)), {"Any": "all", "Tagged": "tagged", "Untag.": "untagged"}[a[3]]
        v[p + "ingress_filter"] = onoff(a[4] == "Enabled")
    return bool(a)


row(r"vlan show", Read("vlan", ["port.#.(pvid|ingress|ingress_filter)", "raw.cpu.(pvid|ingress|ingress_filter)"],
                       _vlan_show, gets=lambda m, r, o: ["get port.*.pvid", "get port.*.ingress", "get port.*.ingress_filter"]))


def _vlanlist_json(d, v, rest, m, r):
    """/vlanlist.json: the VLANs that exist, each with its name; before P4-1
    the management VLAN too."""
    for e in d.get("vlan", []):
        v[f"vlan.{e.get('id')}.name"] = e.get("name", "")
        _left(e, {"id", "name"}, rest, f"vlan {e.get('id')} ")
    if "mgmt" in d:
        v["mgmt.vlan"] = str(d["mgmt"])
    _left(d, {"vlan", "mgmt"}, rest, "")


page(r"/vlanlist\.json", Read("vlan", ["vlan.#.name", "mgmt.vlan"], _json(_vlanlist_json),
                              gets=lambda m, r, o: (["get vlan.*.name"] if "vlan" in o else []) +
                              (["get mgmt.vlan"] if "mgmt" in o else [])))


def _vlan_json(d, v, rest, m, r):
    """/vlan.json?vid=V: the members (untagged and tagged), the name, the
    ports whose PVID is V."""
    V = int(m[1])
    u, t = d.get("untagged_members", []), d.get("tagged_members", [])
    v[f"vlan.{V}.members"], v[f"vlan.{V}.tagged"] = plist(u + t), plist(t)
    v[f"vlan.{V}.name"] = d.get("name", "")
    for p in d.get("pvid", []):
        v[f"port.{p}.pvid"] = str(V)
    _left(d, {"untagged_members", "tagged_members", "name", "pvid"}, rest, "")


def _vlan_json_post(v, m, r):
    """only the ports whose PVID is V: the page lists those"""
    return {p: x for p, x in v.items() if not re.fullmatch(r"port\.\d+\.pvid", p) or x == str(int(m[1]))}


page(r"/vlan\.json\?vid=(\d+)", Read("vlan", ["vlan.#.(members|tagged|name)", "port.#.pvid"], _json(_vlan_json),
                                    gets=lambda m, r, o: ([f"get vlan.{int(m[1])}"] if "vlan" in o else []) +
                                    (["get port.*.pvid"] if "port" in o else []),
                                    post=_vlan_json_post))


# --- lag.L (P4-3)

def _lacp_take(m, r, prev, L, ports):
    """lacp_lag_set(): a port joining a LACP LAG leaves any other LACP LAG
    (one left empty goes off). From the new side's lag.*.mode/members."""
    out = {}
    for k in range(1, 5):
        if k == L or _prev(prev, f"lag.{k}.mode") != "lacp":
            continue
        rest = [int(x) for x in n_pmask(_prev(prev, f"lag.{k}.members", "none")).split(",") if x.isdigit()]
        keep = [x for x in rest if x not in ports]
        if keep != rest:
            out[f"lag.{k}.members"] = plist(keep)
            if not keep:
                out[f"lag.{k}.mode"] = "off"
    return out


LAGS = lambda m, r: [f"lag.{k}.{f}" for k in range(1, 5) for f in ("mode", "members")]
row(r"lag ([1-4]) d", Set("lag", lambda m, r: {f"lag.{m[1]}.mode": "off", f"lag.{m[1]}.members": "none"}))
row(r"lag ([1-4]) lacp off", Set("lag", lambda m, r, prev: {f"lag.{m[1]}.mode": "off", f"lag.{m[1]}.members": "none"}
                                 if _prev(prev, f"lag.{m[1]}.mode") == "lacp" else {},
                                 before=lambda m, r: [f"lag.{m[1]}.mode"], status=True))
row(r"lag ([1-4]) lacp((?: \d)+)", Set("lag", lambda m, r, prev: {f"lag.{m[1]}.mode": "lacp", f"lag.{m[1]}.members":
                                                                  plist(int(x) for x in m[2].split()),
                                                                  **_lacp_take(m, r, prev, int(m[1]), [int(x) for x in m[2].split()])},
                                       before=LAGS, status=True))
# static: lacp_lag_set(L, 0), then the members, with no overlap check (the
# new apply refuses an overlap: a difference by design, DESIGN 6.20.4)
row(r"lag ([1-4])((?: \d)+)", Set("lag", lambda m, r: {f"lag.{m[1]}.mode": "static",
                                                       f"lag.{m[1]}.members": plist(int(x) for x in m[2].split())}))
row(r"lacp off", Set("lag", lambda m, r, prev: {p: x for k in range(1, 5) if _prev(prev, f"lag.{k}.mode") == "lacp"
                                                for p, x in ((f"lag.{k}.mode", "off"), (f"lag.{k}.members", "none"))},
                     before=LAGS, status=True))
row(r"laghash ([1-4])((?: (?:spa|smac|dmac|sip|dip|sport|dport))+)",
    Set("lag", lambda m, r: {f"lag.{m[1]}.hash": flags(m[2].split())}))
row(r"lag(?: \S+.*)?|laghash.*", Refused("lag"))


# --- stp, stp.port.N, stp.lag.L (P4-4)

STP_G = {"prio": ("stp.priority", 0, 15), "hello": ("stp.hello_s", 1, 10), "maxage": ("stp.maxage_s", 6, 40),
         "fwd": ("stp.fwd_delay_s", 4, 30), "txhold": ("stp.txhold", 1, 10)}
row(r"stp (on|off)", Set("stp", lambda m, r: {"stp.enabled": m[1]}))
row(r"stp (prio|hello|maxage|fwd|txhold) (\d+)", Set("stp", lambda m, r: {STP_G[m[1]][0]: str(int(m[2]))}
                                                      if STP_G[m[1]][1] <= int(m[2]) <= STP_G[m[1]][2] else {}))
row(r"stp version (rstp|stp)", Set("stp", lambda m, r: {"stp.version": m[1]}))
row(r"stp pathcost (long|short)", Set("stp", lambda m, r: {"stp.pathcost": m[1]}))
row(r"stp bpdu (filter|flood)", Set("stp", lambda m, r: {"stp.bpdu": m[1]}))
row(r"stp clear", Set("stp", lambda m, r: {}, then=lambda m, r, prev: ["do stp.counters_clear"], status=True))
STP_E = r"stp (port|lag) (\d)"


def _ent_path(m):
    return f"stp.{m[1]}.{m[2]}"


# on also clears a tripped guard and rejoins (listening): no path for it;
# a port in a LAG is refused on the old, kept with no effect on the new
# (DESIGN 6.8: a difference by design)
row(STP_E + r" (on|off)", Set("stp", lambda m, r: {_ent_path(m) + ".enabled": m[3]}))
row(STP_E + r" mcheck", Set("stp", lambda m, r: {}, then=lambda m, r, prev: [f"do {_ent_path(m)}.mcheck"], status=True))
row(STP_E + r" edge (on|off|auto)", Set("stp", lambda m, r: {_ent_path(m) + ".edge": m[3]}))
row(STP_E + r" cost (\d+)", Set("stp", lambda m, r: {_ent_path(m) + ".cost": str(int(m[3]))} if int(m[3]) <= 200000000 else {}))
row(STP_E + r" p2p (auto|on|off)", Set("stp", lambda m, r: {_ent_path(m) + ".p2p": m[3]}))
row(STP_E + r" prio (\d+)", Set("stp", lambda m, r: {_ent_path(m) + ".priority": str(int(m[3]))}
                                if int(m[3]) <= 240 and not int(m[3]) & 15 else {}))
row(STP_E + r" guard (none|bpdu|root)", Set("stp", lambda m, r: {_ent_path(m) + ".guard": m[3]}))
row(STP_E + r" filter (on|off)", Set("stp", lambda m, r: {_ent_path(m) + ".filter": m[3]}))
row(r"stp(?! status).*", Refused("stp"))


# --- igmp, lldp, loop, mirror (P4-5)

row(r"igmp (on|off)", Set("igmp", lambda m, r: {"igmp.enabled": m[1]}))	# off re-runs the setup: apply's
row(r"lldp on", Set("lldp", lambda m, r: {"lldp.enabled": "on"}))
row(r"lldp(?: .*)?", Set("lldp", lambda m, r: {"lldp.enabled": "off"}))	# every other lldp line disables it
row(r"loop (on|off)", Set("loop", lambda m, r: {"loop.enabled": m[1]}))


def _mirror(m, r):
    """mirror <monitor> <p>[r|t]...: a port with no suffix (or another
    letter) is mirrored both ways, r received, t sent."""
    rx, tx = [], []
    for w in m[2].split():
        a = re.fullmatch(r"(\d)(\D?)", w)
        if not a:
            return {}
        if a[2] != "t":
            rx.append(int(a[1]))
        if a[2] != "r":
            tx.append(int(a[1]))
    return {"mirror.enabled": "on", "mirror.port": m[1], "mirror.rx": plist(rx), "mirror.tx": plist(tx)}


row(r"mirror off", Set("mirror", lambda m, r: {"mirror.enabled": "off"}))
row(r"mirror (\d)((?: \S+)*)", Set("mirror", _mirror))


@_cli
def _mirror_status(l, v, m, r):
    a = re.fullmatch(r"(NOT )?Enabled: Mirroring port: (\d+), Port mask RX: 0x([0-9a-f]{4}), Port mask TX: 0x([0-9a-f]{4})", l)
    if a:
        v["mirror.enabled"] = onoff(not a[1])
        v["mirror.port"], v["mirror.rx"], v["mirror.tx"] = a[2], lmask(int(a[3], 16), r), lmask(int(a[4], 16), r)
    return bool(a)


row(r"mirror status", Read("mirror", ["mirror.(enabled|port|rx|tx)"], _mirror_status,
                           post=lambda v, m, r: v if v.get("mirror.enabled") == "on" else {"mirror.enabled": "off"}))
row(r"mirror.*", Refused("mirror"))


def _mirror_json(d, v, rest, m, r):
    """/mirror.json: on, the mirror port (front number), the rx and tx
    sources; the registers whatever on says (post: only on when off)."""
    if "enabled" in d:
        v["mirror.enabled"] = onoff(d["enabled"])
    if "mirrorPort" in d:
        v["mirror.port"] = str(d["mirrorPort"])
    for key, p in (("mirror_rx", "mirror.rx"), ("mirror_tx", "mirror.tx")):
        if key in d:
            v[p] = plist(d[key])
    _left(d, {"enabled", "mirrorPort", "mirror_rx", "mirror_tx"}, rest, "")


page(r"/mirror\.json", Read("mirror", ["mirror.(enabled|port|rx|tx)"], _json(_mirror_json),
                            post=lambda v, m, r: v if v.get("mirror.enabled") == "on" else {"mirror.enabled": "off"}))


def _lldp_json(d, v, rest, m, r):
    """/lldp.json: on, and per port its name and whether LLDP is permitted."""
    if "on" in d:
        v["lldp.enabled"] = onoff(d["on"])
    for e in d.get("portStatus", []):
        P = e.get("port")
        if "name" in e:
            v[f"port.{P}.name"] = e["name"]
        if "permitted" in e:
            v[f"port.{P}.lldp"] = "permit" if e["permitted"] else "block"
        _left(e, {"port", "name", "permitted"}, rest, f"port {P} ")
    _left(d, {"on", "portStatus"}, rest, "")


page(r"/lldp\.json", Read("lldp", ["lldp.enabled", "port.#.(name|lldp)"], _json(_lldp_json),
                          gets=lambda m, r, o: (["get lldp"] if "lldp" in o else []) +
                          (["get port.*.name", "get port.*.lldp"] if "port" in o else [])))


@_cli
def _igmp_show(l, v, m, r):
    """igmp show: each port's IGMP_PORT_CFG (logical port N), as
    state.dbg.igmp.port.<front>.cfg (P7)."""
    if l == "igmp_show called":
        return True
    a = re.fullmatch(r"(\d):(0x[0-9a-f]{8})", l)
    f = fronts(r)
    if a and int(a[1]) in f:
        v[f"state.dbg.igmp.port.{f[int(a[1])]}.cfg"] = a[2]
    return bool(a)


row(r"igmp show", Read("state.dbg", ["state.dbg.igmp.port.#.cfg"], _igmp_show,
                       gets=lambda m, r, o: ["get state.dbg.igmp.port"]))
row(r"igmp(?! show).*", Refused("igmp"))


# --- port.N: bandwidth, storm, SFP speed (P4-6)

def _bw_rate(s):
    """bw's hex kbit/s (up to 4 bytes): the register keeps it in 16 kbit
    steps (>> 4), so the value is what it holds."""
    return str(int(s, 16) & ~15) if re.fullmatch(r"[0-9a-fA-F]{1,8}", s) else None


# bandwidth_ingress_set() turns flow control on: a rate means mode fc
row(r"bw in (\d) ([0-9a-fA-F]{1,8})", Set("port", lambda m, r: {f"port.{m[1]}.bw_in_kbps": _bw_rate(m[2]),
                                                                        f"port.{m[1]}.bw_in_mode": "fc"}))
row(r"bw in (\d) off", Set("port", lambda m, r: {f"port.{m[1]}.bw_in_kbps": "0"}))
row(r"bw in (\d) (fc|drop)", Set("port", lambda m, r: {f"port.{m[1]}.bw_in_mode": m[2]}))
row(r"bw out (\d) ([0-9a-fA-F]{1,8})", Set("port", lambda m, r: {f"port.{m[1]}.bw_out_kbps": _bw_rate(m[2])}))
row(r"bw out (\d) off", Set("port", lambda m, r: {f"port.{m[1]}.bw_out_kbps": "0"}))


@_cli
def _bw_status(l, v, m, r):
    """bw status N: in and out, disabled or the rate (hex, a literal 0 after:
    the register's 16 kbit steps)."""
    a = re.fullmatch(r"(ingress|egress): (?:disabled|enabled: 0x([0-9a-f]{6})0)", l)
    if a:
        v[f"port.{m[1]}.bw_{'in' if a[1] == 'ingress' else 'out'}_kbps"] = str(int(a[2], 16) << 4) if a[2] else "0"
    return bool(a)


row(r"bw status (\d)", Read("port", ["port.#.(bw_in_kbps|bw_out_kbps)"], _bw_status,
                            gets=lambda m, r, o: [f"get port.{m[1]}.bw_in_kbps", f"get port.{m[1]}.bw_out_kbps"]))
row(r"bw.*", Refused("port"))
STORM_T = r"(bcast|mcast|ucast|umcast)"
row(r"storm (\d) " + STORM_T + r" (\d+) (pps|kbps)",
    Set("port", lambda m, r: {f"port.{m[1]}.storm.{m[2]}.rate": str(int(m[3])), f"port.{m[1]}.storm.{m[2]}.unit": m[4]}
        if 1 <= int(m[3]) <= (0xfffff if m[4] == "pps" else 10000000) else {}))
row(r"storm (\d) " + STORM_T + r" off", Set("port", lambda m, r: {f"port.{m[1]}.storm.{m[2]}.rate": "0"}))


@_cli
def _storm_show(l, v, m, r):
    """storm: per port, each type off or its meter rate (hex) and unit."""
    a = re.fullmatch(r"port (\d)((?: (?:bcast|mcast|ucast|umcast) (?:off|[0-9a-f]{6} (?:pps|kbps)))+)", l)
    if not a:
        return False
    for t, x in re.findall(r"(bcast|mcast|ucast|umcast) (off|[0-9a-f]{6} (?:pps|kbps))", a[2]):
        s = f"port.{a[1]}.storm.{t}."
        if x == "off":
            v[s + "rate"] = "0"
        else:
            v[s + "rate"], v[s + "unit"] = str(int(x.split()[0], 16)), x.split()[1]
    return True


def _storm_post(v, m, r):
    """A unit only for a type that is on."""
    return {p: x for p, x in v.items() if not (p.endswith(".unit") and v.get(p[:-4] + "rate") == "0")}


row(r"storm", Read("port", ["port.#.storm.(bcast|mcast|ucast|umcast).(rate|unit)"], _storm_show, post=_storm_post))
row(r"storm.*", Refused("port"))		# "usage: storm ..."


def _sfp_speed(m, r):
    s = int(m[1])
    return {f"port.{r.cages[s][1]}.speed": m[2]} if s in r.cages else {}


# sfp S <speed> (DESIGN 20.2: the SFP port's port.N.speed): apply finds the
# module again and re-reads it, as the old command did
row(r"sfp ([12]) (10g|2g5|1g|100m|auto)", Set("port", _sfp_speed))
row(r"sfp \S+.*", Refused("port"))
