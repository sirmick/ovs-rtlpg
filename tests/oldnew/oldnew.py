#!/usr/bin/env python3
"""oldnew.py - the old-vs-new harness (rtlpg-sched doc/sched/testing.md,
layer 4): the same scenarios on two firmware images, in rtlsim --control,
with the same inputs at the same firmware times, compared on results.

    tests/oldnew/oldnew.py --old IMAGE --new IMAGE [--board FILE] [--only NAME...]
                           [--expect FILE] [--payload FILE] [--save DIR] [-j N]
                           [--rtlsim build/rtlsim]

IMAGEs are boot images (the loader and the application). Each scenario
(scenarios.py) runs once on each, in its own rtlsim. Its results, by name:

- "<n> cli <command>": what the command printed, up to the prompt;
- "<n> web <method> <path>": the response's status, headers (the session
  cookie's value left out) and body; a body that isn't JSON or text is its
  length and SHA-256;
- "<n> boot": the boot's console output; "<n> note": what a scenario says;
- "<n> vlan table", "<n> l2 table": the tables at that point;
- at the end: "reg <addr>", "sds <id>.<page>.<reg>", "phy <phy>.<mmd>.<reg>"
  (the switch core's stored registers, less the window the loader writes the
  application's code through), "vlan <vid>", "l2 <idx>" (the tables),
  "flash config" (the saved config, 0x7f000, as text; less the sector's last
  two bytes, the image CRC the default config's copy brings along), and
  "frames <port> <kind>" (frames the chip sent, counted; bench.kind()).

Every result has the builds' identity scrubbed from it (the version string,
"v0.1.0-<hash>[-dirty]", and build dates), so that two builds of the same
source match. A name only one side has is a difference too. --expect FILE lists the
differences a change is meant to make, one per line:

    <scenario glob>[,<scenario glob>...] <name glob> [-/RE/|+/RE/]...   # why

(fnmatch patterns; '#' starts a comment; -/RE/ and +/RE/ the lines the
result may lose and gain, load_expect()). Those are reported as expected and
don't fail the run; a line that matched no difference is flagged (it may be
stale), without failing it. Exit 0 when every scenario matches, 1 otherwise. --save
DIR writes each side's results, as JSON, for a look.

--payload is the image the upload scenarios upload (default: the
application image next to --old); --new-payload, NEW's (its own application,
say). The run is the same every time: old against old must match on every
scenario.

The new-only checks (checks.py) are measured on each side and printed under
each scenario, not compared; --checks makes NEW's failures fail the run,
--known FILE lists today's (as --expect's format: <scenario glob> <check glob>).
"""
import argparse, difflib, fnmatch, glob, hashlib, json, os, re, struct, sys, time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bench import Bench, Host, ip_bytes, csum, PROMPT
from lacp_partner import LacpPartner
from scenarios import SCENARIOS
import translate

CONFIG_START, CONFIG_LEN = 0x7f000, 0x1000	# layout.h, compact
DEFAULT_BOARD = os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json")	# rtlsim's own default
# The register window the loader writes the application's common code
# through (0x80b0 + 4 i, sys/system.cpp model_code_ram_load()): the program,
# not the switch's state, so not a result.
CODE_LOAD = range(0x80b0, 0x80b0 + 4 * 0x1000)
PASSWORD = "1234"



LINK_LINE = re.compile(r"\n?<new link: [0-9a-f]+, was [0-9a-f]+>\n")


def _link_first(t):
    """An SFP module's event and its port's link change, at one moment: two
    tasks (sfp, link) report them, and which prints first is the boot's
    timing phase (a 5 ms nudge flips feat/om 0aa0752 on the SWGT024). Only
    in that pair, the link lines go first."""
    if "<MODULE " not in t or not LINK_LINE.search(t):
        return t
    return "".join(LINK_LINE.findall(t)) + LINK_LINE.sub("", t)

class Run:
    """What a scenario does to one image, and what came back."""

    def __init__(self, image, board, rtlsim, payload, om=(), oms=()):
        self.b = Bench(image, board=board, rtlsim=rtlsim)
        self.om = set(om)		# the objects this image has on the object model (translate.py)
        self.oms = [set(x) for x in oms] or [self.om]	# both images' (a value both can show is compared)
        self.canon = set().union(*self.oms)	# those compared by value: either image's
        self.payload = open(payload, "rb").read() if payload else b""
        self.checks = []		# (name, [violations], what was measured, worst µs, hard, saturated): not results
        self.replies = {}		# "cli <line>" -> its longest reply, firmware µs from typed to prompt
        # the front ports, as the board has them (rtlsim's default: the SWTG024AS)
        ports = json.load(open(board or DEFAULT_BOARD))["ports"]
        copper = [p for p in ports if "sfp" not in p]
        self.P = [p["phys"] for p in copper]		# physical port of copper front port i
        self.F = [p["front"] for p in copper]		# its name on the CLI
        self.cages = {p["sfp"]["slot"]: (p["phys"], p["front"]) for p in ports if "sfp" in p}
        self.t = 0
        self.n = 0
        self.booted = False
        self.results = {}
        self.host = None
        self.cookie = b""
        self.b.link(self.P[0], True)	# the host's cable

    def _key(self, what):
        k = f"{self.n:03d} {what}"
        self.n += 1
        return k

    def _console(self, text=None):
        """What the UART said between steps, unasked (link changes, protocol
        events, a reply's trailing lines): a result of its own, "<n> console",
        n the step it came before. Compared as it is, in order."""
        text = self.b.pending() if text is None else text
        if text:
            k = f"{self.n:03d} console"
            self.results[k] = _link_first(self.results.get(k, "") + text)

    def _slot(self, ms):
        start = self.t
        self.b.advance(start)
        self._console()
        self.t = start + int(ms * 1000)
        return self.t

    def wait(self, ms):
        self.t += int(ms * 1000)

    def _now(self):
        self.b.advance(self.t)

    # --- the console
    def boot(self, slot=30000):
        """From power-on or a reset: the boot's output to the prompt; then
        the switch's address, for the host."""
        until = self._slot(slot)
        out = self.b.wait_uart(PROMPT, until)
        self.results[self._key("boot")] = out if out is not None else "<timeout>"
        self.b.advance(self.b.now + 10000)	# cmd_editor_init() drops input before it
        # the harness's own question, not a result: the effective address
        # (state.mgmt, P5-3), else the configured one (mgmt, P4-1: its ip
        # printer goes before state.mgmt comes), else the old ip
        q = "get state.mgmt.ip" if "state.mgmt" in self.om else "get mgmt.ip" if "mgmt" in self.om else "ip"
        before, ip = self._type(q, until)
        self._console(before)
        m = re.search(r"(?:Current IP:|(?:state\.)?mgmt\.ip) ([\d.]+)", ip or "")
        sw = m.group(1) if m else "192.168.10.247"
        if not self.booted:
            self.booted = True
            self.boot_defaults(until)
        if self.host is None:
            self.host = Host(self.b, self.P[0], ip=sw.rsplit(".", 1)[0] + ".1", switch_ip=sw)
        self.host.sw_ip = ip_bytes(sw)
        self.host.sw_mac = None

    def boot_defaults(self, until):
        """The new-only check "boot defaults" after the first boot, on an
        image with objects on the object model: every config field the
        hardware holds (translate.READBACK) as the boot left the registers
        against get's running value. Not a result: get's lines aren't
        typed on the other image."""
        objs = {o for _, o, _, _ in translate.READBACK} & self.om
        if not objs:
            return
        running = ""
        for g in sorted({x for o in objs for x in translate.GETS.get(o, [o]) if not x.startswith("state.")}):
            _, out = self._type(f"get {g}", until)
            running += out or ""
        bad, n = translate.boot_defaults(self.b.reg_dump(), translate.values(running), self, objs,
                                         self.b.table("vlan"), self.b.table("l2"))
        self.checks.append(("boot defaults", [f"{p}: hardware {h}, running {x}" for p, h, x in bad],
                            f"{n} fields of {' '.join(sorted(objs))} against the registers the boot left", None, [], None))

    def _type(self, line, until):
        """b.console(), and its reply's firmware time, typed to prompt, kept
        in self.replies as "cli <line>": a measure for the checks (`by
        cli`), not a result. Every line the console is given goes through
        here, the harness's own ("ip", "health tasks") too: a reply holds
        every step its command ran, so each may be the one that saturated
        a long step, and counting more only makes the bound stricter."""
        start = self.b.now
        before, out = self.b.console(line, until)
        if out is not None:
            k = f"cli {line}"
            self.replies[k] = max(self.replies.get(k, 0), self.b.now - start)
        return before, out

    def cli(self, line, slot=5000):
        until = self._slot(slot)
        t = translate.find(line, self.canon)
        out = self._typed(line, until) if t is None else self._translated(line, until, *t)
        self.results[self._key(f"cli {line}")] = out if out is not None else "<timeout>"

    def _typed(self, line, until):
        before, out = self._type(line, until)
        self._console(before)
        return out

    def _translated(self, line, until, m, what):
        """An old line whose object is compared by value (translate.py): on
        an image that has it on the object model, what the row types
        instead; on either, the result as values by path."""
        if isinstance(what, translate.Read):
            return self._read(m, what, lambda: self._typed(line, until), until)
        new = bool(what.objs & self.om)
        if isinstance(what, translate.Refused):
            if new:
                return "refused"
            out = self._typed(line, until)
            return "refused" if out is not None and translate.refused(out) else out
        if not new:
            out = self._typed(line, until)
            if out is None:
                return None
            if translate.refused(out) and not what.accept_error:
                return "refused"
            return "ok" if what.status else translate.canon(translate.normalized(
                {p: "<deleted>" if x is translate.DEL else x for p, x in what.vals(m, self).items()}))
        prev = None
        if what.before:
            text = ""
            for p in what.before(m, self):
                out = self._typed(f"get {p}", until)
                if out is None:
                    return None
                text += out
            prev = translate.normalized(translate.values(text))
        v = what.vals(m, self, prev)
        if not v and not what.status:
            return "refused"		# the row's parser refuses it, as the old does
        lines = [f"del {p}" if x is translate.DEL else f"set {p} {x}" for p, x in v.items()] + (["apply"] if v else [])
        for i, l in enumerate(lines + (what.then(m, self, prev) if what.then else [])):
            out = self._typed(l, until)
            if out is None:
                return None
            if translate.refused(out):
                if i < len(lines):
                    self._typed("discard", until)	# as the old image: refused, nothing changed
                return "refused"
        if what.status:
            return "ok"
        out = {}
        for p, x in v.items():
            text = self._typed(f"get {p}", until)
            if text is None:
                return None
            if x is translate.DEL:
                out[p] = "<deleted>" if translate.refused(text) or not translate.values(text) else "<still there>"
            else:
                out.update(translate.values(text))
        return translate.canon(translate.normalized(out))

    def _read(self, m, what, fetch, until):
        """A Read row (a printer or a page) on this image: from the old
        surface, unless it is gone here (fetch() its text), the values of the
        objects not moved here; from get, those moved; then only what both
        images can show."""
        moved = what.objs & self.om
        v, rest = {}, []
        if not what.gone(self.om):
            text = fetch()
            if text is None:
                return None
            got, rest = what.parse(text, m, self)
            v.update({p: x for p, x in got.items() if translate.obj_of(p) not in moved})
        if moved:
            text = ""
            for l in what.gets(m, self, moved):
                out = self._typed(l, until)
                if out is None:
                    return None
                text += out
            got, more = what.new(text, m, self, moved)
            v.update(got)
            rest += more
        v = what.post(v, m, self)
        return translate.canon({p: x for p, x in v.items() if what.comparable(p, self.oms)},
                               what.not_compared(self.oms) + rest)

    def tables(self):
        """The VLAN and L2 tables now, as one result each."""
        self._now()
        self._console()
        self.results[self._key("vlan table")] = "\n".join(self.b.table("vlan"))
        self.results[self._key("l2 table")] = "\n".join(self.b.table("l2"))

    def frames(self, kind):
        """The frames of `kind` (bench.kind()) the chip has sent so far, per
        port: a result, compared exactly."""
        self._now()
        self._console()
        self.results[self._key(f"frames {kind}")] = " ".join(
            f"{p}:{n}" for (p, k), n in sorted(self.b.frames.items()) if k == kind)

    def lldp_frames(self):
        """The LLDP frames the chip has sent so far, decoded (lldp_text()):
        per port, in order, each run of identical frames once with its
        count."""
        self._now()
        self._console()
        out = []
        for port in sorted({p for p, k, _ in self.b.kept if k == "lldp"}):
            runs = []
            for p, k, f in self.b.kept:
                if p == port and k == "lldp":
                    t = lldp_text(f)
                    if runs and runs[-1][0] == t:
                        runs[-1][1] += 1
                    else:
                        runs.append([t, 1])
            for t, n in runs:
                out.append(f"port {port}: {n} x\n{t}")
        self.results[self._key("lldp frames")] = "\n".join(out)

    def note(self, text):
        self._console()
        self.results[self._key("note")] = text

    # --- the web
    def _http(self, what, req, slot):
        r = self.host.http(req, self._slot(slot))
        self.results[self._key(f"web {what}")] = norm(r)
        return r

    def login(self, slot=3000):
        body = f"pwd={PASSWORD}".encode()
        r = self._http("POST /login", b"POST /login HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                       b"Content-Length: %d\r\n\r\n" % len(body) + body, slot)
        m = re.search(rb"session=(\w+)", r) if isinstance(r, bytes) else None
        self.cookie = m.group(1) if m else b""

    def get(self, path, login=True, slot=5000):
        h = b"Cookie: session=" + self.cookie + b"\r\n" if login and self.cookie else b""
        req = f"GET {path} HTTP/1.1\r\n".encode() + h + b"\r\n"
        if translate.by_post(path, self.om):	# an action the image takes by POST only (its result
            # keeps the GET's name, so the two images' line up)
            req = f"POST {path} HTTP/1.1\r\n".encode() + h + b"Content-Length: 0\r\n\r\n"
        t = translate.find_page(path, self.canon) if login else None
        if t is None:
            self._http(f"GET {path}", req, slot)
            return
        until = self._slot(slot)		# a page compared by value (translate.py)
        out = self._read(*t, lambda: norm(self.host.http(req, until)), until)
        self.results[self._key(f"web GET {path}")] = out if out is not None else "<timeout>"

    def _post(self, path, ctype, body, slot):
        req = (f"POST {path} HTTP/1.1\r\nCookie: session=".encode() + self.cookie +
               f"\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        self._http(f"POST {path}", req, slot)

    def post_cmd(self, cmd, slot=3000):
        if translate.find(cmd, self.canon):
            raise NotImplementedError(f"POST /cmd {cmd!r}: its object is on the object model; translate it (translate.py)")
        self._post("/cmd", "text/plain;charset=UTF-8", cmd.encode(), slot)

    def _multipart(self, path, field, name, data, slot):
        bnd = "----oldnew0123456789"
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bnd}--\r\n".encode()
        self._post(path, f"multipart/form-data; boundary={bnd}", body, slot)

    def post_config(self, text, slot=5000):
        self._multipart("/config", "configuration", "config.txt", self.convert(text).encode(), slot)

    def convert(self, text):
        """A saved config for this image: the settings of objects it has moved
        as set lines (translate.py's Set rows) and an apply; the harness's
        stand-in for rtlpg_client.py convert (P3B)."""
        out, sets = [], False
        for l in text.splitlines():
            t = translate.find(l, self.om)
            if t and isinstance(t[1], translate.Set):
                out += [f"set {p} {x}" for p, x in t[1].values(t[0], self).items()]
                sets = True
            else:
                out.append(l)
        return "\n".join(out + (["apply"] if sets else [])) + ("\n" if text.endswith("\n") else "")

    def post_upload(self, data, slot=60000):
        self._multipart("/upload", "uploadedfile", "rtlplayground.bin", data, slot)

    # --- the outside
    def link(self, port, up=True, speed=None):
        self._now()
        self.b.link(port, up, speed)

    def cable(self, a, b):
        self._now()
        self.b.link(a, True)
        self.b.link(b, True)
        self.b.cable(a, b)

    def uncable(self, a):
        self._now()
        self.b.uncable(a)

    def partner(self, ports, short=True):
        """An LACP partner (lacp_partner.py) on these physical ports; short:
        it asks for the short timeout, else the long one."""
        self._now()
        self.lacp = LacpPartner(self.b, ports, short=short)
        return self.lacp

    def partner_view(self):
        """What the partner sees: its own state and the switch's, per port."""
        self._now()
        self._console()
        p = self.lacp
        self.results[self._key("lacp partner")] = "\n".join(
            f"{x}: actor {p.state(x):02x} partner {p.st[x]['partner']['state'] if p.st[x]['partner'] else 0:02x}"
            f" in {p.pdus_in[x]} out {p.pdus_out[x]}" for x in p.ports)

    def sfp(self, slot, eeprom):
        self._now()
        self.b.sfp(slot, eeprom)

    def counters(self):
        """Every port's good and error counters (STAT 46, 47, 48) nonzero and
        distinct, the good TX count past 32 bits (stat prints its low half):
        rtlsim counts nothing on its own (control's stat set)."""
        self._now()
        for p in sorted(set(self.P) | {c[0] for c in self.cages.values()}):
            for cnt, v in ((46, (p + 1 << 32) | 0x100 * p | 0x11), (47, 0x2000 + p), (48, (0x30 + p) << 32 | 0x40 + p)):
                self.b.sim.cmd(f"stat set {p} {cnt} {v:x}")

    def powercut(self):
        self._now()
        self.b.sim.powercut()
        self.cookie = b""

    def igmp(self, port, typ, dst, group):
        """An IGMPv2 message from a host on `port` (type 0x11 query, 0x16
        report, 0x17 leave) to IP `dst`, about `group`."""
        self._now()
        g = ip_bytes(group)
        m = struct.pack("!BBH4s", typ, 100 if typ == 0x11 else 0, 0, g)
        m = m[:2] + struct.pack("!H", 0xffff ^ csum(m)) + m[4:]
        src = ip_bytes(f"192.168.77.{10 + port}")
        d = ip_bytes(dst)
        h = struct.pack("!BBHHHBBH4s4s", 0x46, 0xc0, 24 + len(m), 0, 0, 1, 2, 0, src, d) + b"\x94\x04\x00\x00"
        h = h[:10] + struct.pack("!H", 0xffff ^ csum(h)) + h[12:]
        mac = bytes([0x01, 0x00, 0x5e, d[1] & 0x7f, d[2], d[3]])
        self.b.sim.nic_inject(port, mac + bytes([2, 0, 0, 0, 0x77, port]) + b"\x08\x00" + h + m)

    def learn(self, port, n, every=2):
        """n hosts behind physical `port`, each heard once (a frame from its
        MAC, 02:00:00:01:hh:ll, i = 0..n-1, to one that isn't there), `every`
        ms apart: the chip learns them (control mode's learning) as far as
        their buckets hold (rtlsim's geometry, sys/tables.cpp l2_hash():
        1024 buckets of 4), the CPU drops them. All n must be in the table
        after: else it raises, the scenario's error on either side."""
        for i in range(n):
            self.wait(every)
            self._now()
            self.b.sim.nic_inject(port, bytes.fromhex("02000000ffff") + bytes([2, 0, 0, 1, i >> 8, i & 255]) +
                                  b"\x88\xb5" + bytes(46))
        self._now()
        got = sum(1 for l in self.b.table("l2") if l.split()[2].endswith("0200") and l.split()[1][:4] == "0001"
                  and int(l.split()[1][4:], 16) < n)
        if got != n:		# a learning regression mustn't hide on both sides
            raise AssertionError(f"learn: {got} of {n} hosts in the L2 table")

    def get_l2_pages(self, pages=40, slot=2000):
        """/l2.json as the UI loads it (app.js l2Load()): from index 0, each
        page from the index after the last entry of the one before, until a
        page is empty or comes round to an entry already seen."""
        idx, seen = 0, set()
        for _ in range(pages):
            self.get(f"/l2.json?idx={idx}", slot=slot)
            res = self.results[f"{self.n - 1:03d} web GET /l2.json?idx={idx}"]
            if res.startswith("HTTP/"):
                got = [int(x, 16) for x in re.findall(r'"idx": *"([0-9a-fA-F]+)"', res.partition("\n\n")[2])]
            else:			# by value (translate.py): state.l2.<idx>
                got = [int(x) for x in re.findall(r"^state\.l2\.(\d+)\+* ", res, re.M)]
            if not got or seen & set(got):
                return
            seen |= set(got)
            idx = got[-1] + 1

    # --- the new-only checks (checks.py): measured on each image, not compared
    def mark(self):
        """Firmware time now (µs), to bound a check's window."""
        self._now()
        return self.b.now

    def gap_check(self, name, kind, t0, t1, lo=None, hi=None, ports=None):
        """The gaps between the frames of `kind` the chip sent per port, every
        gap that overlaps [t0, t1] (so the one across t0 too, and the silence
        from the last frame to t1), each within [lo, hi] s. Ports: those
        given, else every port that sent two or more in the window. The
        worst measure is the longest gap over hi (or the silence at the end);
        a gap under lo is hard, a failure no known line covers (frames too
        often hide behind none)."""
        ts = {p: v for (p, k), v in self.b.times.items() if k == kind}
        if ports is None:
            ports = sorted(p for p, v in ts.items() if sum(t0 <= t <= t1 for t in v) >= 2)
        bad, what, worst, hard = [], [], 0, []
        for p in ports:
            v = ts.get(p, [])
            gaps = [(a, b) for a, b in zip(v, v[1:]) if b >= t0 and a <= t1]
            last = max([t for t in v if t <= t1], default=None)
            tail = t1 - last if last is not None and last >= t0 else None
            if not gaps and last is None:
                bad.append(f"port {p}: none sent in {t0 / 1e6:.3f}..{t1 / 1e6:.3f} s")
                continue
            g = [b - a for a, b in gaps]
            what.append(f"port {p}: {len(g)} gaps" + (f", min {min(g) / 1e6:.3f} s max {max(g) / 1e6:.3f} s" if g else ""))
            for a, b in gaps:
                if lo is not None and b - a < lo * 1e6:
                    hard.append(f"port {p}: {(b - a) / 1e6:.3f} s from {a / 1e6:.3f} s, under {lo} s")
                elif hi is not None and b - a > hi * 1e6:
                    bad.append(f"port {p}: {(b - a) / 1e6:.3f} s from {a / 1e6:.3f} s")
                    worst = max(worst, b - a)
            if hi is not None and tail is not None and tail > hi * 1e6:
                bad.append(f"port {p}: none for {tail / 1e6:.3f} s up to {t1 / 1e6:.3f} s")
                worst = max(worst, tail)
        if not ports:
            bad.append(f"no port sent two in {t0 / 1e6:.3f}..{t1 / 1e6:.3f} s")
        bounds = (f"{lo}" if lo is not None else "") + ".." + (f"{hi}" if hi is not None else "") + " s"
        self.checks.append((name, bad + hard, f"{kind} {bounds}, {t0 / 1e6:.3f}..{t1 / 1e6:.3f} s: " + "; ".join(what),
                            worst or None, hard, None))

    def lacp_check(self, t0, t1, ports):
        """lacp gap: the switch's LACPDUs on the LAG, < 1.2 s apart (its fast
        periodic is 1 s; the partner's short timeout is 3)."""
        self.gap_check("lacp gap", "lacp", t0, t1, hi=1.2, ports=ports)

    def hello_check(self, t0, t1):
        """stp hello: the BPDUs on every port that sends them, 2 s +-50 ms
        apart (the hello time)."""
        self.gap_check("stp hello", "stp", t0, t1, lo=1.95, hi=2.05)

    def long_steps(self, limit_ms=50, slot=8000):
        """`health tasks` at the end: every state's longest step within
        `limit_ms` (decision 25: the step limit is 50 ms, the target 5 ms;
        FL_ERASE, an erase in one step, has no exemption now, but its longest
        is reported). A check per state over the limit ("long step
        <STATE>"), or "long steps" if none is; skipped ("long steps") on a
        build without the command."""
        _, out = self._type("health tasks", self.b.now + slot * 1000)
        if out is None or "state\truns\tmax" not in out:
            out = self._state_sched(slot)
        if out is None or "state\truns\tmax" not in out:
            self.checks.append(("long steps", None, "no `health tasks` in this build: skipped", None, [], None))
            return
        bad, longest, erase = [], (0, ""), None
        for l in out.split("state\truns\tmax", 1)[1].splitlines():
            m = re.match(r"(\w+)\t(\d+)\t(>?)(\d+)(us|ms)$", l)
            if not m:
                continue
            st, runs, over, v, unit = m.groups()
            us = int(v) * (1000 if unit == "ms" else 1) + (1 if over else 0)
            if st == "FL_ERASE":
                erase = f"{over}{v}{unit}, {runs} runs"
            elif us > longest[0]:
                longest = (us, st)
            if us > limit_ms * 1000:
                # ">400ms": the measure saturates (16 bits of 6.144 µs); a known
                # line bounds it by another measure (a console reply, `by`)
                self.checks.append((f"long step {st}", [f"{over}{v}{unit}, {runs} runs"], f"over {limit_ms} ms", us,
                                    [], dict(self.replies) if over else None))
                bad.append(st)
        if not bad:
            m = re.search(r"passes .*", out)
            self.checks.append(("long steps", [], f"none over {limit_ms} ms; longest {longest[1]} "
                                f"{longest[0] / 1000:.3f} ms, FL_ERASE {erase or 'never ran'}; {m.group(0) if m else ''}",
                                None, [], None))

    def _state_sched(self, slot):
        """get state.sched (P5-2 deleted health tasks) as health tasks' text:
        "state\\truns\\tmax", a "STATE\\tRUNS\\tNus" line each (">400ms" where
        the measure saturated: 0xffff fine units, 402640 us), "passes ..."."""
        _, out = self._type("get state.sched.state state.sched.passes state.sched.idle state.sched.pass_max_us",
                            self.b.now + slot * 1000)
        if not out or "state.sched.state." not in out:
            return None
        v = dict(l.split(" ", 1) for l in out.splitlines() if l.startswith("state.sched.") and " " in l)
        fmt = lambda us: ">400ms" if int(us) >= 402640 else f"{us}us"
        rows = ["state\truns\tmax"]
        for p, x in v.items():
            m = re.fullmatch(r"state\.sched\.state\.(\w+)\.runs", p)
            if m:
                rows.append(f"{m.group(1)}\t{x}\t{fmt(v.get(f'state.sched.state.{m.group(1)}.max_us', '0'))}")
        rows.append(f"passes {v.get('state.sched.passes')} idle {v.get('state.sched.idle')} "
                    f"longest {fmt(v.get('state.sched.pass_max_us', '0'))}")
        return "\n".join(rows) + "\n"

    def flash_regions(self):
        """The flash after an upload, region by region, as results: the
        application (0x4000..0x3a000), its home bank in the slot (0x3c000,
        LDR_HOME_LEN bytes: the image's 0x2..0x4000), the staged upload
        (0x40000) and the config sector. Each is said against the image
        uploaded (r.payload), in runs of 4 KiB sectors (the upload's own
        bytes, blank, or other: their hash), so two images that each uploaded
        their own compare equal when each holds what it was sent."""
        self._now()
        self._console()
        for name, a, n, src in [("app", 0x4000, 0x3a000 - 0x4000, 0x4000), ("slot", 0x3c000, 0x3ffe, 2),
                                ("staged", 0x40000, 0x3a000, 0), ("config", CONFIG_START, CONFIG_LEN - 2, None)]:
            self.results[self._key(f"flash {name} {a:#x}")] = self._flash_text(a, self.b.flash(a, n), src)

    def _flash_text(self, a, f, src):
        P, runs = self.payload, []
        for i in range(0, len(f), 0x1000):
            c = f[i:i + 0x1000]
            what = ("blank" if c == b"\xff" * len(c) else
                    "upload" if src is not None and c == P[src + i:src + i + len(c)] else "other")
            if runs and runs[-1][0] == what:
                runs[-1][2] = i + len(c)
            else:
                runs.append([what, i, i + len(c)])
        out = []
        for what, i, j in runs:
            out.append(f"{a + i:#x}..{a + j:#x} " + (
                "blank" if what == "blank" else
                f"the upload's {src + i:#x}..{src + j:#x}" if what == "upload" else f"sha256 {sha(f[i:j])}"))
        return ", ".join(out)

    # --- the end
    def finish(self, checks=True, cov=None):
        self._now()
        self._console()
        res = dict(self.results)
        for l in self.b.reg_dump():
            k, a, v = l.split()
            if k == "r" and int(a, 16) in CODE_LOAD:
                continue
            res[{"r": "reg", "s": "sds", "p": "phy"}[k] + " " + a] = v
        for l in self.b.table("vlan"):
            vid, v = l.split()
            res[f"vlan {vid}"] = v
        for l in self.b.table("l2"):
            idx, *v = l.split()
            res[f"l2 {idx}"] = " ".join(v)
        # the sector less its last two bytes: the image's CRC, which the copy of
        # the default config sector (flash_default_config()) brings along
        cfg = self.b.flash(CONFIG_START, CONFIG_LEN - 2)
        end = min([i for i in (cfg.find(b"\0"), cfg.find(b"\xff")) if i >= 0] or [len(cfg)])
        res["flash config"] = cfg[:end].decode("latin-1") + (
            "" if cfg[end:].count(cfg[end:end + 1]) == len(cfg) - end else f"\n<then not blank: sha256 {sha(cfg[end:])}>")
        for (p, k), n in sorted(self.b.frames.items()):
            res[f"frames {p} {k}"] = str(n)
        if checks:
            self.long_steps()		# after the results: its reply is not one
        if cov:
            self.b.sim.cmd(f"cov dump {cov}")	# what ran, since power-on (cov_diff.py)
        self.b.close()
        return {k: scrub(v) for k, v in res.items()}


# What makes two builds of the same source differ: their identity. Scrubbed
# from every result, so a difference is a difference in behaviour.
IDENTITY = [(re.compile(r"v\d+\.\d+\.\d+-[0-9a-f]{7,}(-dirty)?"), "<version>"),
            (re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"), "<build date>")]


def scrub(v):
    for rx, to in IDENTITY:
        v = rx.sub(to, v)
    return v


LLDP_TLV = {0: "end", 1: "chassis", 2: "port", 3: "ttl", 4: "port desc", 5: "sys name", 6: "sys desc",
            7: "caps", 8: "mgmt addr", 127: "org"}


def _value(v):
    return repr(v.decode("latin-1")) if v and all(32 <= c < 127 for c in v) else v.hex()


def lldp_text(f):
    """An LLDPDU's TLVs, one a line, in order: type, length, value (text if
    printable, else hex; an 802.1 VLAN name decoded). In the system
    description the build's version string is masked, and the length shows
    as the rest plus its length, the one thing that legitimately differs
    between two builds."""
    lines, p = [], f[14:]
    if f[12:14] == b"\x81\x00":
        p = f[18:]
    while len(p) >= 2:
        h = int.from_bytes(p[:2], "big")
        t, n, v = h >> 9, h & 0x1ff, p[2:2 + (h & 0x1ff)]
        p = p[2 + n:]
        name = LLDP_TLV.get(t, f"type {t}")
        ln = str(n)
        if t == 6:
            m = IDENTITY[0][0].search(v.decode("latin-1"))
            if m:
                ln = f"{n - len(m.group(0))}+len(<version>)"
        if t == 127 and len(v) >= 4:
            oui, sub, body = v[:3].hex(), v[3], v[4:]
            if oui == "0080c2" and sub == 3 and len(body) >= 3:	# 802.1 VLAN name
                val = f"802.1 vlan name vid {int.from_bytes(body[:2], 'big')} len {body[2]} {_value(body[3:3 + body[2]])}"
            else:
                val = f"{oui}/{sub} {body.hex()}"
        else:
            val = _value(v)
        lines.append(f"  {name} len {ln}: {val}")
        if t == 0:
            break
    if p.rstrip(b"\0"):
        lines.append(f"  after the end: {p.rstrip(bytes(1)).hex()}")
    return "\n".join(lines)


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def norm(r):
    """A response as a result: text, with what changes from login to login
    (the session's value) left out; a body that isn't text as length + hash."""
    if r is None:
        return "<timeout>"
    if isinstance(r, str):
        return f"<{r}>"
    head, _, body = r.partition(b"\r\n\r\n")
    head = re.sub(rb"session=\w+", b"session=<session>", head).decode("latin-1")
    ctype = re.search(r"(?im)^content-type: *([^;\r\n]*)", head)
    text = ctype is None or ctype.group(1) in ("application/json", "text/plain") or not body
    if not text or len(body) > 4096:
        return head + f"\n\n<{len(body)} bytes, sha256 {sha(body)}>"
    return head + "\n\n" + body.decode("latin-1")


def run_one(job):
    name, side, image, a = job
    t0 = time.time()
    r = Run(image, a["board"], a["rtlsim"], a["payload_" + side] if "payload_" + side in a else a["payload"],
            a["om_" + side], (a["om_old"], a["om_new"]))
    try:
        SCENARIOS[name](r)
        res = r.finish(cov=os.path.join(a["cov"], f"{name}.{side}.cov") if a.get("cov") else None)
        checks = r.checks
    except Exception as e:
        try:
            r.b.close()
        except Exception:
            pass
        res = {"<error>": f"{type(e).__name__}: {e}"}
        checks = [("<error>", [res["<error>"]], "the scenario didn't finish", None, [], None)]
    return name, side, res, checks, time.time() - t0


def verdicts(name, checks, known, used=None):
    """[(PASS|FAIL|KNOWN|SKIP, check, text)]: a check with no violations
    passes; one that a known line (load_known()) lists, for this scenario,
    and whose worst measure is within the line's ceiling, is KNOWN: a
    failure of today's build that is on record. Over the ceiling it FAILs,
    and so does a hard violation (a gap too short) whatever the lines say.
    A measure that saturated (`health tasks`' >400ms) is bounded only by a
    line's `by cli` (the longest console reply of every command the
    scenario typed) or a line that says `unbounded`.
    The known lines that matched go into `used`."""
    out = []
    for c, bad, what, worst, hard, sat in checks:
        if bad is None:
            out.append(("SKIP", c, what))
            continue
        if not bad:
            out.append(("PASS", c, what))
            continue
        text = "; ".join(bad) + (f" [{what}]" if what else "")
        if hard:
            out.append(("FAIL", c, text + " (under the lower bound: no known line covers it)"))
            continue
        lines = [k for k in known if any(fnmatch.fnmatch(name, g) for g in k["scenarios"]) and fnmatch.fnmatch(c, k["check"])]
        ok, why = [], []
        for k in lines:
            if sat is not None and not k["unbounded"]:
                if not k["by"]:
                    why.append(f" (saturated: line {k['line']} needs a `by` measure or `unbounded`)")
                    continue
                miss = [c for c in k["by"] if c != "*" and f"cli {c}" not in sat]
                if miss or not sat:
                    why.append(f" (line {k['line']}'s `cli {','.join(miss) or '*'}` wasn't taken)")
                    continue
                # every command's reply: the saturated step may be any of them
                cmd, m = max(sat.items(), key=lambda x: x[1])
                if m <= k["ceiling"]:
                    ok.append(k)
                    text += f" [by the longest reply, {cmd}: {_us(m)}]"
                else:
                    why.append(f" (by the longest reply, {cmd}: {_us(m)}, over line {k['line']}'s ceiling {_us(k['ceiling'])})")
            elif k["unbounded"] or (worst is not None and worst <= k["ceiling"]):
                ok.append(k)
            else:
                why.append(f" (over line {k['line']}'s ceiling {_us(k['ceiling'])})")
        if used is not None:
            used.update(k["line"] for k in ok)
        out.append(("KNOWN", c, text) if ok else ("FAIL", c, text + "".join(why)))
    return out


def _us(v):
    return f"{v / 1e6:g} s" if v >= 1_000_000 else f"{v / 1000:g} ms"


def load_known(path):
    """The checks' known failures, one a line:

        <scenario glob>[,<scenario glob>...]: <check glob> (<= <n> ms|s [by cli <command>[,...]|*] | unbounded)   # why

    The ceiling bounds the check's worst measure: a long step's maximum, a
    gap check's largest gap over its upper bound. Where that measure
    saturates (a long step's >400ms), `by cli ...` bounds it instead by the
    console replies, in firmware time from typed to prompt (a reply holds
    every step its command ran): the longest of every command the console
    was given (Run._type(): the harness's own too), whichever saturated the
    step; commands named must have been
    typed. A line with no ceiling says `unbounded`, else it is malformed.
    [{scenarios, check, ceiling µs|None, by [commands]|None, unbounded, line}]"""
    out = []
    if not path:
        return out
    for n, l in enumerate(open(path), 1):
        l = l.split("#", 1)[0].strip()
        if not l:
            continue
        m = re.match(r"([^:\s]+):\s*(.+?)(?:\s*<=\s*([\d.]+)\s*(ms|s)(?:\s+by\s+cli\s+(\S+))?|\s+(unbounded))$", l)
        if not m:
            sys.exit(f"{path}:{n}: not '<scenarios>: <check> (<= <n> ms|s [by cli <commands>|*] | unbounded)': {l}")
        ceil = float(m.group(3)) * (1000 if m.group(4) == "ms" else 1_000_000) if m.group(3) else None
        out.append({"scenarios": m.group(1).split(","), "check": m.group(2), "ceiling": ceil,
                    "by": m.group(5).split(",") if m.group(5) else None, "unbounded": bool(m.group(6)), "line": n})
    return out


def stale_known(known, used, names, path):
    """A line no failed check matched, among those naming a scenario run."""
    return [f"known: {path}:{k['line']}: '{','.join(k['scenarios'])}: {k['check']}' matched no failed check"
            for k in known if k["line"] not in used and any(fnmatch.fnmatch(x, p) for x in names for p in k["scenarios"])]


EXPECT_LINES = re.compile(r"\s+([-+])/(.*?)/(?=\s|$)")


def load_expect(path):
    """[(scenario glob, result glob, [(-|+, line regex)], line number)]: a
    line may end in -/RE/ and +/RE/ items, the lines the result may lose
    and gain (each line matched in full, or a run of them as one block, \n
    between); without, the whole result may differ."""
    out = []
    if not path:
        return out
    for n, l in enumerate(open(path), 1):
        l = l.split("#", 1)[0].strip()
        if l:
            s, k = l.split(None, 1)
            rx = [(m.group(1), re.compile(m.group(2))) for m in EXPECT_LINES.finditer(" " + k)]
            k = EXPECT_LINES.split(" " + k, 1)[0].strip() if rx else k.strip()
            out.append((s, k, rx, n))
    return out


def _covered(a, b, rx):
    """Whether every run of lines `a` loses and `b` gains is covered by rx's
    items of its sign: each line matched by one, or the run, its lines
    joined by newlines, by one (a block: "\n" in the item)."""
    def ok(run, sign):
        items = [r for o, r in rx if o == sign]
        return not run or all(any(r.fullmatch(x) for r in items) for x in run) or \
            any(r.fullmatch("\n".join(run)) for r in items)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal" and not (ok(a[i1:i2], "-") and ok(b[j1:j2], "+")):
            return False
    return True


def compare(name, old, new, expect, used=None):
    """(unexpected, expected) differences, each a list of readable blocks;
    the expect lines that matched one go into `used`. A result whose expect
    lines give line regexes is expected only if they cover every line lost
    and gained; their lines count as used only then."""
    bad, ok = [], []
    for k in list(old) + [k for k in new if k not in old]:
        a, b = old.get(k, "<absent>"), new.get(k, "<absent>")
        if a == b:
            continue
        if "\n" in a or "\n" in b:
            d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), "old", "new", lineterm="", n=2))
            block = f"  {k}:\n" + "\n".join("    " + x for x in d.splitlines())
        else:
            block = f"  {k}: old {a!r}, new {b!r}"
        lines = [(rx, n) for s, g, rx, n in expect
                 if any(fnmatch.fnmatch(name, x) for x in s.split(",")) and fnmatch.fnmatch(k, g)]
        hits = [n for rx, n in lines if not rx]
        rx = [x for r, _ in lines for x in r]
        if not hits and rx and _covered(a.splitlines(), b.splitlines(), rx):
            hits = [n for r, n in lines if r]
        if used is not None:
            used.update(hits)
        (ok if hits else bad).append(block)
    return bad, ok


def default_payload(old):
    d = os.path.dirname(os.path.abspath(old))
    c = [p for p in glob.glob(os.path.join(d, "rtlplayground-v*.bin")) if "-flash" not in p]
    return max(c, key=os.path.getmtime) if c else None


def main():
    ap = argparse.ArgumentParser(description="old-vs-new: the same scenarios on two images, compared on results")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--board")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--expect")
    ap.add_argument("--payload")
    ap.add_argument("--new-payload", help="what NEW's upload scenarios upload (default: --payload)")
    ap.add_argument("--checks", action="store_true", help="NEW's new-only checks (checks.py) count too")
    ap.add_argument("--known", help="the checks' known failures, as --expect's format (default: none)")
    ap.add_argument("--new-om", default="", metavar="OBJS",
                    help="the objects NEW has on the object model, comma-separated (translate.py): their old "
                         "commands are translated for it, and compared by value on both sides")
    ap.add_argument("--old-om", default="", metavar="OBJS", help="the same for OLD")
    ap.add_argument("--save")
    ap.add_argument("--cov", metavar="DIR", help="each run's coverage, <scenario>.<side>.cov (tests/oldnew/cov_diff.py)")
    ap.add_argument("-j", type=int, default=os.cpu_count())
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    names = a.only or list(SCENARIOS)
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"oldnew: no scenario {n} (have: {' '.join(SCENARIOS)})")
    opts = {"board": a.board, "rtlsim": a.rtlsim, "payload": a.payload or default_payload(a.old),
            "om_old": {x for x in a.old_om.split(",") if x}, "om_new": {x for x in a.new_om.split(",") if x}}
    for x in opts["om_old"] | opts["om_new"]:
        if x not in translate.objects():
            sys.exit(f"oldnew: no rows for object {x} in translate.py (have: {' '.join(sorted(translate.objects()))})")
    if translate.pending(opts["om_old"] | opts["om_new"]):
        sys.exit(f"oldnew: {' '.join(translate.pending(opts['om_old'] | opts['om_new']))}: pending in translate.py "
                 "(PENDING): take it out when its paths are on the switch")
    if a.new_payload:
        opts["payload_new"] = a.new_payload
    if a.cov:
        os.makedirs(a.cov, exist_ok=True)
        opts["cov"] = os.path.abspath(a.cov)
    expect = load_expect(a.expect)
    known = load_known(a.known)
    t0 = time.time()
    jobs = [(n, side, img, opts) for n in names for side, img in (("old", a.old), ("new", a.new))]
    res, checks = {}, {}
    with ProcessPoolExecutor(max_workers=a.j) as ex:
        for name, side, r, ch, wall in ex.map(run_one, jobs):
            res[(name, side)] = (r, wall)
            checks[(name, side)] = ch
    if a.save:
        os.makedirs(a.save, exist_ok=True)
        for (name, side), (r, _) in res.items():
            with open(os.path.join(a.save, f"{name}.{side}.json"), "w") as f:
                json.dump(r, f, indent=1)
    print(f"old: {a.old}\nnew: {a.new}" + (f"\nexpected differences: {a.expect}" if a.expect else ""))
    failed = 0
    used, kused = set(), set()
    for n in names:
        (o, wo), (w, wn) = res[(n, "old")], res[(n, "new")]
        bad, ok = compare(n, o, w, expect, used)
        err = "<error>" in o or "<error>" in w
        vs = {side: verdicts(n, checks[(n, side)], known, kused) for side in ("old", "new")}
        cfail = (a.checks and any(v == "FAIL" for v, _, _ in vs["new"])) or \
            any(v == "FAIL" and c == "boot defaults" for v, c, _ in vs["new"])
        status = "FAIL" if bad or err or cfail else "PASS"
        failed += status == "FAIL"
        print(f"{status}  {n}: {len(o)} results, {len(bad)} differences"
              + (f", {len(ok)} expected" if ok else "") + f" ({max(wo, wn):.1f} s)")
        for x in bad:
            print(x)
        if ok:
            print("  expected:")
            for x in ok:
                print("  " + x)
        for side in ("old", "new"):
            for v, c, text in vs[side]:
                print(f"  check {side} {v} {c}: {text}")
    for s, g, _, line in expect:
        if line not in used:
            print(f"expect: {a.expect}:{line}: '{s} {g}' matched no difference" +
                  (" (in the scenarios run)" if a.only else ""))
    for l in stale_known(known, kused, names, a.known):
        print(l)
    print(f"oldnew: {len(names) - failed} of {len(names)} scenarios match, {time.time() - t0:.0f} s wall")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
