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

    <scenario glob> <name glob>   # why

(fnmatch patterns; '#' starts a comment). Those are reported as expected and
don't fail the run; a line that matched no difference is flagged (it may be
stale), without failing it. Exit 0 when every scenario matches, 1 otherwise. --save
DIR writes each side's results, as JSON, for a look.

--payload is the image the upload scenario uploads (default: the
application image next to --old). The run is the same every time: old
against old must match on every scenario (make oldnew-self).
"""
import argparse, difflib, fnmatch, glob, hashlib, json, os, re, struct, sys, time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bench import Bench, Host, ip_bytes, csum, PROMPT
from lacp_partner import LacpPartner
from scenarios import SCENARIOS

CONFIG_START, CONFIG_LEN = 0x7f000, 0x1000	# layout.h, compact
DEFAULT_BOARD = os.path.join(HERE, "../../boards/swgt024_v2_0_unmanaged.json")	# rtlsim's own default
# The register window the loader writes the application's common code
# through (0x80b0 + 4 i, sys/system.cpp model_code_ram_load()): the program,
# not the switch's state, so not a result.
CODE_LOAD = range(0x80b0, 0x80b0 + 4 * 0x1000)
PASSWORD = "1234"


class Run:
    """What a scenario does to one image, and what came back."""

    def __init__(self, image, board, rtlsim, payload):
        self.b = Bench(image, board=board, rtlsim=rtlsim)
        self.payload = open(payload, "rb").read() if payload else b""
        # the front ports, as the board has them (rtlsim's default: the SWTG024AS)
        ports = json.load(open(board or DEFAULT_BOARD))["ports"]
        copper = [p for p in ports if "sfp" not in p]
        self.P = [p["phys"] for p in copper]		# physical port of copper front port i
        self.F = [p["front"] for p in copper]		# its name on the CLI
        self.cages = {p["sfp"]["slot"]: (p["phys"], p["front"]) for p in ports if "sfp" in p}
        self.t = 0
        self.n = 0
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
            self.results[k] = self.results.get(k, "") + text

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
        before, ip = self.b.console("ip", until)	# the harness's own question: not a result
        self._console(before)
        m = re.search(r"Current IP: ([\d.]+)", ip or "")
        sw = m.group(1) if m else "192.168.10.247"
        if self.host is None:
            self.host = Host(self.b, self.P[0], ip=sw.rsplit(".", 1)[0] + ".1", switch_ip=sw)
        self.host.sw_ip = ip_bytes(sw)
        self.host.sw_mac = None

    def cli(self, line, slot=5000):
        before, out = self.b.console(line, self._slot(slot))
        self._console(before)
        self.results[self._key(f"cli {line}")] = out if out is not None else "<timeout>"

    def tables(self):
        """The VLAN and L2 tables now, as one result each."""
        self._now()
        self._console()
        self.results[self._key("vlan table")] = "\n".join(self.b.table("vlan"))
        self.results[self._key("l2 table")] = "\n".join(self.b.table("l2"))

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
        self._http(f"GET {path}", f"GET {path} HTTP/1.1\r\n".encode() + h + b"\r\n", slot)

    def _post(self, path, ctype, body, slot):
        req = (f"POST {path} HTTP/1.1\r\nCookie: session=".encode() + self.cookie +
               f"\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        self._http(f"POST {path}", req, slot)

    def post_cmd(self, cmd, slot=3000):
        self._post("/cmd", "text/plain;charset=UTF-8", cmd.encode(), slot)

    def _multipart(self, path, field, name, data, slot):
        bnd = "----oldnew0123456789"
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bnd}--\r\n".encode()
        self._post(path, f"multipart/form-data; boundary={bnd}", body, slot)

    def post_config(self, text, slot=5000):
        self._multipart("/config", "configuration", "config.txt", text.encode(), slot)

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

    # --- the end
    def finish(self):
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
    r = Run(image, a["board"], a["rtlsim"], a["payload"])
    try:
        SCENARIOS[name](r)
        res = r.finish()
    except Exception as e:
        try:
            r.b.close()
        except Exception:
            pass
        res = {"<error>": f"{type(e).__name__}: {e}"}
    return name, side, res, time.time() - t0


def load_expect(path):
    """[(scenario glob, result glob, line number)]"""
    out = []
    if not path:
        return out
    for n, l in enumerate(open(path), 1):
        l = l.split("#", 1)[0].strip()
        if l:
            s, k = l.split(None, 1)
            out.append((s, k.strip(), n))
    return out


def compare(name, old, new, expect, used=None):
    """(unexpected, expected) differences, each a list of readable blocks;
    the expect lines that matched one go into `used`."""
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
        hits = [n for s, g, n in expect if fnmatch.fnmatch(name, s) and fnmatch.fnmatch(k, g)]
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
    ap.add_argument("--save")
    ap.add_argument("-j", type=int, default=os.cpu_count())
    ap.add_argument("--rtlsim", default=os.path.join(HERE, "../../build/rtlsim"))
    a = ap.parse_args()
    names = a.only or list(SCENARIOS)
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"oldnew: no scenario {n} (have: {' '.join(SCENARIOS)})")
    opts = {"board": a.board, "rtlsim": a.rtlsim, "payload": a.payload or default_payload(a.old)}
    expect = load_expect(a.expect)
    t0 = time.time()
    jobs = [(n, side, img, opts) for n in names for side, img in (("old", a.old), ("new", a.new))]
    res = {}
    with ProcessPoolExecutor(max_workers=a.j) as ex:
        for name, side, r, wall in ex.map(run_one, jobs):
            res[(name, side)] = (r, wall)
    if a.save:
        os.makedirs(a.save, exist_ok=True)
        for (name, side), (r, _) in res.items():
            with open(os.path.join(a.save, f"{name}.{side}.json"), "w") as f:
                json.dump(r, f, indent=1)
    print(f"old: {a.old}\nnew: {a.new}" + (f"\nexpected differences: {a.expect}" if a.expect else ""))
    failed = 0
    used = set()
    for n in names:
        (o, wo), (w, wn) = res[(n, "old")], res[(n, "new")]
        bad, ok = compare(n, o, w, expect, used)
        err = "<error>" in o or "<error>" in w
        status = "FAIL" if bad or err else "PASS"
        failed += status == "FAIL"
        print(f"{status}  {n}: {len(o)} results, {len(bad)} differences"
              + (f", {len(ok)} expected" if ok else "") + f" ({max(wo, wn):.1f} s)")
        for x in bad:
            print(x)
        if ok:
            print("  expected:")
            for x in ok:
                print("  " + x)
    for s, g, line in expect:
        if line not in used:
            print(f"expect: {a.expect}:{line}: '{s} {g}' matched no difference" +
                  (" (in the scenarios run)" if a.only else ""))
    print(f"oldnew: {len(names) - failed} of {len(names)} scenarios match, {time.time() - t0:.0f} s wall")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
